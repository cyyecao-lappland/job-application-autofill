"""Validate batched semantic decisions; never execute model code or selectors."""
import copy
from pathlib import Path

from .contracts import ContractError, closed, compile_plan, field_writable, json_value, transform, KINDS


TRANSCRIPT_FILE_LABELS = {'成绩单', '成绩证明'}


def _education_transcript_source_error(profile, snapshot, source):
    """Require education upload sources to belong to the module's bound record."""
    context = snapshot.get('mapping_context') or {}
    if isinstance(source, str) and source.startswith('/attachments/'):
        # Standalone, typed attachment records keep their existing purpose
        # check below. An education editor must use that record's own source.
        return ('transcript_record_binding_mismatch'
                if context.get('record_collection') == '/education' else None)
    if context.get('record_collection') != '/education' or not context.get('record_id'):
        return 'transcript_record_binding_required'
    education = profile.get('education')
    if not isinstance(education, list):
        return 'transcript_record_binding_missing'
    bound_matches = [record for record in education if isinstance(record, dict)
                     and record.get('record_id') == context['record_id']]
    if len(bound_matches) != 1:
        return 'transcript_record_binding_missing'

    source_record_id = None
    source_path = None
    if isinstance(source, str) and source.startswith('record_id:'):
        record_spec, sep, relative = source.partition('/')
        source_record_id = record_spec.removeprefix('record_id:')
        if not sep:
            return 'transcript_source_not_record_path'
        source_path = relative
        source_matches = [record for record in education if isinstance(record, dict)
                          and record.get('record_id') == source_record_id]
        if len(source_matches) != 1:
            return 'transcript_source_record_missing'
        source_record = source_matches[0]
    elif isinstance(source, str) and source.startswith('/education/'):
        parts = source[1:].split('/')
        if len(parts) != 4 or not parts[1].isdigit():
            return 'transcript_source_not_record_path'
        index = int(parts[1])
        if index >= len(education) or not isinstance(education[index], dict):
            return 'transcript_source_record_missing'
        source_record = education[index]
        source_record_id = source_record.get('record_id')
        source_path = '/'.join(parts[2:])
    else:
        return 'transcript_source_not_record_path'

    if source_record_id != context['record_id']:
        return 'transcript_record_binding_mismatch'
    if source_path not in {'transcript/path', 'transcript_usage_policy/path'}:
        return 'transcript_source_not_record_path'

    policy = source_record.get('transcript_usage_policy')
    if source_path == 'transcript_usage_policy/path':
        if (not isinstance(policy, dict) or policy.get('use_for_applications') is not True
                or not isinstance(policy.get('path'), str) or not policy['path']):
            return 'transcript_use_for_applications_not_confirmed'
    elif isinstance(policy, dict):
        # An explicit opt-out wins over the legacy transcript path. When an
        # authorized policy points elsewhere, callers must prefer that path.
        if policy.get('use_for_applications') is False:
            return 'transcript_use_for_applications_denied'
        transcript = source_record.get('transcript')
        transcript_path = transcript.get('path') if isinstance(transcript, dict) else None
        if (policy.get('use_for_applications') is True and policy.get('path')
                and policy.get('path') != transcript_path):
            return 'authorized_transcript_policy_path_preferred'
    return None


def validate_binding(profile, snapshot, binding):
    if binding is None:
        return copy.deepcopy(snapshot)
    closed(binding, {'record_collection', 'record_id', 'reason'})
    path = binding['record_collection']
    if (not isinstance(path, str) or not path.startswith('/') or not isinstance(binding['reason'], str) or not binding['reason'].strip()
            or not isinstance(binding['record_id'], str) or not binding['record_id']):
        raise ContractError('invalid_record_binding')
    value = profile
    try:
        for token in path[1:].split('/'):
            value = value[token.replace('~1', '/').replace('~0', '~')]
    except (KeyError, TypeError):
        raise ContractError('record_collection_missing') from None
    matches = [i for i, record in enumerate(value) if isinstance(record, dict)
               and record.get('record_id') == binding['record_id']] if isinstance(value, list) else []
    if len(matches) != 1:
        raise ContractError('record_binding_not_unique')
    context = snapshot.get('mapping_context', {})
    if context.get('record_id') and (context.get('record_id') != binding['record_id']
                                    or context.get('record_collection') != path):
        raise ContractError('record_binding_conflict')
    result = copy.deepcopy(snapshot)
    result['mapping_context'] = {**context, 'module_type': context.get('module_type', snapshot['module_id']),
        'record_collection': path, 'record_id': binding['record_id']}
    return result


def compile_semantic(profile, snapshot, response):
    closed(response, {'decisions', 'record_binding'})
    if not isinstance(response['decisions'], list):
        raise ContractError('invalid_semantic_decisions')
    bound = validate_binding(profile, snapshot, response['record_binding'])
    proposal = {'mappings': [], 'deferred': []}
    notes = []
    fields={f['id']:f for f in snapshot['fields']}
    for decision in response['decisions']:
        decision = copy.deepcopy(decision)
        closed(decision, {'field_id', 'classification', 'source', 'transform', 'depends_on', 'reason'})
        if not isinstance(decision['reason'], str) or not decision['reason'].strip():
            raise ContractError('semantic_reason_required')
        if isinstance(decision.get('depends_on'), list):
            # A field cannot depend on itself. Structured workers occasionally
            # repeat the target ID while describing a nearby UI relationship;
            # removing that no-op cycle is deterministic and preserves every
            # real cross-field dependency.
            decision['depends_on'] = list(dict.fromkeys(
                dependency for dependency in decision['depends_on'] if dependency != decision['field_id']))
        category = decision['classification']
        if category in {'mapping', 'inference'}:
            field=fields.get(decision['field_id'])
            source=decision.get('source') or ''
            if (field and field.get('label','').strip(' *:：') in {'专业技能证书', '其他资格证书'}
                    and source.startswith('/skills/')):
                decision['classification']='unsupported'
                decision['reason']='skill_name_is_not_a_certification: '+decision['reason']
                proposal['deferred'].append({'field_id':decision['field_id'],'reason':decision['reason']})
                notes.append(copy.deepcopy(decision))
                continue
            if field and field.get('kind')=='file' and field.get('label','').strip(' *:：') in TRANSCRIPT_FILE_LABELS:
                reason = _education_transcript_source_error(profile, bound, source)
                if reason:
                    decision['classification']='unsupported'
                    decision['reason']=reason+': '+decision['reason']
                    proposal['deferred'].append({'field_id':decision['field_id'],'reason':decision['reason']})
                    notes.append(copy.deepcopy(decision))
                    continue
            if field and field.get('kind')=='file' and source.startswith('/attachments/'):
                parts=source.split('/')
                record=profile.get('attachments',[])[int(parts[2])] if len(parts)>3 and parts[2].isdigit() and int(parts[2])<len(profile.get('attachments',[])) else {}
                kind=record.get('kind','')
                label=field.get('label','')
                mismatch=(('简历' in label or 'resume' in label.lower()) and kind!='resume') or (any(word in label for word in ('成绩单','成绩证明')) and kind!='transcript') or ('照' not in label and kind in {'life_photo','portrait','id_photo'})
                if mismatch:
                    decision['classification']='unsupported'
                    decision['reason']='attachment_purpose_mismatch: '+decision['reason']
                    proposal['deferred'].append({'field_id':decision['field_id'],'reason':decision['reason']})
                    notes.append(copy.deepcopy(decision))
                    continue
            if field and (field.get('kind') not in KINDS or not field_writable(field)):
                decision['classification']='unsupported'
                decision['reason']='current_field_not_writable: '+decision['reason']
                proposal['deferred'].append({'field_id':decision['field_id'],'reason':decision['reason']})
                notes.append(copy.deepcopy(decision))
                continue
            try:
                source_value = json_value(profile, decision['source'])
            except ContractError as exc:
                if str(exc) != 'source_is_not_an_answer':
                    raise
                # A structured object is not a fill value. Defer only this
                # answer; keep global field/record/dependency validation below.
                decision['classification'] = 'unsupported'
                decision['reason'] = 'source_is_not_an_answer: ' + decision['reason']
                proposal['deferred'].append({'field_id': decision['field_id'], 'reason': decision['reason']})
                notes.append(copy.deepcopy(decision))
                continue
            # Control execution owns value representation.  Semantic matching
            # only chooses the fact: a boolean bound to an option control has a
            # deterministic 是/否 wire value, even when a closed dropdown did
            # not expose its options during discovery.
            if (field and field.get('kind') in {'select', 'combobox', 'radio_group'}
                    and type(source_value) is bool
                    and decision['transform'] in {'identity', 'string'}):
                decision['transform'] = 'yes_no'
            if field and field.get('date_precision')=='month' and decision['transform'] in {'identity','string'}:
                decision['transform']='year_month'
            try:
                preview_value = transform(source_value, decision['transform'])
            except ContractError as exc:
                # A model can select a real scalar source with a transform for
                # another value shape (for example join_text on a string).
                # Reject only that decision so independent valid mappings in
                # the same module still execute.
                decision['classification'] = 'unsupported'
                decision['reason'] = f'{exc}: ' + decision['reason']
                proposal['deferred'].append({'field_id': decision['field_id'], 'reason': decision['reason']})
                notes.append(copy.deepcopy(decision))
                continue
            field = fields.get(decision['field_id'])
            value_shape_valid = True
            if field:
                if field.get('kind') in {'checkbox', 'radio'}:
                    value_shape_valid = type(preview_value) is bool and (field.get('kind') != 'radio' or preview_value)
                elif field.get('kind') == 'text':
                    value_shape_valid = isinstance(preview_value, str) or (
                        decision['transform'] in {'identity', 'string', 'join_text'}
                        and isinstance(source_value, (str, int, float, list))
                        and not isinstance(source_value, bool))
                elif field.get('kind') == 'file':
                    value_shape_valid = (isinstance(preview_value, str)
                                         and Path(preview_value).is_absolute()
                                         and Path(preview_value).is_file())
                elif field.get('kind') in {'select', 'combobox', 'radio_group'}:
                    value_shape_valid = (isinstance(preview_value, str)
                                         or (isinstance(preview_value, list) and field.get('multiple')))
            if not value_shape_valid:
                decision['classification'] = 'unsupported'
                decision['reason'] = 'invalid_value_shape_for_control: ' + decision['reason']
                proposal['deferred'].append({'field_id': decision['field_id'], 'reason': decision['reason']})
                notes.append(copy.deepcopy(decision))
                continue
            if category == 'mapping' and decision['transform'] not in {'identity', 'string'}:
                decision['classification'] = 'inference'  # Program owns transform semantics, not the prose tag.
            proposal['mappings'].append({k: decision[k] for k in ('field_id', 'source', 'transform', 'depends_on')})
        elif category in {'missing', 'conflict', 'unsupported'}:
            # Some structured workers attach an explanatory candidate pointer to
            # a deferred answer. It is data, never an executable mapping. Keeping
            # it in notes must not discard valid independent decisions in the batch.
            if (decision['source'] is not None and not isinstance(decision['source'], str)) or not isinstance(decision['depends_on'], list):
                raise ContractError('invalid_deferred_metadata')
            proposal['deferred'].append({'field_id': decision['field_id'], 'reason': category+': '+decision['reason']})
        else:
            raise ContractError('invalid_semantic_classification')
        notes.append(copy.deepcopy(decision))
    validation = copy.deepcopy(bound)
    complete = copy.deepcopy(proposal)
    if before_fields := snapshot.get('context_fields'):
        ids = {f['id'] for f in snapshot['fields']}
        validation['fields'] = before_fields
        complete['deferred'] += [{'field_id': f['id'], 'reason': 'context_only'} for f in before_fields if f['id'] not in ids]
    compile_plan(profile, validation, complete)
    validate_record_sources(profile, bound, proposal['mappings'])
    return proposal, bound, notes


def validate_record_sources(profile, snapshot, mappings):
    """Check all merged sources, including mappings found before model binding."""
    context = snapshot.get('mapping_context', {})
    if context.get('record_id'):
        collection = profile
        for token in context['record_collection'][1:].split('/'):
            collection = collection[token.replace('~1', '/').replace('~0', '~')]
        index = next(i for i, item in enumerate(collection) if isinstance(item, dict) and item.get('record_id') == context['record_id'])
        prefix = context['record_collection']+'/'+str(index)+'/'
        for mapping in mappings:
            if mapping['source'].startswith(context['record_collection']+'/') and not mapping['source'].startswith(prefix):
                raise ContractError('mapping_crosses_record_binding')
            from .knowledge import source_template
            source_template(profile, snapshot, mapping['source'])
        if any(b.get('record_collection') == context['record_collection'] and b.get('record_id') == context['record_id']
               for b in snapshot.get('used_record_bindings', [])):
            raise ContractError('record_already_bound_to_other_module')
