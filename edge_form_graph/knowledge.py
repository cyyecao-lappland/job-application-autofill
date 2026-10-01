"""Saved, reviewed semantic knowledge; compile_saved is the only writer.

``approved`` is the caller's independent, whole-module review verdict. Enum
provenance is the accepted decision emitted by graph.review_enums (including
command_id/snapshot_id); callers must not manufacture it from candidate labels.
Counts report newly activated entries, not repeated observations or conflicts.
"""
from __future__ import annotations

import json
import copy
import os
import tempfile
import unicodedata
from contextlib import contextmanager
from pathlib import Path
from urllib.parse import urlsplit

from .contracts import (ContractError, KINDS, SENSITIVE, TRANSFORMS, compile_plan,
                        field_writable, json_value, page_matches, transform)
from .enum_repair import source_context, validate_review
from .field_mapping import FieldMappingIndex, field_key, profile_module
from .employment_defaults import prior_employment_default_source


class KnowledgeError(ValueError):
    """Unreadable, corrupt or incompatible durable knowledge."""


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise KnowledgeError('knowledge_duplicate_key')
        result[key] = value
    return result


def _tokens(pointer):
    if not isinstance(pointer, str) or not pointer.startswith('/'):
        raise ContractError('invalid_source_pointer')
    return [p.replace('~1', '/').replace('~0', '~') for p in pointer[1:].split('/')]


def _pointer(tokens):
    return '/' + '/'.join(str(p).replace('~', '~0').replace('/', '~1') for p in tokens)


def _walk(profile, tokens):
    value, arrays = profile, []
    try:
        for i, token in enumerate(tokens):
            if isinstance(value, list):
                if not token.isdigit() or str(int(token)) != token:
                    raise ContractError('invalid_array_index')
                arrays.append(i)
                value = value[int(token)]
            else:
                value = value[token]
    except (KeyError, IndexError, TypeError, ValueError):
        raise ContractError('source_not_found') from None
    return value, arrays


def resolve_record(profile, snapshot):
    """Return the current record pointer; require a unique record ``id``.

    ``record_id`` keys in records are also supported, but conflicting id aliases
    and duplicate IDs fail closed. No position or record identity is persisted.
    """
    context = snapshot.get('mapping_context') or {}
    collection, rid = context.get('record_collection'), context.get('record_id')
    if not isinstance(rid, str) or not rid:
        raise ContractError('record_binding_required')
    tokens = _tokens(collection)
    records, arrays = _walk(profile, tokens)
    if arrays or not isinstance(records, list):
        raise ContractError('invalid_record_collection')
    matches = [i for i, record in enumerate(records) if isinstance(record, dict)
               and rid in (record.get('id'), record.get('record_id'))]
    if len(matches) != 1:
        raise ContractError('record_not_unique')
    record = records[matches[0]]
    if 'id' in record and 'record_id' in record and record['id'] != record['record_id']:
        raise ContractError('record_identity_conflict')
    return _pointer(tokens + [str(matches[0])])


def source_template(profile, snapshot, source):
    """Canonical source or record-relative source, including list answers.

    Answer lists do not imply array traversal. Only paths through an array
    index require the uniquely resolved record binding.
    """
    json_value(profile, source)
    stable_record = None
    if isinstance(source, str) and source.startswith('record_id:'):
        record_spec, sep, relative = source.partition('/')
        record_id = record_spec.removeprefix('record_id:')
        matches = []
        for collection in ('education', 'employment', 'projects', 'research',
                           'awards', 'campus', 'competitions', 'company_answers'):
            records = profile.get(collection, [])
            if isinstance(records, list):
                matches.extend((collection, index) for index, record in enumerate(records)
                               if isinstance(record, dict) and record.get('record_id') == record_id)
        if not sep or not relative or len(matches) != 1:
            raise ContractError('record_not_unique')
        collection, index = matches[0]
        source = _pointer([collection, str(index), *relative.split('/')])
        stable_record = {'collection': '/' + collection, 'record_id': record_id}
    tokens = _tokens(source)
    _, arrays = _walk(profile, tokens)
    if not arrays:
        return {'pointer': _pointer(tokens)}
    # Company-specific facts are bound to a named record, never to its list position.
    # The outer site/path scope keeps a tenant's answers isolated from other companies.
    if arrays == [1] and tokens[0] == 'company_answers':
        record = profile['company_answers'][int(tokens[1])]
        rid = record.get('record_id')
        if not rid or sum(r.get('record_id') == rid for r in profile['company_answers']) != 1:
            raise ContractError('record_not_unique')
        return {'collection': '/company_answers', 'record_id': rid, 'relative': _pointer(tokens[2:])}
    record = _tokens(resolve_record(profile, snapshot))
    if stable_record and (stable_record['collection'] != _pointer(record[:-1])
                          or stable_record['record_id'] != (snapshot.get('mapping_context') or {}).get('record_id')):
        raise ContractError('mapping_crosses_record_binding')
    if tokens[:len(record)] != record or arrays != [len(record)-1]:
        raise ContractError('unbound_array_source')
    return {'collection': _pointer(record[:-1]), 'relative': _pointer(tokens[len(record):])}


def resolve_source(profile, snapshot, template):
    if set(template) == {'collection', 'current', 'relative'}:
        if template['collection'] != '/education' or template['current'] is not True:
            raise ContractError('invalid_current_record_source')
        records = profile.get('education', [])
        indices = [i for i,r in enumerate(records) if isinstance(r,dict) and r.get('is_current') is True]
        if len(indices) != 1:
            raise ContractError('current_record_not_unique')
        source = '/education/' + str(indices[0]) + template['relative']
        json_value(profile,source)
        return source
    if set(template) == {'pointer'}:
        source = template['pointer']
    elif set(template) == {'collection', 'record_id', 'relative'}:
        if template['collection'] != '/company_answers':
            raise ContractError('fixed_record_source_not_supported')
        records = profile.get('company_answers', [])
        indices = [i for i, r in enumerate(records) if r.get('record_id') == template['record_id']]
        if len(indices) != 1:
            raise ContractError('record_not_unique')
        source = '/company_answers/' + str(indices[0]) + template['relative']
    elif set(template) == {'collection', 'relative'}:
        if (snapshot.get('mapping_context') or {}).get('record_collection') != template['collection']:
            raise ContractError('record_collection_changed')
        source = resolve_record(profile, snapshot) + template['relative']
    else:
        raise ContractError('invalid_source_template')
    if source_template(profile, snapshot, source) != template:
        raise ContractError('source_shape_changed')
    return source


def _key(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'))


def _retain_conditions(prior, candidate):
    if (prior and prior.get('status') == 'active' and 'conditions' in prior
            and 'conditions' not in candidate
            and {k: v for k, v in prior.items() if k not in {'status', 'conditions'}} == candidate):
        return {**candidate, 'conditions': copy.deepcopy(prior['conditions'])}
    return candidate


def _scope(snapshot):
    url = urlsplit(snapshot['target']['url'])
    module = (snapshot.get('mapping_context') or {}).get('module_type') or snapshot.get('module_id')
    if not url.hostname or not module:
        raise ContractError('missing_knowledge_scope')
    # Hash routers can host distinct forms at one URL path. Keep their route,
    # but neither the outer query nor the fragment's query (personal/session data).
    route = url.fragment.partition('?')[0]
    page = (url.path or '/') + ('#' + route if route else '')
    return [url.hostname.lower(), page, module]


def _semantic(field):
    label = unicodedata.normalize('NFKC', field.get('label', ''))
    return [' '.join(label.casefold().split()).strip(' *:：'), field.get('kind')]


def _bound_file_source(profile, snapshot, field):
    """Resolve one existing local file from the field's bound record."""
    if field.get('kind') != 'file' or not _semantic(field)[0]:
        return None
    if _semantic(field)[0] in {'简历附件', '上传简历', '简历', 'resume', 'resume attachment'}:
        resumes=[(i,a) for i,a in enumerate(profile.get('attachments', []))
                 if isinstance(a,dict) and a.get('kind')=='resume'
                 and a.get('value_status')=='source_backed' and isinstance(a.get('path'),str)
                 and Path(a['path']).is_absolute() and Path(a['path']).is_file()]
        if len(resumes)==1:
            return '/attachments/'+str(resumes[0][0])+'/path'
        return None
    try:
        record_pointer = resolve_record(profile, snapshot)
        record, _ = _walk(profile, _tokens(record_pointer))
    except ContractError:
        return None
    if not isinstance(record, dict):
        return None
    record_label = unicodedata.normalize('NFKC', str(record.get('label', '')))
    record_label = ' '.join(record_label.casefold().split()).strip(' *:：')
    if record_label != _semantic(field)[0]:
        return None
    candidates = []
    for key, value in record.items():
        if isinstance(value, str):
            path = Path(value)
            if path.is_absolute() and path.is_file():
                candidates.append(key)
    if len(candidates) != 1:
        return None
    key = str(candidates[0]).replace('~', '~0').replace('/', '~1')
    return record_pointer + '/' + key


def _reason(field):
    if SENSITIVE.search(field.get('label', '')):
        return 'protected_field'
    if field.get('kind') not in KINDS:
        return 'unsupported_field_kind'
    if not field_writable(field):
        return 'field_not_writable'
    return None


def _same_page(before, current):
    return (before.get('target') == current.get('target')
            and before.get('module_id') == current.get('module_id')
            and before.get('module_selector') == current.get('module_selector')
            and (not current.get('mapping_context') or
                 before.get('mapping_context') == current.get('mapping_context')))


def saved_fields_equal(before, after):
    # Popup DOM ids can be regenerated on reload. Everything else, including
    # values, signatures, options, expanded state and selectors, must match.
    def stable(fields):
        return [{k: v for k, v in f.items() if k != 'controls'} for f in fields]
    return stable(before) == stable(after)


def _observed(op, snapshot, current):
    if not _same_page(snapshot, current):
        return False
    matches = [f for f in current['fields'] if f['id'] == op['id']]
    return (len(matches) == 1 and not _reason(matches[0])
            and all(matches[0].get(k) == op['field'].get(k)
                    for k in ('kind', 'selector', 'signature', 'label', 'protected'))
            and 'value' in matches[0] and page_matches([op], current))


@contextmanager
def _writer_lock(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    # Stable sidecar inode: never lock the atomically replaced data file.
    fd = os.open(str(path) + '.lock', os.O_CREAT | os.O_RDWR, 0o600)
    with os.fdopen(fd, 'r+b', buffering=0) as lock:
        if os.name == 'nt':
            import msvcrt
            if os.fstat(lock.fileno()).st_size == 0:
                lock.write(b'\0')
            lock.seek(0)
            msvcrt.locking(lock.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            if os.name == 'nt':
                lock.seek(0)
                msvcrt.locking(lock.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)


class KnowledgeStore:
    def __init__(self, path):
        self.path = Path(path)  # Deliberately no I/O here.

    def _read(self):
        try:
            with self.path.open(encoding='utf-8') as stream:
                data = json.load(stream, object_pairs_hook=_unique_object)
        except FileNotFoundError:
            return {'version': 1, 'field_map': {}, 'enum_map': {}}
        except (OSError, ValueError) as exc:
            raise KnowledgeError('knowledge_unreadable_or_corrupt') from exc
        if (not isinstance(data, dict) or type(data.get('version')) is not int or data['version'] != 1
                or set(data) != {'version', 'field_map', 'enum_map'}):
            raise KnowledgeError('knowledge_invalid_schema')
        for name in ('field_map', 'enum_map'):
            if not isinstance(data[name], dict):
                raise KnowledgeError('knowledge_invalid_schema')
            for key, entry in data[name].items():
                try:
                    scope = json.loads(key)
                    if (not isinstance(scope, list) or len(scope) != (5 if name == 'field_map' else 6)
                            or not all(isinstance(part, str) and part for part in scope[:3])):
                        raise ValueError()
                except (TypeError, ValueError) as exc:
                    raise KnowledgeError('knowledge_invalid_key') from exc
                if not isinstance(entry, dict) or entry.get('status') not in {'active', 'conflicted'}:
                    raise KnowledgeError('knowledge_invalid_entry')
                if entry['status'] == 'conflicted' and set(entry) != {'status'}:
                    raise KnowledgeError('knowledge_invalid_entry')
                if entry['status'] == 'active':
                    required = {'source', 'transform', 'depends_on'} if name == 'field_map' else {'option'}
                    allowed = required | {'status'} | ({'conditions'} if name == 'field_map' else set())
                    if not required | {'status'} <= set(entry) or set(entry) - allowed:
                        raise KnowledgeError('knowledge_invalid_entry')
                    if 'conditions' in entry:
                        if not isinstance(entry['conditions'], list) or any(
                                not isinstance(c, dict) or set(c) != {'pointer', 'equals'}
                                or not isinstance(c['pointer'], str) or not c['pointer'].startswith('/')
                                or type(c['equals']) is not bool for c in entry['conditions']):
                            raise KnowledgeError('knowledge_invalid_conditions')
                    if name == 'field_map' and (not isinstance(entry['source'], dict)
                            or not isinstance(entry['depends_on'], list)):
                        raise KnowledgeError('knowledge_invalid_entry')
                    if name == 'field_map':
                        template = entry['source']
                        if (set(template) not in ({'pointer'}, {'collection', 'relative'}, {'collection', 'record_id', 'relative'})
                                or any(not isinstance(v, str) or not v or (k != 'record_id' and not v.startswith('/')) for k, v in template.items())
                                or ('record_id' in template and template['collection'] != '/company_answers')
                                or not isinstance(entry['transform'], str) or entry['transform'] not in TRANSFORMS
                                or any(not isinstance(dep, list) or len(dep) != 2
                                       or not all(isinstance(v, str) and v for v in dep)
                                       or dep[1] not in KINDS for dep in entry['depends_on'])):
                            raise KnowledgeError('knowledge_invalid_entry')
                    if name == 'enum_map' and not isinstance(entry['option'], str):
                        raise KnowledgeError('knowledge_invalid_entry')
        return data

    def control_sources(self, profile, snapshot):
        """Bind known business fields without requiring an executable control kind."""
        index = FieldMappingIndex(self._read()['field_map'])
        scope = _scope(snapshot)
        counts = {}
        for field in snapshot['fields']:
            key = field_key(profile_module(snapshot), field['label'])
            counts[key] = counts.get(key, 0)+1
        sources = {}
        for field in snapshot['fields']:
            if SENSITIVE.search(field['label']):
                continue
            key = field_key(profile_module(snapshot), field['label'])
            entry = index.scoped_lookup(scope, field['label']) or index.lookup(snapshot, field['label'])
            if (counts[key] != 1 or not entry or entry.get('status') != 'active'
                    or entry.get('transform') != 'identity' or entry.get('depends_on')):
                continue
            try:
                if any(json_value(profile, c['pointer']) is not c['equals'] for c in entry.get('conditions', [])):
                    continue
                sources[field['id']] = resolve_source(profile, snapshot, entry['source'])
            except (ContractError, KeyError, TypeError):
                continue
        return sources

    def known_plan(self, profile, snapshot):
        entries, scope = self._read()['field_map'], _scope(snapshot)
        business_index = FieldMappingIndex(entries)
        fields = snapshot['fields']
        from .parallel import deterministic_record_mappings
        bounded={m['field_id']:m for m in deterministic_record_mappings(profile,snapshot,{f['id'] for f in fields})}
        semantic = {}
        business_fields = {}
        for f in fields:
            semantic.setdefault(_key(_semantic(f)), []).append(f['id'])
            business_fields.setdefault(field_key(profile_module(snapshot), f['label']), []).append(f['id'])
        mappings, deferred = [], []
        for field in fields:
            reason = _reason(field)
            entry = business_index.scoped_lookup(scope, field['label'])
            # A reviewed conditional or employer-specific rule is an exception.
            # Ordinary business-field lookup never keys on website or kind.
            scoped_exception = entry is not None
            universal = None if scoped_exception else business_index.lookup(snapshot, field['label'])
            if universal is not None:
                entry = {**universal, 'depends_on': []}
            try:
                label=field.get('label','').strip(' *:：')
                if entry is None and not reason and field['id'] in bounded:
                    mappings.append(bounded[field['id']])
                    continue
                task_source=None
                task_transform='identity'
                employment_default=prior_employment_default_source(profile,label)
                if employment_default and entry is None and field.get('kind') in {'select','combobox','radio_group'}:
                    task_source=employment_default
                    task_transform='yes_no'
                if entry is None and label in {'外语等级','外语等级-成绩'}:
                    certificates=[(i,r) for i,r in enumerate(profile.get('languages',[]))
                        if r.get('answer_status')=='confirmed' and isinstance(r.get('exam_type'),str) and r['exam_type'].strip()
                        and (type(r.get('score')) in (int,float) or isinstance(r.get('score'),str) and r['score'].strip())]
                    if len(certificates)==1:
                        language_index,_=certificates[0]
                        task_source=f'/languages/{language_index}/'+('exam_type' if label=='外语等级' else 'score')
                        task_transform='identity' if label=='外语等级' else 'string'
                if (entry is None and label in {'专业排名','成绩排名'}
                        and field.get('kind') in {'select','combobox'}
                        and snapshot.get('mapping_context',{}).get('record_collection')=='/education'
                        and sum(field_key('education',f['label']) in {
                            field_key('education','专业排名'),field_key('education','成绩排名')}
                            for f in fields)==1):
                    rank_source=resolve_record(profile,snapshot)
                    education=profile['education'][int(rank_source.split('/')[-1])]
                    position,total=education.get('rank_position'),education.get('rank_total')
                    if (type(position) is int and type(total) is int and 1<=position<=total
                            and education.get('rank_band') in (None,'')):
                        task_source=rank_source+'/rank_position'
                        task_transform='string'
                if not snapshot.get('mapping_context',{}).get('record_id'):
                    ranks={'博士研究生':5,'博士':5,'硕士研究生':4,'硕士':4,'本科':3,'大专':2,'专科':2,'高中':1,'高中及以下':1}
                    ranked=[(ranks.get(r.get('education_level'),0),i,r) for i,r in enumerate(profile.get('education',[]))
                            if r.get('autofill_policy',{}).get('include_by_default') is not False]
                    highest=[x for x in ranked if x[0]==max((r[0] for r in ranked),default=0) and x[0]>0]
                    if len(highest)==1:
                        _,edu_index,education=highest[0]
                        if label=='最高学历':task_source=f'/education/{edu_index}/education_level'
                        elif label in {'最高学历毕业院校','最高学历学校'}:task_source=f'/education/{edu_index}/school_name'
                        elif label.startswith('预计毕业时间') and education.get('expected_graduation_date'):
                            task_source=f'/education/{edu_index}/expected_graduation_date'
                            task_transform='date_'+field['date_part'] if field.get('date_part') in {'year','month'} else 'year_month'
                if label in {'意向工作城市','意向城市','意向工作地点','期望城市','期望工作地点'} and profile.get('batch_answers'):
                    task_source='/batch_answers/preferred_cities' if field.get('multiple') else '/batch_answers/preferred_city'
                    host=urlsplit(snapshot['target']['url']).hostname
                    if field.get('multiple') and host=='poizon.jobs.feishu.cn' and profile['batch_answers'].get('poizon_cities'):
                        task_source='/batch_answers/poizon_cities'
                resume_field=(label in {'上传简历','简历附件','简历','CV/Resume'} or
                    (label in {'file',''} and snapshot.get('module_label','').strip() in {'简历附件','上传简历'}
                     and sum(f.get('kind')=='file' for f in fields)==1))
                if field.get('kind')=='file' and resume_field and profile.get('batch_answers',{}).get('resume_path'):
                    task_source='/batch_answers/resume_path'
                if task_source and not reason:
                    item={'field_id':field['id'],'source':task_source,'transform':task_transform,'depends_on':[]}
                    compile_plan(profile,snapshot,{'mappings':[item],'deferred':[
                        {'field_id':f['id'],'reason':'pending'} for f in fields if f['id']!=field['id']]})
                    mappings.append(item)
                    continue
                if field.get('control_pattern') in {'next_range_date','ant_picker_pair','year_month_range_parts','element_date_pair','ud_date_pair'} and field.get('range_endpoint') in {'start', 'end'} and not reason:
                    source = resolve_record(profile, snapshot)+'/'+field['range_endpoint']+'_date'
                    date_transform='date_'+field['date_part'] if field.get('control_pattern')=='year_month_range_parts' else 'identity' if field.get('control_pattern')=='element_date_pair' else 'year_month'
                    item = {'field_id': field['id'], 'source': source, 'transform': date_transform, 'depends_on': []}
                    compile_plan(profile, snapshot, {'mappings': [item], 'deferred': [
                        {'field_id': f['id'], 'reason': 'pending'} for f in fields if f['id'] != field['id']]})
                    mappings.append(item)
                    continue
                file_source = _bound_file_source(profile, snapshot, field)
                if file_source is not None and not reason:
                    item = {'field_id': field['id'], 'source': file_source,
                            'transform': 'identity', 'depends_on': []}
                    compile_plan(profile, snapshot, {'mappings': [item], 'deferred': [
                        {'field_id': f['id'], 'reason': 'pending'} for f in fields if f['id'] != field['id']]})
                    mappings.append(item)
                    continue
                if reason or not entry or entry['status'] != 'active' or not _semantic(field)[0]:
                    raise ContractError('miss')
                matches = (business_fields[field_key(profile_module(snapshot), field['label'])]
                           if universal is not None else semantic[_key(_semantic(field))])
                if len(matches) != 1:
                    raise ContractError('ambiguous_field')
                if any(json_value(profile, c['pointer']) is not c['equals'] for c in entry.get('conditions', [])):
                    raise ContractError('mapping_condition_not_met')
                deps = [semantic.get(_key(dep), []) for dep in entry['depends_on']]
                if any(len(dep) != 1 for dep in deps):
                    raise ContractError('ambiguous_dependency')
                item = {'field_id': field['id'], 'source': resolve_source(profile, snapshot, entry['source']),
                        'transform': entry['transform'], 'depends_on': [dep[0] for dep in deps]}
                if field.get('date_precision') == 'month' and item['transform'] == 'identity':
                    item['transform'] = 'year_month'
                compile_plan(profile, snapshot, {'mappings': [item], 'deferred': [
                    {'field_id': f['id'], 'reason': 'pending'} for f in fields if f['id'] != field['id']]})
                mappings.append(item)
            except (ContractError, KeyError, TypeError):
                # Conflicts must reach resolve_unknown just like unseen fields.
                # Successful review can resolve this run, but cannot overwrite
                # an ambiguous durable semantic key in _merge.
                deferred.append({'field_id': field['id'], 'reason': reason or 'semantic_match_pending'})
        proposal = {'mappings': mappings, 'deferred': deferred}
        # Independently learned edges can form a cycle when combined. Never
        # return an invalid proposal to the caller's normal graph validator.
        try:
            compile_plan(profile, snapshot, proposal)
        except ContractError:
            proposal = {'mappings': [], 'deferred': [
                {'field_id': f['id'], 'reason': _reason(f) or 'semantic_match_pending'} for f in fields]}
        return proposal

    def compile_saved(self, profile, modules, receipt):
        """Activate one complete reviewed save scope, only after a saved read-back.

        Collection is side-effect free. A missing/changed module rejects the entire
        scope before the real store is locked or written. A failed store write does
        not change the already completed website save.
        """
        if any(m.get('manual_review') or any(op.get('fallback') for op in m.get('operations', [])) for m in modules):
            return {'field_learned': 0, 'enum_learned': 0}
        evidence = receipt.get('evidence', {})
        if (receipt.get('settled') is not True or receipt.get('status') != 'saved'
                or evidence.get('save_confirmed') is not True
                or not evidence.get('save_observed_at') or not modules):
            raise KnowledgeError('learning_requires_confirmed_save')
        saved = evidence.get('saved_modules')
        if not isinstance(saved, list) or len(saved) != len(modules):
            raise KnowledgeError('saved_module_coverage_missing')
        current_by_id = {m['module_id']: m for m in saved}
        if len(current_by_id) != len(saved) or set(current_by_id) != {m['current']['module_id'] for m in modules}:
            raise KnowledgeError('saved_module_coverage_changed')
        prepared = []
        for module in modules:
            review = module.get('review', {})
            current = module['current']
            post = copy.deepcopy(current_by_id[current['module_id']])
            results = module.get('results', {})
            if (module.get('reviewed_revision') != module.get('revision')
                    or module.get('revision') is None or review.get('approved') is not True
                    or review.get('issues') or module.get('command') is not None
                    or set(review.get('checked_field_ids', [])) != {f['id'] for f in current['fields']}
                    or len(review.get('checked_field_ids', [])) != len(current['fields'])
                    or any(v.get('status') not in {'written', 'already_matched', 'prefilled_pending_review'}
                           for v in results.values())):
                raise KnowledgeError('learning_requires_current_complete_review')
            if (post.get('target') != receipt.get('target') or post.get('target') != current.get('target')
                    or post.get('module_selector') != current.get('module_selector')
                    or post.get('observed_at', 0) < evidence['save_observed_at']):
                raise KnowledgeError('saved_observation_not_current')
            # Rebind context from the reviewed plan, not a browser-generated guess.
            if 'mapping_context' in current:
                post['mapping_context'] = copy.deepcopy(current['mapping_context'])
            if (not post.get('fields') and post.get('persistence_evidence', {}).get('kind') == 'module_card_all_values_visible'
                    and isinstance(post.get('persisted_fields'), list)):
                post['fields'] = copy.deepcopy(post['persisted_fields'])
            if not saved_fields_equal(post['fields'], current['fields']):
                raise KnowledgeError('saved_fields_changed')
            if not page_matches(module['operations'], post):
                raise KnowledgeError('saved_values_changed')
            prepared.append((module, post))

        entries = {'field_map': [], 'enum_map': []}
        for module, post in prepared:
            candidates = self._collect_fields(profile, module['snapshot'], module['proposal'], post,
                                              approved=True, operations=module['operations'])
            candidates.extend(self._collect_prefilled_fields(profile, module, post))
            entries['enum_map'].extend(self._collect_enums(profile, module['snapshot'], module['operations'], post))
            guards = module.get('learning_conditions', {})
            for field_id, conditions in guards.items():
                fields = [f for f in post['fields'] if f['id'] == field_id]
                if len(fields) != 1 or not conditions or any(
                        set(c) != {'pointer', 'equals'} or type(c['equals']) is not bool
                        or json_value(profile, c['pointer']) is not c['equals'] for c in conditions):
                    raise KnowledgeError('unmet_learning_condition')
                key = _key(_scope(post) + _semantic(fields[0]))
                for candidate_key, candidate in candidates:
                    if candidate_key == key:
                        candidate['conditions'] = copy.deepcopy(conditions)
            entries['field_map'].extend(candidates)
        with _writer_lock(self.path):
            data = self._read()
            activated = {'field_map': set(), 'enum_map': set()}
            for name, candidates in entries.items():
                for key, candidate in candidates:
                    prior = data[name].get(key)
                    candidate = _retain_conditions(prior, candidate)
                    if prior is None:
                        data[name][key] = {'status': 'active', **candidate}
                        activated[name].add(key)
                    elif prior != {'status': 'active', **candidate}:
                        data[name][key] = {'status': 'conflicted'}
                        activated[name].discard(key)
            fd, temporary = tempfile.mkstemp(prefix=self.path.name + '.', suffix='.tmp', dir=self.path.parent)
            try:
                with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                    json.dump(data, stream, ensure_ascii=False, sort_keys=True)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, self.path)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
        return {'field_learned': len(activated['field_map']), 'enum_learned': len(activated['enum_map'])}

    def _collect_prefilled_fields(self, profile, module, post):
        """Learn reviewed semantic edges for values that were already on page.

        Persistence plus an independent full-field review proves the mapping,
        while ``prefilled_bindings`` limits learning to explicit canonical
        sources. Unbound prefilled fields are accepted as saved content but do
        not produce durable rules.
        """
        basis_transforms = {
            'unique_direct_scalar_equality_in_bound_record': 'identity',
            'canonical_label_alias_requires_value_equivalence_review': 'identity',
            'degree_category_suffix_matches_full_degree': 'degree_category',
            'explicit_referee_relationship_last_clause': 'last_clause',
            'exact_nested_proof_employer': 'identity',
            'rank_band_upper_bound_matches_top_threshold': 'identity',
        }
        reviewed = module.get('review', {})
        if (reviewed.get('approved') is not True or reviewed.get('issues')):
            return []
        checked = set(reviewed.get('checked_field_ids', []))
        fields = {field['id']: field for field in post.get('fields', [])}
        results = module.get('results', {})
        candidates = []
        for binding in module.get('prefilled_bindings', []):
            if not isinstance(binding, dict) or set(binding) != {'field_id', 'source', 'basis'}:
                continue
            field_id, source, basis = binding['field_id'], binding['source'], binding['basis']
            field = fields.get(field_id)
            rule = basis_transforms.get(basis)
            if (field_id not in checked or not field or not rule
                    or results.get(field_id, {}).get('status') != 'prefilled_pending_review'
                    or _reason(field) or not _semantic(field)[0]):
                continue
            try:
                template = source_template(profile, module['snapshot'], source)
                canonical = json_value(profile, source)
                observed = field.get('value')
                if basis == 'canonical_label_alias_requires_value_equivalence_review':
                    # The independent reviewer approved this exact label/value
                    # pair; enum option learning remains separate.
                    if observed in (None, '', []):
                        continue
                elif basis == 'rank_band_upper_bound_matches_top_threshold':
                    import re
                    band = re.fullmatch(r'\s*(\d+(?:\.\d+)?)%\s*[-~～至]\s*(\d+(?:\.\d+)?)%\s*', str(canonical))
                    top = re.fullmatch(r'\s*前\s*(\d+(?:\.\d+)?)%\s*', str(observed))
                    if not band or not top or float(band.group(2)) != float(top.group(1)):
                        continue
                elif transform(canonical, rule) != observed:
                    continue
                candidates.append((_key(_scope(post) + _semantic(field)), {
                    'source': template, 'transform': rule, 'depends_on': []}))
            except (ContractError, KeyError, TypeError, ValueError):
                continue
        return candidates

    def _collect_fields(self, profile, snapshot, proposal, current, approved: bool, *, operations=None):
        """Collect candidates without I/O, optionally using validated executed aliases.

        Supplied operations are evidence, never a replacement for recompilation.
        Only their validated enum decision may change the expected page value.
        """
        if approved is not True or not _same_page(snapshot, current):
            return []
        # Recompile against the fresh saved observation; do not relabel an old
        # observation timestamp to bypass the normal freshness contract.
        compiled, _ = compile_plan(profile, current, proposal)
        supplied = None
        if operations is not None:
            if (not isinstance(operations, list) or any(not isinstance(op, dict)
                    or not isinstance(op.get('id'), str) for op in operations)):
                return []
            supplied = {op['id']: op for op in operations}
            if len(supplied) != len(operations):
                return []
        fields = {f['id']: f for f in snapshot['fields']}
        candidates = []
        for op in compiled:
            if supplied is not None:
                executed = supplied.get(op['id'])
                if executed and executed.get('verification') == 'skipped':
                    continue
                if (not executed or any(executed.get(k) != op[k]
                        for k in ('source', 'transform', 'depends_on'))
                        or not isinstance(executed.get('field'), dict)
                        or any(executed['field'].get(k) != op['field'].get(k)
                               for k in ('kind', 'selector', 'signature', 'label', 'protected'))):
                    continue
                provenance = executed.get('enum_provenance')
                if provenance is not None:
                    try:
                        if (not isinstance(provenance, dict) or provenance.get('approved') is False
                                or not provenance.get('command_id') or not provenance.get('snapshot_id')
                                or op['field']['kind'] not in {'select', 'combobox'}
                                or not isinstance(op['value'], str)
                                or any(provenance.get(k) != op[k] for k in ('source', 'transform'))
                                or provenance.get('field_id') != op['id']
                                or provenance.get('source_value') != op['value']
                                or provenance.get('option') != executed.get('value')
                                or not isinstance(provenance.get('options'), list)
                                or provenance['options'].count(executed.get('value')) != 1):
                            continue
                        decision = {k: provenance[k] for k in ('field_id', 'source_value', 'option', 'reason')}
                        if not validate_review({'decisions': [decision]}, [provenance]):
                            continue
                        op = {**op, 'value': decision['option']}
                    except (ContractError, KeyError, TypeError):
                        continue
                elif executed.get('value') != op['value']:
                    continue
            field = op['field']
            if _reason(field) or not _semantic(field)[0] or not _observed(op, snapshot, current):
                continue
            try:
                source = source_template(profile, snapshot, op['source'])
            except ContractError:
                continue
            candidates.append((_key(_scope(snapshot) + _semantic(field)), {
                'source': source, 'transform': op['transform'],
                'depends_on': sorted([_semantic(fields[fid]) for fid in op['depends_on']], key=_key)}))
        return candidates

    def enum_decisions(self, profile, snapshot, operations, requests):
        entries = self._read()['enum_map']
        by_id = {op['id']: op for op in operations}
        decisions = []
        for request in requests:
            try:
                op = by_id[request['field_id']]
                source = transform(json_value(profile, op['source']), op['transform'])
                if (not isinstance(source, str) or request['source_value'] != source
                        or request.get('source', op['source']) != op['source']
                        or request.get('transform', op['transform']) != op['transform']
                        or request.get('source_context', {}) != source_context(profile, op)
                        or op['field']['kind'] not in {'select', 'combobox'} or _reason(op['field'])):
                    continue
                template = source_template(profile, snapshot, op['source'])
                context = json.dumps(source_context(profile, op), ensure_ascii=False, sort_keys=True)
                qualified_source = source if context == '{}' else source + '\u241f' + context
                entry = entries.get(_key(_scope(snapshot) + [template, op['transform'], qualified_source]))
                if (entry and entry['status'] == 'active' and isinstance(request['options'], list)
                        and request['options'].count(entry['option']) == 1):
                    decisions.append({'field_id': op['id'], 'source_value': source,
                                      'option': entry['option'], 'reason': 'reviewed_knowledge'})
            except (ContractError, KeyError, TypeError):
                continue
        return decisions

    def _collect_enums(self, profile, snapshot, operations, current):
        candidates = []
        for op in operations:
            provenance = op.get('enum_provenance')
            try:
                if (not provenance or provenance.get('approved') is False
                        or not provenance.get('command_id') or not provenance.get('snapshot_id')
                        or op['field']['kind'] not in {'select', 'combobox'}
                        or _reason(op['field']) or not _observed(op, snapshot, current)):
                    continue
                source = transform(json_value(profile, op['source']), op['transform'])
                if (not isinstance(source, str) or provenance['source_value'] != source
                        or provenance['source'] != op['source'] or provenance['transform'] != op['transform']
                        or provenance['field_id'] != op['id'] or provenance['option'] != op['value']
                        or not isinstance(provenance['options'], list)
                        or provenance['options'].count(op['value']) != 1):
                    continue
                decision = {k: provenance[k] for k in ('field_id', 'source_value', 'option', 'reason')}
                if not validate_review({'decisions': [decision]}, [provenance]):
                    continue
                template = source_template(profile, snapshot, op['source'])
                context = json.dumps(source_context(profile, op), ensure_ascii=False, sort_keys=True)
                qualified_source = source if context == '{}' else source + '\u241f' + context
                candidates.append((_key(_scope(snapshot) + [template, op['transform'], qualified_source]),
                                   {'option': op['value']}))
            except (ContractError, KeyError, TypeError):
                continue
        return candidates
