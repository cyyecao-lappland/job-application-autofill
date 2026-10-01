import tempfile
import unittest
from pathlib import Path
from edge_form_graph.knowledge import KnowledgeStore
from edge_form_graph.confirmed_field_policies import preserved_field_reason
from edge_form_graph.contracts import compile_plan, ContractError
from tests.test_graph import snapshot as base_snapshot

class BoundProjectFastPathTests(unittest.TestCase):
    def fixture(self):
        profile={'projects':[{'record_id':'other','name':'Other'},
            {'record_id':'wanted','name':'Target','role':'Engineer','description':'Confirmed work',
             'url':None,'start_date':'2024-09-01','end_date':None}]}
        snapshot={**base_snapshot(),'module_id':'project','module_label':'项目经历',
            'mapping_context':{'record_collection':'/projects','record_id':'wanted'},
            'fields':[{**base_snapshot()['fields'][0],'id':str(i),'selector':'#f'+str(i),'kind':'text','label':label,'value':''}
                      for i,label in enumerate(['项目名称','项目角色','描述','项目链接'])]}
        return profile,snapshot
    def test_bound_known_fields_have_a_plan_before_any_model_call(self):
        profile,snapshot=self.fixture()
        with tempfile.TemporaryDirectory() as folder:
            store=KnowledgeStore(Path(folder)/'knowledge.json')
            plan=store.known_plan(profile,snapshot)
            self.assertEqual([m['source'] for m in plan['mappings']],
                ['/projects/1/name','/projects/1/role','/projects/1/description'])
            self.assertEqual(preserved_field_reason(profile,snapshot,snapshot['fields'][3]),
                'missing: canonical_project_url_unavailable')
            self.assertFalse(store._read()['field_map'])
            snapshot['mapping_context']['record_id']='missing'
            self.assertEqual(store.known_plan(profile,snapshot)['mappings'],[])
    def test_end_date_never_accepts_start_date_as_a_fallback(self):
        profile,snapshot=self.fixture()
        field={**base_snapshot()['fields'][0],'id':'end','selector':'#end','kind':'text','label':'结束时间','value':'',
               'control_pattern':'ud_date_pair','range_endpoint':'end'}
        snapshot['fields']=[field]
        with self.assertRaisesRegex(ContractError,'date_endpoint_source_mismatch'):
            compile_plan(profile,snapshot,{'mappings':[{'field_id':'end','source':'/projects/1/start_date',
                'transform':'year_month','depends_on':[]}],'deferred':[]})
        self.assertEqual(preserved_field_reason(profile,snapshot,field),'missing: canonical_date_endpoint_unavailable')
