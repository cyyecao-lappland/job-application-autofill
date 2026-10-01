import copy
import tempfile
import unittest
from pathlib import Path
import json

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command
from edge_form_graph.application import application_state, build_application, active_command
from edge_form_graph.application_cli import check_prior
from edge_form_graph.contracts import ContractError
from tests.test_graph import Model, snapshot


def manifest(shared=True):
    return {'target':snapshot()['target'], 'discovery_evidence':'synthetic rendered order',
        'modules':[{'id':'a','selector':'#a','page_order':0,'save_scope':'g'},
                   {'id':'b','selector':'#b','page_order':1,'save_scope':'g' if shared else 'h'}],
        'save_scopes':[{'id':g,'selector':'#'+g,'mode':'explicit','evidence':'synthetic form ownership',
                        'capture':{'saveControl':'#save','savedSignal':{'selector':'#saved','text':'保存成功'}}}
                       for g in (['g'] if shared else ['g','h'])]}


def answer(command, saved=True):
    kind=command['kind']
    snap=snapshot()
    snap.update(module_id=command['module_id'],module_selector=command['module_selector'])
    r={'command_id':command['command_id'],'kind':kind,'target':command['target'],
       'settled':True,'status':'completed','results':[],'snapshot':snap,'evidence':{}}
    if kind=='fill':
        snap['fields'][0]['value']='示例大学'
        r['results']=[{'id':op['id'],'status':'written','reason':''} for op in command['operations']]
    elif kind=='save_scope':
        r.update(status='saved' if saved else 'unknown',settled=saved,snapshot=None,evidence={'save_confirmed':saved})
    elif kind=='activate_module':
        r.update(snapshot=None,evidence={'activated':True})
    elif kind=='add_module_record':
        r.update(evidence={'record_editor_open':True})
    return r


class ApplicationTests(unittest.TestCase):
    def test_rebound_record_changed_since_inventory_stops_before_fill(self):
        with SqliteSaver.from_conn_string(':memory:') as saver:
            g=build_application(Model(),saver);cfg={'configurable':{'thread_id':'rebound-conflict'}}
            source=manifest(False)
            source['modules'][0]['record_rebind_expected']=copy.deepcopy(snapshot()['fields'])
            state=application_state({'school':'示例大学'},source,allow_save=True)
            g.invoke(state,cfg)
            receipt=answer(active_command(g.get_state(cfg,subgraphs=True)))
            receipt['snapshot']['fields'][0]['value']='用户修改'
            g.invoke(Command(resume=receipt),cfg)
            final=g.get_state(cfg,subgraphs=True)
            self.assertEqual(final.values['status'],'preflight_value_conflict')
            self.assertEqual(final.values['recovery_reason'],'record_identity_values_changed')
            self.assertIsNone(active_command(final))

    def test_save_preflight_same_file_name_with_changed_readiness_stops(self):
        with SqliteSaver.from_conn_string(':memory:') as saver:
            g=build_application(Model(),saver);cfg={'configurable':{'thread_id':'preflight-upload'}}
            state=application_state({'school':'示例大学'},manifest(False),allow_save=True)
            before=snapshot();before.update(module_id='a',module_selector='#a')
            before['fields'][0].update(kind='file',value='resume.pdf',value_present=True,upload_ready=True)
            state['save_preflight_expected']=[before]
            g.invoke(state,cfg)
            command=active_command(g.get_state(cfg,subgraphs=True));receipt=answer(command)
            receipt['snapshot']=copy.deepcopy(before)
            receipt['snapshot']['fields'][0]['upload_ready']=False
            g.invoke(Command(resume=receipt),cfg)
            final=g.get_state(cfg,subgraphs=True)
            self.assertEqual(final.values['status'],'preflight_value_conflict')
            self.assertEqual(final.values['recovery_reason'],'save_preflight_upload_changed')
            self.assertIsNone(active_command(final))

    def test_save_preflight_reread_preserves_a_user_changed_value(self):
        with SqliteSaver.from_conn_string(':memory:') as saver:
            g=build_application(Model(),saver);cfg={'configurable':{'thread_id':'preflight-conflict'}}
            state=application_state({'school':'示例大学'},manifest(False),allow_save=True)
            before=snapshot();before.update(module_id='a',module_selector='#a')
            before['fields'][0]['value']='示例大学'
            state['save_preflight_expected']=[before]
            g.invoke(state,cfg)
            command=active_command(g.get_state(cfg,subgraphs=True));receipt=answer(command)
            receipt['snapshot']['fields'][0]['value']='用户修改'
            g.invoke(Command(resume=receipt),cfg)
            final=g.get_state(cfg,subgraphs=True)
            self.assertEqual(final.values['status'],'preflight_value_conflict')
            self.assertIsNone(active_command(final))
            self.assertEqual(final.values['current']['fields'][0]['value'],'用户修改')

    def test_revisit_skips_confirmed_saved_scope_before_observing_next(self):
        with SqliteSaver.from_conn_string(':memory:') as saver:
            g=build_application(Model(),saver);cfg={'configurable':{'thread_id':'saved-revisit'}}
            state=application_state({'school':'示例大学'},manifest(False),allow_save=True)
            state['scopes']={'g':'saved_confirmed'}
            g.invoke(state,cfg)
            command=active_command(g.get_state(cfg,subgraphs=True))
            self.assertEqual((command['kind'],command['module_id']),('observe','b'))

    def run_flow(self, shared, failure=False):
        with SqliteSaver.from_conn_string(':memory:') as saver:
            g=build_application(Model(),saver); cfg={'configurable':{'thread_id':'a'}}
            g.invoke(application_state({'school':'示例大学'},manifest(shared),allow_save=True),cfg)
            actions=[]
            for _ in range(12):
                state=g.get_state(cfg,subgraphs=True)
                c=active_command(state)
                if not c:break
                actions.append((c['kind'],c['module_id']))
                g.invoke(Command(resume=answer(c,not failure)),cfg)
            return actions,g.get_state(cfg).values

    def test_shared_save_follows_both_modules(self):
        actions,s=self.run_flow(True)
        self.assertEqual(actions,[('observe','a'),('fill','a'),('observe','b'),('fill','b'),('save_scope','g')])
        self.assertEqual(s['status'],'complete')
        self.assertEqual(s['scopes'],{'g':'saved_confirmed'})

    def test_individual_save_before_next_module(self):
        actions,s=self.run_flow(False)
        self.assertEqual(actions,[('observe','a'),('fill','a'),('save_scope','g'),('observe','b'),('fill','b'),('save_scope','h')])

    def test_navigation_is_programmatically_verified_before_each_ordered_module(self):
        m=manifest(False)
        for module,label in zip(m['modules'],['个人信息','联系方式']):
            module['navigation']={'menu_selector':'.resume-menu-item','label':label}
        with SqliteSaver.from_conn_string(':memory:') as saver:
            g=build_application(Model(),saver);cfg={'configurable':{'thread_id':'nav'}}
            g.invoke(application_state({'school':'示例大学'},m,allow_save=True),cfg)
            actions=[]
            for _ in range(16):
                c=active_command(g.get_state(cfg,subgraphs=True))
                if not c:break
                actions.append((c['kind'],c['module_id']))
                g.invoke(Command(resume=answer(c)),cfg)
            self.assertEqual(actions,[('activate_module','a'),('observe','a'),('fill','a'),('save_scope','g'),
                                      ('activate_module','b'),('observe','b'),('fill','b'),('save_scope','h')])
            self.assertEqual(g.get_state(cfg).values['status'],'complete')

    def test_missing_collection_record_is_added_before_observation(self):
        m=manifest(False);m['modules']=m['modules'][:1];m['save_scopes']=m['save_scopes'][:1]
        m['modules'][0].update(open_mode='add',collection_selector='#collection',
                              record_selector='.record',add_control_selector='.add',expected_record_count=2)
        with SqliteSaver.from_conn_string(':memory:') as saver:
            g=build_application(Model(),saver);cfg={'configurable':{'thread_id':'add'}}
            g.invoke(application_state({'school':'示例大学'},m,allow_save=True),cfg)
            actions=[]
            for _ in range(10):
                c=active_command(g.get_state(cfg,subgraphs=True))
                if not c:break
                actions.append(c['kind']);g.invoke(Command(resume=answer(c)),cfg)
            self.assertEqual(actions,['add_module_record','observe','fill','save_scope'])
            self.assertEqual(g.get_state(cfg).values['status'],'complete')

    def test_unknown_save_stops_at_boundary(self):
        actions,s=self.run_flow(False,True)
        self.assertEqual(s['index'],0)
        self.assertEqual(s['status'],'save_unconfirmed')
        self.assertNotIn(('observe','b'),actions)
        self.assertIsNotNone(s['command'])

    def test_wrong_order_or_scope_rejected(self):
        m=manifest();m['modules'].reverse()
        with self.assertRaises(ContractError):application_state({},m)
        m=manifest();m['modules'][1]['save_scope']='absent'
        with self.assertRaises(ContractError):application_state({},m)

    def test_unknown_boundary_blocks_before_writing(self):
        m=manifest();m['save_scopes'][0]['mode']='unknown'
        with SqliteSaver.from_conn_string(':memory:') as saver:
            g=build_application(Model(),saver);cfg={'configurable':{'thread_id':'a'}}
            g.invoke(application_state({},m),cfg)
            self.assertEqual(g.get_state(cfg).values['status'],'save_scope_unknown')

    def test_existing_unknown_is_not_erased_by_new_entrypoint(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'self'/'writer').mkdir(parents=True)
            (root/'self'/'writer'/'old.json').write_text(json.dumps({'command_id':'old','receipt':{'settled':False,'status':'unknown'}}))
            self.assertEqual(check_prior(root)['status'],'prior_reconciliation_required')

    def test_saved_reconciliation_only_clears_its_settled_original(self):
        from unittest.mock import patch
        for settled,parent,target,expected in [(True,'old','same','preflight_clear'),
                (False,'old','same','prior_reconciliation_required'),
                (True,'other','same','prior_reconciliation_required'),
                (True,'old','different','prior_reconciliation_required')]:
            with tempfile.TemporaryDirectory() as d:
                root=Path(d); writer=root/'run'/'writer'; writer.mkdir(parents=True)
                old={'command_id':'old','kind':'save_scope','started_at':1,'save_stage':'observation_returned',
                     'receipt':{'settled':settled,'status':'unconfirmed','target':'same'}}
                resolved={'command_id':'new','kind':'reconcile_save','started_at':2,
                    'receipt':{'settled':True,'status':'saved','target':target,'evidence':{'save_confirmed':True}}}
                (writer/'old.json').write_text(json.dumps(old));(writer/'new.json').write_text(json.dumps(resolved))
                with patch('edge_form_graph.application_cli.reconciliation_parent',return_value=parent):
                    self.assertEqual(check_prior(root)['status'],expected)

    def test_absent_persisted_education_requires_settled_bound_oracle(self):
        from unittest.mock import patch
        for settled, parent, count, match, expected in [
                (True, 'old', 0, True, 'preflight_clear'),
                (False, 'old', 0, True, 'prior_reconciliation_required'),
                (True, 'other', 0, True, 'prior_reconciliation_required'),
                (True, 'old', 1, True, 'prior_reconciliation_required'),
                (True, 'old', 0, False, 'prior_reconciliation_required')]:
            with tempfile.TemporaryDirectory() as d:
                root=Path(d); writer=root/'run'/'writer'; writer.mkdir(parents=True)
                old={'command_id':'old','kind':'save_scope','started_at':1,'save_stage':'observation_returned',
                     'receipt':{'settled':settled,'status':'unconfirmed','target':'same'}}
                resolved={'command_id':'new','kind':'reconcile_save','started_at':2,
                    'receipt':{'settled':True,'status':'unconfirmed','target':'same','evidence':{
                        'save_confirmed':False,'signal':'persisted_scope_absent','current_draft_matches':match,
                        'persisted':{'oracle':'alibaba_resume_detail_v1','education_count':count,'observed_at':3}}}}
                (writer/'old.json').write_text(json.dumps(old)); (writer/'new.json').write_text(json.dumps(resolved))
                with patch('edge_form_graph.application_cli.reconciliation_parent',return_value=parent):
                    self.assertEqual(check_prior(root)['status'],expected)

    def test_interrupted_field_write_restarts_from_current_page(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'old'/'writer').mkdir(parents=True)
            journal={'command_id':'field-write','kind':'fill','receipt':{'settled':False,'status':'unknown'}}
            (root/'old'/'writer'/'field-write.json').write_text(json.dumps(journal))
            result=check_prior(root)
            self.assertEqual(result['status'],'preflight_current_page_reconciliation')
            self.assertEqual(result['recoverable_from_current_page'][0]['kind'],'fill')

    def test_interrupted_save_remains_a_hard_save_barrier(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'old'/'writer').mkdir(parents=True)
            journal={'command_id':'save-click','kind':'save_scope','receipt':{'settled':False,'status':'unknown'}}
            (root/'old'/'writer'/'save-click.json').write_text(json.dumps(journal))
            result=check_prior(root)
            self.assertEqual(result['status'],'prior_reconciliation_required')
            self.assertEqual(result['blockers'][0]['kind'],'save_scope')

    def test_settled_save_rejected_before_click_does_not_block_restart(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'old'/'writer').mkdir(parents=True)
            receipt={'settled':True,'status':'unconfirmed'}
            journal={'command_id':'save-not-clicked','kind':'save_scope','receipt':receipt,
                     'instrumentation':[{'stage':'ui_read','status':'returned'}]}
            (root/'old'/'writer'/'save-not-clicked.json').write_text(json.dumps(journal))
            self.assertEqual(check_prior(root)['status'],'preflight_clear')
            journal['save_stage']='save_click_issued'
            (root/'old'/'writer'/'save-not-clicked.json').write_text(json.dumps(journal))
            self.assertEqual(check_prior(root)['status'],'prior_reconciliation_required')

    def test_settled_readonly_save_reconciliation_does_not_block_restart(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'old'/'writer').mkdir(parents=True)
            journal={'command_id':'read-only','kind':'reconcile_save',
                     'receipt':{'settled':True,'status':'unconfirmed'}}
            (root/'old'/'writer'/'read-only.json').write_text(json.dumps(journal))
            self.assertEqual(check_prior(root)['status'],'preflight_clear')

    def test_settled_validation_rejection_allows_current_page_repair_only(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);(root/'old'/'writer').mkdir(parents=True)
            journal={'command_id':'validation-rejected','kind':'save_scope',
                     'save_stage':'observation_returned',
                     'receipt':{'settled':True,'status':'unconfirmed',
                                'evidence':{'signal':'validation_failed'}}}
            path=root/'old'/'writer'/'validation-rejected.json'
            path.write_text(json.dumps(journal))
            self.assertEqual(check_prior(root)['status'],'preflight_clear')
            journal['receipt']['evidence']['signal']='current_draft_only'
            path.write_text(json.dumps(journal))
            self.assertEqual(check_prior(root)['status'],'prior_reconciliation_required')

    def test_confirmed_complete_scope_resolves_earlier_unconfirmed_save_in_same_run(self):
        with tempfile.TemporaryDirectory() as d:
            root=Path(d);run=root/'old';(run/'writer').mkdir(parents=True)
            journal={'command_id':'earlier-save','kind':'save_scope','save_stage':'observation_returned',
                     'receipt':{'settled':True,'status':'unconfirmed'}}
            (run/'writer'/'earlier-save.json').write_text(json.dumps(journal))
            (run/'summary.json').write_text(json.dumps(
                {'status':'complete','scopes':{'scope':'saved_confirmed'}}))
            self.assertEqual(check_prior(root)['status'],'preflight_clear')

    def test_fresh_state_carries_only_minimal_reconciliation_evidence(self):
        evidence=[{'command_id':'old','kind':'fill','status':'unknown'}]
        state=application_state({'school':'示例大学'},manifest(),startup_reconciliation=evidence)
        self.assertEqual(state['startup_reconciliation'],evidence)
        self.assertNotIn('old_snapshot',state)

    def test_restart_resumes_nested_fill_without_repeating_map(self):
        class TrackingModel(Model):
            def __init__(self):
                self.mapped = []
                self.reviewed = []

            def map(self, profile, before):
                self.mapped.append(before['module_id'])
                return super().map(profile, before)

            def review(self, profile, before, plan, after):
                self.reviewed.append(before['module_id'])
                return super().review(profile, before, plan, after)

        with tempfile.TemporaryDirectory() as d:
            cfg={'configurable':{'thread_id':'a'}}; model=TrackingModel()
            with SqliteSaver.from_conn_string(str(Path(d)/'state.sqlite')) as saver:
                g=build_application(model,saver)
                g.invoke(application_state({'school':'示例大学'},manifest(),allow_save=True),cfg)
                g.invoke(Command(resume=answer(active_command(g.get_state(cfg,subgraphs=True)))),cfg)
                c=active_command(g.get_state(cfg,subgraphs=True))
                self.assertEqual(c['kind'],'fill')
                self.assertEqual(model.mapped, ['a'])
                self.assertEqual(model.reviewed, [])
            with SqliteSaver.from_conn_string(str(Path(d)/'state.sqlite')) as saver:
                g=build_application(model,saver)
                g.invoke(Command(resume=answer(c)),cfg)
                self.assertEqual(model.calls,1)
                self.assertEqual(model.mapped, ['a'])  # Resuming the fill must not remap a.
                self.assertEqual(model.reviewed, [])
                state = g.get_state(cfg, subgraphs=True)
                self.assertIsNone(state.values['results']['a']['reviewed_revision'])
                c = active_command(state)
                self.assertEqual((c['kind'], c['module_id']), ('observe', 'b'))
                g.invoke(Command(resume=answer(c)), cfg)
                c = active_command(g.get_state(cfg, subgraphs=True))
                self.assertEqual((c['kind'], c['module_id']), ('fill', 'b'))
                self.assertEqual(model.mapped, ['a', 'b'])
                self.assertEqual(model.reviewed, [])  # Scope boundary not reached yet.
                g.invoke(Command(resume=answer(c)), cfg)
                state = g.get_state(cfg, subgraphs=True)
                self.assertEqual(model.mapped, ['a', 'b'])
                self.assertCountEqual(model.reviewed, ['a', 'b'])
                for result in state.values['results'].values():
                    self.assertEqual(result['reviewed_revision'], result['revision'])
                c = active_command(state)
                self.assertEqual((c['kind'], c['module_id']), ('save_scope', 'g'))


if __name__=='__main__':unittest.main()
