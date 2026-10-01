"""Adapt old rule-focused fixtures to the real post-save activation contract.

All receipts here are offline test data. Production has no public pre-save
learn_fields/learn_enums writer.
"""
import copy
from unittest.mock import patch

from edge_form_graph.contracts import compile_plan
from edge_form_graph.knowledge import KnowledgeStore as ProductionStore


class KnowledgeStore(ProductionStore):
    def _saved_fixture(self, profile, before, proposal, current, operations, kind):
        module = {'snapshot': before, 'current': current, 'proposal': proposal, 'operations': operations,
                  'revision': 1, 'reviewed_revision': 1, 'command': None,
                  'review': {'approved': True, 'issues': [], 'checked_field_ids': [f['id'] for f in current['fields']]},
                  'results': {op['id']: {'status': 'already_matched'} for op in operations}}
        receipt = {'status': 'saved', 'kind': 'save', 'settled': True, 'target': current['target'],
                   'evidence': {'save_confirmed': True, 'save_observed_at': current['observed_at'],
                                'saved_modules': [copy.deepcopy(current)]}}
        other = '_collect_enums' if kind == 'field_learned' else '_collect_fields'
        with patch.object(self, other, return_value=[]):
            return self.compile_saved(profile, [module], receipt)[kind]

    def learn_fields(self, profile, snapshot, proposal, current, approved, *, operations=None):
        candidates = self._collect_fields(profile, snapshot, proposal, current, approved, operations=operations)
        if not candidates:
            return 0
        ops = operations if operations is not None else compile_plan(profile, current, proposal)[0]
        return self._saved_fixture(profile, snapshot, proposal, current, ops, 'field_learned')

    def learn_enums(self, profile, snapshot, operations, current):
        candidates = self._collect_enums(profile, snapshot, operations, current)
        if not candidates:
            return 0
        proposal = {'mappings': [{'field_id': o['id'], **{k: o[k] for k in ('source', 'transform', 'depends_on')}} for o in operations],
                    'deferred': [{'field_id': f['id'], 'reason': 'not_tested'} for f in current['fields'] if f['id'] not in {o['id'] for o in operations}]}
        return self._saved_fixture(profile, snapshot, proposal, current, operations, 'enum_learned')
