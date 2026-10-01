"""Pure-data workers. Browser commands remain exclusively in the ordered graph."""
from __future__ import annotations

import copy
import re
from concurrent.futures import ThreadPoolExecutor

from .contracts import (ContractError, closed, compile_plan, field_writable,
                        page_matches, validate_snapshot, redacted_profile)
from .enum_repair import review_plan
from .execution_policy import execution_review


SECTIONS = {
    'education': ('education', 'educations', 'education_experiences', '教育', '学历', '学校', '毕业时间','毕业日期','毕业年月'),
    'work': ('work', 'work_experience', 'work_experiences', 'employment', '工作经历'),
    'internships': ('internships', 'internship', 'internship_experiences', '实习'),
    'projects': ('projects', 'project', 'project_experiences', '项目'),
    'research': ('research', 'publications', 'papers', '研究', '论文'),
    'awards': ('awards', 'honors', 'competitions', '奖项', '获奖', '竞赛'),
    'skills': ('skills', 'certificates', 'languages', '技能', '证书', '语言'),
    'personal': ('personal', 'personal_info', 'personal_answers', 'identity', 'basic_info', 'contact', '基本信息', '个人信息'),
    'campus': ('campus', 'campus_experiences', '校园'),
    'family': ('family', 'relative', 'relatives', '家庭', '亲属'),
    'attachments': ('attachment', 'attachments', '附件', '上传', '简历'),
    'preferences': ('preference', 'preferences', 'company_answer', '意向', '偏好', '声明', '申请信息', '更新说明'),
    'certifications': ('certification', 'certifications', 'training', '资格', '培训'),
}

GLOBAL_CONTEXT_ROOTS={'schema_version','updated_at','purpose','usage','collection_policy',
                      'review_required','document_search_status','batch_answers','preferences','company_answer_defaults'}

DIRECT_FIELD_ALIASES={
    '学校':('school_name',),'学校名称':('school_name',),'毕业院校':('school_name',),'学校全称':('school_name',),
    '所在院系':('college',),'导师':('advisor',),'实验室':('laboratory',),
    '专业类别':('discipline_category',),
    'GPA成绩':('gpa',),'GPA':('gpa',),
    '入学时间':('start_date',),'开始时间':('start_date',),'毕业时间':('end_date',),
    '结束时间':('end_date',),'离职时间':('end_date',),
    '学历':('education_level',),'学历层次':('education_level',),'学位':('degree',),
    '专业':('major',),'专业名称':('major',),'年级排名':('rank_band',),'专业排名':('rank_band',),
    '公司名称':('company','organization'),'单位名称':('company','organization'),
    '工作单位':('company','organization'),'职务':('position','role'),
    '实习描述':('responsibilities','description'),
    '公司/组织名称':('company','organization'),'实习单位':('company','organization'),
    '公司或组织名称':('company','organization','name'),'职位或职责':('position','role'),
    '语言能力':('language',),'语言熟练程度':('overall_proficiency',),'会议/期刊':('venue',),
    '至今':('is_current',),
    '职位':('position','role'),'职位名称':('position','role'),
    '岗位':('position','role'),'岗位名称':('position','role'),'实习岗位':('position','role'),
    '工作地点':('location',),'实习地点':('location',),
    '工作内容':('responsibilities','description'),'工作描述':('responsibilities','description'),
    '工作职责':('responsibilities','description'),'实习内容':('responsibilities','description'),
}


def _bound_record(profile, snapshot):
    context = snapshot.get('mapping_context') or {}
    collection, record_id = context.get('record_collection'), context.get('record_id')
    if not isinstance(collection, str) or not collection.startswith('/') or not record_id:
        return None
    records = profile
    try:
        for token in collection[1:].split('/'):
            token = token.replace('~1', '/').replace('~0', '~')
            records = records[int(token)] if isinstance(records, list) else records[token]
    except (KeyError, IndexError, TypeError, ValueError):
        return None
    if not isinstance(records, list):
        return None
    matches = [(index, record) for index, record in enumerate(records)
               if isinstance(record, dict) and record_id in (record.get('id'), record.get('record_id'))]
    if len(matches) != 1:
        return None
    index, record = matches[0]
    return collection, index, record


def deterministic_record_mappings(profile, snapshot, field_ids):
    """Map bounded, label-exact fields inside an already bound record.

    These mappings are ordinary plan operations: the executor compares the
    fresh page value with the transformed canonical value, skips exact matches,
    and corrects mismatches.  No observed page value is used as the answer.
    """
    bound = _bound_record(profile, snapshot)
    collection, index, record = bound or ('', 0, {})
    wanted = set(field_ids)
    mappings = []
    for field in snapshot.get('fields', []):
        if field.get('id') not in wanted or field.get('protected') or not field_writable(field):
            continue
        label = ' '.join(str(field.get('label', '')).split()).strip(' *:：')
        if label in {'校招信息来源','从哪里知道招聘信息的','您从什么渠道获取了我们的招聘信息？'}:
            url=snapshot.get('target',{}).get('url')
            contextual=[key for key,answer in profile.get('batch_answers',{}).items()
                        if isinstance(answer,dict) and answer.get('target_url')==url
                        and answer.get('recruitment_source') and answer.get('recruitment_source_basis')]
            if len(contextual)==1:
                key=contextual[0].replace('~','~0').replace('/','~1')
                mappings.append({'field_id':field['id'],'source':f'/batch_answers/{key}/recruitment_source',
                                 'transform':'identity','depends_on':[]})
                continue
            source=profile.get('company_answer_defaults',{}).get('recruitment_source')
            if isinstance(source,str) and source.strip():
                mappings.append({'field_id':field['id'],'source':'/company_answer_defaults/recruitment_source',
                                 'transform':'identity','depends_on':[]})
                continue
        if field.get('kind')=='checkbox':
            url=snapshot.get('target',{}).get('url')
            matches=[(key,i) for key,answer in profile.get('batch_answers',{}).items()
                     if isinstance(answer,dict) and answer.get('target_url')==url
                     for i,confirmation in enumerate(answer.get('form_confirmations',[]))
                     if isinstance(confirmation,dict) and confirmation.get('label')==label
                     and confirmation.get('confirmed') is True and confirmation.get('basis')]
            if len(matches)==1:
                key,index=matches[0];key=key.replace('~','~0').replace('/','~1')
                mappings.append({'field_id':field['id'],'source':f'/batch_answers/{key}/form_confirmations/{index}/confirmed',
                                 'transform':'identity','depends_on':[]})
                continue
        if label in {'意向工作城市', '意向城市', '意向工作地点'}:
            answers = profile.get('batch_answers', {})
            url = snapshot.get('target', {}).get('url')
            matches = [(key, answer) for key, answer in answers.items()
                       if isinstance(answer, dict) and answer.get('target_url') == url
                       and isinstance(answer.get('intended_city'), str)
                       and answer['intended_city'] in profile.get('preferences', {}).get('preferred_cities', [])]
            if len(matches) == 1:
                key = matches[0][0].replace('~', '~0').replace('/', '~1')
                mappings.append({'field_id': field['id'], 'source': f'/batch_answers/{key}/intended_city',
                                 'transform': 'identity', 'depends_on': []})
                continue
        if label == '同步更新在线简历' and isinstance(profile.get('company_answer_defaults', {}).get('synchronize_online_resume'), bool):
            mappings.append({'field_id': field['id'], 'source': '/company_answer_defaults/synchronize_online_resume',
                             'transform': 'identity', 'depends_on': []})
            continue
        if field.get('kind') == 'file' and label in {'上传简历', '简历'}:
            resumes = [(i, a) for i, a in enumerate(profile.get('attachments', []))
                       if isinstance(a, dict) and a.get('kind') == 'resume'
                       and a.get('value_status') == 'source_backed' and a.get('path')]
            if len(resumes) == 1:
                mappings.append({'field_id': field['id'], 'source': f'/attachments/{resumes[0][0]}/path',
                                 'transform': 'identity', 'depends_on': []})
                continue
        summaries={'大赛经历':'competition_summary','奖励与荣誉':'award_summary',
                   '自我评价':'self_assessment','自我介绍':'self_assessment'}
        summary=summaries.get(label)
        if label.startswith('其它欢迎使用') and '介绍你自己' in label:
            summary='self_assessment'
        if summary and profile.get('quick_fill',{}).get(summary):
            mappings.append({'field_id':field['id'],'source':'/quick_fill/'+summary,'transform':'identity','depends_on':[]})
            continue
        if label in {'专业技能证书', '其他资格证书'}:
            collection_status = profile.get('collection_status')
            status = collection_status.get('certifications') if isinstance(collection_status, dict) else None
            if status == 'explicit_none' and profile.get('certifications') == []:
                item = {'field_id':field['id'], 'source':'/collection_status/certifications',
                        'transform':'explicit_none_text', 'depends_on':[]}
                try:
                    compile_plan(profile, snapshot, {'mappings':[item], 'deferred':[
                        {'field_id':other['id'], 'reason':'pending'}
                        for other in snapshot['fields'] if other['id'] != field['id']]})
                except ContractError:
                    continue
                mappings.append(item)
            continue
        if label.casefold() == 'github':
            # A Github URL field accepts the exact supplied project URL. Do not
            # manufacture a personal account URL by stripping repository paths.
            links = [(f'/projects/{i}/url', r.get('url')) for i, r in enumerate(profile.get('projects', []))
                     if isinstance(r, dict) and isinstance(r.get('url'), str)
                     and r['url'].startswith('https://github.com/')]
            if len(links) == 1:
                mappings.append({'field_id': field['id'], 'source': links[0][0], 'transform': 'identity', 'depends_on': []})
                continue
        if label == '研究方向' and bound is None:
            current = [(i,r) for i,r in enumerate(profile.get('education',[]))
                       if r.get('is_current') is True and r.get('research_direction')]
            if len(current) == 1:
                mappings.append({'field_id':field['id'], 'source':f'/education/{current[0][0]}/research_direction',
                                 'transform':'identity', 'depends_on':[]})
                continue
        if bound is None:
            continue
        if collection == '/projects' and label in {'项目名称','项目角色','项目链接','项目描述','描述'}:
            key={'项目名称':'name','项目角色':'role','项目链接':'url','项目描述':'description','描述':'description'}[label]
            if record.get(key) in (None,'',[]):
                continue
            item={'field_id':field['id'],'source':f'{collection}/{index}/{key}',
                  'transform':'identity','depends_on':[]}
            try:
                compile_plan(profile,snapshot,{'mappings':[item],'deferred':[
                    {'field_id':f['id'],'reason':'pending'} for f in snapshot['fields'] if f['id']!=field['id']]})
            except ContractError:
                continue
            mappings.append(item)
            continue
        if field.get('kind') == 'file' and label in {'成绩证明', '成绩单'}:
            if collection != '/education' or not record.get('record_id'):
                continue
            policy = record.get('transcript_usage_policy')
            policy_path = policy.get('path') if isinstance(policy, dict) else None
            if isinstance(policy, dict) and policy.get('use_for_applications') is False:
                continue
            if policy_path and isinstance(policy, dict) and policy.get('use_for_applications') is True:
                if not isinstance(policy_path, str):
                    continue
                relative = 'transcript_usage_policy/path'
            else:
                transcript = record.get('transcript')
                transcript_path = transcript.get('path') if isinstance(transcript, dict) else None
                if not isinstance(transcript_path, str) or not transcript_path:
                    continue
                relative = 'transcript/path'
            item = {'field_id':field['id'], 'source':f"record_id:{record['record_id']}/{relative}",
                    'transform':'identity', 'depends_on':[]}
            try:
                compile_plan(profile, snapshot, {'mappings':[item], 'deferred':[
                    {'field_id':other['id'], 'reason':'pending'}
                    for other in snapshot['fields'] if other['id'] != field['id']]})
            except ContractError:
                continue
            mappings.append(item)
            continue
        aliases, transform = DIRECT_FIELD_ALIASES.get(label, ()), 'identity'
        key = next((candidate for candidate in aliases
                    if candidate in record and record[candidate] not in (None, '', [])), None)
        if label == '学位':
            transform = 'degree_category'
        elif label == '是否第二学位' and collection == '/education' and record.get('second_degree') == '无':
            key, transform = 'second_degree', 'none_string_no'
        elif label == '证明人姓名':
            key = 'referee_name'
        elif label in {'证明人关系', '与本人关系'}:
            key = 'referee_relationship'
        elif label in {'证明人联系方式', '证明人联系电话', '证明人邮箱'}:
            key = 'referee_contact'
        elif label == '证明人职务':
            key, transform = 'referee_relationship', 'last_clause'
        if label == '证明人单位':
            proof = record.get('proof')
            if not isinstance(proof, dict) or proof.get('employer_legal_name') in (None, '', []):
                continue
            source = f'{collection}/{index}/proof/employer_legal_name'
        elif key in record and record[key] not in (None, '', []):
            escaped = str(key).replace('~', '~0').replace('/', '~1')
            source = f'{collection}/{index}/{escaped}'
        else:
            observed = field.get('value')
            candidates = [candidate_key for candidate_key, candidate in record.items()
                          if candidate_key not in {'id', 'record_id'}
                          and not isinstance(candidate, (dict, list))
                          and candidate is not None and candidate == observed]
            if observed in (None, '', []) or len(candidates) != 1:
                continue
            escaped = str(candidates[0]).replace('~', '~0').replace('/', '~1')
            source = f'{collection}/{index}/{escaped}'
        item = {'field_id': field['id'], 'source': source,
                'transform': transform, 'depends_on': []}
        if field.get('date_precision')=='month' and transform=='identity' and key in {'start_date','end_date'}:
            item['transform']='year_month'
        try:
            compile_plan(profile, snapshot, {'mappings': [item], 'deferred': [
                {'field_id': other['id'], 'reason': 'pending'}
                for other in snapshot['fields'] if other['id'] != field['id']]})
        except ContractError:
            continue
        mappings.append(item)
    return mappings


def categories(label):
    text = re.sub(r'([a-z])([A-Z])', r'\1_\2', str(label)).lower()
    return {category for category, words in SECTIONS.items() if any(
        (word in text if not word.isascii() else re.search(r'(?<![a-z])' + re.escape(word) + r'(?![a-z])', text))
        for word in words)}


def focused_profile(profile, snapshot, required_sources=()):
    """Prune only confidently unrelated top-level sections, never rebase pointers.

    Unknown schemas/labels fall back to full context. Lists are kept whole (indices
    and record coverage matter). Unclassified keys, policies and metadata survive.
    """
    labels = [snapshot.get('module_label', snapshot.get('module_id', ''))]
    selected = categories(labels[0])
    if not selected or not isinstance(profile, dict):
        return copy.deepcopy(profile)
    for field in snapshot.get('fields', []):
        selected |= categories(field.get('label', ''))
    known_keys = {key: categories(key) for key in profile}
    if not any(value & selected for value in known_keys.values()):
        return copy.deepcopy(profile)
    relevant_roots={key for key,value in known_keys.items() if value & selected}
    # A reviewer must receive every source referenced by the actual plan even
    # when the module heading classifies it differently (skills/certifications).
    for source in required_sources:
        if not isinstance(source, str) or not source.startswith('/'):
            continue
        parts = [part.replace('~1', '/').replace('~0', '~') for part in source[1:].split('/')]
        if parts[0] in profile:
            relevant_roots.add(parts[0])
        if parts[0] == 'collection_status' and len(parts) > 1 and parts[1] in profile:
            relevant_roots.add(parts[1])
    # A project explicitly presented in an internship section remains bound to
    # /projects. Include that canonical collection even though the visible page
    # heading says internship, otherwise semantic fallback would lose its source.
    collection = (snapshot.get('mapping_context') or {}).get('record_collection')
    if isinstance(collection, str) and collection.startswith('/'):
        root = collection[1:].split('/', 1)[0]
        if root in profile:
            relevant_roots.add(root)
    projected={key:copy.deepcopy(value) for key,value in profile.items() if key in relevant_roots}
    metadata=profile.get('field_metadata')
    if isinstance(metadata,dict):
        projected['field_metadata']={key:copy.deepcopy(value) for key,value in metadata.items()
            if key.split('.',1)[0] in relevant_roots}
    missing=profile.get('missing_values')
    if isinstance(missing,list):
        projected['missing_values']=[copy.deepcopy(item) for item in missing if isinstance(item,dict)
            and str(item.get('record','')).split('.',1)[0] in relevant_roots]
    status=profile.get('collection_status')
    if isinstance(status,dict):
        projected['collection_status']={key:copy.deepcopy(value) for key,value in status.items()
            if key in relevant_roots or categories(key) & selected}
    for key in GLOBAL_CONTEXT_ROOTS:
        if key in profile:
            projected[key]=copy.deepcopy(profile[key])
    return projected


def validate_module_snapshot(snapshot, target, module):
    validate_snapshot(snapshot)
    if (snapshot['target'] != target or snapshot['module_id'] != module['id']
            or snapshot.get('module_selector') != module['selector']):
        raise ContractError('wrong_module_snapshot')


def snapshot_content(snapshot):
    # Observation IDs/times change on every read. All identity, value, control,
    # coverage and record data must still match; no digest or timestamp shortcut.
    return {key: copy.deepcopy(value) for key, value in snapshot.items()
            if key not in {'snapshot_id', 'observed_at'}}


def cached_mapping(entry, profile, live):
    validate_snapshot(live)
    if (not entry or entry.get('status') != 'mapped' or redacted_profile(entry.get('profile')) != redacted_profile(profile)
            or snapshot_content(entry['snapshot']) != snapshot_content(live)):
        return None
    return copy.deepcopy(entry['proposal'])


def accepted_review(review, before):
    try:
        closed(review, {'approved', 'checked_field_ids', 'issues'})
        checked = review['checked_field_ids']
        return (review['approved'] is True and review['issues'] == []
                and isinstance(checked, list) and all(isinstance(x, str) for x in checked)
                and len(checked) == len(set(checked))
                and set(checked) == {f['id'] for f in before['fields']})
    except (ContractError, KeyError, TypeError):
        return False


def deterministic_scope_reviews(modules, results, target):
    """Approve a save scope without an LLM only when durable rules prove it.

    This is deliberately narrower than the independent semantic reviewer. Every
    non-protected field must be covered by an active knowledge-table mapping,
    execution/read-back must have succeeded, and no semantic or enum model may
    have participated in the current module. Any new, omitted, ambiguous or
    merely prefilled field falls back to the normal Luna scope review.
    """
    reviews = {}
    for module in modules:
        result = results.get(module['id'])
        if not result:
            return None
        try:
            before, current = result['snapshot'], result['current']
            validate_module_snapshot(before, target, module)
            validate_module_snapshot(current, target, module)
            proposal = result['proposal']
            operations = result.get('operations', [])
            metrics = result.get('metrics', {})
            operation_ids = {op['id'] for op in operations}
            if (metrics.get('model_calls', 0) != 0
                    or metrics.get('semantic_fields', 0) != 0
                    or (metrics.get('field_table_hits', 0)
                        + metrics.get('deterministic_record_hits', 0)) != len(proposal.get('mappings', []))
                    or proposal.get('deferred')
                    or len(operation_ids) != len(operations)
                    or any(result.get('results', {}).get(fid, {}).get('status') not in {'written', 'already_matched', 'verification_skipped'}
                           for fid in operation_ids)
                    or any(fid not in operation_ids for fid in result.get('results', {}))
                    or not page_matches(operations, current)):
                return None
            protected_ids = {field['id'] for field in current['fields'] if protected_present(field)}
            if operation_ids | protected_ids != {field['id'] for field in before['fields']}:
                return None
            plan = review_plan(proposal, operations)
            verdict = {'approved': True,
                       'checked_field_ids': [field['id'] for field in before['fields']],
                       'issues': []}
            reviews[module['id']] = {
                'status': 'approved', 'model': 'program', 'review': verdict,
                'revision': result['revision'], 'current': copy.deepcopy(current),
                'before': copy.deepcopy(before), 'proposal': plan,
            }
        except (ContractError, KeyError, TypeError):
            return None
    return reviews


def protected_present(field):
    """Presence evidence is not a value, a write result, or proof of persistence."""
    return field.get('protected') is True and (field.get('value_present') is True or field.get('secret_match') is True)


def readonly_present(field):
    """A fresh nonempty readonly value needs review, not a write handler."""
    value=field.get('value')
    return (field.get('protected') is not True
            and (field.get('disabled') or field.get('readonly') or field.get('readOnly'))
            and field.get('value_readable') is not False
            and value not in (None,'',[]))


def prefilled_bindings(profile, result):
    """Ground nonempty current values in one stable record without inference."""
    snapshot = result.get('current', {})
    bindings=[{'field_id':f['id'],'source':f['verified_control']['source'],
               'basis':'typed_control_commit_and_fresh_display_match'}
              for f in snapshot.get('fields',[]) if f.get('verified_control')]
    bound = _bound_record(profile, snapshot)
    if bound is None:
        ranks={'博士研究生':4,'博士':4,'硕士研究生':3,'硕士':3,'本科':2,'大专':1}
        ranked=[(ranks.get(r.get('education_level'),0),i,r) for i,r in enumerate(profile.get('education',[]))
                if isinstance(r,dict) and r.get('autofill_policy',{}).get('include_by_default') is not False]
        highest=[r for r in ranked if r[0]>0 and r[0]==max((x[0] for x in ranked),default=0)]
        if len(highest)==1:
            _,index,record=highest[0]
            for field in snapshot.get('fields',[]):
                if not readonly_present(field):continue
                label=field.get('label','').strip(' *:：');value=field.get('value')
                if label=='最高学历' and value in {record.get('education_level'),record.get('degree')}:
                    bindings.append({'field_id':field['id'],'source':f'/education/{index}/education_level',
                                     'basis':'unique_highest_education_requires_value_equivalence_review'})
                if label in {'毕业时间 年','毕业时间 月','预计毕业时间 年','预计毕业时间 月'}:
                    date=record.get('expected_graduation_date') or record.get('end_date')
                    if not isinstance(date,str) or not re.fullmatch(r'\d{4}-\d{2}(?:-\d{2})?',date):continue
                    part=field.get('date_part');wanted=date[:4] if part=='year' else date[5:7] if part=='month' else None
                    if wanted and str(value).isdigit() and int(value)==int(wanted):
                        key='expected_graduation_date' if record.get('expected_graduation_date') else 'end_date'
                        bindings.append({'field_id':field['id'],'source':f'/education/{index}/{key}',
                                         'basis':'unique_highest_graduation_date_component','transform':'date_'+part})
        return bindings
    collection, index, record = bound
    current_fields = {field['id']: field for field in snapshot.get('fields', [])}
    for field_id, outcome in result.get('results', {}).items():
        if outcome.get('status') != 'prefilled_pending_review':
            continue
        field = current_fields.get(field_id, {})
        if field.get('verified_control'):continue
        observed = field.get('value')
        if observed in (None, '', []) or field.get('protected'):
            continue
        label=' '.join(str(field.get('label','')).split()).strip(' *:：')
        # A persisted top-N bucket can be grounded without asking a model to
        # invent an enum alias when the canonical record supplies the exact
        # percentile band.  The upper bound is the bucket boundary (for
        # example 10%-20% -> 前20%).
        if '排名' in label and isinstance(observed, str) and isinstance(record.get('rank_band'), str):
            band=re.fullmatch(r'\s*(\d+(?:\.\d+)?)%\s*[-~～至]\s*(\d+(?:\.\d+)?)%\s*',record['rank_band'])
            top=re.fullmatch(r'\s*前\s*(\d+(?:\.\d+)?)%\s*',observed)
            if band and top and float(band.group(2))==float(top.group(1)):
                bindings.append({'field_id':field_id,'source':collection+'/'+str(index)+'/rank_band',
                                 'basis':'rank_band_upper_bound_matches_top_threshold'})
                continue
        if label=='学位' and isinstance(observed,str) and isinstance(record.get('degree'),str):
            degree=record['degree'].strip()
            if degree.endswith(observed.strip()) and observed.strip() in {'学士','硕士','博士'}:
                bindings.append({'field_id':field_id,'source':collection+'/'+str(index)+'/degree',
                                 'basis':'degree_category_suffix_matches_full_degree'})
                continue
        if label=='证明人职务' and isinstance(observed,str) and isinstance(record.get('referee_relationship'),str):
            parts=[part.strip() for part in re.split(r'[,，;；]',record['referee_relationship']) if part.strip()]
            if parts and observed==parts[-1]:
                bindings.append({'field_id':field_id,'source':collection+'/'+str(index)+'/referee_relationship',
                                 'basis':'explicit_referee_relationship_last_clause'})
                continue
        if label=='证明人单位' and isinstance(observed,str) and isinstance(record.get('proof'),dict):
            employer=record['proof'].get('employer_legal_name')
            if isinstance(employer,str) and employer==observed:
                bindings.append({'field_id':field_id,'source':collection+'/'+str(index)+'/proof/employer_legal_name',
                                 'basis':'exact_nested_proof_employer'})
                continue
        candidates = [key for key, candidate in record.items()
                      if key not in {'id', 'record_id'} and not isinstance(candidate, (dict, list))
                      and candidate is not None and candidate == observed]
        if len(candidates) == 1:
            key = str(candidates[0]).replace('~', '~0').replace('/', '~1')
            bindings.append({'field_id': field_id, 'source': collection+'/'+str(index)+'/'+key,
                             'basis': 'unique_direct_scalar_equality_in_bound_record'})
            continue
        aliases=DIRECT_FIELD_ALIASES.get(label, ())
        key=next((candidate for candidate in aliases
                  if candidate in record and record[candidate] not in (None,'',[])), None)
        if key is not None:
            escaped=key.replace('~','~0').replace('/','~1')
            bindings.append({'field_id':field_id,'source':collection+'/'+str(index)+'/'+escaped,
                             'basis':'canonical_label_alias_requires_value_equivalence_review'})
    return bindings


def fill_results_complete(result, preserved_blank_ids=()):
    fields = {f['id']: f for f in result['current']['fields']}
    preserved_blank_ids = set(preserved_blank_ids)
    def intentional_optional_omission(fid, item):
        field = fields.get(fid, {})
        reason = item.get('reason', '')
        return (item.get('status') == 'deferred' and field.get('required') is False
                and isinstance(reason, str)
                and reason.startswith(('missing:', 'unsupported:', 'source_is_not_an_answer:',
                                       'current_field_not_writable:', 'attachment_purpose_mismatch:')))
    return bool(result['results']) and all(
        item['status'] in {'written', 'already_matched', 'verification_skipped', 'prefilled_pending_review'}
        or (fid in preserved_blank_ids and item.get('status') == 'deferred'
            and fields.get(fid, {}).get('value') in (None, '', []))
        or (item['status'] == 'deferred' and (protected_present(fields.get(fid, {})) or readonly_present(fields.get(fid, {}))))
        or intentional_optional_omission(fid, item)
        for fid, item in result['results'].items())


class ParallelWorkers:
    """Bounded model calls; CodexJsonModel creates an isolated process per call."""

    def __init__(self, model, max_workers=2):
        if type(max_workers) is not int or not 1 <= max_workers <= 3:
            raise ContractError('worker_count_must_be_1_to_3')
        self.model, self.max_workers = model, max_workers

    def run(self, jobs, function):
        def guarded(job):
            try:
                return function(copy.deepcopy(job))
            except Exception as exc:
                # Fail closed without persisting arbitrary exception/profile text.
                return {'status': 'worker_failed', 'error': type(exc).__name__}
        with ThreadPoolExecutor(max_workers=self.max_workers, thread_name_prefix='form-json') as pool:
            return list(pool.map(guarded, jobs))

    def premap(self, profile, modules, target):
        profile = redacted_profile(profile)
        def mapping(module):
            snap = module['snapshot']
            validate_module_snapshot(snap, target, module)
            proposal = self.model.map(focused_profile(profile, snap), snap)
            compile_plan(profile, snap, proposal)
            return {'status': 'mapped', 'snapshot': snap, 'profile': copy.deepcopy(profile), 'proposal': proposal}
        supplied = [m for m in modules if m.get('snapshot') is not None]
        return dict(zip((m['id'] for m in supplied), self.run(supplied, mapping)))

    def review_scope(self, profile, modules, results, target):
        if all(results.get(m['id'], {}).get('agent_tuning') is False for m in modules):
            reviews = {}
            for module in modules:
                result = results[module['id']]
                verdict = execution_review(result)
                reviews[module['id']] = {'status': 'approved' if verdict['approved'] else 'review_rejected',
                    'model': 'program', 'review': verdict, 'revision': result['revision'],
                    'current': copy.deepcopy(result['current']), 'before': copy.deepcopy(result['snapshot']),
                    'proposal': review_plan(result['proposal'], result.get('operations', []))}
            return reviews
        # One unfamiliar module must not send all already-proven modules back
        # through a model. Keep the complete scope below for omission checks.
        program_reviews = {}
        for module in modules:
            verdict = deterministic_scope_reviews([module], results, target)
            if verdict is not None:
                program_reviews.update(verdict)
        if len(program_reviews) == len(modules):
            return program_reviews
        profile = redacted_profile(profile)
        scope_hint={'module_id':' '.join(str(m.get('id','')) for m in modules),
                    'module_label':' '.join(str(m.get('module_label','')) for m in modules),
                    'fields':[copy.deepcopy(f) for m in modules for f in m.get('snapshot',{}).get('fields',[])]}
        required_sources = [entry.get('source') for module in modules
            for key in ('mappings', 'prefilled_bindings')
            for entry in (results.get(module['id'], {}).get('proposal', {}) if key == 'mappings'
                          else results.get(module['id'], {})).get(key, [])]
        profile = focused_profile(profile,scope_hint,required_sources)
        # Every independent reviewer receives the full source and full save scope,
        # including deferred/unmapped records, not the mapper's projection.
        coverage = []
        for module in modules:
            result = results.get(module['id'])
            if not result:
                return {m['id']: {'status': 'review_missing'} for m in modules}
            review_module = {key: value for key, value in module.items() if key != 'snapshot'}
            # Labels are useful to humans but are not a stable review contract: duplicate
            # labels and long DOM-backed IDs can otherwise leave the reviewer unsure which
            # exact blanks the program already proved safe to preserve.  The application
            # graph computes these IDs only when both the original and current values are
            # blank, and fill coverage has already verified the source-side deferral.
            review_module['preserved_blank_field_ids'] = copy.deepcopy(
                result.get('preserved_blank_ids', []))
            plan = review_plan(result['proposal'], result.get('operations', []))
            bindings = copy.deepcopy(result.get('prefilled_bindings', []))
            if not bindings:
                bindings = prefilled_bindings(profile, result)
            if bindings:
                plan['prefilled_bindings'] = bindings
            coverage.append({'module': review_module, 'before': result['snapshot'],
                             'after': result['current'], 'plan': plan,
                             'revision': result['revision']})

        def review(item):
            module, before, after = item['module'], item['before'], item['after']
            validate_module_snapshot(after, target, module)
            original = copy.deepcopy(before)
            original['save_scope_context'] = copy.deepcopy(coverage)
            verdict = self.model.review(copy.deepcopy(profile), original, copy.deepcopy(item['plan']), copy.deepcopy(after))
            validate_module_snapshot(after, target, module)  # slow reviewers can expire evidence
            return {'status': 'approved' if accepted_review(verdict, before) else 'review_rejected',
                    'model': getattr(self.model, 'model', None),
                    'review': verdict, 'revision': item['revision'], 'current': copy.deepcopy(after),
                    'before': copy.deepcopy(before), 'proposal': copy.deepcopy(item['plan'])}
        pending = [item for item in coverage if item['module']['id'] not in program_reviews]
        batch_review = getattr(self.model, 'review_scope', None)
        if callable(batch_review):
            def review_batch(_):
                for item in coverage:
                    validate_module_snapshot(item['after'], target, item['module'])
                verdicts = batch_review(copy.deepcopy(profile), copy.deepcopy(coverage),
                                        [item['module']['id'] for item in pending])
                reviews = {}
                for item in pending:
                    module = item['module']
                    after = item['after']
                    validate_module_snapshot(after, target, module)
                    verdict = verdicts.get(module['id'])
                    reviews[module['id']] = {
                        'status': 'approved' if accepted_review(verdict, item['before']) else 'review_rejected',
                        'model': getattr(self.model, 'model', None), 'review': verdict,
                        'revision': item['revision'], 'current': copy.deepcopy(after),
                        'before': copy.deepcopy(item['before']), 'proposal': copy.deepcopy(item['plan']),
                    }
                return reviews
            batch_result = self.run([None], review_batch)[0]
            if batch_result.get('status') == 'worker_failed':
                return {**program_reviews, **{item['module']['id']: copy.deepcopy(batch_result)
                                              for item in pending}}
            return {**program_reviews, **batch_result}
        return {**program_reviews, **dict(zip((item['module']['id'] for item in pending),
                                               self.run(pending, review)))}


def scope_reviews_current(modules, results, reviews, target):
    for module in modules:
        result, review = results.get(module['id']), reviews.get(module['id'])
        if not result or not review or review.get('status') != 'approved':
            return False
        try:
            validate_module_snapshot(result['current'], target, module)
            expected_plan = review_plan(result['proposal'], result.get('operations', []))
            if result.get('prefilled_bindings'):
                expected_plan['prefilled_bindings'] = copy.deepcopy(result['prefilled_bindings'])
            if (review['revision'] != result['revision']
                    or snapshot_content(review['current']) != snapshot_content(result['current'])
                    or snapshot_content(review['before']) != snapshot_content(result['snapshot'])
                    or review['proposal'] != expected_plan
                    or not accepted_review(review['review'], result['snapshot'])):
                return False
        except (ContractError, KeyError, TypeError):
            return False
    return True
