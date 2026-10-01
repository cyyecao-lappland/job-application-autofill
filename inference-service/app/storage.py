import json
import sqlite3
import time
import numpy as np

from .encoder import DIMENSION, normalize
from .language import resolve_language


class Store:
    """SQLite and aligned language matrices are owned by the single worker."""

    def __init__(self, settings, encoder):
        self.settings, self.encoder = settings, encoder
        settings.data_dir.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(settings.data_dir / "fields.db")
        self.feedback_db = sqlite3.connect(settings.data_dir / "training_feedback.db")
        try:
            self.db.execute("""CREATE TABLE IF NOT EXISTS fields (
                field_id TEXT PRIMARY KEY, canonical_text TEXT NOT NULL,
                aliases_json TEXT NOT NULL, field_type TEXT NOT NULL,
                embedding BLOB NOT NULL, model_version TEXT NOT NULL,
                updated_at INTEGER NOT NULL, active INTEGER NOT NULL)""")
            with self.db:
                columns = {row[1] for row in self.db.execute("PRAGMA table_info(fields)")}
                if "source_key" not in columns:
                    self.db.execute("ALTER TABLE fields ADD COLUMN source_key TEXT")
                self.db.execute("""CREATE TABLE IF NOT EXISTS field_localizations (
                    field_id TEXT NOT NULL, language TEXT NOT NULL CHECK(language IN ('zh','en')),
                    key_text TEXT NOT NULL, embedding BLOB NOT NULL, model_version TEXT NOT NULL,
                    PRIMARY KEY(field_id, language))""")
            self.feedback_db.execute("""CREATE TABLE IF NOT EXISTS feedback (
                id INTEGER PRIMARY KEY AUTOINCREMENT, query_text TEXT NOT NULL,
                positive_field_id TEXT NOT NULL, retrieved_candidates_json TEXT NOT NULL,
                source TEXT NOT NULL, language_pair TEXT, model_version TEXT NOT NULL,
                definitions_json TEXT NOT NULL, created_at INTEGER NOT NULL)""")
            with self.feedback_db:
                columns = {row[1] for row in self.feedback_db.execute("PRAGMA table_info(feedback)")}
                if "localized_definitions_json" not in columns:
                    self.feedback_db.execute("ALTER TABLE feedback ADD COLUMN localized_definitions_json TEXT")
            self._load()
        except BaseException:
            self.close()
            raise

    def _valid(self, blob, version):
        try:
            normalize(np.frombuffer(blob, dtype="<f4").reshape(1, -1))
            return version == self.settings.model_version
        except (TypeError, ValueError):
            return False

    def _load(self):
        rows = self.db.execute(
            "SELECT field_id, canonical_text, embedding, model_version FROM fields WHERE active=1 ORDER BY field_id"
        ).fetchall()
        stale = [row for row in rows if not self._valid(row[2], row[3])]
        local_rows = self.db.execute("""SELECT l.field_id, l.language, l.key_text, l.embedding, l.model_version
            FROM field_localizations l JOIN fields f ON f.field_id=l.field_id
            WHERE f.active=1 ORDER BY l.field_id, l.language""").fetchall()
        counts = {}
        for row in local_rows:
            counts[row[0]] = counts.get(row[0], 0) + 1
        if any(count != 2 for count in counts.values()):
            raise ValueError("Incomplete bilingual definitions; restore both language rows before startup")
        local_stale = [row for row in local_rows if not self._valid(row[3], row[4])]
        with self.db:
            for offset in range(0, len(stale), self.settings.max_batch_size):
                batch = stale[offset:offset + self.settings.max_batch_size]
                vectors = self.encoder.encode([row[1] for row in batch], "passage")
                for row, vector in zip(batch, vectors, strict=True):
                    self.db.execute(
                        "UPDATE fields SET embedding=?, model_version=?, updated_at=? WHERE field_id=?",
                        (vector.astype("<f4").tobytes(), self.settings.model_version, int(time.time()), row[0]),
                    )
            for offset in range(0, len(local_stale), self.settings.max_batch_size):
                batch = local_stale[offset:offset + self.settings.max_batch_size]
                vectors = self.encoder.encode([row[2] for row in batch], "passage")
                for row, vector in zip(batch, vectors, strict=True):
                    self.db.execute(
                        "UPDATE field_localizations SET embedding=?, model_version=? WHERE field_id=? AND language=?",
                        (vector.astype("<f4").tobytes(), self.settings.model_version, row[0], row[1]),
                    )
        self.refresh()

    def refresh(self):
        rows = self.db.execute(
            "SELECT field_id, embedding FROM fields WHERE active=1 ORDER BY field_id"
        ).fetchall()
        local = {(row[0], row[1]): row[2] for row in self.db.execute(
            "SELECT field_id, language, embedding FROM field_localizations"
        )}
        def build(language=None):
            if not rows:
                return np.empty((0, DIMENSION), np.float32)
            return normalize(np.stack([
                np.frombuffer(local.get((field_id, language), blob), dtype="<f4")
                for field_id, blob in rows
            ]))
        matrix, matrices = build(), {lang: build(lang) for lang in ("zh", "en")}
        self.ids, self.matrix, self.matrices = [row[0] for row in rows], matrix, matrices

    def upsert(self, payload):
        zh, en = payload.get("key_zh"), payload.get("key_en")
        if bool(zh) != bool(en):
            raise ValueError("Both language names are required")
        canonical = payload.get("canonical_text") or en
        if not canonical:
            raise ValueError("No field definition")
        texts = list(dict.fromkeys([canonical] + ([zh, en] if zh else [])))
        encoded = dict(zip(texts, self.encoder.encode(texts, "passage"), strict=True))
        with self.db:
            self.db.execute("""INSERT INTO fields
                (field_id, canonical_text, aliases_json, field_type, embedding, model_version, updated_at, active, source_key)
                VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?)
                ON CONFLICT(field_id) DO UPDATE SET
                canonical_text=excluded.canonical_text, aliases_json=excluded.aliases_json,
                field_type=excluded.field_type, embedding=excluded.embedding,
                model_version=excluded.model_version, updated_at=excluded.updated_at,
                active=1, source_key=excluded.source_key""",
                (payload["field_id"], canonical, json.dumps(payload.get("aliases", []), ensure_ascii=False),
                 payload["field_type"], encoded[canonical].astype("<f4").tobytes(), self.settings.model_version,
                 int(time.time()), payload.get("source_key")),
            )
            self.db.execute("DELETE FROM field_localizations WHERE field_id=?", (payload["field_id"],))
            if zh:
                for language, text in (("zh", zh), ("en", en)):
                    self.db.execute("INSERT INTO field_localizations VALUES (?, ?, ?, ?, ?)",
                                    (payload["field_id"], language, text, encoded[text].astype("<f4").tobytes(),
                                     self.settings.model_version))
        self.refresh()
        return {"field_id": payload["field_id"], "active": True}

    def delete(self, field_id):
        with self.db:
            changed = self.db.execute(
                "UPDATE fields SET active=0, updated_at=? WHERE field_id=? AND active=1",
                (int(time.time()), field_id),
            ).rowcount
        if not changed:
            raise KeyError("field_not_found")
        self.refresh()
        return {"field_id": field_id, "active": False}

    def similarity(self, payload):
        queries = self.encoder.encode(payload["texts"], "query")
        languages = [resolve_language(text, payload.get("language", "auto")) for text in payload["texts"]]
        scores = np.empty((len(queries), len(self.ids)), dtype=np.float32)
        for lang in ("zh", "en"):
            indices = [i for i, language in enumerate(languages) if language == lang]
            if indices:
                scores[indices] = np.clip(queries[indices] @ self.matrices[lang].T, -1, 1)
        return {"results": [
            {"text_index": i, "language": languages[i], "candidates": [
                {"field_id": self.ids[j], "cosine": float(row[j])}
                for j in np.argsort(-row, kind="stable")[:payload["top_k"]]
            ]} for i, row in enumerate(scores)
        ]}

    def feedback(self, payload):
        ids = list(dict.fromkeys([payload["positive_field_id"]] + [x[0] for x in payload["retrieved_candidates"]]))
        pair = payload.get("language_pair")
        language = pair.split("-")[1] if pair else resolve_language(payload["query_text"])
        definitions, snapshots = {}, {}
        for field_id in ids:
            row = self.db.execute(
                "SELECT canonical_text, source_key FROM fields WHERE field_id=? AND active=1", (field_id,)
            ).fetchone()
            if row is None:
                raise KeyError("field_not_found")
            names = dict(self.db.execute(
                "SELECT language, key_text FROM field_localizations WHERE field_id=?", (field_id,)
            ))
            definitions[field_id] = names.get(language, row[0])
            snapshots[field_id] = {"canonical_text": row[0], "source_key": row[1], "keys": names}
        with self.feedback_db:
            cursor = self.feedback_db.execute(
                """INSERT INTO feedback (query_text, positive_field_id, retrieved_candidates_json,
                    source, language_pair, model_version, definitions_json, created_at, localized_definitions_json)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (payload["query_text"], payload["positive_field_id"], json.dumps(payload["retrieved_candidates"]),
                 payload["source"], pair, self.settings.model_version,
                 json.dumps(definitions, ensure_ascii=False), int(time.time()),
                 json.dumps(snapshots, ensure_ascii=False)),
            )
        return {"feedback_id": cursor.lastrowid, "status": "recorded"}

    def close(self):
        self.db.close()
        self.feedback_db.close()
