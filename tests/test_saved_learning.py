"""Offline state/knowledge regressions; these are not website success evidence."""
import copy
import tempfile
import time
import unittest
from pathlib import Path

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from edge_form_graph.contracts import compile_plan
from edge_form_graph.graph import build_graph, initial_state
from edge_form_graph.knowledge import KnowledgeError, KnowledgeStore
from tests.test_graph import Model, snapshot
from tests.test_program_first import SemanticModel, fill_receipt


def reviewed_module(mid='education'):
    profile = {'school': 'University'}
    before = snapshot()
    before['module_id'] = mid
    current = copy.deepcopy(before)
    current['fields'][0]['value'] = profile['school']
    proposal = Model().map(profile, before)
    operations, _ = compile_plan(profile, before, proposal)
    module = {'snapshot': before, 'current': current, 'proposal': proposal, 'operations': operations,
              'review': {'approved': True, 'issues': [], 'checked_field_ids': ['#school']},
              'revision': 1, 'reviewed_revision': 1, 'command': None,
              'results': {'#school': {'status': 'written'}}}
    return profile, module


def saved_receipt(modules):
    saved_at = time.time()
    saved = [copy.deepcopy(m['current']) for m in modules]
    for current in saved:
        current['observed_at'] = time.time()
    return {'kind': 'save_scope', 'status': 'saved', 'settled': True,
            'target': saved[0]['target'], 'evidence': {'save_confirmed': True,
                'save_observed_at': saved_at, 'saved_modules': saved}}


class SavedLearningTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = KnowledgeStore(Path(self.temp.name) / 'knowledge.json')

    def test_draft_and_failed_save_never_activate(self):
        for allow_save in (False, True):
            with self.subTest(allow_save=allow_save), SqliteSaver.from_conn_string(':memory:') as saver:
                graph = build_graph(Model(), saver, knowledge=self.store,
                                    semantic_model=SemanticModel({'#school': ('/school',)}))
                cfg = {'configurable': {'thread_id': 'draft'}}
                graph.invoke(initial_state({'school': 'University'}, snapshot(), allow_save=allow_save), cfg)
                command = graph.get_state(cfg).values['command']
                graph.invoke(Command(resume=fill_receipt(command, snapshot())), cfg)
                self.assertFalse(self.store.path.exists())
                if allow_save:
                    command = graph.get_state(cfg).values['command']
                    graph.invoke(Command(resume={'command_id': command['command_id'], 'kind': 'save',
                        'target': command['target'], 'settled': True, 'status': 'unconfirmed',
                        'results': [], 'snapshot': None, 'evidence': {'save_confirmed': False}}), cfg)
                    self.assertEqual(graph.get_state(cfg).values['status'], 'save_unconfirmed')
                self.assertFalse(self.store.path.exists())

    def test_incomplete_or_changed_save_scope_is_atomic(self):
        profile, first = reviewed_module()
        _, second = reviewed_module('other')
        for failure in ('missing', 'changed', 'unsettled', 'review', 'unknown'):
            modules = copy.deepcopy([first, second])
            receipt = saved_receipt(modules)
            if failure == 'missing':
                receipt['evidence']['saved_modules'].pop()
            elif failure == 'changed':
                receipt['evidence']['saved_modules'][1]['fields'][0]['value'] = 'Changed'
            elif failure == 'unsettled':
                receipt['settled'] = False
            elif failure == 'review':
                modules[1]['reviewed_revision'] = 0
            else:
                modules[1]['results']['#school']['status'] = 'unknown'
            with self.subTest(failure=failure), self.assertRaises(KnowledgeError):
                self.store.compile_saved(profile, modules, receipt)
            self.assertFalse(self.store.path.exists())

    def test_saved_learning_is_idempotent_and_does_not_store_answers(self):
        profile, module = reviewed_module()
        receipt = saved_receipt([module])
        self.assertEqual(self.store.compile_saved(profile, [module], receipt)['field_learned'], 1)
        self.assertEqual(self.store.compile_saved(profile, [module], receipt)['field_learned'], 0)
        self.assertNotIn('University', self.store.path.read_text())

    def test_reviewed_prefilled_field_does_not_block_learning_other_fields(self):
        profile, module = reviewed_module()
        extra = copy.deepcopy(module['snapshot']['fields'][0])
        extra.update(id='#existing', selector='#existing', label='已有值', value='')
        module['snapshot']['fields'].append(extra)
        current_extra = copy.deepcopy(extra)
        current_extra['value'] = 'University'
        module['current']['fields'].append(current_extra)
        module['proposal']['deferred'].append({'field_id':'#existing','reason':'current_value_present'})
        module['results']['#existing']={'status':'prefilled_pending_review'}
        module['prefilled_bindings']=[{'field_id':'#existing','source':'/school',
                                       'basis':'unique_direct_scalar_equality_in_bound_record'}]
        module['review']['checked_field_ids'].append('#existing')
        learned=self.store.compile_saved(profile,[module],saved_receipt([module]))
        self.assertEqual(learned['field_learned'],2)

    def test_unbound_reviewed_prefilled_field_is_not_learned(self):
        profile, module = reviewed_module()
        module['results']['#school']={'status':'prefilled_pending_review'}
        module['proposal']={'mappings':[],'deferred':[{'field_id':'#school','reason':'current_value_present'}]}
        module['operations']=[]
        learned=self.store.compile_saved(profile,[module],saved_receipt([module]))
        self.assertEqual(learned['field_learned'],0)
        self.assertEqual(self.store.known_plan(profile,module['current'])['mappings'],[])

    def test_reviewed_bound_prefilled_field_becomes_reusable_mapping(self):
        profile={'education':[{'record_id':'master','school_name':'University'}]}
        before=snapshot();before['mapping_context']={
            'module_type':'education','record_collection':'/education','record_id':'master'}
        before['fields'][0]['label']='学校名称'
        current=copy.deepcopy(before);current['fields'][0]['value']='University'
        module={'snapshot':before,'current':current,
                'proposal':{'mappings':[],'deferred':[{'field_id':'#school','reason':'current_value_present'}]},
                'operations':[],'review':{'approved':True,'issues':[],'checked_field_ids':['#school']},
                'revision':0,'reviewed_revision':0,'command':None,
                'results':{'#school':{'status':'prefilled_pending_review'}},
                'prefilled_bindings':[{'field_id':'#school','source':'/education/0/school_name',
                                       'basis':'unique_direct_scalar_equality_in_bound_record'}]}
        self.assertEqual(self.store.compile_saved(profile,[module],saved_receipt([module]))['field_learned'],1)
        self.assertEqual(self.store.known_plan(profile,current)['mappings'][0]['source'],'/education/0/school_name')

    def test_only_popup_control_ids_are_ignored_after_reload(self):
        profile, module = reviewed_module()
        module['current']['fields'][0]['controls'] = 'old-popup'
        receipt = saved_receipt([module])
        receipt['evidence']['saved_modules'][0]['fields'][0]['controls'] = 'new-popup'
        self.assertEqual(self.store.compile_saved(profile, [module], receipt)['field_learned'], 1)
        receipt['evidence']['saved_modules'][0]['fields'][0]['value'] = 'Changed'
        with self.assertRaises(KnowledgeError):
            self.store.compile_saved(profile, [module], receipt)

    def test_collapsed_card_readback_can_activate_reviewed_field_mapping(self):
        profile, module = reviewed_module()
        receipt = saved_receipt([module])
        post = receipt['evidence']['saved_modules'][0]
        post['persisted_fields'] = copy.deepcopy(post['fields'])
        post['fields'] = []
        post['persistence_evidence'] = {'kind': 'module_card_all_values_visible', 'selector': '#module'}
        self.assertEqual(self.store.compile_saved(profile, [module], receipt)['field_learned'], 1)
        self.assertEqual(len(self.store.known_plan(profile, module['current'])['mappings']), 1)

    def test_condition_is_rechecked_and_preserved_on_relearning(self):
        profile, module = reviewed_module()
        profile['has_experience'] = True
        module['learning_conditions'] = {'#school': [{'pointer': '/has_experience', 'equals': True}]}
        self.store.compile_saved(profile, [module], saved_receipt([module]))
        self.assertEqual(len(self.store.known_plan(profile, module['current'])['mappings']), 1)
        del module['learning_conditions']
        self.store.compile_saved(profile, [module], saved_receipt([module]))
        profile['has_experience'] = False
        self.assertEqual(self.store.known_plan(profile, module['current'])['mappings'], [])
        self.assertIn('conditions', self.store.path.read_text())

    def test_company_source_follows_identity_after_reorder(self):
        profile, module = reviewed_module()
        profile['company_answers'] = [{'record_id': 'target-company', 'answer': 'University'},
                                      {'record_id': 'other-company', 'answer': 'Different'}]
        module['proposal']['mappings'][0]['source'] = '/company_answers/0/answer'
        module['operations'] = compile_plan(profile, module['current'], module['proposal'])[0]
        self.store.compile_saved(profile, [module], saved_receipt([module]))
        profile['company_answers'].reverse()
        plan = self.store.known_plan(profile, module['current'])
        self.assertEqual(plan['mappings'][0]['source'], '/company_answers/1/answer')
        module['current']['target']['url'] = 'https://another-company.test/resume'
        self.assertEqual(self.store.known_plan(profile, module['current'])['mappings'], [])


if __name__ == '__main__':
    unittest.main()
