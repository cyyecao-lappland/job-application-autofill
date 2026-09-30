import copy
import unittest
import test_preflight as baseline

class BatchIsolationTests(unittest.TestCase):
    setUp = baseline.PreflightTests.setUp
    result = baseline.PreflightTests.result
    def add_deferred(self):
        self.plan['execution']['dispatchIds']=['F1']
        self.plan['operations'].append(dict(id='F2',action='fill',label='Date',value='2026-09',before='',
            kind='date',source='facts.md',anchor=self.op['anchor']))

    def test_unready_other_field_does_not_block(self):
        self.add_deferred()
        r=self.result()
        self.assertTrue(r['batchReady'],r)
        self.assertEqual(r['deferredIds'],['F2'])
        self.assertEqual(self.plan['operations'][1]['action'],'fill')

    def test_dispatched_unready_field_blocks_only_batch(self):
        self.add_deferred();self.plan['execution']['dispatchIds'].append('F2')
        r=self.result()
        self.assertTrue(r['planReady'],r)
        self.assertFalse(r['batchReady'])

    def test_deferred_invalid_kind_and_source_still_invalidate_plan(self):
        self.add_deferred();self.plan['operations'][1]['kind']='invented'
        self.assertFalse(self.result()['planReady'])
        self.plan['operations'][1]['kind']='date';del self.plan['operations'][1]['source']
        self.assertFalse(self.result()['planReady'])

    def test_deferred_can_become_ready(self):
        self.add_deferred()
        op=self.plan['operations'][1]
        op.update(locator='observed date',family='date',path='dom',methodEvidence='real operation',savedMethodEvidence='saved/read')
        self.snapshot['fields'].append(dict(label='Date',value='',recordIndex=1))
        self.plan['execution']['probe']['operations'].append(dict(id='F2',count=1,kind='date',value='',anchorMatched=True))
        self.plan['execution']['dispatchIds']=['F2']
        self.assertTrue(self.result()['batchReady'],self.result())

    def test_pending_call_blocks(self):
        self.plan['execution']['pendingCall']={'callId':'unreturned'}
        self.assertFalse(self.result()['batchReady'])

    def test_record_dependency_requires_live_identity(self):
        creation={**self.op,'id':'C1','action':'add-record','kind':'record','label':'Project','value':'A','templateId':'T1'}
        self.op.update(dependsOn='C1',templateId='T1')
        self.plan['operations'].insert(0,creation)
        self.snapshot={'fields':[],'templates':[{'id':'T1','source':'observed','addLocator':'add','fields':[{'label':'Description','kind':'text'}]}]}
        self.plan['execution']['dispatchIds']=['F1']
        self.assertFalse(self.result()['batchReady'])
        self.plan['execution']['results']={'C1':{'status':'written','evidence':'page','recordRef':'r-1'}}
        self.plan['execution']['probe']['operations'][0]['recordRef']='r-2'
        self.assertFalse(self.result()['batchReady'])
        self.plan['execution']['probe']['operations'][0]['recordRef']='r-1'
        self.assertTrue(self.result()['batchReady'],self.result())

if __name__ == '__main__': unittest.main()
