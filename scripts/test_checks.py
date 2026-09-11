"""Offline behavioral checks with synthetic data; no website access."""
import unittest

from audit_coverage import audit
from verify_form import compare, field_value


class Checks(unittest.TestCase):
    def setUp(self):
        self.plan = {
            "jd": {"source": "official JD", "requirements": [{"id": "R1", "text": "retrieval"}]},
            "inventory": [{"id": "E1", "source": "facts.md"}],
            "coverage": [{"id": "E1", "priority": "P0", "requirementIds": ["R1"],
                          "decision": "include", "operationIds": ["O1"], "reason": "direct evidence"}],
            "operations": [{"id": "O1", "label": "Description", "value": "evidence",
                            "source": "facts.md", "anchor": {"label": "Paper", "value": "A"}}]
        }
        self.snapshot = {"fields": [
            {"label": "Description", "value": "wrong", "recordIndex": 9},
            {"label": "Paper", "value": "B", "recordIndex": 9},
            {"label": "Description", "value": "evidence", "recordIndex": 2},
            {"label": "Paper", "value": "A", "recordIndex": 2}]}

    def test_valid_coverage(self):
        self.assertTrue(audit(self.plan)["ok"])

    def test_silent_omission_rejected(self):
        self.plan["inventory"].append({"id": "E2", "source": "other.md"})
        self.assertFalse(audit(self.plan)["ok"])

    def test_deliberate_omission_allowed(self):
        self.plan["coverage"][0].update(priority="P2", decision="omit-relevance",
                                        operationIds=[], reason="unrelated")
        self.assertTrue(audit(self.plan)["ok"])

    def test_missing_destination_and_fake_requirement(self):
        self.plan["coverage"][0].update(operationIds=["absent"], requirementIds=["fake"])
        self.assertGreaterEqual(len(audit(self.plan)["errors"]), 2)

    def test_missing_provenance(self):
        self.plan["operations"][0].pop("source")
        self.assertFalse(audit(self.plan)["ok"])

    def test_merge_cycle_rejected(self):
        self.plan["coverage"][0].update(decision="merge", mergeInto="E1")
        self.assertFalse(audit(self.plan)["ok"])

    def test_reordered_records_and_paper_anchor(self):
        result = compare(self.plan, self.snapshot, "reloaded")
        self.assertEqual(result["matched"], 1)
        self.snapshot["fields"].reverse()
        self.assertEqual(compare(self.plan, self.snapshot, "reloaded")["matched"], 1)

    def test_duplicate_anchor_not_first_match(self):
        self.snapshot["fields"].append({"label": "Paper", "value": "A", "recordIndex": 8})
        self.assertEqual(compare(self.plan, self.snapshot, "page")["results"][0]["status"], "anchor-ambiguous")

    def test_record_boundary_missing(self):
        self.snapshot["fields"][-1].pop("recordIndex")
        self.assertEqual(compare(self.plan, self.snapshot, "page")["results"][0]["status"], "record-boundary-missing")

    def test_page_does_not_claim_saved(self):
        self.assertEqual(compare(self.plan, self.snapshot, "page")["evidence"], "page")

    def test_reloaded_flag_without_receipts_is_downgraded(self):
        result = compare(self.plan, self.snapshot, 'reloaded')
        self.assertEqual(result['evidence'], 'page')
        self.assertFalse(result['persistenceEvidenceRecorded'])
        self.assertTrue(result['warnings'])

    def test_reloaded_requires_recorded_save_and_reopen_calls(self):
        self.snapshot['capture'] = {'stage':'after-reopen','readCallId':'read-2','reopenCallId':'open-2',
                                   'saveReceipt':{'status':'saved','callId':'save-1','indicator':'saved card visible'}}
        self.assertEqual(compare(self.plan,self.snapshot,'reloaded')['evidence'],'reloaded')
        del self.snapshot['capture']['saveReceipt']['indicator']
        self.assertEqual(compare(self.plan,self.snapshot,'reloaded')['evidence'],'page')

    def test_created_record_is_in_verification_denominator(self):
        self.plan["operations"][0].update(action="add-record", label="Paper", value="A")
        result = compare(self.plan, self.snapshot, "page")
        self.assertEqual(result["total"], 1)
        self.assertEqual(result["matched"], 1)

    def test_missing_field_is_not_success(self):
        self.snapshot["fields"].pop(2)
        self.assertEqual(compare(self.plan, self.snapshot, "reloaded")["matched"], 0)

    def test_select_uses_selection_not_search(self):
        field = {"select": True, "display": "Language\nEnglish\nEnglish",
                 "controls": [{"value": "typed search"}]}
        self.assertIsNone(field_value(field))
        field["selectedValue"] = "English"
        self.assertEqual(field_value(field), "English")

    def test_unknown_value_does_not_match_empty(self):
        self.plan["operations"][0]["value"] = ""
        self.snapshot["fields"][2].pop("value")
        result = compare(self.plan, self.snapshot, "page")
        self.assertEqual(result["matched"], 0)
        self.assertEqual(result["results"][0]["status"], "unverified")

    def test_redacted_selection_is_unverified(self):
        self.assertIsNone(field_value({"selectedValue": "English", "readStatus": "redacted"}))

    def test_identity_plan_rejected_and_excluded_snapshot_safe(self):
        self.snapshot["fields"].append({"label": "Passport", "excluded": True})
        self.assertEqual(compare(self.plan, self.snapshot, "page")["matched"], 1)
        self.plan["operations"][0]["label"] = "Passport"
        with self.assertRaises(ValueError):
            compare(self.plan, self.snapshot, "page")


if __name__ == "__main__":
    unittest.main()
