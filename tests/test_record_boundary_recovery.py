import copy
import json
import tempfile
import time
import unittest
from pathlib import Path

from edge_form_graph.contracts import ContractError
from edge_form_graph.record_boundary_recovery import prepare_record_split


class RecordBoundaryRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        (self.folder / 'writer').mkdir()
        self.profile = {'education': [
            {'record_id': 'one', 'education_level': '本科', 'school_name': '甲大学', 'major': '计算机',
             'start_date': '2018-09', 'end_date': '2022-06', 'graduation_type': '毕业'},
            {'record_id': 'two', 'education_level': '本科', 'school_name': '乙大学', 'major': '数学',
             'start_date': '2019-09', 'end_date': '2023-06', 'graduation_type': '毕业'}]}
        self.target = {'browser': 'edge', 'browser_id': 'browser', 'tab_id': 'tab', 'url': 'https://example.test/form'}
        self.module = {'id': 'edu', 'selector': '#education', 'page_order': 0, 'save_scope': 'education',
            'module_label': '教育经历', 'mapping_context': {'record_collection': '/education', 'record_id': 'one', 'module_type': 'education'},
            'mapping_context_extra': {}, 'discovered_empty_collection': True}
        fields = [
            {'id': 'one-level', 'label': '学历', 'value': '本科', 'record_container': '#row-1'},
            {'id': 'one-school', 'label': '学校名称', 'value': '甲大学', 'record_container': '#row-1'},
            {'id': 'one-major', 'label': '专业', 'value': '计算机', 'record_container': '#row-1'},
            {'id': 'one-start', 'label': '入学时间', 'value': '2018-09', 'record_container': '#row-1'},
            {'id': 'one-end', 'label': '毕业时间', 'value': '2022-06', 'record_container': '#row-1'},
            {'id': 'two-level', 'label': '学历', 'value': '本科', 'record_container': '#row-2'},
            {'id': 'two-school', 'label': '学校名称', 'value': '乙大学', 'record_container': '#row-2'},
            {'id': 'two-major', 'label': '专业', 'value': '数学', 'record_container': '#row-2'},
            {'id': 'two-start', 'label': '入学时间', 'value': '2018-09', 'record_container': '#row-2'},
            {'id': 'two-end', 'label': '毕业时间', 'value': '2022-06', 'record_container': '#row-2'},
        ]
        self.rows = [
            {'selector': '#row-1', 'snapshot': {'fields': copy.deepcopy(fields[:5])}},
            {'selector': '#row-2', 'snapshot': {'fields': copy.deepcopy(fields[5:])}},
        ]
        self.inventory = {'url': self.target['url'], 'observed_at': time.time(), 'framework': {
            'family': 'sf', 'sections': [{'selector': '#education', 'records': self.rows,
                'snapshot': {'fields': fields}}]}}
        self.manifest = {'target': self.target, 'program_inventory': True, 'discovery_evidence': 'test',
            'modules': [self.module], 'save_scopes': [{'id': 'education', 'selector': '#education',
                'mode': 'explicit', 'evidence': 'test'}]}
        self.values = {'manifest': self.manifest, 'profile': self.profile, 'scopes': {}, 'results': {},
            'status': 'incomplete_coverage', 'command': None, 'deadline': time.time() + 300,
            'inventory_history': [], 'budget_history': [], 'allow_save': False}
        self._journal('observe-blank', 1, 'observe', self._snapshot('two-start', ''), [])
        self._journal('fill-start', 2, 'fill', self._snapshot('two-start', '2018-09'),
            [{'id': 'two-start', 'status': 'written'}], status='partial')
        self._journal('observe-end-blank', 3, 'observe', self._snapshot('two-end', ''), [])
        self._journal('fill-end', 4, 'fill', self._snapshot('two-end', '2022-06'),
            [{'id': 'two-end', 'status': 'written'}], status='partial')
        self._journal('observe-start-after', 5, 'observe', self._snapshot('two-start', '2018-09'), [])
        self._journal('observe-end-after', 6, 'observe', self._snapshot('two-end', '2022-06'), [])

    def tearDown(self):
        self.temp.cleanup()

    def _snapshot(self, field_id, value):
        return {'module_id': 'edu', 'module_selector': '#education',
            'fields': [{'id': field_id, 'value': value}]}

    def _journal(self, command_id, started, kind, snapshot, results, *, status='completed', settled=True):
        doc = {'command_id': command_id, 'kind': kind, 'started_at': started,
            'receipt': {'settled': settled, 'status': status, 'kind': kind, 'snapshot': snapshot,
                        'results': results}}
        (self.folder / 'writer' / f'{command_id}.json').write_text(json.dumps(doc), encoding='utf-8')

    def run_split(self):
        return prepare_record_split(self.values, (), self.folder, self.inventory, 'edu', 'split duplicate row',
                                    writer_lock_held=True)

    def test_projects_only_linked_row_dates_and_preserves_journals(self):
        before = {p.name: p.read_bytes() for p in (self.folder / 'writer').glob('*.json')}
        result = self.run_split()
        self.assertEqual([m['mapping_context']['record_id'] for m in result['manifest']['modules']], ['one', 'two'])
        proofs = result['inventory_history'][-1]['program_date_proofs']
        self.assertEqual({p['field_id'] for p in proofs}, {'two-start', 'two-end'})
        self.assertEqual(before, {p.name: p.read_bytes() for p in (self.folder / 'writer').glob('*.json')})

    def test_third_value_seen_after_program_fill_is_rejected(self):
        self._journal('observe-third', 7, 'observe', self._snapshot('two-start', '2017-01'), [])
        with self.assertRaisesRegex(ContractError, 'date_observation_conflict'):
            self.run_split()

    def test_unknown_receipt_cannot_hide_a_third_observed_value(self):
        self._journal('observe-third', 7, 'observe', self._snapshot('two-start', '2017-01'), [], status='unknown')
        with self.assertRaisesRegex(ContractError, 'date_observation_conflict'):
            self.run_split()

    def test_unsettled_fill_cannot_prove_date(self):
        self._journal('fill-start', 2, 'fill', self._snapshot('two-start', '2018-09'),
                      [{'id': 'two-start', 'status': 'written'}], settled=False, status='unknown')
        with self.assertRaises(ContractError):
            self.run_split()

    def test_settled_fill_linkage_snapshot_can_prove_initial_blank(self):
        self._journal('observe-blank', 1, 'fill', self._snapshot('two-start', ''),
                      [{'id': 'two-start', 'status': 'unattempted'}], status='partial')
        result = self.run_split()
        proof = next(p for p in result['inventory_history'][-1]['program_date_proofs']
                     if p['field_id'] == 'two-start')
        self.assertEqual(proof['blank_observation_command'], 'observe-blank')

    def test_ambiguous_identity_is_rejected(self):
        self.profile['education'][1]['school_name'] = '甲大学'
        self.profile['education'][1]['major'] = '计算机'
        with self.assertRaisesRegex(ContractError, 'identity_unresolved'):
            self.run_split()


if __name__ == '__main__':
    unittest.main()
