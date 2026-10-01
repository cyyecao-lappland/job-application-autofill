import copy
import unittest

from langgraph.types import Command
from edge_form_graph.storage import SqliteSaver
from edge_form_graph.contracts import compile_plan, field_writable
from edge_form_graph.enum_repair import carry_reviewed_enums, review_requests
from edge_form_graph.graph import build_graph, initial_state
from tests.test_enum_repair import EnumModel, enum_snapshot


class EnumRecompileTests(unittest.TestCase):
    def test_recognized_readonly_ant_picker_has_a_control_path_but_disabled_or_unrecognized_does_not(self):
        field={'selector':'#end','kind':'text','component':'ant-picker','readonly':True,
               'disabled':False,'control_status':'recognized'}
        self.assertTrue(field_writable(field))
        self.assertFalse(field_writable({**field,'disabled':True}))
        self.assertFalse(field_writable({**field,'control_status':'unverified_text_candidate'}))

    def fixture(self, pending=False):
        profile = {'school': '示例大学', 'degree': '本科'}
        snap = enum_snapshot()
        snap['fields'][0]['value'] = profile['school']
        snap['fields'][1]['value'] = '大学本科'
        proposal = EnumModel().map(profile, snap)
        if pending:
            snap['fields'].append({'id': '#extra', 'selector': '#extra', 'kind': 'text',
                                   'label': '备注', 'value': '', 'required': False})
            proposal['deferred'].append({'field_id': '#extra', 'reason': 'mapping_not_found'})
        operations, _ = compile_plan(profile, snap, proposal)
        prior = operations[1]
        request = review_requests(profile, [prior], [{'field_id': '#degree', 'source_value': '本科',
                                                    'options': ['大学本科', '硕士研究生']}])[0]
        prior.update(value='大学本科', enum_provenance={**request, 'option': '大学本科',
                     'reason': 'same education level', 'command_id': 'original-fill'})
        return profile, snap, proposal, operations

    def test_recovered_alias_survives_all_semantic_recompile_paths(self):
        for mode in ('no_pending', 'disabled', 'semantic_success', 'semantic_exception'):
            with self.subTest(mode=mode), SqliteSaver.from_conn_string(':memory:') as saver:
                profile, snap, proposal, prior = self.fixture(mode != 'no_pending')
                model = EnumModel()
                def match_unknown(profile, snapshot):
                    if mode == 'semantic_exception':
                        raise RuntimeError('semantic worker failed')
                    return {'decisions': [{'field_id': '#extra', 'classification': 'unsupported',
                        'source': None, 'transform': None, 'depends_on': [],
                        'reason': 'optional unsupported field left unchanged'}], 'record_binding': None}
                model.match_unknown = match_unknown
                graph = build_graph(model, saver, knowledge=object(), semantic_model=model)
                state = initial_state(profile, snap, agent_tuning=mode != 'disabled')
                state['mapping_cache'] = {'status': 'mapped', 'profile': profile, 'snapshot': snap,
                    'proposal': proposal, 'enum_operations': prior, 'enum_history': [], 'enum_rounds': 1}
                cfg = {'configurable': {'thread_id': mode}}
                graph.invoke(state, cfg)
                for _ in range(5):
                    current = graph.get_state(cfg).values
                    command = current.get('command')
                    if not graph.get_state(cfg).next or not command:
                        break
                    self.assertEqual(command['kind'], 'fill')
                    degree = next((op for op in command['operations'] if op['id'] == '#degree'), None)
                    if degree:
                        self.assertEqual(degree['value'], '大学本科')
                    after = copy.deepcopy(current['current'])
                    after['snapshot_id'] = command['command_id'] + '-after'
                    for op in command['operations']:
                        next(f for f in after['fields'] if f['id'] == op['id'])['value'] = op['value']
                    graph.invoke(Command(resume={'command_id': command['command_id'], 'kind': 'fill',
                        'target': command['target'], 'settled': True, 'status': 'completed', 'snapshot': after,
                        'results': [{'id': op['id'], 'status': 'already_matched', 'reason': ''}
                                    for op in command['operations']], 'evidence': {}}), cfg)
                final = graph.get_state(cfg).values
                degree = next(op for op in final['operations'] if op['id'] == '#degree')
                self.assertEqual(degree['value'], '大学本科')
                self.assertEqual(degree['enum_provenance']['command_id'], 'original-fill')
                self.assertNotEqual(final['status'], 'readback_mismatch')
                if mode == 'no_pending':
                    self.assertEqual(final['status'], 'verified_draft')
                    self.assertEqual(model.plans[-1]['enum_aliases'][0]['option'], '大学本科')

    def test_changed_source_transform_value_context_or_identity_discards_alias(self):
        profile, snap, proposal, prior = self.fixture()
        for mutation in ('source', 'transform', 'value', 'context', 'missing_context', 'label', 'kind', 'unobserved'):
            with self.subTest(mutation=mutation):
                fresh, _ = compile_plan(profile, snap, proposal)
                changed = copy.deepcopy(profile)
                old = copy.deepcopy(prior)
                if mutation == 'source': fresh[1]['source'] = '/other'
                if mutation == 'transform': fresh[1]['transform'] = 'string'
                if mutation == 'value': changed['degree'] = '硕士'
                if mutation == 'context': old[1]['enum_provenance']['source_context'] = {'major': '旧专业'}
                if mutation == 'missing_context': old[1]['enum_provenance'].pop('source_context')
                if mutation == 'label': fresh[1]['field']['label'] = '学位'
                if mutation == 'kind': fresh[1]['field']['kind'] = 'text'
                if mutation == 'unobserved': old[1]['enum_provenance']['options'] = ['硕士研究生']
                carried = carry_reviewed_enums(changed, fresh, old)[1]
                self.assertNotIn('enum_provenance', carried)
                self.assertEqual(carried['value'], '本科')

    def test_cache_recovery_does_not_restore_changed_or_missing_context(self):
        for missing in (True, False):
            with self.subTest(missing=missing), SqliteSaver.from_conn_string(':memory:') as saver:
                profile, snap, proposal, prior = self.fixture()
                if missing:
                    prior[1]['enum_provenance'].pop('source_context')
                else:
                    prior[1]['enum_provenance']['source_context'] = {'major': '旧专业'}
                state = initial_state(profile, snap)
                state['mapping_cache'] = {'status': 'mapped', 'profile': profile, 'snapshot': snap,
                    'proposal': proposal, 'enum_operations': prior, 'enum_history': [], 'enum_rounds': 1}
                graph = build_graph(EnumModel(), saver, knowledge=object())
                cfg = {'configurable': {'thread_id': 'invalid-cache'}}
                graph.invoke(state, cfg)
                op = next(op for op in graph.get_state(cfg).values['command']['operations'] if op['id'] == '#degree')
                self.assertEqual(op['value'], '本科')
                self.assertNotIn('enum_provenance', op)

    def test_linkage_completed_fields_survive_semantic_merge_for_unknown_peer(self):
        with SqliteSaver.from_conn_string(':memory:') as saver:
            profile, snap, _, prior = self.fixture(pending=True)
            proposal = {'mappings': [], 'deferred': [
                {'field_id': field['id'], 'reason': 'semantic_match_pending'} for field in snap['fields']]}
            model = EnumModel()
            model.match_unknown = lambda profile, snapshot: {'decisions': [
                {'field_id': '#extra', 'classification': 'unsupported', 'source': None,
                 'transform': None, 'depends_on': [], 'reason': 'optional unsupported field left unchanged'}],
                 'record_binding': None}
            state = initial_state(profile, snap)
            state['retained_operations'] = prior
            state['results'] = {op['id']: {'status': 'written', 'reason': ''} for op in prior}
            state['mapping_cache'] = {'status': 'mapped', 'profile': profile, 'snapshot': snap, 'proposal': proposal}
            graph = build_graph(model, saver, knowledge=object(), semantic_model=model)
            cfg = {'configurable': {'thread_id': 'linkage-peer'}}
            graph.invoke(state, cfg)
            final = graph.get_state(cfg).values
            self.assertEqual(final['status'], 'verified_draft')
            self.assertEqual([op['value'] for op in final['operations']], ['示例大学', '大学本科'])
            self.assertEqual(final['results']['#school']['status'], 'already_matched')
            self.assertEqual(final['results']['#degree']['status'], 'already_matched')
            self.assertEqual(model.plans[-1]['enum_aliases'][0]['option'], '大学本科')
