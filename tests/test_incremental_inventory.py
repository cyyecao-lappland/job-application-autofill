import copy
import tempfile
import time
import unittest
from pathlib import Path
from edge_form_graph.incremental_inventory import extend_empty_collections
from edge_form_graph.page_planner import plan_page
from edge_form_graph.contracts import ContractError
from tests.test_application import manifest


class IncrementalInventoryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.folder=Path(self.temp.name);(self.folder/'writer').mkdir()
        self.values={'status':'incomplete_coverage','index':2,'command':None,'deadline':1,
            'profile':{'education':[{'record_id':'master','school_name':'A'},{'record_id':'bachelor','school_name':'B'}]},
            'manifest':{**manifest(False),'program_inventory':True},'allow_save':True,
            'scopes':{'g':'saved_confirmed'},'results':{'a':{'status':'verified_draft'}}}
        self.inventory={'url':self.values['manifest']['target']['url'],'observed_at':time.time(),
            'framework':{'family':'sf','sections':[{'id':'education','selector':'#education','label':'教育信息',
                'empty_form_collection':True,'add_selector':'.new-one-btn','add_label':'+添加教育背景',
                'snapshot':{'fields':[]},'records':[]}]}}

    def extend(self):
        return extend_empty_collections(self.values,(),self.folder,self.inventory,'program repair',600,writer_lock_held=True)

    def test_only_first_record_is_planned_and_existing_history_is_preserved(self):
        before=copy.deepcopy(self.values);update=self.extend()
        self.assertEqual(self.values,before);self.assertEqual(update['index'],2)
        module=update['manifest']['modules'][-1]
        self.assertEqual(module['mapping_context']['record_id'],'master')
        self.assertEqual(module['expected_record_count'],0)
        self.assertEqual(module['record_selector'],'form')
        self.assertNotIn('scopes',update);self.assertNotIn('results',update)
        self.assertEqual(update['manifest']['coverage']['gaps'][0]['record_id'],'bachelor')
    def test_observed_feishu_empty_repeater_extends_original_without_restarting(self):
        self.inventory['target']=copy.deepcopy(self.values['manifest']['target'])
        f=self.inventory['framework'];f['family']='feishu'
        s=f['sections'][0];s.pop('empty_form_collection')
        s.update(record_selector=':scope > .component > .card',add_label='添加')
        update=self.extend()
        self.assertEqual(update['index'],2)
        self.assertEqual([m['expected_record_count'] for m in update['manifest']['modules'][2:]],[0,1])
        self.assertTrue(all(m['inline_repeater'] for m in update['manifest']['modules'][2:]))
        self.assertNotIn('results',update)
        self.inventory['target']['tab_id']='other'
        with self.assertRaisesRegex(ContractError,'same_tab'):self.extend()

    def test_existing_scope_and_stale_inventory_are_not_extended(self):
        self.values['manifest']['save_scopes'][0]['selector']='#education'
        with self.assertRaisesRegex(ContractError,'no_new_empty_collection'):self.extend()
        self.inventory['observed_at']=time.time()-121
        with self.assertRaisesRegex(ContractError,'fresh_program_inventory'):self.extend()

    def test_pending_writes_or_dispatched_read_are_not_superseded(self):
        for kind in ['fill','save_scope','add_module_record','observe']:
            self.values.update(status='awaiting_observation',command={'kind':kind,'command_id':'pending'})
            if kind=='observe':(self.folder/'writer/pending.json').write_text('{}')
            with self.assertRaises(ContractError):
                extend_empty_collections(self.values,('observe',),self.folder,self.inventory,'repair',600,writer_lock_held=True)

    def test_existing_editor_never_gets_empty_collection_add(self):
        section=self.inventory['framework']['sections'][0]
        section['snapshot']['fields']=[{'label':'学校','value':'A','kind':'text'}]
        plan=plan_page(self.values['profile'],self.inventory,self.values['manifest']['target'])
        self.assertFalse(any(m.get('discovered_empty_collection') for m in plan['modules']))

    def test_midrun_budget_exhaustion_does_not_skip_existing_modules(self):
        self.values.update(status='budget_exhausted',index=0)
        with self.assertRaisesRegex(ContractError,'must_not_skip_unprocessed_modules'):self.extend()
