import copy
import unittest
import tempfile
from pathlib import Path
from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command
from edge_form_graph.application import application_state,build_application,active_command
from edge_form_graph.contracts import ContractError
from edge_form_graph.recovery import reconcile
from tests.test_application import manifest,answer
from tests.test_graph import Model,snapshot

class FillRecoveryTests(unittest.TestCase):
    def test_upload_recovery_never_replays_an_unconfirmed_file(self):
        old = snapshot(); old['fields'][0].update(kind='file', value='', upload_ready=False)
        op = {'id':'#school', 'field':copy.deepcopy(old['fields'][0]), 'value':'/tmp/resume.pdf'}
        result = {'snapshot':old, 'current':old, 'profile':{}, 'proposal':{'mappings':[], 'deferred':[]},
                  'operations':[op], 'last_receipt':{'results':[{'id':'#school','status':'unknown'}]}}
        with self.assertRaisesRegex(ContractError, 'upload_outcome_requires_readback'):
            reconcile(result, copy.deepcopy(old))
        fresh = copy.deepcopy(old); fresh['fields'][0].update(value='resume.pdf', value_present=True, upload_ready=True)
        _, matched = reconcile(result, fresh)
        self.assertEqual(matched, ['#school'])
        from edge_form_graph.recovery import compare_values
        op['value'] = 'resume.pdf'
        fresh['fields'][0]['upload_ready'] = False
        for readable in (True, False):
            old['fields'][0]['value_readable'] = readable
            self.assertNotEqual(compare_values(result, fresh)[0]['status'], 'matched')
            with self.assertRaises(ContractError):
                reconcile(result, fresh)

    def test_mapping_failure_can_be_corrected_before_first_dispatch(self):
        from edge_form_graph.recovery import never_dispatched, repair_eligible
        stopped = {'status': 'nothing_filled', 'command': None, 'last_receipt': {},
            'revision': 0, 'metrics': {'batch_count': 0, 'written': 0},
            'trace': [{'node': 'map'}, {'node': 'resolve_unknown'}, {'node': 'verify'}],
            'results': {'field': {'status': 'deferred'}}}
        self.assertTrue(never_dispatched(stopped))
        self.assertTrue(repair_eligible(stopped))
        reviewed = {**stopped, 'status': 'review_rejected',
                    'results': {'field': {'status': 'prefilled_pending_review'}}}
        self.assertTrue(repair_eligible(reviewed))
        self.assertFalse(repair_eligible({**reviewed, 'revision': 1}))
        for updates in ({'command': {'kind': 'fill'}},
                        {'last_receipt': {'kind': 'fill', 'settled': False}},
                        {'revision': 1}, {'metrics': {'batch_count': 1, 'written': 0}},
                        {'results': {'field': {'status': 'unknown'}}},
                        {'trace': [{'node': 'execute_fill'}]}, {'trace': []}):
            with self.subTest(updates=updates):
                self.assertFalse(never_dispatched({**stopped, **updates}))

    def test_recovery_rebinds_changed_selector_by_unique_semantic_identity(self):
        from edge_form_graph.recovery import compare_values, reconcile
        old = snapshot(); old['fields'][0]['id'] = old['fields'][0]['selector'] = 'input[placeholder="Choose"]'
        result = {'snapshot': old, 'current': old, 'profile': {'school':'A'},
                  'proposal': {'mappings': [], 'deferred': [{'field_id':old['fields'][0]['id'],'reason':'current_value_present'}]},
                  'operations': [], 'results': {old['fields'][0]['id']:{'status':'prefilled_pending_review'}}}
        fresh = copy.deepcopy(old)
        fresh['fields'][0]['id'] = fresh['fields'][0]['selector'] = 'input[placeholder="Selected"]'
        self.assertEqual(compare_values(result, fresh), [{'field_id':'input[placeholder="Choose"]','status':'unchanged'}])
        cache, _ = reconcile(result, fresh)
        self.assertEqual(cache['snapshot']['fields'][0]['id'], 'input[placeholder="Choose"]')
        self.assertEqual(cache['snapshot']['fields'][0]['selector'], 'input[placeholder="Selected"]')

    def test_recovery_accepts_closed_readable_value_after_unreadable_popup_state(self):
        from edge_form_graph.recovery import compare_values
        old = snapshot(); old['fields'][0].update(value='', value_readable=False, expanded='true')
        fresh = copy.deepcopy(old); fresh['fields'][0].update(value='CET4', value_readable=True, expanded='false')
        result = {'snapshot':old, 'current':old, 'operations':[]}
        self.assertEqual(compare_values(result, fresh)[0]['status'], 'unchanged')

    def start(self,saver):
        model=Model();g=build_application(model,saver);cfg={'configurable':{'thread_id':'test'},'recursion_limit':100}
        g.invoke(application_state({'school':'示例大学'},manifest(False),allow_save=True),cfg)
        g.invoke(Command(resume=answer(active_command(g.get_state(cfg,subgraphs=True)))),cfg)
        return g,cfg,model

    def fail(self,g,cfg,settled=True):
        c=active_command(g.get_state(cfg,subgraphs=True));r=answer(c)
        r.update(status='unknown',settled=settled,snapshot=None)
        r['results'][0].update(status='unknown')
        g.invoke(Command(resume=r),cfg)
        return c,r

    def test_ended_unknown_reobserves_and_skips_matched_without_remap(self):
        with SqliteSaver.from_conn_string(':memory:') as saver:
            g,cfg,model=self.start(saver);old,r=self.fail(g,cfg)
            c=active_command(g.get_state(cfg,subgraphs=True));self.assertEqual(c['kind'],'observe')
            fresh=answer(c);fresh['snapshot']['fields'][0]['value']='示例大学'
            g.invoke(Command(resume=fresh),cfg)
            state=g.get_state(cfg,subgraphs=True);new=active_command(state)
            self.assertEqual((new['kind'],new['module_id']),('fill','a'))
            self.assertNotEqual(old['command_id'],new['command_id']);self.assertEqual(new['min_fill_count'],0)
            self.assertEqual(new['operations'][0]['field']['value'],'示例大学')
            self.assertEqual(model.calls,1)
            self.assertEqual(state.values['recovery_history'][0]['previous']['last_receipt'],r)

    def test_unsettled_dispatches_read_but_unchanged_cannot_retry(self):
        with SqliteSaver.from_conn_string(':memory:') as saver:
            g,cfg,_=self.start(saver);self.fail(g,cfg,False)
            read=active_command(g.get_state(cfg,subgraphs=True))
            self.assertEqual(read['kind'],'observe')
            g.invoke(Command(resume=answer(read)),cfg)
            self.assertIsNone(active_command(g.get_state(cfg,subgraphs=True)))
            s=g.get_state(cfg);self.assertEqual(s.values['recovery_reason'],'value_not_matched_transport_unknown')
            self.assertEqual(s.values['index'],0)

    def test_unsettled_target_match_skips_write_and_preserves_original_receipt(self):
        with SqliteSaver.from_conn_string(':memory:') as saver:
            g,cfg,_=self.start(saver);old,receipt=self.fail(g,cfg,False)
            read=active_command(g.get_state(cfg,subgraphs=True));fresh=answer(read)
            fresh['snapshot']['fields'][0]['value']='示例大学'
            g.invoke(Command(resume=fresh),cfg)
            s=g.get_state(cfg,subgraphs=True);new=active_command(s)
            self.assertEqual(new['min_fill_count'],0)
            self.assertNotEqual(new['command_id'],old['command_id'])
            self.assertFalse(s.values['recovery_history'][0]['previous']['last_receipt']['settled'])
            self.assertEqual(s.values['value_comparison'][0]['status'],'matched')

    def test_user_value_conflict_stops_without_rewrite(self):
        with SqliteSaver.from_conn_string(':memory:') as saver:
            g,cfg,_=self.start(saver);self.fail(g,cfg)
            c=active_command(g.get_state(cfg,subgraphs=True));r=answer(c);r['snapshot']['fields'][0]['value']='用户新值'
            g.invoke(Command(resume=r),cfg)
            self.assertEqual(g.get_state(cfg).values['recovery_reason'],'recovery_user_value_conflict')
            self.assertIsNone(active_command(g.get_state(cfg,subgraphs=True)))

    def test_recovery_limit_and_deadline_not_reset(self):
        with SqliteSaver.from_conn_string(':memory:') as saver:
            g,cfg,_=self.start(saver);deadline=g.get_state(cfg).values['deadline']
            for i in range(3):
                self.fail(g,cfg)
                c=active_command(g.get_state(cfg,subgraphs=True))
                if i<2:
                    self.assertEqual(c['kind'],'observe');g.invoke(Command(resume=answer(c)),cfg)
                else:self.assertIsNone(c)
            s=g.get_state(cfg).values
            self.assertEqual(s['recovery_attempts']['a'],2);self.assertEqual(s['deadline'],deadline)

    def test_identity_and_structure_conflicts(self):
        before=snapshot();result={'current':before,'profile':{},'proposal':{},'operations':[]}
        for change in ('target','fields','signature'):
            fresh=copy.deepcopy(before)
            if change=='target':fresh['target']['tab_id']='different'
            elif change=='fields':fresh['fields'].append({**fresh['fields'][0],'id':'new'})
            else:fresh['fields'][0]['signature']['label']='另一个字段'
            with self.assertRaises(ContractError):reconcile(result,fresh)

    def test_restart_at_recovery_interrupt_preserves_old_command(self):
        with tempfile.TemporaryDirectory() as d:
            db=str(Path(d)/'recovery.sqlite')
            with SqliteSaver.from_conn_string(db) as saver:
                g,cfg,model=self.start(saver);old,_=self.fail(g,cfg)
                read=active_command(g.get_state(cfg,subgraphs=True))
            with SqliteSaver.from_conn_string(db) as saver:
                g=build_application(model,saver)
                self.assertEqual(active_command(g.get_state(cfg,subgraphs=True)),read)
                g.invoke(Command(resume=answer(read)),cfg)
                state=g.get_state(cfg,subgraphs=True)
                self.assertNotEqual(active_command(state)['command_id'],old['command_id'])
                self.assertEqual(state.values['recovery_history'][0]['previous']['command'],old)
                self.assertEqual(model.calls,1)

    def test_authorized_absolute_retry_does_not_mark_unchanged_value_successful(self):
        with SqliteSaver.from_conn_string(':memory:') as saver:
            g,cfg,_=self.start(saver);old,receipt=self.fail(g,cfg,False)
            auth={'basis':'explicit user migration permission','command_id':old['command_id'],'module_id':'a'}
            g.update_state(cfg,{'recovery_requested':True,'migration_authorization':auth},as_node='fill_and_review')
            g.invoke(None,cfg)
            state=g.get_state(cfg,subgraphs=True)
            self.assertEqual(active_command(state)['kind'],'observe')
            self.assertEqual(state.values['migration_authorization'],{})
            self.assertEqual(state.values['recovery_history'][0]['previous']['last_receipt'],receipt)
            self.assertFalse(receipt['settled'])
            g.invoke(Command(resume=answer(active_command(state))),cfg)
            next_command=active_command(g.get_state(cfg,subgraphs=True))
            self.assertEqual(next_command['kind'],'fill')
            self.assertNotEqual(next_command['command_id'],old['command_id'])
            self.assertEqual(next_command['min_fill_count'],1)
            self.assertFalse(g.get_state(cfg).values['recovery_history'][-1]['previous']['last_receipt']['settled'])

    def test_programmatic_value_types_and_unreadable_controls(self):
        from edge_form_graph.recovery import compare_values
        old=snapshot(); op={'id':'#school','value':['A','B']}
        result={'current':old,'operations':[op]}
        for actual,expected in [(['B','A'],'matched'),('','unchanged'),('other','conflict')]:
            fresh=copy.deepcopy(old);fresh['fields'][0]['value']=actual
            self.assertEqual(compare_values(result,fresh)[0]['status'],expected)
        for flag,value in [('value_readable',False),('expanded','true')]:
            fresh=copy.deepcopy(old);fresh['fields'][0].update(value=['A','B'],**{flag:value})
            self.assertEqual(compare_values(result,fresh)[0]['status'],'unreadable')
        op['value']=False;fresh=copy.deepcopy(old);fresh['fields'][0]['value']=0
        self.assertEqual(compare_values(result,fresh)[0]['status'],'conflict')

    def test_new_date_adapter_requires_same_editable_input_identity(self):
        from edge_form_graph.recovery import compare_values
        old=snapshot();old['fields'][0]['kind']='unsupported'
        old['fields'][0]['signature']['type']='text'
        result={'current':old,'operations':[]}
        fresh=copy.deepcopy(old);fresh['fields'][0].update(kind='text',component='element-date',disabled=False)
        self.assertEqual(compare_values(result,fresh)[0]['status'],'unchanged')
        fresh['fields'][0]['disabled']=True
        with self.assertRaisesRegex(ContractError,'identity_changed'):
            compare_values(result,fresh)

    def test_new_date_now_adapter_preserves_same_input_identity(self):
        from edge_form_graph.recovery import compare_values
        old=snapshot();old['fields'][0]['kind']='unsupported'
        old['fields'][0]['signature']['type']='text'
        result={'current':old,'operations':[]}
        fresh=copy.deepcopy(old);fresh['fields'][0].update(
            kind='text',component='element-date-now',disabled=False)
        self.assertEqual(compare_values(result,fresh)[0]['status'],'unchanged')

    def test_plain_text_reclassification_preserves_same_input_identity(self):
        from edge_form_graph.recovery import compare_values
        old=snapshot();field=old['fields'][0]
        field.update(kind='unsupported',component=None,disabled=False)
        field['signature'].update(tag='INPUT',type='text',role='')
        result={'current':old,'operations':[]}
        fresh=copy.deepcopy(old);fresh['fields'][0]['kind']='text'
        self.assertEqual(compare_values(result,fresh)[0]['status'],'unchanged')
        fresh['fields'][0]['signature']['role']='combobox'
        with self.assertRaisesRegex(ContractError,'identity_changed'):
            compare_values(result,fresh)

if __name__=='__main__':unittest.main()
