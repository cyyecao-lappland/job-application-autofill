import json
from pathlib import Path
import tempfile
import unittest
from edge_form_graph.application_cli import check_prior

class RecoveryTests(unittest.TestCase):
    def test_user_resolution_binds_original_receipt_and_target(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'writer').mkdir();(root/'resolutions').mkdir()
            receipt={'settled':False,'status':'unknown','target':{'tab_id':'one'}}
            journal={'command_id':'old','receipt':receipt}
            (root/'writer'/'old.json').write_text(json.dumps(journal))
            resolution={'original_command_id':'old','original_receipt':receipt,'target':receipt['target'],
                        'executor':'codex-edge','status':'superseded_by_user','user_basis':'user reported manual save',
                        'current_values_match':True}
            path=root/'resolutions'/'old.json';path.write_text(json.dumps(resolution))
            self.assertEqual(check_prior(root)['status'],'preflight_clear')
            resolution['target']={'tab_id':'other'};path.write_text(json.dumps(resolution))
            self.assertEqual(check_prior(root)['status'],'prior_reconciliation_required')
            self.assertEqual(json.loads((root/'writer'/'old.json').read_text()),journal)

if __name__=='__main__':unittest.main()
