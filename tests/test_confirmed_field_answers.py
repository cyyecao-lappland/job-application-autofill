import copy
import tempfile
import unittest
from pathlib import Path

from langgraph.checkpoint.sqlite import SqliteSaver

from edge_form_graph.confirmed_field_policies import apply_field_policies, preserved_field_reason
from edge_form_graph.contracts import compile_plan
from edge_form_graph.field_mapping import FieldMappingIndex
from edge_form_graph.graph import build_graph, initial_state
from edge_form_graph.knowledge import KnowledgeStore
from tests import test_field_mapping as fixtures
from tests.test_graph import Model

page = fixtures.page


class ConfirmedFieldAnswerTests(unittest.TestCase):
    plan = fixtures.FieldMappingTests.plan

    def test_birth_date_with_computed_age_label_preserves_the_complete_date(self):
        before = page('个人信息', '出生日期 (年龄)')
        before['fields'][0].update(component='moka-date', readonly=True, control_status='recognized')
        profile = {'identity': {'birth_date': '2001-12-03'}}
        plan = self.plan([], profile, before)
        operations, _ = compile_plan(profile, before, plan)
        self.assertEqual(operations[0]['source'], '/identity/birth_date')
        self.assertEqual(operations[0]['transform'], 'identity')
        self.assertEqual(operations[0]['value'], '2001-12-03')

    def test_education_boolean_requires_bound_record_and_explicit_value(self):
        profile = {'education': [{'record_id': 'a', 'is_top_up': True},
                                 {'record_id': 'b', 'is_top_up': False, 'is_joint_program': False}]}
        for label in ['是否专升本', '该学历是否为联合办学']:
            before = page('教育信息', label, 'combobox')
            before['mapping_context'] = {'record_collection': '/education', 'record_id': 'b'}
            for _ in range(2):
                plan = self.plan([], profile, before)
                self.assertEqual(compile_plan(profile, before, plan)[0][0]['value'], '否')
                profile['education'].reverse()
            before.pop('mapping_context')
            self.assertEqual(self.plan([], profile, before)['mappings'], [])

    def test_unknown_education_boolean_is_not_no(self):
        before = page('education', '是否专升本', 'combobox')
        before['mapping_context'] = {'record_collection': '/education', 'record_id': 'b'}
        for value in [None, '否']:
            profile = {'education': [{'record_id': 'b', 'is_top_up': value}]}
            self.assertEqual(self.plan([], profile, before)['mappings'], [])

    def test_shopee_answer_is_isolated_by_tenant_and_record(self):
        before = page('个人信息', '是否校园大使推荐', 'combobox')
        profile = {'company_answers': [{'record_id': 'company-other', 'campus_ambassador': True},
                                      {'record_id': 'company-shopee', 'campus_ambassador': False}]}
        for tenant, expected in [('shopee', '否'), ('other', None), ('shopee-lookalike', None)]:
            before['target']['url'] = 'https://app.mokahr.com/campus_apply/' + tenant + '/2962'
            plan = self.plan([], profile, before)
            if expected:
                self.assertEqual(compile_plan(profile, before, plan)[0][0]['value'], expected)
            else:
                self.assertEqual(plan['mappings'], [])

    def test_poizon_ai_answer_corrects_old_project_and_stays_scoped(self):
        label = '请描述一个与 AI 协作完成的项目或任务（请说明项目背景、遇到的问题、解决方案以及最终的结果）'
        before = page('AI技能运用：', label)
        before['target']['url'] = 'https://poizon.jobs.feishu.cn/578078/resume/apply'
        before['fields'][0]['value'] = 'Old Ministry project'
        profile = {'personal_answers': {'ai_collaboration_project_answer': 'V project; I design/review/test; AI implements'}}
        plan = self.plan([], profile, before)
        self.assertEqual(compile_plan(profile, before, plan)[0][0]['value'], profile['personal_answers']['ai_collaboration_project_answer'])
        for host, module in [('other.jobs.feishu.cn', 'AI技能运用：'), ('poizon.jobs.feishu.cn', '其它问题')]:
            before['target']['url'] = 'https://' + host + '/resume'
            before['module_label'] = module
            self.assertIsNone(FieldMappingIndex({}).lookup(before, label))

    def test_user_managed_code_blocks_cached_or_model_mapping_without_clear(self):
        before = page(label='推荐码')
        before['fields'][0]['value'] = 'existing-code'
        profile = {'company_answer_defaults': {'referral_code_entry_policy': 'user_managed'}}
        fid = before['fields'][0]['id']
        old = {'mappings': [{'field_id': fid, 'source': '/code', 'transform': 'identity', 'depends_on': []}], 'deferred': []}
        untouched = copy.deepcopy(before)
        result = apply_field_policies(profile, before, old)
        self.assertEqual(result['mappings'], [])
        self.assertEqual(result['deferred'][0]['reason'], 'unsupported: user_managed_referral_code')
        self.assertEqual(before, untouched)
        self.assertEqual(len(old['mappings']), 1)

    def test_no_portfolio_does_not_omit_resume_transcript_or_link_text(self):
        profile = {'collection_status': {'portfolio_attachments': 'explicit_none'}}
        for label, kind, expected in [('作品附件', 'file', True), ('简历附件', 'file', False),
                                     ('成绩单', 'file', False), ('作品链接', 'text', False)]:
            before = page('作品', label, kind)
            self.assertEqual(bool(preserved_field_reason(profile, before, before['fields'][0])), expected)
        before = page('作品上传', 'file', 'file')
        self.assertIsNotNone(preserved_field_reason(profile, before, before['fields'][0]))
        self.assertIsNone(preserved_field_reason({}, before, before['fields'][0]))

    def test_graph_never_dispatches_code_even_when_model_maps_it(self):
        before = page(label='推荐码')
        before['fields'][0]['required'] = False
        profile = {'school': 'replacement-code',
                   'company_answer_defaults': {'referral_code_entry_policy': 'user_managed'}}
        with SqliteSaver.from_conn_string(':memory:') as saver:
            graph = build_graph(Model(), saver)
            cfg = {'configurable': {'thread_id': 'user-code'}}
            graph.invoke(initial_state(profile, before, allow_save=False), cfg)
            value = graph.get_state(cfg).values
            self.assertIsNone(value['command'])
            self.assertEqual(value['operations'], [])
            self.assertEqual(value['results'][before['fields'][0]['id']]['reason'], 'unsupported: user_managed_referral_code')

    def test_semantic_worker_is_not_asked_to_fill_user_managed_code(self):
        class Unexpected(Model):
            def match_unknown(self, *args):
                raise AssertionError('user-owned field reached semantic worker')
        before = page(label='推荐码')
        before['fields'][0]['required'] = False
        profile = {'company_answer_defaults': {'referral_code_entry_policy': 'user_managed'}}
        with tempfile.TemporaryDirectory() as tmp, SqliteSaver.from_conn_string(':memory:') as saver:
            graph = build_graph(Unexpected(), saver, knowledge=KnowledgeStore(Path(tmp) / 'knowledge.json'))
            cfg = {'configurable': {'thread_id': 'no-code-semantics'}}
            graph.invoke(initial_state(profile, before, allow_save=False), cfg)
            value = graph.get_state(cfg).values
            self.assertIsNone(value['command'])
            self.assertEqual(value['metrics'].get('semantic_fields', 0), 0)
