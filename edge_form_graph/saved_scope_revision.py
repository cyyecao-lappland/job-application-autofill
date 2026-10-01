"""Prepare a previously saved scope for a user-authorized correction pass."""
from __future__ import annotations

import copy
import json
import time

from .contracts import ContractError, page_matches
from .parallel import fill_results_complete
from .scope_isolation import assert_settled_history


def _saved_scope_receipt(values, folder, scope_id, scope_selector, modules):
    expected_ids = {m['id'] for m in modules}
    valid = []
    for path in (folder / 'writer').glob('*.json'):
        journal = json.loads(path.read_text(encoding='utf-8'))
        receipt = journal.get('receipt') or {}
        evidence = receipt.get('evidence') or {}
        scope_snapshot = receipt.get('snapshot') or {}
        if (journal.get('kind') not in {'save_scope', 'verify_autosave', 'reconcile_save'}
                or receipt.get('command_id') != journal.get('command_id')
                or receipt.get('kind') != journal.get('kind')
                or scope_snapshot.get('module_id') != scope_id
                or scope_snapshot.get('module_selector') != scope_selector
                or scope_snapshot.get('target') != values['manifest'].get('target')
                or receipt.get('target') != values['manifest'].get('target')
                or receipt.get('settled') is not True or receipt.get('status') != 'saved'
                or evidence.get('save_confirmed') is not True
                or not isinstance(evidence.get('save_observed_at'), (int, float))
                or evidence.get('save_observed_at', 0) <= 0):
            continue
        saved = evidence.get('saved_modules')
        if not isinstance(saved, list) or len(saved) != len(modules):
            continue
        by_id = {item.get('module_id'): item for item in saved if isinstance(item, dict)}
        if len(by_id) != len(saved) or set(by_id) != expected_ids:
            continue
        proven = True
        for module in modules:
            result = values.get('results', {}).get(module['id'])
            current = (result or {}).get('current') or {}
            post = by_id[module['id']]
            persisted_fields = post.get('fields')
            if (not persisted_fields and post.get('persistence_evidence', {}).get('kind') == 'module_card_all_values_visible'
                    and isinstance(post.get('persisted_fields'), list)):
                persisted_fields = post['persisted_fields']
            comparable_post = {**post, 'fields': persisted_fields or []}
            if (not result or not current.get('fields') or not persisted_fields
                    or current.get('module_id') != module['id']
                    or current.get('module_selector') != module['selector']
                    or current.get('target') != receipt['target']
                    or post.get('target') != receipt['target']
                    or post.get('module_selector') != module['selector']
                    or post.get('observed_at', 0) < evidence['save_observed_at']
                    or [{k: v for k, v in f.items() if k != 'controls'} for f in current['fields']]
                       != [{k: v for k, v in f.items() if k != 'controls'} for f in persisted_fields]
                    or not page_matches(result.get('operations', []), comparable_post)
                    or not fill_results_complete(result, result.get('preserved_blank_ids', []))):
                proven = False
                break
        if proven:
            valid.append((journal.get('started_at', 0), receipt))
    if len(valid) != 1:
        raise ContractError('saved_scope_success_proof_missing_or_ambiguous')
    return valid[0][1]


def reopen_saved_scope(values, next_nodes, folder, module_id, basis, *, writer_lock_held=False):
    """Drop only one confirmed-saved scope's live results and restart it normally."""
    if (not writer_lock_held or not isinstance(basis, str) or not basis.strip()
            or not values.get('manifest', {}).get('program_inventory')):
        raise ContractError('saved_scope_revision_requires_program_inventory_lock_and_basis')

    command = values.get('command') or {}
    active_module = next((m for m in values.get('manifest', {}).get('modules', [])
                          if m.get('page_order') == values.get('index')), None)
    pending_observe = (tuple(next_nodes) == ('observe',)
        and values.get('status') == 'awaiting_observation'
        and command.get('kind') == 'observe' and command.get('command_id')
        and active_module is not None and command.get('module_id') == active_module.get('id')
        and command.get('module_selector') == active_module.get('selector')
        and not (folder / 'writer' / (command['command_id'] + '.json')).exists())
    stopped = (not next_nodes and not command
        and values.get('status') in {'incomplete_coverage', 'budget_exhausted'})
    if not (pending_observe or stopped):
        raise ContractError('saved_scope_revision_requires_quiescent_boundary')

    assert_settled_history(values, folder)
    modules = values['manifest'].get('modules', [])
    matches = [i for i, module in enumerate(modules) if module.get('id') == module_id]
    if len(matches) != 1:
        raise ContractError('saved_scope_revision_module_not_unique')
    index = matches[0]
    scope_id = modules[index].get('save_scope')
    scope_modules = [module for module in modules if module.get('save_scope') == scope_id]
    scopes = values.get('scopes', {})
    if not scope_id or scopes.get(scope_id) != 'saved_confirmed' or not scope_modules:
        raise ContractError('saved_scope_revision_requires_confirmed_scope')
    scope_defs = [scope for scope in values['manifest'].get('save_scopes', []) if scope.get('id') == scope_id]
    if len(scope_defs) != 1:
        raise ContractError('saved_scope_revision_scope_definition_not_unique')
    if any(module['id'] not in values.get('results', {}) for module in scope_modules):
        raise ContractError('saved_scope_revision_results_missing')

    save_receipt = _saved_scope_receipt(values, folder, scope_id, scope_defs[0]['selector'], scope_modules)
    now = time.time()
    scoped_ids = {module['id'] for module in scope_modules}
    results = copy.deepcopy(values.get('results', {}))
    previous_results = {mid: results.pop(mid) for mid in scoped_ids}
    mapping_cache = copy.deepcopy(values.get('mapping_cache', {}))
    previous_mapping = {mid: mapping_cache.pop(mid) for mid in list(mapping_cache) if mid in scoped_ids}
    scope_reviews = copy.deepcopy(values.get('scope_reviews', {}))
    previous_review = scope_reviews.pop(scope_id, None)
    new_scopes = copy.deepcopy(scopes)
    previous_scope_state = new_scopes.pop(scope_id)

    last_save_receipt = copy.deepcopy(values.get('last_save_receipt', {}))
    if (last_save_receipt or {}).get('module_id') == scope_id:
        last_save_receipt = {}
    history = copy.deepcopy(values.get('revisit_history', []))
    history.append({'kind': 'saved_scope_revision', 'module_id': module_id, 'scope_id': scope_id,
        'module_ids': sorted(scoped_ids), 'at': now, 'basis': basis,
        'previous_status': values.get('status'), 'previous_index': values.get('index'),
        'previous_scopes': copy.deepcopy(scopes), 'previous_scope_state': previous_scope_state,
        'previous_results': previous_results,
        'previous_scope_review': previous_review, 'previous_mapping_cache': previous_mapping,
        'save_receipt': save_receipt,
        'superseded_undispatched_observe': copy.deepcopy(command) if pending_observe else None})
    return {'index': index, 'status': 'advance', 'command': None, 'current': {},
        'manifest': copy.deepcopy(values['manifest']),
        'results': results, 'scopes': new_scopes, 'scope_reviews': scope_reviews,
        'mapping_cache': mapping_cache, 'last_save_receipt': last_save_receipt,
        'control_diagnostics': {}, 'control_request': {}, 'recovery_reason': None,
        'recovery_requested': False, 'revisit_history': history}
