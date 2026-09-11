"""Offline readiness check. No browser access, no authorization grant."""
import argparse
import json
import re
import time
from pathlib import Path

from audit_coverage import audit
from verify_form import SENSITIVE, compare, targets

KINDS = {"text", "radio", "select", "date", "cascade", "search", "multi",
         "editor", "file", "captcha", "record"}
COMPLEX = {"date", "cascade", "search", "multi", "editor", "file", "captcha"}
CATEGORIES = {"education", "internship", "project", "research", "award", "campus", "skill"}


def assess_readiness(plan, batch, now_ms):
    """Check recorded dispatch evidence, not the truth of a caller's tool result."""
    execution = plan.get("execution", {})
    driver = execution.get("driver", {})
    errors = []
    automatic = {o.get("id"): o for o in targets(plan)
                 if o.get("action") in {"fill", "add-record"}}
    # 全量计划可跨批次保留，但定位证据只需覆盖本次真正派发的目标。
    requested = execution.get("dispatchIds", list(automatic))
    if not requested:
        errors.append("No automatic targets; this is a handoff plan")
    if len(requested) != len(set(requested)) or any(i not in automatic for i in requested):
        errors.append("dispatchIds must name unique automatic targets")
    if driver.get("mode") not in {"direct-tool", "adapter"} or not driver.get("name"):
        errors.append("Choose the actual available direct-tool or adapter driver")
    if driver.get("mode") == "adapter":
        # Reject known empty scaffolds. This is deliberately not a JS validator.
        code = re.sub(r"/\*.*?\*/|//[^\n]*", "", batch or "", flags=re.S).strip()
        if not code or not driver.get("entryPoint"):
            errors.append("Adapter needs executable code and an entry point, not a manifest/comment")
    target = execution.get("target", {})
    probe = execution.get("probe", {})
    if not target.get("url") or not target.get("browser") or probe.get("target") != target:
        errors.append("Read-only probe must match the current browser and application target")
    observed = probe.get("observedAt")
    if (type(observed) is not int or observed > now_ms or now_ms - observed > 300000):
        errors.append("Probe missing or older than five minutes; refresh only the read-only probe")
    if not probe.get("callId") or probe.get("settled") is not True:
        errors.append("Probe needs the returned tool call id and a settled result")
    if driver.get("mode") == "adapter" and probe.get("entryPoint") != driver.get("entryPoint"):
        errors.append("Probe must exercise the actual adapter entry point")
    rows = probe.get("operations", [])
    indexed = {r.get("id"): r for r in rows}
    if len(indexed) != len(rows):
        errors.append("Duplicate operation in probe")
    for key in requested:
        op, row = automatic.get(key, {}), indexed.get(key, {})
        expected_count = 0 if op.get("action") == "add-record" else 1
        # 恢复时当前值可能已是目标值；允许这种探测结果，交执行器跳过已完成项。
        already_matches = row.get("count") == 1 and row.get("value") == op.get("value")
        before_matches = row.get("count") == expected_count and row.get("value") == op.get("before")
        if (not (already_matches or before_matches) or row.get("kind") != op.get("kind") or
                (op.get("anchor") and row.get("anchorMatched") is not True)):
            errors.append(f"{key}: real current-value/control probe missing or mismatched")
    return errors, requested


def check(plan, snapshot, batch=None, handoff=None, now_ms=None):
    coverage = audit(plan)
    errors = list(coverage["errors"])
    ops = list(targets(plan))
    by_id = {op.get("id"): op for op in ops}
    if not isinstance(plan.get("runId"), str) or not plan["runId"].strip():
        errors.append("runId missing")
    review = plan.get("inventoryReview", {})
    for category in sorted(CATEGORIES):
        row = review.get(category, {})
        if row.get("status") not in {"reviewed", "none"} or not row.get("source"):
            errors.append(f"Inventory category not reviewed: {category}")
    execution = plan.get("execution", {})
    if execution.get("authorization", {}).get("fill") is not True or not execution.get("authorization", {}).get("basis"):
        errors.append("Existing user authorization and basis must be recorded")
    if type(execution.get("roundStartedAt")) is not int or execution["roundStartedAt"] < 0:
        errors.append("Original round preparation start is required: roundStartedAt (Unix milliseconds)")
    limits = {"budgetMs": 1800000, "operationTimeoutMs": 120000, "saveTimeoutMs": 180000,
              "connectionAttempts": 2, "familyFailures": 2}
    for name, default in limits.items():
        value = execution.get(name, default)
        if type(value) is not int or value < 1:
            errors.append(f"Invalid limit: {name}")
        elif value != default and not execution.get("overrideBasis"):
            errors.append(f"Changed limit needs explicit user basis: {name}")
    for name in ("selectionReviewed", "mappingReviewed"):
        if plan.get("review", {}).get(name) is not True:
            errors.append(f"Human/model source review not recorded: {name}")
    if not isinstance(snapshot.get("fields"), list):
        errors.append("Safe snapshot fields missing")
        snapshot = {"fields": []}
    for field in snapshot["fields"]:
        if SENSITIVE.search(field.get("label", "")) and set(field) - {"label", "excluded"}:
            errors.append("Snapshot contains an identity field beyond an excluded label")
    templates = {t.get("id"): t for t in snapshot.get("templates", [])}
    seen = set()
    for op in ops:
        key = op.get("id", "(missing)")
        action = op.get("action")
        if action not in {"fill", "keep", "manual", "missing-fact", "add-record"}:
            errors.append(f"{key}: explicit action required")
        if SENSITIVE.search(op.get("label", "")) or SENSITIVE.search(op.get("anchor", {}).get("label", "")):
            errors.append(f"{key}: identity fields must stay outside the plan")
            continue
        if action not in {"fill", "add-record"}:
            continue
        if not op.get("label") or "value" not in op or "before" not in op:
            errors.append(f"{key}: label, value and before required")
        if op.get("kind") not in KINDS or not op.get("family") or not op.get("locator"):
            errors.append(f"{key}: kind, control family and observed locator required")
        if op.get("path") not in {"dom", "native", "visual"}:
            errors.append(f"{key}: choose one approved input path")
        if not op.get("methodEvidence"):
            errors.append(f"{key}: observed method evidence missing")
        # 复杂控件的显示值可能未进入站点表单状态，因此要求曾验证保存的方法。
        if op.get("kind") in COMPLEX and not op.get("savedMethodEvidence"):
            errors.append(f"{key}: complex field lacks prior saved verification; use manual")
        if plan.get("jd", {}).get("status") == "unavailable" and op.get("scope") != "basic" and not execution.get("generalProfileBasis"):
            errors.append(f"{key}: no JD; non-basic content needs user general-profile scope")
        token = json.dumps([op.get("anchor"), op.get("label")], sort_keys=True, ensure_ascii=False)
        if token in seen:
            errors.append(f"{key}: duplicate write target")
        seen.add(token)
        # 新记录必须先建立锚点，再填写其子字段，避免把值写进另一条同名记录。
        dependency = op.get("dependsOn")
        creation = by_id.get(dependency, {})
        if dependency and (creation.get("action") != "add-record" or ops.index(creation) >= ops.index(op)):
            errors.append(f"{key}: dependency must be an earlier add-record operation")
        if action == "add-record" or dependency:
            if action == "add-record" and op.get("kind") != "record":
                errors.append(f"{key}: add-record requires kind record")
            template = templates.get(op.get("templateId"), {})
            if not template.get("source") or not template.get("addLocator"):
                errors.append(f"{key}: observed new-record template missing")
            if not op.get("anchor") or op.get("before") != "":
                errors.append(f"{key}: new record requires a unique planned anchor and empty before")
            if dependency and (op.get("anchor") != creation.get("anchor") or op.get("templateId") != creation.get("templateId")):
                errors.append(f"{key}: dependency record/template differs")
            if action == "add-record":
                anchor = op.get("anchor", {})
                if op.get("label") != anchor.get("label") or op.get("value") != anchor.get("value"):
                    errors.append(f"{key}: creation must initialize its anchor")
                if any(f.get("label") == anchor.get("label") and f.get("value") == anchor.get("value") for f in snapshot["fields"]):
                    errors.append(f"{key}: planned new record already exists")
            elif not any(f.get("label") == op.get("label") and f.get("kind") == op.get("kind") for f in template.get("fields", [])):
                errors.append(f"{key}: target not present in observed template")
            continue
        if op.get("label") and "before" in op:
            probe = {"operations": [{**op, "action": "fill", "value": op["before"]}]}
            result = compare(probe, snapshot, "page")
            if result["matched"] != 1:
                errors.append(f"{key}: old value or unique record mapping does not match snapshot")
    modules = plan.get("modules", [])
    module_ids, assigned = set(), set()
    for module in modules:
        mid = module.get("id")
        if not mid or mid in module_ids:
            errors.append("Module ids must be present and unique")
        module_ids.add(mid)
        for key in module.get("operationIds", []):
            if key not in by_id or key in assigned:
                errors.append(f"{mid}: unknown or multiply assigned operation {key}")
            assigned.add(key)
    readiness, dispatch_ids = assess_readiness(plan, batch, now_ms if now_ms is not None else int(time.time() * 1000))
    # planReady 是计划结构通过；ready 还要求本批真实调用证据齐备。
    # checkedPlan/checkedBatch 供执行入口比对，不能证明证据真实或已完成独立核验。
    return {"schemaVersion": 2, "planReady": not errors,
            "ready": not errors and not readiness, "runId": plan.get("runId"), "errors": errors,
            "executionErrors": readiness, "dispatchIds": dispatch_ids,
            "checkedPlan": plan if not errors and not readiness else None,
            "checkedBatch": batch if not errors and not readiness and plan.get("execution", {}).get("driver", {}).get("mode") == "adapter" else None,
            "warnings": coverage["warnings"], "operationCount": len(ops),
            "automaticCount": sum(o.get("action") in {"fill", "add-record"} for o in ops),
            "note": "Plan and recorded probe checks only; no browser execution, fact verification, permission grant or save proof. Tool evidence must come from actual returned calls."}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", required=True, type=Path)
    args = parser.parse_args()
    root = args.run
    try:
        plan = json.loads((root / "alignment-plan.json").read_text(encoding="utf-8-sig"))
        snapshot = json.loads((root / "before.json").read_text(encoding="utf-8-sig"))
        batch = (root / "batch.js").read_text(encoding="utf-8-sig") if (root / "batch.js").exists() else None
        handoff = (root / "handoff.md").read_text(encoding="utf-8-sig") if (root / "handoff.md").exists() else None
        result = check(plan, snapshot, batch, handoff)
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        result = {"schemaVersion": 2, "planReady": False, "ready": False,
                  "errors": ["Missing, unreadable or malformed preparation files"]}
    # Invalidate a stale ready report even when this check fails.
    root.mkdir(parents=True, exist_ok=True)
    (root / "preflight.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({k: v for k, v in result.items() if k not in {"checkedPlan", "checkedBatch"}}, ensure_ascii=False))
    raise SystemExit(0 if result["ready"] else 1)


if __name__ == "__main__":
    main()
