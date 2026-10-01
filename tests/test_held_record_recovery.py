import copy
import json
import tempfile
import time
import unittest
from pathlib import Path

from edge_form_graph.contracts import ContractError
from edge_form_graph.held_record_recovery import rebind_held_records
from edge_form_graph.page_planner import plan_page


class HeldRecordRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.folder=Path(self.temp.name);(self.folder/'writer').mkdir()
        target={'browser':'edge','browser_id':'b','tab_id':'t','url':'https://example.test/form'}
        profile={'education':[{'record_id':'one','school_name':'A','start_date':'2021-03'},
                              {'record_id':'two','school_name':'B','start_date':'2019-09'}]}
        rows=[{'selector':'#row'+str(i),'snapshot':{'fields':[
            {'id':'school'+str(i),'label':'学校名称','kind':'text','value':name},
            {'id':'year'+str(i),'label':'开始 年','range_endpoint':'start','date_part':'year','value':year},
            {'id':'month'+str(i),'label':'开始 月','range_endpoint':'start','date_part':'month','value':month}]}}
            for i,(name,year,month) in enumerate([('A','2021','3'),('B','2019','9')])]
        self.inventory={'url':target['url'],'target':target,'observed_at':time.time(),
            'framework':{'family':'moka','sections':[{'id':'education','label':'教育背景','selector':'#education',
                'records':rows,'snapshot':{'fields':[]}}]}}
        manifest=plan_page(profile,self.inventory,target)
        for module in manifest['modules']:
            module.pop('mapping_context');module['hold_reason']='existing_record_identity_unresolved'
        self.values={'manifest':manifest,'profile':profile,'status':'incomplete_coverage','index':2,
            'results':{m['id']:{'coverage_hold':'existing_record_identity_unresolved'} for m in manifest['modules']},
            'command':None,'scopes':{},'deadline':1,'allow_save':True}

    def rebind(self):
        return rebind_held_records(self.values,(),self.folder,self.inventory,'split date identity repair',600,writer_lock_held=True)

    def test_rebind_is_exact_and_preserves_the_original_record_count_and_history(self):
        before=copy.deepcopy(self.values);update=self.rebind()
        self.assertEqual(self.values,before)
        self.assertEqual(update['index'],0)
        self.assertEqual([m['mapping_context']['record_id'] for m in update['manifest']['modules']],['one','two'])
        self.assertEqual(len(update['manifest']['modules']),2)
        self.assertTrue(all(not m.get('open_mode') for m in update['manifest']['modules']))
        self.assertTrue(all(m.get('record_rebind_expected') for m in update['manifest']['modules']))
        self.assertEqual(len(update['inventory_history'][-1]['changes']),2)
        self.assertNotIn('scopes',update)

    def test_stale_different_tab_and_pending_commands_are_rejected(self):
        for change in ['stale','tab','command']:
            inv=copy.deepcopy(self.inventory);values=copy.deepcopy(self.values)
            if change=='stale':inv['observed_at']=time.time()-121
            if change=='tab':inv['target']['tab_id']='other'
            if change=='command':values['command']={'kind':'fill'}
            with self.assertRaises(ContractError):
                rebind_held_records(values,(),self.folder,inv,'repair',600,writer_lock_held=True)

    def test_ambiguous_existing_rows_are_not_assigned_by_position(self):
        for row in self.inventory['framework']['sections'][0]['records']:
            row['snapshot']['fields'][0]['value']='Other'
        with self.assertRaisesRegex(ContractError,'no_exact_held_record_identity_match'):self.rebind()

    def test_already_bound_record_is_not_allocated_again(self):
        self.values['manifest']['modules'][1]['mapping_context']={'record_collection':'/education','record_id':'one'}
        self.values['manifest']['modules'][1].pop('hold_reason')
        with self.assertRaisesRegex(ContractError,'no_exact_held_record_identity_match'):self.rebind()

    def test_completed_or_unknown_writes_are_not_erased(self):
        self.values['scopes']['education']='saved_confirmed'
        with self.assertRaisesRegex(ContractError,'no_exact_held_record_identity_match'):self.rebind()
        self.values['scopes']={}
        (self.folder/'writer/pending.json').write_text(json.dumps({'command_id':'pending','kind':'fill'}))
        with self.assertRaisesRegex(ContractError,'settled_calls'):self.rebind()

    def test_fresh_add_controls_do_not_create_new_records(self):
        self.inventory['framework']['sections'][0].update(record_selector=':scope > .row',add_selector='button',add_label='添加')
        self.values['profile']['education'].append({'record_id':'three','school_name':'C'})
        update=self.rebind()
        self.assertEqual(len(update['manifest']['modules']),2)
        self.assertFalse(any(m.get('open_mode') for m in update['manifest']['modules']))

    def parsed_add(self,clicked=False):
        module=self.values['manifest']['modules'][0]
        module.pop('hold_reason')
        module.update(mapping_context={'record_collection':'/education','record_id':'one','module_type':'education'},
                      selector='#old-add-location',open_mode='add',inline_repeater=True,
                      collection_selector='#education',expected_record_count=0)
        self.values['results'][module['id']]={'coverage_hold':'module_add_blocked'}
        receipt={'settled':True,'status':'unconfirmed','results':[],
                 'evidence':{'reason':'open_record_count_changed'}}
        steps=[{'method':'count','status':'returned'}]
        if clicked:steps.append({'method':'click','status':'returned'})
        journal={'command_id':'add-one','kind':'add_module_record','status':'unconfirmed',
                 'receipt':receipt,'instrumentation':steps}
        (self.folder/'writer/add-one.json').write_text(json.dumps(journal))
        self.values['module_add_history']=[{'command':{'command_id':'add-one','module_id':module['id']},'receipt':receipt}]
        return journal

    def test_auto_parsed_record_replaces_only_an_add_stopped_before_any_click(self):
        original=self.parsed_add()
        update=self.rebind()
        module=update['manifest']['modules'][0]
        self.assertEqual(module['selector'],'#row0')
        self.assertEqual(module['mapping_context']['record_id'],'one')
        self.assertNotIn('open_mode',module)
        self.assertEqual(json.loads((self.folder/'writer/add-one.json').read_text()),original)
        self.assertTrue(module['record_rebind_expected'])

    def test_clicked_add_is_never_rebound_as_an_auto_parsed_record(self):
        self.parsed_add(clicked=True)
        with self.assertRaisesRegex(ContractError,'add_reconciliation'):self.rebind()

    def test_only_rebound_identity_gaps_are_removed_from_the_real_coverage_result(self):
        from edge_form_graph.page_planner import coverage_gaps
        self.values['manifest']['coverage']['gaps']=[
            {'scope':'education','selector':'#row0','reason':'existing_record_identity_unresolved'},
            {'scope':'education','selector':'#row1','reason':'existing_record_identity_unresolved'},
            {'scope':'other','selector':'#row0','reason':'existing_record_identity_unresolved'},
            {'scope':'education','selector':'#row0','reason':'existing_identity_must_be_resolved_before_add'}]
        update=self.rebind()
        results={m['id']:{'status':'verified_draft'} for m in update['manifest']['modules']}
        self.assertEqual(coverage_gaps(update['manifest'],results),[
            {'scope':'other','selector':'#row0','reason':'existing_record_identity_unresolved'},
            {'scope':'education','selector':'#row0','reason':'existing_identity_must_be_resolved_before_add'}])
