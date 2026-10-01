import json
import tempfile
import time
import unittest
from pathlib import Path

from edge_form_graph.contracts import ContractError
from edge_form_graph.control_resolution import learn_recipe, resolve_controls, assert_control_settled


class ControlResolutionTests(unittest.TestCase):
    def test_luna_control_ids_are_short_constrained_and_restored(self):
        from edge_form_graph.model import CodexJsonModel
        model=CodexJsonModel(model='gpt-6-luna')
        original=':scope > div:nth-of-type(1) > input:nth-of-type(2)'
        def ask(instructions,data,schema):
            self.assertEqual(data['fields'][0]['field_id'],'control_1')
            self.assertEqual(schema['properties']['decisions']['items']['properties']['field_id']['enum'],['control_1'])
            return {'decisions':[{'field_id':'control_1','action':'needs_implementation','adapter':None}]}
        model.ask=ask
        result=model.diagnose_controls({'fields':[{'field_id':original,'label':'date'}],'allowed_adapters':[]})
        self.assertEqual(result['decisions'][0]['field_id'],original)
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.path = self.folder/'recipes.json'
        self.field = {'id': 'date1', 'label': 'Start date', 'kind': 'combobox',
                      'control_pattern': 'next_range_date', 'readonly': True,
                      'signature': {'tag': 'INPUT', 'role': 'combobox'}}
        self.values = {'status': 'module_blocked', 'profile': {'education': [
            {'record_id': 'other'}, {'record_id': 'wanted', 'start_date': '2024-09-06', 'end_date': '2027-07-01'}]},
            'manifest': {'modules': [{'id': 'edu', 'mapping_context': {
                'record_collection': '/education', 'record_id': 'wanted'}}]}, 'index': 0,
            'control_diagnostics': {'settled': True, 'snapshot': {
                'observed_at': time.time(), 'fields': [self.field]}}}
        self.receipt = {'command_id': 'verified1', 'settled': True, 'status': 'completed',
                        'evidence': {'committed': True, 'adapter': 'next_range_date_v1'}}
        self.knowledge = type('Knowledge', (), {'control_sources': lambda *args: {}})()

    def test_verified_recipe_rebinds_current_record_without_model_or_values(self):
        learn_recipe(self.field, 'next_range_date_v1', self.receipt, self.path)
        class Model:
            def ask(self, *args):
                raise AssertionError('learned structure must not call model')
        result = resolve_controls(self.values, self.knowledge, Model(), self.folder, self.path)
        self.assertEqual(result['ready'][0]['source'], '/education/1/start_date')
        self.assertEqual(result['agent_calls'], 0)
        persisted = self.path.read_text()
        self.assertNotIn('2024', persisted)
        self.assertNotIn('/education/1', persisted)

    def test_unknown_method_asks_model_but_does_not_learn_proposal(self):
        outer = self
        class Model:
            def ask(self, instructions, data, schema):
                outer.assertNotIn('2024', json.dumps(data))
                outer.assertNotIn('profile', data)
                return {'decisions': [{'field_id': 'date1', 'action': 'use_adapter', 'adapter': 'next_range_date_v1'}]}
        result = resolve_controls(self.values, self.knowledge, Model(), self.folder, self.path)
        self.assertEqual(result['agent_calls'], 1)
        self.assertFalse(self.path.exists())

    def test_unverified_receipt_cannot_learn(self):
        for change in ({'settled': False}, {'status': 'unconfirmed'}, {'evidence': {'committed': False}}):
            with self.assertRaises(ContractError):
                learn_recipe(self.field, 'next_range_date_v1', {**self.receipt, **change}, self.path)
        self.assertFalse(self.path.exists())

    def test_wrong_range_adapter_is_blocked_before_preparation(self):
        self.field.update(component='ud-range-date',control_pattern='ud_date_pair',control_status='agent_required')
        class Model:
            def ask(self, *args):
                return {'decisions':[{'field_id':'date1','action':'use_adapter','adapter':'next_range_date_v1'}]}
        result=resolve_controls(self.values,self.knowledge,Model(),self.folder,self.path)
        self.assertEqual(result['ready'],[])
        self.assertTrue(result['blocked'])

    def test_invalid_date_source_is_blocked_before_preparation(self):
        self.field.update(control_pattern=None,control_status='agent_required')
        self.knowledge.control_sources=lambda *args:{'date1':'/education/1/record_id'}
        class Model:
            def ask(self, *args):
                return {'decisions':[{'field_id':'date1','action':'use_adapter','adapter':'element_date_picker_v1'}]}
        result=resolve_controls(self.values,self.knowledge,Model(),self.folder,self.path)
        self.assertEqual(result['ready'],[])
        self.assertEqual(result['blocked'][0]['reason'],'invalid_date')

    def test_save_or_unsettled_control_never_authorizes_repair(self):
        for status in ('needs_reconciliation', 'awaiting_scope_save'):
            with self.assertRaises(ContractError):
                assert_control_settled({**self.values, 'status': status})
        with self.assertRaises(ContractError):
            assert_control_settled({**self.values, 'status': 'control_blocked',
                                   'control_history': [{'receipt': {'settled': False}}]})

    def test_missing_record_does_not_guess_source(self):
        self.values['manifest']['modules'][0]['mapping_context']['record_id'] = 'missing'
        learn_recipe(self.field, 'next_range_date_v1', self.receipt, self.path)
        result = resolve_controls(self.values, self.knowledge, None, self.folder, self.path)
        self.assertEqual(result['ready'], [])
        self.assertEqual(result['blocked'][0]['reason'], 'field_source_unresolved')

    def test_single_probed_city_dialog_routes_without_model(self):
        self.field.clear()
        self.field.update(id='city',label='期望城市',kind='text',signature={'tag':'INPUT','type':'text'})
        self.values['profile']['batch_answers']={'preferred_city':'上海'}
        target={'browser':'edge','tab_id':'same-tab'}
        self.values['control_diagnostics']['snapshot'].update(target=target,module_id='edu')
        self.values['results']={'edu':{'current':{'fields':[dict(self.field)]},'last_receipt':{'target':target,'module_id':'edu','results':[
            {'id':'city','status':'deferred','reason':'text_probe_discovered_composite_control'}]}}}
        self.values['control_diagnostics']['evidence']={'controls':{'dialogs':[{
            'title':'城市选择','controls':[
                {'tag':'SELECT','options':[{'label':'省'}]},
                {'tag':'SELECT','options':[{'label':'市'}]},
                {'tag':'BUTTON','text':'确定'}]}]}}
        result=resolve_controls(self.values,self.knowledge,None,self.folder,self.path)
        self.assertEqual(result['ready'][0]['adapter'],'province_city_dialog_v1')
        self.assertEqual(result['ready'][0]['source'],'/batch_answers/preferred_city')
        self.assertEqual(result['agent_calls'],0)

    def test_matched_date_does_not_call_agent_or_generate_another_action(self):
        self.field.update(range_endpoint='start', value='2024-09', expanded='false')
        result = resolve_controls(self.values, self.knowledge, None, self.folder, self.path)
        self.assertEqual(result['ready'], [])
        self.assertEqual(result['already_matched'], ['date1'])
        self.assertEqual(result['agent_calls'], 0)
