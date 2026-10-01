"""Per-branch depth, cycle detection and retained writes for dynamic forms."""
import copy
import json

MAX_LINKAGE_DEPTH = 15


def update_linkage(previous, before, current, event):
    state = copy.deepcopy(previous or {'depths': {}, 'seen': {}, 'blocked': {}})
    for field in before['fields']:
        state['depths'].setdefault(field['id'], 1)
    parent = event.get('trigger_id')
    depth = state['depths'].get(parent, 1)+1
    fields = {f['id']: f for f in current['fields']}
    for fid in event.get('added', [])+event.get('changed', []):
        if fid not in fields:
            continue
        field = fields[fid]
        key = json.dumps([field.get('value'), field.get('disabled'), field.get('readonly'),
                          [(o.get('label'), bool(o.get('disabled'))) for o in field.get('options', [])]],
                         ensure_ascii=False, sort_keys=True)
        seen = state['seen'].setdefault(fid, [])
        if key in seen:
            state['blocked'][fid] = 'linkage_cycle'
        elif depth > MAX_LINKAGE_DEPTH:
            state['blocked'][fid] = 'linkage_depth_limit'
        else:
            state['depths'][fid] = depth
            seen.append(key)
    return state


def retained_writes(operations, results, current):
    fields = {f['id']: f for f in current['fields']}
    from .verification import incomplete
    kept = []
    for op in operations:
        f = fields.get(op['id'])
        if (f and results.get(op['id'], {}).get('status') == 'verification_skipped'
                and f.get('signature') == op['field'].get('signature') and incomplete(f)):
            kept.append({**copy.deepcopy(op), 'field': copy.deepcopy(f), 'verification': 'skipped'})
            continue
        if (not f or results.get(op['id'], {}).get('status') not in {'written', 'already_matched'}
                or f.get('signature') != op['field'].get('signature')
                or f.get('value') != op.get('value') or f.get('value_readable') is False
                or f.get('expanded') == 'true'):
            continue
        kept.append({**copy.deepcopy(op), 'field': copy.deepcopy(f)})
    return kept


def retain_plan_writes(profile, operations, deferred, current, retained, results):
    """Carry completed linkage fields through a later semantic plan merge."""
    from .contracts import ContractError, json_value, transform
    from .enum_repair import carry_reviewed_enums
    valid = []
    for prior in retained_writes(retained, results, current):
        if not prior.get('fallback'):
            try:
                compiled = {**copy.deepcopy(prior), 'value': transform(json_value(profile, prior['source']), prior['transform'])}
                compiled.pop('enum_provenance', None)
                compiled = carry_reviewed_enums(profile, [compiled], [prior])[0]
                if compiled['value'] != prior['value']:
                    continue
            except (ContractError, KeyError):
                continue
        valid.append(prior)
    mapped = {op['id']: op for op in operations}
    operations += [op for op in valid if op['id'] not in mapped]
    carried = {op['id'] for op in valid if op['id'] not in mapped or all(
        mapped[op['id']].get(key) == op.get(key) for key in ('source', 'transform', 'value'))}
    return operations, [item for item in deferred if item['field_id'] not in carried]
