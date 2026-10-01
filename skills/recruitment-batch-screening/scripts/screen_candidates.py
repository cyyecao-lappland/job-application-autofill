"""Candidate extraction helpers and v2 initialization entry point."""
import json
import re
import sys
import unicodedata


def load_json(path):
    return json.loads(path.read_text(encoding="utf-8-sig"))


def walk_dicts(node, path=()):
    if isinstance(node, dict):
        yield node, path
        for key, value in node.items():
            yield from walk_dicts(value, path + (key,))
    elif isinstance(node, list):
        for index, value in enumerate(node):
            yield from walk_dicts(value, path + (index,))


def first(record, keys):
    return next((record[k] for k in keys if record.get(k) not in (None, "")), "")


def normalize_company(value):
    text = unicodedata.normalize("NFKC", str(value)).casefold()
    text = re.sub(r"[\s\W_]+", "", text)
    # Keep geography and group/subsidiary terms. Other names require explicit aliases.
    for suffix in ("股份有限公司", "有限责任公司", "有限公司"):
        if text.endswith(suffix) and len(text) > len(suffix):
            return text[:-len(suffix)]
    return text


COMPANY = ("company_name", "companyName", "employer_name", "employer")
TITLE = ("job_name", "jobName", "job_title", "jobTitle", "position", "title")


def looks_like_candidate(record):
    return bool(first(record, COMPANY) and first(record, TITLE))


def record_for_review(record, source_file, source_path, ordinal):
    name = str(first(record, COMPANY))
    return {
        "candidate_id": "candidate-" + str(ordinal),
        "company_id": str(first(record, ("company_id", "companyId", "enterprise_id"))),
        "company_name": name, "company_key": normalize_company(name),
        "aliases": record.get("aliases", []),
        "job_id": str(first(record, ("job_id", "jobId", "position_id"))),
        "job_name": str(first(record, TITLE)),
        "detail_url": str(first(record, ("detail_url", "detailUrl", "job_url", "url"))),
        "apply_url": str(first(record, ("apply_url", "applyUrl", "apply_instruction"))),
        "city": first(record, ("city", "cities", "location")),
        "education": record.get("education", ""),
        "experience": record.get("experience", ""),
        "recruitment_type": record.get("recruitment_type", ""),
        "deadline": first(record, ("end_time", "deadline")),
        "jd_text": first(record, ("contents", "jd_text", "description")),
        "source_file": str(source_file), "source_path": list(source_path),
    }


if __name__ == "__main__":
    from screening_run import main
    raise SystemExit(main(["init", *sys.argv[1:]]))
