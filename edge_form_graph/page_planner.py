"""Program-owned page order, canonical record binding and explicit coverage gaps."""
from __future__ import annotations
import copy
import json
import re
import argparse
from pathlib import Path


def collection_kind(label, scope_label=''):
    if '教育' in label or '学历' in label:
        return 'education'
    if '论文' in label:
        return 'research'
    if '语言' in label:
        return 'languages'
    if '实习' in scope_label and '项目' in scope_label:
        return 'combined_experience'
    if '项目' in label:
        return 'projects'
    if any(word in label for word in ('实习', '工作')):
        return 'employment'
    return None


def records_for(profile, kind, label):
    included, excluded = [], []
    collections = ['employment', 'projects'] if kind == 'combined_experience' else [kind]
    employment_ids = {r['record_id'] for r in profile.get('employment', [])
                      if r.get('autofill_policy', {}).get('include_by_default') is not False}
    for collection in collections:
        for record in profile.get(collection, []):
            rid = record['record_id']
            reason = None
            if record.get('autofill_policy', {}).get('include_by_default') is False:
                reason = 'excluded_by_user_policy'
            elif kind == 'education' and '高中' in str(record.get('education_level','')) and '高中' not in label:
                reason = 'high_school_not_requested'
            elif kind == 'combined_experience' and collection == 'projects' and employment_ids.intersection(record.get('related_record_ids') or []):
                reason = 'represented_by_related_employment'
            elif kind == 'research' and (not record.get('venue') or re.search(r'未投稿|在审|未完成|预印本|尚未|未确认', str(record.get('status', '')))):
                reason = 'not_confirmed_venue_publication'
            elif kind == 'languages' and record.get('answer_status') == 'explicit_none':
                reason = 'no_confirmed_certificate_record'
            item = {'record_collection': '/'+collection, 'record_id': rid, 'module_type': collection}
            if reason:
                excluded.append({**item, 'reason': reason})
            else:
                included.append((item, record))
    # A language row represents a language, not one examination certificate.
    if kind == 'languages':
        seen=set(); unique=[]
        for binding, record in reversed(included):
            language=record.get('language')
            if language in seen:
                excluded.append({**binding,'reason':'same_language_represented_by_other_certificate'})
            else:
                seen.add(language);unique.append((binding,record))
        included=list(reversed(unique))
    return included, excluded


def _month(value):
    text=str(value or '').strip()
    match=re.search(r'(\d{4})[-./年](\d{1,2})',text)
    return f'{int(match.group(1)):04d}-{int(match.group(2)):02d}' if match else text[:7]


def _education_level(value):
    text=''.join(str(value or '').split())
    if '本科' in text:return '本科'
    if '博士' in text:return '博士'
    if '硕士' in text:return '硕士'
    if '高中' in text:return '高中'
    return text


def observed_range_dates(fields):
    """Read one date per endpoint, including split year/month controls."""
    observed={}
    for endpoint in ('start','end'):
        controls=[f for f in fields if f.get('range_endpoint')==endpoint]
        populated=[f for f in controls if str(f.get('value') or '').strip()]
        if not populated:continue
        parts=[f for f in controls if f.get('date_part')]
        if parts:
            if len(parts)!=len(controls):return None
            by_part={}
            for field in parts:
                part=field['date_part']
                if part not in {'year','month','day'} or part in by_part:return None
                by_part[part]=str(field.get('value') or '').strip()
            year,month=by_part.get('year',''),by_part.get('month','')
            if not re.fullmatch(r'\d{4}',year) or not re.fullmatch(r'\d{1,2}',month):return None
            if not 1<=int(month)<=12:return None
            if 'day' in by_part:
                import datetime
                if not re.fullmatch(r'\d{1,2}',by_part['day']):return None
                try:datetime.date(int(year),int(month),int(by_part['day']))
                except ValueError:return None
            observed[endpoint]=f'{int(year):04d}-{int(month):02d}'
        else:
            if len(populated)!=1:return None
            observed[endpoint]=populated[0]['value']
    return observed


def row_match(row, candidates, kind=None):
    fields = row.get('snapshot', {}).get('fields', [])
    if row.get('state') == 'collapsed' and row.get('card_values'):
        fields = [{'label': k, 'value': v} for k, v in row['card_values'].items()]
    values = [str(f.get('value') or '').strip() for f in fields if f.get('kind') != 'checkbox']
    if not any(values):
        return 'blank', None
    range_dates=observed_range_dates(fields)
    if range_dates is None:return 'ambiguous',None
    if kind=='education':
        labels={'education_level':{'学历','学历层次','学历类别'},
                'school_name':{'学校','学校名称','学校全称','毕业院校'},
                'major':{'专业','专业名称','所学专业'},
                'start_date':{'入学时间','开始时间','教育时间 起始日期','时间 起始日期'},
                'end_date':{'毕业时间','结束时间','时间 结束日期'}}
        observed={}
        observed_fields={}
        for field in fields:
            label=' '.join(str(field.get('label','')).split()).strip(' *:：')
            value=str(field.get('value') or '').strip()
            if value:
                for key,names in labels.items():
                    if label in names:observed[key]=value;observed_fields[key]=field
        for endpoint,value in range_dates.items():
            observed['start_date' if endpoint=='start' else 'end_date']=value
        # SF creates a blank education row with a disabled level preset. Treat
        # that preset as a row template only when every other field is empty.
        # It may bind only when the profile has one completed record at that
        # level; an ordinary populated row still needs its school/major/dates.
        if (set(observed)=={'education_level'} and observed_fields['education_level'].get('disabled') is True):
            level=_education_level(observed['education_level'])
            completed=[(binding,record) for binding,record in candidates
                       if _education_level(record.get('education_level'))==level
                       and (record.get('graduation_type')=='毕业' or record.get('degree_award_date'))]
            if len(completed)==1:return 'matched',completed[0]
            return 'ambiguous',None
        matches=[]
        for binding,record in candidates:
            evidence=0;compatible=True
            for key,value in observed.items():
                canonical=record.get(key)
                if not canonical:continue
                left=_education_level(value) if key=='education_level' else _month(value) if key.endswith('_date') else value
                right=_education_level(canonical) if key=='education_level' else _month(canonical) if key.endswith('_date') else str(canonical).strip()
                if left!=right:compatible=False;break
                evidence+=1
            if compatible and evidence:matches.append((binding,record))
        return ('matched',matches[0]) if len(matches)==1 else ('ambiguous',None)
    matches=[]
    for binding,record in candidates:
        identities=[record.get(k) for k in ('school_name','company','organization','name','language','venue') if record.get(k)]
        if not any(str(identity) in values for identity in identities):
            continue
        start=record.get('start_date')
        observed_start=range_dates.get('start')
        if observed_start and start and _month(start) != _month(observed_start):
            continue
        matches.append((binding,record))
    return ('matched',matches[0]) if len(matches)==1 else ('ambiguous',None)


def bind_opened_record(profile, module, snapshot, manifest, results):
    """Allocate a confirmed empty editor, or match an existing record by identity."""
    if module.get('mapping_context',{}).get('record_id'):return None
    kind=collection_kind(module.get('module_label',''))
    # A named award editor is a record, while an unstructured awards summary
    # still needs its existing coverage review. Never allocate a blank award
    # row by ordering or guess which year's same-name scholarship it denotes.
    named_award=any(word in module.get('module_label','') for word in ('获奖','奖项','奖学金'))
    if kind is None and named_award:kind='awards'
    identity_labels={'education':{'学校名称','学校','学校全称','毕业院校'},
                     'research':{'论文名称','论文题目'},
                     'employment':{'公司名称','实习单位','单位名称'},
                     'projects':{'项目名称'},'awards':{'奖项名称','获奖名称','奖学金名称'}}
    fields=snapshot.get('fields',[])
    if kind not in identity_labels or sum(f.get('label','').strip(' *:：') in identity_labels[kind] for f in fields)!=1:
        return None  # A whole repeater is not one record editor.
    used={(m.get('mapping_context',{}).get('record_collection'),m.get('mapping_context',{}).get('record_id'))
          for m in manifest['modules'] if m['id']!=module['id']}
    used.update((r.get('current',{}).get('mapping_context',{}).get('record_collection'),
                 r.get('current',{}).get('mapping_context',{}).get('record_id'))
                for mid,r in results.items() if mid!=module['id'])
    candidates,_=records_for(profile,kind,module['module_label'])
    candidates=[x for x in candidates if (x[0]['record_collection'],x[0]['record_id']) not in used]
    if kind=='awards':
        name=next(f for f in fields if f.get('label','').strip(' *:：') in identity_labels[kind]).get('value')
        matches=[item for item in candidates if isinstance(name,str) and name.strip() and item[1].get('name')==name.strip()]
        return copy.deepcopy(matches[0][0]) if len(matches)==1 else None
    state,match=row_match({'snapshot':snapshot},candidates,kind)
    if state=='blank' and candidates and kind!='awards':return copy.deepcopy(candidates[0][0])
    return copy.deepcopy(match[0]) if state=='matched' else None


def plan_page(profile, inventory, target):
    if inventory['url'] != target['url']:
        raise ValueError('inventory_target_changed')
    if (inventory.get('framework') or {}).get('sections'):
        return plan_framework(profile, inventory, target)
    modules=[]; scopes=[]; gaps=[]; exclusions=[]; expected=[]; preserved=[]
    def relative(root,selector):
        return ':scope'+selector[len(root):] if selector.startswith(root+' > ') else selector
    def add(scope, selector, label, binding=None, **extra):
        module={'id':f'module-{len(modules)}','selector':selector,'module_label':label,
                'page_order':len(modules),'save_scope':scope['id'],'capture':{'controlDiagnostics':True},**extra}
        if binding: module['mapping_context']=binding
        modules.append(module)
        return module
    for scope in inventory['scopes']:
        if scope['state']=='collapsed':
            preserved.append({'scope':scope['id'],'reason':'existing_collapsed_card_requires_audit'})
            continue
        if not scope.get('save_selector'):
            gaps.append({'scope':scope['id'],'reason':'save_or_declaration_boundary_unclassified','controls':scope['field_count']})
            continue
        capture={'saveControl':relative(scope['selector'],scope['save_selector'])}
        if scope['save_label']=='保存':
            capture['savedSignal']={'kind':'module_readback','selector':scope['selector'],
                                    'editSelector':'form' if scope['editor_selector'].endswith(' form') else scope['editor_selector']}
        scopes.append({'id':scope['id'],'selector':scope['selector'],'mode':'explicit',
                       'evidence':'program DOM inventory: scoped save control and rendered order', 'capture':capture})
        for unit in scope['units']:
            if 'group' not in unit:
                add(scope,unit['selector'],unit['label'])
                continue
            group=next(g for g in scope['groups'] if g['selector']==unit['group'])
            kind=collection_kind(group['label'],scope['label'])
            # A collection remains identifiable when its add button disappears
            # at capacity. Use the row's field signature, not the add affordance.
            if kind is None:
                labels={f.get('label','').strip(' *:：') for row in group['rows']
                        for f in row.get('snapshot',{}).get('fields',[])}
                if '学历' in labels and labels.intersection({'学校全称','学校名称','学校'}):
                    kind='education'
                elif '会议/期刊' in labels:
                    kind='research'
                elif '语言能力' in labels:
                    kind='languages'
                if kind:
                    group={**group,'label':{'education':'教育情况','research':'论文发表信息','languages':'语言能力'}[kind]}
            if kind is None:
                gaps.append({'scope':scope['id'],'reason':'unknown_collection','label':group['label']})
                for row in group['rows']: add(scope,row['selector'],group['label'],hold_reason='unknown_collection')
                continue
            candidates, omitted=records_for(profile,kind,group['label'])
            group_start=len(modules)
            exclusions.extend(omitted)
            expected.extend({**binding,'scope':scope['id']} for binding,_ in candidates)
            remaining=list(candidates); blanks=[]; unresolved_existing=False
            for row in group['rows']:
                status, match=row_match(row,remaining,kind)
                if status=='matched':
                    add(scope,row['selector'],group['label'],match[0]);remaining.remove(match)
                elif status=='blank':
                    blanks.append(row)
                else:
                    unresolved_existing=True
                    add(scope,row['selector'],group['label'],hold_reason='existing_record_identity_unresolved')
                    gaps.append({'scope':scope['id'],'reason':'existing_record_identity_unresolved','selector':row['selector']})
            # Reuse already open empty rows before adding exactly the missing records.
            count=len(group['rows'])
            for binding, record in remaining:
                if unresolved_existing:
                    gaps.append({**binding,'scope':scope['id'],'reason':'existing_identity_must_be_resolved_before_add'})
                    continue
                if blanks:
                    add(scope,blanks.pop(0)['selector'],group['label'],binding)
                elif group.get('add_selector'):
                    selector=group['selector']+f' > .table-field-tiled-item:nth-child({count+1})'
                    add(scope,selector,group['label'],binding,open_mode='add',inline_repeater=True,
                        collection_selector=group['container_selector'],record_selector=relative(group['container_selector'],group['selector'])+' > .table-field-tiled-item',
                        add_control_selector=relative(group['container_selector'],group['add_selector']),add_label=group['add_label'],expected_record_count=count)
                    count+=1
                else:
                    gaps.append({**binding,'scope':scope['id'],'reason':'add_control_missing'})
            for row in blanks:
                add(scope,row['selector'],group['label'],hold_reason='no_eligible_canonical_record')
            order={row['selector']:i for i,row in enumerate(group['rows'])}
            modules[group_start:]=sorted(modules[group_start:],key=lambda m:order.get(m['selector'],m.get('expected_record_count',count)))
        # No scope is silently treated as complete just because no supported unit was found.
        if not any(m['save_scope']==scope['id'] for m in modules):
            add(scope,scope['selector'],scope['label'],hold_reason='scope_has_no_supported_units')
    # Existing rows and additions are ordered by their observed position; all additions
    # belong immediately after the final observed row of their collection.
    for index,module in enumerate(modules):module.update(page_order=index,id=f'module-{index}')
    return {'target':copy.deepcopy(target),'discovery_evidence':'program full-page DOM inventory',
            'modules':modules,'save_scopes':scopes,'program_inventory':True,
            'coverage':{'expected_records':expected,'gaps':gaps,'excluded_records':exclusions,
                        'preserved_scopes':preserved,'unowned_controls':inventory.get('unowned_controls',[]),
                        'unsupported_frames':inventory.get('unsupported_frames',[])}}


def plan_framework(profile, inventory, target):
    """Plan observed component sections; absent save controls retain real drafts."""
    framework=inventory['framework'];modules=[];scopes=[];gaps=[];expected=[];excluded=[]
    global_save=framework.get('save')
    if global_save:
        scopes.append({'id':'page','selector':'body','mode':'explicit',
                       'evidence':'observed page save: '+global_save['label'],
                       'capture':{'saveControl':global_save['selector']}})
    for section in framework['sections']:
        label=section.get('label') or '未命名栏目'
        fields=section.get('snapshot',{}).get('fields',[])
        labels=' '.join(f.get('label','') for f in fields)
        kind=collection_kind(label)
        if not kind and '姓名' not in labels and '学历' in labels and '学校' in labels:kind='education';label='教育经历'
        if not kind and '姓名' not in labels and ('公司名称' in labels or '实习单位' in labels):kind='employment';label='实习经历'
        if not kind and '姓名' not in labels and '项目名称' in labels:kind='projects';label='项目经历'
        if label=='未命名栏目' and '姓名' in labels:label='基本信息'
        sid='page' if global_save else section['id']
        if not global_save:
            scopes.append({'id':sid,'selector':section['selector'],'mode':'draft',
                           'evidence':'observed section without verified draft-save control','capture':{}})
            if framework['family'] in {'sf','oppo'} and (section.get('edit_selector') or section.get('save_selector')):
                scopes[-1].update(mode='explicit',evidence='previously observed component editor with exact 保存 control',
                    capture={'saveControl':'button,.normal-btn','saveLabel':'保存',
                             'savedSignal':{'kind':'module_readback','selector':section['selector'],
                                            'editSelector':'.normal-btn:not(.transparent)' if framework['family']=='sf' else '.form__button,.layout-edit--list__button'}})
        def add(selector,binding=None,**extra):
            m={'id':'module-'+str(len(modules)),'page_order':len(modules),'selector':selector,
               'module_label':label,'save_scope':sid,'capture':{'controlDiagnostics':True,**(scopes[-1].get('capture',{}) if not global_save else {})},**extra}
            if binding:m['mapping_context']=binding
            elif '姓名' in labels:m['mapping_context']={'module_type':'personal'}
            modules.append(m)
        if kind:
            candidates,omitted=records_for(profile,kind,label);excluded.extend(omitted)
            expected.extend({**b,'scope':sid} for b,_ in candidates)
            if (framework['family']=='sf' and section.get('empty_form_collection') is True
                    and not fields and not section.get('records') and section.get('add_selector') and candidates):
                # Discover just one editor. Its real fields/save control must be
                # observed after the existing add command; remaining records wait
                # for evidence of the site's saved-record structure.
                capture={'saveControl':'button,.normal-btn','saveLabel':'保存',
                         'savedSignal':{'kind':'module_readback','selector':section['selector'],
                                        'editSelector':'.normal-btn:not(.transparent)'}}
                scopes[-1].update(mode='explicit',capture=capture,
                    evidence='SF empty form collection: observed unique add; save control must be verified after opening')
                add(section['selector'],candidates[0][0],open_mode='add',
                    collection_selector=section['selector'],record_selector='form',
                    add_control_selector=section['add_selector'],add_label=section['add_label'],
                    expected_record_count=0,discovered_empty_collection=True)
                gaps.extend({**binding,'scope':sid,'reason':'additional_records_require_saved_structure'}
                            for binding,_ in candidates[1:])
                continue
            rows=section.get('records') or ([{'selector':section['selector'],'snapshot':section.get('snapshot',{})}] if fields else [])
            if not rows and section.get('edit_selector'):
                add(section['selector'],{'module_type':kind})
            remaining=list(candidates);blanks=[];unresolved=False
            for row in rows:
                state,match=row_match(row,remaining,kind)
                if state=='matched':add(row['selector'],match[0]);remaining.remove(match)
                elif state=='blank':blanks.append(row)
                else:unresolved=True;add(row['selector'],hold_reason='existing_record_identity_unresolved')
            count=len(rows)
            for binding,record in remaining:
                if blanks:add(blanks.pop(0)['selector'],binding)
                elif not unresolved and section.get('add_selector') and section.get('record_selector'):
                    relative=section['record_selector'].removeprefix(':scope').strip()
                    sibling_filter=relative.split('>')[-1].strip()
                    selector=section['selector']+' '+relative+f':nth-child({count+1} of {sibling_filter})'
                    add(selector,binding,open_mode='add',inline_repeater=True,
                        collection_selector=section['selector'],record_selector=section['record_selector'],
                        add_control_selector=section['add_selector'],add_label=section['add_label'],expected_record_count=count)
                    if framework['family']=='feishu' and not rows:
                        modules[-1]['supported_empty_repeater']=True
                    count+=1
                else:gaps.append({**binding,'scope':sid,'reason':'record_add_not_yet_supported'})
            for row in blanks:add(row['selector'],hold_reason='no_eligible_canonical_record')
        elif fields or section.get('edit_selector'):add(section['selector'])
        else:add(section['selector'],hold_reason='editor_or_empty_collection_requires_discovery')
        if any(word in label for word in ('大赛','荣誉','获奖','竞赛')):
            gaps.append({'scope':sid,'reason':'summary_record_coverage_requires_review',
                         'record_ids':[r['record_id'] for key in ('awards','competitions') for r in profile.get(key,[])]})
    used={m['save_scope'] for m in modules};scopes=[s for s in scopes if s['id'] in used]
    return {'target':copy.deepcopy(target),'discovery_evidence':'live component section inventory',
            'program_inventory':True,'modules':modules,'save_scopes':scopes,
            'coverage':{'expected_records':expected,'gaps':gaps,'excluded_records':excluded,
                        'unsupported_frames':inventory.get('unsupported_frames',[])}}


def coverage_gaps(manifest, results, omitted=()):
    coverage=manifest.get('coverage',{})
    existing={(m['save_scope'],m.get('mapping_context',{}).get('record_collection'),
               m.get('mapping_context',{}).get('record_id'))
              for m in manifest['modules'] if not m.get('hold_reason') and not m.get('open_mode')
              and m.get('mapping_context',{}).get('record_id')}
    gaps=[gap for gap in coverage.get('gaps',[])
          if not (gap.get('reason')=='record_add_not_yet_supported'
                  and (gap.get('scope'),gap.get('record_collection'),gap.get('record_id')) in existing)]
    gaps.extend({'reason':'unowned_control',**item} for item in coverage.get('unowned_controls',[]))
    gaps.extend({'reason':'unsupported_frame','selector':item} for item in coverage.get('unsupported_frames',[]))
    gaps.extend({'reason':'preserved_scope_not_audited',**item} for item in coverage.get('preserved_scopes',[]))
    for module in manifest['modules']:
        if module['id'] not in results or results[module['id']].get('coverage_hold'):
            gaps.append({'module_id':module['id'],'reason':results.get(module['id'],{}).get('coverage_hold','module_not_processed')})
    gaps.extend(omitted)
    processed={(m['save_scope'],m.get('mapping_context',{}).get('record_collection'),m.get('mapping_context',{}).get('record_id'))
               for m in manifest['modules'] if m['id'] in results and not results[m['id']].get('coverage_hold')}
    for record in coverage.get('expected_records',[]):
        if (record.get('scope'),record['record_collection'],record['record_id']) not in processed:
            gaps.append({**record,'reason':'canonical_record_not_completed'})
    return gaps


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--inventory',required=True);parser.add_argument('--target',required=True)
    parser.add_argument('--profile');parser.add_argument('--output',required=True);args=parser.parse_args()
    profile_path=args.profile or json.loads(Path('private/local-config.json').read_text(encoding='utf-8'))['profile']
    plan=plan_page(json.loads(Path(profile_path).read_text(encoding='utf-8')),
                   json.loads(Path(args.inventory).read_text(encoding='utf-8')),json.loads(args.target))
    Path(args.output).write_text(json.dumps(plan,ensure_ascii=False,indent=2),encoding='utf-8')
    print(json.dumps({'manifest':str(Path(args.output).resolve()),'modules':len(plan['modules']),'coverage':plan['coverage']},ensure_ascii=False))
