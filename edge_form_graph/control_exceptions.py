"""Value-free control diagnosis and closed decisions, with no execution or learning.

An adapter decision is a proposal. The caller must still bind a trusted source,
check fresh executor preconditions, and verify the write before persisting it.
"""
from __future__ import annotations

import re

from .contracts import ContractError, KINDS, SENSITIVE, closed


from .control_registry import adapter_names
ALLOWED_ADAPTERS = adapter_names()
METHOD_ERRORS = frozenset({
    'unsupported_control', 'unsupported_control_method', 'unknown_control_method',
    'control_method_not_supported', 'control_handler_not_verified',
    'no_control_adapter', 'unknown_dialog_adapter',
    'text_probe_discovered_composite_control',
    'date_year_navigation_not_unique',
})
_SUCCESS = {'written', 'already_matched', 'verified', 'completed'}
_TOKEN = re.compile(r'^[A-Za-z][A-Za-z0-9_-]{0,79}$')
_STRUCTURE_ENUMS = {
    'tag': {'INPUT', 'TEXTAREA', 'SELECT', 'BUTTON', 'DIV', 'SPAN'},
    'type': {'text', 'email', 'tel', 'url', 'search', 'number', 'date', 'month',
             'datetime-local', 'checkbox', 'radio', 'file'},
    'role': {'combobox', 'textbox', 'listbox', 'radiogroup', 'checkbox', 'radio',
             'button', 'spinbutton', 'grid', 'dialog', 'tree'},
}

CONTROL_EXCEPTION_SCHEMA = {
    'type': 'object', 'additionalProperties': False, 'required': ['decisions'],
    'properties': {'decisions': {'type': 'array', 'items': {
        'type': 'object', 'additionalProperties': False,
        'required': ['field_id', 'action', 'adapter'],
        'properties': {
            'field_id': {'type': 'string'},
            'action': {'type': 'string', 'enum': ['use_adapter', 'needs_implementation']},
            'adapter': {'type': ['string', 'null']},
        },
    }}},
}
CONTROL_EXCEPTION_INSTRUCTIONS = (
    'Diagnose control interaction only. Field matching and source selection are separate. '
    'Use only an allowed existing adapter whose control structure matches the evidence. '
    'Otherwise choose needs_implementation with adapter null for the development agent. '
    'Never return source paths, applicant values, selectors, scripts, or executable code. '
    'Return exactly one decision for each supplied field_id. This is not write authorization.'
)


def _token(value):
    return value if isinstance(value, str) and _TOKEN.fullmatch(value) else None


def _structure(field):
    signature = field.get('signature')
    signature = signature if isinstance(signature, dict) else {}
    supplied = field.get('structure')
    supplied = supplied if isinstance(supplied, dict) else {}
    structure = {}
    for key, allowed in _STRUCTURE_ENUMS.items():
        value = supplied.get(key, signature.get(key, field.get(key)))
        if key == 'tag' and isinstance(value, str):
            value = value.upper()
        if isinstance(value, str) and value in allowed:
            structure[key] = value
    for key in ('multiple', 'range', 'paired_inputs'):
        value = supplied.get(key, field.get(key))
        if isinstance(value, bool):
            structure[key] = value
    return structure


def _compact_field(field):
    search = field.get('search_evidence')
    search = search if isinstance(search, dict) else {}
    evidence = {key: search[key] for key in ('editable', 'selection_structure')
                if isinstance(search.get(key), bool)}
    if isinstance(search.get('autocomplete'), str) and search['autocomplete'] in {'none', 'list', 'inline', 'both'}:
        evidence['autocomplete'] = search['autocomplete']
    return {
        'field_id': field.get('field_id', field.get('id')),
        'label': field.get('label') if isinstance(field.get('label'), str) else '',
        'kind': _token(field.get('kind')),
        'component': _token(field.get('component')),
        'readonly': field.get('readonly', field.get('readOnly')) is True,
        'disabled': field.get('disabled') is True,
        'search_evidence': evidence,
        'control_status': _token(field.get('control_status')),
        'selection_mode': _token(field.get('selection_mode')),
        'control_pattern': _token(field.get('control_pattern')),
        'structure': _structure(field),
    }


def control_method_key(field):
    """Comparable structural key; labels, identity and answers do not affect it.

    Accepts either a snapshot field or the compact request field. No digest is
    needed. Keep every field binding when grouping requests using this key.
    """
    compact = _compact_field(field)
    return tuple((key, tuple(sorted(value.items())) if isinstance(value, dict) else value)
                 for key, value in compact.items()
                 if key not in {'field_id', 'label', 'control_status'})


def _result_index(results):
    if results is None:
        return {}
    if isinstance(results, dict):
        # Accept both graph result maps and executor receipts.
        if isinstance(results.get('results'), list):
            results = results['results']
        else:
            return {key: value for key, value in results.items() if isinstance(value, dict)}
    if not isinstance(results, list):
        raise ContractError('invalid_control_exception_results')
    return {item.get('field_id', item.get('id')): item for item in results
            if isinstance(item, dict)}


def build_control_exception_request(snapshot, results=None):
    """Report unsupported controls or explicit method errors, never missing facts.

    Generic comboboxes and unverified text candidates already have program paths.
    A Next range date must carry control_pattern or agent_required evidence.
    Raw executor error messages are never forwarded: they can contain values.
    """
    if not isinstance(snapshot, dict) or not isinstance(snapshot.get('fields'), list):
        raise ContractError('invalid_control_exception_snapshot')
    index = _result_index(results)
    fields, seen = [], set()
    for field in snapshot['fields']:
        if not isinstance(field, dict):
            continue
        fid = field.get('id', field.get('field_id'))
        signature = field.get('signature')
        signature = signature if isinstance(signature, dict) else {}
        sensitive_text = ' '.join(str(value or '') for value in
                                  (fid, field.get('label'), signature.get('name'), signature.get('type')))
        if not isinstance(fid, str) or not fid or SENSITIVE.search(sensitive_text):
            continue
        result = index.get(fid, {})
        if result.get('status') in _SUCCESS:
            continue
        unsupported = (field.get('kind') not in KINDS
                       or field.get('control_status') in {'agent_required', 'unsupported'}
                       or field.get('control_pattern') == 'next_range_date')
        method_error = result.get('reason') in METHOD_ERRORS
        if not (unsupported or method_error):
            continue
        compact = _compact_field(field)
        # Duplicate discovery rows are collapsed; separate controls with the same
        # structure retain their field IDs for fresh binding and verification.
        identity = (fid, control_method_key(compact))
        if identity not in seen:
            seen.add(identity)
            fields.append(compact)
    return {'fields': fields, 'allowed_adapters': sorted(ALLOWED_ADAPTERS)}


def validate_control_exception_decisions(request, decisions):
    """Return closed proposals; unknown adapters become needs_implementation.

    The request's allowed_adapters cannot widen the local whitelist. A complete,
    unique response is required, and arbitrary model payload keys are rejected.
    """
    if isinstance(decisions, dict):
        closed(decisions, {'decisions'})
        decisions = decisions['decisions']
    if not isinstance(decisions, list):
        raise ContractError('invalid_control_exception_decisions')
    requested = {field['field_id'] for field in request['fields']}
    seen, approved = set(), []
    for decision in decisions:
        closed(decision, {'field_id', 'action', 'adapter'}, {'field_id', 'action'})
        fid, action, adapter = decision['field_id'], decision['action'], decision.get('adapter')
        if not isinstance(fid, str) or fid not in requested or fid in seen:
            raise ContractError('unknown_or_duplicate_control_field')
        if not isinstance(action, str) or action not in {'use_adapter', 'needs_implementation'}:
            raise ContractError('invalid_control_exception_action')
        if adapter is not None and (not isinstance(adapter, str) or not adapter.strip()):
            raise ContractError('invalid_control_exception_adapter')
        if action == 'use_adapter' and adapter in ALLOWED_ADAPTERS:
            approved.append({'field_id': fid, 'action': action, 'adapter': adapter})
        else:
            approved.append({'field_id': fid, 'action': 'needs_implementation', 'adapter': None})
        seen.add(fid)
    if seen != requested:
        raise ContractError('missing_control_exception_decisions')
    return approved
