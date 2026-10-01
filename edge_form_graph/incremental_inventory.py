"""Extend an original application with newly supported, still-empty collections."""
import copy
import time
from .contracts import ContractError
from .page_planner import plan_page
from .application import application_state
from .scope_isolation import assert_settled_history


def extend_empty_collections(values,next_nodes,folder,inventory,basis,renew_seconds,*,writer_lock_held=False):
    if not writer_lock_held or not basis.strip() or type(renew_seconds) is not int or not 1<=renew_seconds<=1800:
        raise ContractError('inventory_extension_requires_writer_lock_and_basis')
    command=values.get('command') or {}
    pending_read=(tuple(next_nodes)==('observe',) and values['status']=='awaiting_observation'
        and command.get('kind')=='observe' and command.get('command_id')
        and not (folder/'writer'/(command['command_id']+'.json')).exists())
    if not pending_read and (next_nodes or command or values['status'] not in {'incomplete_coverage','budget_exhausted'}):
        raise ContractError('inventory_extension_requires_quiescent_boundary')
    remaining=values['manifest']['modules'][values['index']:]
    if remaining and (not pending_read or any(m['id'] not in values.get('results',{})
            and values.get('scopes',{}).get(m['save_scope'])!='saved_confirmed' for m in remaining)):
        raise ContractError('inventory_extension_must_not_skip_unprocessed_modules')
    if not values['manifest'].get('program_inventory') or not 0<=time.time()-inventory.get('observed_at',0)<120:
        raise ContractError('inventory_extension_requires_fresh_program_inventory')
    assert_settled_history(values,folder)
    from .application_cli import check_prior
    if check_prior(folder).get('blockers'):raise ContractError('inventory_extension_requires_save_reconciliation')
    plan=plan_page(values['profile'],inventory,values['manifest']['target'])
    manifest=copy.deepcopy(values['manifest'])
    old_selectors={s['selector'] for s in manifest['save_scopes']}
    old_ids={s['id'] for s in manifest['save_scopes']}
    incoming=[m for m in plan['modules'] if (m.get('discovered_empty_collection') is True or m.get('supported_empty_repeater') is True)
              and m['selector'] not in old_selectors and m['save_scope'] not in old_ids]
    if not incoming:raise ContractError('no_new_empty_collection')
    if any(m.get('supported_empty_repeater') for m in incoming) and any(
            inventory.get('target',{}).get(k)!=values['manifest']['target'].get(k)
            for k in ('browser_id','tab_id','url')):
        raise ContractError('empty_repeater_extension_requires_same_tab')
    index=len(manifest['modules']);added_scopes={m['save_scope'] for m in incoming}
    original_ids={m['id'] for m in manifest['modules']}
    for module in incoming:
        module=copy.deepcopy(module)
        mid='discovered-'+str(len(manifest['modules']))
        if mid in original_ids:raise ContractError('discovered_module_id_collision')
        module.update(id=mid,page_order=len(manifest['modules']))
        manifest['modules'].append(module)
    manifest['save_scopes'].extend(copy.deepcopy(s) for s in plan['save_scopes'] if s['id'] in added_scopes)
    coverage=manifest.setdefault('coverage',{})
    coverage['gaps']=[g for g in coverage.get('gaps',[]) if g.get('scope') not in added_scopes]
    coverage['gaps'].extend(copy.deepcopy(g) for g in plan.get('coverage',{}).get('gaps',[]) if g.get('scope') in added_scopes)
    application_state(values['profile'],manifest,allow_save=values.get('allow_save',False))
    now=time.time()
    return {'manifest':manifest,'index':index,'command':None,'status':'advance','deadline':now+renew_seconds,
        'current':{},'control_diagnostics':{},'mapping_cache':{},
        'inventory_history':values.get('inventory_history',[])+[{'at':now,'basis':basis,
            'previous_index':values['index'],'superseded_undispatched_observe':copy.deepcopy(command),
            'inventory':copy.deepcopy(inventory),'added_scopes':sorted(added_scopes)}],
        'budget_history':values.get('budget_history',[])+[{'previous_deadline':values['deadline'],
            'deadline':now+renew_seconds,'basis':basis,'at':now}]}
