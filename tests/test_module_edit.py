import copy
import time
import unittest
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command
from edge_form_graph.application import build_application, application_state, active_command
from tests.test_application import manifest, answer
from tests.test_graph import Model


class ModuleEditTests(unittest.TestCase):
    def stopped(self, graph, cfg, edits):
        value=application_state({},manifest(),allow_save=True)
        value['status']='control_committed'
        value['control_diagnostics']={'settled':True,'status':'completed','target':value['manifest']['target'],
            'snapshot':{'observed_at':time.time(),'module_id':'a','fields':[]},
            'evidence':{'controls':{'dialogs':[]},'module_view':{'edit_controls':edits}}}
        graph.update_state(cfg,value,as_node='execute_module_edit')

    def test_open_edit_is_not_module_save(self):
        with SqliteSaver.from_conn_string(':memory:') as saver:
            graph=build_application(Model(),saver);cfg={'configurable':{'thread_id':'test'}}
            self.stopped(graph,cfg,[{'selector':'.edit','label':'编辑'}])
            graph.invoke(Command(goto='prepare_module_edit'),cfg)
            command=active_command(graph.get_state(cfg))
            self.assertEqual(command['kind'],'edit_module')
            graph.invoke(Command(resume=answer(command)),cfg)
            state=graph.get_state(cfg)
            self.assertEqual(state.values['status'],'awaiting_observation')
            self.assertEqual(active_command(state)['kind'],'observe')
            self.assertEqual(state.values['scopes'],{})
            self.assertTrue(state.next)

    def test_arbitrary_start_on_collapsed_card_routes_to_explicit_editor_open(self):
        with SqliteSaver.from_conn_string(':memory:') as saver:
            graph=build_application(Model(),saver);cfg={'configurable':{'thread_id':'collapsed'}}
            m=manifest();m['modules'][0]['capture']={'controlDiagnostics':True}
            graph.invoke(application_state({},m,allow_save=True),cfg)
            command=active_command(graph.get_state(cfg,subgraphs=True))
            snap=answer(command)['snapshot'];snap['fields']=[]
            receipt={'command_id':command['command_id'],'kind':'observe','target':command['target'],
                'settled':True,'status':'completed','results':[],'snapshot':snap,
                'evidence':{'controls':{'dialogs':[]},'module_view':{
                    'edit_controls':[{'selector':'.edit','label':'编辑'}]}}}
            graph.invoke(Command(resume=receipt),cfg)
            state=graph.get_state(cfg)
            self.assertEqual(state.values['status'],'module_edit_not_ready')
            graph.invoke(Command(goto='prepare_module_edit'),cfg)
            self.assertEqual(active_command(graph.get_state(cfg))['kind'],'edit_module')

    def test_missing_edit_stops_without_poisoning_diagnostic_route(self):
        with SqliteSaver.from_conn_string(':memory:') as saver:
            graph=build_application(Model(),saver);cfg={'configurable':{'thread_id':'test'}}
            self.stopped(graph,cfg,[])
            graph.invoke(Command(goto='prepare_module_edit'),cfg)
            self.assertFalse(graph.get_state(cfg).next)
            self.assertIsNone(active_command(graph.get_state(cfg)))
            graph.invoke(Command(goto='prepare_diagnostic'),cfg)
            self.assertEqual(active_command(graph.get_state(cfg))['kind'],'observe')

    def test_preparation_recovery_rejects_browser_command(self):
        from pathlib import Path
        from edge_form_graph.preparation_recovery import recover_preparation
        from edge_form_graph.contracts import ContractError
        with SqliteSaver.from_conn_string(':memory:') as saver:
            graph=build_application(Model(),saver);cfg={'configurable':{'thread_id':'test'}}
            self.stopped(graph,cfg,[{'selector':'.edit','label':'编辑'}])
            graph.invoke(Command(goto='prepare_module_edit'),cfg)
            with self.assertRaises(ContractError):recover_preparation(saver,cfg,Path('.'))
