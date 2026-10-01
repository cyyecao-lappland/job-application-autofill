import copy
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from edge_form_graph.contracts import compile_plan
from edge_form_graph.enum_repair import context_decisions,review_requests,rank_option,validate_review,invalidate_unsupported_rank_aliases
from edge_form_graph.knowledge import KnowledgeStore
from tests.test_field_mapping import page


class RankSourceTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.store=KnowledgeStore(Path(self.temp.name)/'knowledge.json')
        self.profile={'education':[{'record_id':'unpublished','rank_position':None,'rank_total':None},
                                   {'record_id':'ranked','rank_position':1,'rank_total':124,'rank_band':None}]}
        self.before=page('教育背景','专业排名','combobox')
        self.before['mapping_context']={'record_collection':'/education','record_id':'ranked'}

    def test_numeric_rank_is_a_source_for_observed_enum_review_in_its_bound_record(self):
        plan=self.store.known_plan(self.profile,self.before)
        self.assertEqual(plan['mappings'][0]['source'],'/education/1/rank_position')
        operations,deferred=compile_plan(self.profile,self.before,plan)
        self.assertEqual(operations[0]['value'],'1')
        requests=review_requests(self.profile,operations,[{'field_id':operations[0]['id'],
            'source_value':'1','options':['前5%','前10%','前30%']}])
        self.assertEqual(context_decisions(requests)[0]['option'],'前5%')
        self.assertEqual(deferred,[])

    def test_unknown_invalid_or_differently_bound_rank_is_not_generated(self):
        self.before['mapping_context']['record_id']='unpublished'
        self.assertEqual(self.store.known_plan(self.profile,self.before)['mappings'],[])
        self.before['mapping_context']['record_id']='ranked'
        for position,total in [(0,124),(125,124),(True,124),(1,0),(1,None),(1,True)]:
            self.profile['education'][1].update(rank_position=position,rank_total=total)
            self.assertEqual(self.store.known_plan(self.profile,self.before)['mappings'],[])

    def test_duplicate_observed_threshold_labels_do_not_produce_an_enum_decision(self):
        operations,_=compile_plan(self.profile,self.before,self.store.known_plan(self.profile,self.before))
        requests=review_requests(self.profile,operations,[{'field_id':operations[0]['id'],
            'source_value':'1','options':['前5%','前5%','前10%']}])
        self.assertEqual(context_decisions(requests),[])

    def test_numeric_rank_uses_full_one_based_partition_and_no_conflicting_band(self):
        request={'field_id':'rank','field_label':'专业排名','source_value':'1',
                 'source_context':{'rank_position':1,'rank_total':124},
                 'options':['1%-5%','6%-10%','11%-30%','31%-50%','51%-70%','71%-100%']}
        self.assertEqual(rank_option(request),'1%-5%')
        request['source_context']={'rank_position':18,'rank_total':100,'rank_band':'10%-20%'}
        self.assertEqual(rank_option(request),'11%-30%')
        request['source_context']['rank_band']='20%-30%'
        self.assertIsNone(rank_option(request))
        request['source_context']={'rank_position':18,'rank_total':100}
        request['options']=request['options'][2:]
        self.assertIsNone(rank_option(request))

    def test_top_thirty_does_not_prove_a_lower_bound_of_eleven(self):
        request={'field_id':'rank','field_label':'专业排名','source_value':'前30%',
                 'source_context':{'rank_band':'前30%'},
                 'options':['1%-5%','6%-10%','11%-30%','31%-50%','51%-70%','71%-100%']}
        self.assertIsNone(rank_option(request))
        self.assertEqual(validate_review({'decisions':[{'field_id':'rank','source_value':'前30%',
            'option':'11%-30%','reason':'proposed narrowing'}]},[request]),{})
        request['options']=['前30%']
        self.assertEqual(rank_option(request),'前30%')

    def test_overlapping_and_duplicate_bucket_partitions_are_rejected(self):
        request={'source_context':{'rank_position':18,'rank_total':100},
                 'options':['1%-5%','6%-10%','11%-30%','31%-50%','51%-70%','71%-100%']}
        for extra in ('1%-5%','2%-8%','20%-35%','0%-1%','20%-11%','1%-101%'):
            self.assertIsNone(rank_option({**request,'options':request['options']+[extra]}))

    def test_unsupported_old_written_rank_is_held_without_erasing_its_actual_value(self):
        profile={'education':[{'record_id':'master','rank_band':'前30%'}]}
        field={'id':'rank','label':'专业排名','value':'11%-30%'}
        operation={'id':'rank','source':'/education/0/rank_band','transform':'identity','value':'11%-30%',
            'field':field,'enum_provenance':{'field_id':'rank','source_value':'前30%',
                'options':['1%-5%','6%-10%','11%-30%','31%-50%','51%-70%','71%-100%'],
                'option':'11%-30%','reason':'old proposal'}}
        results={'master':{'current':{'fields':[field]},'operations':[operation],
            'results':{'rank':{'status':'written'}},'review':{'approved':True},'reviewed_revision':2}}
        before=copy.deepcopy(results)
        checked=invalidate_unsupported_rank_aliases(profile,[{'id':'master'}],results)
        self.assertEqual(results,before)
        self.assertEqual(checked['master']['coverage_hold'],'unsupported_rank_enum')
        self.assertEqual(checked['master']['current']['fields'][0]['value'],'11%-30%')
        self.assertIsNone(checked['master']['reviewed_revision'])
        self.assertEqual(checked['master']['invalid_enum_history'][0]['previous_result'],{'status':'written'})
        self.assertEqual(invalidate_unsupported_rank_aliases(profile,[{'id':'master'}],checked),checked)
        from edge_form_graph.application import build_application
        from edge_form_graph.storage import SqliteSaver
        from tests.test_graph import Model
        with SqliteSaver.from_conn_string(':memory:') as saver:
            graph=build_application(Model(),saver)
            state={'profile':profile,'deadline':time.time()+60,'index':0,'results':results,
                   'manifest':{'modules':[{'id':'master','save_scope':'education'}],'target':{}}}
            with patch('edge_form_graph.parallel.ParallelWorkers.review_scope') as review:
                update=graph.nodes['review_boundary'].bound.invoke(state)
            review.assert_not_called()
            self.assertEqual(update['status'],'scope_deferred')
            self.assertEqual(update['results']['master']['coverage_hold'],'unsupported_rank_enum')

    def test_unbound_or_ambiguous_repeated_rank_fields_are_not_generated(self):
        self.before['mapping_context'].pop('record_id')
        self.assertEqual(self.store.known_plan(self.profile,self.before)['mappings'],[])
        self.before['mapping_context']['record_id']='ranked'
        duplicate=copy.deepcopy(self.before['fields'][0]);duplicate.update(id='#other',selector='#other')
        self.before['fields'].append(duplicate)
        self.assertEqual(self.store.known_plan(self.profile,self.before)['mappings'],[])
        duplicate['label']='成绩排名';duplicate['signature']['label']='成绩排名'
        self.assertEqual(self.store.known_plan(self.profile,self.before)['mappings'],[])
        duplicate['label']='  专业排名 ＊ ';duplicate['signature']['label']=duplicate['label']
        self.assertEqual(self.store.known_plan(self.profile,self.before)['mappings'],[])

if __name__=='__main__':unittest.main()
