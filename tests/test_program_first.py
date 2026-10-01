import copy
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from edge_form_graph.contracts import ContractError, transform
from edge_form_graph.graph import build_graph, initial_state
from tests.knowledge_fixtures import KnowledgeStore
from edge_form_graph.model import CodexJsonModel
from edge_form_graph.semantic import compile_semantic, validate_record_sources
from tests.test_graph import Model, snapshot


def decision(fid, source, transform='identity'):
    return {'field_id': fid, 'classification': 'mapping' if transform in {'identity', 'string'} else 'inference',
            'source': source, 'transform': transform, 'depends_on': [], 'reason': 'Equivalent field within the same record.'}


class SemanticModel:
    model = 'gpt-6-luna'

    def __init__(self, sources):
        self.sources, self.calls = sources, []

    def match_unknown(self, profile, before):
        self.calls.append(copy.deepcopy(before))
        return {'decisions': [decision(f['id'], *self.sources[f['id']]) for f in before['fields']], 'record_binding': None}

    def review_enums(self, requests):
        raise AssertionError('unexpected enum model')


def fill_receipt(command, before):
    after = copy.deepcopy(before)
    after['snapshot_id'] += '-next'
    by_id = {f['id']: f for f in after['fields']}
    for op in command['operations']:
        by_id[op['id']]['value'] = op['value']
    return {'command_id': command['command_id'], 'kind': 'fill', 'target': command['target'],
            'settled': True, 'status': 'completed', 'snapshot': after,
            'results': [{'id': op['id'], 'status': 'written', 'reason': ''} for op in command['operations']], 'evidence': {}}


def finish_saved(graph, cfg):
    state = graph.get_state(cfg).values
    assert state['status'] == 'awaiting_save', state['status']
    command = state['command']
    saved_at = time.time()
    post = copy.deepcopy(state['current'])
    post['observed_at'] = time.time()
    post['snapshot_id'] += '-saved'
    graph.invoke(Command(resume={
        'command_id': command['command_id'], 'kind': 'save', 'target': command['target'],
        'settled': True, 'status': 'saved', 'snapshot': post, 'results': [],
        'evidence': {'save_confirmed': True, 'save_observed_at': saved_at, 'saved_modules': [post]}}), cfg)
    return graph.get_state(cfg).values


class ProgramFirstTests(unittest.TestCase):
    def test_semantic_failure_keeps_bound_deterministic_employment_operations(self):
        with tempfile.TemporaryDirectory() as d, SqliteSaver.from_conn_string(':memory:') as saver:
            s=snapshot();s['fields'][0].update(label='工作单位',value='')
            s['fields'][0]['signature']['label']='工作单位'
            s['fields'].append({**copy.deepcopy(s['fields'][0]),'id':'#missing',
                'selector':'#missing','label':'行业','required':True})
            s['mapping_context']={'record_collection':'/employment','record_id':'wanted'}
            semantic=SemanticModel({})
            def invalid(*args):
                raise ContractError('invalid_or_protected_source')
            semantic.match_unknown=invalid
            graph=build_graph(Model(),saver,knowledge=KnowledgeStore(Path(d)/'k.json'),semantic_model=semantic)
            cfg={'configurable':{'thread_id':'deterministic-after-error'}}
            graph.invoke(initial_state({'employment':[{'record_id':'excluded','company':'Other'},
                {'record_id':'wanted','company':'Target','industry':None}]},s),cfg)
            state=graph.get_state(cfg).values
            self.assertEqual(state['status'],'awaiting_edge')
            self.assertEqual([op['value'] for op in state['command']['operations']],['Target'])
            self.assertEqual(state['command']['operations'][0]['source'],'/employment/1/company')
            self.assertEqual(state['semantic_report'],[])
            graph.invoke(Command(resume=fill_receipt(state['command'],state['current'])),cfg)
            state=graph.get_state(cfg).values
            self.assertEqual(state['semantic_report'][0]['reason'],'invalid_or_protected_source')
            self.assertEqual(state['proposal']['deferred'][0]['field_id'],'#missing')

    def test_semantic_key_catalog_omits_missing_values_but_keeps_false_and_zero(self):
        from edge_form_graph.model import profile_field_catalog
        keys=profile_field_catalog({'name':None,'blank':'','empty':[], 'accept':False,'score':0,'certificate':'CET-4'})
        self.assertEqual(keys,['/accept','/score','/certificate'])

    def test_failed_optional_semantics_preserves_blank_and_requires_content_review(self):
        with tempfile.TemporaryDirectory() as d, SqliteSaver.from_conn_string(':memory:') as saver:
            s=snapshot();s['fields'][0].update(label='工作单位',value='')
            s['fields'][0]['signature']['label']='工作单位'
            s['fields'].append({**copy.deepcopy(s['fields'][0]),'id':'#optional',
                'selector':'#optional','label':'证书','required':False})
            s['mapping_context']={'record_collection':'/employment','record_id':'wanted'}
            semantic=SemanticModel({})
            def invalid(*args):raise ContractError('unbound_array_source')
            semantic.match_unknown=invalid
            reviewer=Model()
            g=build_graph(reviewer,saver,knowledge=KnowledgeStore(Path(d)/'k.json'),semantic_model=semantic)
            cfg={'configurable':{'thread_id':'optional-error'}}
            g.invoke(initial_state({'employment':[{'record_id':'wanted','company':'Target'}]},s),cfg)
            state=g.get_state(cfg).values
            self.assertEqual([o['value'] for o in state['command']['operations']],['Target'])
            self.assertEqual(state['semantic_report'],[])
            g.invoke(Command(resume=fill_receipt(state['command'],state['current'])),cfg)
            state=g.get_state(cfg).values
            self.assertTrue(state['results']['#optional']['reason'].startswith('unsupported:'))
            self.assertGreaterEqual(reviewer.calls,1)
            self.assertEqual(set(state['review']['checked_field_ids']),{'#school','#optional'})
            self.assertEqual(state['current']['fields'][1]['value'],'')

    def test_semantic_resolution_preserves_verified_typed_control_review_status(self):
        with tempfile.TemporaryDirectory() as d, SqliteSaver.from_conn_string(':memory:') as saver:
            s=snapshot();s['fields'][0].update(value='上海市-上海市',value_present=True,
                verified_control={'source':'/city','actual':'上海市-上海市','adapter':'province_city_dialog_v1','command_id':'prior'})
            s['fields'].append({**copy.deepcopy(s['fields'][0]),'id':'#unknown','selector':'#unknown',
                                'label':'额外内容','value':'','value_present':False,'required':False})
            s['fields'][1].pop('verified_control')
            semantic=SemanticModel({})
            semantic.match_unknown=lambda profile,before:{'decisions':[{
                'field_id':f['id'],'classification':'missing','source':None,'transform':'identity',
                'depends_on':[],'reason':'No supplied fact.'} for f in before['fields']],'record_binding':None}
            g=build_graph(Model(),saver,knowledge=KnowledgeStore(Path(d)/'k.json'),semantic_model=semantic)
            cfg={'configurable':{'thread_id':'typed-review'}}
            g.invoke(initial_state({'city':'上海'},s),cfg)
            v=g.get_state(cfg).values
            self.assertEqual(v['results']['#school']['status'],'prefilled_pending_review')

    def test_deterministic_fields_execute_even_when_model_only_reports_missing(self):
        with tempfile.TemporaryDirectory() as d, SqliteSaver.from_conn_string(':memory:') as saver:
            s=snapshot();s['fields'][0].update(label='所在院系',value='')
            s['fields'][0]['signature']['label']='所在院系'
            s['fields'].append({**copy.deepcopy(s['fields'][0]),'id':'#missing',
                'selector':'#missing','label':'国家奖学金','required':False})
            s['mapping_context']={'record_collection':'/education','record_id':'undergrad'}
            semantic=SemanticModel({})
            semantic.match_unknown=lambda profile,before:{'decisions':[{
                'field_id':f['id'],'classification':'missing','source':None,
                'transform':'identity','depends_on':[],'reason':'No supplied fact.'
            } for f in before['fields']],'record_binding':None}
            graph=build_graph(Model(),saver,knowledge=KnowledgeStore(Path(d)/'k.json'),semantic_model=semantic)
            cfg={'configurable':{'thread_id':'mixed-program-and-missing'}}
            graph.invoke(initial_state({'education':[{'record_id':'undergrad','college':'计算机学院'}]},s),cfg)
            state=graph.get_state(cfg).values
            self.assertEqual(state['status'],'awaiting_edge')
            self.assertEqual([op['value'] for op in state['command']['operations']],['计算机学院'])

    def test_review_prompt_treats_explicit_none_and_optional_no_source_as_non_omissions(self):
        model = CodexJsonModel('gpt-6-luna')
        before = snapshot()
        after = copy.deepcopy(before)
        with patch.object(model, 'ask', return_value={
                'approved': True,
                'checked_field_ids': [f'field_{index + 1}' for index, _ in enumerate(before['fields'])],
                'issues': []}) as ask:
            result = model.review({'languages': [{'answer_status': 'explicit_none'}], 'certifications': []},
                                  before, {'operations': [], 'deferred': []}, after)
        prompt = ask.call_args.args[0]
        payload, schema = ask.call_args.args[1:]
        self.assertEqual(result['checked_field_ids'], [field['id'] for field in before['fields']])
        self.assertEqual([field['id'] for field in payload['before']['fields']],
                         [f'field_{index + 1}' for index, _ in enumerate(before['fields'])])
        self.assertEqual(schema['properties']['checked_field_ids']['items']['enum'],
                         [f'field_{index + 1}' for index, _ in enumerate(before['fields'])])
        self.assertIn('answer_status=explicit_none', prompt)
        self.assertIn('optional web field', prompt)
        self.assertIn('Coverage is driven by the web fields', prompt)
        self.assertIn('does not also have to be duplicated', prompt)
        self.assertIn('source-backed name of the matching skill record', prompt)
        self.assertIn('never reject because save or reload evidence', prompt)
        self.assertIn('plan.prefilled_bindings', prompt)
        self.assertIn('issues is only for concrete defects', prompt)
        self.assertIn('none_string_no transform is deliberately narrow', prompt)
        self.assertIn('age_from_birth_date transform computes', prompt)

    def test_review_payload_binds_preserved_blanks_to_compact_scope_field_ids(self):
        model = CodexJsonModel('gpt-6-luna')
        before = snapshot()
        blank = {**before['fields'][0], 'id': '#degree', 'selector': '#degree',
                 'label': '学位', 'value': '', 'required': True}
        before['fields'].append(blank)
        after = copy.deepcopy(before)
        context_before = copy.deepcopy(before)
        context_after = copy.deepcopy(after)
        before['save_scope_context'] = [{
            'module': {'id': before['module_id'], 'preserved_blank_fields': ['学位'],
                       'preserved_blank_field_ids': ['#degree']},
            'before': context_before, 'after': context_after,
            'plan': {'mappings': [], 'deferred': [
                {'field_id': '#degree', 'reason': 'source_is_not_an_answer: null'}],
                'prefilled_bindings': [{'field_id': before['fields'][0]['id'],
                    'source': '/education/0/school',
                    'basis': 'unique_direct_scalar_equality_in_bound_record'}]},
            'revision': 1,
        }]
        with patch.object(model, 'ask', return_value={
                'approved': True, 'checked_field_ids': ['field_1', 'field_2'], 'issues': []}) as ask:
            result = model.review({'education': [{'degree': None}]}, before,
                                  {'mappings': [], 'deferred': []}, after)
        payload = ask.call_args.args[1]
        scope = payload['before']['save_scope_context'][0]
        self.assertEqual(scope['module']['preserved_blank_field_ids'], ['field_2'])
        self.assertEqual([field['id'] for field in scope['before']['fields']],
                         ['field_1', 'field_2'])
        self.assertEqual(scope['plan']['deferred'][0]['field_id'], 'field_2')
        self.assertEqual(scope['plan']['prefilled_bindings'][0]['field_id'], 'field_1')
        self.assertNotIn('selector', scope['before']['fields'][0])
        self.assertEqual(result['checked_field_ids'], [field['id'] for field in after['fields']])

    def test_semantic_mapping_of_readonly_field_becomes_local_deferred(self):
        s=snapshot();s['fields'][0]['disabled']=True;s['fields'][0]['value']='already present'
        response={'decisions':[{'field_id':s['fields'][0]['id'],'classification':'mapping','source':'/school',
            'transform':'identity','depends_on':[],'reason':'same semantic field'}],'record_binding':None}
        proposal,_,notes=compile_semantic({'school':'A'},s,response)
        self.assertEqual(proposal['mappings'],[])
        self.assertEqual(proposal['deferred'][0]['reason'].split(':')[0],'current_field_not_writable')
        self.assertEqual(notes[0]['classification'],'unsupported')

    def test_semantic_grade_proof_rejects_cross_record_transcript_sources(self):
        with tempfile.TemporaryDirectory() as folder:
            first=Path(folder)/'first.pdf';bound=Path(folder)/'bound.pdf'
            first.write_text('synthetic fixture');bound.write_text('synthetic fixture')
            profile={'education':[
                {'record_id':'edu-other','transcript':{'path':str(first)}},
                {'record_id':'edu-bound','transcript':{'path':str(bound)}}]}
            s=snapshot();s['mapping_context']={'record_collection':'/education','record_id':'edu-bound'}
            s['fields'][0].update(kind='file',label='成绩证明',value='')
            for source in ('record_id:edu-other/transcript/path','/education/0/transcript/path'):
                response={'decisions':[decision(s['fields'][0]['id'],source)],'record_binding':None}
                proposal,_,notes=compile_semantic(profile,s,response)
                self.assertEqual(proposal['mappings'],[])
                self.assertEqual(proposal['deferred'][0]['reason'].split(':')[0],
                                 'transcript_record_binding_mismatch')
                self.assertEqual(notes[0]['classification'],'unsupported')

    def test_semantic_grade_proof_requires_explicit_policy_for_policy_path(self):
        with tempfile.TemporaryDirectory() as folder:
            source=Path(folder)/'authorized.pdf';source.write_text('synthetic fixture')
            profile={'education':[{'record_id':'edu-bound','transcript':{'path':str(source)},
                                   'transcript_usage_policy':{'use_for_applications':True,
                                                              'path':str(source)}}]}
            s=snapshot();s['mapping_context']={'record_collection':'/education','record_id':'edu-bound'}
            s['fields'][0].update(kind='file',label='成绩单',value='')
            response={'decisions':[decision(s['fields'][0]['id'],
                'record_id:edu-bound/transcript_usage_policy/path')],'record_binding':None}
            proposal,_,notes=compile_semantic(profile,s,response)
            self.assertEqual(proposal['mappings'][0]['source'],
                             'record_id:edu-bound/transcript_usage_policy/path')
            response['decisions'][0]['source']='/education/0/transcript/path'
            proposal,_,_=compile_semantic(profile,s,response)
            self.assertEqual(proposal['mappings'][0]['source'],'/education/0/transcript/path')
            response['decisions'][0]['source']='record_id:edu-bound/transcript_usage_policy/path'
            profile['education'][0]['transcript_usage_policy']['use_for_applications']=False
            proposal,_,notes=compile_semantic(profile,s,response)
            self.assertEqual(proposal['mappings'],[])
            self.assertEqual(proposal['deferred'][0]['reason'].split(':')[0],
                             'transcript_use_for_applications_not_confirmed')

            standalone=copy.deepcopy(s);standalone.pop('mapping_context')
            profile['attachments']=[{'record_id':'transcript-file','kind':'transcript','path':str(source)}]
            response={'decisions':[decision(standalone['fields'][0]['id'],'/attachments/0/path')],
                      'record_binding':None}
            proposal,_,_=compile_semantic(profile,standalone,response)
            self.assertEqual(proposal['mappings'][0]['source'],'/attachments/0/path')

    def test_semantic_professional_skill_certificate_rejects_skill_name_source(self):
        s=snapshot();s['fields'][0].update(label='专业技能证书',value='')
        profile={'certifications':[],'collection_status':{'certifications':'explicit_none'},
                 'skills':[{'name':'Python'}]}
        response={'decisions':[decision(s['fields'][0]['id'],'/skills/0/name')],
                  'record_binding':None}
        proposal,_,notes=compile_semantic(profile,s,response)
        self.assertEqual(proposal['mappings'],[])
        self.assertTrue(proposal['deferred'][0]['reason'].startswith('skill_name_is_not_a_certification:'))
        self.assertEqual(notes[0]['classification'],'unsupported')

    def test_recognized_element_select_readonly_input_is_writable(self):
        s=snapshot();field=s['fields'][0]
        field.update(kind='combobox',readonly=True,component='element-select',control_status='recognized')
        response={'decisions':[{'field_id':field['id'],'classification':'mapping','source':'/school',
            'transform':'identity','depends_on':[],'reason':'same semantic field'}],'record_binding':None}
        proposal,_,notes=compile_semantic({'school':'A'},s,response)
        self.assertEqual(proposal['mappings'][0]['field_id'],field['id'])
        self.assertEqual(notes[0]['classification'],'mapping')

    def test_recognized_date_picker_readonly_input_is_writable(self):
        s=snapshot();field=s['fields'][0]
        field.update(kind='text',readonly=True,component='element-date',control_status='recognized')
        response={'decisions':[{'field_id':field['id'],'classification':'mapping','source':'/school',
            'transform':'identity','depends_on':[],'reason':'same semantic field'}],'record_binding':None}
        proposal,_,notes=compile_semantic({'school':'2025-01-02'},s,response)
        self.assertEqual(proposal['mappings'][0]['field_id'],field['id'])
        self.assertEqual(notes[0]['classification'],'mapping')

    def test_recognized_date_now_picker_readonly_input_is_writable(self):
        s=snapshot();field=s['fields'][0]
        field.update(kind='text',readonly=True,component='element-date-now',control_status='recognized')
        response={'decisions':[{'field_id':field['id'],'classification':'mapping','source':'/school',
            'transform':'identity','depends_on':[],'reason':'same semantic field'}],'record_binding':None}
        proposal,_,notes=compile_semantic({'school':'2026-09-16'},s,response)
        self.assertEqual(proposal['mappings'][0]['field_id'],field['id'])
        self.assertEqual(notes[0]['classification'],'mapping')

    def test_source_correction_requires_real_pointer_and_revalidates(self):
        from edge_form_graph.recovery import correct_proposal
        from edge_form_graph.contracts import compile_plan, json_value
        s = snapshot(); p = {'school': 'A', 'place': {'province': 'P', 'city': 'C'}}
        proposal = {'mappings': [{'field_id': '#school', 'source': '/school', 'transform': 'identity', 'depends_on': []}], 'deferred': []}
        fixed = correct_proposal(p, s, proposal, [{'field_id': '#school', 'source': '/place', 'transform': 'join_location'}])
        self.assertEqual(compile_plan(p, s, fixed)[0][0]['value'], 'P C')
        self.assertEqual(proposal['mappings'][0]['source'], '/school')
        with self.assertRaisesRegex(ContractError, 'location_requires_join_transform'):
            transform(json_value(p, '/place'), 'string')
        with self.assertRaisesRegex(ContractError, 'invalid_correction_field'):
            correct_proposal(p, s, proposal, [{'field_id': '#unknown', 'source': '/place', 'transform': 'join_location'}])
        with self.assertRaisesRegex(ContractError, 'source_not_found'):
            correct_proposal(p, s, proposal, [{'field_id': '#school', 'source': '/missing', 'transform': 'identity'}])

    def test_source_correction_accepts_stable_record_identity(self):
        from edge_form_graph.recovery import correct_proposal
        from edge_form_graph.contracts import compile_plan
        s = snapshot()
        s['mapping_context'] = {'module_type': 'awards', 'record_collection': '/awards',
                                'record_id': 'award-ccpc'}
        profile = {'awards': [{'record_id': 'other', 'award_date': '2024-01-01'},
                              {'record_id': 'award-ccpc', 'award_date': '2025-06-01'}]}
        proposal = {'mappings': [], 'deferred': [{'field_id': '#school', 'reason': 'unknown'}]}
        fixed = correct_proposal(profile, s, proposal, [{
            'field_id': '#school', 'source': 'record_id:award-ccpc/award_date',
            'transform': 'year_month'}])
        self.assertEqual(compile_plan(profile, s, fixed)[0][0]['value'], '2025-06')

        s['mapping_context']['record_id'] = 'other'
        with self.assertRaisesRegex(ContractError, 'mapping_crosses_record_binding'):
            correct_proposal(profile, s, proposal, [{
                'field_id': '#school', 'source': 'record_id:award-ccpc/award_date',
                'transform': 'year_month'}])

    def test_object_source_defers_only_its_field_without_losing_valid_mapping(self):
        s = snapshot()
        s['fields'].append({**s['fields'][0], 'id': '#place', 'selector': '#place', 'label': 'Place'})
        response = {'decisions': [decision('#school', '/school'), decision('#place', '/place')], 'record_binding': None}
        proposal, _, notes = compile_semantic({'school': 'A', 'place': {'province': 'P'}}, s, response)
        self.assertEqual([m['field_id'] for m in proposal['mappings']], ['#school'])
        self.assertEqual(proposal['deferred'][0]['field_id'], '#place')
        self.assertEqual(notes[1]['classification'], 'unsupported')
        response['decisions'][1]['source'] = '/missing'
        with self.assertRaisesRegex(ContractError, 'source_not_found'):
            compile_semantic({'school': 'A'}, s, response)

    def test_empty_string_list_source_defers_only_its_field(self):
        s = snapshot()
        s['fields'].append({**s['fields'][0], 'id': '#certificates', 'selector': '#certificates',
                            'label': 'Certificates'})
        response = {'decisions': [decision('#school', '/school'),
                                  decision('#certificates', '/certifications', 'join_text')],
                    'record_binding': None}
        proposal, _, notes = compile_semantic({'school': 'A', 'certifications': []}, s, response)
        self.assertEqual([m['field_id'] for m in proposal['mappings']], ['#school'])
        self.assertEqual(proposal['deferred'][0]['field_id'], '#certificates')
        self.assertTrue(proposal['deferred'][0]['reason'].startswith('source_is_not_an_answer:'))
        self.assertEqual(notes[1]['classification'], 'unsupported')

    def test_wrong_transform_shape_defers_only_its_field(self):
        s = snapshot()
        s['fields'].append({**s['fields'][0], 'id': '#bad', 'selector': '#bad', 'label': 'Bad'})
        response = {'decisions': [decision('#school', '/school'),
                                  decision('#bad', '/school', 'join_text')],
                    'record_binding': None}
        proposal, _, notes = compile_semantic({'school': 'A'}, s, response)
        self.assertEqual([m['field_id'] for m in proposal['mappings']], ['#school'])
        self.assertEqual(proposal['deferred'][0]['field_id'], '#bad')
        self.assertTrue(proposal['deferred'][0]['reason'].startswith('join_requires_nonempty_string_list:'))
        self.assertEqual(notes[1]['classification'], 'unsupported')

    def test_boolean_identity_for_combobox_is_normalized_by_program(self):
        s = snapshot()
        s['fields'].append({**s['fields'][0], 'id': '#overseas', 'selector': '#overseas',
                            'label': 'Overseas', 'kind': 'combobox'})
        response = {'decisions': [decision('#school', '/school'),
                                  decision('#overseas', '/overseas')],
                    'record_binding': None}
        proposal, _, notes = compile_semantic({'school': 'A', 'overseas': False}, s, response)
        self.assertEqual([m['field_id'] for m in proposal['mappings']], ['#school', '#overseas'])
        self.assertEqual(proposal['mappings'][1]['transform'], 'yes_no')
        self.assertEqual(proposal['deferred'], [])
        self.assertEqual(notes[1]['classification'], 'inference')

    def test_empty_record_bound_string_list_is_not_an_answer(self):
        from edge_form_graph.contracts import json_value
        profile = {'education': [{'record_id': 'edu-1', 'certifications': []}]}
        with self.assertRaisesRegex(ContractError, 'source_is_not_an_answer'):
            json_value(profile, 'record_id:edu-1/certifications')

    def test_location_object_can_include_its_explicit_full_text(self):
        from edge_form_graph.contracts import json_value,transform
        value={'province':'湖南','city':'衡阳','district':'雁峰区','full':'湖南省衡阳市雁峰区'}
        self.assertEqual(transform(json_value({'location':value},'/location'),'join_location'),'湖南 衡阳 雁峰区')

    def test_semantic_wire_uses_short_schema_bound_ids_not_selectors(self):
        model=CodexJsonModel('gpt-6-luna');s=snapshot()
        with patch.object(model,'ask',return_value={'decisions':[decision('field_1','/school')],'record_binding':None}) as ask:
            response=model.match_unknown({'school':'A'},s)
        wire=ask.call_args.args[1]['web_module']
        payload=ask.call_args.args[1]
        self.assertEqual(wire['fields'][0]['id'],'field_1')
        self.assertNotIn('selector',wire['fields'][0])
        self.assertEqual(set(wire['fields'][0]),{'id','label'})
        self.assertNotIn('profile',payload)
        self.assertEqual(payload['profile_keys'],['/school'])
        self.assertNotIn('A',json.dumps(payload,ensure_ascii=False))
        self.assertEqual(response['decisions'][0]['field_id'],'#school')
        self.assertEqual(ask.call_args.args[2]['properties']['decisions']['items']['properties']['field_id']['enum'],['field_1'])

    def test_semantic_record_catalog_exposes_real_ids_for_binding(self):
        from edge_form_graph.model import semantic_mapping_payload
        payload,_=semantic_mapping_payload({'education':[{'record_id':'edu-real','school_name':'A'}]},snapshot())
        self.assertEqual(payload['profile_records'][0]['record_id'],'edu-real')
        self.assertEqual(payload['profile_records'][0]['collection'],'/education')
        self.assertNotIn('/education/0/record_id',payload['profile_keys'])
        self.assertNotIn('A',json.dumps(payload,ensure_ascii=False))

    def test_semantic_boundary_reapplies_module_scope(self):
        model=CodexJsonModel('gpt-6-luna');s=snapshot()
        profile={'education':[{'record_id':'edu-1','school':'A'}],
                 'projects':[{'record_id':'project-1','description':'unrelated'}]}
        with patch.object(model,'ask',return_value={
                'decisions':[decision('field_1','/education/0/school')],'record_binding':None}) as ask:
            model.match_unknown(profile,s)
        keys=ask.call_args.args[1]['profile_keys']
        self.assertIn('/education/0/school',keys)
        self.assertFalse(any(key.startswith('/projects') for key in keys))

    def test_existing_record_binding_limits_semantic_keys_to_that_record(self):
        model=CodexJsonModel('gpt-6-luna');s=snapshot()
        s['mapping_context']={'module_type':'education','record_collection':'/education','record_id':'edu-2'}
        profile={'education':[{'record_id':'edu-1','school':'A','degree':'本科'},
                              {'record_id':'edu-2','school':'B','degree':'硕士'}]}
        with patch.object(model,'ask',return_value={
                'decisions':[decision('field_1','/education/1/school')],'record_binding':None}) as ask:
            model.match_unknown(profile,s)
        keys=ask.call_args.args[1]['profile_keys']
        self.assertIn('/education/1/school',keys)
        self.assertIn('/education/1/degree',keys)
        self.assertFalse(any(key.startswith('/education/0/') for key in keys))

    def test_deferred_candidate_pointer_is_never_an_action(self):
        d=decision('#school','/not_an_answer');d['classification']='unsupported'
        proposal,_,_=compile_semantic({},snapshot(),{'decisions':[d],'record_binding':None})
        self.assertEqual(proposal['mappings'],[])
        self.assertEqual(proposal['deferred'][0]['field_id'],'#school')

    def test_transform_classification_is_owned_by_program(self):
        d=decision('#school','/cities','join_text');d['classification']='mapping'
        proposal,_,notes=compile_semantic({'cities':['甲','乙']},snapshot(),{'decisions':[d],'record_binding':None})
        self.assertEqual(notes[0]['classification'],'inference')
        self.assertEqual(proposal['mappings'][0]['transform'],'join_text')

    def test_semantic_self_dependency_is_removed_without_dropping_real_dependencies(self):
        s=snapshot();s['fields'].append({**s['fields'][0],'id':'#city','selector':'#city','label':'城市'})
        first=decision('#school','/school');first['depends_on']=['#school','#school']
        second=decision('#city','/city');second['depends_on']=['#school','#city']
        proposal,_,notes=compile_semantic({'school':'A','city':'B'},s,
            {'decisions':[first,second],'record_binding':None})
        self.assertEqual(proposal['mappings'][0]['depends_on'],[])
        self.assertEqual(proposal['mappings'][1]['depends_on'],['#school'])
        self.assertEqual(notes[1]['depends_on'],['#school'])

    def test_second_module_gets_its_own_semantic_batch(self):
        from edge_form_graph.application import build_application, application_state, active_command
        from tests.test_application import manifest, answer
        with tempfile.TemporaryDirectory() as d, SqliteSaver.from_conn_string(':memory:') as saver:
            semantic=SemanticModel({'#school':('/school',)})
            graph=build_application(Model(),saver,knowledge=KnowledgeStore(Path(d)/'k.json'),semantic_model=semantic)
            cfg={'configurable':{'thread_id':'two-modules'},'recursion_limit':100}
            graph.invoke(application_state({'school':'示例大学'},manifest(),allow_save=False),cfg)
            for _ in range(6):
                command=active_command(graph.get_state(cfg,subgraphs=True))
                if not command:break
                graph.invoke(Command(resume=answer(command)),cfg)
            self.assertEqual([s['module_id'] for s in semantic.calls],['a','b'])

    def test_new_child_resets_semantic_attempt_flag_and_ephemeral_queues(self):
        state=initial_state({},snapshot())
        self.assertIs(state['semantic_attempted'],False)
        self.assertEqual(state['enum_pending'],[])
        self.assertEqual(state['last_receipt'],{})

    def test_enum_and_field_maps_are_reused_without_matching_calls(self):
        from tests.test_enum_repair import EnumModel, enum_snapshot, enum_receipt
        with tempfile.TemporaryDirectory() as d:
            path = Path(d)/'knowledge.json'
            for run in range(2):
                with SqliteSaver.from_conn_string(':memory:') as saver:
                    semantic = SemanticModel({'#school': ('/school',), '#degree': ('/degree',)})
                    enums = EnumModel()
                    semantic.review_enums = enums.review_enums
                    graph = build_graph(EnumModel(), saver, knowledge=KnowledgeStore(path), semantic_model=semantic)
                    cfg = {'configurable': {'thread_id': str(run)}}
                    graph.invoke(initial_state({'school': '示例大学', 'degree': '本科'}, enum_snapshot(), allow_save=True), cfg)
                    first = graph.get_state(cfg).values['command']
                    graph.invoke(Command(resume=enum_receipt(first)), cfg)
                    second = graph.get_state(cfg).values['command']
                    self.assertEqual(second['operations'][0]['value'], '大学本科')
                    graph.invoke(Command(resume=enum_receipt(second, success=True)), cfg)
                    state = finish_saved(graph, cfg)
                    self.assertEqual(state['status'], 'saved')
                    self.assertEqual(len(semantic.calls), 1 if run == 0 else 0)
                    self.assertEqual(enums.enum_calls, 1 if run == 0 else 0)
                    if run:
                        self.assertEqual(state['metrics']['field_table_hits'], 2)
                        self.assertEqual(state['metrics']['enum_table_hits'], 1)

    def test_learning_failure_is_reported_without_undoing_verified_fill(self):
        with tempfile.TemporaryDirectory() as d, SqliteSaver.from_conn_string(':memory:') as saver:
            store = KnowledgeStore(Path(d)/'k.json')
            semantic = SemanticModel({'#school': ('/school',)})
            graph = build_graph(Model(), saver, knowledge=store, semantic_model=semantic)
            cfg = {'configurable': {'thread_id': 'learning-failure'}}
            graph.invoke(initial_state({'school': 'University'}, snapshot(), allow_save=True), cfg)
            command = graph.get_state(cfg).values['command']
            graph.invoke(Command(resume=fill_receipt(command, snapshot())), cfg)
            with patch.object(store, 'compile_saved', side_effect=OSError('private path must not leak')):
                state = finish_saved(graph, cfg)
            self.assertEqual(state['status'], 'saved')
            self.assertEqual(state['learning_errors'], [{'phase': 'compile_saved', 'error': 'OSError'}])

    def test_persistent_second_run_skips_semantic_model_and_uses_new_facts(self):
        with tempfile.TemporaryDirectory() as d:
            store = KnowledgeStore(Path(d)/'knowledge.json')
            for value, expected_calls in [('University A', 1), ('University B', 0)]:
                with SqliteSaver.from_conn_string(':memory:') as saver:
                    regular = Model()
                    regular.map = lambda *a: self.fail('legacy whole-module map called')
                    semantic = SemanticModel({'#school': ('/school',)})
                    graph = build_graph(regular, saver, knowledge=KnowledgeStore(store.path), semantic_model=semantic)
                    cfg = {'configurable': {'thread_id': value}}
                    graph.invoke(initial_state({'school': value}, snapshot(), allow_save=True), cfg)
                    command = graph.get_state(cfg).values['command']
                    self.assertEqual(command['operations'][0]['value'], value)
                    graph.invoke(Command(resume=fill_receipt(command, snapshot())), cfg)
                    self.assertEqual(finish_saved(graph, cfg)['status'], 'saved')
                    self.assertEqual(len(semantic.calls), expected_calls)

    def test_known_fields_are_dispatched_before_one_unknown_batch(self):
        with tempfile.TemporaryDirectory() as d, SqliteSaver.from_conn_string(':memory:') as saver:
            store = KnowledgeStore(Path(d)/'k.json')
            s = snapshot(); p = {'school': 'University', 'major': 'CS', 'city': 'City'}
            plan = Model().map(p, s); after = copy.deepcopy(s); after['fields'][0]['value'] = p['school']
            store.learn_fields(p, s, plan, after, True)
            s['fields'] += [{**s['fields'][0], 'id': '#'+k, 'selector': '#'+k, 'label': k} for k in ('major', 'city')]
            semantic = SemanticModel({'#major': ('/major',), '#city': ('/city',)})
            graph = build_graph(Model(), saver, knowledge=store, semantic_model=semantic)
            cfg = {'configurable': {'thread_id': 'known-first'}}
            graph.invoke(initial_state(p, s), cfg)
            first = graph.get_state(cfg).values['command']
            self.assertEqual([op['id'] for op in first['operations']], ['#school'])
            self.assertEqual(semantic.calls, [])
            r = fill_receipt(first, s)
            graph.invoke(Command(resume=r), cfg)
            second = graph.get_state(cfg).values['command']
            self.assertEqual({op['id'] for op in second['operations']}, {'#major', '#city'})
            self.assertEqual(len(semantic.calls), 1)
            self.assertEqual(len(semantic.calls[0]['context_fields']), 3)

    def test_unbound_prefilled_unknown_is_compared_by_semantic_mapping(self):
        with tempfile.TemporaryDirectory() as d, SqliteSaver.from_conn_string(':memory:') as saver:
            s=snapshot();s['fields'][0]['value']='用户现有值';s['fields'][0]['value_present']=True
            semantic=SemanticModel({'#school':('/school',)})
            graph=build_graph(Model(),saver,knowledge=KnowledgeStore(Path(d)/'k.json'),semantic_model=semantic)
            cfg={'configurable':{'thread_id':'prefilled-skip'}}
            graph.invoke(initial_state({'school':'资料库值'},s),cfg)
            state=graph.get_state(cfg).values
            self.assertEqual(len(semantic.calls),1)
            self.assertEqual(state['status'],'awaiting_edge')
            self.assertEqual(state['command']['operations'][0]['value'],'资料库值')

    def test_bound_prefilled_mismatch_is_corrected_without_semantic_worker(self):
        with tempfile.TemporaryDirectory() as d, SqliteSaver.from_conn_string(':memory:') as saver:
            s=snapshot();s['mapping_context']={'record_collection':'/education','record_id':'edu-1'}
            s['fields'][0].update(value='2024-09-01',value_present=True,label='入学时间')
            semantic=SemanticModel({})
            graph=build_graph(Model(),saver,knowledge=KnowledgeStore(Path(d)/'k.json'),semantic_model=semantic)
            cfg={'configurable':{'thread_id':'prefilled-program-compare'}}
            graph.invoke(initial_state({'education':[{'record_id':'edu-1','start_date':'2024-09-06'}]},s),cfg)
            state=graph.get_state(cfg).values
            self.assertEqual(semantic.calls,[])
            self.assertEqual(state['status'],'awaiting_edge')
            self.assertEqual(state['command']['operations'][0]['value'],'2024-09-06')
            self.assertEqual(state['command']['operations'][0]['source'],'/education/0/start_date')

    def test_prefilled_unknown_survives_semantic_merge_for_empty_peer(self):
        with tempfile.TemporaryDirectory() as d, SqliteSaver.from_conn_string(':memory:') as saver:
            s=snapshot();s['fields'][0]['value']='用户现有值';s['fields'][0]['value_present']=True
            s['fields'].append({**s['fields'][0], 'id':'#optional', 'selector':'#optional',
                                'label':'Optional', 'value':'', 'value_present':False, 'required':False})
            semantic=SemanticModel({})
            semantic.match_unknown=lambda profile,before: {'decisions': [{
                'field_id':field['id'],'classification':'unsupported','source':None,
                'transform':'identity','depends_on':[],'reason':'No confirmed answer.'
            } for field in before['fields']], 'record_binding':None}
            graph=build_graph(Model(),saver,knowledge=KnowledgeStore(Path(d)/'k.json'),semantic_model=semantic)
            cfg={'configurable':{'thread_id':'prefilled-mixed'}}
            graph.invoke(initial_state({'school':'资料库值', 'empty':[]},s),cfg)
            state=graph.get_state(cfg).values
            self.assertEqual(state['status'],'verified_draft')
            self.assertEqual(state['results']['#school']['status'],'prefilled_pending_review')
            self.assertEqual(state['results']['#optional']['status'],'deferred')

    def test_inference_is_recomputed_not_cached_literal(self):
        with tempfile.TemporaryDirectory() as d:
            store = KnowledgeStore(Path(d)/'k.json')
            s = snapshot(); s['fields'][0]['label'] = '是否取得本阶段毕业证'
            for enrolled, expected, calls in [(True, '是', 1), (False, '否', 0)]:
                with SqliteSaver.from_conn_string(':memory:') as saver:
                    semantic = SemanticModel({'#school': ('/has_diploma', 'yes_no')})
                    graph = build_graph(Model(), saver, knowledge=store, semantic_model=semantic)
                    cfg = {'configurable': {'thread_id': str(enrolled)}}
                    graph.invoke(initial_state({'has_diploma': enrolled}, s, allow_save=True), cfg)
                    op = graph.get_state(cfg).values['command']
                    self.assertEqual(op['operations'][0]['value'], expected)
                    graph.invoke(Command(resume=fill_receipt(op, s)), cfg)
                    finish_saved(graph, cfg)
                    self.assertEqual(len(semantic.calls), calls)

    def test_record_binding_cannot_cross_masters_and_project(self):
        p = {'education': [{'record_id': 'master', 'start': '2024-09'}, {'record_id': 'bachelor', 'start': '2020-09'}]}
        response = {'decisions': [decision('#school', '/education/1/start')],
                    'record_binding': {'record_collection': '/education', 'record_id': 'master', 'reason': 'masters module'}}
        with self.assertRaisesRegex(ContractError, 'crosses_record'):
            compile_semantic(p, snapshot(), response)

    def test_new_binding_checks_preexisting_mappings_too(self):
        p = {'education': [{'record_id': 'master', 'school': 'A'}, {'record_id': 'bachelor', 'school': 'B'}]}
        s = snapshot()
        s['mapping_context'] = {'record_collection': '/education', 'record_id': 'master'}
        with self.assertRaisesRegex(ContractError, 'crosses_record'):
            validate_record_sources(p, s, [{'source': '/education/1/school'}])
        p['projects'] = [{'record_id': 'project-a', 'school': 'Project venue'}]
        with self.assertRaisesRegex(ContractError, 'unbound_array_source'):
            validate_record_sources(p, s, [{'source': '/projects/0/school'}])

    def test_recovery_preserves_record_identity_and_rejects_rebinding(self):
        from edge_form_graph.recovery import reconcile
        s = snapshot()
        s['mapping_context'] = {'record_collection': '/education', 'record_id': 'master'}
        result = {'snapshot': s, 'profile': {}, 'proposal': {'mappings': [], 'deferred': []}}
        fresh = snapshot()
        cache, _ = reconcile(result, fresh)
        self.assertEqual(cache['snapshot']['mapping_context'], s['mapping_context'])
        self.assertNotIn('mapping_context', fresh)
        fresh['mapping_context'] = {'record_collection': '/education', 'record_id': 'bachelor'}
        with self.assertRaisesRegex(ContractError, 'mapping_context_changed'):
            reconcile(result, fresh)

    def test_selected_model_is_luna_without_silent_fallback(self):
        from edge_form_graph.runtime import components
        _, semantic = components()
        self.assertEqual(semantic.model, 'gpt-6-luna')
        with patch.object(semantic, 'ask', return_value={'decisions': [], 'record_binding': None}) as ask:
            semantic.match_unknown({}, snapshot())
            self.assertIn('Map every supplied web field label', ask.call_args.args[0])

    def test_safe_transforms_preserve_date_precision_and_list_preferences(self):
        self.assertEqual(transform(['Beijing', 'Shanghai'], 'join_text'), 'Beijing、Shanghai')
        self.assertEqual(transform('2024-09-17', 'year_month'), '2024-09')
        self.assertEqual(transform('CET-4', 'normalize_exam_level'), 'CET4')
        self.assertEqual(transform('工学学士', 'degree_category'), '学士')
        self.assertEqual(transform('实习带教，正式员工', 'last_clause'), '正式员工')
        self.assertFalse(transform(True, 'not_bool'))
        self.assertEqual(transform(True, 'enrolled_no_diploma'), '否')
        with self.assertRaisesRegex(ContractError, 'condition_not_met'):
            transform(False, 'enrolled_no_diploma')
        for value in (None, 'yes', 1):
            with self.assertRaises(ContractError):
                transform(value, 'not_bool')


if __name__ == '__main__':
    unittest.main()
