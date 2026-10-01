"""Resolve previously held existing rows from a fresh, exact identity match."""
import copy
import json
import time
from pathlib import Path

from .contracts import ContractError
from .page_planner import plan_page
from .scope_isolation import assert_settled_history


def rebind_held_records(values, next_nodes, folder, inventory, basis, renew_seconds, *, writer_lock_held=False):
    if (not writer_lock_held or next_nodes or values.get('command')
            or values.get('status') not in {'incomplete_coverage','budget_exhausted'}
            or not values['manifest'].get('program_inventory') or not basis.strip()
            or type(renew_seconds) is not int or not 1<=renew_seconds<=1800):
        raise ContractError('held_record_rebind_requires_stopped_original_application')
    target=values['manifest']['target']
    if (inventory.get('url')!=target['url']
            or any(inventory.get('target',{}).get(key)!=target.get(key) for key in ('browser_id','tab_id','url'))
            or not 0<=time.time()-inventory.get('observed_at',0)<120):
        raise ContractError('held_record_rebind_requires_fresh_same_tab_inventory')
    assert_settled_history(values,folder)
    from .application_cli import check_prior
    blockers=check_prior(folder).get('blockers',[])
    def read_only_add(journal):
        receipt=journal.get('receipt') or {}
        steps=journal.get('instrumentation') or []
        return (journal.get('kind')=='add_module_record' and receipt.get('settled') is True
                and receipt.get('status')=='unconfirmed' and not receipt.get('results')
                and receipt.get('evidence',{}).get('reason')=='open_record_count_changed'
                and bool(steps) and all(s.get('method') in {'count','evaluate'}
                    and s.get('status')=='returned' for s in steps))
    if any(not read_only_add(json.loads(Path(b['journal']).read_text(encoding='utf-8')))
           for b in blockers):
        raise ContractError('held_record_rebind_requires_save_reconciliation')
    fresh=plan_page(values['profile'],inventory,target)
    manifest=copy.deepcopy(values['manifest']);results=copy.deepcopy(values['results'])
    used={(m.get('mapping_context',{}).get('record_collection'),m.get('mapping_context',{}).get('record_id'))
          for m in manifest['modules'] if m.get('mapping_context',{}).get('record_id')}
    changed=[]
    for index,module in enumerate(manifest['modules']):
        parsed_add=False
        if module.get('hold_reason')!='existing_record_identity_unresolved':
            if module.get('open_mode')!='add' or results.get(module['id'],{}).get('coverage_hold')!='module_add_blocked':continue
            histories=[h for h in values.get('module_add_history',[])
                       if h.get('command',{}).get('module_id')==module['id']]
            if not histories:continue
            command=histories[-1]['command']
            journal=json.loads((folder/'writer'/(command['command_id']+'.json')).read_text(encoding='utf-8'))
            if journal.get('receipt')!=histories[-1].get('receipt') or not read_only_add(journal):continue
            parsed_add=True
        scope=module['save_scope']
        if values.get('scopes',{}).get(scope)=='saved_confirmed':continue
        candidates=[m for m in fresh['modules'] if (parsed_add and m.get('mapping_context')==module.get('mapping_context')
                    or not parsed_add and m['selector']==module['selector'])
                    and m['module_label']==module['module_label'] and m['save_scope']==scope
                    and not m.get('hold_reason') and not m.get('open_mode')
                    and m.get('mapping_context',{}).get('record_id')]
        if len(candidates)!=1:continue
        old_scope=next(s for s in manifest['save_scopes'] if s['id']==scope)
        new_scopes=[s for s in fresh['save_scopes'] if s['id']==scope]
        if len(new_scopes)!=1 or any(new_scopes[0].get(k)!=old_scope.get(k) for k in ('selector','mode')):
            continue
        binding=candidates[0]['mapping_context'];identity=(binding['record_collection'],binding['record_id'])
        if identity in used and not parsed_add:continue
        if parsed_add and any(m['id']!=module['id'] and m.get('mapping_context',{}).get('record_collection')==identity[0]
                              and m.get('mapping_context',{}).get('record_id')==identity[1] for m in manifest['modules']):continue
        selector=candidates[0]['selector']
        rows=[row for section in (inventory.get('framework') or {}).get('sections',[])
              for row in section.get('records',[]) if row['selector']==selector]
        if len(rows)!=1 or not rows[0].get('snapshot',{}).get('fields'):continue
        prior=copy.deepcopy(module)
        module.pop('hold_reason',None);module['mapping_context']=copy.deepcopy(binding)
        if parsed_add:
            module['selector']=selector
            for key in ('open_mode','inline_repeater','collection_selector','record_selector','add_control_selector','add_label','expected_record_count'):
                module.pop(key,None)
        module['record_rebind_expected']=copy.deepcopy(rows[0]['snapshot']['fields'])
        changed.append({'index':index,'module_id':module['id'],'previous_module':prior,
                        'previous_result':results.pop(module['id'],None),'binding':copy.deepcopy(binding)})
        used.add(identity)
    if not changed:raise ContractError('no_exact_held_record_identity_match')
    rebound={(m['save_scope'],m['selector']) for m in manifest['modules']
             if m['id'] in {item['module_id'] for item in changed}}
    coverage=manifest.setdefault('coverage',{})
    coverage['gaps']=[gap for gap in coverage.get('gaps',[])
                     if not (gap.get('reason')=='existing_record_identity_unresolved'
                             and (gap.get('scope'),gap.get('selector')) in rebound)]
    from .application import application_state
    application_state(values['profile'],manifest,allow_save=values.get('allow_save',False))
    affected={item['module_id'] for item in changed};scopes={m['save_scope'] for m in manifest['modules'] if m['id'] in affected}
    now=time.time()
    return {'manifest':manifest,'results':results,'index':changed[0]['index'],'command':None,'status':'advance',
            'current':{},'control_diagnostics':{},'mapping_cache':{k:v for k,v in values.get('mapping_cache',{}).items() if k not in affected},
            'scope_reviews':{k:v for k,v in values.get('scope_reviews',{}).items() if k not in scopes},
            'deadline':now+renew_seconds,'inventory_history':values.get('inventory_history',[])+[
                {'at':now,'kind':'held_record_identity_rebind','basis':basis,'changes':changed,'inventory':copy.deepcopy(inventory)}],
            'budget_history':values.get('budget_history',[])+[
                {'at':now,'basis':basis,'previous_deadline':values['deadline'],'deadline':now+renew_seconds}]}
