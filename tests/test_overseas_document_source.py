import time
import unittest
from edge_form_graph.contracts import ContractError,compile_plan

def snapshot(overseas=True):
    fields=[{'id':'number','selector':'#number','label':'证件号','kind':'text','value':'','signature':{},'disabled':False}]
    if overseas:fields.append({'id':'travel','selector':'#travel','label':'有无出国（境）证件','kind':'checkbox','value':True,'signature':{}})
    return {'snapshot_id':'purpose-test','module_id':'politics','observed_at':time.time(),
            'target':{'browser':'edge','browser_id':'existing','tab_id':'existing','url':'http://example.test/form'},'fields':fields}

def proposal(source,overseas=False):
    return {'mappings':[{'field_id':'number','source':source,'transform':'identity','depends_on':[]}],
            'deferred':[{'field_id':'travel','reason':'outside test mapping'}] if overseas else []}

class OverseasSourceTests(unittest.TestCase):
    def test_identity_number_is_rejected_for_overseas_document(self):
        with self.assertRaisesRegex(ContractError,'overseas_document_requires_own_source'):
            compile_plan({'identity':{'identity_document_number':'identity-placeholder'}},snapshot(),proposal('/identity/identity_document_number'))

    def test_outside_overseas_scope_keeps_identity_mapping(self):
        operations,_=compile_plan({'identity':{'identity_document_number':'identity-placeholder'}},snapshot(False),proposal('/identity/identity_document_number'))
        self.assertEqual(operations[0]['value'],'identity-placeholder')

    def test_original_blank_uses_its_own_source(self):
        operations,_=compile_plan({'personal_answers':{'original_blank':''}},snapshot(),proposal('/personal_answers/original_blank',True))
        self.assertEqual(operations[0]['value'],'')
