"""Build bilingual names from schema terms, never from benchmark query labels."""
import argparse
import csv
import json
from pathlib import Path
import sys

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.schemas import FieldDefinition
from scripts.evaluate_profile_keys import SECTIONS

# Context-specific meanings that cannot be conveyed by a leaf token alone.
OVERRIDES = {
    "identity/name": ("本人姓名", "Applicant full name"),
    "family/*/name": ("亲属姓名", "Family member name"),
    "education/*/start_date": ("入学日期", "Enrollment date"),
    "education/*/end_date": ("学习截止日期", "End of studies"),
    "employment/*/start_date": ("任职开始日期", "Date employment began"),
    "employment/*/end_date": ("任职终止日期", "Date employment ended"),
    "family/*/position": ("亲属职位", "Family member occupation"),
    "projects/*/name": ("项目名称", "Project name"),
    "awards/*/name": ("奖项名称", "Award name"),
    "skills/*/name": ("技能名称", "Skill name"),
    "campus/*/role": ("校园任职", "Campus appointment"),
}


def build(keys, terms_path):
    with terms_path.open(encoding="utf-8", newline="") as stream:
        terms = {row["token"]: (row["zh"], row["en"]) for row in csv.DictReader(stream, delimiter="\t")}
    fields = []
    for key in sorted(keys):
        tokens = key.split("/")
        section = tokens[0]
        if key in OVERRIDES:
            names = OVERRIDES[key]
        else:
            path_terms = [terms[token] for token in tokens[1:] if token != "*"]
            names = tuple(" / ".join(term[i] for term in path_terms) for i in (0, 1))
        item = {
            "field_id": ".".join(token for token in tokens if token != "*"),
            "source_key": key,
            "key_zh": SECTIONS[section][0] + " / " + names[0],
            "key_en": SECTIONS[section][1] + " / " + names[1],
            "field_type": "unspecified",
        }
        FieldDefinition.model_validate(item)
        fields.append(item)
    if len({f["field_id"] for f in fields}) != len(fields):
        raise ValueError("Colliding field ids")
    return {"schema_version": "bilingual-field-library/v1", "fields": fields}


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--keys", type=Path, required=True)
    parser.add_argument("--output", type=Path, default=Path("library/profile_fields.json"))
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    library = build(json.loads(args.keys.read_text(encoding="utf-8")), root / "library/key_terms.tsv")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x", encoding="utf-8") as stream:
        json.dump(library, stream, ensure_ascii=False, indent=2)
        stream.write("\n")
    print(json.dumps({"fields": len(library["fields"]), "output": str(args.output)}))
