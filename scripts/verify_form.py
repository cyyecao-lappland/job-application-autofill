"""Offline record-aware comparison; does not connect to or save any form."""
import argparse
import json
import re
from pathlib import Path

SENSITIVE = re.compile(r"证件号码|证件号|身份证|护照|身份号码|national.?id|passport|social.?security", re.I)


def text(value):
    return "" if value is None else str(value).replace("\r\n", "\n").strip()


def field_value(field):
    # Browser snapshots may omit protected input values. Missing evidence is
    # not an empty field, and a select's search box is not its selected value.
    if field.get("readStatus") in {"unknown", "redacted", "unavailable", "conflict"}:
        return None
    if "selectedValue" in field:
        return None if field["selectedValue"] is None else text(field["selectedValue"])
    if "select" in field or field.get("kind") == "select" or field.get("type") == "select":
        return None  # Capture selectedValue from the closed control explicitly.
    if "value" in field:
        return None if field["value"] is None else text(field["value"])
    if field.get("radio"):
        return text(field["radio"])
    controls = field.get("controls", [])
    if len(controls) == 1 and controls[0].get("type") not in ("file", "password"):
        control = controls[0]
        if control.get("readStatus") in {"unknown", "redacted", "unavailable", "conflict"}:
            return None
        if "selectedValue" in control:
            return None if control["selectedValue"] is None else text(control["selectedValue"])
        if "select" in control or control.get("kind") == "select" or control.get("type") in {"select", "select-one", "select-multiple"}:
            return None
        if "value" in control and control["value"] is not None:
            return text(control["value"])
    return None


def targets(plan):
    yield from plan.get("operations", [])
    for record in plan.get("records", []):
        for operation in record.get("operations", []):
            yield {**operation, "anchor": {"label": record["anchorLabel"], "value": record["name"]}}


def compare(plan, snapshot, evidence):
    if evidence not in ("page", "reloaded"):
        raise ValueError("evidence must be page or reloaded")
    requested_evidence = evidence
    capture = snapshot.get("capture", {})
    receipt = capture.get("saveReceipt", {})
    persistence_recorded = (capture.get("stage") == "after-reopen" and
                            bool(capture.get("readCallId")) and bool(capture.get("reopenCallId")) and
                            receipt.get("status") == "saved" and bool(receipt.get("callId")) and
                            bool(receipt.get("indicator")))
    warnings = []
    if evidence == "reloaded" and not persistence_recorded:
        evidence = "page"
        warnings.append("No recorded save/reopen/read evidence; comparison downgraded to page only")
    fields = [f for f in snapshot["fields"]
              if not f.get("excluded") and not SENSITIVE.search(f.get("label", ""))]
    result = []
    for op in targets(plan):
        anchor = op.get("anchor")
        if SENSITIVE.search(op["label"]) or (anchor and SENSITIVE.search(anchor["label"])):
            raise ValueError("Identity-document fields cannot be planned or verified")
        if op.get("action", "fill") not in {"fill", "add-record"}:
            continue
        row = {"label": op["label"], "anchor": anchor, "expected": op["value"]}
        if op["value"] is None or (anchor and anchor.get("value") is None):
            result.append({**row, "status": "unverified", "reason": "Expected source value is unknown; do not infer empty."})
            continue
        scope = fields
        if anchor:
            if any(f["label"] == anchor["label"] and field_value(f) is None for f in fields):
                result.append({**row, "status": "anchor-unverified"})
                continue
            anchors = [f for f in fields if f["label"] == anchor["label"]
                       and field_value(f) == text(anchor["value"])]
            if len(anchors) != 1:
                result.append({**row, "status": "anchor-missing" if not anchors else "anchor-ambiguous"})
                continue
            record_index = anchors[0].get("recordIndex")
            if record_index is None:
                result.append({**row, "status": "record-boundary-missing"})
                continue
            scope = [f for f in fields if f.get("recordIndex") == record_index]
        candidates = [f for f in scope if f["label"] == op["label"]]
        if len(candidates) != 1:
            result.append({**row, "status": "field-missing" if not candidates else "field-ambiguous"})
            continue
        actual = field_value(candidates[0])
        if actual is None:
            result.append({**row, "status": "unverified",
                           "reason": "Current value is unavailable or conflicting; do not infer empty or replay a write."})
            continue
        result.append({**row, "actual": actual,
                       "status": "matched" if actual == text(op["value"]) else "mismatch"})
    return {"evidence": evidence, "requestedEvidence": requested_evidence,
            "persistenceEvidenceRecorded": bool(persistence_recorded), "warnings": warnings,
            "matched": sum(r["status"] == "matched" for r in result),
            "unverified": sum(r["status"] in {"unverified", "anchor-unverified"} for r in result),
            "total": len(result), "results": result, "manual": plan.get("manual", []),
            "note": "Counts cover planned targets only. Save provenance is supplied by the caller and must come from real tool results; this is not a submission receipt."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--plan", required=True, type=Path)
    parser.add_argument("--snapshot", required=True, type=Path)
    parser.add_argument("--evidence", required=True, choices=("page", "reloaded"))
    parser.add_argument("--out", required=True, type=Path)
    args = parser.parse_args()
    report = compare(json.loads(args.plan.read_text(encoding="utf-8-sig")),
                     json.loads(args.snapshot.read_text(encoding="utf-8-sig")), args.evidence)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"evidence": report["evidence"], "matched": report["matched"],
                      "total": report["total"], "out": str(args.out)}, ensure_ascii=False))


if __name__ == "__main__":
    main()
