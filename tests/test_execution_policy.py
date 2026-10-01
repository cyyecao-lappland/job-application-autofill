import copy
import tempfile
import unittest
from pathlib import Path

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from edge_form_graph.application import application_state, build_application, active_command
from edge_form_graph.execution_policy import FAILURE_TEXT, FIRST_OPTION, fallback_operations
from edge_form_graph.graph import build_graph, initial_state
from edge_form_graph.contracts import ContractError
from edge_form_graph.knowledge import KnowledgeStore
from edge_form_graph.reporting import build_report
from tests.test_graph import snapshot
from tests.test_application import manifest, answer
from tests.test_program_first import fill_receipt


class NoModel:
    def __getattr__(self, name):
        if name in {'map', 'review', 'review_enums', 'match_unknown', 'ask'}:
            def forbidden(*args, **kwargs):
                raise AssertionError('model called with tuning off: ' + name)
            return forbidden
        raise AttributeError(name)


class ExecutionPolicyTests(unittest.TestCase):
    def test_known_mapping_is_used_before_any_fallback_without_model(self):
        class KnownTable:
            def known_plan(self, profile, before):
                return {'mappings': [{'field_id': '#school', 'source': '/school',
                        'transform': 'identity', 'depends_on': []}], 'deferred': []}
        before = snapshot()
        with SqliteSaver.from_conn_string(':memory:') as saver:
            graph = build_graph(NoModel(), saver, knowledge=KnownTable())
            cfg = {'configurable': {'thread_id': 'known'}}
            graph.invoke(initial_state({'school': 'Source School'}, before, agent_tuning=False), cfg)
            state = graph.get_state(cfg).values
            self.assertEqual(state['command']['operations'][0]['value'], 'Source School')
            graph.invoke(Command(resume=fill_receipt(state['command'], before)), cfg)
            state = graph.get_state(cfg).values
            self.assertEqual(state['status'], 'verified_draft')
            self.assertEqual(state['manual_review'], [])
            self.assertEqual(state['metrics']['model_calls'], 0)
            self.assertEqual(state['metrics']['written'], 1)

    def test_uncached_enum_mismatch_uses_fallback_without_enum_model(self):
        class KnownTable:
            def known_plan(self, profile, before):
                return {'mappings': [{'field_id': '#school', 'source': '/school',
                        'transform': 'identity', 'depends_on': []}], 'deferred': []}
            def enum_decisions(self, profile, snapshot, operations, requests):
                return []
        before = snapshot()
        before['fields'][0].update(kind='select', options=[{'label': 'First', 'disabled': False}])
        with SqliteSaver.from_conn_string(':memory:') as saver:
            graph = build_graph(NoModel(), saver, knowledge=KnownTable())
            cfg = {'configurable': {'thread_id': 'enum'}}
            graph.invoke(initial_state({'school': 'Other'}, before, agent_tuning=False), cfg)
            command = graph.get_state(cfg).values['command']
            receipt = fill_receipt(command, before)
            receipt['snapshot'] = copy.deepcopy(before)
            receipt['results'][0].update(status='deferred', reason='option_missing_or_ambiguous')
            receipt['evidence']['enum_candidates'] = [{'field_id': '#school', 'source_value': 'Other',
                                                      'options': ['First']}]
            graph.invoke(Command(resume=receipt), cfg)
            state = graph.get_state(cfg).values
            self.assertEqual(state['command']['operations'][0]['value'], FIRST_OPTION)
            self.assertEqual(state['metrics']['model_calls'], 0)

    def test_off_runs_multiple_modules_and_save_without_any_model(self):
        with SqliteSaver.from_conn_string(':memory:') as saver:
            graph = build_application(NoModel(), saver)
            cfg = {'configurable': {'thread_id': 'off'}, 'recursion_limit': 100}
            graph.invoke(application_state({}, manifest(), allow_save=True, agent_tuning=False), cfg)
            actions = []
            for _ in range(12):
                command = active_command(graph.get_state(cfg, subgraphs=True))
                if not command:
                    break
                actions.append((command['kind'], command['module_id']))
                receipt = answer(command)
                if command['kind'] == 'fill':
                    self.assertIs(command['agent_tuning'], False)
                    self.assertEqual(command['operations'][0]['value'], FAILURE_TEXT)
                    receipt['snapshot']['fields'][0]['value'] = FAILURE_TEXT
                graph.invoke(Command(resume=receipt), cfg)
            state = graph.get_state(cfg).values
            self.assertEqual(actions, [('observe', 'a'), ('fill', 'a'), ('observe', 'b'),
                                       ('fill', 'b'), ('save_scope', 'g')])
            self.assertEqual(state['status'], 'complete_with_fallbacks')
            self.assertIs(state['agent_tuning'], False)
            for result in state['results'].values():
                self.assertEqual(result['metrics']['model_calls'], 0)
                self.assertEqual(result['metrics']['fallback_written'], 1)
                self.assertEqual(result['metrics']['written'], 0)
                self.assertEqual(len(result['manual_review']), 1)
            with tempfile.TemporaryDirectory() as tmp:
                report = build_report(tmp, state)
                self.assertEqual(len(report['manual_review']), 2)
                self.assertFalse(report['evidence_currency']['current_completion_confirmed'])
                store = KnowledgeStore(Path(tmp) / 'knowledge.json')
                self.assertEqual(store.compile_saved({}, list(state['results'].values()), {}),
                                 {'field_learned': 0, 'enum_learned': 0})

    def test_dropdown_actual_value_survives_multiple_batches(self):
        before = snapshot()
        before['fields'][0].update(kind='select', options=[{'label': 'First', 'disabled': False}])
        before['fields'].append({**snapshot()['fields'][0], 'id': '#other', 'selector': '#other'})
        with SqliteSaver.from_conn_string(':memory:') as saver:
            graph = build_graph(NoModel(), saver)
            cfg = {'configurable': {'thread_id': 'batches'}}
            graph.invoke(initial_state({}, before, agent_tuning=False), cfg)
            state = graph.get_state(cfg).values
            command = state['command']
            receipt = fill_receipt(command, before)
            receipt['snapshot']['fields'][0]['value'] = 'First'
            receipt['snapshot']['fields'][1]['value'] = ''
            receipt['results'][1].update(status='unattempted', reason='batch_boundary')
            receipt['evidence']['fallback_values'] = {'#school': 'First'}
            graph.invoke(Command(resume=receipt), cfg)
            state = graph.get_state(cfg).values
            self.assertEqual([o['id'] for o in state['command']['operations']], ['#other'])
            graph.invoke(Command(resume=fill_receipt(state['command'], state['current'])), cfg)
            state = graph.get_state(cfg).values
            self.assertEqual(state['status'], 'verified_draft')
            self.assertEqual(state['operations'][0]['value'], 'First')
            self.assertEqual(state['metrics']['model_calls'], 0)

    def test_new_dynamic_field_reenters_program_mapping(self):
        before = snapshot()
        with SqliteSaver.from_conn_string(':memory:') as saver:
            graph = build_graph(NoModel(), saver)
            cfg = {'configurable': {'thread_id': 'dynamic'}, 'recursion_limit': 100}
            graph.invoke(initial_state({}, before, agent_tuning=False), cfg)
            state = graph.get_state(cfg).values
            receipt = fill_receipt(state['command'], before)
            receipt['snapshot']['fields'].append({**before['fields'][0], 'id': '#new', 'selector': '#new'})
            receipt['evidence']['linkage'] = {'trigger_id': '#school', 'added': ['#new'], 'changed': [], 'removed': []}
            graph.invoke(Command(resume=receipt), cfg)
            for _ in range(4):
                state = graph.get_state(cfg).values
                if not state['command']:
                    break
                graph.invoke(Command(resume=fill_receipt(state['command'], state['current'])), cfg)
            state = graph.get_state(cfg).values
            self.assertEqual(state['status'], 'verified_draft')
            self.assertEqual(state['rediscovery_rounds'], 1)
            self.assertEqual(state['linkage']['depths']['#new'], 2)
            self.assertEqual({x['field_id'] for x in state['manual_review']}, {'#school', '#new'})

    def test_fallback_does_not_retry_unknown_writes_or_touch_protected_fields(self):
        before = snapshot()
        fid = before['fields'][0]['id']
        for status in ('written', 'already_matched', 'unknown', 'conflict'):
            self.assertEqual(fallback_operations(before, {fid: {'status': status}}, set()), [])
        before['fields'][0]['protected'] = True
        self.assertEqual(fallback_operations(before, {}, set()), [])

    def test_policy_requires_boolean(self):
        for flag in ('off', 0, None):
            with self.assertRaises(ContractError):
                initial_state({}, snapshot(), agent_tuning=flag)


if __name__ == '__main__':
    unittest.main()
