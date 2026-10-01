"""Offline export; never trains or changes online models."""
import argparse
import json
from pathlib import Path
import sqlite3


def export(database, output):
    with sqlite3.connect(database.resolve().as_uri() + "?mode=ro", uri=True) as db:
        rows = db.execute("""SELECT id, query_text, positive_field_id,
            retrieved_candidates_json, definitions_json, language_pair, source, model_version
            FROM feedback ORDER BY id""")
        with output.open("x", encoding="utf-8") as stream:
            for identifier, query, positive_id, candidates, definitions, pair, source, version in rows:
                definitions = json.loads(definitions)
                negatives = list(dict.fromkeys(fid for fid, _ in json.loads(candidates) if fid != positive_id))
                item = {
                    "query": query, "positive": definitions[positive_id],
                    "hard_negatives": [definitions[fid] for fid in negatives],
                    "language_pair": pair, "feedback_id": identifier,
                    "source": source, "model_version": version,
                }
                stream.write(json.dumps(item, ensure_ascii=False) + "\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", type=Path, default=Path("data/training_feedback.db"))
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    export(args.database, args.output)
