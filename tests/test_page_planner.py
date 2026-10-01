import unittest
import time
import copy
from edge_form_graph.page_planner import plan_page, coverage_gaps, records_for, row_match
from edge_form_graph.scope_isolation import validate_isolation

TARGET={'url':'https://example.test/resume','browser':'edge','browser_id':'b','tab_id':'t'}

def inventory(rows):
    return {'url':TARGET['url'],'scopes':[{'id':'education','selector':'#card','state':'editing','label':'教育情况',
        'save_selector':'#save','save_label':'保存','editor_selector':'#editor','field_count':4,
        'units':[{'group':'#rows'},{'selector':'#github','label':'Github'}],
        'groups':[{'selector':'#rows','container_selector':'#collection','label':'教育情况',
                   'add_selector':'#add','add_label':'添加教育情况','rows':rows}]}]}

def row(selector,school='',start=''):
    return {'selector':selector,'snapshot':{'fields':[{'label':'学校全称','kind':'text','value':school},
            {'label':'时间 起始日期','kind':'combobox','range_endpoint':'start','value':start}]}}

class PagePlannerTests(unittest.TestCase):
    def test_feishu_empty_repeater_plans_adds_with_count_barriers(self):
        inv={'url':TARGET['url'],'framework':{'family':'feishu','sections':[{
            'id':'education','selector':'#education','label':'教育经历',
            'record_selector':':scope > .right > .component > [class*="apply-form-array-card__"]',
            'records':[],'add_selector':':scope > .right > button','add_label':'添加',
            'snapshot':{'fields':[]}}]}}
        p=plan_page(self.profile,inv,TARGET)
        self.assertTrue(all(m.get('inline_repeater') and m['open_mode']=='add' for m in p['modules']))
        self.assertEqual([m['expected_record_count'] for m in p['modules']],list(range(len(p['modules']))))
        self.assertFalse(any(g['reason']=='record_add_not_yet_supported' for g in p['coverage']['gaps']))
    def test_named_award_editor_binds_unique_canonical_record(self):
        from edge_form_graph.page_planner import bind_opened_record
        module={'id':'award','module_label':'获奖经历'}
        profile={'awards':[{'record_id':'recsys','name':'RecSys Challenge 2025'},
                           {'record_id':'s1','name':'学业奖学金'}, {'record_id':'s2','name':'学业奖学金'}]}
        def bind(names, others=None):
            snap={'fields':[{'label':'奖项名称','kind':'text','value':name} for name in names]}
            return bind_opened_record(profile,module,snap,{'modules':[module]+(others or [])},{})
        self.assertEqual(bind(['RecSys Challenge 2025'])['record_id'],'recsys')
        for names in [[''],['学业奖学金'],['Unrecognized'],['RecSys Challenge 2025','学业奖学金']]:
            self.assertIsNone(bind(names))
        self.assertIsNone(bind(['RecSys Challenge 2025'],[{'id':'other','mapping_context':{
            'record_collection':'/awards','record_id':'recsys'}}]))
        self.assertIsNone(bind_opened_record(profile,module,{'fields':[
            {'label':'奖项名称','kind':'text','value':'Unrecognized'},
            {'label':'描述','kind':'text','value':'RecSys Challenge 2025'}]}, {'modules':[module]},{}))

    def test_open_editor_with_save_control_remains_explicit_save_scope(self):
        inv={'url':TARGET['url'],'framework':{'family':'sf','sections':[{
            'id':'personal','selector':'#personal','label':'个人信息','save_selector':'#save','edit_selector':None,
            'snapshot':{'fields':[{'label':'姓名','kind':'text','value':'A'},{'label':'最高学历','kind':'text','value':''},{'label':'学校','kind':'text','value':''}]}}]}}
        plan=plan_page(self.profile,inv,TARGET)
        self.assertEqual(plan['save_scopes'][0]['mode'],'explicit')
        self.assertEqual(plan['modules'][0]['mapping_context']['module_type'],'personal')
        self.assertNotIn('hold_reason',plan['modules'][0])

    def test_add_selector_uses_filtered_sibling_index(self):
        inv={'url':TARGET['url'],'framework':{'family':'moka','sections':[{
            'id':'education','selector':'#education','label':'教育背景','record_selector':':scope > .record',
            'records':[row('#one')],'add_selector':':scope > button','add_label':'添加',
            'snapshot':{'fields':row('#one')['snapshot']['fields']}}]}}
        plan=plan_page(self.profile,inv,TARGET)
        self.assertEqual(plan['modules'][1]['selector'],'#education > .record:nth-child(2 of .record)')

    def test_collection_at_capacity_is_identified_without_add_button(self):
        inv=inventory([row('#one','A','2024-09')]); group=inv['scopes'][0]['groups'][0]
        group.update(label='',add_selector=None,add_label=None)
        group['rows'][0]['snapshot']['fields'].append({'label':'学历','kind':'combobox','value':'硕士'})
        plan=plan_page(self.profile,inv,TARGET)
        self.assertEqual(plan['modules'][0]['mapping_context']['record_id'],'master')
        self.assertFalse(any(g['reason']=='unknown_collection' for g in plan['coverage']['gaps']))

    def test_missing_exam_does_not_replace_confirmed_language_record(self):
        profile={'languages':[{'record_id':'cet4','language':'英语','answer_status':'confirmed'},
                              {'record_id':'cet6','language':'英语','answer_status':'explicit_none'}]}
        included,excluded=records_for(profile,'languages','语言能力')
        self.assertEqual([binding['record_id'] for binding,_ in included],['cet4'])
        self.assertEqual(excluded[0]['reason'],'no_confirmed_certificate_record')

    def setUp(self):
        self.profile={'education':[{'record_id':'master','school_name':'A','start_date':'2024-09-01'},
            {'record_id':'undergrad-a','school_name':'B','start_date':'2021-03-01'},
            {'record_id':'undergrad-b','school_name':'B','start_date':'2019-09-01'},
            {'record_id':'high','school_name':'C','education_level':'高中'}]}

    def test_reuses_existing_and_adds_both_distinct_undergrad_periods(self):
        plan=plan_page(self.profile,inventory([row('#one','A','2024-09')]),TARGET)
        self.assertEqual([m.get('mapping_context',{}).get('record_id') for m in plan['modules']],
                         ['master','undergrad-a','undergrad-b',None])
        self.assertEqual([m['expected_record_count'] for m in plan['modules'] if m.get('open_mode')=='add'],[1,2])
        self.assertEqual(plan['coverage']['excluded_records'][0]['reason'],'high_school_not_requested')
        self.assertNotIn('mapping_context',plan['modules'][-1])

    def test_blank_row_before_bound_row_keeps_rendered_order(self):
        plan=plan_page(self.profile,inventory([row('#blank'),row('#existing','A','2024-09')]),TARGET)
        self.assertEqual([m['selector'] for m in plan['modules'][:2]],['#blank','#existing'])
        self.assertEqual(plan['modules'][0]['mapping_context']['record_id'],'undergrad-a')
        self.assertEqual(len([m for m in plan['modules'] if m.get('open_mode')=='add']),1)

    def test_unknown_existing_identity_is_not_overwritten(self):
        plan=plan_page(self.profile,inventory([row('#one','Other')]),TARGET)
        self.assertEqual(plan['modules'][0]['hold_reason'],'existing_record_identity_unresolved')
        self.assertTrue(coverage_gaps(plan,{}))

    def test_split_dates_identify_same_school_records_without_treating_month_as_a_date(self):
        candidates=[({'record_id':'a'}, {'school_name':'B','major':'C','start_date':'2021-03-01','end_date':'2024-06-18'}),
                    ({'record_id':'b'}, {'school_name':'B','major':'C','start_date':'2019-09-01','end_date':'2021-03-01'})]
        fields=[{'label':'学校名称','value':'B'},{'label':'专业名称','value':'C'}]
        for endpoint,year,month in [('start','2021','3'),('end','2024','6')]:
            fields.extend({'label':endpoint+' '+part,'range_endpoint':endpoint,'date_part':part,'value':value}
                          for part,value in [('year',year),('month',month)])
        record={'snapshot':{'fields':fields}}
        state,match=row_match(record,candidates,'education')
        self.assertEqual((state,match[0]['record_id']),('matched','a'))
        fields[-1]['value']='7'
        self.assertEqual(row_match(record,candidates,'education'),('ambiguous',None))

    def test_split_start_date_matches_a_named_project_and_rejects_incomplete_or_duplicate_parts(self):
        candidates=[({'record_id':'p'}, {'name':'P','start_date':'2026-08'})]
        fields=[{'label':'项目名称','value':'P'},
                {'label':'起止时间 开始 年','range_endpoint':'start','date_part':'year','value':'2026'},
                {'label':'起止时间 开始 月','range_endpoint':'start','date_part':'month','value':'8'}]
        self.assertEqual(row_match({'snapshot':{'fields':fields}},candidates,'projects')[0],'matched')
        for value in ['', '13','9']:
            changed=copy.deepcopy(fields);changed[-1]['value']=value
            self.assertEqual(row_match({'snapshot':{'fields':changed}},candidates,'projects'),('ambiguous',None))
        self.assertEqual(row_match({'snapshot':{'fields':fields+[fields[-1]]}},candidates,'projects'),('ambiguous',None))

    def test_sf_education_sibling_records_bind_only_with_record_facts(self):
        profile={'education':[
            {'record_id':'master','education_level':'硕士研究生','school_name':'华东师范大学',
             'major':'计算机科学与技术','start_date':'2024-09-06','end_date':'2027-07-01','is_current':True},
            {'record_id':'computer','education_level':'本科','school_name':'湘潭大学',
             'major':'计算机科学与技术','start_date':'2021-03-01','end_date':'2024-06-18',
             'graduation_type':'毕业','degree_award_date':'2024-06-18'},
            {'record_id':'materials','education_level':'本科','school_name':'湘潭大学',
             'major':'材料科学与工程','start_date':'2019-09-01','end_date':'2021-03-01',
             'degree':None,'transfer_history':{'to_record_id':'computer'}}]}
        master=row('#master','华东师范大学','2024-09')
        master['snapshot']['fields'] += [
            {'label':'学历','kind':'combobox','value':'硕士研究生','disabled':True},
            {'label':'专业','kind':'text','value':'计算机科学与技术'}]
        blank={'selector':'#new-bachelor','record_boundary':{'record_index':1,'record_count':2},
               'snapshot':{'fields':[{'label':'学历','kind':'combobox','value':'大学本科','disabled':True},
                   {'label':'学校全称','kind':'text','value':''},
                   {'label':'专业','kind':'text','value':''},
                   {'label':'入学时间','kind':'text','value':''}]}}
        inv={'url':TARGET['url'],'framework':{'family':'sf','sections':[{
            'id':'education','selector':'#educationList','label':'教育信息','record_selector':':scope > .education-edit-item__wrapper',
            'records':[master,blank],'snapshot':{'fields':master['snapshot']['fields']}}]}}
        plan=plan_page(profile,inv,TARGET)
        self.assertEqual([m.get('mapping_context',{}).get('record_id') for m in plan['modules']],
                         ['master','computer'])
        self.assertTrue(any(g.get('record_id')=='materials' for g in plan['coverage']['gaps']))

    def test_sf_bachelor_level_alone_does_not_choose_between_two_records(self):
        profile={'education':[{'record_id':'computer','education_level':'本科','school_name':'湘潭大学',
                               'major':'计算机科学与技术','start_date':'2021-03-01'},
                              {'record_id':'materials','education_level':'本科','school_name':'湘潭大学',
                               'major':'材料科学与工程','start_date':'2019-09-01'}]}
        row_with_level={'selector':'#bachelor','snapshot':{'fields':[
            {'label':'学历','kind':'combobox','value':'大学本科','disabled':True},
            {'label':'学校全称','kind':'text','value':'湘潭大学'}]}}
        state,match=row_match(row_with_level,
            [({'record_collection':'/education','record_id':r['record_id']},r) for r in profile['education']],
            kind='education')
        self.assertEqual(state,'ambiguous')
        self.assertIsNone(match)

    def test_opened_empty_editor_gets_unreserved_record_but_nonempty_does_not(self):
        from edge_form_graph.page_planner import bind_opened_record
        module={'id':'open','module_label':'教育经历'}
        other={'id':'other','mapping_context':{'record_collection':'/education','record_id':'master'}}
        snap={'fields':[{'id':'school','label':'学校名称','kind':'text','value':''}]}
        binding=bind_opened_record(self.profile,module,snap,{'modules':[module,other]}, {})
        self.assertEqual(binding['record_id'],'undergrad-a')
        snap['fields'][0]['value']='Other'
        self.assertIsNone(bind_opened_record(self.profile,module,snap,{'modules':[module,other]}, {}))
        snap['fields'][0]['value']=''
        snap['fields'].append(dict(snap['fields'][0],id='another-school'))
        self.assertIsNone(bind_opened_record(self.profile,module,snap,{'modules':[module]}, {}))

    def test_coverage_detects_expected_record_removed_from_manifest(self):
        plan=plan_page(self.profile,inventory([row('#one','A','2024-09')]),TARGET)
        removed=plan['modules'].pop(1)
        gaps=coverage_gaps(plan,{m['id']:{} for m in plan['modules']})
        self.assertTrue(any(g.get('record_id')==removed['mapping_context']['record_id'] for g in gaps))

    def test_bound_existing_record_removes_stale_add_gap_but_preserves_unfinished_coverage(self):
        binding={'record_collection':'/education','record_id':'one'}
        gap={**binding,'scope':'a','reason':'record_add_not_yet_supported'}
        module={'id':'m','save_scope':'a','mapping_context':binding}
        plan={'modules':[module],'coverage':{'gaps':[gap], 'expected_records':[{**binding,'scope':'a'}]}}
        gaps=coverage_gaps(plan,{'m':{'coverage_hold':'module_blocked'}})
        self.assertFalse(any(g['reason']=='record_add_not_yet_supported' for g in gaps))
        self.assertTrue(any(g['reason']=='canonical_record_not_completed' for g in gaps))
        self.assertEqual(plan['coverage']['gaps'],[gap])
        for extra in ({'hold_reason':'identity_unresolved'},{'open_mode':'add'},{'save_scope':'b'}):
            changed=copy.deepcopy(plan);changed['modules'][0].update(extra)
            self.assertIn(gap,coverage_gaps(changed,{}))

    def test_record_completion_does_not_cover_same_record_in_another_save_scope(self):
        binding={'record_collection':'/education','record_id':'one'}
        plan={'modules':[{'id':'m','save_scope':'a','mapping_context':binding}],
              'coverage':{'expected_records':[{**binding,'scope':'a'},{**binding,'scope':'b'}]}}
        self.assertEqual(coverage_gaps(plan,{'m':{'status':'verified_draft'}}),[
            {**binding,'scope':'b','reason':'canonical_record_not_completed'}])

    def test_combined_experience_excludes_user_omission_and_linked_duplicate(self):
        p={'employment':[{'record_id':'omit','autofill_policy':{'include_by_default':False}},{'record_id':'work'}],
           'projects':[{'record_id':'same','related_record_ids':['work']},{'record_id':'other'}]}
        items,exclusions=records_for(p,'combined_experience','实习/项目经历')
        self.assertEqual([b['record_id'] for b,r in items],['work','other'])
        self.assertEqual(len(exclusions),2)

    def test_unpublished_research_not_promoted_to_publication(self):
        p={'research':[{'record_id':'draft','venue':'X','status':'在审'},
                       {'record_id':'published','venue':'Y','status':'已发表'}]}
        items,exclusions=records_for(p,'research','论文发表信息')
        self.assertEqual([b['record_id'] for b,r in items],['published'])

    def test_scope_isolation_rejects_unsettled_stale_or_overlapping_evidence(self):
        plan=plan_page(self.profile,inventory([row('#one')]),TARGET)
        for m in plan['modules']:m['hold_reason']='prior_save_unknown'
        blocker={'command_id':'old','kind':'save_scope','settled':True,'target':TARGET,'module_selector':'#old'}
        plan['scope_isolation']={'target':TARGET,'observed_at':time.time(),'blocks':[
            {'command_id':'old','selector':'#old','unique':True,'scope_relations':{'education':'contained'}}]}
        self.assertTrue(validate_isolation(plan,{'blockers':[blocker]}))
        self.assertFalse(validate_isolation(plan,{'blockers':[{**blocker,'settled':False}]}))
        wrong=copy.deepcopy(plan);wrong['scope_isolation']['blocks'][0]['scope_relations']['education']='overlap'
        self.assertFalse(validate_isolation(wrong,{'blockers':[blocker]}))
        plan['scope_isolation']['observed_at']-=121
        self.assertFalse(validate_isolation(plan,{'blockers':[blocker]}))

if __name__=='__main__':unittest.main()
