import unittest
from edge_form_graph.linkage import update_linkage, retained_writes, retain_plan_writes, MAX_LINKAGE_DEPTH

def snap(*fields):
    return {'fields': list(fields)}

class LinkageTests(unittest.TestCase):
    def test_fifteen_layers_and_siblings_not_rounds(self):
        state = None
        before = snap({'id': 'root', 'value': ''})
        parent = 'root'
        for depth in range(2, 17):
            field = {'id': str(depth), 'value': ''}
            current = snap(*before['fields'], field)
            state = update_linkage(state, before, current, {'trigger_id': parent, 'added': [str(depth)]})
            parent, before = str(depth), current
        self.assertEqual(MAX_LINKAGE_DEPTH, 15)
        self.assertEqual(state['depths']['15'], 15)
        self.assertEqual(state['blocked'], {'16': 'linkage_depth_limit'})
        for i in range(20):
            fid = 'sibling'+str(i)
            current = snap(*before['fields'], {'id': fid, 'value': ''})
            state = update_linkage(state, before, current, {'trigger_id': 'root', 'added': [fid]})
            self.assertEqual(state['depths'][fid], 2)
            self.assertNotIn(fid, state['blocked'])

    def test_repeated_state_blocks_only_that_branch(self):
        before = snap({'id': 'a', 'value': ''}, {'id': 'b', 'value': ''})
        after = snap({'id': 'a', 'value': ''}, {'id': 'b', 'value': 'x'})
        event = {'trigger_id': 'a', 'changed': ['b']}
        state = update_linkage(None, before, after, event)
        state = update_linkage(state, before, after, event)
        self.assertEqual(state['blocked'], {'b': 'linkage_cycle'})

    def test_keep_only_still_matching_successful_writes(self):
        ops = [{'id': x, 'value': 'ok', 'field': {'signature': {'label': x}}} for x in ['a', 'b']]
        current = snap({'id': 'a', 'value': 'ok', 'signature': {'label': 'a'}},
                       {'id': 'b', 'value': '', 'signature': {'label': 'b'}})
        self.assertEqual([o['id'] for o in retained_writes(ops, {'a': {'status': 'written'}, 'b': {'status': 'written'}}, current)], ['a'])

    def test_changed_canonical_value_or_new_mapping_cannot_reuse_retained_write(self):
        field={'id':'a','selector':'#a','kind':'text','value':'old','signature':{'label':'a'}}
        prior={'id':'a','source':'/name','transform':'identity','value':'old','field':field}
        deferred=[{'field_id':'a','reason':'semantic_match_pending'}]
        results={'a':{'status':'written'}}
        ops, kept=retain_plan_writes({'name':'new'},[],deferred,snap(field),[prior],results)
        self.assertEqual(ops,[]);self.assertEqual(kept,deferred)
        newer={**prior,'source':'/other','value':'new'}
        ops, kept=retain_plan_writes({'name':'old'},[newer],deferred,snap(field),[prior],results)
        self.assertEqual(ops,[newer]);self.assertEqual(kept,deferred)
