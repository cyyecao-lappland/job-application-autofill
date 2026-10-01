import copy
import json
import tempfile
import unittest
from pathlib import Path
from edge_form_graph.local_call_audit import audit_local_failure, audited_completion
from edge_form_graph.contracts import ContractError, file_matches
from edge_form_graph.application_cli import recovered_fill_receipt

class CallAuditTests(unittest.TestCase):
    def test_native_readonly_search_timeout_proves_only_completion(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);v,j=self.fixture(p)
            r=j['receipt'];r.update(target={'browser_id':'edge-cdp:http://127.0.0.1:9333'},evidence={'executor':'playwright'})
            j['last_error']={'kind':'TimeoutError'}
            j['instrumentation']=[{'field_id':'city','method':'fill','stage':'popup_search','status':'failed','duration_ms':20,
                'error':{'code':'timeout','message':'locator.fill: Timeout 20ms exceeded.\nCall log:\nlocator resolved to <input readonly="readonly"/>'}}]
            (p/'writer/old.json').write_text(json.dumps(j))
            updates,proof=audit_local_failure(v,p)
            result=updates['results']['m']['last_receipt']
            self.assertTrue(result['settled']);self.assertEqual(result['status'],'unknown')
            self.assertEqual(result['results'][0]['status'],'unknown');self.assertFalse(proof['value_verified'])
            j['last_error']['kind']='Error';(p/'writer/old.json').write_text(json.dumps(j))
            with self.assertRaises(ContractError):audit_local_failure(v,p)

    def test_legacy_readonly_audit_schema_without_write_verified_is_accepted(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);v,j=self.fixture(p)
            r=j['receipt'];r.update(target={'browser_id':'edge-cdp:http://127.0.0.1:9333'},evidence={'executor':'playwright'})
            j['last_error']={'kind':'TimeoutError'}
            j['instrumentation']=[{'field_id':'city','method':'fill','stage':'popup_search','status':'failed','duration_ms':20,
                'error':{'code':'timeout','message':'locator.fill: Timeout 20ms exceeded.\\nCall log:\\nlocator resolved to <input readonly="readonly"/>'}}]
            (p/'writer/old.json').write_text(json.dumps(j))
            _,proof=audit_local_failure(v,p)
            proof.pop('write_verified')  # historical SF637c schema
            audit_dir=p/'local-call-audits';audit_dir.mkdir()
            audit_path=audit_dir/'old.json';audit_path.write_text(json.dumps(proof))
            self.assertTrue(audited_completion(p,j))
            proof['write_verified']=True;audit_path.write_text(json.dumps(proof))
            self.assertFalse(audited_completion(p,j))

    def click_timeout_fixture(self, folder, *, prior_operation=False):
        v,j=self.fixture(folder)
        receipt=j['receipt'];receipt.update(target={'browser_id':'edge-cdp:http://127.0.0.1:9333'},
            evidence={'executor':'playwright'},results=[{'id':'choice','status':'unknown','reason':'browser_call_failed'}])
        message=('locator.click: Timeout 7166ms exceeded.\nCall log:\n'
                 'locator resolved to <div class="ant-select-item-option">target</div>\n'
                 '- attempting click')
        events=[]
        if prior_operation:
            events.append({'stage':'module_readback','method':'evaluate','status':'returned',
                           'operation':1,'field_id':'earlier'})
        events.extend([
            {'stage':'popup_select','method':'click','status':'failed','operation':2 if prior_operation else 1,
             'field_id':'choice','duration_ms':7167,'error':{'code':'timeout','message':message}},
            {'stage':'popup_select','method':'press','status':'returned','operation':2 if prior_operation else 1,
             'field_id':'choice','duration_ms':10},
            {'stage':'popup_select','method':'click','status':'returned','operation':2 if prior_operation else 1,
             'field_id':'choice','duration_ms':35},
            {'stage':'popup_select','method':'evaluate','status':'returned','operation':2 if prior_operation else 1,
             'field_id':'choice','duration_ms':4},
        ])
        j['instrumentation']=events
        j['last_error']={'kind':'TimeoutError','phase':'action_or_readback','code':'browser_call_failed',
            'sanitized':{'code':'timeout','message':message}}
        (folder/'writer/old.json').write_text(json.dumps(j))
        return v,j

    def test_native_click_timeout_proves_transport_only_and_retains_unknown_value(self):
        for prior_operation in (False,True):
            with self.subTest(prior_operation=prior_operation), tempfile.TemporaryDirectory() as d:
                p=Path(d);v,j=self.click_timeout_fixture(p,prior_operation=prior_operation)
                updates,proof=audit_local_failure(v,p)
                derived=updates['results']['m']['last_receipt']
                self.assertTrue(derived['settled'])
                self.assertEqual(derived['status'],'unknown')
                self.assertEqual(derived['results'][0]['status'],'unknown')
                self.assertFalse(proof['value_verified'])
                self.assertFalse(proof['write_verified'])
                self.assertEqual(proof['guard'],'native_playwright_click_timeout')
                (p/'local-call-audits').mkdir()
                audit_path=p/'local-call-audits/old.json'
                audit_path.write_text(json.dumps(proof))
                self.assertTrue(audited_completion(p,j))
                proof['write_verified']=True;audit_path.write_text(json.dumps(proof))
                self.assertFalse(audited_completion(p,j))
                self.assertEqual(json.loads((p/'writer/old.json').read_text()),j)

    def test_native_click_timeout_rejects_incomplete_or_inconsistent_evidence(self):
        mutations=(
            lambda j:j['instrumentation'][-1].update(status='pending'),
            lambda j:j['last_error']['sanitized'].update(message='different error'),
            lambda j:j['instrumentation'][0].update(method='fill'),
            lambda j:j['receipt']['results'].append({'id':'other','status':'unknown'}),
            lambda j:j['receipt']['evidence'].update(executor='other'),
        )
        for mutate in mutations:
            with self.subTest(mutation=mutate), tempfile.TemporaryDirectory() as d:
                p=Path(d);v,j=self.click_timeout_fixture(p);mutate(j)
                (p/'writer/old.json').write_text(json.dumps(j))
                with self.assertRaises(ContractError):audit_local_failure(v,p)

    def fixture(self, folder):
        r={'kind':'fill','command_id':'old','settled':False,'status':'unknown','results':[{'id':'city','status':'unknown'}]}
        j={'kind':'fill','command_id':'old','receipt':r,'control':{'stage':'text_probe_issued'},'last_error':{'sanitized':{'message':'text_probe_discovered_composite_control'}},'instrumentation':[{'field_id':'city','method':'click','status':'returned'}]}
        (folder/'writer').mkdir();(folder/'writer/old.json').write_text(json.dumps(j))
        v={'status':'recovery_blocked','index':0,'manifest':{'modules':[{'id':'m'}]},'results':{'m':{'last_receipt':r,'results':{'city':r['results'][0]}}}}
        return v,j

    def test_terminal_probe_preserves_original_and_does_not_verify_value(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);v,j=self.fixture(p);before=copy.deepcopy(v)
            updates,proof=audit_local_failure(v,p)
            self.assertEqual(v,before)
            self.assertEqual(json.loads((p/'writer/old.json').read_text()),j)
            self.assertTrue(updates['results']['m']['last_receipt']['settled'])
            self.assertFalse(proof['value_verified'])
            self.assertEqual(updates['results']['m']['results']['city']['status'],'deferred')

    def test_recover_restores_only_a_still_valid_command_bound_audit(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);v,j=self.fixture(p)
            j['receipt'].update(target={},snapshot=None,evidence={})
            j['receipt']['results']=[{'id':'city','status':'unknown','reason':'browser_call_failed'}]
            j['command_id']='old'
            (p/'writer/old.json').write_text(json.dumps(j))
            # Ordinary terminal probe audit derives a settled partial receipt.
            updates,proof=audit_local_failure(v,p)
            (p/'local-call-audits').mkdir()
            (p/'local-call-audits/old.json').write_text(json.dumps(proof))
            old={'kind':'fill','command_id':'old','target':{},'operations':[{'id':'city'}]}
            restored=recovered_fill_receipt(p,old,j)
            self.assertTrue(restored['settled'])
            self.assertEqual(restored['status'],'partial')
            self.assertEqual(restored['results'][0]['status'],'deferred')
            self.assertFalse(restored['evidence']['local_completion_audit']['value_verified'])
            # Once the evidence no longer matches the exact journal, recover
            # falls back to the original unsettled receipt.
            j['instrumentation'][0]['status']='pending'
            self.assertFalse(recovered_fill_receipt(p,old,j)['settled'])

    def test_pending_failed_or_absolute_write_cannot_be_reclassified(self):
        for event in ({'method':'click','status':'pending'}, {'method':'click','status':'failed'}, {'method':'fill','status':'returned'}):
            with self.subTest(event=event), tempfile.TemporaryDirectory() as d:
                p=Path(d);v,j=self.fixture(p);j['instrumentation']=[dict(event,field_id='city')]
                (p/'writer/old.json').write_text(json.dumps(j))
                with self.assertRaises(ContractError):audit_local_failure(v,p)

    def test_file_presence_and_other_names_do_not_match(self):
        for value in ('uploaded','other.pdf',''):
            self.assertFalse(file_matches({'value':value,'value_present':True,'upload_ready':True},r'C:\resume.pdf'))
        self.assertTrue(file_matches({'value':'resume.pdf','value_present':True,'upload_ready':True},r'C:\resume.pdf'))

if __name__=='__main__':unittest.main()
