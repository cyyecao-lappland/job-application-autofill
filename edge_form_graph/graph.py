from __future__ import annotations

import copy
import time
import uuid
from typing import TypedDict

from langgraph.graph import StateGraph, START, END
from langgraph.types import interrupt

from .contracts import (ContractError, closed, compile_plan, page_matches, redacted_profile,
                        validate_receipt, validate_snapshot)
from .parallel import (cached_mapping, deterministic_record_mappings,
                       focused_profile, protected_present)
from .enum_repair import (MAX_ROUNDS, candidates_for, context_decisions, review_requests,
                         validate_review, review_plan, carry_reviewed_enums)
from .identity import synthesize
from .semantic import compile_semantic, validate_record_sources
from .execution_policy import tuning_enabled, fallback_operations, execution_review, FIRST_OPTION
from .linkage import update_linkage, retained_writes, retain_plan_writes
from .verification import PROCESSED, attach_skips


class State(TypedDict, total=False):
    profile: dict
    snapshot: dict
    current: dict
    authorization: dict
    started_at: float
    deadline: float
    status: str
    proposal: dict
    operations: list
    deferred: list
    results: dict
    command: dict | None
    revision: int
    reviewed_revision: int | None
    review: dict
    trace: list
    metrics: dict
    mapping_cache: dict
    defer_review: bool
    last_receipt: dict
    enum_pending: list
    enum_history: list
    enum_rounds: int
    semantic_attempted: bool
    semantic_report: list
    learning_errors: list
    agent_tuning: bool
    fallback_attempted: list
    manual_review: list
    rediscovery_rounds: int
    linkage: dict
    retained_operations: list


SUCCESS = PROCESSED  # Scheduling only; skipped readbacks remain separately reported.


def initial_state(profile, snapshot, *, allow_save=False, budget_seconds=1800,
                  snapshot_already_validated=False, agent_tuning=True):
    if not snapshot_already_validated:
        validate_snapshot(snapshot)
    return State(profile=redacted_profile(profile), snapshot=copy.deepcopy(snapshot), current=copy.deepcopy(snapshot),
        authorization={"fill": True, "save": allow_save, "submit": False}, started_at=time.time(),
        deadline=time.time()+budget_seconds, status="new", results={}, command=None, revision=0,
        reviewed_revision=None, trace=[], semantic_attempted=False, semantic_report=[], learning_errors=[],
        enum_pending=[], enum_history=[], enum_rounds=0, last_receipt={},
        agent_tuning=tuning_enabled(agent_tuning), fallback_attempted=[], manual_review=[], rediscovery_rounds=0,
        linkage={'depths': {f['id']: 1 for f in snapshot['fields']}, 'seen': {}, 'blocked': {}}, retained_operations=[],
        metrics={"batch_count": 0, "model_calls": 0, "written": 0, "already_matched": 0})


def stamp(state, node, **updates):
    return {"trace": state["trace"] + [{"node": node, "at": time.time()}], **updates}


def _record_boundary_changed(snapshot):
    boundary=snapshot.get('record_boundary') or {}
    containers={field.get('record_container') for field in snapshot.get('fields', [])
                if field.get('record_container')}
    return boundary.get('scope_record_count', 1)>1 or len(containers)>1


def build_graph(model, checkpointer, *, knowledge=None, semantic_model=None):
    semantic_model = semantic_model or model
    def compile_semantic_plan(s, current, proposal):
        operations, deferred = compile_plan(s['profile'], current, proposal)
        operations, deferred = synthesize(current, operations, deferred)
        operations = carry_reviewed_enums(s['profile'], operations, s['operations'])
        return retain_plan_writes(s['profile'], operations, deferred, current,
                                  s.get('retained_operations', []), s['results'])

    def map_fields(s):
        started = time.monotonic()
        profile = redacted_profile(s['profile'])
        if _record_boundary_changed(s['snapshot']):
            return stamp(s, 'map', profile=profile, proposal={'mappings': [], 'deferred': [
                {'field_id': f['id'], 'reason': 'record_boundary_changed'} for f in s['snapshot']['fields']]},
                results={f['id']: {'status': 'deferred', 'reason': 'record_boundary_changed'}
                         for f in s['snapshot']['fields']}, status='record_boundary_changed')
        proposal = cached_mapping(s.get('mapping_cache'), profile, s['snapshot'])
        cache_hit = proposal is not None
        if not cache_hit and knowledge is not None:
            proposal = knowledge.known_plan(profile, s['snapshot'])
        elif not cache_hit and not s.get('agent_tuning', True):
            proposal = {'mappings': [], 'deferred': [{'field_id': f['id'], 'reason': 'mapping_not_found'}
                                                   for f in s['snapshot']['fields']]}
        elif not cache_hit:
            proposal = model.map(focused_profile(profile, s["snapshot"]), s["snapshot"])
        from .confirmed_field_policies import apply_field_policies
        proposal = apply_field_policies(profile, s['snapshot'], proposal)
        verified={f['id'] for f in s['snapshot']['fields'] if f.get('verified_control')}
        if verified:
            proposal={'mappings':[m for m in proposal['mappings'] if m['field_id'] not in verified],
                      'deferred':[d for d in proposal['deferred'] if d['field_id'] not in verified]+
                      [{'field_id':fid,'reason':'verified_control_pending_review'} for fid in verified]}
        return stamp(s, "map", profile=profile, proposal=proposal, status="mapped",
                     metrics={**s["metrics"], "model_calls": s["metrics"]["model_calls"]+int(not cache_hit and knowledge is None and s.get('agent_tuning', True)),
                              "field_table_hits": len(proposal['mappings']) if knowledge is not None and not cache_hit else 0,
                              "mapping_cache_hit": cache_hit,
                              "mapping_seconds": round(time.monotonic()-started, 3)})

    def validate(s):
        try:
            operations, deferred = compile_plan(s["profile"], s["snapshot"], s["proposal"])
            operations, deferred = synthesize(s['snapshot'], operations, deferred)
            retained = {op['id']: op for op in s.get('retained_operations', [])}
            mapped = {op['id']: op for op in operations}
            # Keep a successful fallback only while the field still has that
            # observed value. A newly resolved canonical mapping takes priority.
            operations += [op for fid, op in retained.items() if fid not in mapped]
            retained_ids = {op['id'] for op in operations if op['id'] in retained
                            and op['value'] == retained[op['id']]['value']}
            deferred = [d for d in deferred if d['field_id'] not in retained_ids]
            cache = s.get('mapping_cache') or {}
            if s['metrics'].get('mapping_cache_hit'):
                operations = carry_reviewed_enums(s['profile'], operations, cache.get('enum_operations', []))
            results = {d["field_id"]: {"status": "deferred", "reason": d["reason"]} for d in deferred}
            results.update({fid: {'status': 'verification_skipped' if retained[fid].get('verification') == 'skipped' else 'already_matched', 'reason': 'retained_after_linkage'} for fid in retained_ids})
            for field in s['current']['fields']:
                if field.get('verified_control'):
                    results[field['id']]={'status':'prefilled_pending_review','reason':'typed_control_commit_retained'}
                if field.get('verification_skipped'):
                    results[field['id']] = {'status': 'verification_skipped', 'reason': 'readback_incomplete'}
            attach_skips(s['current'], operations, results)
            for fid, reason in s.get('linkage', {}).get('blocked', {}).items():
                if any(f['id'] == fid for f in s['current']['fields']):
                    results[fid] = {'status': 'deferred', 'reason': reason}
            return stamp(s, "validate", operations=operations, deferred=deferred, results=results, status="validated",
                         enum_history=cache.get('enum_history', []) if s['metrics'].get('mapping_cache_hit') else [],
                         enum_rounds=cache.get('enum_rounds', 0) if s['metrics'].get('mapping_cache_hit') else 0)
        except ContractError as exc:
            return stamp(s, "validate", status="plan_rejected", review={"issues": [str(exc)]})

    def prepare_fill(s):
        if s["command"] is not None or not s["authorization"]["fill"]:
            raise ContractError("pending_or_unauthorized")
        if time.time() >= s["deadline"]:
            return stamp(s, "prepare_fill", status="budget_exhausted")
        ready, done, results = [], {k for k, v in s["results"].items() if v["status"] in SUCCESS}, copy.deepcopy(s["results"])
        for op in s["operations"]:
            if op["id"] in results:
                continue
            if any(dep in results and results[dep]["status"] not in SUCCESS for dep in op["depends_on"]):
                results[op["id"]] = {"status": "deferred", "reason": "dependency_not_ready"}
                continue
            if set(op["depends_on"]) <= done:
                ready.append(op)
                done.add(op["id"])
        if not ready:
            return stamp(s, "prepare_fill", status="filled", results=results)
        command = {"version": 1, "command_id": str(uuid.uuid4()), "kind": "fill", "browser": "edge",
            "target": s["snapshot"]["target"], "module_id": s["snapshot"]["module_id"],
            "module_selector": s["snapshot"]["module_selector"], "snapshot_id": s["current"]["snapshot_id"],
            "revision": s["revision"], "operations": ready, "deadline": s["deadline"], "max_batch_ms": 8000,
            "agent_tuning": s.get('agent_tuning', True),
            # This is a lower bound on actual value mutations, not on controls
            # that still need a commit/readability check. Keep it identical to
            # the executor's admission rule so an enum-repaired value already
            # displayed in an expanded control does not invalidate the packet.
            "min_fill_count": min(10, sum(
                (op.get('secret_ref') is not None and op['field'].get('secret_match') is not True)
                or (op.get('secret_ref') is None and
                    (sorted(op['field'].get('value')) != sorted(op['value'])
                     if isinstance(op['field'].get('value'), list) and isinstance(op['value'], list)
                     else op['field'].get('value') != op['value']))
                for op in ready)),
            "capture": s["snapshot"].get("capture", {}), "completed_ids": sorted(done - {o["id"] for o in ready})}
        command['record_boundary']=copy.deepcopy(s['snapshot'].get('record_boundary'))
        return stamp(s, "prepare_fill", status="awaiting_edge", command=command, results=results)

    def execute_fill(s):
        # interrupt runs again after resume. Never perform browser effects in this node.
        receipt = interrupt({"role": "trusted_edge_host", "command": s["command"]})
        receipt = validate_receipt(s["command"], receipt)
        if any(r.get('reason')=='record_boundary_changed' for r in receipt['results']):
            return stamp(s, 'execute_fill', status='record_boundary_changed', last_receipt=receipt,
                         current=receipt.get('snapshot') or s['current'])
        if (not receipt["settled"] or receipt["status"] == "unknown"
                or any(r["status"] in {"unknown", "conflict"} for r in receipt["results"])):
            return stamp(s, "execute_fill", status="needs_reconciliation", last_receipt=receipt)
        results = copy.deepcopy(s["results"])
        for r in receipt["results"]:
            if r["status"] != "unattempted":
                results[r["id"]] = {"status": r["status"], "reason": r["reason"]}
        if results == s["results"]:
            return stamp(s, "execute_fill", status="no_progress")
        current = receipt["snapshot"]
        if current is None or current.get("module_id") != s["snapshot"]["module_id"]:
            raise ContractError("missing_current_module_snapshot")
        if {f['id'] for f in current['fields']} != {f['id'] for f in s['current']['fields']}:
            from .recovery import align_logical_fields
            try:
                current = align_logical_fields(s['current'], current)
            except ContractError:
                pass  # A structural change still goes through normal rediscovery checks.
        from .verification import retain_control_commits
        retain_control_commits(s['current'],current,results)
        for key in ('mapping_context', 'module_label', 'record_label', 'used_record_bindings'):
            if key in s['current']:
                current[key] = copy.deepcopy(s['current'][key])
        operations = copy.deepcopy(s['operations'])
        attach_skips(current, operations, results)
        fallback_values = receipt.get('evidence', {}).get('fallback_values', {})
        for op in operations:
            if (op.get('fallback') == 'first_option' and op['value'] == FIRST_OPTION
                    and results.get(op['id'], {}).get('status') in SUCCESS):
                if op['id'] not in fallback_values or not fallback_values[op['id']]:
                    raise ContractError('fallback_selection_evidence_missing')
                op['value'] = fallback_values[op['id']]
        completed = [op for op in operations if results.get(op["id"], {}).get("status") in SUCCESS]
        linkage_event = receipt.get('evidence', {}).get('linkage')
        if linkage_event:
            linkage = update_linkage(s.get('linkage'), s['current'], current, linkage_event)
            return stamp(s, 'execute_fill', status='inventory_changed', results=results, current=current,
                operations=operations, command=None, revision=s['revision']+1, reviewed_revision=None,
                last_receipt=receipt, linkage=linkage,
                metrics={**s['metrics'], 'batch_count': s['metrics']['batch_count']+1})
        if not page_matches(completed, current):
            return stamp(s, "execute_fill", status="readback_mismatch", results=results, current=current,
                         last_receipt=receipt)
        metrics = {**s["metrics"], "batch_count": s["metrics"]["batch_count"]+1,
                   "written": sum(v["status"] == "written" for v in results.values()),
                   "already_matched": sum(v["status"] == "already_matched" for v in results.values()),
                   "verification_skipped": sum(v["status"] == "verification_skipped" for v in results.values())}
        if completed and "ttff_seconds" not in metrics:
            metrics["ttff_seconds"] = time.time()-s["started_at"]
        fallback_ids = {op['id'] for op in operations if op.get('fallback')}
        metrics.update(written=sum(v['status']=='written' and k not in fallback_ids for k,v in results.items()),
                       already_matched=sum(v['status']=='already_matched' and k not in fallback_ids for k,v in results.items()),
                       fallback_written=sum(results.get(fid,{}).get('status') in SUCCESS for fid in fallback_ids))
        changed = (not s.get('agent_tuning', True) and
                   {f['id'] for f in current['fields']} != {f['id'] for f in s['snapshot']['fields']})
        return stamp(s, "execute_fill", status="inventory_changed" if changed else "batch_complete", results=results, current=current,
                     operations=operations,
                     command=None, revision=s["revision"]+1, reviewed_revision=None, metrics=metrics, last_receipt=receipt,
                     enum_pending=s.get('enum_pending', [])+[
                         {'candidate': candidate, 'command_id': receipt['command_id'],
                          'snapshot_id': current['snapshot_id'], 'revision': s['revision']+1}
                         for candidate in candidates_for(s['command'], receipt)])

    def resolve_unknown(s):
        if time.time()-s['current'].get('observed_at',0)>300:
            return stamp(s, 'resolve_unknown', status='rediscovery_required', command=None)
        if {f['id'] for f in s['current']['fields']} != {f['id'] for f in s['snapshot']['fields']}:
            from .recovery import align_logical_fields
            try:
                aligned = align_logical_fields(s['snapshot'], s['current'])
            except ContractError:
                return stamp(s, 'resolve_unknown', status='rediscovery_required', command=None)
            return stamp(s, 'resolve_unknown', status='field_identity_aligned', current=aligned, command=None)
        if (knowledge is None and s.get('agent_tuning', True)) or s.get('semantic_attempted'):
            return stamp(s, 'resolve_unknown', status='semantic_complete')
        # Legacy cached plans contain free-text deferral reasons. Reconsider
        # these once with current transforms/adapters, not just new reason codes.
        fields_by_id = {f['id']: f for f in s['current']['fields']}
        from .confirmed_field_policies import preserved_field_reason
        candidates = {d['field_id'] for d in s['proposal']['deferred']
                      if not fields_by_id[d['field_id']].get('protected')
                      and not preserved_field_reason(s['profile'], s['current'], fields_by_id[d['field_id']])
                      and not fields_by_id[d['field_id']].get('verified_control')
                      and s['results'].get(d['field_id'], {}).get('status') not in SUCCESS
                      and d['field_id'] not in s.get('linkage', {}).get('blocked', {})}
        prefilled = {fid for fid in candidates if fields_by_id[fid].get('value_present') is True
                     and fields_by_id[fid].get('value') not in (None,'',[])}
        program_mappings = deterministic_record_mappings(s['profile'], s['current'], candidates)
        program_ids = {item['field_id'] for item in program_mappings}
        # Bound fields are compared and corrected by the normal executor. Only
        # genuinely unbound fields, whether blank or nonempty, need semantics.
        pending = candidates-program_ids
        results=copy.deepcopy(s['results'])
        for fid in prefilled-program_ids:
            results[fid]={'status':'prefilled_pending_review','reason':'current_value_present'}
        base_deferred = [d for d in s['proposal']['deferred'] if d['field_id'] not in program_ids]
        program_proposal = {'mappings': s['proposal']['mappings']+program_mappings,
                            'deferred': base_deferred}
        if not pending:
            operations, deferred = compile_semantic_plan(s, s['current'], program_proposal)
            for fid in program_ids:
                results.pop(fid, None)
            return stamp(s, 'resolve_unknown', status='semantic_resolved' if program_ids else 'semantic_complete',
                          semantic_attempted=True, proposal=program_proposal, operations=operations,
                          deferred=deferred, results=results,
                          metrics={**s['metrics'],
                                   'deterministic_record_hits': len(program_mappings)})
        if time.time() >= s['deadline']:
            return stamp(s, 'resolve_unknown', status='budget_exhausted')
        if not s.get('agent_tuning', True):
            operations, deferred = compile_semantic_plan(s, s['current'], program_proposal)
            for fid in program_ids:
                results.pop(fid, None)
            return stamp(s, 'resolve_unknown', status='semantic_resolved' if program_ids else 'semantic_complete',
                         semantic_attempted=True, proposal=program_proposal, operations=operations,
                         deferred=deferred, results=results,
                         metrics={**s['metrics'], 'deterministic_record_hits': len(program_mappings)})
        before = copy.deepcopy(s['current'])
        before['context_fields'] = copy.deepcopy(before['fields'])
        pending_fields=[f for f in before['fields'] if f['id'] in pending]
        started = time.monotonic()
        try:
            # One semantic call owns one page module. Field count alone is not
            # evidence that a module is too large, and splitting can weaken the
            # relationships between nearby fields. The model still receives the
            # full module context and the focused canonical profile.
            semantic_snapshot=copy.deepcopy(before)
            semantic_snapshot['fields']=pending_fields
            response=semantic_model.match_unknown(focused_profile(s['profile'],semantic_snapshot),semantic_snapshot)
            proposal,bound,notes=compile_semantic(s['profile'],semantic_snapshot,response)
            model_calls=1
            if time.time() >= s['deadline']:
                return stamp(s, 'resolve_unknown', status='budget_exhausted', semantic_attempted=True)
            # Existing non-empty page values are preserved for independent
            # review. Do not reinsert their original semantic deferrals when
            # merging the model result for the still-empty fields; that would
            # overwrite prefilled_pending_review and make an arbitrary restart
            # unable to save a valid current page.
            merged = {'mappings': program_proposal['mappings']+proposal['mappings'],
                      'deferred': ([d for d in s['proposal']['deferred'] if d['field_id'] not in candidates]
                                   + proposal['deferred'])}
            current = copy.deepcopy(s['current'])
            if bound.get('mapping_context'):
                current['mapping_context'] = bound['mapping_context']
            validate_record_sources(s['profile'], current, merged['mappings'])
            operations, deferred = compile_semantic_plan(s, current, merged)
            newly_mapped_ids = program_ids | {item['field_id'] for item in proposal['mappings']}
            results = {k: v for k, v in results.items() if k not in newly_mapped_ids}
            results.update({d['field_id']: ({'status': 'prefilled_pending_review',
                                              'reason': 'current_value_present'}
                                             if d['field_id'] in prefilled or fields_by_id[d['field_id']].get('verified_control') else
                                             {'status': 'deferred', 'reason': d['reason']})
                            for d in deferred})
            original = copy.deepcopy(s['snapshot'])
            if current.get('mapping_context'):
                original['mapping_context'] = current['mapping_context']
            return stamp(s, 'resolve_unknown', status='semantic_resolved' if newly_mapped_ids else 'semantic_complete',
                semantic_attempted=True, semantic_report=notes, proposal=merged, operations=operations,
                deferred=deferred, results=results, snapshot=original, current=current,
                metrics={**s['metrics'], 'model_calls': s['metrics']['model_calls']+model_calls,
                    'deterministic_record_hits': len(program_mappings),
                    'semantic_model': getattr(semantic_model, 'model', None), 'semantic_fields': len(pending),
                    'semantic_prompt_chars': getattr(semantic_model, 'last_timings', {}).get('prompt_chars'),
                    'semantic_input_data_chars': getattr(semantic_model, 'last_timings', {}).get('input_data_chars'),
                    'semantic_schema_chars': getattr(semantic_model, 'last_timings', {}).get('schema_chars'),
                    'semantic_seconds': round(time.monotonic()-started, 3)})
        except Exception as exc:
            # A rejected semantic batch must not discard independently validated
            # deterministic mappings. Revalidate only the original context and
            # program proposal; never retain a partially accepted model binding.
            program_proposal=copy.deepcopy(program_proposal)
            for item in program_proposal['deferred']:
                if item['field_id'] in pending and fields_by_id[item['field_id']].get('required') is False:
                    item['reason']='unsupported: semantic_resolution_failed; value left unchanged'
            operations, deferred = compile_semantic_plan(s, s['current'], program_proposal)
            for item in deferred:
                fid=item['field_id']
                if fid in pending and fid not in prefilled:
                    results[fid]={'status':'deferred','reason':item['reason']}
            for fid in program_ids:
                results.pop(fid, None)
            return stamp(s, 'resolve_unknown', status='semantic_resolved' if program_ids else 'semantic_complete', semantic_attempted=True,
                results=results, proposal=program_proposal, operations=operations, deferred=deferred,
                semantic_report=[{'classification': 'worker_error', 'reason': str(exc) if isinstance(exc, ContractError) else type(exc).__name__}],
                metrics={**s['metrics'], 'model_calls': s['metrics']['model_calls']+1,
                    'deterministic_record_hits': len(program_mappings),
                    'semantic_seconds': round(time.monotonic()-started, 3)})

    def review_enums(s):
        pending = s.get('enum_pending', [])
        tried = {r['field_id'] for h in s.get('enum_history', []) for r in h['requests']}
        pending = [item for item in pending if item['candidate']['field_id'] not in tried
                   and s['results'].get(item['candidate']['field_id'], {}).get('status') == 'deferred']
        if not pending or s.get('enum_rounds', 0) >= MAX_ROUNDS:
            return stamp(s, 'review_enums', enum_pending=[], status='enum_review_complete')
        if time.time() >= s['deadline']:
            return stamp(s, 'review_enums', status='budget_exhausted', enum_pending=[])
        operations, results = copy.deepcopy(s['operations']), copy.deepcopy(s['results'])
        requests = review_requests(s['profile'], operations, [item['candidate'] for item in pending])
        entry = {'requests': requests, 'evidence': copy.deepcopy(pending), 'revision': s['revision']}
        model_called = False
        cached = []
        started = time.monotonic()
        try:
            cached = knowledge.enum_decisions(s['profile'], s['snapshot'], operations, requests) if knowledge is not None else []
            known = {d['field_id'] for d in cached}
            deterministic = context_decisions([r for r in requests if r['field_id'] not in known])
            known |= {d['field_id'] for d in deterministic}
            missing = [r for r in requests if r['field_id'] not in known]
            decisions = []
            if missing and s.get('agent_tuning', True):
                model_called = True
                fresh = semantic_model.review_enums(copy.deepcopy(missing))
                validate_review(fresh, missing)
                decisions = fresh['decisions']
            verdict = {'decisions': cached+deterministic+decisions}
            accepted = validate_review(verdict, requests if s.get('agent_tuning', True) else
                                       [r for r in requests if r['field_id'] in known])
            entry['review'] = verdict
        except Exception as exc:
            accepted = {}
            entry['error'] = type(exc).__name__
        if time.time() >= s['deadline']:
            accepted = {}
            entry['error'] = 'budget_exhausted'
        for op in operations:
            if op['id'] in accepted:
                decision = accepted[op['id']]
                request = next(r for r in requests if r['field_id'] == op['id'])
                evidence = next(item for item in pending if item['candidate']['field_id'] == op['id'])
                op['value'] = decision['option']
                op['enum_provenance'] = {**request, **decision, 'command_id': evidence['command_id'],
                                         'snapshot_id': evidence['snapshot_id'], 'revision': evidence['revision']}
                results.pop(op['id'], None)
        # Only dependency deferrals can be retried; conflicts/unknown writes remain blocked.
        retry = set(accepted)
        for op in operations:
            if (results.get(op['id'], {}).get('reason') == 'dependency_not_ready'
                    and any(dep in retry for dep in op['depends_on'])):
                results.pop(op['id'])
                retry.add(op['id'])
        return stamp(s, 'review_enums', operations=operations, results=results, enum_pending=[],
                     status='enum_repaired' if accepted else 'enum_review_complete',
                     enum_rounds=s.get('enum_rounds', 0)+1,
                     enum_history=s.get('enum_history', [])+[entry], reviewed_revision=None,
                     metrics={**s['metrics'], 'model_calls': s['metrics']['model_calls']+int(model_called),
                              'enum_table_hits': s['metrics'].get('enum_table_hits', 0)+len(cached),
                              'enum_seconds': s['metrics'].get('enum_seconds', 0)+round(time.monotonic()-started, 3)})

    def fallback(s):
        current_ops = list(s['operations'])
        existing = {op['id'] for op in current_ops}
        current_ops += [op for op in s.get('retained_operations', []) if op['id'] not in existing]
        if s.get('agent_tuning', True):
            return stamp(s, 'fallback', status='fallback_complete', operations=current_ops)
        additions = fallback_operations(s['current'], s['results'], set(s.get('fallback_attempted', [])))
        additions = [op for op in additions if op['id'] not in s.get('linkage', {}).get('blocked', {})]
        ids = {op['id'] for op in additions}
        manual = {item['field_id']: item for item in s.get('manual_review', [])}
        for op in additions:
            manual[op['id']] = {'field_id': op['id'], 'label': op['field']['label'], 'mode': op['fallback'],
                                'reason': s['results'].get(op['id'], {}).get('reason', 'unresolved')}
        return stamp(s, 'fallback', status='fallback_ready' if additions else 'fallback_complete',
                     operations=[op for op in current_ops if op['id'] not in ids]+additions,
                     results={k:v for k,v in s['results'].items() if k not in ids},
                     fallback_attempted=list(set(s.get('fallback_attempted', [])) | ids),
                     manual_review=list(manual.values()))

    def rediscover(s):
        rounds = s.get('rediscovery_rounds', 0)
        if time.time() >= s['deadline']:
            return stamp(s, 'rediscover', status='budget_exhausted')
        return stamp(s, 'rediscover', status='rediscovered', snapshot=copy.deepcopy(s['current']),
                     semantic_attempted=False, mapping_cache={}, rediscovery_rounds=rounds+1,
                     retained_operations=retained_writes(s['operations'], s['results'], s['current']),
                     enum_pending=[], fallback_attempted=[])

    def verify(s):
        if any(f['id'] in s.get('linkage', {}).get('blocked', {}) for f in s['current']['fields']):
            return stamp(s, 'verify', status='linkage_branch_blocked', review={'issues': list(s['linkage']['blocked'].values())})
        if {f["id"] for f in s["current"]["fields"]} != {f["id"] for f in s["snapshot"]["fields"]}:
            return stamp(s, "verify", status="rediscovery_required")
        if any(r["status"] in {"conflict", "unknown"} for r in s["results"].values()):
            return stamp(s, "verify", status="partial_draft")
        completed = [op for op in s["operations"] if s["results"].get(op["id"], {}).get("status") in SUCCESS]
        prefilled = [fid for fid,result in s['results'].items() if result.get('status') == 'prefilled_pending_review']
        if not completed and not prefilled and not all(protected_present(f) or f.get('verification_skipped') for f in s['current']['fields']):
            return stamp(s, "verify", status="nothing_filled")
        if not page_matches(completed, s["current"]):
            return stamp(s, "verify", status="readback_mismatch")
        if any(f.get("required") and not f.get('verification_skipped') and not protected_present(f) and (f.get("value") in (None, "", []) or (f["kind"] == "checkbox" and f["value"] is not True))
               for f in s["current"]["fields"]):
            return stamp(s, "verify", status="required_field_missing")
        if s.get('defer_review'):
            return stamp(s, 'verify', status='filled_pending_review', reviewed_revision=None)
        if not s.get('agent_tuning', True):
            review = execution_review(s)
            return stamp(s, 'verify', status='verified' if review['approved'] else 'review_rejected',
                         review=review, reviewed_revision=s['revision'] if review['approved'] else None,
                         metrics={**s['metrics'], 'review_model': 'program'})
        started = time.monotonic()
        review = model.review(redacted_profile(s["profile"]), s["snapshot"], review_plan(s["proposal"], s['operations']), s["current"])
        closed(review, {"approved", "checked_field_ids", "issues"})
        expected = {f["id"] for f in s["snapshot"]["fields"]}
        checked = review["checked_field_ids"]
        accepted = review["approved"] is True and not review["issues"] and len(checked) == len(set(checked)) and set(checked) == expected
        return stamp(s, "verify", status="verified" if accepted else "review_rejected", review=review,
                     reviewed_revision=s["revision"] if accepted else None,
                     metrics={**s["metrics"], "model_calls": s["metrics"]["model_calls"]+1,
                              "review_model": getattr(model, 'model', None),
                              "review_seconds": round(time.monotonic()-started, 3)})

    def prepare_save(s):
        if not s["authorization"]["save"]:
            return stamp(s, "prepare_save", status="verified_draft")
        if s["command"] is not None or s["reviewed_revision"] != s["revision"]:
            raise ContractError("save_requires_current_review")
        save = s["current"].get("save")
        if not save or not save.get("selector") or not save.get("signal"):
            return stamp(s, "prepare_save", status="save_control_unverified")
        if time.time() >= s["deadline"]:
            return stamp(s, "prepare_save", status="budget_exhausted")
        command = {"version": 1, "command_id": str(uuid.uuid4()), "kind": "save", "browser": "edge",
            "target": s["snapshot"]["target"], "module_id": s["snapshot"]["module_id"],
            "module_selector": s["snapshot"]["module_selector"], "snapshot_id": s["current"]["snapshot_id"],
            "revision": s["revision"], "reviewed_revision": s["reviewed_revision"],
            "expected_fields": [{k: f.get(k) for k in ("id", "value", "kind", "value_present", "upload_ready", "verification_skipped")}
                                for f in s["current"]["fields"] if not f.get("protected")],
            "save": save, "capture": s["snapshot"].get("capture", {}), "deadline": s["deadline"]}
        return stamp(s, "prepare_save", status="awaiting_save", command=command)

    def execute_save(s):
        receipt = validate_receipt(s["command"], interrupt({"role": "trusted_edge_host", "command": s["command"]}))
        if not receipt["settled"] or receipt["status"] != "saved" or not receipt["evidence"].get("save_confirmed"):
            return stamp(s, "execute_save", status="save_unconfirmed")
        return stamp(s, "execute_save", status="saved", command=None, last_receipt=receipt)

    def compile_experience(s):
        if knowledge is None or s.get('manual_review'):
            return stamp(s, 'compile_experience')
        try:
            counts = knowledge.compile_saved(s['profile'], [s], s['last_receipt'])
            return stamp(s, 'compile_experience', metrics={**s['metrics'], **{
                key: s['metrics'].get(key, 0) + value for key, value in counts.items()}})
        except (OSError, ValueError, KeyError, TypeError) as exc:
            return stamp(s, 'compile_experience', learning_errors=s.get('learning_errors', []) + [
                {'phase': 'compile_saved', 'error': str(exc) if isinstance(exc, (ContractError, ValueError)) else type(exc).__name__}])

    graph = StateGraph(State)
    graph.add_node('fallback', fallback)
    graph.add_node('rediscover', rediscover)
    for name, fn in [("map", map_fields), ("validate", validate), ("prepare_fill", prepare_fill),
                     ("execute_fill", execute_fill), ('resolve_unknown', resolve_unknown), ('review_enums', review_enums), ("verify", verify), ("prepare_save", prepare_save), ("execute_save", execute_save), ("compile_experience", compile_experience)]:
        graph.add_node(name, fn)
    graph.add_edge(START, "map")
    graph.add_conditional_edges("map", lambda s: "validate" if s["status"] == "mapped" else END)
    graph.add_conditional_edges("validate", lambda s: "prepare_fill" if s["status"] == "validated" else END)
    graph.add_conditional_edges("prepare_fill", lambda s: "execute_fill" if s["status"] == "awaiting_edge" else "resolve_unknown" if s["status"] == "filled" else END)
    graph.add_conditional_edges('resolve_unknown', lambda s: 'prepare_fill' if s['status'] == 'semantic_resolved'
                                else 'resolve_unknown' if s['status'] == 'field_identity_aligned'
                                else 'review_enums' if s['status'] == 'semantic_complete' else END)
    graph.add_conditional_edges("execute_fill", lambda s: "prepare_fill" if s["status"] == "batch_complete" else
                                'rediscover' if s['status']=='inventory_changed' else END)
    graph.add_conditional_edges('rediscover', lambda s: 'map' if s['status']=='rediscovered' else END)
    graph.add_conditional_edges('review_enums', lambda s: 'prepare_fill' if s['status'] == 'enum_repaired'
                                else 'fallback' if s['status'] == 'enum_review_complete' else END)
    graph.add_conditional_edges('fallback', lambda s: 'prepare_fill' if s['status']=='fallback_ready' else 'verify')
    graph.add_conditional_edges("verify", lambda s: "prepare_save" if s["status"] == "verified" else END)
    graph.add_conditional_edges("prepare_save", lambda s: "execute_save" if s["status"] == "awaiting_save" else END)
    graph.add_conditional_edges("execute_save", lambda s: "compile_experience" if s["status"] == "saved" else END)
    graph.add_edge("compile_experience", END)
    return graph.compile(checkpointer=checkpointer)
