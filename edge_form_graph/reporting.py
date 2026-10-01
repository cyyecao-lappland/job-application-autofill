"""Privacy-safe reports. Inputs/journals are evidence, never recovery authority.

Preserve actionable field/source/evidence references. Bound free text and redact
known personal values and long numeric identifiers; never serialize profiles.
"""
from __future__ import annotations

import json
import math
import os
import re
import tempfile
import time
from collections import Counter
from pathlib import Path


STATUSES = set(('new complete completed partial pending unknown unconfirmed written '
    'complete_with_fallbacks linkage_branch_blocked inventory_changed rediscovered '
    'verification_skipped already_matched prefilled_pending_review unattempted deferred conflict filled saved submitted saved_confirmed '
    'verified verified_draft filled_pending_review partial_draft awaiting_edge awaiting_save '
    'needs_reconciliation save_unconfirmed plan_rejected review_rejected budget_exhausted '
    'readback_mismatch no_progress nothing_filled required_field_missing mapped validated '
    'batch_complete superseded_by_user module_filled module_blocked scope_reviewed '
    'scope_review_blocked within_save_scope awaiting_observation awaiting_scope_save '
    'semantic_resolved semantic_complete save_scope_unknown recovery_blocked '
    'awaiting_fill_reconciliation rediscovery_required save_control_unverified '
    'automatic_save_verifier_required scope_draft').split())
NODES = set(('map validate prepare_fill execute_fill review_enums verify prepare_save fallback rediscover '
    'execute_save premap enter observe fill review save advance reconcile repair resolve_unknown').split())
STAGES = set(('popup_open popup_search popup_select popup_close popup_readback '
    'popup_baseline_read popup_read module_readback identity_readback ui_read field_action '
    'control_read save_click save_readback save_click_issued save_click_returned '
    'confirmation_issued confirmation_returned observation_returned dialog_read save_confirmation').split())
METRICS = set(('batch_count model_calls model_hits table_hits mapping_table_hits '
    'fallback_written '
    'program_mapping_hits model_mapping_hits mapping_cache_hit mapping_cache_hits '
    'cache_hits cache_misses inferred_fields mapped_fields unresolved_fields '
    'written already_matched verification_skipped mapping_seconds ttff_seconds model_seconds '
    'table_hit_count model_call_count model_hit_count field_table_hits enum_table_hits '
    'enum_learned field_learned enum_seconds semantic_fields semantic_seconds '
    'semantic_prompt_chars semantic_input_data_chars semantic_schema_chars').split())
TASK_ACTIONS = {
    'unsupported': 'Inspect the control contract and add a supported semantic adapter with synthetic tests before attempting a write.',
    'missing': 'Locate verified source information or request the missing fact; do not invent a value.',
    'conflict': 'Compare fresh read-only evidence with the approved source and resolve the conflict before writing.',
    'inference': 'Review the proposed interpretation against source evidence; retain uncertainty until independently accepted.',
    'mapping': 'Check the source-to-field mapping and exact option correspondence before use.',
    'worker_error': 'Inspect the semantic worker failure separately from browser execution; preserve unresolved fields.',
    'unknown': 'Inspect the unresolved evidence and classify the next step without assuming a cause.',
    'reconciliation': 'Resolve the original pending call outcome before any replay; do not treat timeout as settlement.',
    'learning': 'Inspect the failed learning phase; do not treat an unpersisted mapping as a table hit.',
}
CAUSES = {
    'local_executor_error': ('local', 'The executor failed before a field action; recorded browser reads had returned.',
        'Inspect the retained local exception, especially journal replacement errors, before changing browser controls.'),
    'option_missing_or_ambiguous': ('mapping', 'An exact unique option was unavailable.',
        'Review the observed option mapping; keep unresolved choices deferred.'),
    'dependency_not_ready': ('mapping', 'A required predecessor was incomplete.',
        'Resolve the predecessor before attempting dependent fields.'),
    'plan_rejected': ('mapping', 'The mapping plan failed validation.',
        'Inspect mapping validation before changing UI execution.'),
    'review_rejected': ('inference', 'Independent review rejected the proposal.',
        'Reassess the inference with source evidence.'),
    'value_did_not_match_after_action': ('ui', 'Readback did not match the requested value.',
        'Inspect the control commit behavior with a read-only observation.'),
    'selection_not_committed': ('ui', 'The selection commit check failed.',
        'Inspect popup closure and selection readback.'),
    'popup_ownership_unconfirmed': ('ui', 'Popup ownership was not established.',
        'Improve ownership evidence before allowing selection.'),
    'old_value_changed': ('ui', 'The field changed after the baseline observation.',
        'Re-observe and review the changed field before any write.'),
    'browser_call_failed': ('ui', 'A browser call failed; its root cause is unknown.',
        'Inspect the failed stage and original call settlement before recovery.'),
    'timeout': ('ui', 'The runtime reported a timeout; its root cause is unknown.',
        'Inspect the failed stage; a timeout does not establish settlement.'),
    'transport_failure': ('ui', 'The runtime reported a transport failure; its root cause is unknown.',
        'Inspect the host connection and original call outcome.'),
}


def number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def token(value, allowed):
    return value if isinstance(value, str) and value in allowed else 'unknown'


def timings(trace, started_at=None):
    """Prefer explicit durations. Legacy stamps are intervals, including waits."""
    result = []
    previous = started_at
    for index, event in enumerate(trace if isinstance(trace, list) else []):
        if not isinstance(event, dict):
            continue
        duration = event.get('duration_ms', event.get('elapsed_ms'))
        basis = 'explicit_duration'
        if not number(duration):
            seconds = event.get('elapsed_seconds', event.get('duration_seconds'))
            if number(seconds):
                duration = seconds * 1000
            elif number(event.get('started_at')) and number(event.get('ended_at')):
                duration = (event['ended_at'] - event['started_at']) * 1000
            elif number(previous) and number(event.get('at')):
                duration = (event['at'] - previous) * 1000
                basis = 'stamp_interval_including_waits'
            else:
                duration, basis = None, 'unknown'
        result.append({'event': index + 1, 'node': token(event.get('node'), NODES),
                       'elapsed_ms': round(duration, 3) if number(duration) and duration >= 0 else None,
                       'basis': basis})
        previous = event.get('at')
    return result


def build_report(folder, values):
    """Read current application/module state and all writer journals below folder."""
    private = set()

    def collect(value, personal=False):
        if isinstance(value, dict):
            for key, item in value.items():
                collect(item, personal or key in {'profile', 'value', 'source_value', 'expected', 'before', 'after', 'secret'})
        elif isinstance(value, list):
            for item in value:
                collect(item, personal)
        elif personal and isinstance(value, str) and value:
            private.add(value)

    collect(values)
    # Read once, before sanitization, so journal-only values also redact reasons.
    journal_inputs = []
    for path in sorted(Path(folder).rglob('writer/*.json')):
        try:
            journal = json.loads(path.read_text(encoding='utf-8'))
            if not isinstance(journal, dict):
                raise ValueError('journal_not_object')
            collect(journal)
        except (OSError, ValueError):
            journal = None
        journal_inputs.append((path, journal))

    def safe(value, limit=500):
        if not isinstance(value, str):
            return None
        for secret in sorted(private, key=len, reverse=True):
            if len(secret) >= 3:
                value = value.replace(secret, '[redacted-value]')
            else:
                value = re.sub(r'(?<!\w)'+re.escape(secret)+r'(?!\w)', '[redacted-value]', value)
        value = re.sub(r'\d{8,}', '[redacted-number]', value)
        value = re.sub(r'[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}', '[redacted-email]', value)
        return value[:limit]

    def command_ref(value):
        if isinstance(value, str) and value not in private and re.fullmatch(r'[a-fA-F0-9]{8}(?:-[a-fA-F0-9]{4}){3}-[a-fA-F0-9]{12}', value):
            return value
        return safe(value)

    report = {'version': 1, 'status': token(values.get('status'), STATUSES),
              'agent_tuning': values.get('agent_tuning', True), 'manual_review': [],
              'recovery_reason': safe(values.get('recovery_reason')),
              'value_comparison': [{'field_id': safe(c.get('field_id')),
                  'status': token(c.get('status'), {'matched','unchanged','conflict','unreadable'})}
                  for c in values.get('value_comparison',[]) if isinstance(c,dict)],
              'modules': [], 'journals': [], 'diagnoses': [], 'unknown_operations': [],
              'agent_tasks': [], 'learning_errors': [],
              'metrics': {}, 'node_timings': timings(values.get('trace'), values.get('started_at')),
              'progress': {'filled_modules': 0, 'saved_scopes': 0, 'submitted_confirmed': False},
              'future_improvements': []}

    def diagnose(code, ref):
        category, observation, improvement = CAUSES.get(code, (
            'unknown', 'Evidence does not establish a cause.',
            'Collect structured stage evidence without copying personal values.'))
        confirmed = code in {'option_missing_or_ambiguous', 'dependency_not_ready', 'old_value_changed'}
        report['diagnoses'].append({'reference': ref, 'category': category,
            'observed_reason': safe(code),
            'confirmed_observation': observation, 'confirmed_cause': observation if confirmed else None,
            'hypothesis': None, 'cause_status': 'confirmed' if confirmed else 'unknown',
            'code': code if code in CAUSES else 'unknown'})
        if improvement not in report['future_improvements']:
            report['future_improvements'].append(improvement)

    def metrics(state):
        output = {key: value for key, value in (state.get('metrics') or {}).items()
                if key in METRICS and (number(value) or isinstance(value, bool))}
        for key in ('semantic_model', 'review_model'):
            raw = (state.get('metrics') or {}).get(key)
            if raw is not None:
                output[key] = safe(raw)
        return output

    def task(ref, category, evidence, **context):
        report['agent_tasks'].append({'reference': ref, 'category': category,
            'evidence': evidence, 'context': context, 'root_cause': 'unknown',
            'recommended_action': TASK_ACTIONS[category], 'executed': False})

    def learning(state, ref):
        for index, error in enumerate(state.get('learning_errors') or [], 1):
            if not isinstance(error, dict):
                error = {}
            entry = {'reference': f'{ref}/learning-{index}',
                'phase': token(error.get('phase'), {'learn_fields', 'learn_enums', 'compile_saved'}),
                'error': safe(error.get('error'))}
            report['learning_errors'].append(entry)
            task(entry['reference'], 'learning', 'recorded_learning_error', phase=entry['phase'])

    report['metrics'] = metrics(values)
    # Application results contain full module states; a standalone module is also accepted.
    results = values.get('results') or {}
    states = [v for v in results.values() if isinstance(v, dict) and
              any(k in v for k in ('snapshot', 'metrics', 'trace', 'operations'))]
    current = values.get('current')
    if isinstance(current, dict) and any(k in current for k in ('metrics', 'trace', 'operations')) and current not in states:
        states.append(current)
    if not states and any(k in values for k in ('snapshot', 'operations', 'trace')):
        states = [values]
    if all(state is not values for state in states):
        learning(values, 'application')
    for index, state in enumerate(states, 1):
        ref = f'module-{index}'
        module_id = (state.get('snapshot') or {}).get('module_id')
        if module_id is None:
            module_id = next((key for key, item in results.items() if item is state), None)
        fields = {f.get('id'): f for f in (state.get('current') or state.get('snapshot') or {}).get('fields', []) if isinstance(f, dict)}
        report['manual_review'].extend({'module_id': safe(module_id), 'field_id': safe(item.get('field_id')),
            'label': safe(item.get('label')), 'mode': safe(item.get('mode')), 'reason': safe(item.get('reason'))}
            for item in state.get('manual_review', []))
        plans = {op.get('id'): op for op in state.get('operations', []) if isinstance(op, dict)}
        learning(state, ref)
        operations = state.get('results') or {}
        operations = list(operations.values()) if isinstance(operations, dict) else operations
        counts = Counter(token(r.get('status'), STATUSES) for r in operations if isinstance(r, dict))
        filled = not state.get('manual_review') and bool(operations) and all(isinstance(r, dict) and r.get('status') in {'written', 'already_matched'} for r in operations)
        report['progress']['filled_modules'] += int(filled)
        report['modules'].append({'reference': ref, 'status': token(state.get('status'), STATUSES),
            'module_id': safe(module_id),
            'filled_confirmed': filled, 'counts': dict(counts), 'metrics': metrics(state),
            'linkage': {'max_depth': max((state.get('linkage') or {}).get('depths', {}).values(), default=1),
                'depth_limit': 15,
                'blocked': {safe(fid): token(reason, {'linkage_depth_limit', 'linkage_cycle'})
                    for fid, reason in (state.get('linkage') or {}).get('blocked', {}).items()}},
            'model_classifications_unconfirmed': dict(Counter(
                token(note.get('classification'), {'mapping', 'inference', 'missing', 'conflict', 'unsupported', 'worker_error'})
                for note in state.get('semantic_report', []) if isinstance(note, dict))),
            'node_timings': timings(state.get('trace'), state.get('started_at'))})
        result_map = state.get('results') if isinstance(state.get('results'), dict) else {}
        for note_index, note in enumerate(state.get('semantic_report') or [], 1):
            if not isinstance(note, dict):
                continue
            field_id = note.get('field_id')
            prior = result_map.get(field_id, {}) if isinstance(field_id, str) else {}
            if prior.get('status') in {'written', 'already_matched'}:
                continue
            category = token(note.get('classification'), set(TASK_ACTIONS))
            task(f'{ref}/decision-{note_index}', category, 'model_classification_unconfirmed',
                 module_id=safe(module_id), field_id=safe(field_id),
                 field_label=safe(fields.get(field_id, {}).get('label')),
                 source=safe(note.get('source')), transform=safe(note.get('transform')),
                 reason=safe(note.get('reason')),
                 depends_on=[safe(d) for d in note.get('depends_on', [])] if isinstance(note.get('depends_on'), list) else [],
                 source_reference_present=bool(note.get('source')),
                 dependency_count=len(note['depends_on']) if isinstance(note.get('depends_on'), list) else 0,
                 result_status=token(prior.get('status'), STATUSES))
        for op_index, operation in enumerate(operations, 1):
            if not isinstance(operation, dict):
                continue
            op_ref = f'{ref}/operation-{op_index}'
            field_id = operation.get('id') or (list(result_map)[op_index-1] if result_map else None)
            field_context = {'module_id': safe(module_id), 'field_id': safe(field_id),
                             'field_label': safe(fields.get(field_id, {}).get('label')),
                             'source': safe(plans.get(field_id, {}).get('source'))}
            if operation.get('status') in {'unknown', 'pending', 'unconfirmed'}:
                report['unknown_operations'].append({'reference': op_ref, **field_context, 'status': token(operation.get('status'), STATUSES)})
            if operation.get('status') not in {'written', 'already_matched'}:
                diagnose(operation.get('reason'), op_ref)
                report['diagnoses'][-1].update(field_context)
                category = 'conflict' if operation.get('status') == 'conflict' else 'unknown'
                task(op_ref, category, 'unresolved_state_result', **field_context,
                     reason=safe(operation.get('reason')), status=token(operation.get('status'), STATUSES))
        if state.get('status') in CAUSES:
            diagnose(state['status'], ref)
    scopes = values.get('scopes') or {}
    report['progress']['saved_scopes'] = sum(s == 'saved_confirmed' for s in scopes.values())
    for index, (path, journal) in enumerate(journal_inputs, 1):
        ref = f'journal-{index}'
        if journal is None:
            report['unknown_operations'].append({'reference': ref, 'journal_path': safe(str(path), 1000), 'status': 'unreadable'})
            diagnose(None, ref)
            continue
        receipt = journal.get('receipt') or {}
        if not isinstance(receipt, dict):
            receipt = {}
        settled = receipt.get('settled') if isinstance(receipt.get('settled'), bool) else None
        status = token(receipt.get('status', journal.get('status', 'pending')), STATUSES)
        item = {'reference': ref, 'kind': token(journal.get('kind'), {'fill', 'save', 'save_scope', 'observe', 'reconcile_save', 'submit'}),
                'journal_path': safe(str(path), 1000), 'command_id': command_ref(journal.get('command_id')),
                'status': status, 'settled': settled, 'stages': [],
                'saved_confirmed': receipt.get('evidence', {}).get('save_confirmed') is True and status == 'saved' and settled is True}
        legacy_error = journal.get('last_error') or {}
        evidence = receipt.get('evidence') or {}
        item['last_error'] = {key: safe(legacy_error.get(key)) for key in ('kind', 'phase', 'code')}
        item['save_stage'] = safe(journal.get('save_stage', evidence.get('stage')))
        item['legacy_stages'] = [{'stage': safe(e.get('name', e.get('stage'))),
            'at': e.get('at') if number(e.get('at')) else None}
            for e in journal.get('stages', []) if isinstance(e, dict)]
        report['journals'].append(item)
        if not receipt or settled is not True or status in {'unknown', 'unconfirmed', 'pending'} or journal.get('pending_field') or journal.get('remote_unsettled') is True:
            report['unknown_operations'].append({'reference': ref, 'journal_path': item['journal_path'],
                'command_id': item['command_id'], 'pending_field': safe(journal.get('pending_field')), 'status': status, 'settled': settled})
        if legacy_error or evidence.get('error'):
            error = evidence.get('error') or legacy_error.get('sanitized') or {}
            diagnose(error.get('code', legacy_error.get('code')), ref)
            report['diagnoses'][-1].update(observed_error=safe(error.get('message')),
                phase=safe(legacy_error.get('phase')), stage=item['save_stage'],
                journal_path=item['journal_path'], command_id=item['command_id'])
        for op_index, operation in enumerate(receipt.get('results', journal.get('results', [])), 1):
            if not isinstance(operation, dict):
                continue
            op_ref = f'{ref}/operation-{op_index}'
            if operation.get('status') in {'unknown', 'unconfirmed', 'pending'}:
                report['unknown_operations'].append({'reference': op_ref, 'field_id': safe(operation.get('id')), 'status': token(operation.get('status'), STATUSES)})
            if operation.get('status') not in {'written', 'already_matched'}:
                diagnose(operation.get('reason'), op_ref)
                report['diagnoses'][-1].update(field_id=safe(operation.get('id')), journal_path=item['journal_path'], command_id=item['command_id'])
        for event_index, event in enumerate(journal.get('instrumentation', []), 1):
            duration = event.get('duration_ms')
            item['stages'].append({'event': event_index, 'stage': token(event.get('stage'), STAGES),
                'operation': event.get('operation') if isinstance(event.get('operation'), int) else None,
                'field_id': safe(event.get('field_id')), 'field_label': safe(event.get('field_label')),
                'error_message': safe((event.get('error') or {}).get('message')),
                'status': token(event.get('status'), {'pending', 'returned', 'failed'}),
                'duration_ms': round(duration, 3) if number(duration) and duration >= 0 else None})
            if event.get('status') == 'failed':
                diagnose((event.get('error') or {}).get('code'), f'{ref}/stage-{event_index}')
                report['diagnoses'][-1].update(stage=token(event.get('stage'), STAGES),
                    field_id=safe(event.get('field_id')), field_label=safe(event.get('field_label')),
                    observed_error=safe((event.get('error') or {}).get('message')),
                    journal_path=item['journal_path'], command_id=item['command_id'])
                if (event.get('error') or {}).get('code') == 'timeout':
                    report['diagnoses'][-1]['hypothesis'] = {
                        'status': 'unconfirmed', 'text': 'The UI condition may not have become ready, or the host call may have stalled; stage evidence alone cannot distinguish these.'}
        if item['kind'] == 'submit' and status == 'submitted' and settled is True and receipt.get('evidence', {}).get('submission_confirmed') is True:
            report['progress']['submitted_confirmed'] = True
    report['unknown_operation_count'] = len(report['unknown_operations'])
    # The parent's last completed child may predate a currently interrupted child.
    # Journals retain uncertainty independently; no parent status can clear it.
    journal_pending = any(r['reference'].startswith('journal-') for r in report['unknown_operations'])
    state_pending = bool(values.get('command')) or any(bool(s.get('command')) for s in states)
    report['evidence_currency'] = {
        'state_is_checkpoint_summary': True,
        'nested_live_state_available': False,
        'unresolved_writer_evidence': journal_pending,
        'pending_state_command': state_pending,
        'current_completion_confirmed': bool(report['modules']) and all(m['filled_confirmed'] for m in report['modules'])
            and values.get('status') in {'complete', 'filled', 'filled_pending_review', 'verified', 'verified_draft', 'saved'}
            and not (journal_pending or state_pending or report['unknown_operations']),
        'filled_modules_in_state': report['progress']['filled_modules'],
        'saved_scopes_are_historical_confirmations': True,
    }
    if journal_pending or state_pending:
        report['progress']['filled_modules'] = 0
        for module in report['modules']:
            module['filled_in_state'] = module['filled_confirmed']
            module['filled_confirmed'] = False
        task('application', 'reconciliation', 'pending_or_unknown_writer_or_state',
             parent_state_may_precede_active_child=True)
    report['learning_error_count'] = len(report['learning_errors'])
    report['timing_note'] = 'Legacy stamp intervals may include interrupts and user waits; they are not measured node execution times.'
    return report


def _atomic_write(path, text):
    descriptor, temporary = tempfile.mkstemp(prefix='.agent-report-', dir=path.parent)
    try:
        with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
            stream.write(text)
        for attempt in range(5):
            try:
                os.replace(temporary, path)
                break
            except PermissionError:
                if attempt == 4:
                    raise
                time.sleep(0.1 * (attempt + 1))
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


def write_reports(folder, values):
    """Write agent-report.json/md; never mutate state or writer journals.

    Returns the same privacy-safe dictionary written to JSON. Graph integration
    may call this after checkpoint publication; this function never resumes work.
    """
    folder = Path(folder)
    report = build_report(folder, values)
    folder.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(report, ensure_ascii=False, indent=2, allow_nan=False)
    lines = ['# Agent report', '', f"Checkpoint state: {report['status']}; current completion confirmed: {report['evidence_currency']['current_completion_confirmed']}", '',
             f"Filled modules: {report['progress']['filled_modules']}; confirmed saved scopes: {report['progress']['saved_scopes']}; "
             f"submission confirmed: {report['progress']['submitted_confirmed']}.", '',
             f"Unknown evidence records retained: {report['unknown_operation_count']}.", '',
             report['timing_note'], '', '## Diagnosis', '']
    lines += [f"- {d['reference']} [{d['category']}]: {d['confirmed_observation']} Cause status: {d['cause_status']}; hypothesis: none asserted." for d in report['diagnoses']]
    lines += ['', '## Manual review: fallback values are not confirmed facts', '']
    lines += [f"- {v['module_id']} / {v['label'] or v['field_id']}: {v['mode']} ({v['reason']})"
              for v in report['manual_review']]
    lines += ['', '## Future improvements', ''] + ['- '+v for v in report['future_improvements']]
    lines += ['', '## Agent tasks (recommendations only)', '']
    lines += [f"- {t['reference']} {t['context'].get('field_label') or t['context'].get('field_id') or ''} [{t['category']}; {t['evidence']}]: {t['recommended_action']}" for t in report['agent_tasks']]
    # Full sanitized evidence keeps counters, timings, and unknowns available in both formats.
    lines += ['', '## Evidence, counters and timings', '', '```json', payload, '```', '']
    _atomic_write(folder/'agent-report.json', payload+'\n')
    _atomic_write(folder/'agent-report.md', '\n'.join(lines))
    return report
