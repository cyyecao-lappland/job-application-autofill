import copy
import unittest
from edge_form_graph.verification import carry_control_commits,retain_control_commits


class VerifiedControlTests(unittest.TestCase):
    def fixture(self):
        field={'id':'city','selector':'#city','signature':{'label':'期望城市'},'value':'上海市-上海市'}
        snapshot={'target':{'url':'https://example.test'},'module_id':'m','fields':[field]}
        command={'target':snapshot['target'],'module_id':'m','command_id':'c','field_selector':'#city',
                 'field_signature':field['signature'],'source':'/city','adapter':'province_city_dialog_v1',
                 'controlTarget':{'kind':'hierarchy','path':[{'level':'province','label':'上海市'},{'level':'city','label':'上海市'}]}}
        receipt={'settled':True,'status':'completed','evidence':{'committed':True,'verification':'match','actual':field['value']}}
        return snapshot,[{'command':command,'receipt':receipt}]

    def test_unchanged_verified_display_is_retained_but_never_saved(self):
        snapshot,history=self.fixture();carry_control_commits(snapshot,history,{'city':'上海'})
        self.assertEqual(snapshot['fields'][0]['verified_control']['command_id'],'c')
        self.assertNotIn('saved',snapshot['fields'][0]['verified_control'])

    def test_changed_source_value_or_unsettled_receipt_cannot_carry(self):
        for mode in ('source','value','settled'):
            snapshot,history=self.fixture();profile={'city':'上海'}
            if mode=='source':profile['city']='北京'
            if mode=='value':snapshot['fields'][0]['value']='北京市-北京市'
            if mode=='settled':history[0]['receipt']['settled']=False
            carry_control_commits(snapshot,history,profile)
            self.assertNotIn('verified_control',snapshot['fields'][0])

    def test_later_changed_value_is_a_conflict(self):
        snapshot,history=self.fixture();carry_control_commits(snapshot,history,{'city':'上海'})
        current=copy.deepcopy(snapshot);current['fields'][0].pop('verified_control');results={}
        retain_control_commits(snapshot,current,results)
        self.assertIn('verified_control',current['fields'][0])
        current['fields'][0]['value']='';retain_control_commits(snapshot,current,results)
        self.assertEqual(results['city']['status'],'conflict')
