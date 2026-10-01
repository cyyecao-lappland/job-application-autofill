"""Cross-site field identity stays independent of control execution."""
import copy
import json
import tempfile
import unittest
from pathlib import Path

from edge_form_graph.field_mapping import FieldMappingIndex
from edge_form_graph.contracts import compile_plan, transform, ContractError
from edge_form_graph.knowledge import KnowledgeStore
from tests.test_graph import snapshot


def entry(module, label, source, *, site='old.example', kind='text', **extra):
    key = json.dumps([site, '/resume', module, label, kind], ensure_ascii=False)
    return key, {'status': 'active', 'source': source, 'transform': 'identity', 'depends_on': [], **extra}


def page(module='personal', label='姓名', kind='text'):
    result = snapshot()
    result.update(module_id=module, module_label=module)
    result['target']['url'] = 'https://new.example/resume'
    result['fields'][0].update(label=label, kind=kind)
    result['fields'][0]['signature']['label'] = label
    return result


class FieldMappingTests(unittest.TestCase):
    def test_alibaba_education_level_does_not_require_awarded_degree(self):
        before = page('教育情况','学历','combobox')
        before['target']['url']='https://campus-talent.alibaba.com/personal/resume'
        before['mapping_context']={'record_collection':'/education','record_id':'transfer'}
        rule=FieldMappingIndex({}).lookup(before,'学历')
        self.assertEqual(rule['source']['relative'],'/education_level')
        self.assertEqual(transform('本科',rule['transform']),'本科')
        self.assertEqual(transform('硕士研究生',rule['transform']),'硕士')
        with self.assertRaises(ContractError): transform('工学学士',rule['transform'])

    def test_explicit_address_city(self):
        self.assertEqual(transform('湖南省衡阳市雁峰区', 'china_city_path'), '中国-湖南-衡阳')
        self.assertEqual(transform('上海市普陀区', 'china_city_path'), '中国-上海-上海')
        self.assertEqual(transform('湖南省衡阳市雁峰区', 'address_city'), '衡阳')
        self.assertEqual(transform('上海市普陀区', 'address_city'), '上海')
        for value in ('普陀区', '', '湖南', None):
            with self.assertRaises(ContractError):
                transform(value, 'address_city')

    def test_school_city_requires_unique_current_education(self):
        profile = {'education': [{'record_id':'old','campus_location':'北京市','is_current':False},
                                 {'record_id':'current','campus_location':'上海市普陀区','is_current':True}]}
        before = page(label='学校所在城市', kind='combobox')
        for index in (1, 0):
            plan = self.plan([], profile, before)
            self.assertEqual(plan['mappings'][0]['source'], f'/education/{index}/campus_location')
            self.assertEqual(compile_plan(profile, before, plan)[0][0]['value'], '上海')
            profile['education'].reverse()
        profile['education'][0]['is_current'] = True
        self.assertEqual(self.plan([], profile, before)['mappings'], [])

    def plan(self, entries, profile, before):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / 'knowledge.json'
            path.write_text(json.dumps({'version': 1, 'field_map': dict(entries), 'enum_map': {}}), encoding='utf-8')
            return KnowledgeStore(path).known_plan(profile, before)

    def test_existing_saved_relation_reused_across_site_module_alias_and_control(self):
        rules = [entry('personalInfo', '姓名', {'pointer': '/identity/name'})]
        profile = {'identity': {'name': 'Synthetic Person'}}
        for kind in ('text', 'select', 'combobox'):
            before = page('个人信息', '您的姓名', kind)
            plan = self.plan(rules, profile, before)
            self.assertEqual(plan['mappings'][0]['source'], '/identity/name')
            self.assertEqual(compile_plan(profile, before, plan)[0][0]['value'], 'Synthetic Person')

    def test_standard_schema_mapping_works_without_prior_website_rule(self):
        profile = {'identity': {'name': 'Synthetic'}, 'contact': {'email': 'synthetic@example.test'}}
        for label, pointer in [('姓名', '/identity/name'), ('电子邮箱', '/contact/email')]:
            plan = self.plan([], profile, page(label=label))
            self.assertEqual(plan['mappings'][0]['source'], pointer)

    def test_semantic_lookup_ignores_kind_even_when_executor_cannot_use_value(self):
        before = page(kind='checkbox')
        semantic = FieldMappingIndex({}).lookup(before, '姓名')
        self.assertEqual(semantic['source'], {'pointer': '/identity/name'})
        # The execution contract separately rejects a name as a checkbox value.
        self.assertEqual(self.plan([], {'identity': {'name': 'Synthetic'}}, before)['mappings'], [])

    def test_same_label_in_another_module_does_not_read_personal_name(self):
        self.assertEqual(self.plan([], {'identity': {'name': 'Synthetic'}}, page('家庭成员'))['mappings'], [])

    def test_education_record_resolved_by_id_after_reordering(self):
        rules = [entry('education', '学校', {'collection': '/education', 'relative': '/school_name'})]
        profile = {'education': [{'record_id': 'bachelor', 'school_name': 'B'},
                                  {'record_id': 'master', 'school_name': 'M'}]}
        before = page('教育情况', '学校全称', 'combobox')
        before['mapping_context'] = {'record_collection': '/education', 'record_id': 'master'}
        for source in ('/education/1/school_name', '/education/0/school_name'):
            self.assertEqual(self.plan(rules, profile, before)['mappings'][0]['source'], source)
            profile['education'].reverse()
        before.pop('mapping_context')
        self.assertEqual(self.plan(rules, profile, before)['mappings'], [])

    def test_conflicting_aliases_do_not_choose_first_or_fall_back_to_default(self):
        rules = [entry('personalInfo', '姓名', {'pointer': '/identity/name'}),
                 entry('personal', '您的姓名', {'pointer': '/identity/other'}, site='second.example')]
        self.assertEqual(self.plan(rules, {'identity': {'name': 'A', 'other': 'B'}}, page())['mappings'], [])

    def test_durable_conflict_cannot_be_revived_by_schema_default(self):
        key, _ = entry('personalInfo', '姓名', {'pointer': '/identity/name'})
        self.assertEqual(self.plan([(key, {'status': 'conflicted'})],
                                   {'identity': {'name': 'A'}}, page())['mappings'], [])

    def test_company_answer_never_becomes_universal(self):
        rules = [entry('personal', '公司亲属', {'collection': '/company_answers',
                      'record_id': 'company-a', 'relative': '/relative'})]
        profile = {'company_answers': [{'record_id': 'company-a', 'relative': '无'}]}
        self.assertEqual(self.plan(rules, profile, page(label='公司亲属'))['mappings'], [])

    def test_conditions_stay_scoped_but_do_not_depend_on_control_kind(self):
        rules = [entry('personalInfo', '额外联系方式', {'pointer': '/contact/email'},
                       conditions=[{'pointer': '/allow_extra', 'equals': True}])]
        profile = {'contact': {'email': 'synthetic@example.test'}, 'allow_extra': True}
        before = page('personal', '额外联系方式', 'combobox')
        self.assertEqual(self.plan(rules, profile, before)['mappings'], [])
        before['target']['url'] = 'https://old.example/resume'
        self.assertEqual(self.plan(rules, profile, before)['mappings'][0]['source'], '/contact/email')
        profile['allow_extra'] = False
        self.assertEqual(self.plan(rules, profile, before)['mappings'], [])

    def test_duplicate_semantic_field_is_ambiguous_regardless_of_control_kind(self):
        before = page()
        duplicate = copy.deepcopy(before['fields'][0])
        duplicate.update(id='#second', selector='#second', label='您的姓名', kind='combobox')
        before['fields'].append(duplicate)
        self.assertEqual(self.plan([], {'identity': {'name': 'Synthetic'}}, before)['mappings'], [])


if __name__ == '__main__':
    unittest.main()
