import unittest
from edge_form_graph.parallel import deterministic_record_mappings,prefilled_bindings

class ApplicationContextTests(unittest.TestCase):
    def test_intended_location_requires_exact_url_and_preferred_city(self):
        profile={'preferences':{'preferred_cities':['杭州']},'batch_answers':{
            'employer':{'target_url':'https://example.org/apply','intended_city':'杭州'}}}
        snapshot={'target':{'url':'https://example.org/apply'},'fields':[
            {'id':'one','kind':'combobox','label':'意向工作地点','value':'','selector':'#city'}]}
        self.assertEqual(len(deterministic_record_mappings(profile,snapshot,{'one'})),1)
        profile['batch_answers']['employer']['intended_city']='北京'
        self.assertEqual(deterministic_record_mappings(profile,snapshot,{'one'}),[])
        profile['batch_answers']['employer']['intended_city']='杭州'
        snapshot['target']['url']='https://another.org/apply'
        self.assertEqual(deterministic_record_mappings(profile,snapshot,{'one'}),[])

    def test_workflow_confirmations_never_cross_urls_or_labels(self):
        profile={'batch_answers':{'employer':{'target_url':'https://example.org/apply','form_confirmations':[{'label':'truthfulness','confirmed':True,'basis':'explicit authorized workflow'}]}}}
        snapshot={'target':{'url':'https://example.org/apply'},'fields':[{'id':'one','kind':'checkbox','label':'truthfulness','value':False,'disabled':False,'selector':'#truthfulness'}]}
        self.assertEqual(deterministic_record_mappings(profile,snapshot,{'one'})[0]['source'],'/batch_answers/employer/form_confirmations/0/confirmed')
        snapshot['target']['url']='https://other.org/apply'
        self.assertEqual(deterministic_record_mappings(profile,snapshot,{'one'}),[])
        snapshot['target']['url']='https://example.org/apply';snapshot['fields'][0]['label']='criminal record'
        self.assertEqual(deterministic_record_mappings(profile,snapshot,{'one'}),[])

    def test_readonly_graduation_candidates_require_unique_highest_and_matching_values(self):
        profile={'education':[{'education_level':'硕士研究生','degree':'硕士','end_date':'2027-07-01'}]}
        fields=[{'id':'level','label':'最高学历','value':'硕士','kind':'combobox','disabled':True},
                {'id':'year','label':'毕业时间 年','value':'2027','date_part':'year','kind':'combobox','disabled':True},
                {'id':'month','label':'毕业时间 月','value':'7','date_part':'month','kind':'combobox','disabled':True}]
        result={'current':{'fields':fields}}
        self.assertEqual(len(prefilled_bindings(profile,result)),3)
        fields[-1]['value']='6'
        self.assertEqual(len(prefilled_bindings(profile,result)),2)
        profile['education'].append(dict(profile['education'][0]))
        self.assertEqual(prefilled_bindings(profile,result),[])
