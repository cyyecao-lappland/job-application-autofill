import copy
import json
import tempfile
import time
import unittest
from pathlib import Path
from edge_form_graph.undispatched_add import renew_undispatched_add
from edge_form_graph.contracts import ContractError
from types import SimpleNamespace
from unittest.mock import patch
from langgraph.types import Command
from edge_form_graph.storage import SqliteSaver
from edge_form_graph.application import application_state, build_application, active_command
from edge_form_graph.application_cli import operate
from tests.test_application import manifest, answer
from tests.test_graph import Model


class UndispatchedAddTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.folder=Path(self.temp.name);(self.folder/'writer').mkdir()
        self.target={'browser_id':'browser','tab_id':'tab','url':'https://example.test/form'}
        self.command={'command_id':'add','kind':'add_module_record','target':self.target,
            'collection_selector':'#collection','record_selector':'.record','add_control_selector':'.add',
            'add_label':'添加实习经历','expected_record_count':0,'deadline':1}
        self.values={'status':'awaiting_module_add','command':self.command,'manifest':{'target':self.target},'deadline':1}
        self.section={'selector':'#collection','record_selector':'.record','add_selector':'.add',
            'add_label':'添加实习经历','records':[],'controls':0}
        self.inventory={'target':self.target,'url':self.target['url'],'observed_at':time.time(),
                        'framework':{'sections':[self.section]}}

    def renew(self):
        return renew_undispatched_add(self.values,('add_module_record',),self.folder,self.inventory,
                                     'continue confirmed original empty collection',600,writer_lock_held=True)

    def test_keeps_original_command_and_baseline_and_archives_old_deadline(self):
        result=self.renew()
        self.assertEqual(result['command']['command_id'],'add')
        self.assertEqual(result['command']['expected_record_count'],0)
        self.assertGreater(result['command']['deadline'],time.time())
        self.assertEqual(result['budget_history'][0]['previous_command']['deadline'],1)

    def test_changed_count_selector_or_controls_rejects_without_replanning_add(self):
        for key,value in [('records',[{'selector':'#new'}]),('record_selector','.different'),('controls',1)]:
            original=copy.deepcopy(self.section);self.section[key]=value
            with self.assertRaises(ContractError):self.renew()
            self.section.clear();self.section.update(original)

    def test_any_existing_journal_and_other_tab_reject_even_if_settled(self):
        path=self.folder/'writer/add.json';path.write_text(json.dumps({'receipt':{'settled':True}}))
        with self.assertRaises(ContractError):self.renew()
        path.unlink();self.inventory['target']={**self.target,'tab_id':'other'}
        with self.assertRaises(ContractError):self.renew()

    def test_stale_inventory_cannot_renew(self):
        self.inventory['observed_at']=time.time()-121
        with self.assertRaises(ContractError):self.renew()

    def test_cli_renews_real_graph_interrupt_and_resumes_original_add(self):
        cfg={'configurable':{'thread_id':'application'},'recursion_limit':100}
        data=manifest(False)
        data['modules']=data['modules'][:1]
        data['save_scopes']=data['save_scopes'][:1]
        data['modules'][0].update(open_mode='add',collection_selector='#collection',record_selector='.record',
            add_control_selector='.add',expected_record_count=0,add_label='添加实习经历')
        inventory={**self.inventory,'target':data['target'],'url':data['target']['url']}
        path=self.folder/'inventory.json';path.write_text(json.dumps(inventory))
        db=str(self.folder/'application.sqlite');model=Model()
        with SqliteSaver.from_conn_string(db) as saver:
            graph=build_application(model,saver)
            graph.invoke(application_state({'school':'示例大学'},data),cfg)
            state=graph.get_state(cfg,subgraphs=True)
            original=active_command(state)
            self.assertEqual(state.next,('add_module_record',))
            expired={**original,'deadline':1}
            graph.update_state(cfg,{'command':expired,'deadline':1},as_node='enter')
        args=SimpleNamespace(action='renew-undispatched-add',run_dir=str(self.folder),inventory=str(path),
                             basis='continue original undispatched empty collection',renew_seconds=600)
        with patch('edge_form_graph.application_cli.review_model',return_value=model):
            summary=operate(args)
        self.assertEqual(summary['pending_kind'],'add_module_record')
        with SqliteSaver.from_conn_string(db) as saver:
            graph=build_application(model,saver)
            resumed=active_command(graph.get_state(cfg,subgraphs=True))
            self.assertEqual(resumed['command_id'],original['command_id'])
            self.assertGreater(resumed['deadline'],time.time())
            graph.invoke(Command(resume=answer(resumed)),cfg)
            current=graph.get_state(cfg,subgraphs=True)
            self.assertEqual(active_command(current)['kind'],'observe')
            self.assertEqual(current.values['module_add_history'][0]['command']['command_id'],original['command_id'])
