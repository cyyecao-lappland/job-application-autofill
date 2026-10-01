import copy
import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from edge_form_graph.application_cli import completed_semantic_error_receipt


class LocalSemanticErrorResumeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.folder = Path(self.tmp.name)
        (self.folder / 'writer').mkdir()
        target = {'browser': 'edge', 'tab_id': 'same-tab'}
        self.receipt = {'command_id': 'finished-fill', 'kind': 'fill', 'target': target,
                        'settled': True, 'status': 'partial',
                        'results': [{'id': 'date', 'status': 'deferred'}],
                        'snapshot': {'module_id': 'personal', 'module_selector': '#personal',
                                     'fields': [{'id': 'fresh', 'value': '2002-02 (24岁)'}]}}
        error = "ContractError('duplicate_or_unknown_field')"
        self.child = SimpleNamespace(values={'command': None, 'snapshot': {
            'mapping_context': {'module_type': 'personal'}}, 'last_receipt': copy.deepcopy(self.receipt)},
            next=('resolve_unknown',), tasks=(SimpleNamespace(error=error),))
        self.child.values['last_receipt']['snapshot']['mapping_context'] = {'module_type': 'personal'}
        self.state = SimpleNamespace(values={'command': None, 'index': 0, 'manifest': {
            'target': target, 'modules': [{'id': 'personal', 'selector': '#personal'}]}},
            next=('fill_and_review',), tasks=(SimpleNamespace(name='fill_and_review', error=error, state=self.child),))
        self.write_journal()

    def write_journal(self, **updates):
        data = {'receipt': self.receipt, 'pending_field': None, **updates}
        (self.folder / 'writer' / 'finished-fill.json').write_text(json.dumps(data), encoding='utf8')

    def test_consumed_receipt_accepts_only_matching_original_values_and_bound_context(self):
        self.assertIsNotNone(completed_semantic_error_receipt(self.state, self.folder))
        self.child.values['last_receipt']['snapshot']['fields'][0]['value'] = 'third-value'
        self.assertIsNone(completed_semantic_error_receipt(self.state, self.folder))

    def test_pending_unknown_conflict_and_wrong_module_never_allow_local_continuation(self):
        for updates in ({'pending_field': 'date'}, {'remote_unsettled': True}):
            self.write_journal(**updates)
            self.assertIsNone(completed_semantic_error_receipt(self.state, self.folder))
        self.write_journal()
        for status in ('unknown', 'conflict'):
            self.child.values['last_receipt']['results'][0]['status'] = status
            self.assertIsNone(completed_semantic_error_receipt(self.state, self.folder))
        self.child.values['last_receipt']['results'][0]['status'] = 'deferred'
        self.state.values['manifest']['modules'][0]['id'] = 'other'
        self.assertIsNone(completed_semantic_error_receipt(self.state, self.folder))
