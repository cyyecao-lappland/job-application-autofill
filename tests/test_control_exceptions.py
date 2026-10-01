import copy
import json
import unittest

from edge_form_graph.contracts import ContractError
from edge_form_graph.control_exceptions import (
    ALLOWED_ADAPTERS, build_control_exception_request, control_method_key,
    validate_control_exception_decisions,
)


def field(fid='#date', **changes):
    return {'id': fid, 'label': '时间 起始日期', 'kind': 'combobox', 'component': None,
            'readonly': True, 'disabled': False, 'control_status': 'recognized',
            'control_pattern': 'next_range_date',
            'signature': {'tag': 'INPUT', 'type': 'text', 'role': 'combobox',
                          'name': 'private_source', 'label': 'private_label'},
            'search_evidence': {'editable': False, 'selection_structure': False}, **changes}


class ControlExceptionTests(unittest.TestCase):
    def test_request_is_value_free_and_does_not_mutate_inputs(self):
        candidate = field(value='PRIVATE_VALUE', profile={'secret': 'PRIVATE_PROFILE'},
                          options=[{'label': 'PRIVATE_OPTION'}], source='/PRIVATE_SOURCE',
                          structure={'tag': 'INPUT', 'value': 'PRIVATE_STRUCTURE'},
                          search_evidence={'editable': False, 'selection_structure': False,
                                           'value': 'PRIVATE_SEARCH', 'options': ['PRIVATE_OPTION']})
        snapshot = {'fields': [candidate], 'profile': {'secret': 'PRIVATE_PROFILE'},
                    'target_url': 'https://private.example?token=PRIVATE_TOKEN'}
        original = copy.deepcopy(snapshot)
        request = build_control_exception_request(snapshot)
        self.assertEqual(snapshot, original)
        self.assertNotIn('PRIVATE', json.dumps(request))
        self.assertNotIn('private_source', json.dumps(request))
        self.assertEqual(set(request['fields'][0]), {
            'field_id', 'label', 'kind', 'component', 'readonly', 'disabled',
            'search_evidence', 'control_status', 'selection_mode', 'control_pattern', 'structure'})
        self.assertEqual(request['fields'][0]['structure'],
                         {'tag': 'INPUT', 'type': 'text', 'role': 'combobox'})

    def test_only_unsupported_or_explicit_method_errors_are_reported(self):
        ordinary = field(kind='text', control_pattern=None, readonly=False,
                         control_status='unverified_text_candidate')
        excluded = ('source_not_found', 'source_is_not_an_answer', 'missing_source',
                    'option_missing_or_ambiguous', 'local_executor_error', 'browser_call_failed',
                    'value_did_not_match_after_action', 'control_transition_timeout', 'batch_boundary')
        for reason in excluded:
            with self.subTest(reason=reason):
                request = build_control_exception_request({'fields': [ordinary]},
                            [{'id': '#date', 'status': 'deferred', 'reason': reason}])
                self.assertEqual(request['fields'], [])
        request = build_control_exception_request({'fields': [ordinary]},
                    [{'id': '#date', 'status': 'deferred',
                      'reason': 'text_probe_discovered_composite_control'}])
        self.assertEqual(len(request['fields']), 1)

    def test_next_date_requires_structural_evidence_not_label_guess(self):
        generic = field(control_pattern=None)
        self.assertEqual(build_control_exception_request({'fields': [generic]})['fields'], [])
        for changes in ({'control_pattern': 'next_range_date'}, {'control_status': 'agent_required'},
                        {'kind': 'unsupported'}):
            with self.subTest(changes=changes):
                request = build_control_exception_request({'fields': [{**generic, **changes}]})
                self.assertEqual(len(request['fields']), 1)

    def test_personal_fields_can_use_the_normal_control_queue(self):
        fields = [field('#protected', protected=True), field('#id-card', label='身份证号码'),
                  field('#passport', label='Passport number'),
                  field('#secret', signature={'name': 'identity_number'})]
        self.assertEqual(len(build_control_exception_request({'fields': fields})['fields']), len(fields))

    def test_completed_results_are_not_requeued_and_all_receipt_shapes_work(self):
        snapshot = {'fields': [field()]}
        for results in ([{'id': '#date', 'status': 'written'}],
                        {'#date': {'status': 'already_matched'}},
                        {'results': [{'field_id': '#date', 'status': 'completed'}]}):
            self.assertEqual(build_control_exception_request(snapshot, results)['fields'], [])

    def test_structural_keys_ignore_answers_labels_and_identity(self):
        start = field(value='2020-01')
        end = field('#end', label='时间 结束日期', value='2026-01',
                    signature={'tag': 'INPUT', 'type': 'text', 'role': 'combobox', 'name': 'end'})
        self.assertEqual(control_method_key(start), control_method_key(end))
        request = build_control_exception_request({'fields': [start, copy.deepcopy(start), end]})
        self.assertEqual([f['field_id'] for f in request['fields']], ['#date', '#end'])
        self.assertEqual(control_method_key(start), control_method_key(request['fields'][0]))
        self.assertNotEqual(control_method_key(start), control_method_key({**start, 'selection_mode': 'tag'}))
        self.assertNotEqual(control_method_key(start), control_method_key({**start, 'control_pattern': None}))

    def test_allowed_adapter_proposal_and_unknown_adapter_hand_off(self):
        request = build_control_exception_request({'fields': [field()]})
        for adapter in ALLOWED_ADAPTERS:
            decision = {'field_id': '#date', 'action': 'use_adapter', 'adapter': adapter}
            self.assertEqual(validate_control_exception_decisions(request, {'decisions': [decision]}), [decision])
        request['allowed_adapters'].append('generated_function')
        for adapter in ('generated_function', 'eval(payload)', None):
            self.assertEqual(validate_control_exception_decisions(request, [
                {'field_id': '#date', 'action': 'use_adapter', 'adapter': adapter}]),
                [{'field_id': '#date', 'action': 'needs_implementation', 'adapter': None}])

    def test_decisions_reject_unknown_fields_duplicates_code_and_missing_entries(self):
        request = build_control_exception_request({'fields': [field()]})
        valid = {'field_id': '#date', 'action': 'use_adapter', 'adapter': 'next_range_date_v1'}
        invalid = [[], [valid, valid], [{**valid, 'field_id': '#other'}],
                   [{**valid, 'source': '/education/0/start'}], [{**valid, 'code': 'execute()'}],
                   [{**valid, 'action': 'execute'}], [{**valid, 'action': []}],
                   [{**valid, 'adapter': {'code': 'execute()'}}]]
        for decisions in invalid:
            with self.subTest(decisions=decisions), self.assertRaises(ContractError):
                validate_control_exception_decisions(request, decisions)
        self.assertEqual(validate_control_exception_decisions(request,
            [{'field_id': '#date', 'action': 'needs_implementation'}]),
            [{'field_id': '#date', 'action': 'needs_implementation', 'adapter': None}])

    def test_empty_request_needs_no_model_decisions(self):
        request = build_control_exception_request({'fields': []})
        self.assertEqual(validate_control_exception_decisions(request, []), [])


if __name__ == '__main__':
    unittest.main()
