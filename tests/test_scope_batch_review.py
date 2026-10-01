"""Synthetic scope reviews: no credentials, real models or browser writes."""
import copy
import unittest

from edge_form_graph.contracts import ContractError, compile_plan
from edge_form_graph.model import CodexJsonModel
from edge_form_graph.parallel import ParallelWorkers, scope_reviews_current
from tests.test_parallel import inventory


def fixture(count=3, known=()):
    manifest = inventory(count)
    results = {}
    profile = {'school': '示例大学', 'unmapped_records': ['must inventory']}
    for module in manifest['modules']:
        before = copy.deepcopy(module['snapshot'])
        current = copy.deepcopy(before)
        current['fields'][0]['value'] = profile['school']
        plan = {'mappings': [{'field_id': '#school', 'source': '/school',
                             'transform': 'identity', 'depends_on': []}], 'deferred': []}
        operations, _ = compile_plan(profile, before, plan)
        results[module['id']] = {
            'snapshot': before, 'current': current, 'proposal': plan,
            'operations': operations, 'results': {'#school': {'status': 'written'}},
            'revision': 1,
            'metrics': {'field_table_hits': 1, 'model_calls': int(module['id'] not in known)},
        }
    return profile, manifest, results


class RecordingReviewer(CodexJsonModel):
    def __init__(self, mutate=None):
        super().__init__('gpt-6-luna')
        self.inputs = []
        self.schemas = []
        self.mutate = mutate

    def ask(self, instructions, data, schema):
        self.inputs.append(copy.deepcopy(data))
        self.schemas.append(copy.deepcopy(schema))
        context = {item['module']['id']: item for item in data['save_scope_context']}
        response = {'reviews': [{'module_id': mid, 'approved': True, 'issues': [],
                                'checked_field_ids': [f['id'] for f in context[mid]['before']['fields']]}
                               for mid in data['review_module_ids']]}
        if self.mutate:
            self.mutate(response)
        return response


class ScopeBatchReviewTests(unittest.TestCase):
    def test_checked_id_enum_cannot_be_overwritten_by_issue_length_constraint(self):
        profile, manifest, results = fixture(2)
        reviewer = RecordingReviewer()
        ParallelWorkers(reviewer).review_scope(profile, manifest['modules'], results, manifest['target'])
        variants = reviewer.schemas[0]['properties']['reviews']['items']['anyOf']
        for variant in variants:
            props = variant['properties']
            self.assertIn('enum', props['checked_field_ids']['items'])
            self.assertNotIn('minLength', props['checked_field_ids']['items'])
            self.assertEqual(props['issues']['items']['minLength'], 20)
            self.assertIsNot(props['checked_field_ids'], props['issues'])

    def test_many_unknown_modules_use_one_call_and_keep_complete_scope(self):
        profile, manifest, results = fixture(8)
        reviewer = RecordingReviewer()
        reviews = ParallelWorkers(reviewer).review_scope(profile, manifest['modules'], results, manifest['target'])
        self.assertEqual(len(reviewer.inputs), 1)
        self.assertEqual(len(reviewer.inputs[0]['save_scope_context']), 8)
        self.assertEqual(reviewer.inputs[0]['profile']['unmapped_records'], ['must inventory'])
        self.assertEqual(set(reviews), set(results))
        self.assertTrue(scope_reviews_current(manifest['modules'], results, reviews, manifest['target']))

    def test_known_modules_keep_program_review_and_are_visible_to_model(self):
        profile, manifest, results = fixture(3, known={'a', 'b'})
        reviewer = RecordingReviewer()
        reviews = ParallelWorkers(reviewer).review_scope(profile, manifest['modules'], results, manifest['target'])
        self.assertEqual(reviews['a']['model'], 'program')
        self.assertEqual(reviews['b']['model'], 'program')
        self.assertEqual(reviews['c']['model'], 'gpt-6-luna')
        self.assertEqual(reviewer.inputs[0]['review_module_ids'], ['module_3'])
        self.assertEqual(len(reviewer.inputs[0]['save_scope_context']), 3)
        self.assertTrue(scope_reviews_current(manifest['modules'], results, reviews, manifest['target']))

    def test_all_known_modules_need_no_model_call(self):
        profile, manifest, results = fixture(3, known={'a', 'b', 'c'})
        reviewer = RecordingReviewer()
        reviews = ParallelWorkers(reviewer).review_scope(profile, manifest['modules'], results, manifest['target'])
        self.assertEqual(reviewer.inputs, [])
        self.assertTrue(scope_reviews_current(manifest['modules'], results, reviews, manifest['target']))

    def test_rejection_missing_duplicate_or_foreign_fields_block_save_gate(self):
        def reject(response):
            response['reviews'][0].update(approved=False, issues=['The school source belongs to another record.'])
        def missing(response):
            response['reviews'].pop()
        def duplicate(response):
            response['reviews'].append(copy.deepcopy(response['reviews'][0]))
        def foreign(response):
            response['reviews'][0]['checked_field_ids'] = response['reviews'][1]['checked_field_ids']
        def incomplete(response):
            response['reviews'][0]['checked_field_ids'] = []
        for mutation in (reject, missing, duplicate, foreign, incomplete):
            with self.subTest(mutation=mutation.__name__):
                profile, manifest, results = fixture(3)
                reviewer = RecordingReviewer(mutation)
                reviews = ParallelWorkers(reviewer).review_scope(profile, manifest['modules'], results, manifest['target'])
                self.assertEqual(len(reviewer.inputs), 1)
                self.assertFalse(scope_reviews_current(manifest['modules'], results, reviews, manifest['target']))

    def test_long_duplicate_field_ids_round_trip_without_mutating_evidence(self):
        profile, manifest, results = fixture(2)
        original = copy.deepcopy(results)
        reviewer = RecordingReviewer()
        ParallelWorkers(reviewer).review_scope(profile, manifest['modules'], results, manifest['target'])
        wire = reviewer.inputs[0]['save_scope_context']
        ids = [item['before']['fields'][0]['id'] for item in wire]
        self.assertEqual(len(set(ids)), 2)
        self.assertTrue(all('selector' not in item['before']['fields'][0] for item in wire))
        self.assertEqual(results, original)

    def test_preserved_blanks_and_dependency_ids_remain_module_scoped(self):
        profile, manifest, results = fixture(2)
        coverage = []
        for module in manifest['modules']:
            result = results[module['id']]
            plan = copy.deepcopy(result['proposal'])
            plan['mappings'][0]['depends_on'] = ['#school']
            plan['deferred'] = [{'field_id': '#school', 'reason': 'fixture transport only'}]
            coverage.append({'module': {**module, 'preserved_blank_field_ids': ['#school']},
                             'before': result['snapshot'], 'after': result['current'],
                             'plan': plan, 'revision': 1})
        reviewer = RecordingReviewer()
        verdicts = reviewer.review_scope(profile, coverage, ['a', 'b'])
        for item in reviewer.inputs[0]['save_scope_context']:
            fid = item['before']['fields'][0]['id']
            self.assertEqual(item['module']['preserved_blank_field_ids'], [fid])
            self.assertEqual(item['plan']['mappings'][0]['depends_on'], [fid])
            self.assertEqual(item['plan']['deferred'][0]['field_id'], fid)
        self.assertEqual(verdicts['a']['checked_field_ids'], ['#school'])
        coverage[0]['module']['preserved_blank_field_ids'] = ['unknown']
        with self.assertRaises(ContractError):
            reviewer.review_scope(profile, coverage, ['a', 'b'])

    def test_changed_live_value_invalidates_batched_approval(self):
        profile, manifest, results = fixture(2, known={'a'})
        reviews = ParallelWorkers(RecordingReviewer()).review_scope(profile, manifest['modules'], results, manifest['target'])
        results['a']['current']['fields'][0]['value'] = 'User change'
        self.assertFalse(scope_reviews_current(manifest['modules'], results, reviews, manifest['target']))


if __name__ == '__main__':
    unittest.main()
