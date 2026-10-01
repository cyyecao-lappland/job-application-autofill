import copy
import json
import tempfile
import time
import unittest
from pathlib import Path

from edge_form_graph.contracts import ContractError
from edge_form_graph.profile_file_normalization import normalize_profile_files


class ProfileFileNormalizationTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.base = self.root / 'career'
        self.base.mkdir()
        (self.base / '本科成绩单.pdf').write_bytes(b'profile source')
        (self.base / '另一份成绩单.pdf').write_bytes(b'other source')
        (self.root / 'outside.pdf').write_bytes(b'outside')
        (self.root / 'run' / 'writer').mkdir(parents=True)
        self.folder = self.root / 'run'
        self.profile = {'education': [{'record_id': 'edu-1',
            'transcript': {'path': '本科成绩单.pdf', 'display_name': '成绩单'},
            'transcript_usage_policy': {'path': '本科成绩单.pdf', 'allow_upload': True}}]}
        self.values = {'profile': self.profile, 'manifest': {'program_inventory': True},
            'status': 'incomplete_coverage', 'command': None, 'inventory_history': [],
            'results': {'kept': {'status': 'filled'}}, 'deadline': time.time() + 60,
            'mapping_cache': {'old': 'mapping'}, 'scope_reviews': {'old': 'review'}}
        self.input_bytes = (self.base / '本科成绩单.pdf').read_bytes()

    def tearDown(self):
        self.temp.cleanup()

    def run_normalize(self, values=None, next_nodes=(), **kwargs):
        return normalize_profile_files(values or self.values, next_nodes, self.folder, self.base,
            'transcript paths from supplied profile', writer_lock_held=True, **kwargs)

    def test_resolves_only_supplied_paths_and_preserves_run_evidence(self):
        before = copy.deepcopy(self.values['profile'])
        updates = self.run_normalize()
        absolute = str((self.base / '本科成绩单.pdf').resolve())
        record = updates['profile']['education'][0]
        self.assertEqual(record['transcript']['path'], absolute)
        self.assertEqual(record['transcript_usage_policy']['path'], absolute)
        self.assertEqual(record['transcript']['display_name'], '成绩单')
        self.assertEqual(updates['inventory_history'][-1]['basis'], 'transcript paths from supplied profile')
        self.assertEqual(self.values['profile'], before)
        self.assertEqual(self.values['results'], {'kept': {'status': 'filled'}})
        self.assertEqual(updates['mapping_cache'], {})
        self.assertEqual(updates['scope_reviews'], {})
        self.assertEqual((self.base / '本科成绩单.pdf').read_bytes(), self.input_bytes)
        self.assertFalse(list(self.folder.joinpath('writer').glob('*.json')))

    def test_preserves_valid_absolute_path(self):
        absolute = str((self.base / '本科成绩单.pdf').resolve())
        self.values['profile']['education'][0]['transcript']['path'] = absolute
        updates = self.run_normalize()
        self.assertEqual(updates['profile']['education'][0]['transcript']['path'], absolute)
        event = updates['inventory_history'][-1]['paths'][0]
        self.assertEqual((event['before'], event['after']), (absolute, absolute))

    def test_allows_an_undispatched_ordinary_observe(self):
        values = copy.deepcopy(self.values)
        values.update(status='awaiting_observation', command={'kind': 'observe', 'command_id': 'observe-1'})
        updates = self.run_normalize(values, ('observe',))
        self.assertEqual(updates['inventory_history'][-1]['superseded_undispatched_observe'], values['command'])

    def test_rejects_a_dispatched_observe(self):
        values = copy.deepcopy(self.values)
        values.update(status='awaiting_observation', command={'kind': 'observe', 'command_id': 'observe-1'})
        (self.folder / 'writer' / 'observe-1.json').write_text('{}', encoding='utf-8')
        with self.assertRaisesRegex(ContractError, 'quiescent_state'):
            self.run_normalize(values, ('observe',))

    def test_requires_writer_lock_and_basis(self):
        with self.assertRaisesRegex(ContractError, 'writer_lock_and_quiescent_state'):
            normalize_profile_files(self.values, (), self.folder, self.base, 'basis', writer_lock_held=False)
        with self.assertRaisesRegex(ContractError, 'writer_lock_and_quiescent_state'):
            normalize_profile_files(self.values, (), self.folder, self.base, ' ', writer_lock_held=True)

    def test_rejects_path_escape_and_missing_file(self):
        self.values['profile']['education'][0]['transcript']['path'] = '../outside.pdf'
        with self.assertRaisesRegex(ContractError, 'outside_base'):
            self.run_normalize()
        self.values['profile']['education'][0]['transcript']['path'] = 'not-found.pdf'
        with self.assertRaisesRegex(ContractError, 'file_missing'):
            self.run_normalize()

    def test_rejects_disagreeing_transcript_paths(self):
        self.values['profile']['education'][0]['transcript_usage_policy']['path'] = '另一份成绩单.pdf'
        with self.assertRaisesRegex(ContractError, 'paths_conflict'):
            self.run_normalize()

    def test_rejects_unsettled_writer_history(self):
        journal = {'command_id': 'pending', 'kind': 'fill', 'receipt': {'settled': False, 'status': 'unknown'}}
        (self.folder / 'writer' / 'pending.json').write_text(json.dumps(journal), encoding='utf-8')
        with self.assertRaisesRegex(ContractError, 'settled_calls'):
            self.run_normalize()


if __name__ == '__main__':
    unittest.main()
