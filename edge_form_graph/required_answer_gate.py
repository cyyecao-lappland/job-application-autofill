"""Identify completed semantic decisions that make a required answer unavailable."""


def unanswerable_required_fields(result):
    snapshot = result.get('current') or result.get('snapshot') or {}
    fields = {field['id']: field for field in snapshot.get('fields', [])}
    rejected_enums = {decision['field_id']
                      for entry in result.get('enum_history', []) if not entry.get('error')
                      for decision in entry.get('review', {}).get('decisions', [])
                      if decision.get('option') is None}
    unavailable = []
    for fid, item in result.get('results', {}).items():
        field = fields.get(fid, {})
        reason = str(item.get('reason', ''))
        if (field.get('required') is not True or field.get('value_present') is True
                or item.get('status') != 'deferred'):
            continue
        if reason.startswith(('missing:', 'conflict:')):
            unavailable.append({'field_id': fid, 'label': field.get('label', ''), 'reason': reason})
        elif fid in rejected_enums and reason == 'option_missing_or_ambiguous':
            unavailable.append({'field_id': fid, 'label': field.get('label', ''),
                                'reason': 'no_equivalent_option_after_semantic_review'})
    return unavailable
