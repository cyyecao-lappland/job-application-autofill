import unittest
from edge_form_graph.contracts import ContractError, transform
from edge_form_graph.parallel import focused_profile, deterministic_record_mappings
from tests.test_graph import snapshot
from edge_form_graph.enum_repair import context_decisions

class ExplicitNoneTests(unittest.TestCase):
    def test_second_degree_negative_is_bound_and_does_not_answer_transfer_question(self):
        profile = {'education': [{'record_id': 'edu-1', 'second_degree': '无'}]}
        page = snapshot()
        page['mapping_context'] = {'record_collection': '/education', 'record_id': 'edu-1'}
        page['fields'][0].update(label='是否第二学位', value='')
        mapping = deterministic_record_mappings(profile, page, [page['fields'][0]['id']])
        self.assertEqual(mapping[0]['source'], '/education/0/second_degree')
        self.assertEqual(mapping[0]['transform'], 'none_string_no')
        page['fields'][0]['label'] = '是否专升本'
        self.assertEqual(deterministic_record_mappings(profile, page, [page['fields'][0]['id']]), [])

    def test_unified_enrollment_alias_needs_same_record_full_time_evidence(self):
        request = {'field_id': 'type', 'field_label': '受教育类型',
                   'source_value': '全日制统分统招', 'options': ['全日制统招', '成人教育'],
                   'source_context': {'study_mode': '全日制'}}
        self.assertEqual(context_decisions([request])[0]['option'], '全日制统招')
        request['source_context']['study_mode'] = '非全日制'
        self.assertEqual(context_decisions([request]), [])

    def test_other_qualification_certificate_uses_explicit_none_not_skills(self):
        page = snapshot()
        page['fields'][0]['label'] = '其他资格证书'
        profile = {'certifications': [], 'collection_status': {'certifications': 'explicit_none'}}
        mapping = deterministic_record_mappings(profile, page, [page['fields'][0]['id']])
        self.assertEqual(mapping[0]['source'], '/collection_status/certifications')
        self.assertEqual(mapping[0]['transform'], 'explicit_none_text')
        profile['collection_status']['certifications'] = 'unknown_not_none'
        self.assertEqual(deterministic_record_mappings(profile, page, [page['fields'][0]['id']]), [])

    def test_review_projection_preserves_explicit_none_source_and_collection(self):
        profile = {'skills': [{'name': '能力描述'}], 'certifications': [],
                   'collection_status': {'certifications': 'explicit_none'},
                   'education': [{'school': 'unrelated'}]}
        projected = focused_profile(profile, {'module_label': '技能信息'},
                                    ['/collection_status/certifications'])
        self.assertEqual(projected['collection_status']['certifications'], 'explicit_none')
        self.assertEqual(projected['certifications'], [])
        self.assertNotIn('education', projected)

    def test_review_projection_preserves_cross_category_plan_source(self):
        profile = {'skills': [], 'certifications': [{'name': '证书'}]}
        projected = focused_profile(profile, {'module_label': '技能信息'},
                                    ['/certifications/0/name'])
        self.assertEqual(projected['certifications'], profile['certifications'])

    def test_only_explicit_none_has_a_negative_text_answer(self):
        self.assertEqual(transform('explicit_none','explicit_none_text'),'无')
        for value in [None, '', [], False, 'unknown_not_none', 'populated']:
            with self.assertRaises(ContractError):
                transform(value,'explicit_none_text')
