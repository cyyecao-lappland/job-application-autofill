import copy
import json
import tempfile
import unittest
from pathlib import Path

from edge_form_graph.contracts import ContractError
from edge_form_graph.observation_completion import recover_observation, audited_observation, persist_observation_audit
from edge_form_graph.scope_isolation import assert_settled_history


class ObservationCompletionTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.folder=Path(self.temp.name);(self.folder/'writer').mkdir()
        self.command={'command_id':'old','kind':'observe','target':{'tab_id':'same'}}
        self.values={'status':'awaiting_observation','command':self.command}
        self.journal={'command_id':'old','kind':'observe','status':'pending',
            'instrumentation':[{'stage':'module_readback','method':'evaluate','status':'returned','duration_ms':4}]}
        self.path=self.folder/'writer/old.json';self.path.write_text(json.dumps(self.journal))

    def recover(self):
        return recover_observation(self.values,('observe',),self.folder,'finished read failed locally',writer_lock_held=True)

    def test_audit_creation_is_idempotent_and_never_rewrites_existing_proof(self):
        proof,_=self.recover()
        persist_observation_audit(self.folder,proof)
        path=self.folder/'observation-audits/old.json'
        original=path.read_bytes()
        retry,_=self.recover()
        retry['basis']='retry after local restart'
        self.assertEqual(persist_observation_audit(self.folder,retry),proof)
        self.assertEqual(path.read_bytes(),original)
        retry['original_command']['target']={'tab_id':'other'}
        with self.assertRaisesRegex(ContractError,'exists_but_differs'):
            persist_observation_audit(self.folder,retry)
        self.assertEqual(path.read_bytes(),original)

    def test_only_completed_read_transport_is_certified_not_values(self):
        original=self.path.read_text();proof,receipt=self.recover()
        self.assertTrue(receipt['settled']);self.assertEqual(receipt['status'],'unconfirmed')
        self.assertIsNone(receipt['snapshot']);self.assertEqual(receipt['results'],[])
        self.assertFalse(proof['value_verified']);self.assertFalse(proof['write_verified'])
        self.assertEqual(original,self.path.read_text())
        (self.folder/'observation-audits').mkdir()
        (self.folder/'observation-audits/old.json').write_text(json.dumps(proof))
        self.assertTrue(audited_observation(self.folder,self.journal))
        assert_settled_history(self.values,self.folder)

    def test_missing_or_changed_instrumentation_does_not_match_audit(self):
        self.assertFalse(audited_observation(self.folder,self.journal))
        proof,_=self.recover();(self.folder/'observation-audits').mkdir()
        (self.folder/'observation-audits/old.json').write_text(json.dumps(proof))
        changed=copy.deepcopy(self.journal);changed['instrumentation'][0]['duration_ms']=5
        self.assertFalse(audited_observation(self.folder,changed))

    def test_pending_failed_write_or_other_reader_never_qualifies(self):
        for changes in [{'status':'pending'},{'status':'failed'},{'method':'click'},
                        {'method':'fill'},{'stage':'popup_search'}]:
            journal=copy.deepcopy(self.journal);journal['instrumentation'][0].update(changes)
            self.path.write_text(json.dumps(journal))
            with self.assertRaisesRegex(ContractError,'known_reads_returned'):self.recover()
        self.path.write_text(json.dumps({**self.journal,'instrumentation':[]}))
        with self.assertRaises(ContractError):self.recover()

    def test_completed_receipt_and_other_commands_are_not_replaced(self):
        self.path.write_text(json.dumps({**self.journal,'receipt':{'settled':True}}))
        with self.assertRaises(ContractError):self.recover()
        self.values['command']={**self.command,'kind':'save_scope'}
        with self.assertRaises(ContractError):self.recover()
        with self.assertRaises(ContractError):
            recover_observation(self.values,(),self.folder,'basis',writer_lock_held=True)
