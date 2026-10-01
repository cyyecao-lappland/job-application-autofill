import tempfile
import unittest
from pathlib import Path
from edge_form_graph.knowledge import _bound_file_source

class ResumeBindingTests(unittest.TestCase):
    def test_resume_excludes_other_attachment_and_ambiguous_resumes(self):
        with tempfile.TemporaryDirectory() as directory:
            path=Path(directory)/'resume.pdf'
            path.write_bytes(b'local-test')
            resume={'kind':'resume','value_status':'source_backed','path':str(path)}
            profile={'attachments':[{'kind':'life_photo','path':str(path)},resume]}
            field={'kind':'file','label':'简历附件'}
            self.assertEqual(_bound_file_source(profile,{},field),'/attachments/1/path')
            self.assertIsNone(_bound_file_source(profile,{},dict(field,label='作品附件')))
            profile['attachments'].append(dict(resume))
            self.assertIsNone(_bound_file_source(profile,{},field))

    def test_resume_requires_existing_source_backed_file(self):
        profile={'attachments':[{'kind':'resume','value_status':'unknown','path':'missing.pdf'}]}
        self.assertIsNone(_bound_file_source(profile,{}, {'kind':'file','label':'简历附件'}))
