"""Synthetic preparation checks; never contacts a browser."""
import json
import subprocess
import sys
import time
import tempfile
import unittest
from pathlib import Path

from preflight import CATEGORIES, check


class PreflightTests(unittest.TestCase):
    def setUp(self):
        self.op = {"id": "F1", "label": "Description", "value": "evidence", "before": "",
                   "source": "facts.md", "action": "fill", "kind": "text", "family": "plain",
                   "path": "dom", "locator": "label within named project", "methodEvidence": "observed DOM",
                   "anchor": {"label": "Project", "value": "A"}}
        self.plan = {"runId": "synthetic", "jd": {"source": "JD", "requirements": [{"id": "R1", "text": "retrieval"}]},
                     "inventory": [{"id": "E1", "source": "facts.md"}],
                     "coverage": [{"id": "E1", "priority": "P0", "requirementIds": ["R1"],
                                   "decision": "include", "operationIds": ["F1"], "reason": "evidence"}],
                     "inventoryReview": {k: {"status": "reviewed", "source": "index.md"} for k in CATEGORIES},
                     "review": {"selectionReviewed": True, "mappingReviewed": True},
                     "execution": {"roundStartedAt": 0, "authorization": {"fill": True, "basis": "User asked to fill"}},
                     "operations": [self.op]}
        self.snapshot = {"fields": [{"label": "Project", "value": "A", "recordIndex": 1},
                                    {"label": "Description", "value": "", "recordIndex": 1}]}
        self.plan['execution'].update(driver={'mode':'direct-tool','name':'test browser'},
            target={'browser':'test','url':'https://example.test/form'},
            probe={'callId':'read-1','settled':True,'observedAt':int(time.time()*1000),
                   'target':{'browser':'test','url':'https://example.test/form'},
                   'operations':[{'id':'F1','count':1,'kind':'text','value':'','anchorMatched':True}]})

    def result(self):
        return check(self.plan, self.snapshot, "local script", "manual list")

    def test_ready(self):
        result = self.result()
        self.assertTrue(result["ready"], result["errors"])

    def test_missing_category_and_silent_omission(self):
        self.plan["inventoryReview"].pop("project")
        self.plan["inventory"].append({"id": "E2", "source": "other.md"})
        self.assertGreaterEqual(len(self.result()["errors"]), 2)

    def test_changed_old_value(self):
        self.snapshot["fields"][1]["value"] = "user edit"
        self.assertFalse(self.result()["ready"])

    def test_ambiguous_record(self):
        self.snapshot["fields"].append({"label": "Project", "value": "A", "recordIndex": 2})
        self.assertFalse(self.result()["ready"])

    def test_unverified_date(self):
        self.op["kind"] = "date"
        self.assertFalse(self.result()["ready"])

    def test_manual_date_allowed(self):
        self.op.update(kind="date", action="manual")
        self.plan["coverage"][0].update(decision="manual", operationIds=[])
        self.assertTrue(self.result()["planReady"])
        self.assertFalse(self.result()["ready"])

    def test_unavailable_jd_is_not_fake_pass(self):
        self.plan["jd"] = {"status": "unavailable", "reason": "personal center"}
        self.plan["coverage"][0].update(priority="unranked", requirementIds=[])
        self.assertFalse(self.result()["ready"])
        self.plan["execution"]["generalProfileBasis"] = "User requested general profile"
        result = self.result()
        self.assertTrue(result["ready"], result["errors"])
        self.assertTrue(result["warnings"])

    def test_duplicate_target(self):
        self.plan["operations"].append({**self.op, "id": "F2"})
        self.assertFalse(self.result()["ready"])

    def test_identity_values_rejected(self):
        self.snapshot["fields"].append({"label": "Passport", "value": "synthetic"})
        self.assertFalse(self.result()["ready"])

    def test_budget_cannot_silently_increase(self):
        self.plan["execution"]["budgetMs"] = 1800001
        self.assertFalse(self.result()["ready"])

    def test_thirty_minute_budget_allowed(self):
        self.plan["execution"]["budgetMs"] = 1800000
        self.assertTrue(self.result()["ready"])

    def test_round_start_required(self):
        del self.plan["execution"]["roundStartedAt"]
        self.assertFalse(self.result()["ready"])

    def test_creation_dependency(self):
        creation = {**self.op, "id": "C1", "action": "add-record", "kind": "record",
                    "label": "Project", "value": "A", "templateId": "T1"}
        self.op.update(dependsOn="C1", templateId="T1")
        self.plan["operations"].insert(0, creation)
        self.snapshot = {"fields": [], "templates": [{"id": "T1", "source": "observed template",
                         "addLocator": "project add button", "fields": [{"label": "Description", "kind": "text"}]}]}
        self.plan['execution']['dispatchIds'] = ['C1']
        self.plan['execution']['probe']['operations'] = [{'id':'C1','count':0,'kind':'record','value':'','anchorMatched':True}]
        self.assertTrue(self.result()["ready"], self.result()["errors"])
        self.op["anchor"] = {"label": "Project", "value": "B"}
        self.assertFalse(self.result()["ready"])

    def test_direct_tool_does_not_require_local_script(self):
        result = check(self.plan, self.snapshot, "", "handoff")
        self.assertTrue(result["ready"])

    def test_comment_only_adapter_rejected(self):
        self.plan['execution']['driver'] = {'mode':'adapter','name':'test','entryPoint':'run'}
        self.plan['execution']['probe']['entryPoint'] = 'run'
        result = check(self.plan, self.snapshot, '/* run() would execute here */')
        self.assertTrue(result['planReady'])
        self.assertFalse(result['ready'])

    def test_missing_probe_does_not_invalidate_plan(self):
        del self.plan['execution']['probe']
        result = self.result()
        self.assertTrue(result['planReady'])
        self.assertFalse(result['ready'])

    def test_stale_and_wrong_target_probes_rejected(self):
        self.plan['execution']['probe']['observedAt'] = 0
        self.assertFalse(self.result()['ready'])
        self.plan['execution']['probe']['observedAt'] = int(time.time()*1000)
        self.plan['execution']['probe']['target']['url'] = 'https://example.test/another'
        self.assertFalse(self.result()['ready'])

    def test_one_observed_field_does_not_authorize_unobserved_batch(self):
        self.plan['operations'].append({**self.op, 'id':'F2','label':'Another'})
        self.snapshot['fields'].append({'label':'Another','value':'','recordIndex':1})
        self.assertFalse(self.result()['ready'])
        self.plan['execution']['dispatchIds'] = ['F1']
        self.assertTrue(self.result()['ready'])

    def test_real_probe_can_find_a_previously_completed_target(self):
        self.plan['execution']['probe']['operations'][0]['value'] = self.op['value']
        self.assertTrue(self.result()['ready'])

    def test_cli_invalidates_stale_report_when_files_missing(self):
        with tempfile.TemporaryDirectory() as directory:
            report = Path(directory) / "preflight.json"
            report.write_text('{"ready":true}', encoding="utf-8")
            proc = subprocess.run([sys.executable, str(Path(__file__).with_name("preflight.py")),
                                   "--run", directory], capture_output=True)
            self.assertEqual(proc.returncode, 1)
            self.assertFalse(json.loads(report.read_text(encoding="utf-8"))["ready"])


if __name__ == "__main__":
    unittest.main()
