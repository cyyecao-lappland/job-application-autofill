import copy
import threading
import tempfile
import time
import unittest
from unittest.mock import patch
from collections import Counter
from pathlib import Path

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from edge_form_graph.application import application_state, build_application, active_command
from edge_form_graph.contracts import ContractError, compile_plan, json_value
from edge_form_graph.parallel import (ParallelWorkers, cached_mapping, focused_profile,
                                      scope_reviews_current, fill_results_complete, prefilled_bindings,
                                      deterministic_record_mappings, deterministic_scope_reviews)
from tests.test_application import manifest, answer
from tests.test_graph import Model, snapshot


def inventory(count=2):
    result = manifest()
    result['modules'] = []
    for index in range(count):
        mid = chr(ord('a') + index)
        snap = snapshot()
        snap.update(module_id=mid, module_selector='#' + mid)
        result['modules'].append({'id': mid, 'selector': '#' + mid,
                                  'page_order': index, 'save_scope': 'g', 'snapshot': snap})
    return result


class ConcurrentModel(Model):
    def __init__(self, reject=None):
        self.lock = threading.Lock()
        self.active = self.peak = 0
        self.events = []
        self.maps = Counter()
        self.reject = reject
        self.review_inputs = []
        self.barrier = threading.Barrier(2)

    def enter(self, kind, mid):
        with self.lock:
            self.active += 1
            self.peak = max(self.active, self.peak)
            self.events.append((kind, mid))
        time.sleep(0.025)

    def leave(self):
        with self.lock:
            self.active -= 1

    def map(self, profile, before):
        self.enter('map', before['module_id'])
        try:
            with self.lock:
                self.maps[before['module_id']] += 1
            return super().map(profile, before)
        finally:
            self.leave()

    def review(self, profile, before, plan, after):
        self.enter('review', before['module_id'])
        try:
            with self.lock:
                self.review_inputs.append(copy.deepcopy((profile, before)))
            # Both reviewers must be in flight, not merely invoked serially.
            self.barrier.wait(timeout=3)
            verdict = super().review(profile, before, plan, after)
            if before['module_id'] == self.reject:
                verdict.update(approved=False, issues=['source omission'])
            return verdict
        finally:
            self.leave()


class ParallelTests(unittest.TestCase):
    def test_nonempty_readonly_fields_do_not_block_a_fresh_run(self):
        result={'current':{'fields':[{'id':'readonly','disabled':True,'value_readable':True,'value':'2027-07-01'}]},
                'results':{'readonly':{'status':'deferred','reason':'field_not_writable'}}}
        self.assertTrue(fill_results_complete(result))

    def test_confirmed_optional_omission_does_not_block_module(self):
        for reason in ('missing: no verified fact', 'unsupported: no safe source',
                       'source_is_not_an_answer: empty canonical list'):
            result={'current':{'fields':[{'id':'optional','required':False,'value':''}]},
                    'results':{'optional':{'status':'deferred','reason':reason}}}
            self.assertTrue(fill_results_complete(result), reason)
        for required, reason in ((True,'missing: no verified fact'),
                                 (False,'control_method_unverified'),
                                 (False,'semantic_match_pending')):
            result={'current':{'fields':[{'id':'field','required':required,'value':''}]},
                    'results':{'field':{'status':'deferred','reason':reason}}}
            self.assertFalse(fill_results_complete(result), (required,reason))

    def test_explicit_persisted_blank_exception_is_field_scoped(self):
        result={'current':{'fields':[{'id':'degree','required':True,'value':''},
                                     {'id':'other','required':True,'value':''}]},
                'results':{'degree':{'status':'deferred','reason':'source_is_not_an_answer'},
                           'other':{'status':'deferred','reason':'missing'}}}
        self.assertFalse(fill_results_complete(result))
        self.assertFalse(fill_results_complete(result, {'degree'}))
        result['results']['other']={'status':'already_matched','reason':''}
        self.assertTrue(fill_results_complete(result, {'degree'}))

    def test_prefilled_binding_uses_only_unique_direct_value_in_bound_record(self):
        profile={'education':[{'record_id':'edu-1','school':'A','major':'B',
                               'legacy':{'school':'A'},'duplicate':'B'}]}
        result={'current':{
                    'mapping_context':{'record_collection':'/education','record_id':'edu-1'},
                    'fields':[{'id':'school','label':'学校名称','value':'A'},
                              {'id':'major','label':'Unrelated','value':'B'}]},
                'results':{'school':{'status':'prefilled_pending_review'},
                           'major':{'status':'prefilled_pending_review'}}}
        self.assertEqual(prefilled_bindings(profile,result),[{
            'field_id':'school','source':'/education/0/school',
            'basis':'unique_direct_scalar_equality_in_bound_record'}])

    def test_prefilled_binding_can_propose_canonical_label_alias_for_enum_review(self):
        profile={'education':[{'record_id':'edu-1','education_level':'本科',
                               'degree':'工学学士','rank_band':'10%-20%'}]}
        result={'current':{
                    'mapping_context':{'record_collection':'/education','record_id':'edu-1'},
                    'fields':[{'id':'level','label':'学历','value':'大学本科'},
                              {'id':'degree','label':'学位','value':'学士'},
                              {'id':'rank','label':'年级排名','value':'前20%'}]},
                'results':{'level':{'status':'prefilled_pending_review'},
                           'degree':{'status':'prefilled_pending_review'},
                           'rank':{'status':'prefilled_pending_review'}}}
        self.assertEqual(prefilled_bindings(profile,result),[
            {'field_id':'level','source':'/education/0/education_level',
             'basis':'canonical_label_alias_requires_value_equivalence_review'},
            {'field_id':'degree','source':'/education/0/degree',
             'basis':'degree_category_suffix_matches_full_degree'},
            {'field_id':'rank','source':'/education/0/rank_band',
             'basis':'rank_band_upper_bound_matches_top_threshold'}])

    def test_prefilled_binding_supports_bounded_referee_derivations(self):
        profile={'employment':[{'record_id':'job-1','referee_relationship':'实习带教，正式员工',
                                'proof':{'employer_legal_name':'示例科技有限公司'}}]}
        result={'current':{
                    'mapping_context':{'record_collection':'/employment','record_id':'job-1'},
                    'fields':[{'id':'position','label':'证明人职务','value':'正式员工'},
                              {'id':'employer','label':'证明人单位','value':'示例科技有限公司'}]},
                'results':{'position':{'status':'prefilled_pending_review'},
                           'employer':{'status':'prefilled_pending_review'}}}
        self.assertEqual(prefilled_bindings(profile,result),[
            {'field_id':'position','source':'/employment/0/referee_relationship',
             'basis':'explicit_referee_relationship_last_clause'},
            {'field_id':'employer','source':'/employment/0/proof/employer_legal_name',
             'basis':'exact_nested_proof_employer'}])

    def test_bound_record_rules_generate_values_without_using_page_text(self):
        profile={'employment':[{'record_id':'job-1','end_date':'2026-09-16',
                                'referee_relationship':'实习带教，正式员工',
                                'proof':{'employer_legal_name':'示例科技有限公司'}}]}
        s=snapshot();s['mapping_context']={'record_collection':'/employment','record_id':'job-1'}
        base=s['fields'][0]
        s['fields']=[{**base,'id':'end','selector':'#end','label':'离职时间','value':'2024-01-01'},
                     {**base,'id':'position','selector':'#position','label':'证明人职务','value':'经理'},
                     {**base,'id':'employer','selector':'#employer','label':'证明人单位','value':'旧单位'}]
        self.assertEqual(deterministic_record_mappings(profile,s,{'end','position','employer'}),[
            {'field_id':'end','source':'/employment/0/end_date','transform':'identity','depends_on':[]},
            {'field_id':'position','source':'/employment/0/referee_relationship','transform':'last_clause','depends_on':[]},
            {'field_id':'employer','source':'/employment/0/proof/employer_legal_name','transform':'identity','depends_on':[]}])

    def test_bound_education_proof_prefers_only_its_authorized_transcript(self):
        with tempfile.TemporaryDirectory() as folder:
            other=Path(folder)/'other.pdf';own=Path(folder)/'own.pdf';legacy=Path(folder)/'legacy.pdf'
            for path in (other,own,legacy):path.write_text('synthetic fixture')
            profile={'education':[
                {'record_id':'edu-other','transcript':{'path':str(other)}},
                {'record_id':'edu-bound','transcript':{'path':str(legacy)},
                 'transcript_usage_policy':{'use_for_applications':True,'path':str(own)}}]}
            s=snapshot();s['mapping_context']={'record_collection':'/education','record_id':'edu-bound'}
            field={**s['fields'][0],'id':'#proof','selector':'#proof','kind':'file','label':'成绩证明'}
            s['fields']=[field]
            mappings=deterministic_record_mappings(profile,s,{'#proof'})
            self.assertEqual(mappings,[{'field_id':'#proof','source':'record_id:edu-bound/transcript_usage_policy/path',
                                        'transform':'identity','depends_on':[]}])
            self.assertEqual(compile_plan(profile,s,{'mappings':mappings,'deferred':[]})[0][0]['value'],str(own))
            profile['education'][1]['transcript_usage_policy']['path']=''
            mappings=deterministic_record_mappings(profile,s,{'#proof'})
            self.assertEqual(mappings[0]['source'],'record_id:edu-bound/transcript/path')
            profile['education'][1]['transcript_usage_policy']['use_for_applications']=False
            self.assertEqual(deterministic_record_mappings(profile,s,{'#proof'}),[])

    def test_focused_education_profile_keeps_transcript_sources_for_bound_record(self):
        profile={'education':[{'record_id':'edu-1','transcript':{'path':'/synthetic/master.pdf'},
                               'transcript_usage_policy':{'use_for_applications':True,
                                                          'path':'/synthetic/authorized.pdf'}}],
                 'employment':[{'record_id':'job-1','company':'unrelated'}]}
        s=snapshot();s['module_label']='教育背景';s['mapping_context']={'record_collection':'/education','record_id':'edu-1'}
        focused=focused_profile(profile,s)
        self.assertEqual(focused['education'],profile['education'])
        self.assertNotIn('employment',focused)

    def test_professional_skill_certificate_requires_explicit_none_status(self):
        s=snapshot();s['fields'][0].update(id='#cert',selector='#cert',label='专业技能证书')
        profile={'certifications':[],'collection_status':{'certifications':'explicit_none'},
                 'skills':[{'name':'Python'}]}
        mappings=deterministic_record_mappings(profile,s,{'#cert'})
        self.assertEqual(mappings,[{'field_id':'#cert','source':'/collection_status/certifications',
                                    'transform':'explicit_none_text','depends_on':[]}])
        self.assertEqual(compile_plan(profile,s,{'mappings':mappings,'deferred':[]})[0][0]['value'],'无')

        missing_status={'certifications':[],'skills':[{'name':'Python'}]}
        self.assertEqual(deterministic_record_mappings(missing_status,s,{'#cert'}),[])
        missing_collection={'collection_status':{'certifications':'explicit_none'},
                            'skills':[{'name':'Python'}]}
        self.assertEqual(deterministic_record_mappings(missing_collection,s,{'#cert'}),[])

        ordinary=snapshot();ordinary['fields'][0].update(id='#skill',selector='#skill',label='专业技能')
        self.assertEqual(deterministic_record_mappings(profile,ordinary,{'#skill'}),[])

    def test_project_fallback_uses_project_sources_for_internship_fields(self):
        profile={'projects':[{'record_id':'vcyuan','organization':'V次元 App',
                              'role':'推荐算法工程开发','responsibilities':'负责推荐算法链路'}]}
        s=snapshot();s['module_id']='vcyuan-internship';s['module_selector']='#vcyuan'
        s['mapping_context']={'module_type':'internship_project_fallback',
                              'record_collection':'/projects','record_id':'vcyuan'}
        base=s['fields'][0]
        s['fields']=[{**base,'id':'company','selector':'#company','label':'公司名称','value':''},
                     {**base,'id':'position','selector':'#position','label':'职位名称','value':''},
                     {**base,'id':'content','selector':'#content','label':'工作内容','value':''}]
        self.assertEqual(deterministic_record_mappings(profile,s,{'company','position','content'}),[
            {'field_id':'company','source':'/projects/0/organization','transform':'identity','depends_on':[]},
            {'field_id':'position','source':'/projects/0/role','transform':'identity','depends_on':[]},
            {'field_id':'content','source':'/projects/0/responsibilities','transform':'identity','depends_on':[]}])
        self.assertIn('projects', focused_profile(profile,s))

    def test_program_review_accepts_only_complete_active_table_coverage(self):
        before=snapshot();current=copy.deepcopy(before);current['fields'][0]['value']='示例大学'
        proposal={'mappings':[{'field_id':'#school','source':'/school','transform':'identity','depends_on':[]}],
                  'deferred':[]}
        operations,_=compile_plan({'school':'示例大学'},before,proposal)
        result={'snapshot':before,'current':current,'proposal':proposal,'operations':operations,
                'results':{'#school':{'status':'written'}},'revision':1,
                'metrics':{'model_calls':0,'field_table_hits':1}}
        module={'id':before['module_id'],'selector':before['module_selector']}
        reviews=deterministic_scope_reviews([module],{module['id']:result},before['target'])
        self.assertEqual(reviews[module['id']]['model'],'program')
        self.assertTrue(reviews[module['id']]['review']['approved'])
        result['metrics']['semantic_fields']=1
        self.assertIsNone(deterministic_scope_reviews([module],{module['id']:result},before['target']))

    def test_camel_case_module_hint_preserves_pointer_paths(self):
        profile={'personal_info':{'name':'示例'},'education':[{'school':'大学'}], 'autofill_policy':{'a':True}}
        snap={'module_id':'personalInfo','fields':[{'label':'姓名'}]}
        result=focused_profile(profile,snap)
        self.assertEqual(json_value(result,'/personal_info/name'),'示例')
        self.assertNotIn('autofill_policy',result)
        self.assertNotIn('education',result)

    def flow(self, model, *, mutate=None, recover=None):
        with SqliteSaver.from_conn_string(':memory:') as saver:
            graph = build_application(model, saver)
            config = {'configurable': {'thread_id': 'parallel'}, 'recursion_limit': 100}
            graph.invoke(application_state({'school': '示例大学', 'unmapped_records': ['must review']},
                                           inventory(), allow_save=True), config)
            actions = []
            for _ in range(12):
                command = active_command(graph.get_state(config, subgraphs=True))
                if command is None:
                    break
                actions.append((command['kind'], command['module_id']))
                model.events.append((command['kind'], command['module_id']))
                receipt = answer(command)
                if command['kind'] == 'reconcile_save':
                    self.assertIn('original_command_id', command)
                    self.assertEqual(len(command['expected_modules']), 2)
                    receipt.update(status='saved' if recover else 'unconfirmed', snapshot=None,
                                   evidence={'save_confirmed': bool(recover)})
                elif command['kind'] == 'save_scope' and recover is not None:
                    receipt.update(status='unconfirmed', settled=True, snapshot=None, evidence={})
                if mutate:
                    mutate(command, receipt)
                graph.invoke(Command(resume=receipt), config)
            return actions, graph.get_state(config).values

    def test_bounded_workers_and_invalid_limits(self):
        for limit in (1, 2, 3):
            model = ConcurrentModel()
            data = inventory(7)
            results = ParallelWorkers(model, limit).premap({'school': '示例大学'}, data['modules'], data['target'])
            self.assertEqual(len(results), 7)
            self.assertEqual(model.peak, limit)
            self.assertTrue(all(r['status'] == 'mapped' for r in results.values()))
        for value in (0, 4, True, 2.5):
            with self.assertRaises(ContractError):
                ParallelWorkers(Model(), value)

    def test_parallel_maps_reviews_and_strictly_sequential_fills(self):
        model = ConcurrentModel()
        actions, state = self.flow(model)
        self.assertEqual(actions, [('observe', 'a'), ('fill', 'a'), ('observe', 'b'),
                                   ('fill', 'b'), ('save_scope', 'g')])
        self.assertEqual(model.peak, 2)
        self.assertEqual(model.maps, {'a': 1, 'b': 1})
        self.assertEqual(state['status'], 'complete')
        self.assertTrue(all(r['metrics']['mapping_cache_hit'] for r in state['results'].values()))
        last_fill = max(i for i, event in enumerate(model.events) if event[0] == 'fill')
        self.assertTrue(all(i > last_fill for i, event in enumerate(model.events) if event[0] == 'review'))
        for profile, before in model.review_inputs:
            self.assertEqual(profile['unmapped_records'], ['must review'])
            self.assertEqual({x['module']['id'] for x in before['save_scope_context']}, {'a', 'b'})

    def test_changed_live_value_remaps_only_affected_module(self):
        def mutate(command, receipt):
            if command['kind'] == 'observe' and command['module_id'] == 'b':
                receipt['snapshot']['fields'][0]['value'] = 'user change'
        model = ConcurrentModel()
        _, state = self.flow(model, mutate=mutate)
        self.assertEqual(state['status'], 'complete')
        self.assertEqual(model.maps, {'a': 1, 'b': 2})
        self.assertFalse(state['results']['b']['metrics']['mapping_cache_hit'])

    def test_rejected_review_blocks_save(self):
        actions, state = self.flow(ConcurrentModel(reject='b'))
        self.assertNotIn(('save_scope', 'g'), actions)
        self.assertEqual(state['status'], 'scope_review_blocked')
        self.assertTrue(all(r['reviewed_revision'] is None for r in state['results'].values()))

    def test_cache_ignores_observation_identity_but_not_field_identity(self):
        data = inventory(1)
        profile = {'school': '示例大学'}
        entry = ParallelWorkers(Model()).premap(profile, data['modules'], data['target'])['a']
        live = copy.deepcopy(data['modules'][0]['snapshot'])
        live.update(snapshot_id='new', observed_at=time.time())
        self.assertIsNotNone(cached_mapping(entry, profile, live))
        live['fields'][0]['selector'] = '#replacement'
        self.assertIsNone(cached_mapping(entry, profile, live))
        live['observed_at'] -= 301
        with self.assertRaises(ContractError):
            cached_mapping(entry, profile, live)

    def test_manifest_snapshot_identity_and_age_are_checked(self):
        for key, value in [('module_id', 'other'), ('module_selector', '#other'),
                           ('observed_at', time.time() - 301), ('target', {**snapshot()['target'], 'tab_id': 'other'})]:
            data = inventory()
            data['modules'][0]['snapshot'][key] = value
            with self.assertRaises(ContractError):
                application_state({}, data)

    def test_projection_preserves_pointers_lists_and_policies(self):
        profile = {'education': [{'school': 'one'}, {'school': 'two'}],
                   'projects': [{'name': 'unrelated'}], 'autofill_policy': {'complete': True},
                   'field_metadata': {'education': 'confirmed', 'projects.x': 'drop'},
                   'change_log': [{'note': 'x'*5000}], 'unknown_context': 'keep'}
        projected = focused_profile(profile, snapshot())
        self.assertNotIn('projects', projected)
        self.assertEqual(json_value(projected, '/education/1/school'), 'two')
        self.assertNotIn('autofill_policy', projected)
        self.assertEqual(projected['field_metadata'], {'education': 'confirmed'})
        self.assertNotIn('change_log', projected)
        self.assertNotIn('unknown_context', projected)
        unknown = {**snapshot(), 'module_id': 'ambiguous'}
        self.assertEqual(focused_profile(profile, unknown), profile)
        mixed = {**snapshot(), 'module_id': 'education projects'}
        mixed_profile=focused_profile(profile,mixed)
        self.assertEqual(mixed_profile['education'],profile['education'])
        self.assertEqual(mixed_profile['projects'],profile['projects'])
        self.assertNotIn('change_log',mixed_profile)

    def test_missing_or_stale_review_never_passes_gate(self):
        _, state = self.flow(ConcurrentModel())
        modules, results = state['manifest']['modules'], state['results']
        reviews = state['scope_reviews']['g']
        self.assertFalse(scope_reviews_current(modules, results, {}, state['manifest']['target']))
        results['a']['revision'] += 1
        self.assertFalse(scope_reviews_current(modules, results, reviews, state['manifest']['target']))
        results['a']['revision'] -= 1
        results['a']['current']['observed_at'] -= 301
        self.assertFalse(scope_reviews_current(modules, results, reviews, state['manifest']['target']))

    def test_readonly_save_recovery_never_repeats_save(self):
        for confirmed in (True, False):
            actions, state = self.flow(ConcurrentModel(), recover=confirmed)
            self.assertEqual(actions.count(('save_scope', 'g')), 1)
            self.assertEqual(actions.count(('reconcile_save', 'g')), 1)
            self.assertEqual(state['status'], 'complete' if confirmed else 'needs_reconciliation')

    @patch('edge_form_graph.identity.standing_authorized', return_value=False)
    def test_protected_presence_allows_review_without_secret_values(self, _auth):
        class ProtectedModel(ConcurrentModel):
            def map(self, profile, before):
                result = super().map(profile, before)
                result['deferred'] = [{'field_id': f['id'], 'reason': 'protected'}
                                      for f in before['fields'] if f.get('protected')]
                return result

        for present in (True, False):
            def mutate(command, receipt):
                if receipt.get('snapshot'):
                    receipt['snapshot']['fields'].append({'id': '#private', 'kind': 'text',
                        'selector': '#private', 'label': '证件号码', 'value': '', 'required': True,
                        'protected': True, 'value_present': present})
            model = ProtectedModel()
            actions, state = self.flow(model, mutate=mutate)
            self.assertEqual(state['status'], 'complete' if present else 'module_blocked')
            self.assertEqual(('save_scope', 'g') in actions, present)
            if present:
                self.assertEqual(len(model.review_inputs), 2)
                self.assertEqual(state['results']['a']['results']['#private']['status'], 'deferred')

    def test_missing_review_response_blocks_save(self):
        class Missing(ConcurrentModel):
            def review(self, *args):
                return None
        actions, state = self.flow(Missing())
        self.assertEqual(state['status'], 'scope_review_blocked')
        self.assertNotIn(('save_scope', 'g'), actions)

    def test_other_deferred_field_blocks_before_review(self):
        class Unsupported(ConcurrentModel):
            def map(self, profile, before):
                result = super().map(profile, before)
                result['deferred'] = [{'field_id': f['id'], 'reason': 'unsupported'}
                                      for f in before['fields'] if f['id'] == '#unknown']
                return result
        def mutate(command, receipt):
            if receipt.get('snapshot'):
                receipt['snapshot']['fields'].append({'id': '#unknown', 'kind': 'text', 'value': 'exists',
                    'value_present': True, 'required': False})
        model = Unsupported()
        actions, state = self.flow(model, mutate=mutate)
        self.assertEqual(state['status'], 'module_blocked')
        self.assertFalse(model.review_inputs)
        self.assertNotIn(('save_scope', 'g'), actions)

    def test_restart_keeps_premaps_and_defers_reviews_until_boundary(self):
        model = ConcurrentModel()
        config = {'configurable': {'thread_id': 'restart'}}
        with tempfile.TemporaryDirectory() as folder:
            database = str(Path(folder) / 'application.sqlite')
            with SqliteSaver.from_conn_string(database) as saver:
                graph = build_application(model, saver)
                graph.invoke(application_state({'school': '示例大学'}, inventory(), allow_save=True), config)
                command = active_command(graph.get_state(config, subgraphs=True))
                graph.invoke(Command(resume=answer(command)), config)
                command = active_command(graph.get_state(config, subgraphs=True))
                self.assertEqual(command['kind'], 'fill')
                self.assertEqual(model.maps, {'a': 1, 'b': 1})
            with SqliteSaver.from_conn_string(database) as saver:
                graph = build_application(model, saver)
                graph.invoke(Command(resume=answer(command)), config)
                self.assertFalse(model.review_inputs)
                state = graph.get_state(config, subgraphs=True)
                self.assertIsNone(state.values['results']['a']['reviewed_revision'])
                command = active_command(state)
                self.assertEqual((command['kind'], command['module_id']), ('observe', 'b'))
                graph.invoke(Command(resume=answer(command)), config)
                command = active_command(graph.get_state(config, subgraphs=True))
                graph.invoke(Command(resume=answer(command)), config)
                self.assertEqual(len(model.review_inputs), 2)
                self.assertEqual(model.maps, {'a': 1, 'b': 1})
                self.assertEqual(active_command(graph.get_state(config, subgraphs=True))['kind'], 'save_scope')


if __name__ == '__main__':
    unittest.main()
