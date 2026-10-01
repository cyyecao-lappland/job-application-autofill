"""Read-before-retry policy for absolute-value form operations, never save replay."""
import copy
from .contracts import ContractError, validate_snapshot, redacted_profile, file_matches


def never_dispatched(result):
    """A failed initial mapping has no browser write to settle or replay."""
    return (result.get('status') == 'nothing_filled'
            and result.get('command') is None and not result.get('last_receipt')
            and result.get('revision') == 0
            and result.get('metrics', {}).get('batch_count') == 0
            and result.get('metrics', {}).get('written') == 0
            and bool(result.get('trace'))
            and all(event.get('node') in {'map', 'validate', 'prepare_fill',
                    'resolve_unknown', 'review_enums', 'verify'} for event in result['trace'])
            and bool(result.get('results'))
            and all(r.get('status') == 'deferred' for r in result['results'].values()))


def repair_eligible(result):
    if never_dispatched(result):
        return True
    # A fresh arbitrary-start run can reach independent review using only
    # values already present on the page. If that review rejects the semantic
    # bindings, permit source corrections even though no fill receipt exists;
    # there is no browser write to settle or replay.
    if (result.get('status') == 'review_rejected' and result.get('command') is None
            and not result.get('last_receipt') and result.get('revision') == 0
            and result.get('metrics', {}).get('batch_count') == 0):
        return True
    receipt = result.get('last_receipt') or {}
    command = result.get('command') or {}
    command_terminal = (result.get('command') is None or (
        result.get('status') == 'readback_mismatch'
        and command.get('kind') == 'fill'
        and receipt.get('command_id') == command.get('command_id')))
    return (result.get('status') in {'required_field_missing', 'nothing_filled', 'partial_draft', 'review_rejected',
                                     'readback_mismatch'}
            and command_terminal and receipt.get('kind') == 'fill'
            and receipt.get('settled') is True and receipt.get('status') in {'completed', 'partial'}
            and not any(r.get('status') in {'unknown', 'conflict'} for r in receipt.get('results', []))
            and not any(r.get('status') in {'unknown', 'conflict'} for r in result.get('results', {}).values()))

def eligibility(result):
    receipt = result.get('last_receipt')
    if result.get('command'):
        if not receipt or receipt.get('settled') is not True:
            return 'transport_settlement_required'
        if result['command'].get('kind') != 'fill':
            return 'not_a_fill_recovery'
    transient=any(r.get('reason') in {'popup_not_ready','operation_budget','popup_not_unique_or_not_loaded'}
                  for r in result.get('results',{}).values())
    if result.get('status') in {'plan_rejected','review_rejected'} or (result.get('status')=='required_field_missing' and not transient):
        return 'input_or_review_correction_required'
    return None

def align_logical_fields(old, fresh):
    """Rebind changing DOM selectors to stable field identities during recovery."""
    aligned = copy.deepcopy(fresh)
    before = {f['id']: f for f in old['fields']}
    after = {f['id']: f for f in aligned['fields']}
    missing_old = [f for fid, f in before.items() if fid not in after]
    missing_new = [f for fid, f in after.items() if fid not in before]
    if not missing_old and not missing_new:
        return aligned
    if len(missing_old) != len(missing_new):
        raise ContractError('recovery_structure_changed')
    used = set()
    for prior in missing_old:
        candidates = [actual for actual in missing_new if id(actual) not in used
                      and actual.get('label') == prior.get('label')
                      and actual.get('kind') == prior.get('kind')
                      and actual.get('protected') == prior.get('protected')
                      and actual.get('signature') == prior.get('signature')]
        if len(candidates) != 1:
            raise ContractError('recovery_structure_changed')
        actual = candidates[0]
        used.add(id(actual))
        actual['_logical_rebound'] = True
        actual['id'] = prior['id']
    if len(used) != len(missing_new):
        raise ContractError('recovery_structure_changed')
    return aligned

def compare_values(result, fresh):
    """Read-only classification. Matching is page evidence, not remote settlement."""
    validate_snapshot(fresh)
    old = result.get('current') or result['snapshot']
    for key in ('target', 'module_id', 'module_selector'):
        if old[key] != fresh[key]:
            raise ContractError('recovery_target_changed')
    fresh = align_logical_fields(old, fresh)
    before = {f['id']:f for f in old['fields']}
    after = {f['id']:f for f in fresh['fields']}
    if set(before) != set(after):
        raise ContractError('recovery_structure_changed')
    planned = {o['id']:o for o in result.get('operations', [])}
    comparisons = []
    def equal(a, b):
        if isinstance(a, list) and isinstance(b, list):
            return sorted(a) == sorted(b)
        return type(a) is type(b) and a == b
    for fid, prior in before.items():
        actual = after[fid]
        for key in ('signature', 'selector', 'kind', 'protected'):
            if prior.get(key) != actual.get(key):
                if key == 'selector' and actual.get('_logical_rebound'):
                    continue
                if key == 'kind' and prior.get('kind') == 'unsupported' and actual.get('kind') == 'text':
                    signature = actual.get('signature', {})
                    if (actual.get('component') in {'element-date','element-date-now'} and not actual.get('disabled')
                            and not actual.get('protected') and signature.get('type') == 'text'):
                        continue  # Same observed editable date, newly supported adapter.
                    if (actual.get('component') is None and not actual.get('disabled')
                            and signature.get('tag') == 'INPUT' and signature.get('type') == 'text'
                            and not signature.get('role')):
                        # Same plain input. aria-autocomplete="none" used to be
                        # misclassified as an unsupported custom control.
                        continue
                raise ContractError('recovery_field_identity_changed')
        op = planned.get(fid)
        if actual.get('value_readable') is False or actual.get('expanded') in (True, 'true'):
            status = 'unreadable'
        elif op and op.get('field', {}).get('kind') == 'file':
            status = ('matched' if file_matches(actual, op['value']) else
                      'unchanged' if equal(actual['value'], prior['value']) or
                      prior.get('value_readable') is False else 'unreadable')
        elif prior.get('value_readable') is False or prior.get('expanded') in (True, 'true'):
            # The old observation was a search/display state, not a committed
            # value. A later closed/readable control is fresh authoritative
            # evidence; preserve it for review instead of calling it a user edit.
            status = 'matched' if op and equal(actual['value'], op['value']) else 'unchanged'
        elif actual.get('protected'):
            status = ('matched' if op and op.get('secret_ref') and actual.get('secret_match') is True
                      else 'unchanged' if not op and actual.get('value_present') == prior.get('value_present')
                      else 'unreadable')
        elif op and equal(actual['value'], op['value']):
            status = 'matched'
        elif equal(actual['value'], prior['value']):
            status = 'unchanged'
        else:
            status = 'conflict'
        comparisons.append({'field_id': fid, 'status': status})
    return comparisons


def reconcile(result, fresh):
    comparisons = compare_values(result, fresh)
    if any(c['status'] == 'conflict' for c in comparisons):
        raise ContractError('recovery_user_value_conflict')
    if any(c['status'] == 'unreadable' for c in comparisons):
        raise ContractError('recovery_value_unreadable')
    matched = [c['field_id'] for c in comparisons if c['status'] == 'matched']
    # An upload creates a remote attachment, unlike an absolute-value edit.
    # Even an unchanged display cannot authorize replay of a dispatched file.
    receipt = result.get('last_receipt') or {}
    dispatched = {r.get('id', r.get('field_id')) for r in receipt.get('results', [])}
    for op in result.get('operations', []):
        if (op.get('field', {}).get('kind') == 'file' and op['id'] not in matched
                and (op['id'] in dispatched or (result.get('command') or {}).get('kind') == 'fill')):
            raise ContractError('upload_outcome_requires_readback')
    old = result.get('current') or result['snapshot']
    fresh = align_logical_fields(old, fresh)
    for field in fresh['fields']:
        field.pop('_logical_rebound', None)
    for key in ('mapping_context', 'module_label', 'record_label', 'used_record_bindings'):
        if key in old:
            if key in fresh and fresh[key] != old[key]:
                raise ContractError('recovery_mapping_context_changed')
            fresh[key] = copy.deepcopy(old[key])
    # Keep the proposal only after recompilation by the child graph. It still
    # covers deferred fields; matched values are skipped by the executor.
    cache = {'status':'mapped','snapshot':copy.deepcopy(fresh),
             'profile':redacted_profile(result['profile']), 'proposal':copy.deepcopy(result['proposal']),
             'enum_operations': [copy.deepcopy(o) for o in result.get('operations', []) if o.get('enum_provenance')],
             'enum_history': copy.deepcopy(result.get('enum_history', [])),
             'enum_rounds': result.get('enum_rounds', 0)}
    return cache, matched


def correct_proposal(profile, snapshot, proposal, corrections):
    """Source-only corrections; no literals, locators or review approvals."""
    from .contracts import closed, compile_plan
    from .semantic import validate_record_sources
    updated = copy.deepcopy(proposal)
    current_ids = {f['id'] for f in snapshot['fields']}
    seen = set()
    for correction in corrections:
        closed(correction, {'field_id', 'source', 'transform'})
        fid = correction['field_id']
        fields = [f for f in snapshot['fields'] if f['id'] == fid and not f.get('protected')]
        if len(fields) != 1 or fid in seen:
            raise ContractError('invalid_correction_field')
        seen.add(fid)
        old = next((m for m in updated['mappings'] if m['field_id'] == fid), {})
        rebound_old_id = None
        if not old:
            rebound = [m for m in updated['mappings']
                       if m['field_id'] not in current_ids
                       and m.get('source') == correction['source']
                       and m.get('transform') == correction['transform']]
            if len(rebound) == 1:
                old = rebound[0]
                rebound_old_id = old['field_id']
        updated['mappings'] = [m for m in updated['mappings']
                               if m['field_id'] not in {fid, rebound_old_id}]
        updated['deferred'] = [d for d in updated['deferred'] if d['field_id'] != fid]
        updated['mappings'].append({**correction, 'depends_on': old.get('depends_on', [])})
    compile_plan(profile, snapshot, updated)
    validate_record_sources(profile, snapshot, updated['mappings'])
    return updated
