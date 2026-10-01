"""Synthetic contract tests only. No live browser or recruitment claims."""
import copy
from datetime import datetime, timezone, timedelta
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from screening_run import init_run, advance, load_json, validate_review, langgraph_companies, denylist


class EvidenceGates(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(prefix="synthetic-screening-")
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.run = self.root / "run"
        self.profile = self.root / "profile.md"
        self.criteria = self.root / "criteria.md"
        self.profile.write_text("SYNTHETIC_TEST 硕士 2027 计算机 上海 Python", encoding="utf-8")
        self.criteria.write_text("SYNTHETIC_TEST 2027 全职 算法 上海", encoding="utf-8")
        self.haitou = self.put("haitou.json", {"applications": []})
        self.jobs = [{"company_name": "模拟甲", "job_name": "算法岗位", "job_id": str(i),
                      "detail_url": f"https://portal.example.invalid/job/{i}"} for i in range(1, 4)]
        self.candidates = self.put("candidates.json", {"jobs": self.jobs})
        self.config = {"schema": "recruitment-screening-config-v2", "target": 1,
                       "target_unit": "companies", "candidate_files": [str(self.candidates)],
                       "profile_path": str(self.profile), "criteria_path": str(self.criteria),
                       "denylist_sources": [{"role": "haitou_company_exclusion",
                         "confirmed_via": "SYNTHETIC_TEST", "path": str(self.haitou),
                         "records_pointer": "/applications", "company_name_pointer": "/company"}],
                       "official_roots": [{"company_key": "模拟甲", "url": "https://official.example.invalid/careers",
                                           "verified_via": "SYNTHETIC_TEST"}]}

    def put(self, relative, data):
        destination = self.root / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
        return destination

    def start(self):
        return init_run(self.put("config.json", self.config), self.run)

    def test_unconfigured_public_binding_stops_before_creating_run(self):
        self.config.pop("denylist_sources")
        self.put("skill/references/local-sources.json", {"denylist_sources": []})
        with patch("screening_run.__file__", str(self.root / "skill/scripts/screening_run.py")):
            with self.assertRaisesRegex(ValueError, "confirmed haitou source required"):
                self.start()
        self.assertFalse(self.run.exists())

    def test_private_binding_used_when_run_config_omits_sources(self):
        sources = self.config.pop("denylist_sources")
        self.put("skill/references/local-sources.json", {"denylist_sources": []})
        self.put("skill/references/local-sources.local.json", {"denylist_sources": sources})
        with patch("screening_run.__file__", str(self.root / "skill/scripts/screening_run.py")):
            result = self.start()
        self.assertEqual(result["status"], "needs_review")
        manifest = load_json(self.run / "manifest.json")
        self.assertEqual(manifest["config"]["denylist_sources"], sources)

    def test_explicit_run_sources_take_priority_over_private_binding(self):
        self.put("skill/references/local-sources.local.json", {"denylist_sources": []})
        with patch("screening_run.__file__", str(self.root / "skill/scripts/screening_run.py")):
            result = self.start()
        self.assertEqual(result["status"], "needs_review")

    def review(self, key="candidate-1", decision="accept"):
        manifest = load_json(self.run / "manifest.json")
        candidate = next(c for c in manifest["candidates"] if c["candidate_id"] == key)
        now = datetime.now(timezone.utc).isoformat()
        receipt = {"run_id": manifest["run_id"], "candidate_id": key, "model": "gpt-6-luna",
                   "agent_id": "SYNTHETIC_TEST", "spawn_call_id": "SYNTHETIC_TEST_spawn",
                   "result_call_id": "SYNTHETIC_TEST_result", "started_at": now,
                   "browser_calls": ["SYNTHETIC_TEST_root", "SYNTHETIC_TEST_jd"]}
        self.put(f"run/receipts/{key}.json", receipt)
        detail = "https://official.example.invalid/job/" + candidate["job_id"]
        apply = "https://official.example.invalid/apply/" + candidate["job_id"]
        jd = f"SYNTHETIC_TEST {candidate['company_name']} 算法岗位 {candidate['job_id']} 2027 硕士 计算机 上海 Python 正在招聘 申请职位 {apply}"
        self.put(f"run/evidence/{key}-root.json", {"raw_browser_output": "SYNTHETIC_TEST " + detail})
        self.put(f"run/evidence/{key}-jd.json", {"raw_browser_output": jd})
        from screening_run import CHECKS
        review = {"schema": "recruitment-jd-review-v2", "run_id": manifest["run_id"],
                  "candidate_id": key, "decision": decision, "reason": "SYNTHETIC_TEST",
                  "reviewed_at": now, "invocation_file": f"receipts/{key}.json", "eligibility": "pass",
                  "jd_source_url": detail, "identity": {"company_name": candidate["company_name"],
                    "job_name": candidate["job_name"], "official_job_id": candidate["job_id"]},
                  "pages": [{"url": self.config["official_roots"][0]["url"], "tool_call_id": "SYNTHETIC_TEST_root",
                             "observed_at": now, "snapshot_file": f"evidence/{key}-root.json"},
                            {"url": detail, "tool_call_id": "SYNTHETIC_TEST_jd",
                             "observed_at": now, "snapshot_file": f"evidence/{key}-jd.json"}],
                  "opening": {"status": "open", "quote": "正在招聘", "apply_label": "申请职位",
                              "apply_url": apply, "apply_enabled": True, "deadline_kind": "not_stated"},
                  "checks": [{"field": field, "result": "pass", "jd_quote": "2027",
                              "profile_quote": "2027", "criteria_quote": "2027", "reason": "SYNTHETIC_TEST"}
                             for field in sorted(CHECKS)],
                  "all_hard_requirements_reviewed_reason": "SYNTHETIC_TEST"}
        return review

    def ingest(self, review):
        return advance(self.run, [self.put("review.json", review)])

    def test_valid_stops_at_target(self):
        self.start()
        result = self.ingest(self.review())
        self.assertEqual((result["status"], result["accepted_count"], result["next_for_review"]), ("complete", 1, []))

    def test_reject_preserves_other_job_at_same_company(self):
        self.start()
        result = self.ingest(self.review(decision="reject"))
        self.assertEqual(result["next_for_review"][0]["candidate_id"], "candidate-2")
        result = self.ingest(self.review("candidate-2"))
        self.assertEqual(result["status"], "complete")

    def test_gates_reject_incomplete_evidence(self):
        self.start()
        base = self.review()
        manifest = load_json(self.run / "manifest.json")
        candidate = manifest["candidates"][0]
        cases = []
        value = copy.deepcopy(base); value["schema"] = "v1"; cases.append(value)
        value = copy.deepcopy(base); value["eligibility"] = "unknown"; cases.append(value)
        value = copy.deepcopy(base); value["checks"][0]["result"] = "unknown"; cases.append(value)
        value = copy.deepcopy(base); value["checks"][0]["profile_quote"] = "invented"; cases.append(value)
        value = copy.deepcopy(base); value["pages"][0]["url"] = "https://aggregator.example.invalid"; cases.append(value)
        value = copy.deepcopy(base); value["pages"][-1]["tool_call_id"] = "invented"; cases.append(value)
        value = copy.deepcopy(base); value["pages"][-1]["snapshot_file"] = "missing.txt"; cases.append(value)
        value = copy.deepcopy(base); value["opening"]["status"] = "closed"; cases.append(value)
        value = copy.deepcopy(base); value["identity"]["job_name"] = "other"; cases.append(value)
        value = copy.deepcopy(base); value["pages"][-1]["url"] += "missing"; cases.append(value)
        for value in cases:
            with self.subTest(case=value), self.assertRaises((ValueError, OSError)):
                validate_review(manifest, candidate, value, self.run, datetime.now(timezone.utc))

    def test_boolean_only_cannot_count_and_refills(self):
        self.start()
        result = self.ingest({"candidate_id": "candidate-1", "decision": "accept", "jd_verified": True})
        self.assertEqual(result["accepted_count"], 0)
        self.assertEqual(result["next_for_review"][0]["candidate_id"], "candidate-2")

    def test_current_denylist_invalidates_previous_acceptance(self):
        self.start()
        self.ingest(self.review())
        self.put("haitou.json", {"applications": [{"company": "模拟甲有限公司"}]})
        result = advance(self.run, [])
        self.assertEqual((result["accepted_count"], result["status"]), (0, "source_exhausted"))

    def test_candidate_alias_excluded(self):
        self.jobs[0]["aliases"] = ["海投中的旧名称"]
        self.put("candidates.json", {"jobs": self.jobs})
        self.put("haitou.json", {"applications": [{"company": "海投中的旧名称"}]})
        self.start()
        manifest = load_json(self.run / "manifest.json")
        self.assertEqual(len(manifest["candidates"]), 2)

    def test_registered_alias_excluded(self):
        self.config["company_aliases"] = [{"company_name": "模拟甲", "aliases": ["海投中的旧名称"]}]
        self.put("haitou.json", {"applications": [{"company": "海投中的旧名称"}]})
        self.assertEqual(self.start()["status"], "source_exhausted")

    def test_denylist_missing_fields_fails_closed(self):
        self.put("haitou.json", {"applications": [{"wrong": "模拟甲"}]})
        with self.assertRaises(KeyError):
            self.start()

    def test_unknown_review_not_injected(self):
        self.start()
        with self.assertRaises(ValueError):
            self.ingest(self.review("candidate-2"))

    def test_duplicate_review_cannot_overwrite(self):
        self.start()
        review = self.review(decision="hold")
        self.ingest(review)
        with self.assertRaises(ValueError):
            self.ingest(review)

    def test_resume_does_not_skip_pending(self):
        self.start()
        result = advance(self.run, [])
        self.assertEqual(result["next_for_review"][0]["candidate_id"], "candidate-1")
        self.assertEqual(len(list(self.run.glob("round-*.json"))), 2)

    def test_changed_profile_requires_new_run(self):
        self.start()
        self.profile.write_text("changed", encoding="utf-8")
        with self.assertRaises(ValueError):
            advance(self.run, [])

    def test_stale_pages_cannot_pass(self):
        self.start()
        review = self.review()
        manifest = load_json(self.run / "manifest.json")
        with self.assertRaises(ValueError):
            validate_review(manifest, manifest["candidates"][0], review, self.run,
                            datetime.now(timezone.utc) + timedelta(hours=25))

    def test_company_id_counts_once(self):
        self.config["target"] = 2
        self.jobs[0]["company_id"] = "shared"
        self.jobs[1]["company_id"] = "shared"
        self.jobs[1]["company_name"] = "模拟甲另一名称"
        self.put("candidates.json", {"jobs": self.jobs[:2]})
        self.start()
        result = self.ingest(self.review())
        self.assertEqual((result["accepted_count"], result["status"]), (1, "source_exhausted"))

    def test_langgraph_source_all_states_exclude_without_mutation(self):
        ledger = {"schema": 1, "revision": 1,
                  "applied": {"a": {"company": "模拟已投"}},
                  "in_progress": {"b": {"status": "blocked", "job": {"company": "模拟处理中"}}},
                  "queue": [{"company": "模拟排队"}],
                  "events": [{"details": {"company": "模拟历史"}}],
                  "runs": {"run": {"config": {"excluded_companies": ["模拟禁投"]}}}}
        source = self.put("ledger.json", ledger)
        before = source.read_text(encoding="utf-8")
        config = {"denylist_sources": [{"role": "haitou_company_exclusion", "adapter": "langgraph_ledger_v1",
                   "path": str(source), "confirmed_via": "SYNTHETIC_TEST"}]}
        blocked, metadata = denylist(config)
        self.assertEqual(len(blocked), 5)
        self.assertIn("name:模拟处理中", blocked)
        self.assertIn("name:模拟历史", blocked)
        self.assertEqual(metadata[0]["revision"], 1)
        self.assertEqual(source.read_text(encoding="utf-8"), before)

    def test_langgraph_missing_collection_fails_closed(self):
        with self.assertRaises(ValueError):
            langgraph_companies({"schema": 1, "applied": {}})


if __name__ == "__main__":
    unittest.main()
