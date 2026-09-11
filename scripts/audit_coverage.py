"""Check local JD/experience/operation links; does not judge relevance or operate browsers."""
import argparse
import json
from pathlib import Path

DECISIONS = {"include", "brief", "merge", "omit-relevance", "omit-capacity",
             "manual", "missing-fact", "no-field"}
PRESENTED = {"include", "brief", "merge"}


def audit(plan):
    errors, warnings = [], []

    def index(items, category):
        indexed = {}
        for item in items:
            key = item.get("id")
            if not isinstance(key, str) or not key.strip():
                errors.append(f"{category}: missing id")
            elif key in indexed:
                errors.append(f"{category}: duplicate id {key}")
            else:
                indexed[key] = item
        return indexed

    jd = plan.get("jd", {})
    unavailable = jd.get("status") == "unavailable"
    if unavailable and not jd.get("reason"):
        errors.append("Unavailable JD needs a reason")
    if unavailable:
        warnings.append("JD selection incomplete; priorities are unranked")
    if not unavailable and not jd.get("source"):
        errors.append("JD source missing; do not claim JD-based selection")
    requirements = index(jd.get("requirements", []), "requirement")
    if not unavailable and not requirements:
        errors.append("No JD requirements recorded")
    if unavailable and requirements:
        errors.append("Unavailable JD must not contain inferred requirements")
    for key, item in requirements.items():
        if not item.get("text"):
            errors.append(f"{key}: requirement text missing")
    inventory = index(plan.get("inventory", []), "inventory")
    if not inventory:
        errors.append("No experience inventory recorded")
    coverage = index(plan.get("coverage", []), "coverage")
    operations_list = list(plan.get("operations", []))
    for record in plan.get("records", []):
        operations_list.extend(record.get("operations", []))
    operations = index(operations_list, "operation")
    for key, op in operations.items():
        if op.get("action", "fill") in {"fill", "add-record"} and not op.get("source"):
            errors.append(f"{key}: source missing for proposed value")
    for key, item in inventory.items():
        if not item.get("source"):
            errors.append(f"{key}: inventory source missing")
        if key not in coverage:
            errors.append(f"{key}: no selection decision (silent omission)")
    for key, row in coverage.items():
        if key not in inventory:
            errors.append(f"{key}: coverage references unknown experience")
        priority, decision = row.get("priority"), row.get("decision")
        if priority not in ({"unranked"} if unavailable else {"P0", "P1", "P2"}):
            errors.append(f"{key}: invalid priority")
        if unavailable and decision == "omit-relevance":
            errors.append(f"{key}: cannot claim JD relevance without a JD")
        if decision not in DECISIONS:
            errors.append(f"{key}: invalid selection decision")
        if not row.get("reason"):
            errors.append(f"{key}: decision reason missing")
        links = row.get("requirementIds", [])
        if priority in {"P0", "P1"} and not links and not row.get("mandatory"):
            errors.append(f"{key}: priority lacks JD link")
        for req in links:
            if req not in requirements:
                errors.append(f"{key}: unknown JD requirement {req}")
        if decision in PRESENTED:
            if not row.get("operationIds"):
                errors.append(f"{key}: selected content has no destination")
            for op_id in row.get("operationIds", []):
                op = operations.get(op_id)
                if not op or op.get("action", "fill") not in {"fill", "keep", "add-record"}:
                    errors.append(f"{key}: destination {op_id} is missing or not presented")
        elif priority == "P0":
            warnings.append(f"{key}: P0 not presented ({decision}); include in handoff")
        if decision == "merge":
            target = row.get("mergeInto")
            if target == key or target not in coverage or coverage[target].get("decision") not in PRESENTED:
                errors.append(f"{key}: invalid merge target")
    for key in coverage:
        seen, current = set(), key
        while current in coverage and coverage[current].get("decision") == "merge":
            if current in seen:
                errors.append(f"{key}: cyclic merge")
                break
            seen.add(current)
            current = coverage[current].get("mergeInto")
    return {"ok": not errors, "inventoryCount": len(inventory),
            "decisionCount": len(coverage), "errors": errors, "warnings": warnings}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    result = audit(json.loads(args.plan.read_text(encoding="utf-8-sig")))
    output = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(output, encoding="utf-8")
    print(output, end="")
    raise SystemExit(0 if result["ok"] else 1)


if __name__ == "__main__":
    main()
