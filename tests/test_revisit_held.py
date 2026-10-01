import copy
import json
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path
from edge_form_graph.scope_isolation import revisit_held
from edge_form_graph.contracts import ContractError


class RevisitTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.folder=Path(self.tmp.name);(self.folder/'writer').mkdir()
        self.values={'status':'incomplete_coverage','index':1,'command':None,'deadline':1,
            'manifest':{'program_inventory':True,'modules':[{'id':'m','save_scope':'s','snapshot':{'old':True}}]},
            'results':{'m':{'coverage_hold':'control_blocked','results':{'a':{'status':'written'}}}},
            'opened_modules':['m'],'recovery_attempts':{'m':2},'scopes':{}}

    def revisit(self):
        return revisit_held(self.values,(),self.folder,'m','user requested continued program repair',600)

    def test_preserves_source_state_and_prior_result_history(self):
        before=copy.deepcopy(self.values);update=self.revisit()
        self.assertEqual(before,self.values)
        self.assertEqual(update['index'],0)
        self.assertNotIn('opened_modules',update)
        self.assertNotIn('recovery_attempts',update)
        self.assertNotIn('coverage_hold',update['results']['m'])
        self.assertEqual(update['revisit_history'][0]['previous_result'],before['results']['m'])
        self.assertNotIn('snapshot',update['manifest']['modules'][0])
        self.assertIsNone(update['command'])

    def test_saved_scope_never_revisited(self):
        self.values['scopes']['s']='saved_confirmed'
        with self.assertRaises(ContractError):self.revisit()

    def test_refresh_scope_rereads_all_members_without_erasing_receipts(self):
        self.values['manifest']['modules'].insert(0, {'id': 'first', 'save_scope': 's'})
        self.values['results']['first'] = {'status': 'verified_draft', 'reviewed_revision': 2,
            'review': {'approved': True}, 'last_receipt': {'settled': True},
            'results': {'f': {'status': 'already_matched'}}}
        update = revisit_held(self.values, (), self.folder, 'm', 'fresh scope evidence', 600,
                              refresh_scope=True)
        self.assertEqual(update['index'], 0)
        self.assertIsNone(update['results']['first']['reviewed_revision'])
        self.assertEqual(update['results']['first']['last_receipt'], {'settled': True})
        self.assertEqual(update['revisit_history'][-1]['refreshed_scope_modules'], ['first', 'm'])

    def test_refresh_scope_does_not_replay_an_unknown_member(self):
        self.values['manifest']['modules'].insert(0, {'id': 'first', 'save_scope': 's'})
        self.values['results']['first'] = {'results': {'f': {'status': 'unknown'}}}
        with self.assertRaisesRegex(ContractError, 'reconciled_modules'):
            revisit_held(self.values, (), self.folder, 'm', 'fresh scope evidence', 600, refresh_scope=True)

    def test_refresh_scope_checks_child_recovery_status_and_original_receipt(self):
        self.values['manifest']['modules'].insert(0, {'id': 'first', 'save_scope': 's'})
        for result in [
                {'status': 'needs_reconciliation', 'results': {}},
                {'status': 'recovery_blocked', 'results': {}},
                {'status': 'filled_pending_review', 'results': {},
                 'last_receipt': {'results': [{'id': 'f', 'status': 'unknown'}]}},
                {'status': 'filled_pending_review', 'results': {},
                 'last_receipt': {'results': [{'id': 'f', 'status': 'conflict'}]}}]:
            self.values['results']['first'] = result
            with self.assertRaisesRegex(ContractError, 'reconciled_modules'):
                revisit_held(self.values, (), self.folder, 'm', 'fresh scope evidence', 600, refresh_scope=True)

    def test_undispatched_next_observation_can_yield_to_a_targeted_repair_under_lock(self):
        self.values.update(status='awaiting_observation',command={'kind':'observe','command_id':'read-next'})
        with self.assertRaises(ContractError):
            revisit_held(self.values,('observe',),self.folder,'m','repair',600)
        update=revisit_held(self.values,('observe',),self.folder,'m','repair',600,writer_lock_held=True)
        self.assertEqual(update['revisit_history'][-1]['superseded_undispatched_observe'],self.values['command'])
        self.assertIsNone(update['command'])
        (self.folder/'writer/read-next.json').write_text('{}')
        with self.assertRaises(ContractError):
            revisit_held(self.values,('observe',),self.folder,'m','repair',600,writer_lock_held=True)

    def test_undispatched_writes_and_other_tasks_cannot_be_superseded(self):
        for kind,node in [('fill','observe'),('save_scope','observe'),('observe','read_diagnostic')]:
            self.values.update(status='awaiting_observation',command={'kind':kind,'command_id':'next'})
            with self.assertRaises(ContractError):
                revisit_held(self.values,(node,),self.folder,'m','repair',600,writer_lock_held=True)

    def test_unknown_save_never_revisited(self):
        (self.folder/'writer/save.json').write_text(json.dumps({'kind':'save_scope','receipt':{'settled':True,'status':'unconfirmed'}}))
        with self.assertRaises(ContractError):self.revisit()

    def test_exact_settled_save_reconciliation_resolves_old_save_only(self):
        original={'command_id':'old','kind':'save_scope','started_at':1,'receipt':{'target':'same','settled':True,'status':'unconfirmed'}}
        confirmation={'command_id':'confirmation','kind':'reconcile_save','started_at':2,
                      'receipt':{'target':'same','settled':True,'status':'saved','evidence':{'save_confirmed':True}}}
        (self.folder/'writer/old.json').write_text(json.dumps(original))
        (self.folder/'writer/new.json').write_text(json.dumps(confirmation))
        with patch('edge_form_graph.application_cli.reconciliation_parent',return_value='old'):
            self.assertEqual(self.revisit()['index'],0)
        with patch('edge_form_graph.application_cli.reconciliation_parent',return_value='other'):
            with self.assertRaises(ContractError):self.revisit()
        confirmation['receipt']['target']='different'
        (self.folder/'writer/new.json').write_text(json.dumps(confirmation))
        with patch('edge_form_graph.application_cli.reconciliation_parent',return_value='old'):
            with self.assertRaises(ContractError):self.revisit()

    def test_pending_call_never_revisited(self):
        (self.folder/'writer/fill.json').write_text(json.dumps({'kind':'fill','receipt':{'settled':False}}))
        with self.assertRaises(ContractError):self.revisit()

    def test_added_record_requires_reconciliation(self):
        (self.folder/'writer/add.json').write_text(json.dumps({'kind':'add_module_record','receipt':{'settled':True,'status':'unconfirmed'},'instrumentation':[{'method':'click','status':'returned'}]}))
        with self.assertRaises(ContractError):self.revisit()

    def test_completed_add_without_editor_evidence_is_still_blocked(self):
        (self.folder/'writer/add.json').write_text(json.dumps({'kind':'add_module_record','receipt':{'settled':True,'status':'completed'},'instrumentation':[{'method':'click','status':'returned'}]}))
        with self.assertRaises(ContractError):self.revisit()

    def test_autosave_and_unproven_save_cannot_be_bypassed(self):
        for kind,status in [('verify_autosave','unconfirmed'),('save_scope','saved')]:
            (self.folder/'writer/save.json').write_text(json.dumps({'kind':kind,'receipt':{'settled':True,'status':status}}))
            with self.assertRaises(ContractError):self.revisit()
