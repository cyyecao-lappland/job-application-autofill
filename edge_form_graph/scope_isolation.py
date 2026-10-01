"""A settled unknown save holds its DOM scope, never authorizes a replay."""
import time
import copy
import json
from .contracts import ContractError


def validate_isolation(manifest,prior):
    proof=manifest.get('scope_isolation',{})
    if not manifest.get('program_inventory') or not 0 <= time.time()-proof.get('observed_at',0) < 120:
        return False
    blockers=prior.get('blockers',[])
    if not blockers or proof.get('target')!=manifest['target']:
        return False
    held=set()
    for blocker in blockers:
        if not blocker.get('settled') or blocker['kind'] not in {'save','save_scope'} or blocker.get('target')!=manifest['target']:
            return False
        evidence=next((x for x in proof.get('blocks',[]) if x['command_id']==blocker['command_id']),None)
        if not evidence or evidence.get('selector')!=blocker.get('module_selector') or not evidence.get('unique'):
            return False
        if set(evidence.get('scope_relations',{}))!={s['id'] for s in manifest['save_scopes']}:
            return False
        for scope, relation in evidence['scope_relations'].items():
            if relation=='contained':held.add(scope)
            elif relation!='disjoint':return False
    if not held:return False
    return all(m.get('hold_reason')=='prior_save_unknown' for m in manifest['modules'] if m['save_scope'] in held)


def assert_settled_history(values, folder):
    """Completion proof survives a revisit; a stopped graph alone is no proof."""
    for path in (folder/'writer').glob('*.json'):
        journal=json.loads(path.read_text(encoding='utf-8'));receipt=journal.get('receipt') or {}
        from .save_preflight_recovery import audited_preflight
        if audited_preflight(folder,journal):continue
        from .observation_completion import audited_observation
        if audited_observation(folder, journal):continue
        if receipt.get('settled') is not True:
            from .local_call_audit import audited_completion
            if not audited_completion(folder,journal):raise ContractError('defer_requires_settled_calls')
        add_completed=(receipt.get('status')=='completed' and bool(receipt.get('snapshot'))
                       and receipt.get('evidence',{}).get('record_editor_open') is True)
        if journal['kind']=='add_module_record' and not add_completed and any(x.get('method')=='click' for x in journal.get('instrumentation',[])):
            reconciled=any(h.get('command',{}).get('original_command_id')==journal.get('command_id')
                and h.get('command',{}).get('reconcile_only') is True
                and h.get('receipt',{}).get('settled') is True and h['receipt'].get('status')=='completed'
                and h['receipt'].get('evidence',{}).get('reconciliation_only') is True
                for h in values.get('module_add_history',[]))
            if not reconciled:raise ContractError('defer_requires_add_reconciliation')


def revisit_held(values, next_nodes, folder, module_id, basis, renew_seconds, *, writer_lock_held=False,
                 refresh_scope=False):
    command=values.get('command') or {}
    undispatched_observe=(writer_lock_held and tuple(next_nodes)==('observe',)
        and values['status']=='awaiting_observation' and command.get('kind')=='observe'
        and bool(command.get('command_id'))
        and not (folder/'writer'/(command['command_id']+'.json')).exists())
    if (not values['manifest'].get('program_inventory') or not undispatched_observe and
            (next_nodes or command or values['status'] not in {'budget_exhausted','incomplete_coverage'})):
        raise ContractError('revisit_requires_stopped_incomplete_inventory')
    if not basis.strip() or type(renew_seconds) is not int or not 1 <= renew_seconds <= 1800:
        raise ContractError('revisit_requires_explicit_budget_basis')
    assert_settled_history(values,folder)
    modules=values['manifest']['modules']
    matches=[(i,m) for i,m in enumerate(modules) if m['id']==module_id]
    if len(matches)!=1:raise ContractError('revisit_module_not_unique')
    index,module=matches[0];scope=module['save_scope']
    result=values.get('results',{}).get(module_id,{})
    if (module.get('hold_reason') or values.get('scopes',{}).get(scope)=='saved_confirmed'
            or not result.get('coverage_hold') or result['coverage_hold']=='needs_reconciliation'):
        raise ContractError('revisit_requires_unsaved_held_module')
    # Never reopen a save whose outcome is unresolved, even if its call ended.
    journals=[json.loads(path.read_text(encoding='utf-8')) for path in (folder/'writer').glob('*.json')]
    from .application_cli import reconciliation_parent
    resolved={}
    for journal in journals:
        receipt=journal.get('receipt') or {}
        if (journal.get('kind')=='reconcile_save' and receipt.get('settled') is True
                and receipt.get('status')=='saved' and receipt.get('evidence',{}).get('save_confirmed') is True):
            parent=reconciliation_parent(folder,journal.get('command_id'))
            if parent:resolved[parent]=journal
    for journal in journals:
        receipt=journal.get('receipt') or {}
        from .save_preflight_recovery import audited_preflight
        if audited_preflight(folder,journal):continue
        original=journal.get('command_id') if journal.get('kind') in {'save','save_scope'} else (
            reconciliation_parent(folder,journal.get('command_id')) if journal.get('kind')=='reconcile_save' else None)
        confirmation=resolved.get(original)
        if (confirmation and receipt.get('settled') is True
                and receipt.get('target')==confirmation['receipt'].get('target')
                and confirmation.get('started_at',0)>journal.get('started_at',0)):
            continue
        if journal.get('kind') in {'save','save_scope','verify_autosave','reconcile_save'} and not (
                receipt.get('status')=='saved' and receipt.get('settled') is True
                and receipt.get('evidence',{}).get('save_confirmed') is True):
            raise ContractError('revisit_requires_save_reconciliation')
    manifest=copy.deepcopy(values['manifest'])
    for m in manifest['modules']:m.pop('snapshot',None)
    results=copy.deepcopy(values['results']);results[module_id].pop('coverage_hold',None)
    refreshed_ids = [module_id]
    if refresh_scope:
        indices = [i for i, m in enumerate(modules) if m['save_scope'] == scope]
        if indices != list(range(min(indices), max(indices) + 1)):
            raise ContractError('revisit_review_scope_requires_contiguous_modules')
        if any(m.get('hold_reason') or results.get(m['id'], {}).get('coverage_hold') == 'needs_reconciliation'
               or results.get(m['id'], {}).get('status') in {'needs_reconciliation', 'recovery_blocked'}
               or any(item.get('status') in {'unknown', 'conflict'} for item in
                      (results.get(m['id'], {}).get('last_receipt') or {}).get('results', []))
               or any(item.get('status') == 'unknown'
                      for item in results.get(m['id'], {}).get('results', {}).values())
               for m in modules if m['save_scope'] == scope):
            raise ContractError('revisit_review_scope_requires_reconciled_modules')
        index = min(indices)
        refreshed_ids = [modules[i]['id'] for i in indices]
        for mid in refreshed_ids:
            if mid in results:
                results[mid].pop('coverage_hold', None)
                results[mid]['review'] = {}
                results[mid]['reviewed_revision'] = None
    now=time.time();deadline=now+renew_seconds
    return {'manifest':manifest,'index':index,'status':'module_blocked','command':None,
            'current':{},'control_diagnostics':{},'control_request':{},'scope_reviews':{},
            'mapping_cache':{},'results':results,'recovery_requested':False,'recovery_reason':None,
            'deadline':deadline,'budget_history':values.get('budget_history',[])+[{
                'previous_deadline':values['deadline'],'deadline':deadline,'basis':basis,'at':now}],
            'revisit_history':values.get('revisit_history',[])+[{
                'module_id':module_id,'previous_index':values['index'],'previous_status':values['status'],
                'superseded_undispatched_observe':copy.deepcopy(command) if undispatched_observe else None,
                'previous_result':copy.deepcopy(result),'basis':basis,'at':now,
                'refreshed_scope_modules':refreshed_ids}]}


def defer_independent(values, next_nodes, folder):
    if next_nodes or not values['manifest'].get('program_inventory'):
        raise ContractError('defer_requires_stopped_program_inventory')
    status=values['status']
    if status not in {'module_blocked','control_blocked','recovery_blocked','scope_draft','save_control_unverified','module_add_blocked','scope_review_blocked','needs_reconciliation'}:
        raise ContractError('defer_status_not_supported')
    assert_settled_history(values,folder)
    modules=values['manifest']['modules'];index=values['index'];scope=modules[index]['save_scope']
    results=copy.deepcopy(values['results']);next_index=index+1
    if status in {'scope_draft','save_control_unverified','scope_review_blocked','needs_reconciliation'}:
        while next_index<len(modules) and modules[next_index]['save_scope']==scope:next_index+=1
    for module in modules[index:next_index]:
        results.setdefault(module['id'],{})['coverage_hold']=status
    return {'index':next_index,'status':'advance','command':None,'results':results}
