import copy
import json
import tempfile
import time
import unittest
from pathlib import Path

from edge_form_graph.contracts import ContractError
from edge_form_graph.saved_scope_revision import reopen_saved_scope


class SavedScopeRevisionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        (self.folder / 'writer').mkdir()
        self.target = {'browser': 'edge', 'browser_id': 'b', 'tab_id': 't', 'url': 'https://example.test'}
        self.modules = [
            {'id': 'saved-a', 'selector': '#saved-a', 'page_order': 0, 'save_scope': 'saved'},
            {'id': 'saved-b', 'selector': '#saved-b', 'page_order': 1, 'save_scope': 'saved'},
            {'id': 'other', 'selector': '#other', 'page_order': 2, 'save_scope': 'other'},
        ]
        self.saved_current = {m['id']: self._snapshot(m) for m in self.modules[:2]}
        self.values = {'status': 'incomplete_coverage', 'index': 3, 'command': None, 'deadline': 1,
            'manifest': {'program_inventory': True, 'target': self.target, 'modules': copy.deepcopy(self.modules),
                'save_scopes': [{'id': 'saved', 'selector': '#saved-form', 'mode': 'explicit',
                    'capture': {'saveControl': '#save', 'savedSignal': {'selector': '#edit'}}},
                    {'id': 'other', 'selector': '#other-form', 'mode': 'explicit', 'capture': {'saveControl': '#other-save'}}]},
            'scopes': {'saved': 'saved_confirmed', 'other': 'saved_confirmed'},
            'results': {m['id']: self._result(m, self.saved_current[m['id']]) for m in self.modules[:2]},
            'mapping_cache': {'saved-a': {'cached': True}, 'other': {'cached': 'keep'}},
            'scope_reviews': {'saved': {'saved-a': {'status': 'approved'}}, 'other': {'other': {'status': 'approved'}}},
            'last_save_receipt': {'module_id': 'other', 'status': 'saved'}, 'inventory_history': [],
            'revisit_history': []}
        self.values['results']['other'] = self._result(self.modules[2], self._snapshot(self.modules[2]))
        self.receipt = self._save_receipt(self.modules[:2], self.saved_current)
        journal = {'command_id': self.receipt['command_id'], 'kind': 'save_scope', 'started_at': 100,
            'receipt': copy.deepcopy(self.receipt)}
        (self.folder / 'writer' / (journal['command_id'] + '.json')).write_text(json.dumps(journal), encoding='utf-8')

    def _snapshot(self, module, value='unchanged'):
        return {'module_id': module['id'], 'module_selector': module['selector'], 'target': self.target,
            'observed_at': 200, 'fields': [{'id': module['id'] + '-field', 'label': '专业技能证书',
                'kind': 'text', 'selector': '#' + module['id'] + '-field', 'value': value,
                'signature': {'label': '专业技能证书'}, 'protected': False}]}

    def _result(self, module, current):
        return {'snapshot': copy.deepcopy(current), 'current': copy.deepcopy(current), 'operations': [],
            'results': {current['fields'][0]['id']: {'status': 'already_matched'}}, 'revision': 1}

    def _save_receipt(self, modules, snapshots):
        return {'command_id': 'save-confirmed', 'kind': 'save_scope',
            'target': self.target, 'settled': True, 'status': 'saved',
            'snapshot': {'module_id': 'saved', 'module_selector': '#saved-form', 'target': self.target,
                'fields': [], 'capture': {'savedSignal': {'selector': '#saved-form'}}},
            'evidence': {'save_confirmed': True, 'save_observed_at': 150,
                'saved_modules': [copy.deepcopy(snapshots[m['id']]) for m in modules]}}

    def reopen(self, values=None, next_nodes=(), module_id='saved-a', *, writer_lock_held=True):
        return reopen_saved_scope(values or self.values, next_nodes, self.folder, module_id,
            'correct program supplied the wrong certificate source mapping', writer_lock_held=writer_lock_held)

    def test_reopens_only_the_target_scope_and_preserves_prior_evidence(self):
        before = copy.deepcopy(self.values)
        journal_path = self.folder / 'writer' / 'save-confirmed.json'
        journal_bytes = journal_path.read_bytes()
        updates = self.reopen()
        self.assertEqual(self.values, before)
        self.assertEqual(updates['index'], 0)
        self.assertEqual(updates['status'], 'advance')
        self.assertNotIn('saved-a', updates['results'])
        self.assertNotIn('saved-b', updates['results'])
        self.assertEqual(updates['results']['other'], before['results']['other'])
        self.assertEqual(updates['scopes'], {'other': 'saved_confirmed'})
        self.assertEqual(updates['scope_reviews'], {'other': before['scope_reviews']['other']})
        self.assertEqual(updates['mapping_cache'], {'other': before['mapping_cache']['other']})
        history = updates['revisit_history'][-1]
        self.assertEqual(history['basis'], 'correct program supplied the wrong certificate source mapping')
        self.assertEqual(history['previous_results'], {'saved-a': before['results']['saved-a'],
            'saved-b': before['results']['saved-b']})
        self.assertEqual(history['previous_scopes'], before['scopes'])
        self.assertEqual(history['save_receipt'], self.receipt)
        self.assertEqual(updates['last_save_receipt'], before['last_save_receipt'])
        self.assertEqual(updates['manifest'], before['manifest'])
        self.assertEqual(journal_path.read_bytes(), journal_bytes)
        self.assertIsNone(updates['command'])

    def test_undispatched_ordinary_observe_can_be_superseded_under_lock(self):
        values = copy.deepcopy(self.values)
        values.update(status='awaiting_observation', index=2,
            command={'kind': 'observe', 'command_id': 'pending-read', 'module_id': 'other',
                     'module_selector': '#other', 'capture': {'controlDiagnostics': True}})
        updates = self.reopen(values, ('observe',))
        self.assertEqual(updates['revisit_history'][-1]['superseded_undispatched_observe'], values['command'])
        (self.folder / 'writer' / 'pending-read.json').write_text('{}', encoding='utf-8')
        with self.assertRaisesRegex(ContractError, 'quiescent_boundary'):
            self.reopen(values, ('observe',))

    def test_allows_ordinary_observe_and_requires_writer_lock(self):
        values = copy.deepcopy(self.values)
        values.update(status='awaiting_observation', index=2,
            command={'kind': 'observe', 'command_id': 'diagnostic', 'module_id': 'other',
                     'module_selector': '#other', 'capture': {'controlDiagnostics': True}})
        self.assertEqual(self.reopen(values, ('observe',))['index'], 0)
        with self.assertRaisesRegex(ContractError, 'program_inventory_lock_and_basis'):
            self.reopen(writer_lock_held=False)

    def test_requires_saved_scope_and_unique_module(self):
        self.values['scopes']['saved'] = 'draft'
        with self.assertRaisesRegex(ContractError, 'confirmed_scope'):
            self.reopen()
        self.values['scopes']['saved'] = 'saved_confirmed'
        with self.assertRaisesRegex(ContractError, 'module_not_unique'):
            self.reopen(module_id='absent')

    def test_requires_complete_matching_saved_readback_for_every_scope_module(self):
        self.receipt['evidence']['saved_modules'].pop()
        journal_path = self.folder / 'writer' / 'save-confirmed.json'
        journal = json.loads(journal_path.read_text(encoding='utf-8'))
        journal['receipt'] = copy.deepcopy(self.receipt)
        journal_path.write_text(json.dumps(journal), encoding='utf-8')
        with self.assertRaisesRegex(ContractError, 'success_proof_missing_or_ambiguous'):
            self.reopen()

    def test_accepts_module_card_persisted_fields_readback(self):
        journal_path = self.folder / 'writer' / 'save-confirmed.json'
        journal = json.loads(journal_path.read_text(encoding='utf-8'))
        for post in journal['receipt']['evidence']['saved_modules']:
            post['persisted_fields'] = copy.deepcopy(post['fields'])
            post['fields'] = []
            post['persistence_evidence'] = {'kind': 'module_card_all_values_visible', 'selector': post['module_selector']}
        journal_path.write_text(json.dumps(journal), encoding='utf-8')
        updates = self.reopen()
        self.assertEqual(updates['index'], 0)

    def test_rejects_changed_saved_value_or_incomplete_module_result(self):
        self.receipt['evidence']['saved_modules'][0]['fields'][0]['value'] = 'different'
        journal_path = self.folder / 'writer' / 'save-confirmed.json'
        journal = json.loads(journal_path.read_text(encoding='utf-8'))
        journal['receipt'] = copy.deepcopy(self.receipt)
        journal_path.write_text(json.dumps(journal), encoding='utf-8')
        with self.assertRaisesRegex(ContractError, 'success_proof_missing_or_ambiguous'):
            self.reopen()
        self.receipt['evidence']['saved_modules'][0]['fields'][0]['value'] = 'unchanged'
        self.values['results']['saved-b']['results']['saved-b-field']['status'] = 'unknown'
        with self.assertRaisesRegex(ContractError, 'success_proof_missing_or_ambiguous'):
            self.reopen()

    def test_unsettled_writer_history_blocks_reopen(self):
        (self.folder / 'writer' / 'pending-fill.json').write_text(json.dumps({'command_id': 'pending',
            'kind': 'fill', 'receipt': {'settled': False, 'status': 'unknown'}}), encoding='utf-8')
        with self.assertRaisesRegex(ContractError, 'settled_calls'):
            self.reopen()


if __name__ == '__main__':
    unittest.main()
