import copy
import json
import tempfile
import time
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from edge_form_graph.storage import SqliteSaver
from langgraph.types import Command

from edge_form_graph.application import active_command, application_state, build_application
from edge_form_graph.application_cli import operate, repair_updates
from edge_form_graph.contracts import ContractError, validate_receipt, redacted_profile, json_value, page_matches
from edge_form_graph.enum_repair import context_decisions, review_requests, source_context, validate_review
from edge_form_graph.graph import build_graph, initial_state
from edge_form_graph.identity import synthesize, SECRET_REF, SOURCE, standing_authorized
from edge_form_graph.model import CodexJsonModel
from edge_form_graph.parallel import ParallelWorkers
from edge_form_graph.recovery import correct_proposal, repair_eligible
from tests.test_application import manifest
from tests.test_graph import Model, snapshot


def enum_snapshot(mid='education'):
    snap = snapshot()
    snap.update(module_id=mid, module_selector='#'+mid)
    snap['fields'].append({'id': '#degree', 'kind': 'combobox', 'selector': '#degree',
                           'label': '学历', 'value': '', 'required': True})
    return snap


class EnumModel(Model):
    def __init__(self):
        self.enum_calls = 0
        self.plans = []

    def map(self, profile, before):
        plan = super().map(profile, before)
        plan['mappings'].append({'field_id': '#degree', 'source': '/degree',
                                 'transform': 'identity', 'depends_on': []})
        return plan

    def review_enums(self, requests):
        self.enum_calls += 1
        return {'decisions': [{'field_id': r['field_id'], 'source_value': r['source_value'],
                               'option': '大学本科', 'reason': 'same education level'} for r in requests]}

    def review(self, profile, before, plan, after):
        self.plans.append(plan)
        return super().review(profile, before, plan, after)


def enum_receipt(command, *, candidates=True, success=False):
    snap = enum_snapshot(command['module_id'])
    snap['snapshot_id'] = command['command_id']+'-after'
    results = []
    for op in command.get('operations', []):
        field = next(f for f in snap['fields'] if f['id'] == op['id'])
        status = 'written' if op['id'] == '#school' or success else 'deferred'
        if status == 'written':
            field['value'] = op['value']
        results.append({'id': op['id'], 'status': status, 'reason': '' if status == 'written' else 'enum_not_exact'})
    snap['fields'][0]['value'] = '示例大学' if command['kind'] == 'fill' else ''
    evidence = {}
    if candidates and not success and command['kind'] == 'fill':
        op = next(o for o in command['operations'] if o['id'] == '#degree')
        evidence['enum_candidates'] = [{'field_id': '#degree', 'source_value': op['value'],
                                        'options': ['大学本科', '硕士研究生']}]
    return {'command_id': command['command_id'], 'kind': command['kind'], 'target': command['target'],
            'settled': True, 'status': 'completed', 'snapshot': snap, 'results': results, 'evidence': evidence}


class EnumRepairTests(unittest.TestCase):
    def test_education_level_variants_require_exact_record_context_and_unique_observed_option(self):
        request={'field_id':'level','field_label':'学历','source_value':'硕士研究生',
                 'source_context':{'education_level':'硕士研究生'},'options':['本科','硕士','博士']}
        self.assertEqual(context_decisions([request]),[{'field_id':'level','source_value':'硕士研究生',
            'option':'硕士','reason':'explicit_same_record_education_level'}])
        for changed in [{'field_label':'学位'},{'options':['硕士','硕士研究生']},
                        {'options':['研究生']},{'options':['工程硕士专业学位']},
                        {'source_context':{}},{'source_context':{'education_level':'博士'}}]:
            self.assertEqual(context_decisions([{**request,**changed}]),[])

    def test_enum_request_includes_only_bounded_same_record_qualifiers(self):
        profile={'education':[{'degree':'硕士','degree_type':'工学','major':'计算机科学与技术',
                               'degree_certificate_number':'private','advisor':'private'}]}
        op={'id':'degree','source':'/education/0/degree','transform':'identity',
            'field':{'label':'学位'}}
        context=source_context(profile,op)
        self.assertEqual(context,{'degree_type':'工学','major':'计算机科学与技术'})
        request=review_requests(profile,[op],[{'field_id':'degree','source_value':'硕士',
                                               'options':['工学硕士学位']}])[0]
        self.assertEqual(request['source_context'],context)
        self.assertEqual(context_decisions([request]),[{'field_id':'degree','source_value':'硕士',
            'option':'工学硕士学位','reason':'explicit_same_record_degree_type'}])

    def test_degree_context_requires_one_exact_composed_option(self):
        base={'field_id':'d','source_value':'硕士','field_label':'学位',
              'source_context':{'degree_type':'工学'},'options':['工程硕士专业学位','理学硕士学位']}
        self.assertEqual(context_decisions([base]),[])
        duplicate={**base,'options':['工学硕士学位','工学硕士学位']}
        self.assertEqual(context_decisions([duplicate]),[])

    def test_rank_context_selects_smallest_supported_top_threshold(self):
        request={'field_id':'r','source_value':'10%-20%','field_label':'专业排名',
                 'source_context':{'rank_position':18,'rank_total':100},
                 'options':['前10%','前20%','前30%','其他']}
        self.assertEqual(context_decisions([request]),[{'field_id':'r','source_value':'10%-20%',
            'option':'前20%','reason':'explicit_rank_position_over_total'}])

    def test_rank_band_fallback_uses_exact_observed_upper_threshold(self):
        request={'field_id':'r','source_value':'10%-20%','field_label':'专业排名',
                 'source_context':{'rank_band':'10%-20%'},
                 'options':['前10%','前20%','前30%','其他']}
        self.assertEqual(context_decisions([request]),[{'field_id':'r','source_value':'10%-20%',
            'option':'前20%','reason':'explicit_rank_band_upper_bound'}])

    def test_study_mode_context_selects_unique_education_type(self):
        request={'field_id':'mode','source_value':'全日制','field_label':'受教育类型',
                 'source_context':{'study_mode':'全日制','education_type':'全日制统分统招'},
                 'options':['全日制教育','非全日制教育','在职教育']}
        self.assertEqual(context_decisions([request]),[{'field_id':'mode','source_value':'全日制',
            'option':'全日制教育','reason':'explicit_same_record_study_mode'}])

    def test_unsettled_fill_with_enum_evidence_enters_recovery_instead_of_crashing_validation(self):
        from edge_form_graph.contracts import validate_receipt
        command={'command_id':'c','kind':'fill','target':{'tab_id':'t'},'operations':[
            {'id':'f','field':{'id':'f','kind':'combobox','selector':'#f','signature':{},'protected':False},
             'value':'source','source':'/x','transform':'identity','depends_on':[]}]}
        receipt={'command_id':'c','kind':'fill','target':{'tab_id':'t'},'settled':False,'status':'unknown',
                 'results':[{'id':'f','status':'unknown','reason':'browser_call_failed'}], 'snapshot':None,
                 'evidence':{'enum_candidates':[{'field_id':'f','source_value':'source','options':['A']}]}}
        self.assertEqual(validate_receipt(command,receipt)['status'],'unknown')

    def start(self, saver, model=None):
        model = model or EnumModel()
        graph = build_graph(model, saver)
        cfg = {'configurable': {'thread_id': 'enum'}}
        graph.invoke(initial_state({'school': '示例大学', 'degree': '本科'}, enum_snapshot()), cfg)
        return graph, cfg, model

    def test_alias_backfill_survives_restart_and_review_has_provenance(self):
        with tempfile.TemporaryDirectory() as folder:
            db = str(Path(folder)/'state.sqlite')
            with SqliteSaver.from_conn_string(db) as saver:
                graph, cfg, model = self.start(saver)
                old = graph.get_state(cfg).values['command']
                graph.invoke(Command(resume=enum_receipt(old)), cfg)
                new = graph.get_state(cfg).values['command']
                self.assertEqual([o['id'] for o in new['operations']], ['#degree'])
                op = new['operations'][0]
                self.assertEqual(op['value'], '大学本科')
                self.assertEqual(op['enum_provenance']['source_value'], '本科')
                self.assertEqual(op['enum_provenance']['command_id'], old['command_id'])
                self.assertIsNone(graph.get_state(cfg).values['reviewed_revision'])
            with SqliteSaver.from_conn_string(db) as saver:
                graph = build_graph(model, saver)
                graph.invoke(Command(resume=enum_receipt(new, success=True)), cfg)
                state = graph.get_state(cfg).values
                self.assertEqual(state['status'], 'verified_draft')
                self.assertEqual(model.enum_calls, 1)
                self.assertEqual(model.plans[0]['enum_aliases'][0]['option'], '大学本科')

    def test_invalid_or_declined_alias_stays_blocked(self):
        for option in (None, 'invented', {'literal': '大学本科'}):
            with self.subTest(option=option), SqliteSaver.from_conn_string(':memory:') as saver:
                model = EnumModel()
                model.review_enums = lambda req: {'decisions': [{'field_id': '#degree', 'source_value': '本科',
                                                               'option': option, 'reason': 'uncertain'}]}
                graph, cfg, _ = self.start(saver, model)
                graph.invoke(Command(resume=enum_receipt(graph.get_state(cfg).values['command'])), cfg)
                state = graph.get_state(cfg)
                self.assertFalse(state.next)
                self.assertEqual(state.values['status'], 'required_field_missing')
                self.assertEqual(state.values['operations'][1]['value'], '本科')

    def test_receipt_evidence_cannot_cross_scope_or_unsettled_write(self):
        with SqliteSaver.from_conn_string(':memory:') as saver:
            graph, cfg, _ = self.start(saver)
            command = graph.get_state(cfg).values['command']
            unsettled = enum_receipt(command)
            unsettled.update(settled=False, status='unknown')
            unsettled['results'][1].update(status='unknown')
            self.assertEqual(validate_receipt(command, unsettled)['status'], 'unknown')
            mutations = [lambda r: r['evidence']['enum_candidates'][0].update(field_id='#school'),
                         lambda r: r['evidence']['enum_candidates'][0].update(source_value='博士'),
                         lambda r: r['evidence']['enum_candidates'][0].update(options=['本科', '本科']),
                         lambda r: r['snapshot'].update(module_id='other'),
                         lambda r: r['snapshot']['fields'][1].update(selector='#other')]
            for mutate in mutations:
                r = enum_receipt(command)
                mutate(r)
                with self.assertRaises(ContractError):
                    validate_receipt(command, r)

    def test_repeat_defer_is_bounded(self):
        with SqliteSaver.from_conn_string(':memory:') as saver:
            graph, cfg, model = self.start(saver)
            for _ in range(2):
                command = graph.get_state(cfg).values['command']
                graph.invoke(Command(resume=enum_receipt(command)), cfg)
            self.assertEqual(model.enum_calls, 1)
            self.assertFalse(graph.get_state(cfg).next)

    def test_candidates_survive_batches_and_restart_until_module_review(self):
        with tempfile.TemporaryDirectory() as folder:
            db = str(Path(folder)/'batches.sqlite')
            with SqliteSaver.from_conn_string(db) as saver:
                graph, cfg, model = self.start(saver)
                first = graph.get_state(cfg).values['command']
                receipt = enum_receipt(first)
                receipt['results'][0]['status'] = 'unattempted'
                receipt['snapshot']['fields'][0]['value'] = ''
                graph.invoke(Command(resume=receipt), cfg)
                state = graph.get_state(cfg).values
                ordinary = state['command']
                self.assertEqual([op['id'] for op in ordinary['operations']], ['#school'])
                self.assertEqual(model.enum_calls, 0)
                self.assertEqual(state['enum_pending'][0]['command_id'], first['command_id'])
            with SqliteSaver.from_conn_string(db) as saver:
                graph = build_graph(model, saver)
                graph.invoke(Command(resume=enum_receipt(ordinary, candidates=False)), cfg)
                repaired = graph.get_state(cfg).values['command']
                self.assertEqual([op['id'] for op in repaired['operations']], ['#degree'])
                self.assertEqual(model.enum_calls, 1)
                self.assertEqual(repaired['operations'][0]['enum_provenance']['command_id'], first['command_id'])
                graph.invoke(Command(resume=enum_receipt(repaired, success=True)), cfg)
                self.assertEqual(graph.get_state(cfg).values['status'], 'verified_draft')

    def test_native_select_nonexact_compiles_for_live_deferral(self):
        snap = enum_snapshot()
        snap['fields'][1].update(kind='select', options=[{'label': '大学本科'}])
        with SqliteSaver.from_conn_string(':memory:') as saver:
            g = build_graph(EnumModel(), saver)
            cfg = {'configurable': {'thread_id': 'native'}}
            g.invoke(initial_state({'school': '示例大学', 'degree': '本科'}, snap), cfg)
            self.assertEqual(g.get_state(cfg).values['command']['operations'][1]['value'], '本科')

    def test_review_requires_exact_coverage_and_source(self):
        req = [{'field_id': 'a', 'source_value': '本科', 'options': ['大学本科']}]
        for decisions in ([], [{'field_id': 'b', 'source_value': '本科', 'option': '大学本科', 'reason': 'same'}],
                          [{'field_id': 'a', 'source_value': '硕士', 'option': '大学本科', 'reason': 'same'}]):
            with self.assertRaises(ContractError):
                validate_review({'decisions': decisions}, req)

    def test_cli_repair_original_run_observes_then_keeps_ordered_save_gate(self):
        with tempfile.TemporaryDirectory() as folder:
            db = str(Path(folder)/'application.sqlite')
            cfg = {'configurable': {'thread_id': 'application'}, 'recursion_limit': 100}
            model = EnumModel()
            with SqliteSaver.from_conn_string(db) as saver:
                g = build_application(model, saver)
                g.invoke(application_state({'school': '示例大学', 'degree': '本科'}, manifest(False), allow_save=True), cfg)
                observe = active_command(g.get_state(cfg, subgraphs=True))
                g.invoke(Command(resume=enum_receipt(observe)), cfg)
                fill = active_command(g.get_state(cfg, subgraphs=True))
                g.invoke(Command(resume=enum_receipt(fill, candidates=False)), cfg)
                old = copy.deepcopy(g.get_state(cfg).values['results']['a'])
                self.assertEqual(old['status'], 'required_field_missing')
                # A legacy run can exhaust transient retries before this explicit repair.
                g.update_state(cfg, {'recovery_attempts': {'a': 2}}, as_node='fill_and_review')
            args = SimpleNamespace(action='repair', run_dir=folder, basis='enum backend repaired',
                                   renew_seconds=300, budget_basis='explicit continuation budget')
            with patch('edge_form_graph.application_cli.review_model', return_value=model):
                summary = operate(args)
            self.assertEqual(summary['pending_kind'], 'observe')
            with SqliteSaver.from_conn_string(db) as saver:
                g = build_application(model, saver)
                state = g.get_state(cfg, subgraphs=True)
                self.assertEqual(state.values['index'], 0)
                self.assertEqual(state.values['recovery_attempts']['a'], 2)
                self.assertEqual(state.values['repair_attempts']['a'], 1)
                self.assertEqual(state.values['recovery_history'][0]['previous'], old)
                obs = enum_receipt(active_command(state))
                obs['snapshot']['fields'][0]['value'] = '示例大学'
                g.invoke(Command(resume=obs), cfg)
                command = active_command(g.get_state(cfg, subgraphs=True))
                g.invoke(Command(resume=enum_receipt(command)), cfg)
                command = active_command(g.get_state(cfg, subgraphs=True))
                self.assertEqual(command['kind'], 'fill')
                g.invoke(Command(resume=enum_receipt(command, success=True)), cfg)
                state = g.get_state(cfg, subgraphs=True)
                self.assertEqual(active_command(state)['kind'], 'save_scope')
                self.assertEqual(state.values['index'], 0)
                self.assertEqual(model.plans[-1]['enum_aliases'][0]['source_value'], '本科')

    def test_two_explicit_cli_repairs_preserve_exhausted_auto_counter_and_history(self):
        with tempfile.TemporaryDirectory() as folder:
            db = str(Path(folder)/'application.sqlite')
            cfg = {'configurable': {'thread_id': 'application'}, 'recursion_limit': 100}
            model = EnumModel()
            legacy_history = [{'kind': 'automatic_recovery', 'attempt': n} for n in (1, 2)]
            with SqliteSaver.from_conn_string(db) as saver:
                graph = build_application(model, saver)
                graph.invoke(application_state({'school': '示例大学', 'degree': '本科'}, manifest(False)), cfg)
                graph.invoke(Command(resume=enum_receipt(active_command(graph.get_state(cfg, subgraphs=True)))), cfg)
                fill = active_command(graph.get_state(cfg, subgraphs=True))
                blocked = enum_receipt(fill, candidates=False)
                blocked['results'][1]['reason'] = 'preexisting_popup'
                graph.invoke(Command(resume=blocked), cfg)
                graph.update_state(cfg, {'recovery_attempts': {'a': 2}, 'recovery_history': legacy_history},
                                   as_node='fill_and_review')
                self.assertFalse(graph.get_state(cfg).next)

            args = SimpleNamespace(action='repair', run_dir=folder, basis='explicit executor code repair',
                                   renew_seconds=300, budget_basis='explicit user continuation')
            preserved = copy.deepcopy(legacy_history)
            for attempt in (1, 2):
                # A fresh CLI/graph instance for each explicitly requested continuation.
                with patch('edge_form_graph.application_cli.review_model', return_value=model):
                    summary = operate(args)
                self.assertEqual(summary['pending_kind'], 'observe')
                self.assertEqual(summary['recovery_attempts'], {'a': 2})
                self.assertEqual(summary['repair_attempts'], {'a': attempt})
                with SqliteSaver.from_conn_string(db) as saver:
                    graph = build_application(model, saver)
                    state = graph.get_state(cfg, subgraphs=True)
                    self.assertEqual(state.values['recovery_history'][:-1], preserved)
                    self.assertEqual(len(state.values['budget_history']), attempt)
                    self.assertEqual(state.values['index'], 0)
                    self.assertEqual(state.values['scopes'], {})
                    observed = enum_receipt(active_command(state))
                    observed['snapshot']['fields'][0]['value'] = '示例大学'
                    graph.invoke(Command(resume=observed), cfg)
                    fill = active_command(graph.get_state(cfg, subgraphs=True))
                    self.assertEqual((fill['kind'], fill['module_id']), ('fill', 'a'))
                    receipt = enum_receipt(fill, candidates=False)
                    receipt['results'][1]['reason'] = 'preexisting_popup'
                    graph.invoke(Command(resume=receipt), cfg)
                    state = graph.get_state(cfg, subgraphs=True)
                    self.assertFalse(state.next)
                    self.assertEqual(state.values['status'], 'module_blocked')
                    self.assertEqual(state.values['recovery_attempts'], {'a': 2})
                    self.assertEqual(state.values['repair_attempts'], {'a': attempt})
                    self.assertEqual(state.values['results']['a']['last_receipt'], receipt)
                    preserved = copy.deepcopy(state.values['recovery_history'])
            with patch('edge_form_graph.application_cli.review_model', return_value=model):
                summary=operate(args)
                self.assertEqual(summary['pending_kind'],'observe')
            with SqliteSaver.from_conn_string(db) as saver:
                state = build_application(model, saver).get_state(cfg)
                self.assertEqual(state.values['recovery_history'][:-1], preserved)
                self.assertEqual(state.values['repair_attempts'], {'a': 3})
                self.assertEqual(state.values['recovery_attempts'], {'a': 2})
                self.assertEqual(len(state.values['budget_history']), 3)

    def test_repair_rejects_pending_unknown_and_implicit_budget(self):
        with SqliteSaver.from_conn_string(':memory:') as saver:
            g, cfg, _ = self.start(saver)
            g.invoke(Command(resume=enum_receipt(g.get_state(cfg).values['command'], candidates=False)), cfg)
            result = g.get_state(cfg).values
            values = {'status': 'module_blocked', 'command': None, 'manifest': manifest(), 'index': 0,
                      'results': {'a': result}, 'profile': {}, 'deadline': time.time()-1}
            state = SimpleNamespace(values=values, next=())
            for kwargs in ({}, {'renew_seconds': 300}, {'renew_seconds': 1801, 'budget_basis': 'explicit'}):
                with self.assertRaises(ContractError):
                    repair_updates(state, basis='repair', **kwargs)
            updates = repair_updates(state, basis='repair', renew_seconds=300, budget_basis='explicit')
            self.assertGreater(updates['deadline'], time.time())
            values['repair_attempts'] = {'a': 2}
            repair_updates(state, basis='new authorized correction', renew_seconds=300, budget_basis='explicit')
            self.assertEqual(values['repair_attempts'], {'a': 2})
            values['repair_attempts'] = {}
            result['last_receipt']['settled'] = False
            with self.assertRaises(ContractError):
                repair_updates(state, basis='repair', renew_seconds=300, budget_basis='explicit')

    def test_explicit_repair_can_reread_after_transient_unreadable_control(self):
        values = {'status': 'recovery_blocked', 'recovery_reason': 'recovery_value_unreadable',
                  'command': None, 'manifest': manifest(), 'index': 0, 'profile': {},
                  'deadline': time.time()+300, 'mapping_cache': {},
                  'results': {'a': {'command': {'command_id': 'old-fill'}, 'results': {}}},
                  'recovery_history': [{'module_id': 'a', 'repair_authorization': {
                      'basis': 'prior repair', 'module_id': 'a'}}]}
        updates = repair_updates(SimpleNamespace(values=values, next=()), basis='fresh closed-control read')
        self.assertTrue(updates['recovery_requested'])
        self.assertEqual(updates['repair_authorization']['module_id'], 'a')

    def test_explicit_repair_can_reread_after_settled_locator_conflict_exhausts_retries(self):
        receipt = {'kind': 'fill', 'settled': True, 'status': 'partial', 'results': [
            {'id': '#stable', 'status': 'already_matched', 'reason': ''},
            {'id': '#changed', 'status': 'conflict', 'reason': 'field_missing_or_ambiguous'}]}
        values = {'status': 'recovery_blocked', 'recovery_reason': 'recovery_limit',
                  'command': None, 'manifest': manifest(), 'index': 0, 'profile': {},
                  'deadline': time.time()+300, 'mapping_cache': {},
                  'results': {'a': {'command': {'command_id': 'old-fill'},
                                    'last_receipt': receipt, 'results': {}}},
                  'recovery_history': [{'module_id': 'a'}]}
        updates = repair_updates(SimpleNamespace(values=values, next=()), basis='stable selector fix')
        self.assertTrue(updates['recovery_requested'])
        receipt['results'][1] = {'id': '#changed', 'status': 'unknown', 'reason': 'transport'}
        with self.assertRaises(ContractError):
            repair_updates(SimpleNamespace(values=values, next=()), basis='unsafe retry')

    def test_page_matches_rebound_logical_id_by_unique_reviewed_identity(self):
        field = {'id': '#fresh', 'selector': '#stable', 'label': '政治面貌', 'kind': 'combobox',
                 'protected': False, 'value': '中共党员', 'value_readable': True,
                 'signature': {'tag': 'INPUT', 'label': '政治面貌'}}
        op = {'id': '#old-logical', 'field': {**field, 'id': '#old-logical'}, 'value': '中共党员'}
        self.assertTrue(page_matches([op], {'fields': [field]}))
        self.assertFalse(page_matches([op], {'fields': [field, {**field, 'id': '#duplicate'}]}))

    def test_readback_mismatch_with_matching_settled_command_is_repairable(self):
        receipt = {'kind': 'fill', 'command_id': 'fill-1', 'settled': True,
                   'status': 'completed', 'results': [{'id': '#a', 'status': 'already_matched'}]}
        result = {'status': 'readback_mismatch', 'command': {'kind': 'fill', 'command_id': 'fill-1'},
                  'last_receipt': receipt, 'results': {}}
        self.assertTrue(repair_eligible(result))
        result['command']['command_id'] = 'different'
        self.assertFalse(repair_eligible(result))

    def test_source_correction_rebinds_one_stale_logical_field_id(self):
        snap = snapshot()
        snap['fields'][0]['id'] = '#fresh-structural'
        snap['fields'][0]['selector'] = '#fresh-structural'
        proposal = {'mappings': [{'field_id': '#old-placeholder', 'source': '/school',
                                  'transform': 'identity', 'depends_on': []}], 'deferred': []}
        updated = correct_proposal({'school': '示例大学'}, snap, proposal, [{
            'field_id': '#fresh-structural', 'source': '/school', 'transform': 'identity'}])
        self.assertEqual([m['field_id'] for m in updated['mappings']], ['#fresh-structural'])


class ConfidentialTests(unittest.TestCase):
    def test_repair_preserves_direct_values_cache_and_old_state(self):
        sensitive = {'identity': {'identity_document_number': 'synthetic-sensitive-marker'}, 'school': 'safe'}
        result = {'status': 'required_field_missing', 'command': None, 'profile': sensitive,
                  'mapping_cache': {'profile': sensitive}, 'results': {},
                  'last_receipt': {'kind': 'fill', 'settled': True, 'status': 'completed', 'results': []}}
        values = {'status': 'module_blocked', 'command': None, 'manifest': manifest(), 'index': 0,
                  'results': {'a': result}, 'profile': sensitive, 'deadline': time.time()+300,
                  'mapping_cache': {'a': {'profile': sensitive, 'proposal': {'mappings': [], 'deferred': []}}}}
        updates = repair_updates(SimpleNamespace(values=values, next=()), basis='repair')
        self.assertIn('synthetic-sensitive-marker', json.dumps(updates))
        self.assertIn('a', updates['mapping_cache'])
        self.assertIn('synthetic-sensitive-marker', json.dumps(values))

    def test_personal_source_variants_use_direct_values(self):
        for key in ('identity_document_number', 'identity document number', 'identity-document-number',
                    'identity.number', 'identity_number'):
            profile = {'identity': {key: 'synthetic-sensitive-marker'}, 'school': 'safe'}
            self.assertIn('synthetic-sensitive-marker', json.dumps(redacted_profile(profile)))
            self.assertEqual(json_value(profile, '/identity/'+key), 'synthetic-sensitive-marker')

    def test_model_boundaries_follow_user_direct_value_policy(self):
        model = CodexJsonModel()
        captured = []
        def capture(instructions, data, schema):
            captured.append(data)
            if 'checked_field_ids' in schema.get('properties', {}):
                return {'approved': True, 'checked_field_ids': [], 'issues': []}
        model.ask = capture
        profile = {'identity': {'identity_document_number': 'synthetic-sensitive-marker'}}
        model.map(profile, {})
        model.review(profile, {}, {}, {})
        self.assertIn('synthetic-sensitive-marker', json.dumps(captured))

    @patch('edge_form_graph.identity.standing_authorized', return_value=True)
    def test_opaque_synthesis_and_boolean_only_match(self, _auth):
        snap = snapshot()
        field = {'id': '#id', 'selector': '#id', 'label': ' * 证件号码： ', 'kind': 'text',
                 'protected': True, 'required': True, 'value': ''}
        snap['fields'] = [field]
        ops, deferred = synthesize(snap, [], [{'field_id': '#id', 'reason': 'protected'}])
        self.assertEqual(deferred, [])
        self.assertEqual(ops[0]['value'], None)
        self.assertEqual(ops[0]['secret_ref'], SECRET_REF)
        self.assertEqual(ops[0]['source'], SOURCE)
        self.assertFalse(page_matches(ops, snap))
        field['secret_match'] = True
        self.assertTrue(page_matches(ops, snap))
        field['secret_match'] = 1
        self.assertFalse(page_matches(ops, snap))
        snap['fields'].append({**field, 'id': '#second'})
        self.assertEqual(synthesize(snap, [], [{'field_id': '#id', 'reason': 'protected'}])[0], [])

    @patch('edge_form_graph.identity.standing_authorized', return_value=True)
    def test_identity_is_last_unless_dependency_requires_earlier(self, _auth):
        snap = snapshot()
        snap['fields'].append({'id': '#id', 'selector': '#id', 'label': '证件号码',
                               'kind': 'text', 'protected': True, 'value': ''})
        ordinary = [{'id': '#school', 'depends_on': []}, {'id': '#later', 'depends_on': []}]
        deferred = [{'field_id': '#id', 'reason': 'protected'}]
        ops, _ = synthesize(snap, ordinary, deferred)
        self.assertEqual([op['id'] for op in ops], ['#school', '#later', '#id'])
        ordinary[1]['depends_on'] = ['#id']
        ops, _ = synthesize(snap, ordinary, deferred)
        self.assertEqual([op['id'] for op in ops], ['#school', '#id', '#later'])

    def test_standing_auth_requires_fixed_profile_path(self):
        with tempfile.TemporaryDirectory() as folder:
            config = Path(folder)/'local.json'
            for data, expected in (({'identity_document_fill': True, 'profile': str(config.resolve())}, True),
                                   ({'identity_document_fill': True, 'profile': 'relative.json'}, False),
                                   ({'identity_document_fill': 'true', 'profile': str(config.resolve())}, False)):
                config.write_text(json.dumps(data), encoding='utf-8')
                with patch('edge_form_graph.identity.LOCAL_CONFIG', config):
                    self.assertEqual(standing_authorized(), expected)

    @patch('edge_form_graph.identity.standing_authorized', return_value=True)
    def test_legacy_opaque_execution_still_requires_secret_match(self, _auth):
        class Protected(Model):
            def map(self, profile, before):
                self.seen = profile
                return {'mappings': [], 'deferred': [{'field_id': '#id', 'reason': 'protected'}]}
        for matched in (False, True):
            with SqliteSaver.from_conn_string(':memory:') as saver:
                snap = snapshot()
                snap['fields'] = [{'id': '#id', 'selector': '#id', 'label': '身份证号码', 'kind': 'text',
                                   'protected': True, 'required': True, 'value': '', 'secret_match': False}]
                model = Protected()
                graph = build_graph(model, saver)
                cfg = {'configurable': {'thread_id': 'id'}}
                state = initial_state({}, snap)
                state['profile'] = {'identity': {'identity_document_number': 'synthetic-sensitive-marker'}}
                graph.invoke(state, cfg)
                command = graph.get_state(cfg).values['command']
                self.assertNotIn('synthetic-sensitive-marker', json.dumps(command))
                self.assertIn('synthetic-sensitive-marker', json.dumps(model.seen))
                self.assertEqual(command['min_fill_count'], 1)
                snap['fields'][0]['secret_match'] = matched
                graph.invoke(Command(resume={'command_id': command['command_id'], 'kind': 'fill',
                    'target': command['target'], 'status': 'completed', 'settled': True,
                    'results': [{'id': '#id', 'status': 'written', 'reason': ''}],
                    'snapshot': snap, 'evidence': {}}), cfg)
                self.assertEqual(graph.get_state(cfg).values['status'],
                                 'verified_draft' if matched else 'readback_mismatch')


if __name__ == '__main__':
    unittest.main()
