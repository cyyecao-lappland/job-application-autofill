import copy
import json
import tempfile
import unittest
from pathlib import Path
from edge_form_graph.contracts import ContractError
from edge_form_graph.save_preflight_recovery import recover_save_preflight,audited_preflight,is_readonly_preflight,preflight_writer_lock


class SavePreflightRecoveryTests(unittest.TestCase):
    def test_shared_scope_recovery_is_refused_before_audit_or_mutation(self):
        with tempfile.TemporaryDirectory() as d:
            folder=Path(d);(folder/'writer').mkdir()
            command={'kind':'save_scope','command_id':'shared','expected_modules':[{'module_id':'a'},{'module_id':'b'}]}
            values={'status':'awaiting_scope_save','command':command,'index':1,
                    'manifest':{'modules':[{'id':'a'},{'id':'b'}]}}
            original=copy.deepcopy(values)
            with self.assertRaisesRegex(ContractError,'requires_single_module_scope'):
                recover_save_preflight(values,command,folder,'program repair')
            self.assertEqual(values,original)
            self.assertFalse((folder/'save-preflight-audits').exists())

    def test_recovery_writer_lock_is_exclusive_and_released(self):
        with tempfile.TemporaryDirectory() as d:
            folder=Path(d);(folder/'writer').mkdir()
            with preflight_writer_lock(folder):
                with self.assertRaises(ContractError):
                    with preflight_writer_lock(folder):pass
            self.assertFalse((folder/'writer'/'active.lock').exists())

    def test_readonly_failure_preserves_journal_and_requires_new_review(self):
        with tempfile.TemporaryDirectory() as d:
            folder=Path(d);(folder/'writer').mkdir()
            command={'kind':'save_scope','command_id':'save-1','expected_modules':[{'module_id':'m'}]}
            journal={'kind':'save_scope','command_id':'save-1','status':'pending',
                     'instrumentation':[{'stage':'module_readback','method':'evaluate','status':'returned'}]}
            path=folder/'writer'/'save-1.json';path.write_text(json.dumps(journal))
            values={'status':'awaiting_scope_save','command':command,'index':0,
                    'manifest':{'modules':[{'id':'m','snapshot':{'old':True}}]}}
            result=recover_save_preflight(values,command,folder,'Authorized program repair')
            self.assertEqual(json.loads(path.read_text()),journal)
            self.assertTrue(audited_preflight(folder,journal))
            self.assertEqual(result['scope_reviews'],{})
            self.assertIsNone(result['command'])
            self.assertNotIn('snapshot',result['manifest']['modules'][0])
            (folder/'writer'/'active.lock').write_text('active')
            with self.assertRaises(ContractError):recover_save_preflight(values,command,folder,'basis')

    def test_any_action_pending_call_or_save_stage_refuses_recovery(self):
        base={'kind':'save_scope','status':'pending',
              'instrumentation':[{'stage':'module_readback','method':'evaluate','status':'returned'}]}
        for update in ({'save_stage':'save_click_issued'}, {'receipt':{'status':'unknown'}},
                       {'stages':[{'name':'save_click_issued'}]}, {'kind':'fill'},
                       {'instrumentation':[]}, {'status':'unknown'}):
            self.assertFalse(is_readonly_preflight({**base,**update}))
        for key,value in [('stage','field_action'),('method','click'),('status','failed'),('status','pending')]:
            changed=copy.deepcopy(base);changed['instrumentation'][0][key]=value
            self.assertFalse(is_readonly_preflight(changed))
