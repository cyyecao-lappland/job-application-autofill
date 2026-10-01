import unittest
from edge_form_graph.employment_defaults import prior_employment_default_source as source


class EmploymentDefaultsTests(unittest.TestCase):
    def setUp(self):
        self.profile={'company_answer_defaults':{'prior_group_employment':{'default':False,'exceptions':['腾讯']}}}

    def test_named_employer_uses_explicit_negative_default(self):
        self.assertEqual(source(self.profile,'是否曾在顺丰任职'),'/company_answer_defaults/prior_group_employment/default')

    def test_exception_or_actual_employment_disables_default(self):
        self.assertIsNone(source(self.profile,'是否曾在腾讯任职'))
        self.profile['employment']=[{'company':'顺丰科技有限公司'}]
        self.assertIsNone(source(self.profile,'是否曾在顺丰任职'))

    def test_absent_default_ambiguous_company_and_other_propositions_do_not_infer_no(self):
        self.assertIsNone(source({},'是否曾在顺丰任职'))
        for label in ['是否曾在本集团任职','是否曾应聘顺丰','亲属是否在顺丰任职']:
            self.assertIsNone(source(self.profile,label))
