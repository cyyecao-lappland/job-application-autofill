import unittest
from edge_form_graph.parallel import deterministic_record_mappings, focused_profile
from edge_form_graph.confirmed_field_policies import apply_field_policies, preserved_field_reason

class ApplicationAnswerRules(unittest.TestCase):
    def test_optional_misc_attachment_cannot_reuse_resume_by_default(self):
        p={'attachments':[{'kind':'resume','value_status':'source_backed','path':'resume.pdf'}]}
        f={'kind':'file','label':'上传附件','required':False,'value_present':False}
        self.assertTrue(preserved_field_reason(p,{},f).startswith('attachment_purpose_mismatch:'))
        for change in [{'value_present':True},{'required':True},{'label':'上传简历'}]:
            self.assertIsNone(preserved_field_reason(p,{},dict(f,**change)))
        p['attachments'].append({'kind':'portfolio','value_status':'source_backed','path':'portfolio.pdf'})
        # A portfolio source does not establish the purpose of an unspecified attachment.
        before={'fields':[dict(f,id='file')]}
        proposed={'mappings':[{'field_id':'file','source':'/attachments/1/path'}],'deferred':[]}
        filtered=apply_field_policies(p,before,proposed)
        self.assertEqual(filtered['mappings'],[])
        self.assertEqual(filtered['deferred'][0]['field_id'],'file')
        self.assertTrue(filtered['deferred'][0]['reason'].startswith('unsupported:'))
    def test_exact_application_city_is_scalar_and_cannot_cross_targets_or_geography(self):
        profile={'preferences':{'preferred_cities':['上海']},'batch_answers':{'company':{'target_url':'https://example.org/job/1/apply','intended_city':'上海'}}}
        snapshot={'target':{'url':'https://example.org/job/1/apply'},'fields':[{'id':'city','label':'意向工作城市','kind':'text','selector':'#city'}]}
        mappings=deterministic_record_mappings(profile,snapshot,{'city'})
        self.assertEqual(mappings[0]['source'],'/batch_answers/company/intended_city')
        snapshot['target']['url']='https://example.org/job/2/apply'
        self.assertEqual(deterministic_record_mappings(profile,snapshot,{'city'}),[])
        snapshot['target']['url']='https://example.org/job/1/apply'
        profile['batch_answers']['company']['intended_city']='北京'
        self.assertEqual(deterministic_record_mappings(profile,snapshot,{'city'}),[])

    def test_upload_projection_keeps_attachment_sources_but_excludes_unrelated_history(self):
        p={'attachments':[{'path':'resume.pdf'}],'education':[{'school_name':'School'}],'projects':[{'name':'Project'}],'preferences':{'preferred_cities':['上海']}}
        selected=focused_profile(p,{'module_label':'上传','fields':[{'label':'上传简历'}]})
        self.assertIn('attachments',selected)
        self.assertNotIn('education',selected)
        self.assertNotIn('projects',selected)

    def test_resume_rule_requires_unique_source_and_never_fills_misc_attachment(self):
        resume={'kind':'resume','value_status':'source_backed','path':'resume.pdf'}
        p={'attachments':[resume]}
        s={'fields':[{'id':'file','label':'上传简历','kind':'file','selector':'#file'}]}
        self.assertEqual(len(deterministic_record_mappings(p,s,{'file'})),1)
        p['attachments'].append(dict(resume))
        self.assertEqual(deterministic_record_mappings(p,s,{'file'}),[])
        p['attachments'].pop();s['fields'][0]['label']='上传附件'
        self.assertEqual(deterministic_record_mappings(p,s,{'file'}),[])
