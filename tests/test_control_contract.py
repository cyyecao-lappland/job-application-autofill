import copy
import json
import subprocess
import tempfile
import unittest
from pathlib import Path

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from edge_form_graph.control_registry import registry_report, assert_registry
from edge_form_graph.control_contract import compile_control_target, validate_control_outcome
from edge_form_graph.control_resolution import structure_key, learn_recipe
from edge_form_graph.contracts import ContractError, page_matches, json_value, redacted_profile
from edge_form_graph.graph import build_graph, initial_state
from edge_form_graph.verification import carry_control_skips
from test_graph import Model, snapshot, receipt

ROOT = Path(__file__).resolve().parents[1]


class ControlContractTests(unittest.TestCase):
    def test_actual_js_loaded_declarations_match_python(self):
        result = subprocess.run(['node', '--input-type=module', '-e',
            "import {registryReport} from './browser/controls/service.mjs'; console.log(JSON.stringify(registryReport()))"],
            cwd=ROOT, capture_output=True, text=True, check=True)
        self.assertEqual(assert_registry(json.loads(result.stdout)), registry_report())

    def test_order_is_irrelevant_but_every_protocol_field_is_checked(self):
        current = registry_report()
        changed = copy.deepcopy(current)
        changed['controls'].reverse()
        self.assertEqual(assert_registry(changed), current)
        for key, replacement in [('adapterVersion', 99), ('targetTypes', ['other']), ('protocolVersion', 99)]:
            changed = copy.deepcopy(current)
            changed['controls'][0][key] = replacement
            with self.assertRaisesRegex(ContractError, key):
                assert_registry(changed)
        for key in ('protocolVersion', 'registryVersion'):
            changed = copy.deepcopy(current); changed[key] += 1
            with self.assertRaisesRegex(ContractError, key):
                assert_registry(changed)

    def test_duplicates_and_missing_controls_are_rejected(self):
        current = registry_report()
        current['controls'].append(current['controls'][0])
        with self.assertRaisesRegex(ContractError, 'duplicate'):
            assert_registry(current)
        current = registry_report(); current['controls'].pop()
        with self.assertRaisesRegex(ContractError, 'missing'):
            assert_registry(current)

    def test_personal_values_are_ordinary_values(self):
        profile = {'password': 'test-password', 'identity_document_number': 'test-id'}
        self.assertEqual(redacted_profile(profile), profile)
        self.assertEqual(json_value(profile, '/password'), profile['password'])
        self.assertEqual(compile_control_target(profile, '/identity_document_number', 'plain_text_probe_v1'),
                         {'kind': 'text', 'text': 'test-id'})

    def test_month_precision_and_explicit_current_are_preserved(self):
        profile = {'education':[{'start_date':'2024-09','is_current':True}]}
        target = compile_control_target(profile, '/education/0/start_date', 'next_range_date_v1')
        self.assertEqual(target['start'], {'precision':'month','year':2024,'month':9})
        self.assertEqual(target['end'], {'kind':'current'})
        profile['education'][0]['is_current'] = False
        with self.assertRaises(ContractError):
            compile_control_target(profile, '/education/0/start_date', 'next_range_date_v1')
        with self.assertRaises(ContractError):
            compile_control_target({'date':'2026-02-30'}, '/date', 'element_date_picker_v1')

    def test_current_state_does_not_identify_a_recipe(self):
        field = {'kind':'text','component':None,'readonly':False,'disabled':False}
        self.assertEqual(structure_key(field), structure_key({**field,'disabled':True,'readonly':True}))
        self.assertNotEqual(structure_key(field), structure_key({**field,'kind':'combobox'}))

    def test_skipped_control_is_not_a_learned_method(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(ContractError, 'verified_commit'):
                learn_recipe({'kind':'text'}, 'plain_text_probe_v1', {
                    'command_id':'one','status':'completed','settled':True,
                    'evidence':{'adapter':'plain_text_probe_v1','committed':False,'verification':'skipped'}},
                    Path(directory)/'recipes.json')

    def test_timeout_cannot_be_labeled_verification_skipped(self):
        command = {'command_id':'one','adapter':'plain_text_probe_v1'}
        outcome = {'schema':'control-receipt/v1','operationId':'one','adapter':'plain_text_probe_v1',
                   'persistence':'not_assessed','call':'finished','verification':'skipped','committed':False,'reason':'readback_incomplete'}
        validate_control_outcome(command, outcome, True)
        with self.assertRaises(ContractError):
            validate_control_outcome(command, outcome, False)

    def test_skip_continues_graph_without_refill_or_false_match(self):
        with SqliteSaver.from_conn_string(':memory:') as saver:
            graph = build_graph(Model(), saver)
            config = {'configurable': {'thread_id':'skip'}}
            graph.invoke(initial_state({'school':'示例大学'}, snapshot(), agent_tuning=False), config)
            state = graph.get_state(config)
            command = state.values['command']
            result = receipt(command, status='verification_skipped')
            result['results'][0]['reason'] = 'readback_incomplete'
            result['snapshot']['fields'][0].update(value=None, value_readable=False)
            graph.invoke(Command(resume=result), config)
            current = graph.get_state(config).values
            self.assertEqual(current['status'], 'verified_draft')
            self.assertIsNone(current['command'])
            self.assertEqual(current['results']['#school']['status'], 'verification_skipped')
            self.assertEqual(current['metrics']['written'], 0)
            self.assertEqual(current['metrics']['verification_skipped'], 1)
            changed = copy.deepcopy(current['current'])
            changed['fields'][0].update(value='用户修改', value_readable=True)
            self.assertFalse(page_matches(current['operations'], changed))

    def test_skip_carry_is_limited_to_exact_finished_control(self):
        snap = snapshot(); snap['fields'][0].update(value=None,value_readable=False)
        command = {'command_id':'one','target':snap['target'],'module_id':snap['module_id'],
                   'field_selector':'#school','field_signature':snap['fields'][0]['signature']}
        record = {'command':command,'receipt':{'settled':True,'status':'completed','evidence':{'verification':'skipped'}}}
        self.assertTrue(carry_control_skips(copy.deepcopy(snap), [record])['fields'][0]['verification_skipped'])
        record['receipt']['settled'] = False
        self.assertNotIn('verification_skipped', carry_control_skips(copy.deepcopy(snap), [record])['fields'][0])


if __name__ == '__main__':
    unittest.main()
