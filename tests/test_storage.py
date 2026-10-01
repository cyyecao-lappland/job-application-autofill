import sqlite3
import random
import json
import tempfile
import threading
import unittest
from pathlib import Path
from unittest.mock import patch

from langgraph.checkpoint.sqlite import SqliteSaver as OldSaver
from langgraph.types import Command
from langgraph.checkpoint.base import empty_checkpoint
from edge_form_graph.storage import SqliteSaver
from edge_form_graph.graph import build_graph, initial_state
from edge_form_graph.application import build_application, application_state, active_command
from tests.test_graph import Model, snapshot, receipt
from tests.test_application import manifest, answer


class StorageTests(unittest.TestCase):
    def put_step(self, saver, config, number, values, parents=None):
        checkpoint = empty_checkpoint()
        checkpoint.update(id=f'{number:016d}', channel_values=values)
        config = {'configurable': {'checkpoint_ns': '', **config['configurable']}}
        return saver.put(config, checkpoint, {'step': number, 'parents': parents or {}}, {})

    def test_automatic_retention_bounds_unique_steps_and_reclaims_file_space(self):
        generator = random.Random(73)
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / 'growth.sqlite'
            config = {'configurable': {'thread_id': 'application'}}
            sizes = []
            for batch in range(20):
                with SqliteSaver.from_conn_string(str(path)) as saver:
                    for number in range(batch * 50, (batch + 1) * 50):
                        config = self.put_step(saver, config, number, {
                            'status': 'awaiting_save',
                            'snapshot': generator.randbytes(12000),
                            'profile': {'stable': '个人资料' * 10000}})
                        saver.put_writes(config, [('pending', {'operation': number})], 'writer')
                sizes.append(path.stat().st_size)
            with SqliteSaver.from_conn_string(str(path)) as saver:
                self.assertEqual(saver.conn.execute('SELECT COUNT(*) FROM checkpoints').fetchone()[0], 32)
                self.assertEqual(saver.conn.execute('SELECT COUNT(*) FROM writes').fetchone()[0], 32)
                latest = saver.get_tuple({'configurable': {'thread_id': 'application'}})
                self.assertEqual(latest.checkpoint['channel_values']['status'], 'awaiting_save')
                self.assertEqual(latest.pending_writes, [('writer', 'pending', {'operation': 999})])
            self.assertLess(max(sizes), 2500000)
            self.assertLess(sizes[-1], sizes[4] + 1500000)

    def test_save_relationship_and_reconciliation_survive_pruned_history(self):
        from edge_form_graph.application_cli import reconciliation_parent
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            path = folder / 'application.sqlite'
            with SqliteSaver.from_conn_string(str(path)) as saver:
                config = {'configurable': {'thread_id': 'application'}}
                config = self.put_step(saver, config, 0, {'command': {
                    'command_id': 'save-old', 'kind': 'save_scope', 'module_selector': '#education'}})
                config = self.put_step(saver, config, 1, {'command': {
                    'command_id': 'reconcile-old', 'kind': 'reconcile_save',
                    'original_command_id': 'save-old', 'module_selector': '#education'}})
                config = self.put_step(saver, config, 2, {'status': 'needs_reconciliation',
                    'command': {'command_id': 'reconcile-old', 'kind': 'reconcile_save',
                        'original_command_id': 'save-old'}, 'last_save_receipt': {'status': 'unknown'}})
                for number in range(3, 300):
                    config = self.put_step(saver, config, number, {'status': 'incomplete_coverage'})
            self.assertEqual(reconciliation_parent(folder, 'reconcile-old'), 'save-old')
            with SqliteSaver.from_conn_string(str(path)) as saver:
                self.assertIsNone(saver.get_tuple({'configurable': {'thread_id': 'application', 'checkpoint_id': f'{0:016d}'}}))
                self.assertEqual(saver.command_link('application', 'save-old')['module_selector'], '#education')
                self.assertEqual(saver.reconciliation_values('application')['last_save_receipt'], {'status': 'unknown'})

    def test_legacy_indexes_are_built_before_removing_old_command(self):
        with tempfile.TemporaryDirectory() as temp:
            path = str(Path(temp) / 'legacy.sqlite')
            with OldSaver.from_conn_string(path) as old:
                config = {'configurable': {'thread_id': 'application'}}
                config = self.put_step(old, config, 0, {'command': {
                    'command_id': 'reconcile', 'kind': 'reconcile_save', 'original_command_id': 'save'}})
                for number in range(1, 100):
                    config = self.put_step(old, config, number, {'status': 'awaiting_observation'})
            with SqliteSaver.from_conn_string(path) as saver:
                saver.compact(vacuum=True)
                self.assertEqual(saver.command_link('application', 'reconcile')['original_command_id'], 'save')

    def test_pruned_save_history_keeps_the_same_side_effect_barrier(self):
        from edge_form_graph.application_cli import check_prior
        cases = [(True, 'old', 'same', 'preflight_clear'),
                 (False, 'old', 'same', 'prior_reconciliation_required'),
                 (True, 'other', 'same', 'prior_reconciliation_required'),
                 (True, 'old', 'different', 'prior_reconciliation_required')]
        for settled, parent, target, expected in cases:
            with self.subTest(settled=settled, parent=parent, target=target), tempfile.TemporaryDirectory() as temp:
                root = Path(temp)
                writer = root / 'run' / 'writer'
                writer.mkdir(parents=True)
                old = {'command_id': 'old', 'kind': 'save_scope', 'started_at': 1,
                    'save_stage': 'observation_returned',
                    'receipt': {'settled': settled, 'status': 'unconfirmed', 'target': 'same'}}
                resolved = {'command_id': 'new', 'kind': 'reconcile_save', 'started_at': 2,
                    'receipt': {'settled': True, 'status': 'saved', 'target': target,
                                'evidence': {'save_confirmed': True}}}
                (writer / 'old.json').write_text(json.dumps(old), encoding='utf-8')
                (writer / 'new.json').write_text(json.dumps(resolved), encoding='utf-8')
                with SqliteSaver.from_conn_string(str(root / 'run' / 'application.sqlite')) as saver:
                    config = {'configurable': {'thread_id': 'application'}}
                    config = self.put_step(saver, config, 0, {'command': {
                        'command_id': 'old', 'kind': 'save_scope', 'module_selector': '#scope'}})
                    config = self.put_step(saver, config, 1, {'command': {
                        'command_id': 'new', 'kind': 'reconcile_save', 'original_command_id': parent}})
                    for number in range(2, 200):
                        config = self.put_step(saver, config, number, {'status': 'incomplete_coverage'})
                result = check_prior(root)
                self.assertEqual(result['status'], expected)
                if settled and expected == 'prior_reconciliation_required':
                    self.assertEqual(result['blockers'][0]['module_selector'], '#scope')

    def test_child_dependencies_retained_and_obsolete_namespaces_removed(self):
        with SqliteSaver.from_conn_string(':memory:') as saver:
            config = {'configurable': {'thread_id': 'application'}}
            for number in range(100):
                config = self.put_step(saver, config, number, {'status': 'awaiting_edge'})
                child = {'configurable': {'thread_id': 'application', 'checkpoint_ns': f'child:{number}'}}
                self.put_step(saver, child, 1000 + number, {'status': 'awaiting_edge'}, {'': f'{number:016d}'})
            saver.compact()
            self.assertIsNone(saver.get_tuple({'configurable': {'thread_id': 'application', 'checkpoint_ns': 'child:0'}}))
            self.assertIsNotNone(saver.get_tuple({'configurable': {'thread_id': 'application', 'checkpoint_ns': 'child:99'}}))
            self.assertEqual(saver.conn.execute('SELECT COUNT(*) FROM checkpoints').fetchone()[0], 64)

    def test_pending_nested_graph_resumes_after_aggressive_compaction(self):
        with tempfile.TemporaryDirectory() as temp:
            path = str(Path(temp) / 'nested.sqlite')
            config = {'configurable': {'thread_id': 'application'}, 'recursion_limit': 500}
            for iteration in range(30):
                with SqliteSaver.from_conn_string(path) as saver:
                    graph = build_application(Model(), saver)
                    if iteration == 0:
                        graph.invoke(application_state({'school': '示例大学'}, manifest(), allow_save=True), config)
                    original = graph.get_state(config, subgraphs=True)
                    command = active_command(original)
                    if not command:
                        self.assertEqual(original.values['status'], 'complete')
                        break
                    saver.compact(keep=2, vacuum=True)
                    restored = graph.get_state(config, subgraphs=True)
                    self.assertEqual(restored.values, original.values)
                    self.assertEqual(restored.next, original.next)
                    self.assertEqual(active_command(restored), command)
                    graph.invoke(Command(resume=answer(command)), config)
            else:
                self.fail('synthetic application failed to complete')

    def test_legacy_read_and_shared_exact_payload(self):
        with tempfile.TemporaryDirectory() as temp:
            path = str(Path(temp) / 'state.sqlite')
            with OldSaver.from_conn_string(path) as old:
                graph = build_graph(Model(), old)
                config = {'configurable': {'thread_id': 'module'}}
                graph.invoke(initial_state({'school': '示例大学'}, snapshot()), config)
            with SqliteSaver.from_conn_string(path) as saver:
                graph = build_graph(Model(), saver)
                state = graph.get_state(config)
                self.assertTrue(state.next)
                obj = {'history': [{'payload': '重复内容' * 5000}], 'pending': state.tasks}
                first = saver.serde.dumps_typed(obj)
                count = saver.conn.execute('SELECT COUNT(*) FROM state_chunks').fetchone()[0]
                second = saver.serde.dumps_typed(obj)
                self.assertEqual(first, second)
                self.assertEqual(count, saver.conn.execute('SELECT COUNT(*) FROM state_chunks').fetchone()[0])
                self.assertEqual(saver.serde.base.loads_typed(saver.serde.base.dumps_typed(obj)),
                                 saver.serde.loads_typed(first))
                graph.invoke(Command(resume=receipt(state.values['command'])), config)
                self.assertNotEqual(graph.get_state(config).values['status'], 'awaiting_edge')

    def test_parent_child_interrupt_survives_reopen(self):
        with tempfile.TemporaryDirectory() as temp:
            path = str(Path(temp) / 'application.sqlite')
            config = {'configurable': {'thread_id': 'application'}, 'recursion_limit': 500}
            with SqliteSaver.from_conn_string(path) as saver:
                graph = build_application(Model(), saver)
                graph.invoke(application_state({'school': '示例大学', 'large': 'x' * 20000}, manifest(), allow_save=True), config)
                original = graph.get_state(config, subgraphs=True)
                command = active_command(original)
                self.assertIsNotNone(command)
            with SqliteSaver.from_conn_string(path) as saver:
                graph = build_application(Model(), saver)
                restored = graph.get_state(config, subgraphs=True)
                self.assertEqual(active_command(restored), command)
                graph.invoke(Command(resume=answer(command)), config)
                self.assertIsNotNone(graph.get_state(config, subgraphs=True))

    def test_missing_chunk_is_an_error(self):
        with SqliteSaver.from_conn_string(':memory:') as saver:
            typed = saver.serde.dumps_typed({'large': 'x' * 20000})
            saver.conn.execute('DELETE FROM state_chunks')
            with self.assertRaisesRegex(ValueError, 'checkpoint_chunk_missing'):
                saver.serde.loads_typed(typed)


if __name__ == '__main__':
    unittest.main()
