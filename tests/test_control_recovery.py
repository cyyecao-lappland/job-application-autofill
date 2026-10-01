import copy
import time
import unittest
import tempfile
from pathlib import Path
from edge_form_graph.control_recovery import reconcile_control
from edge_form_graph.contracts import ContractError

class ControlRecoveryTests(unittest.TestCase):
    def test_method_reuse_contains_no_answer_or_locator(self):
        from edge_form_graph.control_methods import sync_methods
        h,d=self.fixture();method=reconcile_control(h,d)
        with tempfile.TemporaryDirectory() as folder:
            path=Path(folder)/'methods.json'
            sync_methods([method],path)
            self.assertEqual(sync_methods([],path),[method])
            with self.assertRaises(ContractError):sync_methods([{**method,'value':'personal'}],path)
    def fixture(self):
        signature={'label':'籍贯','tag':'INPUT'}
        command={'target':{'browser':'edge','tab_id':'original','url':'https://example.test/form'},'module_id':'m','field_label':'籍贯','field_signature':signature,
                 'location':{'province':'湖南','city':'衡阳'},'adapter':'province_city_dialog_v1','source':'/contact/hometown','command_id':'old'}
        history={'command':command,'receipt':{'settled':True,'evidence':{'stage':'confirm_issued'}}}
        diagnostic={'command_id':'new','target':command['target'],'settled':True,'snapshot':{'observed_at':time.time(),'module_id':'m',
            'fields':[{'label':'籍贯','signature':signature,'value':'湖南省-衡阳市','value_readable':True}]},'evidence':{'controls':{'dialogs':[]}}}
        return history,diagnostic
    def test_late_dom_confirmation_preserves_original_receipt(self):
        h,d=self.fixture();old=copy.deepcopy(h)
        result=reconcile_control(h,d)
        self.assertEqual(result['verified_command_id'],'new');self.assertEqual(h,old)
        self.assertNotIn('value',result)
    def test_open_dialog_wrong_value_stale_or_wrong_target_never_learn(self):
        for mutate in [lambda h,d:d['evidence']['controls'].update(dialogs=[{}]),lambda h,d:h['receipt'].update(settled=False),
                       lambda h,d:d['snapshot']['fields'][0].update(value='北京'),lambda h,d:d['snapshot'].update(observed_at=0),
                       lambda h,d:d.update(target={})]:
            h,d=self.fixture();mutate(h,d)
            with self.assertRaises(ContractError):reconcile_control(h,d)
