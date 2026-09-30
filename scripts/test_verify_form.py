"""Synthetic snapshot regressions; no browser or private profile access."""
import unittest
from verify_form import compare, field_value


class SnapshotTests(unittest.TestCase):
    def report(self, field, expected="Expected"):
        return compare({"operations": [{"label": "学校", "value": expected}]},
                       {"fields": [{"label": "学校", **field}]}, "page")

    def test_missing_unknown_and_redacted_are_not_empty(self):
        for field in ({}, {"value": None}, {"value": "", "readStatus": "unknown"},
                      {"value": "Expected", "readStatus": "redacted"},
                      {"controls": [{"value": "", "readStatus": "conflict"}]}):
            with self.subTest(field=field):
                result = self.report(field, "")
                self.assertEqual(result["unverified"], 1)
                self.assertEqual(result["matched"], 0)
                self.assertNotIn("actual", result["results"][0])

    def test_explicit_empty_can_match_or_mismatch(self):
        self.assertEqual(self.report({"value": ""}, "")["matched"], 1)
        self.assertEqual(self.report({"value": ""})["results"][0]["status"], "mismatch")

    def test_unknown_expected_source_does_not_match_empty(self):
        self.assertEqual(self.report({"value": ""}, None)["unverified"], 1)

    def test_select_search_value_is_never_selected_value(self):
        for field in ({"select": [], "value": "Expected"}, {"select": True, "value": "Expected"},
                      {"kind": "select", "value": "Expected"},
                      {"controls": [{"type": "select-one", "value": "Expected"}]}):
            with self.subTest(field=field):
                self.assertEqual(self.report(field)["unverified"], 1)

    def test_select_uses_independent_selected_value(self):
        self.assertEqual(self.report({"select": True, "value": "search", "selectedValue": "Expected"})["matched"], 1)
        self.assertEqual(self.report({"controls": [{"type": "select-one", "value": "search", "selectedValue": "Expected"}]})["matched"], 1)
        self.assertEqual(self.report({"selectedValue": None, "value": "Expected"})["unverified"], 1)

    def test_unknown_anchor_cannot_prove_record_identity(self):
        plan = {"records": [{"anchorLabel": "项目", "name": "Example", "operations": [{"label": "职责", "value": "work"}]}]}
        snapshot = {"fields": [{"label": "项目", "recordIndex": 1, "readStatus": "unknown"},
                               {"label": "职责", "recordIndex": 1, "value": "work"}]}
        result = compare(plan, snapshot, "page")
        self.assertEqual(result["results"][0]["status"], "anchor-unverified")

    def test_date_reset_is_visible_in_final_snapshot(self):
        plan = {"operations": [{"label": "开始月份", "value": "09"}]}
        result = compare(plan, {"fields": [{"label": "开始月份", "selectedValue": "01", "select": True}]}, "page")
        self.assertEqual(result["results"][0]["status"], "mismatch")

    def test_reloaded_without_provenance_is_downgraded(self):
        result = compare({"operations": []}, {"fields": []}, "reloaded")
        self.assertEqual(result["evidence"], "page")
        self.assertFalse(result["persistenceEvidenceRecorded"])

    def test_sensitive_fields_rejected_without_echoing_values(self):
        with self.assertRaises(ValueError) as error:
            compare({"operations": [{"label": "证件号码", "value": "SYNTHETIC-ONLY"}]}, {"fields": []}, "page")
        self.assertNotIn("SYNTHETIC-ONLY", str(error.exception))


if __name__ == "__main__":
    unittest.main()
