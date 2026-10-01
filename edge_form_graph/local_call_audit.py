"""Derive terminal local failures from completed executor instrumentation.

Original receipts remain immutable. This proves call completion, not values or
persistence. Only explicitly known post-probe local guards are eligible.
"""
import copy
import json
import re
from .contracts import ContractError

GUARDS = {'text_probe_discovered_composite_control', 'date_year_navigation_not_unique'}


def native_readonly_search_timeout(journal):
    receipt=journal.get('receipt') or {}
    events=journal.get('instrumentation',[])
    failures=[e for e in events if e.get('status')!='returned']
    unknown=[r['id'] for r in receipt.get('results',[]) if r.get('status')=='unknown']
    if (journal.get('kind')!='fill' or journal.get('last_error',{}).get('kind')!='TimeoutError'
            or not str(receipt.get('target',{}).get('browser_id','')).startswith('edge-cdp:')
            or receipt.get('evidence',{}).get('executor')!='playwright'
            or len(failures)!=1 or len(unknown)!=1 or not events or events[-1]!=failures[0]):return False
    event=failures[0];error=event.get('error',{});message=error.get('message','')
    return (event.get('status')=='failed' and event.get('method')=='fill'
        and event.get('stage')=='popup_search' and event.get('field_id')==unknown[0]
        and type(event.get('duration_ms')) in (int,float) and event['duration_ms']>0
        and error.get('code')=='timeout'
        and bool(re.match(r'locator\.fill: Timeout \d+ms exceeded\.',message))
        and 'Call log:' in message and 'locator resolved to <input' in message
        and 'readonly="readonly"' in message)


def native_playwright_click_timeout(journal):
    """Prove an awaited native Playwright click timed out locally, not its effect."""
    receipt=journal.get('receipt') or {}
    events=journal.get('instrumentation',[])
    failures=[e for e in events if e.get('status')!='returned']
    unknown=[r['id'] for r in receipt.get('results',[]) if r.get('status')=='unknown']
    if (journal.get('kind')!='fill' or journal.get('last_error',{}).get('kind')!='TimeoutError'
            or journal.get('last_error',{}).get('phase')!='action_or_readback'
            or journal.get('last_error',{}).get('code')!='browser_call_failed'
            or not str(receipt.get('target',{}).get('browser_id','')).startswith('edge-cdp:')
            or receipt.get('evidence',{}).get('executor')!='playwright'
            or len(failures)!=1 or len(unknown)!=1 or not events):return False
    event=failures[0];error=event.get('error',{});message=error.get('message','')
    sanitized=journal.get('last_error',{}).get('sanitized',{})
    if (event.get('status')!='failed' or event.get('method')!='click'
            or event.get('stage') not in {'popup_open','popup_select'}
            or event.get('field_id')!=unknown[0]
            or type(event.get('duration_ms')) not in (int,float) or event['duration_ms']<=0
            or error.get('code')!='timeout'
            or not re.match(r'locator\.click: Timeout \d+ms exceeded\.',message)
            or 'Call log:' not in message
            or 'locator resolved to <' not in message
            or sanitized.get('code')!='timeout' or sanitized.get('message')!=message):return False
    # Cleanup may run after the timed-out click. It must itself have returned;
    # no pending or failed instrumented call can be reclassified as settled.
    failed_index=events.index(event)
    target_operation=event.get('operation')
    return (type(target_operation) is int and target_operation>0
        and all(e.get('status') in {'returned','failed'} and type(e.get('operation')) is int
            and e['operation']<=target_operation for e in events)
        and all(e.get('status')=='returned' for e in events[failed_index+1:]))

def guard_stage_matches(journal, guard):
    control=journal.get('control') or {}
    if guard=='text_probe_discovered_composite_control':
        return control.get('stage')=='text_probe_issued'
    return (guard=='date_year_navigation_not_unique'
        and control.get('stage')=='year_view_issued'
        and control.get('trace')==['trigger_issued','year_view_issued'])

def audited_completion(folder, journal):
    path=folder/'local-call-audits'/(journal.get('command_id','')+'.json')
    if not path.exists():return False
    proof=json.loads(path.read_text(encoding='utf-8'))
    receipt=journal.get('receipt') or {}
    if proof.get('guard') in {'native_readonly_search_timeout','native_playwright_click_timeout'}:
        write_flag_ok=(proof.get('write_verified') is False if proof.get('guard')=='native_playwright_click_timeout'
                       else proof.get('write_verified') in (None,False))
        return (proof.get('original_receipt')==receipt and proof.get('original_command_id')==journal.get('command_id')
            and proof.get('event_count')==len(journal.get('instrumentation',[]))
            and proof.get('value_verified') is False and write_flag_ok
            and ((proof.get('guard')=='native_readonly_search_timeout' and native_readonly_search_timeout(journal))
                 or (proof.get('guard')=='native_playwright_click_timeout' and native_playwright_click_timeout(journal))))
    events=journal.get('instrumentation',[])
    unknown=[r['id'] for r in receipt.get('results',[]) if r.get('status')=='unknown']
    selected=[e for e in events if len(unknown)==1 and e.get('field_id')==unknown[0]]
    return (journal.get('kind')=='fill' and proof.get('original_receipt')==receipt
        and proof.get('original_command_id')==journal.get('command_id')
        and proof.get('guard') in GUARDS
        and guard_stage_matches(journal,proof.get('guard'))
        and proof['guard']==journal.get('last_error',{}).get('sanitized',{}).get('message')
        and len(events)==proof.get('event_count') and bool(selected)
        and all(e.get('status')=='returned' for e in events)
        and all(e.get('method') in {'evaluate','count','innerText','click','press','getAttribute','isVisible','waitFor'} for e in selected))

def restore_audited_receipt(folder, journal):
    """Rebuild the derived receipt only when its command-bound audit still verifies."""
    if not audited_completion(folder, journal):
        return None
    receipt=copy.deepcopy(journal.get('receipt') or {})
    proof=json.loads((folder/'local-call-audits'/(journal.get('command_id','')+'.json')).read_text(encoding='utf-8'))
    receipt['settled']=True
    receipt['evidence']={**receipt.get('evidence',{}),'local_completion_audit':proof}
    if proof.get('guard') not in {'native_readonly_search_timeout','native_playwright_click_timeout'}:
        guard=proof['guard']
        receipt['status']='partial'
        unknown={item['id'] for item in receipt.get('results',[]) if item.get('status')=='unknown'}
        for item in receipt.get('results',[]):
            if item.get('id') in unknown:
                item.update(status='deferred',reason=guard)
    return receipt

def audit_local_failure(values, folder):
    if values.get('status') != 'recovery_blocked' or values.get('command'):
        raise ContractError('local_audit_requires_stopped_recovery')
    module = values['manifest']['modules'][values['index']]
    prior = values['results'][module['id']]
    receipt = prior.get('last_receipt') or {}
    if receipt.get('kind') != 'fill' or receipt.get('settled') is not False:
        raise ContractError('local_audit_requires_unknown_fill')
    path = folder/'writer'/(receipt['command_id']+'.json')
    journal = json.loads(path.read_text(encoding='utf-8'))
    events = journal.get('instrumentation', [])
    guard = journal.get('last_error', {}).get('sanitized', {}).get('message')
    native_readonly_timeout=journal.get('receipt')==receipt and native_readonly_search_timeout(journal)
    native_click_timeout=journal.get('receipt')==receipt and native_playwright_click_timeout(journal)
    if native_readonly_timeout or native_click_timeout:
        proof={'original_command_id':receipt['command_id'],'original_receipt':copy.deepcopy(receipt),
            'basis':'native_playwright_timeout_recorded_by_awaited_executor_instrumentation',
            'guard':'native_readonly_search_timeout' if native_readonly_timeout else 'native_playwright_click_timeout',
            'event_count':len(events),'value_verified':False,'write_verified':False}
        results=copy.deepcopy(values['results']);child=results[module['id']]
        derived=copy.deepcopy(receipt);derived['settled']=True
        derived['evidence']={**derived.get('evidence',{}),'local_completion_audit':proof}
        child['last_receipt']=derived
        return {'results':results,'command':None},proof
    if (journal.get('receipt') != receipt or guard not in GUARDS or not guard_stage_matches(journal,guard) or not events
            or any(e.get('status') != 'returned' for e in events)):
        raise ContractError('local_failure_completion_not_proven')
    unknown = [r['id'] for r in receipt['results'] if r['status'] == 'unknown']
    if len(unknown) != 1:
        raise ContractError('local_audit_ambiguous_operation')
    selected = [e for e in events if e.get('field_id') == unknown[0]]
    if not selected or any(e.get('method') not in {'evaluate','count','innerText','click','press','getAttribute','isVisible','waitFor'} for e in selected):
        raise ContractError('local_audit_requires_probe_only')
    proof = {'original_command_id':receipt['command_id'], 'original_receipt':copy.deepcopy(receipt),
             'basis':'all_instrumented_calls_returned_before_known_local_guard',
             'guard':guard, 'event_count':len(events), 'value_verified':False}
    results=copy.deepcopy(values['results'])
    child=results[module['id']]
    derived=copy.deepcopy(receipt)
    derived.update(settled=True,status='partial')
    derived['evidence']={**derived.get('evidence',{}),'local_completion_audit':proof}
    for result in derived['results']:
        if result['id'] in unknown:result.update(status='deferred',reason=guard)
    child['last_receipt']=derived
    child['command']=None
    child['results'].update({r['id']:r for r in derived['results']})
    return {'results':results,'command':None}, proof
