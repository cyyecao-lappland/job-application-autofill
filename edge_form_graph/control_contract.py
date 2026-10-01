"""Typed control targets and compatibility conversion, independent of orchestration."""
from datetime import date

from .contracts import ContractError, json_value
from .control_registry import declaration


def date_target(value):
    if not isinstance(value, str):
        raise ContractError('explicit_date_required')
    parts = value.split('-')
    try:
        if len(parts) not in (1, 2, 3) or [len(p) for p in parts] != [4, 2, 2][:len(parts)]:
            raise ValueError()
        values = list(map(int, parts))
        date(*(values + [1] * (3-len(values))))
    except ValueError:
        raise ContractError('invalid_date') from None
    return dict(precision=('year', 'month', 'day')[len(parts)-1], **dict(zip(('year', 'month', 'day'), values)))


def compile_control_target(profile, source, adapter):
    codec = declaration(adapter)['legacyCodec']
    value = json_value(profile, source)
    if codec == 'choice':
        if not isinstance(value, str) or not value.strip():
            raise ContractError('choice_source_required')
        return {'kind': 'choice', 'cardinality': 'single', 'choice': {'label': value}}
    if codec == 'text':
        if isinstance(value, list) and all(isinstance(v, str) for v in value):
            value = '、'.join(value)
        if not isinstance(value, str):
            raise ContractError('text_source_required')
        return {'kind': 'text', 'text': value}
    if codec in ('province_city', 'administrative_region'):
        if codec == 'province_city' and isinstance(value,str) and value.removesuffix('市') in {'上海','北京','天津','重庆'}:
            municipality=value.removesuffix('市')+'市'
            value={'province':municipality,'city':municipality}
        if isinstance(value, dict):
            levels = ('province', 'city') if codec == 'province_city' else ('province', 'city', 'district')
            if any(not isinstance(value.get(k), str) or not value[k].strip() for k in levels):
                raise ContractError('explicit_location_source_required')
            return {'kind': 'hierarchy', 'path': [{'level': k, 'label': value[k]} for k in levels]}
        if codec == 'administrative_region' and isinstance(value, str) and value.strip():
            # Historical full-path text is retained explicitly, never guessed into levels.
            return {'kind': 'hierarchy', 'path': [{'level': 'full_path', 'label': value}]}
        raise ContractError('explicit_location_source_required')
    if codec == 'date':
        return {'kind': 'date', 'value': date_target(value)}
    if codec == 'date_range':
        if not source.endswith(('/start_date', '/end_date')):
            raise ContractError('date_range_source_required')
        prefix = source.rsplit('/', 1)[0]
        start = date_target(json_value(profile, prefix+'/start_date'))
        try:
            end = {'kind': 'date', 'value': date_target(json_value(profile, prefix+'/end_date'))}
        except ContractError as exc:
            if str(exc) not in ('source_not_found', 'source_is_not_an_answer'):
                raise
            if json_value(profile, prefix+'/is_current') is not True:
                raise ContractError('date_range_end_unknown') from None
            end = {'kind': 'current'}
        return {'kind': 'date_range', 'start': start, 'end': end}
    raise ContractError('unsupported_target_codec')


def validate_control_outcome(command, outcome, settled):
    if not isinstance(outcome, dict) or outcome.get('schema') != 'control-receipt/v1':
        raise ContractError('invalid_control_outcome')
    if outcome.get('operationId') != command['command_id'] or outcome.get('adapter') != command['adapter']:
        raise ContractError('wrong_control_outcome')
    if outcome.get('persistence') != 'not_assessed' or outcome.get('verification') not in {'match', 'skipped', 'mismatch'}:
        raise ContractError('invalid_control_verification')
    if not settled or outcome.get('call') != 'finished':
        raise ContractError('unfinished_control_outcome')
    if outcome['verification'] == 'match' and outcome.get('committed') is not True:
        raise ContractError('match_requires_committed_readback')
    if outcome['verification'] == 'skipped' and (outcome.get('committed') is not False or not outcome.get('reason')):
        raise ContractError('skipped_is_not_verified')
    return outcome
