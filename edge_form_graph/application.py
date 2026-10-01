"""Ordered modules with independent save scopes. No model chooses the next module."""
from __future__ import annotations

import copy
import time
import uuid
from typing import TypedDict

from langgraph.graph import StateGraph, START, END
from langgraph.types import interrupt

from .contracts import ContractError, validate_snapshot, validate_target, validate_receipt, redacted_profile
from .graph import build_graph, initial_state
from .parallel import (ParallelWorkers, validate_module_snapshot, scope_reviews_current,
                       fill_results_complete, prefilled_bindings)
from .recovery import eligibility, reconcile, repair_eligible, compare_values, never_dispatched
from .execution_policy import tuning_enabled


class ApplicationState(TypedDict, total=False):
    profile: dict
    manifest: dict
    index: int
    status: str
    current: dict
    results: dict
    scopes: dict
    command: dict | None
    deadline: float
    allow_save: bool
    mapping_cache: dict
    scope_reviews: dict
    recovery_attempts: dict
    recovery_history: list
    recovery_reason: str
    recovery_requested: bool
    migration_authorization: dict
    budget_history: list
    revisit_history: list
    save_preflight_expected: list
    repair_authorization: dict
    repair_attempts: dict
    startup_reconciliation: list
    value_check_attempts: dict
    value_comparison: list
    diagnostic_previous: dict
    control_diagnostics: dict
    control_request: dict
    control_history: list
    control_methods: list
    last_save_receipt: dict
    learning_errors: list
    module_edit_history: list
    module_cancel_history: list
    module_add_history: list
    module_delete_history: list
    omitted_modules: list
    opened_modules: list
    activated_modules: list
    agent_tuning: bool
    coverage_gaps: list
    inventory_history: list


def application_state(profile, manifest, *, allow_save=False, deadline=None, startup_reconciliation=None, agent_tuning=True):
    validate_target(manifest['target'])
    modules, scopes = manifest['modules'], manifest['save_scopes']
    if not modules or len({m['id'] for m in modules}) != len(modules):
        raise ContractError('invalid_module_inventory')
    if len({g['id'] for g in scopes}) != len(scopes):
        raise ContractError('duplicate_save_scope')
    # The observer must report rendered-page order, not model preference order.
    if [m['page_order'] for m in modules] != list(range(len(modules))):
        raise ContractError('page_order_required')
    known = {g['id']: g for g in scopes}
    sequence = []
    for m in modules:
        if not m.get('selector') or m['save_scope'] not in known:
            raise ContractError('module_scope_missing')
        if not sequence or sequence[-1] != m['save_scope']:
            if m['save_scope'] in sequence:
                raise ContractError('interleaved_save_scopes_not_supported')
            sequence.append(m['save_scope'])
    if set(sequence) != set(known):
        raise ContractError('unused_save_scope')
    for g in scopes:
        if g.get('mode') not in {'explicit', 'automatic', 'unknown', 'draft'} or not g.get('selector'):
            raise ContractError('invalid_save_scope')
        if not g.get('evidence'):
            raise ContractError('save_scope_needs_page_evidence')
    if not manifest.get('discovery_evidence'):
        raise ContractError('page_inventory_needs_evidence')
    for module in modules:
        if module.get('open_mode') not in {None, 'add', 'delete', 'verify_absent'}:
            raise ContractError('invalid_module_open_mode')
        if module.get('open_mode') == 'add':
            if (not isinstance(module.get('collection_selector'), str)
                    or not isinstance(module.get('record_selector'), str)
                    or not isinstance(module.get('add_control_selector'), str)
                    or not isinstance(module.get('expected_record_count'), int)
                    or (module.get('max_records') is not None
                        and (not isinstance(module.get('max_records'), int)
                             or module['max_records'] < module['expected_record_count']))
                    or module['expected_record_count'] < 0):
                raise ContractError('invalid_collection_open_contract')
        if module.get('open_mode') in {'delete', 'verify_absent'}:
            identities=module.get('record_identity_fields')
            if (not isinstance(module.get('collection_selector'), str)
                    or not isinstance(module.get('record_selector'), str)
                    or (module.get('open_mode') == 'delete' and
                        (not isinstance(module.get('edit_control_selector'), str)
                         or not isinstance(module.get('delete_control_selector'), str)))
                    or not isinstance(module.get('expected_record_count'), int)
                    or module['expected_record_count'] <= 0
                    or not isinstance(identities, list) or not identities
                    or any(set(item) != {'label','value'} or not all(isinstance(item[k],str) and item[k]
                        for k in ('label','value')) for item in identities)):
                raise ContractError('invalid_collection_delete_contract')
        navigation = module.get('navigation')
        if navigation is not None and (set(navigation) != {'menu_selector', 'label'}
                or not all(isinstance(navigation[k], str) and navigation[k].strip()
                           for k in ('menu_selector', 'label'))):
            raise ContractError('invalid_module_navigation')
        if module.get('snapshot') is not None:
            validate_module_snapshot(module['snapshot'], manifest['target'], module)
    return ApplicationState(profile=redacted_profile(profile), manifest=copy.deepcopy(manifest), index=0,
        status='new', current={}, results={}, scopes={}, command=None,
        mapping_cache={}, scope_reviews={},
        deadline=deadline or time.time()+1800, allow_save=allow_save,
        activated_modules=[], opened_modules=[], agent_tuning=tuning_enabled(agent_tuning),
        startup_reconciliation=copy.deepcopy(startup_reconciliation or []))


def build_application(model, checkpointer, *, max_workers=2, knowledge=None, semantic_model=None):
    # Nested checkpoints keep child interrupts and independent reviews across restarts.
    module_graph = build_graph(model, True, knowledge=knowledge, semantic_model=semantic_model)
    workers = ParallelWorkers(model, max_workers)

    def premap(s):
        if knowledge is not None or not s.get('agent_tuning', True):
            return {}  # Ready fields must not wait for speculative model mapping.
        if s.get('command') is not None or time.time() >= s['deadline']:
            return {}
        return {'mapping_cache': workers.premap(s['profile'], s['manifest']['modules'], s['manifest']['target'])}

    def enter(s):
        if s['command'] is not None:
            raise ContractError('pending_application_command')
        if time.time() >= s['deadline']:
            return {'status': 'budget_exhausted'}
        if s['index'] == len(s['manifest']['modules']):
            if s['manifest'].get('program_inventory'):
                from .page_planner import coverage_gaps
                gaps=coverage_gaps(s['manifest'],s['results'],s.get('omitted_modules',[]))
                if gaps:return {'status':'incomplete_coverage','coverage_gaps':gaps}
            return {'status': 'complete_with_fallbacks' if any(r.get('manual_review') for r in s['results'].values()) else 'complete'}
        m = s['manifest']['modules'][s['index']]
        if s.get('scopes',{}).get(m['save_scope']) == 'saved_confirmed':
            return {'status':'module_held','index':s['index']+1}
        if m.get('hold_reason'):
            return {'status':'module_held','index':s['index']+1,
                    'results':{**s['results'],m['id']:{'coverage_hold':m['hold_reason']}}}
        g = next(g for g in s['manifest']['save_scopes'] if g['id'] == m['save_scope'])
        if g['mode'] == 'unknown':
            return {'status': 'save_scope_unknown'}
        if m.get('navigation') and m['id'] not in s.get('activated_modules', []):
            return {'status':'awaiting_module_navigation','command':{
                'version':1,'command_id':str(uuid.uuid4()),'kind':'activate_module','browser':'edge',
                'target':s['manifest']['target'],'module_id':m['id'],'module_selector':m['selector'],
                'navigation':copy.deepcopy(m['navigation']),'deadline':s['deadline']}}
        if m.get('open_mode')=='add' and m['id'] not in s.get('opened_modules',[]):
            return {'status':'awaiting_module_add','command':{
                'version':1,'command_id':str(uuid.uuid4()),'kind':'add_module_record','browser':'edge',
                'target':s['manifest']['target'],'module_id':m['id'],'module_selector':m['selector'],
                'collection_selector':m['collection_selector'],'record_selector':m['record_selector'],
                'add_control_selector':m['add_control_selector'],
                'expected_record_count':m['expected_record_count'],'add_label':m.get('add_label','添加'),
                **({'inline_repeater':True} if m.get('inline_repeater') else {}),
                **({'add_control_kind':m['add_control_kind']} if m.get('add_control_kind') else {}),
                **({'max_records':m['max_records']} if m.get('max_records') is not None else {}),
                'capture':copy.deepcopy(m.get('capture',{})),'deadline':s['deadline']}}
        if m.get('open_mode')=='delete':
            return {'status':'awaiting_module_delete','command':{
                'version':1,'command_id':str(uuid.uuid4()),'kind':'delete_module_record','browser':'edge',
                'target':s['manifest']['target'],'module_id':m['id'],'module_selector':m['selector'],
                'collection_selector':m['collection_selector'],'record_selector':m['record_selector'],
                'edit_control_selector':m['edit_control_selector'],
                'delete_control_selector':m['delete_control_selector'],
                'expected_record_count':m['expected_record_count'],
                'record_identity_fields':copy.deepcopy(m['record_identity_fields']),
                'delete_label':'删除本条记录','confirm_label':'确定',
                'navigation':copy.deepcopy(m.get('navigation')),
                'capture':copy.deepcopy(m.get('capture',{})),'deadline':s['deadline']}}
        if m.get('open_mode')=='verify_absent':
            return {'status':'awaiting_module_absence_verification','command':{
                'version':1,'command_id':str(uuid.uuid4()),'kind':'verify_module_record_absent','browser':'edge',
                'target':s['manifest']['target'],'module_id':m['id'],'module_selector':m['selector'],
                'collection_selector':m['collection_selector'],'record_selector':m['record_selector'],
                'expected_record_count':m['expected_record_count'],
                'record_identity_fields':copy.deepcopy(m['record_identity_fields']),
                'navigation':copy.deepcopy(m.get('navigation')),'deadline':s['deadline']}}
        return {'status': 'awaiting_observation', 'command': {
            'version': 1, 'command_id': str(uuid.uuid4()), 'kind': 'observe', 'browser': 'edge',
            'target': s['manifest']['target'], 'module_id': m['id'], 'module_selector': m['selector'],
            'capture': m.get('capture', {}), 'deadline': s['deadline']}}

    def activate_module(s):
        r=validate_receipt(s['command'],interrupt({'role':'trusted_edge_host','command':s['command']}))
        if not r['settled'] or r['status']!='completed' or r.get('evidence',{}).get('activated') is not True:
            return {'status':'module_navigation_blocked','command':None}
        return {'status':'module_activated','command':None,
                'activated_modules':s.get('activated_modules',[])+[s['command']['module_id']]}

    def add_module_record(s):
        r=validate_receipt(s['command'],interrupt({'role':'trusted_edge_host','command':s['command']}))
        capacity=(r['settled'] and r['status']=='completed'
                  and r.get('evidence',{}).get('capacity_reached') is True
                  and r.get('evidence',{}).get('record_count')==s['command'].get('max_records'))
        completed=(r['settled'] and r['status']=='completed' and r.get('snapshot')
                   and r.get('evidence',{}).get('record_editor_open') is True)
        status='module_added' if completed else 'module_capacity_reached' if capacity else 'module_add_blocked'
        return {'status':status,'command':None,
                'current':copy.deepcopy(r.get('snapshot') or {}),
                'opened_modules':s.get('opened_modules',[])+([s['command']['module_id']] if completed else []),
                'omitted_modules':s.get('omitted_modules',[])+([{
                    'module_id':s['command']['module_id'],'reason':'collection_capacity_reached',
                    'record_count':r['evidence']['record_count']} ] if capacity else []),
                'module_add_history':s.get('module_add_history',[])+[
                    {'command':s['command'],'receipt':r}]}

    def delete_module_record(s):
        r=validate_receipt(s['command'],interrupt({'role':'trusted_edge_host','command':s['command']}))
        completed=(r['settled'] and r['status']=='completed'
                   and r.get('evidence',{}).get('deletion_confirmed') is True
                   and r.get('evidence',{}).get('reloaded') is True
                   and r.get('evidence',{}).get('record_count_after')==s['command']['expected_record_count']-1)
        return {'status':'module_deleted' if completed else 'module_delete_blocked','command':None,
                'omitted_modules':s.get('omitted_modules',[])+([{
                    'module_id':s['command']['module_id'],'reason':'source_record_incomplete_deleted',
                    'record_count':r['evidence']['record_count_after']} ] if completed else []),
                'module_delete_history':s.get('module_delete_history',[])+[
                    {'command':s['command'],'receipt':r}]}

    def verify_module_record_absent(s):
        r=validate_receipt(s['command'],interrupt({'role':'trusted_edge_host','command':s['command']}))
        completed=(r['settled'] and r['status']=='completed'
                   and r.get('evidence',{}).get('absence_confirmed') is True
                   and r.get('evidence',{}).get('reloaded') is True
                   and r.get('evidence',{}).get('record_count')==s['command']['expected_record_count'])
        return {'status':'module_absence_confirmed' if completed else 'module_absence_unconfirmed','command':None,
                'omitted_modules':s.get('omitted_modules',[])+([{
                    'module_id':s['command']['module_id'],'reason':'source_record_incomplete_deleted_verified',
                    'record_count':r['evidence']['record_count']} ] if completed else [])}

    def observe(s):
        r = validate_receipt(s['command'], interrupt({'role': 'trusted_edge_host', 'command': s['command']}))
        if not r['settled'] or r['status'] != 'completed' or not r['snapshot']:
            return {'status': 'observation_unconfirmed', 'command': None,
                    'recovery_reason': r.get('evidence', {}).get('reason', 'observation_unconfirmed')}
        snap = r['snapshot']
        if snap['module_id'] != s['command']['module_id'] or snap['module_selector'] != s['command']['module_selector']:
            raise ContractError('wrong_observed_module')
        guarded=next((expected for expected in s.get('save_preflight_expected',[])
                      if expected['module_id']==snap['module_id']),None)
        if guarded:
            try:
                fresh_by_id={f['id']:f for f in snap.get('fields',[])}
                if any(f.get('kind')=='file' and any(f.get(key)!=fresh_by_id.get(f['id'],{}).get(key)
                       for key in ('value','value_present','upload_ready')) for f in guarded['fields']):
                    raise ContractError('save_preflight_upload_changed')
                comparisons=compare_values({'current':guarded,'operations':[]},snap)
                if any(item['status']!='unchanged' for item in comparisons):
                    raise ContractError('save_preflight_value_changed')
            except ContractError as exc:
                return {'status':'preflight_value_conflict','recovery_reason':str(exc),
                        'command':None,'current':snap}
        # An existing collapsed card is a valid arbitrary-start state.  Keep
        # the read-only diagnostic evidence so the explicit open-module route
        # can authorize exactly one observed Edit control before any mapping.
        if not any(f.get('kind') != 'file' for f in snap.get('fields', [])):
            view = r.get('evidence', {}).get('module_view', {})
            controls = r.get('evidence', {}).get('controls', {})
            if (s['command'].get('capture', {}).get('controlDiagnostics') is True
                    and len(view.get('edit_controls', [])) == 1
                    and not controls.get('dialogs')):
                return {'status': 'module_edit_not_ready', 'current': snap,
                        'command': None, 'control_diagnostics': r}
            if not snap.get('fields'):
                mid=s['manifest']['modules'][s['index']]['id']
                return {'status':'module_blocked','current':snap,'command':None,
                        'results':{**s['results'],mid:{'current':snap,'status':'editor_not_observed'}}}
        validate_snapshot(snap)
        module = s['manifest']['modules'][s['index']]
        manifest=copy.deepcopy(s['manifest'])
        if module.get('existing_card_identity'):
            from .collapsed_record_recovery import education_identity_matches
            record = next((r for r in s['profile'].get('education', [])
                           if r['record_id'] == module['mapping_context']['record_id']), None)
            if record is None or not education_identity_matches(record, snap['fields']):
                return {'status': 'preflight_value_conflict', 'recovery_reason': 'existing_card_identity_changed',
                        'command': None, 'current': snap}
        if module.get('record_split_expected'):
            expected=module['record_split_expected'];actual=snap['fields']
            if [(f['id'],f.get('value'),f.get('kind')) for f in expected]!=[(f['id'],f.get('value'),f.get('kind')) for f in actual]:
                return {'status':'preflight_value_conflict','recovery_reason':'record_split_values_changed',
                        'command':None,'current':snap}
            manifest['modules'][s['index']].pop('record_split_expected')
        if module.get('record_rebind_expected'):
            expected=module['record_rebind_expected'];actual=snap['fields']
            if [(f['id'],f.get('value'),f.get('kind')) for f in expected]!=[(f['id'],f.get('value'),f.get('kind')) for f in actual]:
                return {'status':'preflight_value_conflict','recovery_reason':'record_identity_values_changed',
                        'command':None,'current':snap}
            manifest['modules'][s['index']].pop('record_rebind_expected')
        if manifest.get('program_inventory'):
            from .page_planner import bind_opened_record
            binding=bind_opened_record(s['profile'],module,snap,manifest,s.get('results',{}))
            if binding:
                manifest['modules'][s['index']]['mapping_context']=binding
                module=manifest['modules'][s['index']]
        for key in ('mapping_context', 'module_label', 'record_label'):
            if key in module:
                snap[key] = copy.deepcopy(module[key])
        if knowledge is not None:
            snap['used_record_bindings'] = [r['current']['mapping_context'] for mid, r in s['results'].items()
                if mid != module['id'] and r.get('current', {}).get('mapping_context', {}).get('record_id')]
        from .verification import carry_control_skips,carry_control_commits
        carry_control_skips(snap, s.get('control_history', []))
        carry_control_commits(snap,s.get('control_history',[]),s['profile'])
        return {'status': 'observed', 'current': snap, 'command': None, 'manifest':manifest,
                'save_preflight_expected':[expected for expected in s.get('save_preflight_expected',[])
                                           if expected['module_id']!=snap['module_id']]}

    def fill_and_review(s):
        child = initial_state(s['profile'], s['current'], allow_save=False,
                              budget_seconds=max(0, s['deadline']-time.time()),
                              snapshot_already_validated=True, agent_tuning=s.get('agent_tuning', True))
        child['deadline'] = s['deadline']
        child['mapping_cache'] = s.get('mapping_cache', {}).get(s['current']['module_id'], {})
        child['defer_review'] = True
        result = module_graph.invoke(child)
        # No required or unresolved field is silently skipped to reach the next module.
        m = s['manifest']['modules'][s['index']]
        preserved_labels=set(m.get('preserved_blank_fields', []))
        before_by_id={f['id']:f for f in result.get('snapshot',{}).get('fields',[])}
        result_by_id=result.get('results',{})
        preserved_ids={f['id'] for f in result.get('current',{}).get('fields',[])
            if f.get('label') in preserved_labels and f.get('value') in (None,'',[])
            and before_by_id.get(f['id'],{}).get('value') in (None,'',[])
            and result_by_id.get(f['id'],{}).get('status') == 'deferred'
            and str(result_by_id.get(f['id'],{}).get('reason','')).startswith('source_is_not_an_answer:')}
        result['preserved_blank_ids'] = sorted(preserved_ids)
        from .parallel import _bound_record
        bound=_bound_record(s['profile'],result.get('current',{}))
        if bound and bound[2].get('is_current') is True and any(
                f['kind']=='checkbox' and f['label']=='至今' and f.get('value') is True
                for f in result.get('current',{}).get('fields',[])):
            preserved_ids.update(f['id'] for f in result['current']['fields']
                if f.get('control_pattern')=='next_range_date' and f.get('range_endpoint')=='end'
                and f.get('value') in (None,'') and f.get('expanded')!='true')
            result['preserved_blank_ids']=sorted(preserved_ids)
        result['prefilled_bindings'] = prefilled_bindings(s['profile'], result) if s.get('agent_tuning', True) else []
        complete = (result['status'] in {'filled_pending_review','required_field_missing'}
                    and fill_results_complete(result,preserved_ids))
        results = {**s['results'], m['id']: result}
        cache = dict(s.get('mapping_cache', {}))
        if result.get('proposal') is not None and result['status'] != 'plan_rejected':
            cache[m['id']] = {'status': 'mapped', 'snapshot': result['snapshot'],
                              'profile': s['profile'], 'proposal': result['proposal']}
        return {'results': results, 'mapping_cache': cache,
                'status': 'module_filled' if complete else 'module_blocked'}

    def review_boundary(s):
        if time.time() >= s['deadline']:
            return {'status': 'budget_exhausted'}
        modules = s['manifest']['modules']
        module = modules[s['index']]
        if s['index']+1 < len(modules) and modules[s['index']+1]['save_scope'] == module['save_scope']:
            return {'status': 'within_save_scope'}
        scope = [m for m in modules if m['save_scope'] == module['save_scope']]
        from .enum_repair import invalidate_unsupported_rank_aliases
        checked_results=invalidate_unsupported_rank_aliases(s['profile'],scope,s['results'])
        if any(checked_results.get(m['id'],{}).get('coverage_hold') for m in scope):
            return {'status':'scope_deferred','results':checked_results}
        reviews = workers.review_scope(s['profile'], scope, s['results'], s['manifest']['target'])
        accepted = scope_reviews_current(scope, s['results'], reviews, s['manifest']['target'])
        results = copy.deepcopy(s['results'])
        for m in scope:
            review = reviews.get(m['id'], {})
            result = results[m['id']]
            result['review'] = review.get('review', {})
            result['metrics'] = {**result.get('metrics', {}), 'review_model': review.get('model')}
            result['reviewed_revision'] = result['revision'] if review.get('status') == 'approved' and accepted else None
            result['status'] = 'verified_draft' if accepted else 'review_rejected'
        return {'scope_reviews': {**s.get('scope_reviews', {}), module['save_scope']: reviews},
                'results': results, 'status': 'scope_reviewed' if accepted else 'scope_review_blocked'}

    def recovery_route(s):
        if s['status']=='module_filled':
            return 'review_boundary'
        mid=s['manifest']['modules'][s['index']]['id']
        result=s['results'].get(mid,{})
        # Automatic recovery is bounded and reserved for ended uncertain calls
        # or transient control failures; missing facts do not cause busy loops.
        transient=any(r.get('reason') in {'popup_not_ready','operation_budget','popup_not_unique_or_not_loaded'}
                      for r in result.get('results',{}).values())
        if s.get('recovery_requested') or result.get('status')=='needs_reconciliation' or (not eligibility(result) and
                transient and
                s.get('recovery_attempts',{}).get(mid,0)<2):
            return 'prepare_recovery'
        return END

    def prepare_recovery(s):
        mid=s['manifest']['modules'][s['index']]['id']
        result=s['results'][mid]
        reason=eligibility(result)
        value_check = reason == 'transport_settlement_required' and (result.get('command') or {}).get('kind') == 'fill'
        if value_check:
            reason = None  # Authorize observation only; never label the old request settled.
        repair=s.get('repair_authorization', {})
        repair_ok=(repair.get('basis') and repair.get('module_id') == mid
                   and (repair_eligible(result) or s.get('recovery_reason') in {
                       'recovery_field_identity_changed','stale_snapshot','recovery_value_unreadable',
                       'recovery_limit','duplicate_or_unknown_field','invalid_option_value'}))
        if repair_ok and reason == 'input_or_review_correction_required':
            reason=None
        migration=s.get('migration_authorization',{})
        old=result.get('command') or {}
        uncertain_ids={x['id'] for x in (result.get('last_receipt') or {}).get('results',[]) if x.get('status')=='unknown'}
        migration_ok=(migration.get('basis') and migration.get('command_id')==old.get('command_id')
            and migration.get('module_id')==mid and old.get('kind')=='fill'
            and bool(uncertain_ids)
            and all(op.get('field',{}).get('kind') in {'text','select','combobox','radio','checkbox'}
                    and not op['field'].get('protected') for op in old.get('operations',[]) if op['id'] in uncertain_ids))
        if reason=='transport_settlement_required' and migration_ok:
            reason=None  # User-authorized supersession, NOT remote settlement evidence.
        # Explicit code/data corrections have their own cumulative history and
        # never erase or extend the two automatic value-check attempts.
        counter = 'repair_attempts' if repair_ok else 'value_check_attempts' if value_check else 'recovery_attempts'
        counter_key=mid if repair_ok else old.get('command_id') if value_check else mid
        attempts=s.get(counter,{}).get(counter_key,0)
        if reason or (attempts>=2 and not repair_ok) or time.time()>=s['deadline']:
            return {'status':'recovery_blocked','recovery_requested':False,
                    'recovery_reason':reason or ('recovery_limit' if attempts>=2 else 'budget_exhausted')}
        m=s['manifest']['modules'][s['index']]
        return {'status':'awaiting_fill_reconciliation','recovery_requested':False,
            'migration_authorization':{}, 'repair_authorization':{}, 'recovery_reason':'',
            counter:{**s.get(counter,{}),counter_key:attempts+1},
            'recovery_history':s.get('recovery_history',[])+[{'module_id':mid,'previous':copy.deepcopy(result),'at':time.time(),
                'migration_authorization':migration if migration_ok else {},
                'repair_authorization':repair if repair_ok else {},'old_outcome_unchanged':True}],
            'command':{'version':1,'command_id':str(uuid.uuid4()),'kind':'observe','browser':'edge',
                       'target':s['manifest']['target'],'module_id':mid,'module_selector':m['selector'],
                       'capture':{**m.get('capture',{}),**({'recoveryFieldSelector':next(op['field']['selector'] for op in old['operations'] if op['id'] in uncertain_ids)} if value_check and uncertain_ids else {})},'deadline':s['deadline']}}

    def reconcile_fill(s):
        r=validate_receipt(s['command'],interrupt({'role':'trusted_edge_host','command':s['command']}))
        if not r['settled'] or r['status']!='completed' or not r['snapshot']:
            return {'status':'recovery_blocked','recovery_reason':'recovery_read_unconfirmed'}
        mid=s['command']['module_id']
        comparisons=[]
        try:
            comparisons=compare_values(s['results'][mid],r['snapshot'])
            cache,matched=reconcile(s['results'][mid],r['snapshot'])
            corrections=s['recovery_history'][-1].get('repair_authorization',{}).get('corrections',[])
            if corrections:
                from .recovery import correct_proposal
                cache['proposal']=correct_proposal(s['profile'],cache['snapshot'],cache['proposal'],corrections)
                # Enum attempts are keyed only by field ID.  A source or
                # transform correction changes the question being reviewed,
                # so the prior "tried" marker must not suppress the new
                # candidate.  Preserve accepted enum operations themselves;
                # only reset the attempt ledger and bounded round counter.
                cache['enum_history']=[]
                cache['enum_rounds']=0
        except ContractError as exc:
            return {'status':'recovery_blocked','recovery_reason':str(exc),'command':None,
                    'value_comparison':comparisons}
        previous=s['results'][mid]
        old_receipt=previous.get('last_receipt') or {}
        review_only_no_write=(previous.get('status')=='review_rejected' and not old_receipt
            and previous.get('revision')==0 and previous.get('metrics',{}).get('batch_count')==0)
        if old_receipt.get('settled') is not True and not never_dispatched(previous) and not review_only_no_write:
            uncertain={x['id'] for x in old_receipt.get('results',[]) if x.get('status')=='unknown'}
            old_ops={o['id']:o for o in (previous.get('command') or {}).get('operations',[])}
            # A terminal batch receipt plus converged absolute setters permits
            # skipping them, never replay. Missing/unchanged evidence stays blocked.
            converged=(bool(uncertain) and uncertain <= set(matched) and uncertain <= set(old_ops)
                and all(old_ops[f]['field']['kind'] in {'text','select','combobox','radio','checkbox'} for f in uncertain))
            authorization=s['recovery_history'][-1].get('migration_authorization',{})
            unchanged={c['field_id'] for c in comparisons if c['status']=='unchanged'}
            # Explicit continuation can supersede an ended local batch with one
            # same-target absolute retry. This is NOT proof of remote cancellation.
            authorized_retry=(authorization.get('command_id')==old_receipt.get('command_id')
                and bool(authorization.get('basis')) and bool(uncertain)
                and uncertain <= (set(matched)|unchanged) and uncertain <= set(old_ops)
                and all(old_ops[f]['field']['kind'] in {'text','select','combobox','radio','checkbox'}
                    and not old_ops[f]['field'].get('protected') for f in uncertain))
            if not converged and not authorized_retry:
                return {'status':'recovery_blocked','recovery_reason':'value_not_matched_transport_unknown',
                        'command':None,'value_comparison':comparisons}
        scope=s['manifest']['modules'][s['index']]['save_scope']
        reviews={k:v for k,v in s.get('scope_reviews',{}).items() if k!=scope}
        history=copy.deepcopy(s['recovery_history']);history[-1].update(snapshot_id=r['snapshot']['snapshot_id'],matched_ids=matched,
            value_comparison=comparisons,old_remote_settlement_confirmed=old_receipt.get('settled') is True)
        return {'status':'observed','current':cache['snapshot'],'command':None,'scope_reviews':reviews,
                'value_comparison':comparisons,
                'mapping_cache':{**s.get('mapping_cache',{}),mid:cache},'recovery_history':history}

    def boundary(s):
        if time.time() >= s['deadline']:
            return {'status': 'budget_exhausted'}
        modules = s['manifest']['modules']
        m = modules[s['index']]
        if s['index']+1 < len(modules) and modules[s['index']+1]['save_scope'] == m['save_scope']:
            return {'status': 'within_save_scope'}
        g = next(g for g in s['manifest']['save_scopes'] if g['id'] == m['save_scope'])
        scope = [x for x in modules if x['save_scope'] == g['id']]
        if not scope_reviews_current(scope, s['results'], s.get('scope_reviews', {}).get(g['id'], {}), s['manifest']['target']):
            return {'status': 'scope_review_blocked'}
        if not s['allow_save'] or g['mode'] == 'draft':
            return {'status': 'scope_draft'}
        if not g.get('capture', {}).get('savedSignal') or (g['mode'] != 'automatic' and not g.get('capture', {}).get('saveControl')):
            return {'status': 'save_control_unverified'}
        expected = [s['results'][x['id']]['current'] for x in modules if x['save_scope'] == g['id']]
        preserved_blank_field_ids = sorted({field_id for x in modules if x['save_scope'] == g['id']
            for field_id in s['results'][x['id']].get('preserved_blank_ids', [])})
        return {'status': 'awaiting_scope_save', 'command': {
            'version': 1, 'command_id': str(uuid.uuid4()), 'kind': 'verify_autosave' if g['mode'] == 'automatic' else 'save_scope', 'browser': 'edge',
            'target': s['manifest']['target'], 'module_id': g['id'], 'module_selector': g['selector'],
            'capture': g['capture'], 'expected_modules': expected,
            'preserved_blank_field_ids': preserved_blank_field_ids, 'deadline': s['deadline']}}

    def save_scope(s):
        r = validate_receipt(s['command'], interrupt({'role': 'trusted_edge_host', 'command': s['command']}))
        if not r['settled'] or r['status'] != 'saved' or r['evidence'].get('save_confirmed') is not True:
            if r['settled'] and r['status'] == 'unconfirmed' and s['command']['kind'] != 'verify_autosave':
                return {'status': 'awaiting_save_reconciliation', 'command': {
                    **s['command'], 'kind': 'reconcile_save', 'command_id': str(uuid.uuid4()),
                    'original_command_id': s['command']['command_id']}}
            return {'status': 'save_unconfirmed'}
        return {'status': 'scope_saved', 'command': None,
                'scopes': {**s['scopes'], s['command']['module_id']: 'saved_confirmed'}, 'last_save_receipt': r}

    def recover_save(s):
        r = validate_receipt(s['command'], interrupt({'role': 'trusted_edge_host', 'command': s['command']}))
        if r['settled'] and r['status'] == 'saved' and r['evidence'].get('save_confirmed') is True:
            return {'status': 'scope_saved', 'command': None,
                    'scopes': {**s['scopes'], s['command']['module_id']: 'saved_confirmed'}, 'last_save_receipt': r}
        return {'status': 'needs_reconciliation'}

    def compile_experience(s):
        if knowledge is None:
            return {}
        scope_id = s['manifest']['modules'][s['index']]['save_scope']
        modules = [m for m in s['manifest']['modules'] if m['save_scope'] == scope_id]
        results = copy.deepcopy(s['results'])
        try:
            if not scope_reviews_current(modules, results, s['scope_reviews'].get(scope_id, {}), s['manifest']['target']):
                raise ContractError('saved_scope_review_changed')
            counts = knowledge.compile_saved(s['profile'], [results[m['id']] for m in modules], s['last_save_receipt'])
            metrics = results[modules[-1]['id']]['metrics']
            for key, value in counts.items():
                metrics[key] = metrics.get(key, 0) + value
            return {'results': results}
        except (OSError, ValueError, KeyError, TypeError) as exc:
            return {'learning_errors': s.get('learning_errors', []) + [
                {'phase': 'compile_saved', 'scope_id': scope_id,
                 'error': str(exc) if isinstance(exc, (ContractError, ValueError)) else type(exc).__name__}]}

    def advance(s):
        if s['status'] not in {'within_save_scope', 'scope_saved', 'scope_deferred', 'module_capacity_reached', 'module_deleted',
                               'module_absence_confirmed'} or s['command'] is not None:
            raise ContractError('cannot_advance_module')
        return {'index': s['index']+1, 'status': 'advance'}

    def prepare_diagnostic(s):
        m=s['manifest']['modules'][s['index']]
        return {'diagnostic_previous':{'status':s['status'],'command':s.get('command')},
            'status':'awaiting_observation','command':{'version':1,'command_id':str(uuid.uuid4()),'kind':'observe','browser':'edge',
            'target':s['manifest']['target'],'module_id':m['id'],'module_selector':m['selector'],
            'capture':{**m.get('capture',{}),'controlDiagnostics':True},'deadline':time.time()+120}}

    def read_diagnostic(s):
        r=validate_receipt(s['command'],interrupt({'role':'trusted_edge_host','command':s['command']}))
        return {**s['diagnostic_previous'],'control_diagnostics':r}

    def prepare_control(s):
        from .contracts import json_value
        d=s['control_diagnostics'];req=s['control_request']
        if not d['settled'] or time.time()-d['snapshot']['observed_at']>300:
            raise ContractError('fresh_control_diagnosis_required')
        if not req.get('source'):
            known=[m for m in s.get('control_methods',[]) if m['target_url']==d['target']['url'] and m['module_id']==d['snapshot']['module_id'] and m['signature']['label']==req['label']]
            if not known:raise ContractError('control_method_not_learned')
            req={**req,'source':known[-1]['source'],'adapter':known[-1]['adapter']}
        source_profile=s['profile']
        if req.get('profile_path'):
            from .cli import load
            source_profile=load(req['profile_path'])
        from .control_contract import compile_control_target
        from .control_registry import registry_report
        adapter=req.get('adapter') or 'province_city_dialog_v1'
        control_target=compile_control_target(source_profile,req['source'],adapter)
        fields=[f for f in d['evidence']['controls']['fields'] if
                (f.get('semantic_id')==req['field_id'] if req.get('field_id') else f['label']==req['label'])]
        original=[f for f in d['snapshot']['fields'] if
                  (f['id']==req['field_id'] and f['label']==req['label'] if req.get('field_id') else f['label']==req['label'])]
        if control_target['kind']=='date_range' and len(original)==1 and original[0].get('control_pattern')=='next_range_date':
            fields=[original[0]]
        if len(fields)!=1 or len(original)!=1:raise ContractError('unique_control_field_required')
        return {'status':'awaiting_control','command':{'version':1,'kind':'control_fill','command_id':str(uuid.uuid4()),
            'browser':'edge','target':d['target'],'module_id':d['snapshot']['module_id'],'module_selector':d['snapshot']['module_selector'],
            # readControlEvidence reports a document-absolute diagnostic path,
            # while every control adapter resolves inside module_selector. Use
            # the semantic snapshot's module-scoped selector after the label
            # and signature have been uniquely cross-checked above.
            'adapter':adapter,'field_label':req['label'],'field_selector':original[0]['selector'],
            'allow_logical_selector':True,'field_signature':original[0]['signature'],'before_value':original[0]['value'],'source':req['source'],'controlTarget':control_target,'controlRegistry':registry_report(),'deadline':time.time()+120}}

    def execute_control(s):
        r=validate_receipt(s['command'],interrupt({'role':'trusted_edge_host','command':s['command']}))
        methods=list(s.get('control_methods',[]))
        if r['settled'] and r['status']=='completed' and r['evidence'].get('committed') is True:
            methods.append({'target_url':r['target']['url'],'module_id':s['command']['module_id'],
                'signature':s['command']['field_signature'],'adapter':s['command']['adapter'],
                'source':s['command']['source'],'verified_command_id':r['command_id']})
        # A control commit is not a saved module. Discard prior review approvals.
        return {'status':'control_committed' if r['status']=='completed' else 'control_blocked','command':None,
            'scope_reviews':{},'control_methods':methods,
            'control_history':s.get('control_history',[])+[{'command':s['command'],'receipt':r}]}

    def reconcile_control_node(s):
        from .control_recovery import reconcile_control
        method=reconcile_control(s['control_history'][-1],s['control_diagnostics'])
        return {'status':'control_committed','command':None,'scope_reviews':{},
            'control_methods':s.get('control_methods',[])+[method]}

    def prepare_module_edit(s):
        d=s['control_diagnostics'];m=s['manifest']['modules'][s['index']]
        edits=d.get('evidence',{}).get('module_view',{}).get('edit_controls',[])
        if (not d['settled'] or d['status']!='completed' or time.time()-d['snapshot']['observed_at']>300
            or d['snapshot']['module_id']!=m['id'] or any(f.get('kind')!='file' for f in d['snapshot']['fields']) or len(edits)!=1
            or d['evidence']['controls']['dialogs']):
            return {'status':'module_edit_not_ready','command':None}
        return {'status':'awaiting_module_edit','command':{'version':1,'kind':'edit_module','command_id':str(uuid.uuid4()),
            'browser':'edge','target':d['target'],'module_id':m['id'],'module_selector':m['selector'],
            'edit_selector':edits[0]['selector'],'capture':m.get('capture',{}),'deadline':time.time()+120}}

    def execute_module_edit(s):
        r=validate_receipt(s['command'],interrupt({'role':'trusted_edge_host','command':s['command']}))
        return {'status':'module_edit_opened' if r['settled'] and r['status']=='completed' else 'module_edit_blocked',
            'command':None,'scope_reviews':{},
            'module_edit_history':s.get('module_edit_history',[])+[{'command':s['command'],'receipt':r}]}

    def prepare_module_cancel(s):
        m=s['manifest']['modules'][s['index']]
        result=s['results'][m['id']]
        preserved=result.get('preserved_blank_ids',[])
        if not preserved:
            return {'status':'module_cancel_not_authorized','command':None}
        return {'status':'awaiting_module_cancel','command':{
            'version':1,'kind':'cancel_module_edit','command_id':str(uuid.uuid4()),'browser':'edge',
            'target':s['manifest']['target'],'module_id':m['id'],'module_selector':m['selector'],
            'cancel_label':'取消','expected_snapshot':copy.deepcopy(result['current']),
            'preserved_blank_field_ids':copy.deepcopy(preserved),
            'capture':copy.deepcopy(m.get('capture',{})),'deadline':time.time()+120}}

    def execute_module_cancel(s):
        r=validate_receipt(s['command'],interrupt({'role':'trusted_edge_host','command':s['command']}))
        completed=r['settled'] and r['status']=='completed' and r['evidence'].get('editor_closed') is True
        return {'status':'module_omitted_source_incomplete' if completed else 'module_cancel_blocked',
                'command':None,
                'module_cancel_history':s.get('module_cancel_history',[])+[
                    {'command':s['command'],'receipt':r}]}

    def prepare_module_cancel_reconciliation(s):
        history=s.get('module_cancel_history',[])
        if not history:
            return {'status':'module_cancel_reconciliation_not_authorized','command':None}
        prior=history[-1];receipt=prior.get('receipt',{});old=prior.get('command',{})
        if (receipt.get('settled') is not True or receipt.get('status')!='unconfirmed'
                or receipt.get('evidence',{}).get('reason')!='cancel_not_confirmed'):
            return {'status':'module_cancel_reconciliation_not_authorized','command':None}
        return {'status':'awaiting_module_cancel_reconciliation','command':{
            'version':1,'kind':'reconcile_module_cancel','command_id':str(uuid.uuid4()),'browser':'edge',
            'target':old['target'],'module_id':old['module_id'],'module_selector':old['module_selector'],
            'original_command_id':old['command_id'],'capture':copy.deepcopy(old['capture']),
            'deadline':time.time()+120}}

    def execute_module_cancel_reconciliation(s):
        r=validate_receipt(s['command'],interrupt({'role':'trusted_edge_host','command':s['command']}))
        completed=r['settled'] and r['status']=='completed' and r['evidence'].get('editor_closed') is True
        return {'status':'module_omitted_source_incomplete' if completed else 'module_cancel_blocked',
                'command':None,
                'module_cancel_history':s.get('module_cancel_history',[])+[
                    {'command':s['command'],'receipt':r}]}

    g = StateGraph(ApplicationState)
    g.add_node('prepare_module_edit',prepare_module_edit)
    g.add_node('execute_module_edit',execute_module_edit)
    g.add_conditional_edges('prepare_module_edit',lambda s:'execute_module_edit' if s['status']=='awaiting_module_edit' else END)
    # Opening a collapsed card is a deterministic preparation step.  Once the
    # editor is confirmed open, immediately re-enter the same module and take
    # a fresh snapshot instead of requiring an agent/manual resume boundary.
    g.add_conditional_edges('execute_module_edit',
                            lambda s:'enter' if s['status']=='module_edit_opened' else END)
    g.add_node('prepare_module_cancel',prepare_module_cancel)
    g.add_node('execute_module_cancel',execute_module_cancel)
    g.add_conditional_edges('prepare_module_cancel',
                            lambda s:'execute_module_cancel' if s['status']=='awaiting_module_cancel' else END)
    g.add_edge('execute_module_cancel',END)
    g.add_node('prepare_module_cancel_reconciliation',prepare_module_cancel_reconciliation)
    g.add_node('execute_module_cancel_reconciliation',execute_module_cancel_reconciliation)
    g.add_conditional_edges('prepare_module_cancel_reconciliation',
                            lambda s:'execute_module_cancel_reconciliation'
                            if s['status']=='awaiting_module_cancel_reconciliation' else END)
    g.add_edge('execute_module_cancel_reconciliation',END)
    g.add_node('prepare_diagnostic',prepare_diagnostic)
    g.add_node('read_diagnostic',read_diagnostic)
    g.add_edge('prepare_diagnostic','read_diagnostic')
    g.add_edge('read_diagnostic',END)
    g.add_node('prepare_control',prepare_control)
    g.add_node('execute_control',execute_control)
    g.add_edge('prepare_control','execute_control')
    g.add_edge('execute_control',END)
    g.add_node('reconcile_control',reconcile_control_node)
    g.add_edge('reconcile_control',END)
    for name, fn in [('premap', premap), ('enter', enter), ('activate_module',activate_module),
                     ('add_module_record',add_module_record), ('delete_module_record',delete_module_record),
                     ('verify_module_record_absent',verify_module_record_absent),
                     ('observe', observe), ('fill_and_review', fill_and_review),
                     ('review_boundary', review_boundary),
                     ('compile_experience', compile_experience), ('boundary', boundary), ('save_scope', save_scope), ('recover_save', recover_save), ('advance', advance),
                     ('prepare_recovery',prepare_recovery),('reconcile_fill',reconcile_fill)]:
        g.add_node(name, fn)
    g.add_edge(START, 'premap')
    g.add_edge('premap', 'enter')
    g.add_conditional_edges('enter', lambda s: 'enter' if s['status']=='module_held' else 'activate_module' if s['status']=='awaiting_module_navigation'
                            else 'add_module_record' if s['status']=='awaiting_module_add'
                            else 'delete_module_record' if s['status']=='awaiting_module_delete'
                            else 'verify_module_record_absent' if s['status']=='awaiting_module_absence_verification'
                            else 'observe' if s['status']=='awaiting_observation' else END)
    g.add_edge('activate_module','enter')
    g.add_conditional_edges('add_module_record',
                            lambda s:'enter' if s['status']=='module_added' else 'advance'
                            if s['status']=='module_capacity_reached' else END)
    g.add_conditional_edges('delete_module_record',
                            lambda s:'advance' if s['status']=='module_deleted' else END)
    g.add_conditional_edges('verify_module_record_absent',
                            lambda s:'advance' if s['status']=='module_absence_confirmed' else END)
    g.add_conditional_edges('observe', lambda s: 'fill_and_review' if s['status']=='observed' else END)
    g.add_conditional_edges('fill_and_review', recovery_route)
    g.add_conditional_edges('prepare_recovery',lambda s:'reconcile_fill' if s['status']=='awaiting_fill_reconciliation' else END)
    g.add_conditional_edges('reconcile_fill',lambda s:'fill_and_review' if s['status']=='observed' else END)
    g.add_conditional_edges('review_boundary', lambda s: 'advance' if s['status'] in {'within_save_scope','scope_deferred'} else 'boundary' if s['status']=='scope_reviewed' else END)
    g.add_conditional_edges('boundary', lambda s: 'advance' if s['status']=='within_save_scope' else 'save_scope' if s['status']=='awaiting_scope_save' else END)
    g.add_conditional_edges('save_scope', lambda s: 'compile_experience' if s['status']=='scope_saved' else 'recover_save' if s['status']=='awaiting_save_reconciliation' else END)
    g.add_conditional_edges('recover_save', lambda s: 'compile_experience' if s['status']=='scope_saved' else END)
    g.add_edge('compile_experience', 'advance')
    g.add_edge('advance', 'enter')
    return g.compile(checkpointer=checkpointer)


def active_command(state):
    """Find the actual suspended child, not its parent's historical observe state."""
    for task in state.tasks:
        if task.state and hasattr(task.state, 'values'):
            command = active_command(task.state)
            if command:
                return command
    return state.values.get('command') if state.next else None
