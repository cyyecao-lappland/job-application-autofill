import copy
import json
import tempfile
import unittest
from pathlib import Path

from edge_form_graph.reporting import write_reports, timings


class ReportingTests(unittest.TestCase):
    def test_recovery_blocker_is_actionable_not_generic_unknown(self):
        with tempfile.TemporaryDirectory() as directory:
            report = write_reports(directory, {'status': 'recovery_blocked',
                'recovery_reason': 'transport_settlement_required'})
            self.assertEqual(report['status'], 'recovery_blocked')
            self.assertEqual(report['recovery_reason'], 'transport_settlement_required')

    def test_empty_state_is_not_completion_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertFalse(write_reports(directory, {'status': 'new'})['evidence_currency']['current_completion_confirmed'])

    def test_private_values_unknown_writes_and_inputs_preserved(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'writer').mkdir()
            private = 'SYNTHETIC_PERSON_123456789012345678'
            state = {'status': 'complete', 'profile': {'name': private},
                'scopes': {private: 'saved_confirmed'},
                'metrics': {'model_calls': 2, 'table_hits': 3, private: 17},
                'results': {private: {'status': 'partial_draft', 'snapshot': {},
                    'results': {private: {'status': 'unknown', 'reason': private}},
                    'metrics': {'model_hits': 1, 'field_table_hits': 3, 'enum_table_hits': 2},
                    'semantic_report': [{'classification': 'inference', 'reason': private}],
                    'trace': [{'node': 'map', 'duration_ms': 25}]}}}
            journal = {'kind': 'fill', 'status': 'unknown', 'pending_field': private,
                'receipt': {'status': 'unknown', 'settled': False, 'results': [
                    {'id': private, 'status': 'unknown', 'reason': 'browser_call_failed'}]},
                'instrumentation': [{'stage': 'popup_search', 'status': 'failed', 'duration_ms': 42,
                    'error': {'code': 'timeout', 'message': private}}]}
            path = root/'writer'/'private.json'
            text = json.dumps(journal)
            path.write_text(text, encoding='utf-8')
            original = copy.deepcopy(state)
            report = write_reports(root, state)
            self.assertEqual(state, original)
            self.assertEqual(path.read_text(encoding='utf-8'), text)
            for name in ('agent-report.json', 'agent-report.md'):
                output = (root/name).read_text(encoding='utf-8')
                self.assertNotIn(private, output)
                self.assertIn('private.json', output)
            self.assertEqual(report['metrics'], {'model_calls': 2, 'table_hits': 3})
            self.assertEqual(report['modules'][0]['metrics']['field_table_hits'], 3)
            self.assertEqual(report['modules'][0]['model_classifications_unconfirmed'], {'inference': 1})
            self.assertGreaterEqual(report['unknown_operation_count'], 3)
            self.assertEqual(report['progress'], {'filled_modules': 0, 'saved_scopes': 1, 'submitted_confirmed': False})
            self.assertEqual(report['journals'][0]['stages'][0]['duration_ms'], 42)
            self.assertTrue(all(d['cause_status'] == 'unknown' for d in report['diagnoses']))

    def test_confirmed_mapping_condition_and_inference_are_separate(self):
        with tempfile.TemporaryDirectory() as directory:
            report = write_reports(directory, {'snapshot': {}, 'status': 'review_rejected',
                'results': {'x': {'status': 'deferred', 'reason': 'option_missing_or_ambiguous'}}})
            self.assertEqual(report['diagnoses'][0]['category'], 'mapping')
            self.assertEqual(report['diagnoses'][0]['cause_status'], 'confirmed')
            self.assertEqual(report['diagnoses'][1]['category'], 'inference')

    def test_timing_does_not_claim_stamp_interval_is_execution(self):
        rows = timings([{'node': 'map', 'at': 3}, {'node': 'validate', 'at': 7},
                        {'node': 'private name', 'duration_ms': 2}], 1)
        self.assertEqual(rows[0]['elapsed_ms'], 2000)
        self.assertEqual(rows[1]['basis'], 'stamp_interval_including_waits')
        self.assertEqual(rows[2]['node'], 'unknown')
        self.assertEqual(rows[2]['basis'], 'explicit_duration')
        self.assertIsNone(timings([{'node': 'map'}])[0]['elapsed_ms'])

    def test_pending_corrupt_and_settled_unknown_journals_retained(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root/'writer').mkdir()
            for name, value in [('pending', {'status': 'pending'}),
                                ('ended', {'receipt': {'status': 'unknown', 'settled': True}})]:
                (root/'writer'/f'{name}.json').write_text(json.dumps(value), encoding='utf-8')
            (root/'writer'/'broken.json').write_text('{', encoding='utf-8')
            report = write_reports(root, {'status': 'complete'})
            self.assertEqual(report['unknown_operation_count'], 3)
            self.assertFalse(report['progress']['submitted_confirmed'])

    def test_fill_save_and_submit_require_distinct_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            report = write_reports(directory, {'status': 'saved', 'snapshot': {},
                'results': {'x': {'status': 'written'}}})
            self.assertEqual(report['progress']['filled_modules'], 1)
            self.assertEqual(report['progress']['saved_scopes'], 0)
            self.assertFalse(report['progress']['submitted_confirmed'])

    def test_actionable_semantic_tasks_redact_values_but_keep_context(self):
        with tempfile.TemporaryDirectory() as directory:
            secret = 'Synthetic Private Person'
            state = {'profile': {'name': secret}, 'results': {'education': {
                'snapshot': {'module_id': 'education', 'fields': [
                    {'id': '#school', 'label': 'School', 'value': ''}]},
                'metrics': {'semantic_model': 'gpt-6-luna', 'field_table_hits': 2,
                    'semantic_fields': 4, 'semantic_seconds': 1.5, 'field_learned': 1,
                    'enum_table_hits': 3, 'enum_seconds': 0.2, 'enum_learned': 1},
                'learning_errors': [{'phase': 'learn_fields', 'error': 'OSError'}],
                'semantic_report': [{'field_id': '#school', 'classification': kind,
                    'source': '/education/0/school', 'transform': 'identity', 'depends_on': ['#level'],
                    'reason': f'Needs review for {secret}, reference 123456789012345678'}
                    for kind in ('unsupported', 'missing', 'conflict', 'inference')],
                'results': {'#school': {'status': 'deferred', 'reason': 'unsupported_control'}}}}}
            report = write_reports(directory, state)
            decisions = [t for t in report['agent_tasks'] if t['evidence'] == 'model_classification_unconfirmed']
            self.assertEqual(len(decisions), 4)
            self.assertEqual(decisions[0]['context']['field_id'], '#school')
            self.assertEqual(decisions[0]['context']['field_label'], 'School')
            self.assertEqual(decisions[0]['context']['module_id'], 'education')
            self.assertEqual(decisions[0]['context']['source'], '/education/0/school')
            self.assertIn('Needs review', decisions[0]['context']['reason'])
            self.assertEqual(report['learning_error_count'], 1)
            self.assertEqual(report['modules'][0]['metrics']['field_learned'], 1)
            self.assertEqual(report['modules'][0]['metrics']['semantic_model'], 'gpt-6-luna')
            for name in ('agent-report.json', 'agent-report.md'):
                text = (Path(directory)/name).read_text(encoding='utf-8')
                self.assertNotIn(secret, text)
                self.assertNotIn('123456789012345678', text)

    def test_active_nested_journal_overrides_old_parent_completion(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            writer = root/'child'/'writer'
            writer.mkdir(parents=True)
            command = '12345678-1234-4321-9876-123456789012'
            journal = {'command_id': command, 'kind': 'fill', 'status': 'pending',
                'pending_field': '#school', 'instrumentation': [{'field_id': '#school',
                    'field_label': 'School', 'stage': 'popup_search', 'status': 'failed',
                    'duration_ms': 50, 'error': {'code': 'timeout', 'message': 'Timeout waiting for popup'}}]}
            path = writer/'active.json'
            original = json.dumps(journal)
            path.write_text(original, encoding='utf-8')
            state = {'status': 'complete', 'results': {'education': {'snapshot': {},
                'status': 'filled', 'results': {'#school': {'status': 'written'}}}}}
            report = write_reports(root, state)
            self.assertEqual(report['progress']['filled_modules'], 0)
            self.assertEqual(report['evidence_currency']['filled_modules_in_state'], 1)
            self.assertFalse(report['evidence_currency']['current_completion_confirmed'])
            self.assertEqual(report['journals'][0]['command_id'], command)
            self.assertEqual(report['journals'][0]['journal_path'], str(path))
            self.assertEqual(report['diagnoses'][0]['stage'], 'popup_search')
            self.assertEqual(report['diagnoses'][0]['observed_error'], 'Timeout waiting for popup')
            self.assertEqual(report['diagnoses'][0]['hypothesis']['status'], 'unconfirmed')
            self.assertTrue(any(t['category'] == 'reconciliation' for t in report['agent_tasks']))
            self.assertEqual(path.read_text(encoding='utf-8'), original)


if __name__ == '__main__':
    unittest.main()
