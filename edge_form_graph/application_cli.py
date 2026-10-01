"""Single application entrypoint; imports real child checkpoints, never model receipts."""
import argparse
import copy
import json
import time
import uuid
from pathlib import Path

from .storage import SqliteSaver
from langgraph.types import Command

from .application import application_state, build_application, active_command
from .cli import load, atomic_json, owner_lock
from .collections import plan_experience_placement
from .contracts import ContractError, validate_receipt, redacted_profile
from .recovery import repair_eligible
from .runtime import review_model


def recovered_fill_receipt(folder, old, journal):
    """Recover the journal receipt, retaining a still-valid local audit derivation."""
    receipt=validate_receipt(old,journal['receipt'])
    if old.get('kind')=='fill':
        from .local_call_audit import restore_audited_receipt
        audited=restore_audited_receipt(folder,journal)
        if audited is not None:
            return audited
    return receipt


def completed_semantic_error_receipt(state, folder):
    """Permit local graph continuation after a consumed, settled ordinary fill."""
    if (state.values.get('command') or tuple(state.next) != ('fill_and_review',)
            or len(state.tasks) != 1 or state.tasks[0].name != 'fill_and_review'
            or state.tasks[0].error != "ContractError('duplicate_or_unknown_field')"):
        return None
    child = state.tasks[0].state
    if (not hasattr(child, 'values') or child.values.get('command')
            or tuple(child.next) != ('resolve_unknown',) or len(child.tasks) != 1
            or child.tasks[0].error != "ContractError('duplicate_or_unknown_field')"):
        return None
    receipt = child.values.get('last_receipt') or {}
    module = state.values['manifest']['modules'][state.values['index']]
    snapshot = receipt.get('snapshot') or {}
    if (receipt.get('kind') != 'fill' or receipt.get('settled') is not True
            or receipt.get('status') not in {'completed', 'partial'}
            or any(r.get('status') in {'unknown', 'conflict'} for r in receipt.get('results', []))
            or receipt.get('target') != state.values['manifest']['target']
            or snapshot.get('module_id') != module['id']
            or snapshot.get('module_selector') != module['selector']):
        return None
    journal_path = folder/'writer'/(receipt['command_id']+'.json')
    if not journal_path.is_file():
        return None
    journal = load(journal_path)
    bound_receipt = copy.deepcopy(receipt)
    original_snapshot = (journal.get('receipt') or {}).get('snapshot') or {}
    for key in ('mapping_context', 'module_label', 'used_record_bindings', 'record_label'):
        if key not in original_snapshot and key in bound_receipt['snapshot']:
            if bound_receipt['snapshot'][key] != child.values['snapshot'].get(key):
                return None
            bound_receipt['snapshot'].pop(key)
    if (journal.get('receipt') != bound_receipt or journal.get('pending_field') is not None
            or journal.get('remote_unsettled') is True):
        return None
    return receipt


def reconciliation_parent(folder, command_id):
    """Recover the original save link independently of checkpoint retention."""
    database = folder/'application.sqlite'
    if not database.exists():
        return None
    with SqliteSaver.from_conn_string(str(database)) as saver:
        command = saver.command_link('application', command_id)
        if command and command['kind'] == 'reconcile_save':
            return command['original_command_id']
    return None


def check_prior(root):
    """Keep only unresolved side-effect evidence; never resume stale page state.

    A fresh run may reconcile field writes from the current page. Save-like
    actions remain a hard save barrier because repeating them is unsafe.
    """
    from .runtime import local_path
    root = local_path(root)
    blocked, recoverable = [], []
    journals = [(p, load(p)) for p in Path(root).rglob('writer/*.json')]
    for path, journal in journals:
        from .save_preflight_recovery import audited_preflight
        if audited_preflight(path.parent.parent,journal):continue
        r = journal.get('receipt')
        if not r or not r.get('settled') or r.get('status') in {'unknown', 'unconfirmed'}:
            # A settled save can be resolved by a later read-only saved-card
            # observation in this writer. An unfinished call is never cleared.
            if (r and r.get('settled') is True and journal.get('kind') in {'save','save_scope'}
                    and any(p.parent == path.parent and j.get('kind') == 'reconcile_save'
                        and j.get('started_at', 0) > journal.get('started_at', 0)
                        and (j.get('receipt') or {}).get('settled') is True
                        and (j.get('receipt') or {}).get('status') == 'saved'
                        and (j.get('receipt') or {}).get('evidence', {}).get('save_confirmed') is True
                        and (j.get('receipt') or {}).get('target') == r.get('target')
                        and reconciliation_parent(p.parent.parent,j.get('command_id')) == journal.get('command_id')
                        for p,j in journals)):
                continue
            summary_path = path.parent.parent/'summary.json'
            run_complete = False
            if summary_path.exists():
                summary = load(summary_path)
                scopes = summary.get('scopes', {})
                run_complete = (summary.get('status') == 'complete' and bool(scopes)
                                and all(value == 'saved_confirmed' for value in scopes.values()))
            if run_complete and journal.get('kind') in {'save', 'save_scope', 'reconcile_save'}:
                continue
            # A completed save attempt that the page explicitly rejected for
            # validation is a known non-save outcome.  It is safe for a fresh
            # arbitrary-start run to re-read the current draft and repair the
            # missing fields.  Keep every other unconfirmed post-click result
            # behind the hard reconciliation barrier.
            validation_rejected = (
                r and r.get('settled') is True
                and r.get('status') == 'unconfirmed'
                and journal.get('kind') in {'save', 'save_scope'}
                and journal.get('save_stage') == 'observation_returned'
                and (r.get('evidence') or {}).get('signal') == 'validation_failed')
            if validation_rejected:
                continue
            # A registered read-only server oracle can prove the reviewed
            # education is absent. This permits repairing a fresh draft, never
            # labels the old save successful or erases its journal.
            if (r and r.get('settled') is True and journal.get('kind') == 'save_scope'
                    and journal.get('save_stage') == 'observation_returned'
                    and any(p.parent == path.parent and j.get('kind') == 'reconcile_save'
                        and j.get('started_at', 0) > journal.get('started_at', 0)
                        and (j.get('receipt') or {}).get('settled') is True
                        and (j.get('receipt') or {}).get('status') == 'unconfirmed'
                        and (j.get('receipt') or {}).get('target') == r.get('target')
                        and (j.get('receipt') or {}).get('evidence', {}).get('signal') == 'persisted_scope_absent'
                        and (j.get('receipt') or {}).get('evidence', {}).get('current_draft_matches') is True
                        and (j.get('receipt') or {}).get('evidence', {}).get('persisted', {}).get('oracle') == 'alibaba_resume_detail_v1'
                        and (j.get('receipt') or {}).get('evidence', {}).get('persisted', {}).get('education_count') == 0
                        and (j.get('receipt') or {}).get('evidence', {}).get('persisted', {}).get('observed_at', 0) > journal.get('started_at', 0)
                        and reconciliation_parent(p.parent.parent, j.get('command_id')) == journal.get('command_id')
                        for p, j in journals)):
                continue
            settled_add_without_click=(r and r.get('settled') is True
                and journal.get('kind')=='add_module_record'
                and not any(item.get('method')=='click' for item in journal.get('instrumentation',[])))
            if settled_add_without_click:
                continue
            # A settled reconciliation is read-only. A settled save command
            # that returned before save_click_issued is also proven to have no
            # side effect and must not poison every future arbitrary start.
            # Once a click stage exists, keep the hard save barrier.
            if (r and r.get('settled') is True
                    and (journal.get('kind') == 'reconcile_save'
                         or (journal.get('kind') in {'save', 'save_scope'}
                             and not journal.get('save_stage')
                             and not any(stage.get('name') == 'save_click_issued'
                                         for stage in journal.get('stages', []) if isinstance(stage, dict))))):
                continue
            resolution_path = path.parent.parent/'resolutions'/path.name
            if resolution_path.exists():
                resolution = load(resolution_path)
                if (resolution.get('original_receipt') == r and r
                    and resolution.get('original_command_id') == journal.get('command_id')
                    and resolution.get('target') == r.get('target')
                    and resolution.get('executor') in {'codex-edge', 'playwright'}
                    and resolution.get('status') == 'superseded_by_user'
                    and resolution.get('user_basis')
                    and resolution.get('current_values_match') is True):
                    continue
            item={'journal': str(path), 'command_id': journal.get('command_id'),
                  'kind': journal.get('kind'), 'status': (r or {}).get('status', 'pending')}
            if journal.get('kind') in {'observe','fill','control_fill','edit_module','add_module_record','delete_module_record','verify_module_record_absent','cancel_module_edit','reconcile_module_cancel'}:
                recoverable.append(item)
            else:
                blocked.append(item)
    if blocked:
        for item in blocked:
            journal=load(item['journal']); receipt=journal.get('receipt') or {}
            item['settled']=receipt.get('settled') is True
            item['target']=receipt.get('target')
            database=Path(item['journal']).parent.parent/'application.sqlite'
            if database.exists() and item['settled']:
                with SqliteSaver.from_conn_string(str(database)) as saver:
                    command = saver.command_link('application', item['command_id'])
                    if command:
                        item['module_selector'] = command['module_selector']
        return {'status': 'prior_reconciliation_required', 'blockers': blocked,
                'recoverable_from_current_page': recoverable}
    if recoverable:
        return {'status':'preflight_current_page_reconciliation',
                'recoverable_from_current_page':recoverable}
    return {'status': 'preflight_clear', 'recoverable_from_current_page':[]}


def publish(folder, state):
    from .control_registry import registry_report
    command = active_command(state)
    v = state.values
    summary = {'status': v.get('status'), 'module_index': v.get('index'),
               'pure_review_pending': tuple(state.next) == ('review_boundary',) and not v.get('command'),
               'scopes': v.get('scopes', {}), 'pending_kind': command and command['kind'],
               'active_module_id': command and command['module_id'],
               'recovery_reason':v.get('recovery_reason'), 'recovery_attempts':v.get('recovery_attempts',{}),
               'repair_attempts':v.get('repair_attempts',{})}
    summary['value_check_attempts']=v.get('value_check_attempts',{})
    summary['value_comparison']=v.get('value_comparison',[])
    summary['verified_control_methods']=len(v.get('control_methods',[]))
    summary['control_attempts']=len(v.get('control_history',[]))
    summary['verification_skipped_count']=sum(
        item.get('status') == 'verification_skipped'
        for result in v.get('results', {}).values()
        for item in result.get('results', {}).values())
    summary['run_dir']=str(folder)
    summary['agent_tuning']=v.get('agent_tuning', True)
    summary['manual_review_count']=sum(len(r.get('manual_review', [])) for r in v.get('results', {}).values())
    summary['startup_reconciliation']=v.get('startup_reconciliation',[])
    summary['coverage_gaps']=v.get('coverage_gaps',[])
    summary['program_inventory']=v.get('manifest',{}).get('program_inventory',False)
    modules=v.get('manifest',{}).get('modules',[])
    from .required_answer_gate import unanswerable_required_fields
    summary['unanswerable_required_fields']=[{**item,'module_id':mid}
        for mid,result in v.get('results',{}).items()
        for item in unanswerable_required_fields(result)]
    if isinstance(v.get('index'),int) and 0<=v['index']<len(modules):
        result=v.get('results',{}).get(modules[v['index']]['id'],{})
        snapshot=result.get('current') or result.get('snapshot')
        if snapshot and isinstance(snapshot.get('fields'),list):
            from .control_exceptions import build_control_exception_request
            summary['control_exception_count']=len(build_control_exception_request(
                snapshot,(result.get('last_receipt') or {}).get('results',[]))['fields'])
    if summary['program_inventory']:
        from .page_planner import coverage_gaps
        summary['coverage_gaps']=coverage_gaps(v['manifest'],v.get('results',{}),v.get('omitted_modules',[]))
    phase = {'activate_module':'awaiting_module_navigation','edit_module':'awaiting_module_edit',
             'add_module_record':'awaiting_module_add',
             'delete_module_record':'awaiting_module_delete',
             'verify_module_record_absent':'awaiting_module_absence_verification',
             'cancel_module_edit':'awaiting_module_cancel','reconcile_module_cancel':'awaiting_module_cancel_reconciliation',
             'control_fill':'awaiting_control','fill':'awaiting_edge', 'save':'awaiting_save', 'observe':'awaiting_observation',
             'save_scope':'awaiting_scope_save','verify_autosave':'awaiting_scope_save','reconcile_save':'awaiting_reconciliation'}.get(command and command['kind'], v.get('status'))
    atomic_json(folder/'request.json', {'phase': phase, 'command': command, 'controlRegistry': registry_report()})
    # This is the trusted graph's current dispatch gate, not a model-produced policy.
    if command:
        atomic_json(folder/'policy.json', {'target': command['target'], 'module_id': command['module_id'],
            'module_selector': command['module_selector'], 'fill': True, 'save': v['allow_save'],
            'application_gate': True, 'active_command_id': command['command_id'], 'active_kind': command['kind']})
    else:
        atomic_json(folder/'policy.json', {'application_gate':True, 'active_command_id':None})
    atomic_json(folder/'summary.json', summary)
    from .reporting import write_reports
    write_reports(folder, v)
    if v.get('control_methods'):
        from .control_methods import sync_methods
        sync_methods(v['control_methods'])
    history = v.get('control_history') or []
    if history and history[-1]['receipt'].get('evidence', {}).get('committed') is True:
        from .control_resolution import learn_recipe
        item = history[-1]
        fields = (v.get('control_diagnostics', {}).get('snapshot') or {}).get('fields', [])
        matches = [f for f in fields if f['signature'] == item['command']['field_signature']]
        if len(matches) == 1:
            learn_recipe(matches[0], item['command']['adapter'], item['receipt'])
    return summary


def reusable_recovery_observation(folder, values):
    """Find the one completed observation produced by the latest recovery.

    This only permits local re-evaluation after recovery code changes.  It
    never dispatches another browser read and never rewrites the journal.
    """
    if values.get('status') != 'recovery_blocked':
        raise ContractError('reprocess_requires_recovery_block')
    history = values.get('recovery_history') or []
    if not history:
        raise ContractError('reprocess_requires_recovery_history')
    latest = history[-1]
    mid = values['manifest']['modules'][values['index']]['id']
    old = values['results'][mid].get('command') or {}
    migration = latest.get('migration_authorization') or {}
    repair = latest.get('repair_authorization') or {}
    migration_bound = (migration.get('basis')
                       and migration.get('command_id') == old.get('command_id'))
    repair_bound = repair.get('basis') and repair.get('module_id') == mid
    if latest.get('module_id') != mid or not (migration_bound or repair_bound):
        raise ContractError('reprocess_requires_bound_recovery_authorization')
    module = values['manifest']['modules'][values['index']]
    candidates = []
    for path in (folder/'writer').glob('*.json'):
        journal = load(path)
        receipt = journal.get('receipt') or {}
        snapshot = receipt.get('snapshot') or {}
        if (journal.get('kind') == 'observe' and journal.get('started_at', 0) >= latest.get('at', float('inf'))
                and receipt.get('kind') == 'observe' and receipt.get('settled') is True
                and receipt.get('status') == 'completed' and snapshot
                and snapshot.get('target') == values['manifest']['target']
                and snapshot.get('module_id') == mid
                and snapshot.get('module_selector') == module['selector']):
            candidates.append((journal, receipt))
    if len(candidates) != 1:
        raise ContractError('reprocess_observation_not_unique')
    journal, receipt = candidates[0]
    command = {'version':1, 'command_id':journal['command_id'], 'kind':'observe', 'browser':'edge',
               'target':values['manifest']['target'], 'module_id':mid, 'module_selector':module['selector'],
               'capture':copy.deepcopy(module.get('capture',{})), 'deadline':values['deadline']}
    validate_receipt(command, receipt)
    return command


def reusable_save_reconciliation(folder, values):
    """Create a fresh read-only save reconciliation after detector fixes."""
    if values.get('status') != 'needs_reconciliation':
        raise ContractError('reprocess_save_requires_reconciliation_state')
    old = values.get('command') or {}
    if old.get('kind') != 'reconcile_save' or not old.get('original_command_id'):
        raise ContractError('reprocess_save_requires_original_command')
    reconciliation = load(folder/'writer'/(old['command_id']+'.json'))
    original = load(folder/'writer'/(old['original_command_id']+'.json'))
    if (reconciliation.get('receipt', {}).get('settled') is not True
            or reconciliation.get('receipt', {}).get('status') != 'unconfirmed'
            or original.get('kind') not in {'save', 'save_scope'}
            or original.get('receipt', {}).get('settled') is not True
            or original.get('save_stage') not in {'save_click_returned', 'observation_returned'}):
        raise ContractError('reprocess_save_evidence_not_settled')
    return {**copy.deepcopy(old), 'command_id': str(uuid.uuid4()), 'deadline': time.time()+300}


def repair_updates(state, *, basis, renew_seconds=0, budget_basis=None, corrections=None):
    """Resume the original stopped application, never bypass its observation gate."""
    values = state.values
    mid = values['manifest']['modules'][values['index']]['id']
    current_result = values.get('results', {}).get(mid, {})
    current_receipt = current_result.get('last_receipt') or {}
    # An executor implementation bug can consume the two automatic retries
    # even though every browser call ended.  Permit an explicit, read-first
    # repair only for terminal locator conflicts; unknown writes and other
    # conflict types remain ineligible.
    settled_locator_conflict = (
        values.get('status') == 'recovery_blocked'
        and values.get('recovery_reason') == 'recovery_limit'
        and bool(values.get('recovery_history'))
        and current_receipt.get('kind') == 'fill'
        and current_receipt.get('settled') is True
        and current_receipt.get('status') in {'completed', 'partial'}
        and bool(current_receipt.get('results'))
        and not any(r.get('status') == 'unknown' for r in current_receipt['results'])
        and all(r.get('status') != 'conflict' or r.get('reason') == 'field_missing_or_ambiguous'
                for r in current_receipt['results'])
        and any(r.get('status') == 'conflict' for r in current_receipt['results']))
    settled_terminal_recovery = (
        values.get('status') == 'recovery_blocked'
        and values.get('recovery_reason') == 'recovery_limit'
        and bool(values.get('recovery_history'))
        and repair_eligible(current_result))
    code_recovery = (values.get('status') == 'recovery_blocked'
                     and values.get('recovery_reason') in {
                         'recovery_field_identity_changed', 'stale_snapshot', 'recovery_value_unreadable',
                         'duplicate_or_unknown_field', 'invalid_option_value'}
                     and bool(values.get('recovery_history'))) or settled_locator_conflict or settled_terminal_recovery
    if state.next or (values.get('status') not in {'module_blocked', 'scope_review_blocked'} and not code_recovery) or values.get('command'):
        raise ContractError('repair_requires_terminal_module_blocked')
    old = values['results'][mid].get('command') or {}
    prior_migration = (values.get('recovery_history') or [{}])[-1].get('migration_authorization') or {}
    prior_repair = (values.get('recovery_history') or [{}])[-1].get('repair_authorization') or {}
    code_recovery = settled_locator_conflict or settled_terminal_recovery or (code_recovery and (
        values.get('recovery_reason') in {'recovery_value_unreadable', 'duplicate_or_unknown_field',
                                          'invalid_option_value'}
        or (prior_repair.get('basis') and prior_repair.get('module_id') == mid)
        or (prior_migration.get('basis')
            and prior_migration.get('command_id') == old.get('command_id')
            and prior_migration.get('module_id') == mid)))
    if (not isinstance(basis, str) or not basis.strip()
            or (not repair_eligible(values['results'][mid]) and not code_recovery)):
        raise ContractError('repair_requires_settled_fill_and_basis')
    # Explicit repair after a new correction is not an automatic retry loop.
    # Retain the cumulative count; only automatic recovery is capped at two.
    def scrub_profiles(value):
        if isinstance(value, dict):
            return {k: redacted_profile(v) if k == 'profile' else scrub_profiles(v) for k, v in value.items()}
        if isinstance(value, list):
            return [scrub_profiles(v) for v in value]
        return copy.deepcopy(value)
    results = scrub_profiles(values['results'])
    updates = {'profile': redacted_profile(values['profile']), 'results': results,
               'mapping_cache': scrub_profiles(values.get('mapping_cache', {})), 'recovery_requested': True,
               'repair_authorization': {'basis': basis, 'module_id': mid, 'at': time.time()}}
    if code_recovery and prior_migration.get('basis'):
        # Preserve the prior one-shot same-command migration authority.  This
        # authorizes evaluation of a fresh read after the implementation fix;
        # it does not settle or rewrite the old fill journal.
        updates['migration_authorization'] = copy.deepcopy(prior_migration)
    resolved = []
    for label, source, rule in corrections or []:
        matches = [f for f in results[mid]['current']['fields'] if f['label'] == label and not f.get('protected')]
        if len(matches) != 1:
            raise ContractError('correction_label_not_unique')
        resolved.append({'field_id': matches[0]['id'], 'source': source, 'transform': rule})
    updates['repair_authorization']['corrections'] = resolved
    if renew_seconds:
        if (type(renew_seconds) is not int or not 1 <= renew_seconds <= 1800
                or not isinstance(budget_basis, str) or not budget_basis.strip()):
            raise ContractError('explicit_repair_budget_basis_required')
        deadline = time.time()+renew_seconds
        updates['deadline'] = deadline
        updates['budget_history'] = values.get('budget_history', [])+[
            {'previous_deadline': values['deadline'], 'deadline': deadline,
             'seconds': renew_seconds, 'basis': budget_basis, 'at': time.time()}]
    elif time.time() >= values['deadline']:
        raise ContractError('repair_budget_expired')
    return updates


def operate(args):
    if (Path(__file__).resolve().parent.parent/'MIGRATING.txt').exists():
        raise ContractError('storage_migration_in_progress')
    if args.action == 'preflight':
        return check_prior(args.prior_root)
    if args.action == 'plan-experiences':
        profile_path = args.profile
        if not profile_path:
            local = Path(__file__).resolve().parent.parent/'private'/'local-config.json'
            profile_path = load(local)['profile']
        return {'status': 'planned',
                'available_sections': list(args.section),
                'placements': plan_experience_placement(load(profile_path), args.section)}
    from .runtime import local_path
    folder = local_path(args.run_dir, writable=True)
    if args.action == 'fresh-start':
        base=folder
        base.mkdir(parents=True, exist_ok=True)
        folder=base/'attempts'/(time.strftime('%Y%m%d-%H%M%S')+'-'+uuid.uuid4().hex[:8])
        args.action='start'
    folder.mkdir(parents=True, exist_ok=True)
    if args.action == 'reconcile':
        old_path = local_path(args.journal)
        old = load(old_path)
        packet = load(old_path.parent.parent/'request.json')['command']
        if packet is None:
            from .graph import build_graph
            with SqliteSaver.from_conn_string(str(old_path.parent.parent/'checkpoints.sqlite')) as old_saver:
                old_graph = build_graph(review_model(), old_saver)
                packet = old_graph.get_state({'configurable':{'thread_id':'module'}}).values.get('command')
        if not packet or old.get('kind') != 'save' or packet['command_id'] != old['command_id'] or not old.get('receipt'):
            raise ContractError('legacy_save_command_required')
        if not args.user_saved or not args.basis:
            raise ContractError('explicit_user_intervention_required')
        packet = dict(packet, kind='reconcile_save', command_id=str(uuid.uuid4()), deadline=time.time()+300,
                      original_command_id=old['command_id'], original_journal=str(old_path),
                      original_receipt=old['receipt'], user_basis=args.basis)
        with owner_lock(folder):
            if (folder/'request.json').exists():
                raise ContractError('recovery_run_already_exists')
            atomic_json(folder/'request.json', {'phase':'awaiting_reconciliation','command':packet})
            atomic_json(folder/'policy.json', {'application_gate':True,'active_command_id':packet['command_id'],
                'active_kind':packet['kind'],'target':packet['target'],'module_id':packet['module_id'],
                'module_selector':packet['module_selector'],'fill':False,'save':False})
        return {'status':'awaiting_reconciliation','pending_kind':'reconcile_save'}
    with owner_lock(folder), SqliteSaver.from_conn_string(str(folder/'application.sqlite')) as saver:
        from .runtime import components
        knowledge, semantic_model = components()
        graph = build_application(review_model(), saver, knowledge=knowledge, semantic_model=semantic_model)
        config = {'configurable': {'thread_id':'application'}, 'recursion_limit':1000}
        if args.action == 'repair-preparation':
            from .preparation_recovery import recover_preparation
            recover_preparation(saver,config,folder)
            return publish(folder,graph.get_state(config,subgraphs=True))
        state = graph.get_state(config, subgraphs=True)
        if args.action == 'resume-pure-review':
            from .scope_isolation import assert_settled_history
            if (tuple(state.next) != ('review_boundary',) or state.values.get('command')
                    or state.values.get('status') != 'module_filled' or not args.basis.strip()):
                raise ContractError('pure_review_resume_requires_ended_writers_and_review_boundary')
            assert_settled_history(state.values, folder)
            graph.invoke(None, config)
            return publish(folder, graph.get_state(config, subgraphs=True))
        if args.action == 'start':
            if state.values:
                raise ContractError('application_already_exists')
            prior = check_prior(args.prior_root)
            manifest=load(args.manifest)
            if prior['status']=='prior_reconciliation_required':
                from .scope_isolation import validate_isolation
                if validate_isolation(manifest,prior):
                    prior={**prior,'status':'preflight_current_page_reconciliation',
                           'recoverable_from_current_page':prior.get('recoverable_from_current_page',[])+prior['blockers']}
            allowed_prior={'preflight_clear','preflight_current_page_reconciliation'}
            if prior['status'] not in allowed_prior:
                atomic_json(folder/'preflight.json', prior)
                return prior
            local = Path(__file__).resolve().parent.parent/'private'/'local-config.json'
            profile = args.profile or load(local)['profile']
            graph.invoke(application_state(load(profile), manifest, allow_save=args.allow_save,
                agent_tuning=(getattr(args, 'agent_tuning', None) != 'off' if getattr(args, 'agent_tuning', None) is not None
                              else load(local).get('agent_tuning', True) if local.exists() else True),
                startup_reconciliation=prior.get('recoverable_from_current_page',[])), config)
        elif args.action == 'reconcile-add':
            if state.next or state.values.get('status') not in {'module_add_blocked','awaiting_module_add'}:
                raise ContractError('add_reconciliation_requires_stopped_add')
            prior=(state.values.get('module_add_history') or [])[-1]
            old=prior['command'];receipt=prior['receipt']
            journal=load(folder/'writer'/(old['command_id']+'.json'))
            if (receipt.get('settled') is not True or journal.get('receipt')!=receipt
                    or not old.get('inline_repeater')
                    or not any(e.get('method')=='click' and e.get('status')=='returned' for e in journal.get('instrumentation',[]))):
                raise ContractError('prior_add_completion_not_proven')
            relative=old['record_selector'].removeprefix(':scope').strip()
            sibling_filter=relative.split('>')[-1].strip()
            selector=old['collection_selector']+' '+relative+f':nth-child({old["expected_record_count"]+1} of {sibling_filter})'
            manifest=copy.deepcopy(state.values['manifest'])
            manifest['modules'][state.values['index']]['selector']=selector
            command={**old,'module_selector':selector,'command_id':str(uuid.uuid4()),
                     'original_command_id':old['command_id'],'reconcile_only':True,'deadline':time.time()+120}
            graph.update_state(config,{'manifest':manifest,'command':command,'status':'awaiting_module_add'},as_node='enter')
            graph.invoke(None,config)
        elif args.action == 'audit-local-failure':
            if state.next:
                raise ContractError('local_audit_requires_stopped_application')
            from .local_call_audit import audit_local_failure
            updates, proof = audit_local_failure(state.values, folder)
            (folder/'local-call-audits').mkdir(exist_ok=True)
            atomic_json(folder/'local-call-audits'/(proof['original_command_id']+'.json'), proof)
            graph.update_state(config, updates, as_node='reconcile_fill')
        elif args.action == 'revisit-held':
            from .scope_isolation import revisit_held
            from .save_preflight_recovery import preflight_writer_lock
            with preflight_writer_lock(folder):
                state=graph.get_state(config,subgraphs=True)
                updates=revisit_held(state.values,state.next,folder,args.module_id,args.basis,args.renew_seconds,
                                     writer_lock_held=True,refresh_scope=args.refresh_scope)
                # No dispatched command or old child interrupt is resumed.
                graph.update_state(config,updates,as_node='reconcile_fill')
                if args.reread:
                    graph.update_state(config,{'status':'advance'},as_node='advance')
                    graph.invoke(None,config)
                return publish(folder,graph.get_state(config,subgraphs=True))
        elif args.action == 'extend-inventory':
            from .incremental_inventory import extend_empty_collections
            from .save_preflight_recovery import preflight_writer_lock
            with preflight_writer_lock(folder):
                state=graph.get_state(config,subgraphs=True)
                updates=extend_empty_collections(state.values,state.next,folder,load(Path(args.inventory)),
                    args.basis,args.renew_seconds,writer_lock_held=True)
                graph.update_state(config,updates,as_node='advance')
                graph.invoke(None,config)
                return publish(folder,graph.get_state(config,subgraphs=True))
        elif args.action == 'reopen-saved-scope':
            from .saved_scope_revision import reopen_saved_scope
            from .save_preflight_recovery import preflight_writer_lock
            with preflight_writer_lock(folder):
                state=graph.get_state(config,subgraphs=True)
                updates=reopen_saved_scope(state.values,state.next,folder,args.module_id,args.basis,writer_lock_held=True)
                graph.update_state(config,updates,as_node='advance')
                graph.invoke(None,config)
                return publish(folder,graph.get_state(config,subgraphs=True))
        elif args.action == 'split-record-boundary':
            from .record_boundary_recovery import prepare_record_split
            from .save_preflight_recovery import preflight_writer_lock
            with preflight_writer_lock(folder):
                state=graph.get_state(config,subgraphs=True)
                updates=prepare_record_split(state.values,state.next,folder,load(Path(args.inventory)),
                    args.module_id,args.basis,writer_lock_held=True)
                graph.update_state(config,updates,as_node='advance')
                graph.invoke(None,config)
                return publish(folder,graph.get_state(config,subgraphs=True))
        elif args.action == 'rebind-held-records':
            from .held_record_recovery import rebind_held_records
            from .save_preflight_recovery import preflight_writer_lock
            with preflight_writer_lock(folder):
                state=graph.get_state(config,subgraphs=True)
                updates=rebind_held_records(state.values,state.next,folder,load(Path(args.inventory)),
                    args.basis,args.renew_seconds,writer_lock_held=True)
                graph.update_state(config,updates,as_node='advance')
                graph.invoke(None,config)
                return publish(folder,graph.get_state(config,subgraphs=True))
        elif args.action == 'rebind-collapsed-education':
            from .collapsed_record_recovery import rebind_collapsed_education
            from .save_preflight_recovery import preflight_writer_lock
            with preflight_writer_lock(folder):
                state=graph.get_state(config,subgraphs=True)
                updates=rebind_collapsed_education(state.values,state.next,folder,load(Path(args.inventory)),
                    args.scope_id,args.basis,writer_lock_held=True)
                graph.update_state(config,updates,as_node='advance')
                graph.invoke(None,config)
                return publish(folder,graph.get_state(config,subgraphs=True))
        elif args.action == 'normalize-profile-files':
            from .profile_file_normalization import normalize_profile_files
            from .save_preflight_recovery import preflight_writer_lock
            with preflight_writer_lock(folder):
                state=graph.get_state(config,subgraphs=True)
                updates=normalize_profile_files(state.values,state.next,folder,Path(args.base_dir),args.basis,
                                                writer_lock_held=True)
                graph.update_state(config,updates)
                return publish(folder,graph.get_state(config,subgraphs=True))
        elif args.action == 'sync-confirmed-profile':
            from .confirmed_profile_updates import sync_confirmed_facts
            from .save_preflight_recovery import preflight_writer_lock
            with preflight_writer_lock(folder):
                state=graph.get_state(config,subgraphs=True)
                updates=sync_confirmed_facts(state.values,state.next,folder,load(Path(args.patch)),
                                             writer_lock_held=True)
                graph.update_state(config,updates)
                return publish(folder,graph.get_state(config,subgraphs=True))
        elif args.action == 'recover-observation':
            from .observation_completion import recover_observation, persist_observation_audit
            from .save_preflight_recovery import preflight_writer_lock
            with preflight_writer_lock(folder):
                state=graph.get_state(config,subgraphs=True)
                proof, receipt=recover_observation(state.values,state.next,folder,args.basis,writer_lock_held=True)
                persist_observation_audit(folder,proof)
                graph.invoke(Command(resume=receipt),config)
                return publish(folder,graph.get_state(config,subgraphs=True))
        elif args.action == 'defer-independent':
            from .scope_isolation import defer_independent
            updates=defer_independent(state.values,state.next,folder)
            graph.update_state(config,updates,as_node='advance')
            graph.invoke(None,config)
        elif args.action == 'resolve-controls':
            from .control_resolution import assert_control_settled, resolve_controls
            if state.next:
                raise ContractError('control_requires_stopped_application')
            assert_control_settled(state.values)
            return resolve_controls(state.values, knowledge, review_model(), folder)
        elif args.action == 'continue-after-control':
            if state.next or state.values.get('status') != 'control_committed':
                raise ContractError('verified_control_required')
            # Re-enter through a current-page observation, never the old child plan.
            manifest = copy.deepcopy(state.values['manifest'])
            manifest['modules'][state.values['index']].pop('snapshot', None)
            graph.invoke(Command(update={'manifest': manifest, 'command': None,
                'deadline': max(state.values['deadline'], time.time()+600),
                'scope_reviews': {}, 'recovery_reason': None}, goto='enter'), config)
        elif args.action == 'open-module':
            if state.next or state.values.get('status') not in {'control_committed','module_edit_opened','module_edit_not_ready','module_blocked'}:
                raise ContractError('module_edit_requires_stopped_settled_application')
            graph.invoke(Command(goto='prepare_module_edit'),config)
        elif args.action == 'reconcile-control':
            if state.next or state.values.get('status')!='control_blocked':
                raise ContractError('control_recovery_requires_stopped_control')
            graph.invoke(Command(goto='reconcile_control'),config)
        elif args.action == 'apply-control':
            if state.next or state.values.get('status') not in {'needs_reconciliation','control_committed','control_blocked','module_blocked','module_edit_opened','recovery_blocked'}:
                raise ContractError('control_requires_stopped_application')
            if state.values.get('status')=='recovery_blocked':
                current_id=state.values['manifest']['modules'][state.values['index']]['id']
                prior_receipt=state.values.get('results',{}).get(current_id,{}).get('last_receipt') or {}
                if prior_receipt.get('settled') is not True:
                    raise ContractError('prior_control_call_unsettled')
            if state.values.get('status')=='control_blocked' and not state.values['control_history'][-1]['receipt']['settled']:
                raise ContractError('prior_control_call_unsettled')
            # Saving was never dispatched in this recovery path. Do not use
            # an arbitrary unknown-save state as permission for more writes.
            if state.values.get('status')=='needs_reconciliation':
                old=state.values.get('command') or {}
                prior=load(folder/'writer'/(old['original_command_id']+'.json'))
                if (prior.get('receipt',{}).get('evidence',{}).get('signal')!='preexisting_dialog_or_invalid_scope'
                        or not prior['receipt']['settled'] or prior.get('save_stage')
                        or any(e.get('method')=='click' for e in prior.get('instrumentation',[]))):
                    raise ContractError('save_may_have_been_dispatched')
            from .control_methods import sync_methods
            updates={'control_methods':sync_methods(state.values.get('control_methods',[])),
                'control_request':{'label':args.label,'field_id':args.field_id,'source':args.source,'adapter':args.adapter,
                                   'profile_path':args.profile}}
            graph.invoke(Command(update=updates,goto='prepare_control'),config)
        elif args.action == 'inspect-controls':
            if not state.values:
                raise ContractError('diagnosis_requires_stopped_application')
            pending=active_command(state)
            if state.next:
                if not pending or pending['kind']!='observe' or not pending.get('capture',{}).get('controlDiagnostics') or (folder/'writer'/(pending['command_id']+'.json')).exists():
                    raise ContractError('diagnosis_requires_stopped_application')
            else:
                graph.invoke(Command(goto='prepare_diagnostic'), config)
                state=graph.get_state(config,subgraphs=True);pending=active_command(state)
            if args.browser_id:
                # Reconnect is read-only and bound to the same tab and URL;
                # never migrate old write receipts or the application manifest.
                pending=copy.deepcopy(pending)
                pending['target']['browser_id']=args.browser_id
                pending['deadline']=time.time()+120
                graph.update_state(config,{'command':pending},as_node='prepare_diagnostic')
        elif args.action == 'repair':
            updates = repair_updates(state, basis=args.basis, renew_seconds=args.renew_seconds,
                                     budget_basis=args.budget_basis, corrections=getattr(args, 'correct_field', None))
            graph.update_state(config, updates, as_node='fill_and_review')
            graph.invoke(None, config)
        elif args.action == 'cancel-module':
            values=state.values
            command=values.get('command') or {}
            if state.next or not args.basis.strip():
                raise ContractError('cancel_requires_settled_validation_failure_and_basis')
            mid=values['manifest']['modules'][values['index']]['id']
            preserved=values.get('results',{}).get(mid,{}).get('preserved_blank_ids',[])
            reconcile_after_click=False
            if values.get('status')=='needs_reconciliation' and command.get('kind')=='reconcile_save':
                reconciliation=load(folder/'writer'/(command['command_id']+'.json'))
                original=load(folder/'writer'/(command.get('original_command_id','')+'.json'))
                original_receipt=original.get('receipt') or {}
                validation_count=(original_receipt.get('evidence') or {}).get('ui',{}).get('validation_error_count')
                authorized=(preserved and reconciliation.get('receipt',{}).get('settled') is True
                    and reconciliation.get('receipt',{}).get('status')=='unconfirmed'
                    and reconciliation.get('receipt',{}).get('evidence',{}).get('signal')=='current_draft_only'
                    and original.get('kind')=='save_scope' and original.get('save_stage')=='observation_returned'
                    and original_receipt.get('settled') is True and original_receipt.get('status')=='unconfirmed'
                    and original_receipt.get('evidence',{}).get('signal')=='validation_failed'
                    and validation_count==len(preserved))
            elif values.get('status')=='module_cancel_blocked' and values.get('module_cancel_history'):
                # A local selector miss is explicitly settled before any click.
                # It may be retried after the detector is fixed; the executor
                # still rechecks the reviewed snapshot and validation errors.
                prior=values['module_cancel_history'][-1]
                prior_receipt=prior.get('receipt') or {}
                journal=load(folder/'writer'/(prior.get('command',{}).get('command_id','')+'.json'))
                retry_without_write=(preserved and prior_receipt.get('settled') is True
                    and prior_receipt.get('status')=='unconfirmed'
                    and prior_receipt.get('evidence',{}).get('reason')=='cancel_control_changed'
                    and journal.get('kind')=='cancel_module_edit'
                    and not any(item.get('method')=='click' for item in journal.get('instrumentation',[])))
                reconcile_after_click=(preserved and prior_receipt.get('settled') is True
                    and prior_receipt.get('status')=='unconfirmed'
                    and prior_receipt.get('evidence',{}).get('reason')=='cancel_not_confirmed'
                    and journal.get('kind')=='cancel_module_edit'
                    and any(item.get('stage')=='module_cancel' and item.get('method')=='click'
                            and item.get('status')=='returned' for item in journal.get('instrumentation',[])))
                authorized=retry_without_write or reconcile_after_click
            else:
                authorized=False
            if not authorized:
                raise ContractError('cancel_requires_settled_validation_failure_and_basis')
            goto='prepare_module_cancel_reconciliation' if reconcile_after_click else 'prepare_module_cancel'
            graph.invoke(Command(update={'omission_authorization':{
                'basis':args.basis,'module_id':mid,'at':time.time(),
                'preserved_blank_field_ids':copy.deepcopy(preserved)}},goto=goto),config)
        elif args.action == 'renew-undispatched-add':
            from .undispatched_add import renew_undispatched_add
            from .save_preflight_recovery import preflight_writer_lock
            with preflight_writer_lock(folder):
                state=graph.get_state(config,subgraphs=True)
                updates=renew_undispatched_add(state.values,state.next,folder,load(Path(args.inventory)),
                    args.basis,args.renew_seconds,writer_lock_held=True)
                graph.update_state(config,updates,as_node='enter')
                return publish(folder,graph.get_state(config,subgraphs=True))
        elif args.action == 'renew-observation':
            from .save_preflight_recovery import preflight_writer_lock
            with preflight_writer_lock(folder):
                pending=active_command(state)
                task_names={t.name for t in state.tasks}
                if (not args.basis.strip() or not 1<=args.renew_seconds<=1800 or not pending
                        or pending.get('kind')!='observe' or len(task_names)!=1
                        or not task_names<={'observe','read_diagnostic'}
                        or (folder/'writer'/(pending['command_id']+'.json')).exists()):
                    raise ContractError('renew_requires_undispatched_read_only_observation')
                deadline=time.time()+args.renew_seconds
                history=state.values.get('budget_history',[])+[{'previous_deadline':state.values['deadline'],
                    'previous_command':copy.deepcopy(pending),'basis':args.basis,'at':time.time(),'deadline':deadline}]
                graph.update_state(config,{'command':{**pending,'deadline':deadline},'deadline':deadline,
                    'budget_history':history},as_node='prepare_diagnostic' if 'read_diagnostic' in task_names else 'enter')
                return publish(folder,graph.get_state(config,subgraphs=True))
        elif args.action == 'recover-save-preflight':
            from .save_preflight_recovery import recover_save_preflight,preflight_writer_lock
            with preflight_writer_lock(folder):
                updates=recover_save_preflight(state.values,active_command(state),folder,args.basis,writer_lock_held=True)
                graph.update_state(config,updates,as_node='save_scope')
                graph.invoke(Command(goto='enter'),config)
                return publish(folder,graph.get_state(config,subgraphs=True))
        elif args.action == 'recover':
            if state.next or state.values.get('status') not in {'module_blocked','recovery_blocked'}:
                raise ContractError('recovery_requires_stopped_fill_module')
            values=state.values
            mid=values['manifest']['modules'][values['index']]['id']
            results=dict(values['results']);result=dict(results[mid])
            old=result.get('command')
            if old:
                journal=load(folder/'writer'/(old['command_id']+'.json'))
                result['last_receipt']=recovered_fill_receipt(folder,old,journal)
                # Preserve deferred child states from the audit derivation. The
                # audit establishes completion only; it never verifies values.
                for item in result['last_receipt'].get('results',[]):
                    if item.get('status')=='deferred':
                        result.setdefault('results',{})[item['id']]=copy.deepcopy(item)
            results[mid]=result
            updates={'results':results,'status':'module_blocked','recovery_requested':True}
            if args.migration_basis:
                if not old or old.get('kind')!='fill':
                    raise ContractError('migration_requires_old_fill_command')
                updates['migration_authorization']={'basis':args.migration_basis,'command_id':old['command_id'],
                    'module_id':mid,'at':time.time()}
            if args.renew_seconds:
                if not args.migration_basis or not 1<=args.renew_seconds<=1800:
                    raise ContractError('explicit_migration_budget_required')
                updates['budget_history']=values.get('budget_history',[])+[{'previous_deadline':values['deadline'],
                    'basis':args.migration_basis,'at':time.time()}]
                updates['deadline']=time.time()+args.renew_seconds
            graph.update_state(config,updates,as_node='fill_and_review')
            graph.invoke(None,config)
        elif args.action == 'reprocess-recovery':
            if state.next or state.values.get('command'):
                raise ContractError('reprocess_requires_stopped_application')
            command = reusable_recovery_observation(folder, state.values)
            graph.update_state(config, {'status':'awaiting_fill_reconciliation', 'command':command,
                                        'recovery_reason':''}, as_node='prepare_recovery')
            # Enter the existing reconciliation interrupt.  The normal resume
            # path will validate and consume the already-written receipt.
            graph.invoke(None, config)
        elif args.action == 'reprocess-save-reconciliation':
            if state.next:
                raise ContractError('reprocess_save_requires_stopped_application')
            source_values=state.values
            updates={}
            if source_values.get('manifest',{}).get('program_inventory') and source_values.get('status') in {'incomplete_coverage','module_blocked'}:
                historical=saver.reconciliation_values('application')
                if historical:
                    source_values=historical
                    results=copy.deepcopy(state.values['results'])
                    module=historical['manifest']['modules'][historical['index']]
                    results[module['id']].pop('coverage_hold',None)
                    updates={'index':historical['index'],'results':results}
            command = reusable_save_reconciliation(folder, source_values)
            graph.update_state(config, {**updates,'status':'awaiting_save_reconciliation','command':command},
                               as_node='save_scope')
            graph.invoke(None, config)
        elif args.action == 'resume':
            command = active_command(state)
            if command is None:
                consumed = completed_semantic_error_receipt(state, folder)
                if consumed is not None:
                    from .save_preflight_recovery import preflight_writer_lock
                    with preflight_writer_lock(folder):
                        proof = {'kind':'local_semantic_error_resume','command_id':consumed['command_id'],
                                 'at':time.time(),'browser_command_replayed':False,
                                 'basis':'consumed settled fill receipt; retry only failed local graph node'}
                        atomic_json(folder/('local-error-resume-'+consumed['command_id']+'.json'),proof)
                        graph.invoke(None, config)
                    return publish(folder, graph.get_state(config, subgraphs=True))
            replay_completed_observation = False
            failed_observer = (len(state.tasks) == 1 and state.tasks[0].name == 'observe'
                               and bool(state.tasks[0].error))
            if not command and failed_observer:
                candidate = state.values.get('command') or {}
                if candidate.get('kind') == 'observe':
                    stored = saver.get_tuple(config)
                    journal = load(folder/'writer'/(candidate['command_id']+'.json'))
                    receipt = validate_receipt(candidate, journal['receipt'])
                    has_resume = any(channel == '__resume__' and
                        (value == receipt or value == [receipt])
                        for _, channel, value in (stored.pending_writes or []))
                    if receipt.get('settled') is True and receipt.get('status') == 'completed' and has_resume:
                        command = candidate
                        replay_completed_observation = True
            if not command:
                raise ContractError('application_not_awaiting_receipt')
            r = load(folder/'receipt.json')
            j = load(folder/'writer'/(command['command_id']+'.json'))
            if j.get('receipt') != r or r.get('command_id') != command['command_id']:
                raise ContractError('receipt_not_current_writer_journal')
            graph.invoke(None if replay_completed_observation else Command(resume=r), config)
        elif not state.values:
            raise ContractError('application_not_found')
        return publish(folder, graph.get_state(config, subgraphs=True))


def main(argv=None):
    from .control_registry import adapter_names, assert_registry
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest='action', required=True)
    registry_check = sub.add_parser('check-control-registry')
    registry_check.add_argument('--executor-report', required=True)
    preflight = sub.add_parser('preflight'); preflight.add_argument('--prior-root', required=True)
    plan_experiences = sub.add_parser('plan-experiences')
    plan_experiences.add_argument('--profile')
    plan_experiences.add_argument('--section', action='append', required=True,
                                  choices=['projects','internships','employment','work'])
    start = sub.add_parser('start')
    fresh_start = sub.add_parser('fresh-start')
    for command in (start,fresh_start):
        command.add_argument('--manifest', required=True)
        command.add_argument('--prior-root', required=True)
        command.add_argument('--profile')
        command.add_argument('--allow-save', action='store_true')
        command.add_argument('--agent-tuning', choices=['on', 'off'], default=None,
                             help='off: no model calls; unresolved text/dropdowns get marked draft fallbacks')
    resume = sub.add_parser('resume'); status = sub.add_parser('status'); recover = sub.add_parser('recover')
    pure_review_parser = sub.add_parser('resume-pure-review')
    pure_review_parser.add_argument('--run-dir', required=True)
    pure_review_parser.add_argument('--basis', required=True)
    reprocess_recovery = sub.add_parser('reprocess-recovery')
    local_audit = sub.add_parser('audit-local-failure')
    reconcile_add = sub.add_parser('reconcile-add')
    revision_parser=sub.add_parser('reopen-saved-scope')
    revision_parser.add_argument('--run-dir',required=True)
    revision_parser.add_argument('--module-id',required=True)
    revision_parser.add_argument('--basis',required=True)
    split_parser=sub.add_parser('split-record-boundary')
    split_parser.add_argument('--run-dir',required=True)
    split_parser.add_argument('--inventory',required=True)
    split_parser.add_argument('--module-id',required=True)
    split_parser.add_argument('--basis',required=True)
    normalize_files_parser=sub.add_parser('normalize-profile-files')
    normalize_files_parser.add_argument('--base-dir',required=True)
    normalize_files_parser.add_argument('--basis',required=True)
    sync_profile_parser=sub.add_parser('sync-confirmed-profile')
    sync_profile_parser.add_argument('--patch',required=True)
    recover_observation_parser=sub.add_parser('recover-observation')
    recover_observation_parser.add_argument('--basis',required=True)
    rebind_cards_parser=sub.add_parser('rebind-collapsed-education')
    rebind_cards_parser.add_argument('--inventory',required=True)
    rebind_cards_parser.add_argument('--scope-id',required=True)
    rebind_cards_parser.add_argument('--basis',required=True)
    rebind_held_parser=sub.add_parser('rebind-held-records')
    rebind_held_parser.add_argument('--run-dir',required=True)
    rebind_held_parser.add_argument('--inventory',required=True)
    rebind_held_parser.add_argument('--basis',required=True)
    rebind_held_parser.add_argument('--renew-seconds',type=int,required=True)
    renew_add_parser=sub.add_parser('renew-undispatched-add')
    renew_add_parser.add_argument('--inventory',required=True)
    renew_add_parser.add_argument('--basis',required=True)
    renew_add_parser.add_argument('--renew-seconds',type=int,default=600)
    reprocess_save = sub.add_parser('reprocess-save-reconciliation')
    recover_save_preflight_parser=sub.add_parser('recover-save-preflight')
    recover_save_preflight_parser.add_argument('--basis',required=True)
    renew_observation_parser=sub.add_parser('renew-observation')
    renew_observation_parser.add_argument('--basis',required=True)
    renew_observation_parser.add_argument('--renew-seconds',type=int,required=True)
    inspect_controls = sub.add_parser('inspect-controls')
    resolve_controls_parser = sub.add_parser('resolve-controls')
    continue_control = sub.add_parser('continue-after-control')
    defer_independent_parser = sub.add_parser('defer-independent')
    revisit_parser=sub.add_parser('revisit-held')
    revisit_parser.add_argument('--module-id',required=True)
    revisit_parser.add_argument('--basis',required=True)
    revisit_parser.add_argument('--renew-seconds',type=int,required=True)
    revisit_parser.add_argument('--reread',action='store_true')
    revisit_parser.add_argument('--refresh-scope',action='store_true')
    extend_inventory_parser=sub.add_parser('extend-inventory')
    extend_inventory_parser.add_argument('--inventory',required=True)
    extend_inventory_parser.add_argument('--basis',required=True)
    extend_inventory_parser.add_argument('--renew-seconds',type=int,required=True)
    extend_inventory_parser.add_argument('--run-dir',required=True)
    inspect_controls.add_argument('--browser-id')
    apply_control=sub.add_parser('apply-control')
    apply_control.add_argument('--label',required=True)
    apply_control.add_argument('--source')
    apply_control.add_argument('--field-id')
    apply_control.add_argument('--profile')
    apply_control.add_argument('--adapter',choices=sorted(adapter_names()))
    reconcile_control=sub.add_parser('reconcile-control')
    open_module=sub.add_parser('open-module')
    repair_preparation=sub.add_parser('repair-preparation')
    recover.add_argument('--migration-basis')
    recover.add_argument('--renew-seconds',type=int,default=0)
    repair = sub.add_parser('repair')
    repair.add_argument('--basis', required=True)
    repair.add_argument('--renew-seconds', type=int, default=0)
    repair.add_argument('--budget-basis')
    repair.add_argument('--correct-field', nargs=3, action='append', metavar=('LABEL', 'SOURCE', 'TRANSFORM'))
    cancel_module=sub.add_parser('cancel-module')
    cancel_module.add_argument('--basis',required=True)
    reconcile = sub.add_parser('reconcile')
    reconcile.add_argument('--journal',required=True)
    reconcile.add_argument('--user-saved',action='store_true')
    reconcile.add_argument('--basis',required=True)
    for c in (start, fresh_start, resume, status, reconcile, recover, reprocess_recovery, reprocess_save, recover_save_preflight_parser, renew_observation_parser, repair, cancel_module, inspect_controls, apply_control, reconcile_control, open_module, repair_preparation, resolve_controls_parser, continue_control, defer_independent_parser, local_audit, reconcile_add, revisit_parser, normalize_files_parser, sync_profile_parser, recover_observation_parser, rebind_cards_parser, renew_add_parser):
        c.add_argument('--run-dir', required=True)
    args = p.parse_args(argv)
    if args.action == 'check-control-registry':
        result = {'status': 'registry_matched', 'registry': assert_registry(json.loads(args.executor_report))}
    else:
        result = operate(args)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
