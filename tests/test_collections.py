import unittest

from edge_form_graph.collections import plan_collection, plan_experience_placement
from edge_form_graph.contracts import ContractError


class CollectionPlannerTests(unittest.TestCase):
    def test_projects_fall_back_to_internships_only_when_project_section_is_absent(self):
        profile = {
            'employment': [{'record_id': 'tencent', 'start_date': '2025-09-17'}],
            'projects': [
                {'record_id': 'smart-education', 'start_date': '2024-09-01',
                 'presentation_policy': {'user_requested_fallback': '无项目经历栏目时按实习填写'}},
                {'record_id': 'vcyuan', 'start_date': '2026-08-01',
                 'presentation_policy': {'user_requested_fallback': '无项目经历栏目时按实习填写'}},
                {'record_id': 'ordinary-project', 'start_date': '2026-01-01'},
            ],
        }
        fallback = plan_experience_placement(profile, ['internships'])
        self.assertEqual([item['record_id'] for item in fallback], ['vcyuan', 'tencent', 'smart-education'])
        self.assertEqual(fallback[0]['source_collection'], '/projects')
        self.assertEqual(fallback[0]['module_type'], 'internship_project_fallback')
        self.assertEqual(fallback[1]['source_collection'], '/employment')

        normal = plan_experience_placement(profile, ['internships', 'projects'])
        by_id = {item['record_id']: item for item in normal}
        self.assertEqual(by_id['vcyuan']['target_section'], 'projects')
        self.assertEqual(by_id['ordinary-project']['target_section'], 'projects')
        self.assertEqual(by_id['tencent']['target_section'], 'internships')

    def test_project_fallback_respects_default_exclusion(self):
        profile = {'employment': [], 'projects': [{
            'record_id': 'excluded', 'start_date': '2026-01-01',
            'autofill_policy': {'include_by_default': False},
            'presentation_policy': {'user_requested_fallback': '无项目经历栏目时按实习填写'}}]}
        self.assertEqual(plan_experience_placement(profile, ['employment']), [])

    def test_binds_existing_and_adds_every_missing_in_profile_order(self):
        profile = {'education': [
            {'record_id': 'master', 'school': '华东师范大学', 'start': '2024-09'},
            {'record_id': 'computer', 'school': '湘潭大学', 'start': '2021-03'},
            {'record_id': 'materials', 'school': '湘潭大学', 'start': '2019-09'}]}
        page = [{'page_id': 'card-1', 'facts': {'school': '湘潭大学', 'start': '2021-03'}}]
        plan = plan_collection(profile, '/education', page, ['school', 'start'])
        self.assertEqual(plan['actions'], [
            {'action': 'add_missing', 'record_id': 'master'},
            {'action': 'bind_existing', 'record_id': 'computer', 'page_id': 'card-1'},
            {'action': 'add_missing', 'record_id': 'materials'}])

    def test_excluded_records_and_unmatched_cards_are_preserved(self):
        profile = {'projects': [
            {'record_id': 'keep', 'name': 'A'},
            {'record_id': 'omit', 'name': 'B', 'autofill_policy': {'include_by_default': False}}]}
        page = [{'page_id': 'extra', 'facts': {'name': 'C'}}]
        plan = plan_collection(profile, '/projects', page, ['name'])
        self.assertEqual(plan['actions'], [{'action': 'add_missing', 'record_id': 'keep'}])
        self.assertEqual(plan['preserve_unmatched_page_ids'], ['extra'])

    def test_duplicate_or_incomplete_identity_is_deferred_without_guessing(self):
        profile = {'projects': [{'record_id': 'a', 'name': '同名'}, {'record_id': 'b', 'name': '同名'}]}
        plan = plan_collection(profile, '/projects', [], ['name'])
        self.assertEqual(plan['actions'], [{'action': 'add_missing', 'record_id': 'a'}])
        self.assertEqual(plan['deferred'][0]['reason'], 'canonical_identity_duplicate')
        with self.assertRaises(ContractError):
            plan_collection({'projects': [{'name': 'x'}]}, '/projects', [], ['name'])


if __name__ == '__main__':
    unittest.main()
