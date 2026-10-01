"""Append-only recovery of local preparation branches; never resolves browser writes."""
import copy
from langgraph.checkpoint.base import create_checkpoint
from .contracts import ContractError


def recover_preparation(saver, config, folder):
    saved=saver.get_tuple(config)
    checkpoint=copy.deepcopy(saved.checkpoint)
    values=checkpoint['channel_values']
    pending=saved.pending_writes or []
    branches={k for k in values if k.startswith('branch:')}|{
        channel for _,channel,_ in pending if channel.startswith('branch:')}
    allowed={'branch:to:prepare_module_edit','branch:to:prepare_diagnostic',
             'branch:to:prepare_control','branch:to:reconcile_control',
             'branch:to:read_diagnostic','branch:to:execute_module_edit'}
    if not branches or not branches<=allowed or values.get('command'):
        raise ContractError('only_undispatched_preparation_can_be_recovered')
    allowed_pending={'command','status','diagnostic_previous','control_request','control_methods','__error__'}|allowed
    for _,channel,value in pending:
        if channel=='command' and value:
            if value.get('kind') not in {'observe','edit_module','control_fill'} or (folder/'writer'/(value['command_id']+'.json')).exists():
                raise ContractError('dispatched_command_cannot_be_discarded')
        elif channel not in allowed_pending:
            raise ContractError('unexpected_pending_preparation_write')
    for key in branches:
        values.pop(key, None)
    step=saved.metadata.get('step',0)+1
    new=create_checkpoint(checkpoint,None,step)
    # The old checkpoint and its pending writes remain available for audit.
    return saver.put(saved.config,new,{'source':'update','step':step,'parents':{},
        'preparation_recovery_of':checkpoint['id']}, {})
