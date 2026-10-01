import json
import sqlite3
import threading

import numpy as np
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.encoder import DIMENSION
from app.language import resolve_language
from app.main import create_app
from app.storage import Store


class RoutedEncoder:
    def __init__(self, settings):
        self.calls = []

    def encode(self, texts, role="query"):
        self.calls.append((list(texts), role, threading.get_ident()))
        result = np.zeros((len(texts), DIMENSION), np.float32)
        for i, text in enumerate(texts):
            result[i, 1 if text in ("中文乙", "English A") else 0] = 1
        return result


def config(tmp_path, version="v1"):
    return Settings(tmp_path / "model", tmp_path / "data", model_version=version)


def item(identifier="a"):
    return {"field_id": identifier, "key_zh": "中文甲" if identifier == "a" else "中文乙",
            "key_en": "English A" if identifier == "a" else "English B",
            "source_key": f"identity/{identifier}", "field_type": "text"}


@pytest.mark.parametrize("text,expected", [
    ("联系电话", "zh"), ("Mobile number", "en"), ("姓名 Name", "zh"), ("GPA", "en"),
    ("section: 教育经历\nfield: GPA", "en"),
    ("section: Education\nfield: 所学专业", "zh"),
])
def test_language_routing(text, expected):
    assert resolve_language(text) == expected
    assert resolve_language(text, "zh") == "zh"
    assert resolve_language(text, "en") == "en"


def test_http_mixed_batch_routes_once_and_no_duplicate_ids(tmp_path):
    app = create_app(config(tmp_path), RoutedEncoder)
    with TestClient(app) as client:
        for identifier in ("a", "b"):
            assert client.post("/v1/library/upsert", json=item(identifier)).status_code == 200
        before = len(app.state.service.encoder.calls)
        result = client.post("/v1/similarity", json={"texts": ["中文询问", "English question"]}).json()["results"]
        assert [r["language"] for r in result] == ["zh", "en"]
        assert [r["candidates"][0]["field_id"] for r in result] == ["a", "b"]
        assert all(len({c["field_id"] for c in row["candidates"]}) == 2 for row in result)
        assert len(app.state.service.encoder.calls) == before + 1
        forced = client.post("/v1/similarity", json={"texts": ["中文询问"], "language": "en"}).json()
        assert forced["results"][0]["candidates"][0]["field_id"] == "b"
        assert client.post("/v1/similarity", json={"texts": ["test"], "language": "fr"}).status_code == 422
        invalid = item()
        del invalid["key_en"]
        assert client.post("/v1/library/upsert", json=invalid).status_code == 422


def test_cache_restart_migration_and_version_rebuild(tmp_path):
    settings = config(tmp_path)
    store = Store(settings, RoutedEncoder(settings))
    store.upsert(item())
    store.upsert(item("b"))
    store.close()
    encoder = RoutedEncoder(settings)
    store = Store(settings, encoder)
    assert encoder.calls == []
    assert store.matrices["zh"].shape == store.matrices["en"].shape == (2, 384)
    store.db.execute("UPDATE field_localizations SET embedding=? WHERE field_id='a' AND language='zh'", (b"bad",))
    store.db.commit()
    store.close()
    repair = RoutedEncoder(settings)
    store = Store(settings, repair)
    assert repair.calls[0][0] == ["中文甲"]
    store.close()
    settings = config(tmp_path, "v2")
    updated = RoutedEncoder(settings)
    store = Store(settings, updated)
    assert sum(len(call[0]) for call in updated.calls) == 6
    assert store.db.execute("SELECT DISTINCT model_version FROM field_localizations").fetchall() == [("v2",)]
    store.delete("a")
    assert store.ids == ["b"]
    store.upsert(item())
    assert store.ids == ["a", "b"]
    store.upsert({"field_id": "a", "canonical_text": "Legacy", "aliases": [], "field_type": "text"})
    assert store.db.execute("SELECT COUNT(*) FROM field_localizations WHERE field_id='a'").fetchone()[0] == 0
    assert np.array_equal(store.matrices["zh"][0], store.matrices["en"][0])
    store.close()


def test_old_schema_preserved_and_bilingual_write_atomic(tmp_path):
    settings = config(tmp_path)
    settings.data_dir.mkdir()
    vector = np.zeros(DIMENSION, np.float32)
    vector[0] = 1
    with sqlite3.connect(settings.data_dir / "fields.db") as db:
        db.execute("""CREATE TABLE fields (
            field_id TEXT PRIMARY KEY, canonical_text TEXT NOT NULL, aliases_json TEXT NOT NULL,
            field_type TEXT NOT NULL, embedding BLOB NOT NULL, model_version TEXT NOT NULL,
            updated_at INTEGER NOT NULL, active INTEGER NOT NULL)""")
        db.execute("INSERT INTO fields VALUES ('legacy','old','[]','text',?,'v1',0,1)", (vector.tobytes(),))
    store = Store(settings, RoutedEncoder(settings))
    assert store.ids == ["legacy"]
    assert np.array_equal(store.matrices["zh"], store.matrices["en"])
    store.upsert(item())
    store.db.execute("""CREATE TRIGGER reject_localization BEFORE INSERT ON field_localizations
        WHEN NEW.language='en' BEGIN SELECT RAISE(ABORT, 'test failure'); END""")
    with pytest.raises(sqlite3.IntegrityError):
        store.upsert({**item(), "key_zh": "新名称", "key_en": "New name"})
    assert store.db.execute("SELECT key_text FROM field_localizations WHERE field_id='a' AND language='zh'").fetchone()[0] == "中文甲"
    assert store.db.execute("SELECT canonical_text FROM fields WHERE field_id='a'").fetchone()[0] == "English A"
    store.close()


def test_feedback_freezes_both_names_and_selected_language(tmp_path):
    settings = config(tmp_path)
    store = Store(settings, RoutedEncoder(settings))
    store.upsert(item())
    store.feedback({"query_text": "中文询问", "positive_field_id": "a", "retrieved_candidates": [],
                    "source": "human_confirmed", "language_pair": "zh-zh"})
    store.upsert({**item(), "key_zh": "修改后"})
    definition, snapshot = store.feedback_db.execute(
        "SELECT definitions_json, localized_definitions_json FROM feedback"
    ).fetchone()
    assert json.loads(definition)["a"] == "中文甲"
    assert json.loads(snapshot)["a"]["keys"] == {"en": "English A", "zh": "中文甲"}
    store.close()
