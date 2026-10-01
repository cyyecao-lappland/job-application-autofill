"""Renew one never-dispatched add after rechecking its exact collection baseline."""
import copy
import time
from .contracts import ContractError
from .scope_isolation import assert_settled_history


def renew_undispatched_add(values, next_nodes, folder, inventory, basis, seconds, *, writer_lock_held=False):
    command = values.get('command') or {}
    if (not writer_lock_held or tuple(next_nodes) != ('add_module_record',)
            or values.get('status') != 'awaiting_module_add' or command.get('kind') != 'add_module_record'
            or not command.get('command_id') or not basis.strip() or type(seconds) is not int
            or not 1 <= seconds <= 1800
            or (folder/'writer'/(command['command_id']+'.json')).exists()):
        raise ContractError('add_renew_requires_original_undispatched_command')
    target = values['manifest']['target']
    if (command.get('target') != target or inventory.get('url') != target['url']
            or any(inventory.get('target', {}).get(k) != target.get(k) for k in ('browser_id', 'tab_id', 'url'))
            or not 0 <= time.time()-inventory.get('observed_at', 0) < 120):
        raise ContractError('add_renew_requires_fresh_same_tab_inventory')
    assert_settled_history(values, folder)
    section = [s for s in (inventory.get('framework') or {}).get('sections', [])
               if s['selector'] == command.get('collection_selector')]
    if (len(section) != 1 or section[0].get('record_selector') != command.get('record_selector')
            or section[0].get('add_selector') != command.get('add_control_selector')
            or section[0].get('add_label') != command.get('add_label')
            or len(section[0].get('records', [])) != command.get('expected_record_count')):
        raise ContractError('add_renew_collection_baseline_changed')
    # Only the evidenced empty baseline is supported here. Nonempty collections
    # require stable sibling identities before another record may be appended.
    if command['expected_record_count'] != 0 or section[0].get('controls') != 0:
        raise ContractError('add_renew_requires_observed_empty_collection')
    now=time.time();deadline=now+seconds
    return {'command': {**command, 'deadline': deadline}, 'deadline': deadline,
        'budget_history': values.get('budget_history', [])+[{'kind':'undispatched_add_renewal',
            'at':now, 'basis':basis, 'previous_deadline':values['deadline'], 'deadline':deadline,
            'previous_command':copy.deepcopy(command), 'inventory':copy.deepcopy(inventory)}]}
