"""Recover a settled chooser confirmation using fresh DOM evidence, never replay."""
import re
import time
from .contracts import ContractError

def reconcile_control(history, diagnostic):
    command,receipt=history['command'],history['receipt']
    if not receipt['settled'] or receipt['evidence'].get('stage')!='confirm_issued':
        raise ContractError('control_confirmation_not_settled')
    if not diagnostic['settled'] or diagnostic['target']!=command['target']:
        raise ContractError('control_recovery_target_changed')
    snapshot=diagnostic['snapshot']
    if time.time()-snapshot['observed_at']>300 or snapshot['module_id']!=command['module_id']:
        raise ContractError('fresh_control_diagnosis_required')
    if diagnostic['evidence']['controls']['dialogs']:
        raise ContractError('control_dialog_still_open')
    fields=[f for f in snapshot['fields'] if f['label']==command['field_label'] and f['signature']==command['field_signature']]
    if len(fields)!=1 or fields[0].get('value_readable') is False:
        raise ContractError('control_field_not_readable')
    norm=lambda x:re.sub(r'[\s省市-]+','',x)
    wanted=command['location']['province']+command['location']['city']
    if norm(fields[0]['value'])!=norm(wanted):raise ContractError('control_value_mismatch')
    return {'target_url':command['target']['url'],'module_id':command['module_id'],
        'signature':command['field_signature'],'adapter':command['adapter'],'source':command['source'],
        'verified_command_id':diagnostic['command_id'],'original_command_id':command['command_id']}
