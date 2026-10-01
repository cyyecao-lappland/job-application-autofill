"""Apply explicit, record-bound user facts without replacing a run's profile."""
from __future__ import annotations

import copy
import json
import time
from pathlib import Path

from .contracts import ContractError, json_value
from .scope_isolation import assert_settled_history

RECORD_ROOTS = {'education', 'projects', 'company_answers', 'attachments'}
OBJECT_ROOTS = {'personal_answers', 'company_answer_defaults', 'field_metadata', 'collection_status', 'batch_answers'}


def apply_confirmed_facts(profile, patch):
    if (set(patch) != {'version', 'basis', 'updates'} or patch['version'] != 1
            or not isinstance(patch['basis'], str) or not patch['basis'].strip()
            or not isinstance(patch['updates'], list) or not patch['updates']):
        raise ContractError('confirmed_profile_patch_invalid')
    output = copy.deepcopy(profile)
    changes = []
    for entry in patch['updates']:
        if not isinstance(entry, dict) or set(entry) - {'root', 'record_id', 'create_record', 'values'}:
            raise ContractError('confirmed_profile_update_invalid')
        root, values = entry.get('root'), entry.get('values')
        if not isinstance(values, dict) or not values or 'record_id' in values:
            raise ContractError('confirmed_profile_values_invalid')
        if root in RECORD_ROOTS:
            records = output.get(root)
            rid = entry.get('record_id')
            if not isinstance(records, list) or not isinstance(rid, str) or not rid:
                raise ContractError('confirmed_profile_record_invalid')
            matches = [r for r in records if isinstance(r, dict) and r.get('record_id') == rid]
            if root == 'attachments':
                candidate = {**(matches[0] if len(matches)==1 else {}), **values}
                path = candidate.get('path')
                if (candidate.get('value_status') != 'source_backed' or not candidate.get('source')
                        or not isinstance(path, str) or not Path(path).is_absolute() or not Path(path).is_file()):
                    raise ContractError('confirmed_attachment_source_file_required')
            if not matches and root in {'company_answers', 'attachments'} and entry.get('create_record') is True:
                record = {'record_id': rid}
                records.append(record)
            elif len(matches) == 1:
                record = matches[0]
            else:
                raise ContractError('confirmed_profile_record_not_unique')
            prefix = f'record_id:{rid}/'
        elif root in OBJECT_ROOTS and 'record_id' not in entry and 'create_record' not in entry:
            if root not in output:
                output[root] = {}
            record = output[root]
            if not isinstance(record, dict):
                raise ContractError('confirmed_profile_root_invalid')
            prefix = f'/{root}/'
        else:
            raise ContractError('confirmed_profile_root_not_allowed')
        for key, after in values.items():
            if not isinstance(key, str) or not key:
                raise ContractError('confirmed_profile_key_invalid')
            existed = key in record
            before = copy.deepcopy(record.get(key))
            if not existed or before != after:
                changes.append({'source': prefix + key.replace('~', '~0').replace('/', '~1'),
                                'before_exists': existed, 'before': before, 'after': copy.deepcopy(after)})
                record[key] = copy.deepcopy(after)
    return output, changes


def _source_value(profile, source):
    try:
        return ('value', json_value(profile, source))
    except ContractError as exc:
        return ('unavailable', str(exc))


def sync_confirmed_facts(values, next_nodes, folder, patch, *, writer_lock_held=False):
    command = values.get('command') or {}
    journal_path = folder / 'writer' / (str(command.get('command_id')) + '.json')
    settled_observation = False
    if command.get('kind') == 'observe' and command.get('target') and journal_path.is_file():
        journal = json.loads(journal_path.read_text(encoding='utf-8'))
        receipt = journal.get('receipt') or {}
        observed = receipt.get('snapshot') or {}
        settled_observation = (journal.get('kind') == 'observe'
            and journal.get('command_id') == command.get('command_id')
            and receipt.get('command_id') == command.get('command_id')
            and receipt.get('kind') == 'observe' and receipt.get('settled') is True
            and receipt.get('status') == 'completed'
            and receipt.get('target') == command['target']
            and observed.get('target') == command['target']
            and observed.get('module_id') == command.get('module_id')
            and observed.get('module_selector') == command.get('module_selector'))
    pending_observe = (tuple(next_nodes) == ('observe',)
        and values.get('status') == 'awaiting_observation' and command.get('kind') == 'observe'
        and command.get('command_id')
        and (not journal_path.exists() or settled_observation))
    stopped = (not next_nodes and not command and values.get('status') in {
        'incomplete_coverage', 'budget_exhausted', 'module_blocked', 'scope_review_blocked'})
    pure_review_boundary = (tuple(next_nodes) == ('review_boundary',) and not command
                            and values.get('status') == 'module_filled')
    if not writer_lock_held or not (pending_observe or stopped or pure_review_boundary):
        raise ContractError('confirmed_profile_sync_requires_quiescent_writer')
    assert_settled_history(values, folder)
    profile, changes = apply_confirmed_facts(values['profile'], patch)
    modules = {m['id']: m for m in values['manifest']['modules']}
    for mid, result in values.get('results', {}).items():
        unknown = {r['id'] for r in (result.get('last_receipt') or {}).get('results', [])
                   if r.get('status') == 'unknown'}
        saved = values.get('scopes', {}).get(modules.get(mid, {}).get('save_scope')) == 'saved_confirmed'
        if saved:
            for mapping in (result.get('proposal') or {}).get('mappings', []):
                source = mapping.get('source')
                if source and _source_value(values['profile'], source) != _source_value(profile, source):
                    raise ContractError('confirmed_profile_updates_require_saved_scope_revision')
        for operation in (result.get('command') or {}).get('operations', []):
            source = operation.get('source')
            if source and (saved or operation.get('id') in unknown):
                if _source_value(values['profile'], source) != _source_value(profile, source):
                    raise ContractError('confirmed_profile_updates_require_prior_write_reconciliation')
    reviews = {scope: copy.deepcopy(review) for scope, review in values.get('scope_reviews', {}).items()
               if values.get('scopes', {}).get(scope) == 'saved_confirmed'}
    results = copy.deepcopy(values.get('results', {}))
    if changes:
        for mid, result in results.items():
            if values.get('scopes', {}).get(modules.get(mid, {}).get('save_scope')) != 'saved_confirmed':
                result['review'] = {}
                result['reviewed_revision'] = None
                if result.get('status') == 'verified_draft':
                    result['status'] = 'filled_pending_review'
    history = copy.deepcopy(values.get('inventory_history', []))
    history.append({'kind': 'confirmed_profile_update', 'at': time.time(),
                    'basis': patch['basis'], 'changes': changes})
    return {'profile': profile, 'mapping_cache': {}, 'scope_reviews': reviews, 'results': results,
            'inventory_history': history}
