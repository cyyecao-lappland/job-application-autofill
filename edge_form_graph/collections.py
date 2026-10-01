"""Deterministic collection reconciliation before repeated-form execution."""
from __future__ import annotations

import unicodedata

from .contracts import ContractError, SENSITIVE


def _text(value):
    if value is None:
        return ''
    return ''.join(str(value).split()).casefold()


def _record_key(record, identity_fields):
    values = tuple(_text(record.get(field)) for field in identity_fields)
    return values if all(values) else None


def _collection_value(profile, pointer):
    """Resolve a collection pointer without treating record objects as answers.

    ``contracts.json_value`` intentionally accepts only leaf answer values.  A
    collection planner needs the surrounding list of record objects, so it
    uses the same protected JSON-pointer traversal but validates the result as
    a collection instead of weakening the answer contract globally.
    """
    if not isinstance(profile, dict) or not isinstance(pointer, str) or not pointer.startswith('/') or SENSITIVE.search(pointer):
        raise ContractError('invalid_or_protected_source')
    value = profile
    try:
        for token in pointer[1:].split('/'):
            token = token.replace('~1', '/').replace('~0', '~')
            value = value[int(token)] if isinstance(value, list) and token.isdigit() else value[token]
    except (KeyError, IndexError, TypeError, ValueError):
        raise ContractError('source_not_found') from None
    if not isinstance(value, list):
        raise ContractError('invalid_collection_plan')
    return value


def _included_records(profile, collection):
    records = profile.get(collection, []) if isinstance(profile, dict) else []
    if not isinstance(records, list):
        raise ContractError('invalid_collection_plan')
    result = []
    for record in records:
        if not isinstance(record, dict) or not isinstance(record.get('record_id'), str):
            raise ContractError('collection_record_id_required')
        if record.get('autofill_policy', {}).get('include_by_default') is False:
            continue
        result.append(record)
    return result


def plan_experience_placement(profile, available_sections):
    """Choose canonical records for the experience sections actually on the page.

    Projects remain projects whenever the page exposes a project section.  When
    it does not, a project may enter the internship/work section only through an
    explicit per-record user fallback policy.  The returned source collection
    remains ``/projects`` so no employment relationship is fabricated and field
    values continue to resolve from the original canonical record.
    """
    if not isinstance(available_sections, (list, tuple, set)) or not all(
            isinstance(item, str) and item.strip() for item in available_sections):
        raise ContractError('invalid_experience_sections')
    sections = {item.strip().casefold() for item in available_sections}
    project_available = bool(sections & {'project', 'projects'})
    employment_targets = sections & {'internship', 'internships', 'employment', 'work'}
    placements = []

    if employment_targets:
        target = ('internships' if sections & {'internship', 'internships'} else 'employment')
        for record in _included_records(profile, 'employment'):
            placements.append({
                'target_section': target,
                'source_collection': '/employment',
                'record_id': record['record_id'],
                'module_type': 'employment',
                'presentation_kind': 'employment',
                'start_date': record.get('start_date'),
            })

        if not project_available:
            for record in _included_records(profile, 'projects'):
                policy = record.get('presentation_policy')
                if not isinstance(policy, dict) or policy.get(
                        'user_requested_fallback') != '无项目经历栏目时按实习填写':
                    continue
                placements.append({
                    'target_section': target,
                    'source_collection': '/projects',
                    'record_id': record['record_id'],
                    # Keep durable mappings separate from true employment fields:
                    # 公司名称 may bind to project.organization here, while it
                    # normally binds to employment.company.
                    'module_type': 'internship_project_fallback',
                    'presentation_kind': 'project_practice_as_internship',
                    'start_date': record.get('start_date'),
                })

    if project_available:
        for record in _included_records(profile, 'projects'):
            placements.append({
                'target_section': 'projects',
                'source_collection': '/projects',
                'record_id': record['record_id'],
                'module_type': 'projects',
                'presentation_kind': 'project',
                'start_date': record.get('start_date'),
            })

    # Newest first gives deterministic page order without relying on JSON array
    # position. Missing dates remain last and record_id makes ties stable.
    placements.sort(key=lambda item: (item.get('start_date') or '', item['record_id']), reverse=True)
    for item in placements:
        item.pop('start_date', None)
    return placements


def plan_collection(profile, collection_pointer, page_records, identity_fields):
    """Bind exact existing cards and plan one add per missing canonical record.

    Page records contain only host-observed semantic facts and a stable page_id
    for the current read.  Missing or duplicate identities stop that record;
    this function never fuzzy-matches, deletes cards or invents array indexes.
    """
    collection = _collection_value(profile, collection_pointer)
    if not isinstance(collection, list) or not identity_fields or not all(
            isinstance(field, str) and field for field in identity_fields):
        raise ContractError('invalid_collection_plan')
    if not isinstance(page_records, list) or not all(isinstance(item, dict) for item in page_records):
        raise ContractError('invalid_page_records')

    canonical = []
    for record in collection:
        if not isinstance(record, dict) or not isinstance(record.get('record_id'), str):
            raise ContractError('collection_record_id_required')
        if record.get('autofill_policy', {}).get('include_by_default') is False:
            continue
        canonical.append(record)

    page_by_key = {}
    deferred = []
    for item in page_records:
        page_id = item.get('page_id')
        facts = item.get('facts')
        if not isinstance(page_id, str) or not page_id or not isinstance(facts, dict):
            raise ContractError('page_record_identity_required')
        key = _record_key(facts, identity_fields)
        if key is None:
            deferred.append({'page_id': page_id, 'reason': 'page_identity_incomplete'})
            continue
        page_by_key.setdefault(key, []).append(page_id)

    actions = []
    seen_record_ids = set()
    canonical_keys = set()
    for record in canonical:
        rid = record['record_id']
        if rid in seen_record_ids:
            raise ContractError('duplicate_record_id')
        seen_record_ids.add(rid)
        key = _record_key(record, identity_fields)
        if key is None:
            deferred.append({'record_id': rid, 'reason': 'canonical_identity_incomplete'})
            continue
        if key in canonical_keys:
            deferred.append({'record_id': rid, 'reason': 'canonical_identity_duplicate'})
            continue
        canonical_keys.add(key)
        matches = page_by_key.get(key, [])
        if len(matches) == 1:
            actions.append({'action': 'bind_existing', 'record_id': rid, 'page_id': matches[0]})
        elif len(matches) == 0:
            actions.append({'action': 'add_missing', 'record_id': rid})
        else:
            deferred.append({'record_id': rid, 'reason': 'page_identity_duplicate',
                             'page_ids': sorted(matches)})

    extras = sorted(page_id for key, ids in page_by_key.items() if key not in canonical_keys for page_id in ids)
    return {'collection': collection_pointer, 'identity_fields': list(identity_fields),
            'actions': actions, 'deferred': deferred, 'preserve_unmatched_page_ids': extras,
            'canonical_count': len(canonical), 'page_count': len(page_records)}
