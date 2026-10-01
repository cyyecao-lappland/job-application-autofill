"""Reacquire already bound SF education cards, without replaying old writes."""
import copy
import json
import time

from .contracts import ContractError
from .page_planner import records_for, _education_level
from .scope_isolation import assert_settled_history


def _boundary_rejected_before_writes(folder, last):
    """A structural rejection is resolved only by its unchanged no-action journal."""
    if (last.get('settled') is not True or last.get('status') != 'partial'
            or not last.get('results') or not last.get('command_id')
            or any(x.get('status') != 'conflict' or x.get('reason') != 'record_boundary_changed'
                   for x in last['results'])):
        return False
    path = folder / 'writer' / (last['command_id'] + '.json')
    if not path.is_file():
        return False
    journal = json.loads(path.read_text(encoding='utf-8'))
    calls = journal.get('instrumentation') or []
    return (journal.get('kind') == 'fill' and journal.get('command_id') == last['command_id']
            and journal.get('receipt') == last and journal.get('action_issued') is False
            and journal.get('pending_field') is None and bool(calls)
            and all(e.get('method') == 'evaluate' and e.get('stage') == 'module_readback'
                    and e.get('status') == 'returned' for e in calls))


def education_identity_matches(record, fields):
    names = {'school_name': {'学校全称', '学校名称', '学校', '毕业院校'},
             'education_level': {'学历', '学历层次', '学历类别'},
             'major': {'专业名称', '专业', '所学专业'}}
    for key, labels in names.items():
        values = [str(f.get('value') or '').strip() for f in fields
                  if f.get('label', '').strip(' *:：') in labels]
        expected = str(record.get(key) or '').strip()
        if len(values) != 1 or not expected or not values[0]:
            return False
        if key == 'education_level':
            if _education_level(values[0]) != _education_level(expected):
                return False
        elif values[0] != expected:
            return False
    return True


def rebind_collapsed_education(values, next_nodes, folder, inventory, scope_id, basis, *, writer_lock_held=False):
    if (not writer_lock_held or not basis.strip() or next_nodes or values.get('command')
            or values.get('status') not in {'observation_unconfirmed', 'incomplete_coverage', 'module_blocked'}
            or not values['manifest'].get('program_inventory')):
        raise ContractError('card_rebind_requires_stopped_original_application')
    target = values['manifest']['target']
    if (inventory.get('url') != target['url']
            or any(inventory.get('target', {}).get(k) != target.get(k) for k in ('browser_id', 'tab_id', 'url'))
            or not 0 <= time.time() - inventory.get('observed_at', 0) < 120):
        raise ContractError('card_rebind_requires_fresh_same_tab_inventory')
    assert_settled_history(values, folder)
    scope = next((s for s in values['manifest']['save_scopes'] if s['id'] == scope_id), None)
    if not scope or values.get('scopes', {}).get(scope_id) == 'saved_confirmed':
        raise ContractError('card_rebind_requires_unsaved_scope')
    selected = [(i, m) for i, m in enumerate(values['manifest']['modules']) if m['save_scope'] == scope_id]
    if not selected or [i for i, _ in selected] != list(range(selected[0][0], selected[-1][0] + 1)):
        raise ContractError('card_rebind_requires_contiguous_bound_records')
    for _, module in selected:
        result = values.get('results', {}).get(module['id'], {})
        last = result.get('last_receipt') or {}
        if (module.get('mapping_context', {}).get('record_collection') != '/education'
                or result.get('status') in {'needs_reconciliation', 'recovery_blocked'}
                or result.get('coverage_hold') == 'needs_reconciliation'
                or last.get('status') in {'unknown', 'unconfirmed'}
                or (any(x.get('status') in {'unknown', 'conflict'} for x in last.get('results', []))
                    and not _boundary_rejected_before_writes(folder, last))
                or any(x.get('status') in {'unknown', 'conflict'} for x in result.get('results', {}).values())):
            raise ContractError('card_rebind_requires_resolved_education_writes')
    # A settled failed save/add remains unresolved. It cannot be erased by a rebind.
    journals = [json.loads(path.read_text(encoding='utf-8')) for path in (folder / 'writer').glob('*.json')]
    from .application_cli import reconciliation_parent
    resolved = {}
    for journal in journals:
        receipt = journal.get('receipt') or {}
        if (journal.get('kind') == 'reconcile_save' and receipt.get('settled') is True
                and receipt.get('status') == 'saved' and receipt.get('evidence', {}).get('save_confirmed') is True):
            parent = reconciliation_parent(folder, journal.get('command_id'))
            if parent:
                resolved[parent] = journal
    for journal in journals:
        if journal.get('kind') not in {'save', 'save_scope', 'reconcile_save', 'verify_autosave'}:
            continue
        receipt = journal.get('receipt') or {}
        original = journal.get('command_id') if journal.get('kind') in {'save', 'save_scope'} else (
            reconciliation_parent(folder, journal.get('command_id')) if journal.get('kind') == 'reconcile_save' else None)
        confirmation = resolved.get(original)
        if (confirmation and receipt.get('settled') is True
                and receipt.get('target') == confirmation['receipt'].get('target')
                and confirmation.get('started_at', 0) > journal.get('started_at', 0)):
            continue
        if receipt.get('status') != 'saved' or receipt.get('evidence', {}).get('save_confirmed') is not True:
            from .save_preflight_recovery import audited_preflight
            if not audited_preflight(folder, journal):
                raise ContractError('card_rebind_requires_save_reconciliation')
    framework = inventory.get('framework') or {}
    sections = [s for s in framework.get('sections', []) if s['selector'] == scope['selector']]
    if framework.get('family') != 'sf' or len(sections) != 1:
        raise ContractError('card_rebind_framework_or_section_changed')
    rows = sections[0].get('records', [])
    if len(rows) != len(selected) or any(r.get('state') != 'collapsed' or not r.get('edit_selector') for r in rows):
        raise ContractError('card_rebind_requires_unique_closed_cards')
    candidates, _ = records_for(values['profile'], 'education', sections[0]['label'])
    assigned = {}
    for row in rows:
        fields = [{'label': k, 'value': v} for k, v in row.get('card_values', {}).items()]
        matches = [(binding, record) for binding, record in candidates if education_identity_matches(record, fields)]
        if len(matches) != 1 or matches[0][0]['record_id'] in assigned:
            raise ContractError('card_rebind_identity_not_unique')
        assigned[matches[0][0]['record_id']] = row
    if set(assigned) != {m['mapping_context']['record_id'] for _, m in selected}:
        raise ContractError('card_rebind_does_not_match_original_bindings')
    manifest = copy.deepcopy(values['manifest'])
    results = copy.deepcopy(values.get('results', {}))
    originals = []
    new_scopes = []
    for index, old in selected:
        row = assigned[old['mapping_context']['record_id']]
        sid = scope_id + ':' + old['id']
        if any(s['id'] == sid for s in manifest['save_scopes']):
            raise ContractError('card_rebind_scope_already_exists')
        # Each existing card owns its own editor/save control. The executor
        # must observe a unique actual save button before it can dispatch save.
        capture = {'controlDiagnostics': True, 'saveControl': 'button,.normal-btn', 'saveLabel': '保存',
                   'savedSignal': {'kind': 'module_readback', 'selector': row['selector'],
                                   'editSelector': '.normal-btn:not(.transparent)'}}
        manifest['modules'][index] = {'id': old['id'], 'page_order': index, 'selector': row['selector'],
            'module_label': old['module_label'], 'mapping_context': copy.deepcopy(old['mapping_context']),
            'save_scope': sid, 'capture': capture, 'existing_card_identity': True}
        new_scopes.append({'id': sid, 'selector': row['selector'], 'mode': 'explicit', 'capture': capture,
            'evidence': 'SF unique existing record card; scoped save control must be verified after edit opens'})
        originals.append({'module': copy.deepcopy(old), 'result': results.pop(old['id'], None)})
    manifest['save_scopes'] = [s for s in manifest['save_scopes'] if s['id'] != scope_id] + new_scopes
    from .application import application_state
    application_state(values['profile'], manifest, allow_save=values.get('allow_save', False))
    now = time.time()
    return {'manifest': manifest, 'results': results, 'index': selected[0][0], 'status': 'advance',
            'command': None, 'current': {}, 'mapping_cache': {}, 'scope_reviews': {},
            'control_diagnostics': {}, 'recovery_reason': None, 'deadline': now + 1800,
            'inventory_history': values.get('inventory_history', []) + [{'kind': 'existing_education_card_rebind',
                'at': now, 'basis': basis, 'originals': originals, 'inventory': copy.deepcopy(inventory)}]}
