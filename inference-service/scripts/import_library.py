"""Offline library provisioning. The service must be stopped."""
import argparse
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.config import Settings
from app.encoder import Encoder
from app.lock import InstanceLock
from app.schemas import FieldDefinition
from app.storage import Store


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--library", type=Path, default=Path(__file__).resolve().parents[1] / "library/profile_fields.json")
    args = parser.parse_args()
    library = json.loads(args.library.read_text(encoding="utf-8"))
    fields = [FieldDefinition.model_validate(f).model_dump() for f in library["fields"]]
    if len({f["field_id"] for f in fields}) != len(fields):
        raise ValueError("Duplicate field_id")
    settings = Settings.from_env()
    lock = InstanceLock(settings.data_dir)
    try:
        store = Store(settings, Encoder(settings))
        try:
            for definition in fields:
                store.upsert(definition)
            count = store.db.execute("SELECT COUNT(*) FROM field_localizations").fetchone()[0]
            print(json.dumps({"imported": len(fields), "active": len(store.ids), "localized_vectors": count}))
        finally:
            store.close()
    finally:
        lock.close()


if __name__ == "__main__":
    main()
