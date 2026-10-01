import unittest
from edge_form_graph.required_answer_gate import unanswerable_required_fields


class RequiredGateTests(unittest.TestCase):
    def result(self, *, required=True, present=False, status='deferred', reason='missing: no source'):
        return {'current': {'fields': [{'id': 'a', 'label': 'frequency',
                'required': required, 'value_present': present}]},
                'results': {'a': {'status': status, 'reason': reason}}}

    def test_only_explicit_required_empty_missing_answer_stops_queue(self):
        self.assertEqual(len(unanswerable_required_fields(self.result())), 1)
        for change in [{'required': False}, {'required': None}, {'present': True},
                       {'status': 'unknown'}, {'reason': 'unsupported: adapter unavailable'},
                       {'reason': 'dependency_not_ready'}]:
            self.assertEqual(unanswerable_required_fields(self.result(**change)), [])

    def test_enum_requires_completed_rejection_not_control_ambiguity(self):
        result = self.result(reason='option_missing_or_ambiguous')
        self.assertEqual(unanswerable_required_fields(result), [])
        result['enum_history'] = [{'review': {'decisions': [{'field_id': 'a', 'option': None}]}}]
        self.assertEqual(len(unanswerable_required_fields(result)), 1)
        result['enum_history'][0]['error'] = 'model_worker_failed'
        self.assertEqual(unanswerable_required_fields(result), [])
