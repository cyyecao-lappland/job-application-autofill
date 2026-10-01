"""Audit a terminated, read-only save preflight. Never replay a save click."""
import copy
import json
import time
from contextlib import contextmanager
from .contracts import ContractError
from .cli import atomic_json


def is_readonly_preflight(journal):
    events=journal.get('instrumentation',[])
    return (journal.get('kind')=='save_scope' and journal.get('status')=='pending'
            and not journal.get('receipt') and not journal.get('save_stage')
            and not journal.get('stages') and bool(events)
            and all(e.get('stage')=='module_readback' and e.get('method')=='evaluate'
                    and e.get('status')=='returned' for e in events))


def audited_preflight(folder,journal):
    path=folder/'save-preflight-audits'/(journal.get('command_id','')+'.json')
    if not path.exists() or not is_readonly_preflight(journal):return False
    proof=json.loads(path.read_text(encoding='utf-8'))
    return (proof.get('original_journal')==journal and proof.get('save_dispatched') is False
            and proof.get('command',{}).get('command_id')==journal['command_id']
            and bool(proof.get('basis')))


def recover_save_preflight(values,command,folder,basis,*,writer_lock_held=False):
    if (not basis.strip() or not command or command.get('kind')!='save_scope'
            or values.get('status')!='awaiting_scope_save'
            or values.get('command')!=command or
            (not writer_lock_held and (folder/'writer'/'active.lock').exists())):
        raise ContractError('save_preflight_recovery_requires_stopped_current_command')
    expected=command.get('expected_modules',[])
    module=values['manifest']['modules'][values['index']]
    if len(expected)!=1 or expected[0].get('module_id')!=module['id']:
        raise ContractError('save_preflight_recovery_requires_single_module_scope')
    path=folder/'writer'/(command['command_id']+'.json')
    journal=json.loads(path.read_text(encoding='utf-8'))
    if journal.get('command_id')!=command['command_id'] or not is_readonly_preflight(journal):
        raise ContractError('save_preflight_was_not_readonly')
    proof={'original_journal':journal,'command':copy.deepcopy(command),'basis':basis,
           'at':time.time(),'save_dispatched':False}
    audit=folder/'save-preflight-audits'/path.name
    audit.parent.mkdir(exist_ok=True)
    if not audit.exists():atomic_json(audit,proof)
    manifest=copy.deepcopy(values['manifest'])
    manifest['modules'][values['index']].pop('snapshot',None)
    return {'command':None,'status':'module_blocked','manifest':manifest,
            'scope_reviews':{},'mapping_cache':{},'current':{},'control_diagnostics':{},
            'save_preflight_expected':copy.deepcopy(command.get('expected_modules',[]))}


@contextmanager
def preflight_writer_lock(folder):
    path=folder/'writer'/'active.lock'
    try:stream=path.open('x',encoding='utf-8')
    except FileExistsError as exc:raise ContractError('writer_active_during_preflight_recovery') from exc
    try:
        stream.write('save_preflight_recovery');stream.flush()
        yield
    finally:
        stream.close()
        path.unlink()
