"""Explicit live model smoke using invented JSON, never a real website or profile."""
import copy
import json
from pathlib import Path
import time
import uuid

from edge_form_graph.model import CodexJsonModel
from edge_form_graph.contracts import compile_plan
from edge_form_graph.cli import atomic_json
from tests.test_graph import snapshot


def main():
    profile = {"school": "示例大学"}
    before = snapshot()
    model = CodexJsonModel()
    plan = model.map(profile, before)
    operations, deferred = compile_plan(profile, before, plan)
    after = copy.deepcopy(before)
    after["snapshot_id"] = str(uuid.uuid4())
    after["observed_at"] = time.time()
    for op in operations:
        next(f for f in after["fields"] if f["id"] == op["id"])["value"] = op["value"]
    review = model.review(profile, before, plan, after)
    result = {"model_calls": model.calls, "mapping_count": len(operations), "deferred_count": len(deferred),
              "review_approved": review["approved"], "review_issues": review["issues"],
              "synthetic_profile": True, "synthetic_page": True,
              "browser_actions": 0}
    folder = Path(__file__).resolve().parent.parent / "private"
    folder.mkdir(exist_ok=True)
    atomic_json(folder / "model-smoke.json", result)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if len(operations) != 1 or deferred or review["approved"] is not True:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
