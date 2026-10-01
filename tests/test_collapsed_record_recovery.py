import copy
import json
import tempfile
import time
import unittest
from pathlib import Path

from edge_form_graph.collapsed_record_recovery import rebind_collapsed_education
from edge_form_graph.contracts import ContractError
from edge_form_graph.page_planner import row_match


class CollapsedRecordRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name); (self.folder/'writer').mkdir()
        self.record = {'record_id': 'one', 'school_name': '甲大学', 'major': '计算机',
                       'education_level': '本科', 'start_date': '2021-03', 'end_date': '2024-06'}
        self.binding = {'record_collection': '/education', 'record_id': 'one', 'module_type': 'education'}
        self.target = {'browser': 'edge', 'browser_id': 'browser', 'tab_id': 'tab', 'url': 'https://example.test/form'}
        self.values = {'status': 'observation_unconfirmed', 'command': None, 'profile': {'education': [self.record]},
            'manifest': {'target': self.target, 'program_inventory': True, 'discovery_evidence': 'live',
                'modules': [{'id': 'edu', 'selector': '#obsolete', 'page_order': 0, 'save_scope': 'education',
                             'module_label': '教育信息', 'mapping_context': self.binding}],
                'save_scopes': [{'id': 'education', 'selector': '#education', 'mode': 'explicit', 'evidence': 'live'}]},
            'results': {'edu': {'status': 'record_boundary_changed', 'results': {'date': {'status': 'written'}}}},
            'scopes': {}, 'deadline': time.time()+300}
        self.row = {'selector': '#card', 'state': 'collapsed', 'edit_selector': '#edit',
            'card_values': {'学校全称': '甲大学', '学历': '大学本科', '专业名称': '计算机',
                            '入学时间': '2019-09', '毕业时间': '2024-07'}, 'snapshot': {'fields': []}}
        self.inventory = {'target': self.target, 'url': self.target['url'], 'observed_at': time.time(),
            'framework': {'family': 'sf', 'sections': [{'selector': '#education', 'label': '教育信息', 'records': [self.row]}]}}

    def run_rebind(self):
        return rebind_collapsed_education(self.values, (), self.folder, self.inventory, 'education',
                                         'repair stale card scope', writer_lock_held=True)

    def test_existing_identity_allows_date_correction_only_after_fresh_observation(self):
        original = copy.deepcopy(self.values)
        result = self.run_rebind()
        module = result['manifest']['modules'][0]
        self.assertEqual(module['selector'], '#card')
        self.assertEqual(module['mapping_context'], self.binding)
        self.assertTrue(module['existing_card_identity'])
        self.assertNotIn('open_mode', module)
        self.assertEqual(result['results'], {})
        self.assertEqual(result['inventory_history'][0]['originals'][0]['result'], original['results']['edu'])
        self.assertEqual(self.values, original)

    def test_unbound_card_with_wrong_dates_is_never_a_blank_editor(self):
        state, _ = row_match(self.row, [(self.binding, self.record)], 'education')
        self.assertEqual(state, 'ambiguous')

    def test_different_major_or_duplicate_identity_is_rejected(self):
        self.row['card_values']['专业名称'] = '材料'
        with self.assertRaisesRegex(ContractError, 'identity_not_unique'): self.run_rebind()
        self.row['card_values']['专业名称'] = '计算机'
        self.values['profile']['education'].append({**self.record, 'record_id': 'duplicate'})
        with self.assertRaisesRegex(ContractError, 'identity_not_unique'): self.run_rebind()

    def test_changed_tab_stale_inventory_and_saved_scope_are_rejected(self):
        self.inventory['target'] = {**self.target, 'tab_id': 'other'}
        with self.assertRaises(ContractError): self.run_rebind()
        self.inventory['target'] = self.target; self.inventory['observed_at'] = time.time()-150
        with self.assertRaises(ContractError): self.run_rebind()
        self.inventory['observed_at'] = time.time(); self.values['scopes']['education'] = 'saved_confirmed'
        with self.assertRaises(ContractError): self.run_rebind()

    def test_unknown_writes_or_pending_journal_are_not_cleared(self):
        self.values['results']['edu']['results']['date']['status'] = 'unknown'
        with self.assertRaises(ContractError): self.run_rebind()
        self.values['results']['edu']['results']['date']['status'] = 'written'
        path = self.folder/'writer/pending.json'; path.write_text(json.dumps({'kind': 'fill', 'receipt': {'settled': False}}))
        with self.assertRaises(ContractError): self.run_rebind()
        self.assertTrue(path.exists())

    def test_unresolved_settled_save_is_not_bypassed(self):
        path = self.folder/'writer/save.json'; path.write_text(json.dumps({'kind': 'save_scope',
            'receipt': {'settled': True, 'status': 'unconfirmed', 'evidence': {}}}))
        with self.assertRaisesRegex(ContractError, 'save_reconciliation'): self.run_rebind()

    def test_boundary_change_does_not_hide_unknown_or_conflicting_field_receipt(self):
        for status in ('unknown', 'conflict'):
            self.values['results']['edu']['last_receipt'] = {'settled': True, 'status': 'partial',
                'results': [{'id': 'date', 'status': status}, {'id': 'other', 'status': 'record_boundary_changed'}]}
            with self.assertRaisesRegex(ContractError, 'resolved_education_writes'): self.run_rebind()
        self.values['results']['edu']['last_receipt'] = {'settled': True, 'status': 'unconfirmed', 'results': []}
        with self.assertRaisesRegex(ContractError, 'resolved_education_writes'): self.run_rebind()

    def test_only_exact_structural_no_action_journal_resolves_boundary_rejection(self):
        last = {'command_id': 'old', 'kind': 'fill', 'settled': True, 'status': 'partial',
                'results': [{'id': 'date', 'status': 'conflict', 'reason': 'record_boundary_changed'}]}
        self.values['results']['edu']['last_receipt'] = last
        journal = {'kind': 'fill', 'command_id': 'old', 'action_issued': False, 'pending_field': None,
            'receipt': last, 'instrumentation': [{'method': 'evaluate', 'stage': 'module_readback', 'status': 'returned'}]}
        path = self.folder/'writer/old.json'; path.write_text(json.dumps(journal))
        self.run_rebind()
        journal['instrumentation'][0]['method'] = 'click'; path.write_text(json.dumps(journal))
        with self.assertRaises(ContractError): self.run_rebind()
        journal['instrumentation'][0]['method'] = 'evaluate'; journal['action_issued'] = True
        path.write_text(json.dumps(journal))
        with self.assertRaises(ContractError): self.run_rebind()
