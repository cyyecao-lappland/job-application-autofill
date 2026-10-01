"""Synthetic profiles only; every store lives in a temporary directory."""
import copy
import json
import multiprocessing
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import Mock, patch

from edge_form_graph.contracts import ContractError, compile_plan
from edge_form_graph.knowledge import KnowledgeError, resolve_record
from tests.knowledge_fixtures import KnowledgeStore


def fixture(source='/school', kind='text', context=None):
    snapshot = {'snapshot_id': 'before', 'observed_at': time.time(), 'module_id': 'education-1',
                'module_selector': '#module',
                'target': {'browser': 'edge', 'browser_id': 'test', 'tab_id': 'test',
                           'url': 'https://example.test/resume?session=synthetic'},
                'fields': [{'id': 'field', 'selector': '#private-selector', 'kind': kind,
                            'label': 'School', 'value': ''}]}
    if context:
        snapshot['mapping_context'] = context
    plan = {'mappings': [{'field_id': 'field', 'source': source,
                          'transform': 'identity', 'depends_on': []}], 'deferred': []}
    return snapshot, plan


def readback(snapshot, value):
    current = copy.deepcopy(snapshot)
    current['snapshot_id'] = 'after'
    current['fields'][0]['value'] = value
    return current


def writer(path, number):
    snapshot, plan = fixture()
    snapshot['fields'][0]['label'] = 'School ' + str(number)
    KnowledgeStore(path).learn_fields({'school': 'synthetic'}, snapshot, plan,
                                     readback(snapshot, 'synthetic'), True)


class KnowledgeTests(unittest.TestCase):
    def test_language_level_and_score_share_the_unique_confirmed_exam(self):
        snapshot,_=fixture(kind='combobox');snapshot['fields'][0]['label']='外语等级'
        profile={'languages':[{'record_id':'cet4','exam_type':'CET-4','score':448,'answer_status':'confirmed'},
                              {'record_id':'cet6','exam_type':'CET-6','score':None,'answer_status':'explicit_none'}]}
        plan=self.store.known_plan(profile,snapshot)
        self.assertEqual(plan['mappings'][0]['source'],'/languages/0/exam_type')
        snapshot['fields'][0].update(label='外语等级-成绩',kind='text')
        plan=self.store.known_plan(profile,snapshot)
        self.assertEqual(compile_plan(profile,snapshot,plan)[0][0]['value'],'448')
        profile['languages'][1].update(score=500,answer_status='confirmed')
        self.assertEqual(self.store.known_plan(profile,snapshot)['mappings'],[])
        for key,value in [('score',''),('score','  '),('score',False),('exam_type','  ')]:
            profile['languages']= [{'record_id':'cet4','exam_type':'CET-4','score':448,'answer_status':'confirmed',key:value}]
            self.assertEqual(self.store.known_plan(profile,snapshot)['mappings'],[])

    def test_prior_employment_default_never_overrides_an_existing_reviewed_mapping(self):
        snapshot,_=fixture(kind='combobox');snapshot['fields'][0]['label']='是否曾在顺丰任职'
        profile={'answer':True,'company_answer_defaults':{'prior_group_employment':{'default':False,'exceptions':['腾讯']}}}
        entry={'status':'active','source':{'pointer':'/answer'},'transform':'yes_no','depends_on':[]}
        with patch('edge_form_graph.knowledge.FieldMappingIndex.scoped_lookup',return_value=entry):
            plan=self.store.known_plan(profile,snapshot)
        self.assertEqual(plan['mappings'][0]['source'],'/answer')
        with patch('edge_form_graph.knowledge.FieldMappingIndex.scoped_lookup',return_value={'status':'conflicted'}):
            plan=self.store.known_plan(profile,snapshot)
        self.assertEqual(plan['mappings'],[])

    def test_highest_school_and_graduation_use_highest_explicit_degree(self):
        snapshot,_=fixture()
        snapshot['fields'][0]['label']='最高学历毕业院校'
        profile={'education':[{'record_id':'b','education_level':'本科','school_name':'B'},
                              {'record_id':'m','education_level':'硕士研究生','school_name':'M','expected_graduation_date':'2027-07-01'}]}
        plan=self.store.known_plan(profile,snapshot)
        self.assertEqual(plan['mappings'][0]['source'],'/education/1/school_name')
        snapshot['fields'][0].update(label='预计毕业时间： 月',date_part='month')
        plan=self.store.known_plan(profile,snapshot)
        ops,_=compile_plan(profile,snapshot,plan)
        self.assertEqual(ops[0]['value'],'7')

    def test_paired_year_month_controls_resolve_bound_record_dates(self):
        context={'record_collection':'/education','record_id':'master','module_type':'education'}
        snapshot,_=fixture(context=context,kind='combobox')
        snapshot['fields']=[dict(snapshot['fields'][0],id=str(i),selector='#f'+str(i),label=f'{endpoint} {part}',
            component='moka-select',control_pattern='year_month_range_parts',range_endpoint=endpoint,date_part=part)
            for i,(endpoint,part) in enumerate([('start','year'),('start','month'),('end','year'),('end','month')])]
        profile={'education':[{'record_id':'master','start_date':'2024-09-06','end_date':'2027-07-01'}]}
        plan=self.store.known_plan(profile,snapshot)
        ops,_=compile_plan(profile,snapshot,plan)
        self.assertEqual([op['value'] for op in ops],['2024','9','2027','7'])

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'nested' / 'knowledge.json'
        self.store = KnowledgeStore(self.path)

    def learn(self, profile=None, snapshot=None, plan=None, approved=True):
        profile = profile or {'school': 'synthetic university'}
        snapshot, plan = (snapshot, plan) if snapshot else fixture()
        op = compile_plan(profile, snapshot, plan)[0][0]
        return self.store.learn_fields(profile, snapshot, plan, readback(snapshot, op['value']), approved)

    def test_lazy_missing_and_explicit_corruption(self):
        self.assertFalse(self.path.parent.exists())
        snapshot, _ = fixture()
        self.assertEqual(self.store.known_plan({}, snapshot)['deferred'][0]['reason'], 'semantic_match_pending')
        self.assertFalse(self.path.parent.exists())
        self.path.parent.mkdir()
        for content in ('broken', '{}', '{"version":1,"field_map":[],"enum_map":{}}',
                        '{"version":1,"field_map":{"x":{}},"enum_map":{}}'):
            self.path.write_text(content, encoding='utf-8')
            with self.assertRaises(KnowledgeError):
                self.store.known_plan({}, snapshot)
            with self.assertRaises(KnowledgeError):
                self.learn()

    def test_second_instance_reuses_without_model_or_private_values(self):
        self.assertEqual(self.learn(), 1)
        self.assertEqual(self.learn(), 0)
        snapshot, _ = fixture()
        snapshot['fields'][0].update(id='new-id', selector='#new', label='  SCHOOL： ')
        model = Mock(side_effect=AssertionError('model must not be called'))
        plan = KnowledgeStore(self.path).known_plan({'school': 'new university'}, snapshot)
        if plan['deferred']:
            model()
        self.assertEqual(plan['mappings'][0]['field_id'], 'new-id')
        self.assertEqual(compile_plan({'school': 'new university'}, snapshot, plan)[0][0]['value'], 'new university')
        model.assert_not_called()
        text = self.path.read_text(encoding='utf-8')
        for private in ('synthetic university', 'private-selector', 'session=synthetic', 'field_id'):
            self.assertNotIn(private, text)

    def test_approval_readback_and_target_required(self):
        snapshot, plan = fixture()
        for approved, current in ((False, readback(snapshot, 'synthetic')),
                                  (1, readback(snapshot, 'synthetic')),
                                  (True, readback(snapshot, 'wrong'))):
            self.assertEqual(self.store.learn_fields({'school': 'synthetic'}, snapshot, plan, current, approved), 0)
        current = readback(snapshot, 'synthetic')
        current['target']['tab_id'] = 'other'
        self.assertEqual(self.store.learn_fields({'school': 'synthetic'}, snapshot, plan, current, True), 0)
        self.assertFalse(self.path.exists())

    def test_conflicts_persist_for_source_and_transform(self):
        for changed in ('source', 'transform'):
            with self.subTest(changed=changed):
                self.store = KnowledgeStore(Path(self.temp.name) / (changed + '.json'))
                snapshot, plan = fixture()
                profile = ({'school': '2026-09-24', 'other': '2026-09-24'}
                           if changed == 'transform'
                           else {'school': 'synthetic', 'other': 'synthetic'})
                self.assertEqual(self.learn(profile, snapshot, plan), 1)
                alternate = copy.deepcopy(plan)
                alternate['mappings'][0][changed] = '/other' if changed == 'source' else 'year_month'
                self.assertEqual(self.learn(profile, snapshot, alternate), 0)
                self.assertEqual(self.learn(profile, snapshot, plan), 0)
                known = KnowledgeStore(self.store.path).known_plan(profile, snapshot)
                self.assertEqual(known, {'mappings': [], 'deferred': [
                    {'field_id': 'field', 'reason': 'semantic_match_pending'}]})
                self.assertEqual(next(iter(json.loads(self.store.path.read_text())['field_map'].values())),
                                 {'status': 'conflicted'})

    def test_contract_transforms_round_trip_with_record_binding(self):
        for rule, value, kind, expected in (
                ('not_bool', False, 'checkbox', True),
                ('year_month', '2026-09-24', 'text', '2026-09'),
                ('join_text', ['synthetic A', 'synthetic B'], 'text', 'synthetic A、synthetic B')):
            with self.subTest(rule=rule):
                store = KnowledgeStore(Path(self.temp.name) / (rule + '.json'))
                context = {'module_type': 'education', 'record_collection': '/education', 'record_id': 'master'}
                snapshot, plan = fixture('/education/0/answer', kind, context)
                plan['mappings'][0]['transform'] = rule
                profile = {'education': [{'id': 'master', 'answer': value}, {'id': 'other', 'answer': value}]}
                self.assertEqual(store.learn_fields(profile, snapshot, plan, readback(snapshot, expected), True), 1)
                profile['education'].reverse()
                known = KnowledgeStore(store.path).known_plan(profile, snapshot)
                self.assertEqual(known['mappings'][0]['source'], '/education/1/answer')
                self.assertEqual(known['mappings'][0]['transform'], rule)
                self.assertEqual(compile_plan(profile, snapshot, known)[0][0]['value'], expected)

    def test_join_text_still_rejects_unbound_and_nested_array_sources(self):
        for profile, source, context in (
                ({'education': [{'id': 'master', 'answers': ['A', 'B']}]}, '/education/0/answers', None),
                ({'education': [{'id': 'master', 'answers': [['A', 'B']]}]}, '/education/0/answers/0',
                 {'module_type': 'education', 'record_collection': '/education', 'record_id': 'master'})):
            snapshot, plan = fixture(source, context=context)
            plan['mappings'][0]['transform'] = 'join_text'
            self.assertEqual(self.store.learn_fields(profile, snapshot, plan, readback(snapshot, 'A、B'), True), 0)

    def test_list_value_sources_re_evaluate_for_join_text_and_native_multiselect(self):
        for kind, rule in (('text', 'join_text'), ('select', 'identity')):
            with self.subTest(kind=kind):
                store = KnowledgeStore(Path(self.temp.name) / (kind + '-list.json'))
                snapshot, plan = fixture('/preferences/cities', kind)
                snapshot['fields'][0]['multiple'] = kind == 'select'
                plan['mappings'][0]['transform'] = rule
                profile = {'preferences': {'cities': ['synthetic A', 'synthetic B']}}
                value = 'synthetic A、synthetic B' if rule == 'join_text' else ['synthetic A', 'synthetic B']
                self.assertEqual(store.learn_fields(profile, snapshot, plan, readback(snapshot, value), True), 1)
                profile['preferences']['cities'] = ['synthetic C', 'synthetic D']
                known = KnowledgeStore(store.path).known_plan(profile, snapshot)
                self.assertEqual(known, plan)
                expected = 'synthetic C、synthetic D' if rule == 'join_text' else ['synthetic C', 'synthetic D']
                self.assertEqual(compile_plan(profile, snapshot, known)[0][0]['value'], expected)
                self.assertNotIn('synthetic A', store.path.read_text(encoding='utf-8'))
                self.assertNotIn('synthetic C', store.path.read_text(encoding='utf-8'))

    def test_records_reorder_and_project_isolation(self):
        context = {'module_type': 'education', 'record_collection': '/education', 'record_id': 'edu_master'}
        snapshot, plan = fixture('/education/0/school', context=context)
        profile = {'education': [{'id': 'edu_master', 'school': 'master'}, {'id': 'edu_bachelor', 'school': 'bachelor'}],
                   'projects': [{'id': 'project', 'school': 'project'}]}
        self.assertEqual(self.learn(profile, snapshot, plan), 1)
        profile['education'].reverse()
        snapshot['module_id'] = 'education-2'
        hit = KnowledgeStore(self.path).known_plan(profile, snapshot)
        self.assertEqual(hit['mappings'][0]['source'], '/education/1/school')
        self.assertEqual(resolve_record(profile, snapshot), '/education/1')
        snapshot['mapping_context']['record_id'] = 'edu_bachelor'
        self.assertEqual(self.store.known_plan(profile, snapshot)['mappings'][0]['source'], '/education/0/school')
        snapshot['mapping_context'] = {'module_type': 'projects', 'record_collection': '/projects', 'record_id': 'project'}
        self.assertEqual(self.store.known_plan(profile, snapshot)['mappings'], [])
        snapshot['mapping_context'] = context | {'record_id': 'missing'}
        self.assertEqual(self.store.known_plan(profile, snapshot)['mappings'], [])
        profile['education'].append(copy.deepcopy(profile['education'][0]))
        snapshot['mapping_context']['record_id'] = 'edu_bachelor'
        with self.assertRaises(ContractError):
            resolve_record(profile, snapshot)

    def test_unbound_and_wrong_bound_arrays_not_learned(self):
        profile = {'education': [{'id': 'a', 'school': 'A'}, {'id': 'b', 'school': 'B'}]}
        for context in (None, {'module_type': 'education', 'record_collection': '/education', 'record_id': 'b'}):
            snapshot, plan = fixture('/education/0/school', context=context)
            self.assertEqual(self.learn(profile, snapshot, plan), 0)

    def test_dependencies_rebind_semantically_without_stored_ids(self):
        snapshot, plan = fixture()
        snapshot['fields'].append({'id': 'dependency-id', 'selector': '#dependency', 'kind': 'text',
                                   'label': 'Country', 'value': ''})
        plan['mappings'][0]['depends_on'] = ['dependency-id']
        plan['mappings'].append({'field_id': 'dependency-id', 'source': '/country',
                                 'transform': 'identity', 'depends_on': []})
        current = readback(snapshot, 'synthetic')
        current['fields'][1]['value'] = 'country'
        profile = {'school': 'synthetic', 'country': 'country'}
        self.assertEqual(self.store.learn_fields(profile, snapshot, plan, current, True), 2)
        snapshot['fields'][1].update(id='new-dependency', selector='#new-dependency')
        known = self.store.known_plan(profile, snapshot)
        self.assertEqual(known['mappings'][0]['depends_on'], ['new-dependency'])
        self.assertNotIn('dependency-id', self.path.read_text())
        self.assertEqual(len(compile_plan(profile, snapshot, known)[0]), 2)

    def test_same_batch_collision_and_ambiguous_live_field(self):
        snapshot, plan = fixture()
        snapshot['fields'].append({**snapshot['fields'][0], 'id': 'second', 'selector': '#second'})
        plan['mappings'].append({**plan['mappings'][0], 'field_id': 'second', 'source': '/other'})
        current = readback(snapshot, 's')
        current['fields'][1]['value'] = 's'
        self.assertEqual(self.store.learn_fields({'school': 's', 'other': 's'}, snapshot, plan, current, True), 0)
        self.assertEqual(self.store.known_plan({'school': 's', 'other': 's'}, snapshot)['mappings'], [])

    def test_deferred_reasons_and_scope(self):
        self.learn()
        snapshot, _ = fixture()
        for edit, expected in (({'kind': 'rich_text'}, 'unsupported_field_kind'),
                               ({'disabled': True}, 'field_not_writable')):
            other = copy.deepcopy(snapshot)
            other['fields'][0].update(edit)
            self.assertEqual(self.store.known_plan({'school': 's'}, other)['deferred'][0]['reason'], expected)
        for url in ('https://other.test/resume', 'https://example.test/other'):
            snapshot['target']['url'] = url
            self.assertFalse(self.store.known_plan({'school': 's'}, snapshot)['mappings'])

    def test_bound_file_control_uses_existing_path_without_model(self):
        upload = Path(self.temp.name) / 'life-photo.jpg'
        upload.write_bytes(b'photo')
        snapshot, _ = fixture('/attachments/0/path', 'file', {
            'module_type': 'attachments', 'record_collection': '/attachments',
            'record_id': 'life-photo'})
        snapshot['fields'][0]['label'] = '生活照'
        profile = {'attachments': [{'record_id': 'life-photo', 'label': '生活照',
                                    'kind': 'life_photo', 'path': str(upload)}]}
        plan = self.store.known_plan(profile, snapshot)
        self.assertEqual(plan['deferred'], [])
        self.assertEqual(plan['mappings'], [{'field_id': 'field',
            'source': '/attachments/0/path', 'transform': 'identity', 'depends_on': []}])
        self.assertEqual(compile_plan(profile, snapshot, plan)[0][0]['value'], str(upload))

    def test_file_control_rejects_record_label_as_path(self):
        snapshot, plan = fixture('/attachments/0/label', 'file', {
            'module_type': 'attachments', 'record_collection': '/attachments',
            'record_id': 'life-photo'})
        snapshot['fields'][0]['label'] = '生活照'
        profile = {'attachments': [{'record_id': 'life-photo', 'label': '生活照'}]}
        with self.assertRaisesRegex(ContractError, 'file_path_missing'):
            compile_plan(profile, snapshot, plan)

    def test_hash_routes_scope_fields_and_enums_without_personal_queries(self):
        profile, snapshot, operations, requests, _ = self.enum_fixture()
        _, plan = fixture('/degree', 'combobox')
        snapshot['target']['url'] = ('https://example.test/resume?session=private-outer'
                                     '#/education?applicant=private-fragment')
        current = readback(snapshot, '大学本科')
        self.assertEqual(self.store.learn_fields(profile, snapshot, plan, current, True,
                                                operations=operations), 1)
        self.assertEqual(self.store.learn_enums(profile, snapshot, operations, current), 1)
        store = KnowledgeStore(self.path)
        for suffix in ('?session=changed#/education?applicant=changed', '#/education'):
            snapshot['target']['url'] = 'https://example.test/resume' + suffix
            self.assertEqual(store.known_plan(profile, snapshot), plan)
            self.assertEqual(store.enum_decisions(profile, snapshot, operations, requests)[0]['option'], '大学本科')
        for suffix in ('#/projects?applicant=private-fragment', ''):
            snapshot['target']['url'] = 'https://example.test/resume' + suffix
            self.assertEqual(store.known_plan(profile, snapshot), {'mappings': [], 'deferred': [
                {'field_id': 'field', 'reason': 'semantic_match_pending'}]})
            self.assertEqual(store.enum_decisions(profile, snapshot, operations, requests), [])
        data = json.loads(self.path.read_text(encoding='utf-8'))
        for name in ('field_map', 'enum_map'):
            key = next(iter(data[name]))
            self.assertEqual(json.loads(key)[1], '/resume#/education')
            self.assertNotIn('private-', key)
            self.assertNotIn('?', key)

    def enum_fixture(self):
        snapshot, plan = fixture('/degree', 'combobox')
        profile = {'degree': '本科'}
        operations = compile_plan(profile, snapshot, plan)[0]
        request = {'field_id': 'field', 'source': '/degree', 'transform': 'identity',
                   'source_value': '本科', 'options': ['大学本科', '硕士研究生']}
        operations[0].update(value='大学本科', enum_provenance={**request, 'option': '大学本科',
            'reason': 'approved model equivalence', 'command_id': 'fill-1', 'snapshot_id': 'observed-1', 'revision': 1})
        return profile, snapshot, operations, [request], readback(snapshot, '大学本科')

    def test_enum_reuse_changed_source_stale_duplicate_options_and_scope(self):
        profile, snapshot, operations, requests, current = self.enum_fixture()
        self.assertEqual(self.store.learn_enums(profile, snapshot, operations, current), 1)
        store = KnowledgeStore(self.path)
        expected = [{'field_id': 'field', 'source_value': '本科', 'option': '大学本科', 'reason': 'reviewed_knowledge'}]
        self.assertEqual(store.enum_decisions(profile, snapshot, operations, requests), expected)
        self.assertEqual(store.enum_decisions({'degree': '硕士'}, snapshot, operations, requests), [])
        for options in (['硕士研究生'], ['大学本科', '大学本科']):
            self.assertEqual(store.enum_decisions(profile, snapshot, operations, [requests[0] | {'options': options}]), [])
        snapshot['module_id'] = 'projects'
        self.assertEqual(store.enum_decisions(profile, snapshot, operations, requests), [])

    def test_enum_requires_accepted_provenance_and_matching_page(self):
        for mutation in ('missing', 'declined', 'wrong_source', 'unobserved', 'wrong_page'):
            profile, snapshot, operations, requests, current = self.enum_fixture()
            if mutation == 'missing':
                operations[0].pop('enum_provenance')
            elif mutation == 'declined':
                operations[0]['enum_provenance']['approved'] = False
            elif mutation == 'wrong_source':
                operations[0]['enum_provenance']['source_value'] = '硕士'
            elif mutation == 'unobserved':
                operations[0]['enum_provenance']['options'] = []
            else:
                current['fields'][0]['value'] = ''
            self.assertEqual(self.store.learn_enums(profile, snapshot, operations, current), 0)

    def test_learn_fields_uses_reviewed_enum_alias_without_persisting_values(self):
        snapshot, plan = fixture('/ethnicity', 'select')
        profile = {'ethnicity': '汉'}
        operations = compile_plan(profile, snapshot, plan)[0]
        operations[0].update(value='汉族', enum_provenance={
            'field_id': 'field', 'source': '/ethnicity', 'transform': 'identity',
            'source_value': '汉', 'option': '汉族', 'options': ['汉族', '其他'],
            'reason': 'approved equivalence', 'command_id': 'fill', 'snapshot_id': 'options'})
        current = readback(snapshot, '汉族')
        original = copy.deepcopy(operations)
        self.assertEqual(self.store.learn_fields(profile, snapshot, plan, current, True), 0)
        self.assertEqual(self.store.learn_fields(profile, snapshot, plan, current, False, operations=operations), 0)
        self.assertEqual(self.store.learn_fields(profile, snapshot, plan, current, True, operations=operations), 1)
        self.assertEqual(KnowledgeStore(self.path).known_plan(profile, snapshot), plan)
        self.assertEqual(operations, original)
        self.assertNotIn('汉', self.path.read_text(encoding='utf-8'))

    def test_learn_fields_rejects_unproven_or_mismatched_alias(self):
        for mutation in ('missing', 'source', 'transform', 'field', 'duplicate', 'missing_operation',
                         'source_value', 'provenance_source', 'provenance_transform', 'field_id',
                         'option', 'reason', 'options', 'duplicate_options', 'approved',
                         'command_id', 'snapshot_id', 'page'):
            with self.subTest(mutation=mutation):
                profile, snapshot, operations, _, current = self.enum_fixture()
                _, plan = fixture('/degree', 'combobox')
                op = operations[0]
                provenance = op['enum_provenance']
                if mutation == 'missing':
                    op.pop('enum_provenance')
                elif mutation in ('source', 'transform'):
                    op[mutation] = '/other' if mutation == 'source' else 'string'
                elif mutation == 'field':
                    op['field']['selector'] = '#other'
                elif mutation == 'duplicate':
                    operations.append(copy.deepcopy(op))
                elif mutation == 'missing_operation':
                    operations = []
                elif mutation.startswith('provenance_'):
                    provenance[mutation.removeprefix('provenance_')] = 'wrong'
                elif mutation == 'duplicate_options':
                    provenance['options'] = [op['value'], op['value']]
                elif mutation == 'page':
                    current['fields'][0]['value'] = 'wrong'
                else:
                    provenance[mutation] = {'approved': False, 'options': [], 'reason': '',
                                            'command_id': '', 'snapshot_id': ''}.get(mutation, 'wrong')
                self.assertEqual(self.store.learn_fields(profile, snapshot, plan, current, True,
                                                        operations=operations), 0)
                self.assertFalse(self.path.exists())

    def test_learn_fields_raw_operations_remain_supported(self):
        snapshot, plan = fixture()
        profile = {'school': 'synthetic'}
        operations = compile_plan(profile, snapshot, plan)[0]
        self.assertEqual(self.store.learn_fields(profile, snapshot, plan, readback(snapshot, 'synthetic'),
                                                True, operations=operations), 1)

    def test_enum_conflict_remains_conflicted(self):
        profile, snapshot, operations, requests, current = self.enum_fixture()
        self.store.learn_enums(profile, snapshot, operations, current)
        operations[0]['value'] = '硕士研究生'
        operations[0]['enum_provenance']['option'] = '硕士研究生'
        current['fields'][0]['value'] = '硕士研究生'
        self.assertEqual(self.store.learn_enums(profile, snapshot, operations, current), 0)
        self.assertEqual(self.store.enum_decisions(profile, snapshot, operations, requests), [])

    def test_enum_bound_source_reorders_and_rebinds_field_id(self):
        profile, snapshot, operations, requests, current = self.enum_fixture()
        profile = {'education': [{'id': 'master', 'degree': '本科'}, {'id': 'other', 'degree': 'other'}]}
        snapshot['mapping_context'] = {'module_type': 'education', 'record_collection': '/education', 'record_id': 'master'}
        current['mapping_context'] = copy.deepcopy(snapshot['mapping_context'])
        operations[0]['source'] = '/education/0/degree'
        operations[0]['enum_provenance']['source'] = '/education/0/degree'
        self.assertEqual(self.store.learn_enums(profile, snapshot, operations, current), 1)
        profile['education'].reverse()
        operations[0].update(id='new-id', source='/education/1/degree')
        operations[0]['field'].update(id='new-id', selector='#new')
        snapshot['fields'][0].update(id='new-id', selector='#new')
        requests[0].update(field_id='new-id', source='/education/1/degree')
        decisions = KnowledgeStore(self.path).enum_decisions(profile, snapshot, operations, requests)
        self.assertEqual(decisions[0]['field_id'], 'new-id')
        self.assertEqual(decisions[0]['option'], '大学本科')
        self.assertNotIn('/education/0', self.path.read_text(encoding='utf-8'))

    def test_structurally_corrupt_active_entry_is_explicit_error(self):
        self.learn()
        data = json.loads(self.path.read_text(encoding='utf-8'))
        entry = next(iter(data['field_map'].values()))
        entry['source'] = {'unrecognized': '/school'}
        self.path.write_text(json.dumps(data), encoding='utf-8')
        with self.assertRaises(KnowledgeError):
            self.store.known_plan({'school': 's'}, fixture()[0])

    def test_atomic_replace_failure_preserves_old_store(self):
        self.learn()
        old = self.path.read_bytes()
        snapshot, plan = fixture()
        snapshot['fields'][0]['label'] = 'Another school'
        with patch('edge_form_graph.knowledge.os.replace', side_effect=OSError('synthetic interruption')):
            with self.assertRaises(OSError):
                self.learn(snapshot=snapshot, plan=plan)
        self.assertEqual(self.path.read_bytes(), old)
        self.assertEqual(list(self.path.parent.glob('*.tmp')), [])

    def test_process_safe_read_modify_write(self):
        context = multiprocessing.get_context('spawn')
        processes = [context.Process(target=writer, args=(str(self.path), i)) for i in range(4)]
        for process in processes:
            process.start()
        for process in processes:
            process.join(20)
            if process.is_alive():
                process.terminate()
                process.join()
            self.assertEqual(process.exitcode, 0)
        self.assertEqual(len(json.loads(self.path.read_text())['field_map']), 4)


if __name__ == '__main__':
    unittest.main()
