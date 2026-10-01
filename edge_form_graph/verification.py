"""Skipped readback is a terminal field result, never evidence for learning."""
import copy
import re

PROCESSED = {'written', 'already_matched', 'verification_skipped'}


def incomplete(field):
    value = field.get('value')
    return (field.get('value_readable') is False or value is None
            or isinstance(value, str) and re.search(r'[*•●]{3,}', value) is not None)


def attach_skips(current, operations, results):
    fields = {f['id']: f for f in current['fields']}
    for op in operations:
        if results.get(op['id'], {}).get('status') != 'verification_skipped':
            continue
        field = fields.get(op['id'])
        if field and field.get('signature') == op['field'].get('signature'):
            op['verification'] = 'skipped'
            field['verification_skipped'] = True


def carry_control_skips(snapshot, history):
    """Only settled receipts for this exact target/module/field can suppress a retry."""
    for item in history:
        command, receipt = item['command'], item['receipt']
        if (receipt.get('settled') is not True or receipt.get('status') != 'completed'
                or receipt.get('evidence', {}).get('verification') != 'skipped'
                or command['target'] != snapshot['target'] or command['module_id'] != snapshot['module_id']):
            continue
        for field in snapshot['fields']:
            if (field.get('selector') == command['field_selector']
                    and field.get('signature') == command['field_signature'] and incomplete(field)):
                field['verification_skipped'] = True
                field['verification_skip_command'] = command['command_id']
    return snapshot


def carry_control_commits(snapshot, history, profile):
    """Retain a typed control's verified display, without treating it as saved."""
    from .control_contract import compile_control_target
    from .contracts import ContractError
    for item in history:
        command,receipt=item['command'],item['receipt'];evidence=receipt.get('evidence',{})
        if (receipt.get('settled') is not True or receipt.get('status')!='completed'
                or evidence.get('committed') is not True or evidence.get('verification')!='match'
                or command['target']!=snapshot['target'] or command['module_id']!=snapshot['module_id']):continue
        try:
            if compile_control_target(profile,command['source'],command['adapter'])!=command.get('controlTarget'):continue
        except ContractError:continue
        for field in snapshot['fields']:
            if (field.get('selector')==command['field_selector'] and field.get('signature')==command['field_signature']
                    and not incomplete(field) and field.get('expanded') not in (True,'true')
                    and field.get('value')==evidence.get('actual') and field.get('value') not in (None,'',[])):
                field['verified_control']={'command_id':command['command_id'],'source':command['source'],
                    'adapter':command['adapter'],'actual':copy.deepcopy(evidence['actual'])}
    return snapshot


def retain_control_commits(before, current, results):
    old={f['id']:f for f in before['fields'] if f.get('verified_control')}
    for field in current['fields']:
        prior=old.get(field['id'])
        if not prior:continue
        if (field.get('signature')==prior.get('signature') and field.get('value')==prior['verified_control']['actual']
                and not incomplete(field) and field.get('expanded') not in (True,'true')):
            field['verified_control']=copy.deepcopy(prior['verified_control'])
        else:results[field['id']]={'status':'conflict','reason':'verified_control_value_changed'}
