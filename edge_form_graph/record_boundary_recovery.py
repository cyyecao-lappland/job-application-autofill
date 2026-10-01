"""Split an accidentally shared SF education editor using observed row boundaries.

Only dates proved written by this run into a newly blank linked row may be
disregarded for identity matching. Actual values remain unchanged until the
normal graph fills the newly bound record.
"""
import copy
import json
import time
from .contracts import ContractError
from .page_planner import records_for, row_match
from .scope_isolation import assert_settled_history


def prepare_record_split(values, next_nodes, folder, inventory, module_id, basis, *, writer_lock_held=False):
    command=values.get('command') or {}
    pending_read=(tuple(next_nodes)==('observe',) and values.get('status')=='awaiting_observation'
        and command.get('kind')=='observe' and command.get('command_id')
        and not (folder/'writer'/(command['command_id']+'.json')).exists())
    if (not writer_lock_held or not basis.strip() or not values['manifest'].get('program_inventory')
        or not pending_read and (next_nodes or command or values['status']!='incomplete_coverage')):
        raise ContractError('record_split_requires_quiescent_writer')
    if inventory.get('url')!=values['manifest']['target']['url'] or not 0<=time.time()-inventory.get('observed_at',0)<120:
        raise ContractError('record_split_requires_fresh_same_page_inventory')
    assert_settled_history(values,folder)
    matches=[(i,m) for i,m in enumerate(values['manifest']['modules']) if m['id']==module_id]
    if len(matches)!=1:raise ContractError('record_split_module_not_unique')
    index,old=matches[0];scope=old['save_scope']
    if (values.get('scopes',{}).get(scope)=='saved_confirmed' or old.get('mapping_context',{}).get('record_collection')!='/education'
        or not old.get('discovered_empty_collection')):raise ContractError('record_split_requires_unsaved_discovered_education')
    framework=inventory.get('framework') or {}
    sections=[s for s in framework.get('sections',[]) if s['selector']==old['selector']]
    if framework.get('family')!='sf' or len(sections)!=1:raise ContractError('record_split_section_changed')
    section=sections[0];rows=section.get('records',[])
    if len(rows)!=2:raise ContractError('record_split_requires_two_observed_rows')
    snapshots=[]
    for path in (folder/'writer').glob('*.json'):
        journal=json.loads(path.read_text(encoding='utf-8'));receipt=journal.get('receipt') or {};snap=receipt.get('snapshot') or {}
        if snap.get('module_id')==module_id and snap.get('module_selector')==old['selector']:
            snapshots.append((journal.get('started_at',0),journal['command_id'],journal.get('kind'),receipt,
                              receipt.get('settled') is True))
    snapshots.sort(key=lambda x:x[0])
    candidates,_=records_for(values['profile'],'education',old['module_label'])
    primary=next((r for b,r in candidates if b['record_id']==old['mapping_context']['record_id']),None)
    if primary is None:raise ContractError('record_split_primary_missing')
    bindings=[];proofs=[];remaining=list(candidates)
    section_fields=section.get('snapshot',{}).get('fields',[])
    for row_index,row in enumerate(rows):
        # Keep the already identifiable primary row intact. A projection is
        # only justified for the other row when its dates prevent matching.
        state,match=row_match(row,remaining,kind='education')
        if state=='matched':
            bindings.append(match[0]);remaining.remove(match)
            continue
        projected=copy.deepcopy(row);row_proofs=[]
        owned=[f for f in section_fields if f.get('record_container')==row['selector']]
        if not owned:raise ContractError('record_split_ownership_missing')
        level=next((f.get('value') for f in owned if f.get('label')=='学历'),None)
        for field in projected.get('snapshot',{}).get('fields',[]):
            key={'入学时间':'start_date','毕业时间':'end_date'}.get(field.get('label'))
            if not key or '本科' not in str(level) or field.get('value')!=primary.get(key):continue
            originals=[f for f in owned if f.get('label')==field['label'] and f.get('value')==field['value']]
            if len(originals)!=1:raise ContractError('record_split_date_identity_ambiguous')
            fid=originals[0]['id']
            seen=[(at,cid,jkind,r,settled,next(f for f in r['snapshot']['fields'] if f['id']==fid))
                for at,cid,jkind,r,settled in snapshots
                if (jkind in {'observe','fill'}
                    and any(f.get('id')==fid for f in r.get('snapshot',{}).get('fields',[])))]
            fills=[(at,cid,r) for at,cid,jkind,r,settled in snapshots
                if jkind=='fill' and r.get('kind')=='fill' and r.get('status') in {'completed','partial'}
                and settled and r.get('snapshot')
                and any(f.get('id')==fid and f.get('value')==field['value'] for f in r['snapshot'].get('fields',[]))
                and sum(x.get('id')==fid and x.get('status')=='written' for x in r.get('results',[]))==1]
            if (not seen or not seen[0][4] or seen[0][5].get('value')!='' or not fills
                    or seen[0][0]>=fills[0][0] or seen[0][2] not in {'observe','fill'}):
                raise ContractError('record_split_requires_proven_program_date')
            third=[x for x in seen if x[0]>seen[0][0] and x[5].get('value') not in {'',field['value']}]
            later=[x for x in seen if x[0]>fills[-1][0] and x[2]=='observe' and x[4]
                   and x[3].get('status')=='completed']
            if third or not later or any(x[5].get('value')!=field['value'] for x in later):
                raise ContractError('record_split_date_observation_conflict')
            row_proofs.append({'field_id':fid,'label':field['label'],'key':key,'observed':field['value'],
                'blank_observation_command':seen[0][1],'write_command':fills[-1][1]})
            field['value']=''  # Identity matching projection only; never a page write.
        if ({p['key'] for p in row_proofs} != {'start_date','end_date'}
                or len(row_proofs)!=2 or len({p['field_id'] for p in row_proofs})!=2):
            raise ContractError('record_split_requires_primary_and_two_proven_dates')
        proofs.extend(row_proofs)
        state,match=row_match(projected,remaining,kind='education')
        if state!='matched':raise ContractError('record_split_identity_unresolved')
        bindings.append(match[0]);remaining.remove(match)
    if (len(proofs)!=2 or len({p['field_id'] for p in proofs})!=2
            or bindings[0]!=old['mapping_context']):
        raise ContractError('record_split_requires_primary_and_two_proven_dates')
    manifest=copy.deepcopy(values['manifest']);new=[]
    for i,(row,binding) in enumerate(zip(rows,bindings)):
        mid=module_id if i==0 else module_id+'-linked-'+str(i)
        if i and any(m['id']==mid for m in manifest['modules']):raise ContractError('record_split_id_exists')
        new.append({'id':mid,'selector':row['selector'],'module_label':old['module_label'],
            'mapping_context':binding,'save_scope':scope,'capture':{'controlDiagnostics':True},
            'record_split_expected':copy.deepcopy(row['snapshot']['fields'])})
    manifest['modules'][index:index+1]=new
    for i,m in enumerate(manifest['modules']):m['page_order']=i;m.pop('snapshot',None)
    results=copy.deepcopy(values['results']);previous=results.pop(module_id,None)
    from .application import application_state
    application_state(values['profile'],manifest,allow_save=values.get('allow_save',False))
    now=time.time()
    return {'manifest':manifest,'results':results,'index':index,'command':None,'status':'advance',
        'current':{},'scope_reviews':{},'mapping_cache':{},'control_diagnostics':{},'recovery_reason':None,
        'deadline':now+1800,'inventory_history':values.get('inventory_history',[])+[{
            'at':now,'basis':basis,'kind':'record_boundary_split','module_id':module_id,
            'original_module':copy.deepcopy(old),'original_result':previous,'inventory':copy.deepcopy(inventory),
            'bindings':bindings,'program_date_proofs':proofs,'superseded_undispatched_observe':copy.deepcopy(command)}],
        'budget_history':values.get('budget_history',[])+[{'previous_deadline':values['deadline'],'deadline':now+1800,'basis':basis,'at':now}]}
