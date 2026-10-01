"""Control-agent boundary: structural recipes and independently bound answers."""
import copy
import json
import time
from pathlib import Path

from .cli import atomic_json, load, owner_lock
from .contracts import ContractError, json_value
from .control_exceptions import (build_control_exception_request, control_method_key,
    validate_control_exception_decisions, CONTROL_EXCEPTION_INSTRUCTIONS, CONTROL_EXCEPTION_SCHEMA)
from .knowledge import resolve_record
from .control_registry import declaration, adapter_names
from .control_contract import compile_control_target

RECIPES = Path(__file__).resolve().parent.parent/'private'/'control-recipes.json'


def structure_key(field):
    # Temporary operability never identifies the learned component capability.
    return json.loads(json.dumps([item for item in control_method_key(field)
                                 if item[0] not in {'disabled', 'readonly'}]))


def recipe_matches(recipe, field):
    stable = [item for item in recipe['structure'] if item[0] not in {'disabled', 'readonly'}]
    if stable != structure_key(field):
        return False
    current = declaration(recipe['adapter'])
    return (recipe.get('adapterVersion', 1) == current['adapterVersion']
            and recipe.get('protocolVersion', 1) == current['protocolVersion'])


def learn_recipe(field, adapter, receipt, path=RECIPES):
    if (receipt.get('settled') is not True or receipt.get('status') != 'completed'
            or receipt.get('evidence', {}).get('committed') is not True
            or receipt.get('evidence', {}).get('adapter') != adapter):
        raise ContractError('control_recipe_requires_verified_commit')
    from .control_exceptions import ALLOWED_ADAPTERS
    if adapter not in ALLOWED_ADAPTERS:
        raise ContractError('invalid_control_adapter')
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with owner_lock(path.parent):
        entries = load(path).get('recipes', []) if path.exists() else []
        if any(r.get('verified_command_id') == receipt['command_id'] for r in entries):
            return
        key = structure_key(field)
        entries = [r for r in entries if [item for item in r['structure'] if item[0] not in {'disabled', 'readonly'}] != key]
        declared = declaration(adapter)
        entries.append({'structure': key, 'adapter': adapter,
                        'adapterVersion': declared['adapterVersion'], 'protocolVersion': declared['protocolVersion'],
                        'verified_command_id': receipt['command_id']})
        atomic_json(path, {'version': 2, 'recipes': entries})


def resolve_controls(values, knowledge, model, folder, path=RECIPES):
    diagnosis = values.get('control_diagnostics') or {}
    snapshot = copy.deepcopy(diagnosis.get('snapshot') or {})
    if (diagnosis.get('settled') is not True or not snapshot
            or time.time()-snapshot['observed_at'] > 300):
        raise ContractError('fresh_control_diagnosis_required')
    module = values['manifest']['modules'][values['index']]
    for key in ('mapping_context', 'module_label', 'record_label'):
        if key in module:
            snapshot[key] = copy.deepcopy(module[key])
    prior = values.get('results', {}).get(module['id'], {})
    request = build_control_exception_request(snapshot, (prior.get('last_receipt') or {}).get('results'))
    recipes = load(path).get('recipes', []) if Path(path).exists() else []
    decisions, unknown = [], []
    checked = []
    original = {f['id']: f for f in snapshot['fields']}
    dialogs=diagnosis.get('evidence',{}).get('controls',{}).get('dialogs',[])
    city_dialog=False
    if len(dialogs)==1 and dialogs[0].get('title')=='城市选择':
        controls=dialogs[0].get('controls',[])
        selects=[c for c in controls if c.get('tag')=='SELECT']
        city_dialog=(len(selects)==2 and
            all(any(o.get('label','').strip()==placeholder for o in c.get('options',[])) for c,placeholder in zip(selects,('省','市')))
            and len([c for c in controls if c.get('tag')=='BUTTON' and c.get('text')=='确定'])==1)
    discovered={r['id'] for r in (prior.get('last_receipt') or {}).get('results',[])
                if r.get('reason')=='text_probe_discovered_composite_control'}
    prior_snapshot=prior.get('current') or {}
    receipt=prior.get('last_receipt') or {}
    # Old text probes used the same reason for an opened dialog and a later
    # field merely blocked by it. Bind only the field actually clicked.
    if len(discovered)>1 and receipt.get('command_id'):
        journal_path=Path(folder)/'writer'/(receipt['command_id']+'.json')
        if journal_path.exists():
            journal=load(journal_path)
            if journal.get('receipt')==receipt:
                clicked={e.get('field_id') for e in journal.get('instrumentation',[])
                         if e.get('method')=='click' and e.get('status')=='returned'
                         and e.get('field_id') in discovered}
                if len(clicked)==1:discovered=clicked
    bound_probe=(bool(snapshot.get('target')) and receipt.get('target')==snapshot['target']
                 and (receipt.get('module_id') or (receipt.get('snapshot') or {}).get('module_id')
                      or (prior.get('command') or {}).get('module_id')
                      or prior_snapshot.get('module_id'))==snapshot.get('module_id')==module['id'])
    for field in request['fields']:
        current = original[field['field_id']]
        old_fields=[f for f in prior_snapshot.get('fields',[]) if f.get('id')==field['field_id']]
        same_field=(len(old_fields)==1 and old_fields[0].get('signature')==current.get('signature')
                    and old_fields[0].get('label')==current.get('label'))
        if city_dialog and bound_probe and same_field and len(discovered)==1 and field['field_id'] in discovered:
            decisions.append({'field_id':field['field_id'],'action':'use_adapter',
                              'adapter':'province_city_dialog_v1','actor':'program_dialog_rule'})
            continue
        if current.get('control_pattern') == 'next_range_date' and current.get('range_endpoint') in {'start', 'end'}:
            try:
                if current['range_endpoint']=='end' and current.get('value') in ('',None) and current.get('expanded')!='true' and json_value(values['profile'],resolve_record(values['profile'],snapshot)+'/is_current') is True and any(f['kind']=='checkbox' and f['label']=='至今' and f.get('value') is True for f in snapshot['fields']):
                    checked.append(field['field_id']);continue
                source = resolve_record(values['profile'], snapshot)+'/'+current['range_endpoint']+'_date'
                wanted = json_value(values['profile'], source)[:7]
                if current.get('value') == wanted and current.get('expanded') != 'true' and current.get('value_readable') is not False:
                    checked.append(field['field_id'])
                    continue
            except (ContractError, KeyError, TypeError):
                pass
        matches = [r for r in recipes if r.get('adapter') in adapter_names()
                   and recipe_matches(r, field)]
        if len(matches) == 1:
            decisions.append({'field_id': field['field_id'], 'action': 'use_adapter',
                              'adapter': matches[0]['adapter'], 'actor': 'program_recipe'})
        else:
            unknown.append(field)
    agent_request = {**request, 'fields': unknown}
    stamp = str(time.time_ns())
    audit = Path(folder)/'control-agent'
    audit.mkdir(exist_ok=True)
    atomic_json(audit/(stamp+'-request.json'), agent_request)
    if unknown:
        diagnose=getattr(model,'diagnose_controls',None)
        response = (diagnose(agent_request) if callable(diagnose) else
                    model.ask(CONTROL_EXCEPTION_INSTRUCTIONS, agent_request, CONTROL_EXCEPTION_SCHEMA))
        proposed = validate_control_exception_decisions(agent_request, response)
        decisions.extend(dict(d, actor='gpt-6-luna') for d in proposed)
    # Source resolution stays in the program; the control model never sees values.
    sources = knowledge.control_sources(values['profile'], snapshot)
    # Reuse a reviewed source from this module's own executed plan. Its DOM
    # identity must still agree; never carry a source between records or pages.
    for op in prior.get('operations', []):
        field=original.get(op.get('id'))
        hierarchy_source=(op.get('transform')=='join_location' and any(
            d.get('field_id')==op.get('id') and d.get('adapter')=='province_city_dialog_v1'
            for d in decisions))
        if (field and (op.get('transform') == 'identity' or hierarchy_source) and op.get('source')
                and field.get('signature') == op.get('field',{}).get('signature')):
            try:
                json_value(values['profile'],op['source'])
                sources.setdefault(field['id'],op['source'])
            except ContractError:
                pass
    by_id = {f['id']: f for f in snapshot['fields']}
    ready, blocked = [], []
    for decision in decisions:
        field = by_id[decision['field_id']]
        source = sources.get(field['id'])
        if (decision.get('adapter') == 'province_city_dialog_v1'
                and field.get('label','').strip(' *:：') in {'期望城市','意向工作城市','意向城市'}
                and values['profile'].get('batch_answers',{}).get('preferred_city')):
            source='/batch_answers/preferred_city'
        if field.get('control_pattern') == 'next_range_date':
            try:
                source = resolve_record(values['profile'], snapshot)+'/start_date'
                json_value(values['profile'], source)
                try:
                    json_value(values['profile'], source.rsplit('/', 1)[0]+'/end_date')
                except ContractError:
                    if json_value(values['profile'],source.rsplit('/',1)[0]+'/is_current') is not True:raise
            except (ContractError, KeyError, TypeError):
                source = None
        if decision['action'] != 'use_adapter' or not source:
            blocked.append({**decision, 'reason': 'handler_implementation_required' if
                            decision['action'] != 'use_adapter' else 'field_source_unresolved'})
        else:
            try:
                if declaration(decision['adapter'])['legacyCodec'] == 'date_range' and field.get('control_pattern') != 'next_range_date':
                    raise ContractError('adapter_structure_incompatible')
                compile_control_target(values['profile'], source, decision['adapter'])
            except ContractError as exc:
                blocked.append({**decision, 'reason': str(exc)})
                continue
            ready.append({**decision, 'label': field['label'], 'source': source})
    result = {'status': 'control_actions_ready' if ready else 'control_agent_blocked' if blocked else 'no_control_exceptions',
              'ready': ready, 'blocked': blocked, 'agent_calls': int(bool(unknown)),
              'reused_methods': len(decisions)-len(unknown), 'already_matched': checked}
    atomic_json(audit/(stamp+'-result.json'), result)
    return result


def assert_control_settled(values):
    status = values.get('status')
    if status not in {'module_blocked', 'control_blocked', 'control_committed', 'recovery_blocked', 'module_edit_opened'}:
        raise ContractError('control_requires_stopped_application')
    if status == 'control_blocked':
        history = values.get('control_history') or []
        if not history or history[-1]['receipt'].get('settled') is not True:
            raise ContractError('prior_control_call_unsettled')
    module = values['manifest']['modules'][values['index']]
    if status in {'module_blocked', 'recovery_blocked'}:
        receipt = values.get('results', {}).get(module['id'], {}).get('last_receipt')
        if receipt and receipt.get('settled') is not True:
            raise ContractError('prior_control_call_unsettled')
