"""Trusted synthesis of an opaque operation. Never load the identity value."""
import copy
import json
from pathlib import Path
import re

SOURCE = '/identity/identity_document_number'
SECRET_REF = {'kind': 'identity_document_number', 'source': SOURCE}
LOCAL_CONFIG = Path(__file__).resolve().parent.parent / 'private' / 'local-config.json'


def standing_authorized():
    try:
        config = json.loads(LOCAL_CONFIG.read_text(encoding='utf-8-sig'))
        return (config.get('identity_document_fill') is True
                and isinstance(config.get('profile'), str) and Path(config['profile']).is_absolute())
    except (OSError, ValueError):
        return False


def synthesize(snapshot, operations, deferred):
    if not standing_authorized():
        return operations, deferred
    candidates = [f for f in snapshot['fields'] if f.get('protected') is True
                  and f.get('kind') == 'text'
                  and re.sub(r'[\s*：:]', '', f.get('label', '')) in {'身份证号', '身份证号码', '证件号码'}]
    if len(candidates) != 1:
        return operations, deferred
    field = candidates[0]
    if field.get('disabled') or not field.get('selector') or not any(d['field_id'] == field['id'] for d in deferred):
        return operations, deferred
    safe_field = copy.deepcopy(field)
    safe_field['value'] = None
    op = {'id': field['id'], 'source': SOURCE, 'transform': 'identity', 'value': None,
          'secret_ref': copy.deepcopy(SECRET_REF), 'field': safe_field, 'depends_on': []}
    # Ordinary controls retain their order/focus. Insert early only for a dependency.
    index = next((i for i, ordinary in enumerate(operations) if field['id'] in ordinary['depends_on']), len(operations))
    ordered = operations[:index]+[op]+operations[index:]
    return ordered, [d for d in deferred if d['field_id'] != field['id']]
