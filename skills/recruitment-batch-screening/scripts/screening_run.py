"""Resumable screening with evidence gates. Python stdlib; no network or submission.

Browser observations and Luna tool receipts must be captured by the coordinator.
This validates consistency, not authenticity of arbitrarily edited local files.
"""
from __future__ import annotations

import argparse
from datetime import datetime, timezone, timedelta
import json
from pathlib import Path
import sys
from urllib.parse import urlsplit
from uuid import uuid4

from screen_candidates import (load_json, walk_dicts, looks_like_candidate,
                               record_for_review, normalize_company)

CHECKS = {"location", "graduation_year", "degree", "major", "language",
          "experience", "other_hard_gates", "role_fit"}


def require(condition, message):
    if not condition:
        raise ValueError(message)


def nonempty(value):
    return isinstance(value, str) and bool(value.strip())


def timestamp(value):
    require(nonempty(value), "missing timestamp")
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    require(parsed.tzinfo is not None, "timestamp must include timezone")
    return parsed.astimezone(timezone.utc)


def url(value):
    require(nonempty(value), "missing URL")
    parsed = urlsplit(value)
    require(parsed.scheme in {"https", "http"} and parsed.hostname and not parsed.username,
            "invalid HTTP URL")
    return value


def pointer(data, value):
    require(isinstance(value, str) and (value == "" or value.startswith("/")),
            "use explicit JSON pointer, including empty string for root")
    for part in value.split("/")[1:]:
        key = part.replace("~1", "/").replace("~0", "~")
        data = data[int(key)] if isinstance(data, list) else data[key]
    return data


def path(value):
    result = Path(value)
    require(result.is_absolute(), "input paths must be absolute")
    return result.resolve(strict=True)


def write_new(destination, data):
    with destination.open("x", encoding="utf-8") as stream:
        json.dump(data, stream, ensure_ascii=False, indent=2)
        stream.write("\n")


def company_tokens(record):
    tokens = {"name:" + normalize_company(record["company_name"])}
    if record.get("company_id"):
        tokens.add("id:" + str(record["company_id"]))
    for name in record.get("aliases", []):
        require(nonempty(name), "invalid company alias")
        tokens.add("name:" + normalize_company(name))
    tokens.discard("name:")
    return tokens


def resolve_company(record, config):
    matches = [r for r in config.get("company_aliases", [])
               if company_tokens(r) & company_tokens(record)]
    require(len(matches) <= 1, "conflicting company alias groups")
    if matches:
        match = matches[0]
        record = {**record, "aliases": list(set(record.get("aliases", []) +
                  match.get("aliases", []) + [record["company_name"], match["company_name"]]))}
        if match.get("company_id"):
            record["company_id"] = match["company_id"]
        record["company_key"] = normalize_company(match["company_name"])
    return record


def denylist(config):
    """Read only configured collections/fields; never recursively scan arbitrary names."""
    blocked, sources = set(), []
    require(config.get("denylist_sources"), "confirmed haitou source required")
    cached = {}
    for spec in config["denylist_sources"]:
        require(spec.get("role") == "haitou_company_exclusion" and nonempty(spec.get("confirmed_via")),
                "source purpose/confirmation missing")
        source = path(spec["path"])
        require(not any(s in source.name.casefold() for s in ("company-universe", "iguopin-candidates", "batch-01")),
                "candidate universe/batch is not a haitou source")
        if source not in cached:
            cached[source] = load_json(source)
        root = cached[source]
        if spec.get("adapter") == "langgraph_ledger_v1":
            rows = langgraph_companies(root)
            for row in rows:
                blocked.update(company_tokens(resolve_company(row, config)))
            sources.append({"path": str(source), "records": len(rows),
                            "unique_company_names": len({normalize_company(r["company_name"]) for r in rows}),
                            "revision": root.get("revision"), "modified_at": source.stat().st_mtime,
                            "adapter": "langgraph_ledger_v1"})
            continue
        rows = pointer(root, spec["records_pointer"])
        require(isinstance(rows, list), "denylist pointer must select an array")
        for row in rows:
            require(isinstance(row, dict), "denylist record must be object")
            name = pointer(row, spec["company_name_pointer"])
            require(nonempty(name), "missing company name: refusing incomplete denylist")
            normalized = {"company_name": name}
            if spec.get("company_id_pointer"):
                normalized["company_id"] = pointer(row, spec["company_id_pointer"])
            if spec.get("aliases_pointer"):
                normalized["aliases"] = pointer(row, spec["aliases_pointer"])
                require(isinstance(normalized["aliases"], list), "aliases must be array")
            blocked.update(company_tokens(resolve_company(normalized, config)))
        sources.append({"path": str(source), "records": len(rows),
                        "modified_at": source.stat().st_mtime})
    return blocked, sources


def langgraph_companies(root):
    """Read the known Store v1 shape without importing/starting its writer."""
    require(isinstance(root, dict) and root.get("schema") == 1, "unsupported LangGraph ledger schema")
    require(isinstance(root.get("applied"), dict) and isinstance(root.get("in_progress"), dict) and
            isinstance(root.get("queue"), list) and isinstance(root.get("events"), list) and
            isinstance(root.get("runs"), dict), "incomplete LangGraph ledger")
    rows = []

    def add(name, location):
        require(nonempty(name), "missing LangGraph company at " + location)
        # Platform IDs are not shared registry IDs; match names/explicit aliases only.
        rows.append({"company_name": name, "source_location": location})

    for key, item in root["applied"].items():
        add(item["company"], "applied/" + key)
    for key, item in root["in_progress"].items():
        add(item["job"]["company"], "in_progress/" + key)
    for i, item in enumerate(root["queue"]):
        add(item["company"], "queue/" + str(i))
    for i, item in enumerate(root["events"]):
        details = item.get("details", {})
        require(isinstance(details, dict), "invalid event details")
        if "company" in details:
            add(details["company"], "events/" + str(i))
    for key, item in root["runs"].items():
        for name in item.get("config", {}).get("excluded_companies", []):
            add(name, "runs/" + key + "/config/excluded_companies")
    return rows


def init_run(config_path, run_dir):
    config = load_json(config_path)
    require(config.get("schema") == "recruitment-screening-config-v2", "v2 config required")
    if "denylist_sources" not in config:
        references = Path(__file__).resolve().parents[1] / "references"
        binding = references / "local-sources.local.json"
        if not binding.exists():
            binding = references / "local-sources.json"
        config["denylist_sources"] = load_json(binding)["denylist_sources"]
    require(type(config.get("target")) is int and config["target"] > 0, "positive target required")
    require(config.get("target_unit") in {"jobs", "companies"}, "explicit target_unit required")
    # Capture the profile and screening preferences verbatim, so changes require a new run.
    profile = path(config["profile_path"]).read_text(encoding="utf-8-sig")
    criteria = path(config["criteria_path"]).read_text(encoding="utf-8-sig")
    require(profile.strip() and criteria.strip(), "profile and criteria cannot be empty")
    require(config.get("candidate_files"), "candidate files required")
    blocked, sources = denylist(config)
    records, seen, exclusions = [], set(), []
    for source_name in config["candidate_files"]:
        source = path(source_name)
        for row, row_path in walk_dicts(load_json(source)):
            if not looks_like_candidate(row):
                continue
            candidate = resolve_company(record_for_review(row, source, row_path, len(records)), config)
            # Scope provider-local IDs; URL paths/query strings are case-sensitive.
            address = candidate["detail_url"] or candidate["apply_url"]
            require(address, "candidate missing detail/apply URL")
            host = urlsplit(url(address)).netloc.casefold()
            key = (host, candidate["company_key"], candidate["job_id"]) if candidate["job_id"] else (address,)
            reason = "duplicate_job" if key in seen else None
            seen.add(key)
            if company_tokens(candidate) & blocked:
                reason = "company_in_haitou_json"
            if reason:
                exclusions.append({"company_name": candidate["company_name"], "url": address, "reason": reason})
                continue
            candidate["candidate_id"] = "candidate-" + str(len(records) + 1)
            records.append(candidate)
    require(not run_dir.exists(), "run directory already exists; use advance")
    run_dir.mkdir(parents=True)
    manifest = {"schema": "recruitment-screening-run-v2", "run_id": str(uuid4()),
                "started_at": datetime.now(timezone.utc).isoformat(), "config": config,
                "profile_text": profile, "criteria_text": criteria, "candidates": records,
                "initial_exclusions": exclusions, "denylist_sources": sources}
    write_new(run_dir / "manifest.json", manifest)
    return advance(run_dir, [])


def read_evidence(run_dir, reference):
    require(nonempty(reference), "missing evidence file")
    result = (run_dir / reference).resolve(strict=True)
    require(result.is_relative_to(run_dir.resolve()), "evidence must be inside run directory")
    return result.read_text(encoding="utf-8-sig")


def validate_review(manifest, candidate, review, run_dir, now):
    require(review.get("schema") == "recruitment-jd-review-v2", "legacy boolean review refused")
    require(review.get("run_id") == manifest["run_id"], "wrong run")
    require(review.get("decision") in {"accept", "reject", "hold"} and nonempty(review.get("reason")),
            "decision and reason required")
    invocation = json.loads(read_evidence(run_dir, review.get("invocation_file")))
    require(invocation.get("run_id") == manifest["run_id"] and
            invocation.get("candidate_id") == candidate["candidate_id"], "invocation identity mismatch")
    require(invocation.get("model") == "gpt-6-luna" and nonempty(invocation.get("agent_id")),
            "actual Luna invocation required")
    require(nonempty(invocation.get("spawn_call_id")) and nonempty(invocation.get("result_call_id")),
            "missing parent tool invocation receipts")
    started = timestamp(manifest["started_at"])
    invoked = timestamp(invocation.get("started_at"))
    reviewed = timestamp(review.get("reviewed_at"))
    require(started <= invoked <= reviewed <= now + timedelta(minutes=5), "invalid invocation/review time")
    calls = invocation.get("browser_calls", [])
    require(isinstance(calls, list) and calls, "browser tool call IDs required even for failed access")
    require(all(nonempty(c) for c in calls), "invalid browser call ID")
    # A hold/reject can record a failed page read. It is never counted as a match.
    if review["decision"] != "accept":
        return
    require(review.get("eligibility") == "pass", "accept requires eligibility pass")
    pages = review.get("pages")
    require(isinstance(pages, list) and pages, "official navigation chain required")
    require((now - reviewed) <= timedelta(hours=24), "review older than 24h")
    roots = manifest["config"].get("official_roots", [])
    if manifest["config"].get("official_roots_path"):
        roots = load_json(path(manifest["config"]["official_roots_path"]))
    require(isinstance(roots, list), "official roots must be array")
    trusted = [r for r in roots if r.get("company_key") == candidate["company_key"] and
               r.get("url") == pages[0].get("url") and nonempty(r.get("verified_via"))]
    require(trusted, "official company root not registered with verification source")
    texts = []
    for i, page in enumerate(pages):
        url(page["url"])
        require(page.get("tool_call_id") in calls, "page not tied to actual browser read receipt")
        observed = timestamp(page.get("observed_at"))
        require(invoked <= observed <= reviewed and now - observed <= timedelta(hours=24), "stale/out-of-run page")
        text = read_evidence(run_dir, page.get("snapshot_file"))
        require(nonempty(text), "empty browser snapshot")
        texts.append(text)
        if i:
            # Every redirect/link must be visible in the preceding raw browser capture.
            require(page["url"] in texts[i - 1], "unproven official navigation link")
    last, jd = pages[-1], texts[-1]
    identity = review.get("identity", {})
    require(identity.get("company_name") == candidate["company_name"] and
            identity.get("job_name") == candidate["job_name"], "candidate/JD identity mismatch")
    for field in ("company_name", "job_name", "official_job_id"):
        require(nonempty(identity.get(field)) and identity[field] in jd, "identity absent from official snapshot: " + field)
    require(review.get("jd_source_url") == last["url"], "JD URL mismatch")
    opening = review.get("opening", {})
    require(opening.get("status") == "open" and nonempty(opening.get("quote")) and opening["quote"] in jd,
            "missing current open-status evidence")
    require(nonempty(opening.get("apply_label")) and opening["apply_label"] in jd and
            url(opening.get("apply_url")) in jd and opening.get("apply_enabled") is True,
            "missing active application entry evidence")
    require(opening.get("deadline_kind") in {"dated", "rolling", "not_stated"}, "deadline classification required")
    if opening["deadline_kind"] == "dated":
        require(timestamp(opening.get("deadline_at")) >= now, "deadline expired")
        require(nonempty(opening.get("deadline_quote")) and opening["deadline_quote"] in jd, "deadline evidence missing")
    checks = review.get("checks", [])
    require(isinstance(checks, list), "checks must be array")
    fields = [c.get("field") for c in checks]
    require(CHECKS <= set(fields) and len(fields) == len(set(fields)), "missing/duplicate qualification checks")
    for check in checks:
        require(check.get("result") in {"pass", "not_required"}, "failed/unknown condition cannot pass")
        require(nonempty(check.get("reason")), "qualification reasoning required")
        if check["result"] == "not_required":
            require(check.get("field") not in {"location", "graduation_year", "degree", "major", "role_fit"},
                    "core eligibility/fit cannot be waived")
            continue
        require(nonempty(check.get("jd_quote")) and check["jd_quote"] in jd, "JD quote not in snapshot")
        require(nonempty(check.get("profile_quote")) and check["profile_quote"] in manifest["profile_text"],
                "profile quote not in configured profile")
        require(nonempty(check.get("criteria_quote")) and check["criteria_quote"] in manifest["criteria_text"],
                "screening preference quote missing")
    require(nonempty(review.get("all_hard_requirements_reviewed_reason")), "additional hard-gate review required")


def advance(run_dir, review_paths):
    manifest = load_json(run_dir / "manifest.json")
    require(manifest.get("schema") == "recruitment-screening-run-v2", "v2 run required")
    config = manifest["config"]
    require(path(config["profile_path"]).read_text(encoding="utf-8-sig") == manifest["profile_text"] and
            path(config["criteria_path"]).read_text(encoding="utf-8-sig") == manifest["criteria_text"],
            "profile/criteria changed; start a new run")
    blocked, sources = denylist(config)  # Reload on EVERY advancement, including finalization.
    rounds = sorted(run_dir.glob("round-*.json"))
    history = load_json(rounds[-1])["reviews"] if rounds else {}
    candidates = manifest["candidates"]
    known = {c["candidate_id"] for c in candidates}
    permitted = {r["candidate_id"] for r in load_json(rounds[-1])["next_for_review"]} if rounds else set()
    for review_path in review_paths:
        review = load_json(review_path)
        key = review.get("candidate_id")
        require(key in known and key in permitted, "review not requested by preceding round")
        require(key not in history, "duplicate review; start new run to retry held candidates")
        history[key] = review
    now = datetime.now(timezone.utc)
    accepted, dispositions, pending = [], [], []
    companies, official_jobs, official_urls = set(), set(), set()
    for candidate in candidates:
        key = candidate["candidate_id"]
        if company_tokens(candidate) & blocked:
            dispositions.append({"candidate_id": key, "reason": "company_in_latest_haitou_json"})
            continue
        review = history.get(key)
        if review is None:
            pending.append(candidate)
            continue
        try:
            validate_review(manifest, candidate, review, run_dir, now)
            if review["decision"] != "accept":
                dispositions.append({"candidate_id": key, "reason": review["decision"], "detail": review["reason"]})
                continue
            company = candidate["company_id"] or candidate["company_key"]
            identity = (company, review["identity"]["official_job_id"])
            require(identity not in official_jobs and review["jd_source_url"] not in official_urls,
                    "duplicate official job")
            if config["target_unit"] == "companies":
                require(company not in companies, "company already accepted")
            if len(accepted) >= config["target"]:
                continue
            companies.add(company)
            official_jobs.add(identity)
            official_urls.add(review["jd_source_url"])
            accepted.append({**candidate, "state": "岗位已核实", "review": review})
        except (ValueError, KeyError, TypeError, OSError) as exc:
            dispositions.append({"candidate_id": key, "reason": "evidence_validation_failed", "detail": str(exc)})
    # One review at a time: real browser work stops exactly when target is met.
    pending = [c for c in pending if not (config["target_unit"] == "companies" and
                                        (c["company_id"] or c["company_key"]) in companies)]
    remaining = max(0, config["target"] - len(accepted))
    status = "complete" if not remaining else ("needs_review" if pending else "source_exhausted")
    report = {"schema": "recruitment-screening-round-v2", "run_id": manifest["run_id"],
              "checked_at": now.isoformat(), "status": status, "target": config["target"],
              "accepted_count": len(accepted), "remaining": remaining, "accepted": accepted,
              "next_for_review": pending[:1] if remaining else [], "reviews": history,
              "dispositions": dispositions, "denylist_sources": sources}
    write_new(run_dir / f"round-{len(rounds) + 1:06d}.json", report)
    return {k: report[k] for k in ("status", "accepted_count", "remaining", "next_for_review")}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    init = sub.add_parser("init")
    init.add_argument("--config", type=Path, required=True)
    init.add_argument("--run-dir", type=Path, required=True)
    step = sub.add_parser("advance")
    step.add_argument("--run-dir", type=Path, required=True)
    step.add_argument("--reviews", nargs="*", type=Path, default=[])
    args = parser.parse_args(argv)
    try:
        result = init_run(args.config, args.run_dir) if args.command == "init" else advance(args.run_dir, args.reviews)
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return 0
    except (ValueError, KeyError, TypeError, OSError) as exc:
        print("screening blocked: " + str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
