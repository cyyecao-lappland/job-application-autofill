"""Program-owned execution policy. Fallbacks are drafts, never personal facts."""
import copy

from .contracts import ContractError, SENSITIVE, field_writable, page_matches

FAILURE_TEXT = '识别失败！需人工填写！'
FIRST_OPTION = '__FIRST_ENABLED_OPTION__'


def tuning_enabled(value):
    if type(value) is not bool:
        raise ContractError('agent_tuning_requires_boolean')
    return value


def fallback_operations(snapshot, results, attempted):
    operations = []
    for field in snapshot['fields']:
        fid = field['id']
        result = results.get(fid, {})
        if (fid in attempted or result.get('status') in {'written', 'already_matched', 'verification_skipped', 'unknown', 'conflict'}
                or field.get('protected') or SENSITIVE.search(field.get('label', ''))
                or not field_writable(field)):
            continue
        kind = field['kind']
        if field.get('control_pattern') == 'next_range_date':
            continue  # A calendar is not an option list; send it to control repair.
        if kind == 'text' and field.get('component') not in {'ant-date', 'ant-month', 'element-date', 'element-date-now'}:
            if field.get('max_length') and field['max_length'] < len(FAILURE_TEXT):
                continue  # Do not silently truncate the manual-review marker.
            value, mode = FAILURE_TEXT, 'text_marker'
        elif kind in {'select', 'combobox'}:
            value, mode = FIRST_OPTION, 'first_option'
        else:
            continue
        operations.append({'id': fid, 'field': copy.deepcopy(field), 'source': None,
                           'transform': 'draft_fallback', 'depends_on': [], 'value': value,
                           'fallback': mode})
    return operations


def execution_review(state):
    """Check actual execution, including explicitly marked placeholders; no semantic claim."""
    fields = state['current']['fields']
    operations = state.get('operations', [])
    completed = [op for op in operations if state['results'].get(op['id'], {}).get('status')
                 in {'written', 'already_matched', 'verification_skipped'}]
    covered = {op['id'] for op in completed}
    issues = []
    if not page_matches(completed, state['current']):
        issues.append('program_readback_mismatch')
    for field in fields:
        if field.get('disabled') and field.get('value') not in (None, '', []) and field.get('value_readable') is not False:
            continue  # Site-owned computed value: present, not a personal fact learned by us.
        if field['id'] not in covered and not field.get('verification_skipped') and not (field.get('protected') and
                (field.get('value_present') or field.get('secret_match'))):
            if field.get('required'):
                issues.append('unresolved_required_field:' + field['id'])
    return {'approved': not issues, 'checked_field_ids': [f['id'] for f in fields], 'issues': issues}
