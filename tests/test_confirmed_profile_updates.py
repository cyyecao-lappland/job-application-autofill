import copy
import json
import tempfile
import unittest
from pathlib import Path

from edge_form_graph.confirmed_profile_updates import apply_confirmed_facts, sync_confirmed_facts
from edge_form_graph.contracts import ContractError


class ConfirmedProfileUpdateTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.folder = Path(self.temp.name)
        (self.folder / 'writer').mkdir()
        self.profile = {'education': [
            {'record_id': 'master', 'rank_position': None, 'rank_band': '前30%', 'transcript': {'path': 'normalized.pdf'}},
            {'record_id': 'bachelor', 'transfer_history': {'from_record_id': 'materials'}}],
            'company_answers': [], 'personal_answers': {}, 'field_metadata': {}}
        self.patch = {'version': 1, 'basis': 'explicit user answer', 'updates': [
            {'root': 'education', 'record_id': 'bachelor', 'values': {'is_top_up': False}},
            {'root': 'company_answers', 'record_id': 'company-shopee', 'create_record': True,
             'values': {'company': 'Shopee', 'campus_ambassador': False}}]}
        self.values = {'profile': self.profile, 'status': 'incomplete_coverage', 'command': None,
                       'manifest': {'modules': [{'id': 'm', 'save_scope': 's'}]},
                       'results': {}, 'scopes': {}, 'scope_reviews': {'s': {'approved': True}},
                       'mapping_cache': {'m': 'old'}, 'inventory_history': []}

    def tearDown(self):
        self.temp.cleanup()

    def sync(self, values=None, nodes=()):
        return sync_confirmed_facts(values or self.values, nodes, self.folder, self.patch,
                                    writer_lock_held=True)

    def test_record_identity_preserves_normalized_files_and_transfer_history(self):
        before = copy.deepcopy(self.profile)
        after, changes = apply_confirmed_facts(self.profile, self.patch)
        self.assertEqual(self.profile, before)
        self.assertEqual(after['education'][0], before['education'][0])
        self.assertFalse(after['education'][1]['is_top_up'])
        self.assertEqual(after['education'][1]['transfer_history'], before['education'][1]['transfer_history'])
        self.assertFalse(after['company_answers'][0]['campus_ambassador'])
        again, repeated = apply_confirmed_facts(after, self.patch)
        self.assertEqual(again, after)
        self.assertEqual(repeated, [])
        self.assertTrue(changes)

    def test_duplicate_or_missing_record_is_rejected(self):
        for records in ([], [{'record_id': 'bachelor'}, {'record_id': 'bachelor'}]):
            profile = copy.deepcopy(self.profile)
            profile['education'] = records
            with self.assertRaises(ContractError):
                apply_confirmed_facts(profile, self.patch)

    def test_attachment_sync_requires_an_existing_source_file_and_preserves_record_identity(self):
        path = self.folder / 'resume.pdf'
        path.write_bytes(b'test attachment')
        profile = {**self.profile, 'attachments': []}
        patch = {'version': 1, 'basis': 'existing observed resume file', 'updates': [
            {'root': 'attachments', 'record_id': 'resume', 'create_record': True,
             'values': {'path': str(path), 'source': 'original upload evidence', 'value_status': 'source_backed'}}]}
        after, _ = apply_confirmed_facts(profile, patch)
        from edge_form_graph.contracts import json_value
        self.assertEqual(json_value(after, 'record_id:resume/path'), str(path))
        again, changes = apply_confirmed_facts(after, patch)
        self.assertEqual(again, after)
        self.assertEqual(changes, [])
        path.unlink()
        with self.assertRaisesRegex(ContractError, 'source_file_required'):
            apply_confirmed_facts(profile, patch)

    def test_saved_reviews_preserved_and_unsaved_reviews_invalidated(self):
        self.values['scopes']['saved'] = 'saved_confirmed'
        self.values['scope_reviews']['saved'] = {'proof': 'kept'}
        updates = self.sync()
        self.assertEqual(updates['scope_reviews'], {'saved': {'proof': 'kept'}})
        self.assertEqual(updates['mapping_cache'], {})
        self.assertEqual(updates['inventory_history'][-1]['basis'], self.patch['basis'])
        self.assertFalse(list((self.folder / 'writer').glob('*.json')))

    def test_module_review_is_invalidated_but_saved_evidence_is_preserved(self):
        self.values['manifest']['modules'].append({'id': 'saved-module', 'save_scope': 'saved'})
        self.values['scopes']['saved'] = 'saved_confirmed'
        result = {'status': 'verified_draft', 'review': {'approved': True}, 'reviewed_revision': 7,
                  'results': {'f': {'status': 'written'}}, 'last_receipt': {'settled': True}}
        self.values['results'] = {'m': copy.deepcopy(result), 'saved-module': copy.deepcopy(result)}
        updates = self.sync()
        self.assertEqual(updates['results']['saved-module'], result)
        self.assertEqual(updates['results']['m']['review'], {})
        self.assertIsNone(updates['results']['m']['reviewed_revision'])
        self.assertEqual(updates['results']['m']['last_receipt'], result['last_receipt'])
        self.assertEqual(updates['results']['m']['results'], result['results'])

    def test_only_undispatched_ordinary_observe_is_allowed(self):
        self.values.update(status='awaiting_observation', command={'kind': 'observe', 'command_id': 'obs'})
        self.sync(nodes=('observe',))
        (self.folder / 'writer' / 'obs.json').write_text('{}')
        with self.assertRaisesRegex(ContractError, 'quiescent_writer'):
            self.sync(nodes=('observe',))
        self.values['command']['kind'] = 'save_scope'
        with self.assertRaisesRegex(ContractError, 'quiescent_writer'):
            self.sync(nodes=('observe',))

    def test_unsettled_history_is_not_erased(self):
        journal = {'kind': 'fill', 'command_id': 'old', 'receipt': {'settled': False}}
        (self.folder / 'writer' / 'old.json').write_text(json.dumps(journal))
        with self.assertRaisesRegex(ContractError, 'settled_calls'):
            self.sync()

    def test_ended_observation_allows_new_facts_before_consuming_the_same_receipt(self):
        target={'tab_id':'one'}
        command={'kind':'observe','command_id':'read','target':target,'module_id':'m','module_selector':'#m'}
        self.values.update(status='awaiting_observation',command=command)
        receipt={**command,'settled':True,'status':'completed','snapshot':{
            'target':target,'module_id':'m','module_selector':'#m'}}
        path=self.folder/'writer'/'read.json'
        journal={'kind':'observe','command_id':'read','receipt':receipt}
        path.write_text(json.dumps(journal))
        self.sync(nodes=('observe',))
        self.assertEqual(json.loads(path.read_text()),journal)
        receipt['snapshot']['target']={'tab_id':'wrong'}
        path.write_text(json.dumps(journal))
        with self.assertRaisesRegex(ContractError,'quiescent_writer'):
            self.sync(nodes=('observe',))

    def test_interrupted_pure_review_boundary_accepts_sources_but_save_node_does_not(self):
        self.values.update(status='module_filled',command=None)
        self.sync(nodes=('review_boundary',))
        with self.assertRaisesRegex(ContractError,'quiescent_writer'):
            self.sync(nodes=('prepare_save',))

    def test_new_fact_cannot_change_unreconciled_command_source(self):
        self.values['results']['m'] = {'command': {'operations': [
            {'id': 'field', 'source': 'record_id:bachelor/is_top_up'}]},
            'last_receipt': {'results': [{'id': 'field', 'status': 'unknown'}]}}
        with self.assertRaisesRegex(ContractError, 'prior_write_reconciliation'):
            self.sync()

    def test_saved_source_change_requires_scope_revision(self):
        self.values['scopes']['s'] = 'saved_confirmed'
        self.values['results']['m'] = {'proposal': {'mappings': [
            {'field_id': 'field', 'source': 'record_id:bachelor/is_top_up'}]}}
        with self.assertRaisesRegex(ContractError, 'saved_scope_revision'):
            self.sync()

    def test_company_record_source_protects_saved_and_unknown_writes(self):
        self.profile['company_answers'] = [{'record_id': 'company-shopee', 'campus_ambassador': True}]
        source = 'record_id:company-shopee/campus_ambassador'
        self.values['results']['m'] = {'command': {'operations': [{'id': 'field', 'source': source}]},
            'last_receipt': {'results': [{'id': 'field', 'status': 'unknown'}]}}
        with self.assertRaisesRegex(ContractError, 'prior_write_reconciliation'):
            self.sync()
        self.values['results']['m'] = {'proposal': {'mappings': [{'field_id': 'field', 'source': source}]}}
        self.values['scopes']['s'] = 'saved_confirmed'
        with self.assertRaisesRegex(ContractError, 'saved_scope_revision'):
            self.sync()
