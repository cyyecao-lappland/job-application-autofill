import copy
import tempfile
import time
import unittest
from pathlib import Path

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from edge_form_graph.contracts import ContractError, compile_plan, transform
from edge_form_graph.graph import build_graph, initial_state


def snapshot():
    return {"snapshot_id": "snapshot-1", "observed_at": time.time(), "module_id": "education",
        "module_selector": "#education", "target": {"browser": "edge", "browser_id": "edge-instance", "tab_id": "existing-tab", "url": "https://example.test/resume"},
        "fields": [{"id": "#school", "selector": "#school", "kind": "text", "label": "学校", "value": "", "required": True,
                    "signature": {"tag": "INPUT", "label": "学校"}}],
        "save": {"selector": "#save", "label": "保存", "enabled": True, "signal": {"selector": "#saved", "text": "保存成功"}}, "capture": {}}


class Model:
    calls = 0
    def map(self, profile, before):
        self.calls += 1
        return {"mappings": [{"field_id": "#school", "source": "/school", "transform": "identity", "depends_on": []}], "deferred": []}
    def review(self, profile, before, plan, after):
        self.calls += 1
        return {"approved": True, "checked_field_ids": [f["id"] for f in before["fields"]], "issues": []}


def receipt(command, *, status="written"):
    after = snapshot()
    after["snapshot_id"] = "snapshot-2"
    after["fields"][0]["value"] = "示例大学"
    return {"command_id": command["command_id"], "kind": command["kind"], "target": command["target"],
        "settled": status != "unknown", "status": "unknown" if status == "unknown" else "completed",
        "results": [{"id": "#school", "status": status, "reason": ""}], "snapshot": after,
        "evidence": {"executor": "synthetic-test"}}


class GraphTests(unittest.TestCase):
    def test_settled_fill_rebinds_unique_field_signature_when_the_selector_changes(self):
        with SqliteSaver.from_conn_string(':memory:') as saver:
            graph=build_graph(Model(),saver);config={'configurable':{'thread_id':'selector-change'}}
            graph.invoke(initial_state({'school':'示例大学'},snapshot()),config)
            response=receipt(graph.get_state(config).values['command'])
            response['snapshot']['fields'][0].update(id='#fresh',selector='#fresh')
            graph.invoke(Command(resume=response),config)
            state=graph.get_state(config).values
            self.assertEqual(state['status'],'verified_draft')
            self.assertEqual(state['current']['fields'][0]['id'],'#school')
            self.assertEqual(state['current']['fields'][0]['selector'],'#fresh')
            self.assertEqual(state['last_receipt'],response)
            self.assertIsNone(state['command'])

    def test_multi_record_snapshot_cannot_be_mapped_as_one_bound_module(self):
        s=snapshot();s['mapping_context']={'record_collection':'/education','record_id':'master'}
        s['record_boundary']={'record_count':2,'record_index':None,'scope_record_count':2}
        s['fields'][0]['record_container']='#educationList > .record:nth-of-type(1)'
        s['fields'].append({**s['fields'][0],'id':'#other','selector':'#other',
                            'record_container':'#educationList > .record:nth-of-type(2)'})
        model=Model();before=model.calls
        with SqliteSaver.from_conn_string(':memory:') as saver:
            graph=build_graph(model,saver);config={'configurable':{'thread_id':'boundary'}}
            graph.invoke(initial_state({'school':'示例大学'},s),config)
            state=graph.get_state(config).values
        self.assertEqual(state['status'],'record_boundary_changed')
        self.assertIsNone(state['command'])
        self.assertEqual(model.calls,before)

    def test_minimum_is_capped_at_ten_without_limiting_packet_size(self):
        class Many(Model):
            def map(self, profile, before):
                return {"mappings": [{"field_id": f["id"], "source": "/school", "transform": "identity", "depends_on": []}
                                     for f in before["fields"]], "deferred": []}
        for count in (0, 3, 10, 11, 40):
            with self.subTest(count=count), SqliteSaver.from_conn_string(":memory:") as saver:
                s = snapshot()
                s["fields"] = [{**s["fields"][0], "id": f"#f{i}", "selector": f"#f{i}"} for i in range(count)]
                graph = build_graph(Many(), saver)
                config = {"configurable": {"thread_id": "t"}}
                if not count:
                    with self.assertRaisesRegex(ContractError, "no_editable_fields_in_module"):
                        initial_state({"school": "示例大学"}, s)
                    continue
                graph.invoke(initial_state({"school": "示例大学"}, s), config)
                command = graph.get_state(config).values["command"]
                if count:
                    self.assertEqual(len(command["operations"]), count)
                    self.assertEqual(command["min_fill_count"], min(count, 10))
                else:
                    self.assertIsNone(command)

    def test_source_not_model_literal_and_no_script(self):
        proposal = Model().map({}, {})
        proposal["mappings"][0]["value"] = "编造学校"
        with self.assertRaises(ContractError):
            compile_plan({"school": "示例大学"}, snapshot(), proposal)

    def test_null_not_no_and_boolean_rule(self):
        self.assertEqual(transform(True, "not_yes_no"), "否")
        with self.assertRaises(ContractError):
            transform(None, "not_yes_no")
        self.assertEqual(transform("1970-02-01", "age_from_birth_date"), "56")
        self.assertEqual(transform("无", "none_string_no"), "否")

    def test_boolean_identity_is_normalized_for_yes_no_radio_group(self):
        s=snapshot();field=s['fields'][0]
        field.update(kind='radio_group',options=[{'label':'是'},{'label':'否'}])
        proposal=Model().map({},{});proposal['mappings'][0]['source']='/answer'
        operation=compile_plan({'answer':False},s,proposal)[0][0]
        self.assertEqual(operation['transform'],'yes_no')
        self.assertEqual(operation['value'],'否')

    def test_boolean_identity_is_normalized_for_closed_combobox(self):
        s=snapshot();field=s['fields'][0]
        field.update(kind='combobox',options=[])
        proposal=Model().map({},{});proposal['mappings'][0]['source']='/answer'
        operation=compile_plan({'answer':True},s,proposal)[0][0]
        self.assertEqual(operation['transform'],'yes_no')
        self.assertEqual(operation['value'],'是')

    def test_wrong_browser_rejected(self):
        s = snapshot(); s["target"]["browser"] = "chrome"
        with self.assertRaises(ContractError):
            initial_state({}, s)

    def test_all_fields_must_be_accounted_for(self):
        with self.assertRaises(ContractError):
            compile_plan({}, snapshot(), {"mappings": [], "deferred": []})

    def test_source_resolved_by_program(self):
        ops, _ = compile_plan({"school": "示例大学"}, snapshot(), Model().map({}, {}))
        self.assertEqual(ops[0]["value"], "示例大学")

    def test_text_identity_normalizes_numeric_and_flat_string_list_sources(self):
        proposal = Model().map({}, {})
        proposal["mappings"][0]["source"] = "/score"
        numeric = compile_plan({"score": 491}, snapshot(), proposal)[0][0]
        self.assertEqual((numeric["value"], numeric["transform"]), ("491", "string"))
        proposal["mappings"][0]["source"] = "/skills"
        listed = compile_plan({"skills": ["Python", "SQL"]}, snapshot(), proposal)[0][0]
        self.assertEqual((listed["value"], listed["transform"]), ("Python、SQL", "join_text"))
        proposal["mappings"][0].update(source="/school", transform="join_text")
        scalar = compile_plan({"school": "示例大学"}, snapshot(), proposal)[0][0]
        self.assertEqual((scalar["value"], scalar["transform"]), ("示例大学", "identity"))

    def test_text_max_length_uses_same_record_curated_short_variant(self):
        s = snapshot(); s["fields"][0]["max_length"] = 10
        p = Model().map({}, {}); p["mappings"][0]["source"] = "/employment/0/responsibilities"
        profile = {"employment": [{"responsibilities": "这是超过控件长度的完整职责正文",
                                    "texts": {"short": "短版职责"}}]}
        op = compile_plan(profile, s, p)[0][0]
        self.assertEqual((op["source"], op["value"]), ("/employment/0/texts/short", "短版职责"))

    def test_disabled_cascade_can_be_planned_after_parent(self):
        s = snapshot()
        s["fields"].append({"id": "#city", "selector": "#city", "kind": "select", "label": "城市", "disabled": True, "value": "", "options": []})
        p = Model().map({}, {})
        p["mappings"].append({"field_id": "#city", "source": "/city", "transform": "identity", "depends_on": ["#school"]})
        ops, _ = compile_plan({"school": "示例大学", "city": "示例市"}, s, p)
        self.assertEqual([o["id"] for o in ops], ["#school", "#city"])

    def test_dependency_cycle_rejected(self):
        s = snapshot(); s["fields"].append({**s["fields"][0], "id": "#other", "selector": "#other"})
        p = Model().map({}, {}); p["mappings"][0]["depends_on"] = ["#other"]
        p["mappings"].append({"field_id": "#other", "source": "/school", "transform": "identity", "depends_on": ["#school"]})
        with self.assertRaises(ContractError):
            compile_plan({"school": "示例大学"}, s, p)

    def test_personal_source_uses_ordinary_value_path(self):
        p = Model().map({}, {}); p["mappings"][0]["source"] = "/password"
        operations, deferred = compile_plan({"password": "test"}, snapshot(), p)
        self.assertEqual(operations[0]['value'], 'test')
        self.assertEqual(deferred, [])

    def test_stale_snapshot_rejected(self):
        s = snapshot(); s["observed_at"] -= 301
        with self.assertRaises(ContractError):
            initial_state({}, s)

    def test_order_persistence_and_save_gate(self):
        with tempfile.TemporaryDirectory() as d:
            config = {"configurable": {"thread_id": "t"}}
            db = str(Path(d) / "state.sqlite")
            model = Model()
            with SqliteSaver.from_conn_string(db) as saver:
                graph = build_graph(model, saver)
                graph.invoke(initial_state({"school": "示例大学"}, snapshot(), allow_save=True), config)
                state = graph.get_state(config)
                command = state.values["command"]
                self.assertEqual(state.next, ("execute_fill",))
                self.assertEqual(command["kind"], "fill")
                self.assertIsNone(state.values["reviewed_revision"])
            # A fresh graph/process uses the same checkpoint and does not rerun mapping.
            with SqliteSaver.from_conn_string(db) as saver:
                graph = build_graph(model, saver)
                graph.invoke(Command(resume=receipt(command)), config)
                state = graph.get_state(config)
                self.assertEqual(state.next, ("execute_save",))
                self.assertEqual(state.values["command"]["kind"], "save")
                self.assertEqual(model.calls, 2)
                nodes = [e["node"] for e in state.values["trace"]]
                self.assertLess(nodes.index("validate"), nodes.index("execute_fill"))
                self.assertLess(nodes.index("verify"), nodes.index("prepare_save"))
                save = state.values["command"]
                r = {"command_id": save["command_id"], "kind": "save", "target": save["target"], "settled": True,
                    "status": "saved", "results": [], "snapshot": None, "evidence": {"save_confirmed": True}}
                graph.invoke(Command(resume=r), config)
                self.assertEqual(graph.get_state(config).values["status"], "saved")

    def test_unknown_does_not_retry_or_save(self):
        with SqliteSaver.from_conn_string(":memory:") as saver:
            graph = build_graph(Model(), saver); config = {"configurable": {"thread_id": "t"}}
            graph.invoke(initial_state({"school": "示例大学"}, snapshot(), allow_save=True), config)
            command = graph.get_state(config).values["command"]
            graph.invoke(Command(resume=receipt(command, status="unknown")), config)
            state = graph.get_state(config)
            self.assertEqual(state.values["status"], "needs_reconciliation")
            self.assertFalse(state.next)
            self.assertEqual(state.values["command"], command)

    def test_receipt_target_cannot_be_switched(self):
        with SqliteSaver.from_conn_string(":memory:") as saver:
            graph = build_graph(Model(), saver); config = {"configurable": {"thread_id": "t"}}
            graph.invoke(initial_state({"school": "示例大学"}, snapshot()), config)
            r = receipt(graph.get_state(config).values["command"])
            r["target"] = {**r["target"], "tab_id": "another-tab"}
            with self.assertRaises(ContractError):
                graph.invoke(Command(resume=r), config)

    def test_readback_lie_does_not_save(self):
        with SqliteSaver.from_conn_string(":memory:") as saver:
            graph = build_graph(Model(), saver); config = {"configurable": {"thread_id": "t"}}
            graph.invoke(initial_state({"school": "示例大学"}, snapshot(), allow_save=True), config)
            r = receipt(graph.get_state(config).values["command"])
            r["snapshot"]["fields"][0]["value"] = "另一学校"
            graph.invoke(Command(resume=r), config)
            state = graph.get_state(config).values
            self.assertEqual(state["status"], "readback_mismatch")
            self.assertEqual(state["last_receipt"], r)

    def test_draft_mode_does_not_issue_save(self):
        with SqliteSaver.from_conn_string(":memory:") as saver:
            graph = build_graph(Model(), saver); config = {"configurable": {"thread_id": "t"}}
            graph.invoke(initial_state({"school": "示例大学"}, snapshot()), config)
            graph.invoke(Command(resume=receipt(graph.get_state(config).values["command"])), config)
            self.assertEqual(graph.get_state(config).values["status"], "verified_draft")

    def test_rejected_review_does_not_save(self):
        class Reject(Model):
            def review(self, *args):
                return {"approved": True, "checked_field_ids": [], "issues": []}
        with SqliteSaver.from_conn_string(":memory:") as saver:
            graph = build_graph(Reject(), saver); config = {"configurable": {"thread_id": "t"}}
            graph.invoke(initial_state({"school": "示例大学"}, snapshot(), allow_save=True), config)
            graph.invoke(Command(resume=receipt(graph.get_state(config).values["command"])), config)
            self.assertEqual(graph.get_state(config).values["status"], "review_rejected")

    def test_new_fields_require_discovery_before_save(self):
        with SqliteSaver.from_conn_string(":memory:") as saver:
            graph = build_graph(Model(), saver); config = {"configurable": {"thread_id": "t"}}
            graph.invoke(initial_state({"school": "示例大学"}, snapshot(), allow_save=True), config)
            r = receipt(graph.get_state(config).values["command"])
            r["snapshot"]["fields"].append({"id": "#new", "kind": "text", "value": ""})
            graph.invoke(Command(resume=r), config)
            self.assertEqual(graph.get_state(config).values["status"], "rediscovery_required")

    def test_many_fields_use_two_model_calls_not_per_field(self):
        s = snapshot()
        s["fields"] = [{**s["fields"][0], "id": "#f"+str(i), "selector": "#f"+str(i)} for i in range(40)]
        class BatchModel(Model):
            def map(self, profile, before):
                self.calls += 1
                return {"mappings": [{"field_id": f["id"], "source": "/school", "transform": "identity", "depends_on": []} for f in before["fields"]], "deferred": []}
        model = BatchModel()
        with SqliteSaver.from_conn_string(":memory:") as saver:
            graph = build_graph(model, saver); config = {"configurable": {"thread_id": "t"}}
            graph.invoke(initial_state({"school": "示例大学"}, s), config)
            current = copy.deepcopy(s)
            for _ in range(1):
                command = graph.get_state(config).values["command"]
                self.assertEqual(len(command["operations"]), 40)
                self.assertEqual(command["min_fill_count"], 10)
                for op in command["operations"]:
                    next(f for f in current["fields"] if f["id"] == op["id"])["value"] = op["value"]
                graph.invoke(Command(resume={"command_id": command["command_id"], "kind": "fill", "target": command["target"],
                    "settled": True, "status": "completed", "results": [{"id": op["id"], "status": "written", "reason": ""} for op in command["operations"]],
                    "snapshot": copy.deepcopy(current), "evidence": {"executor": "synthetic"}}), config)
            self.assertEqual(graph.get_state(config).values["status"], "verified_draft")
            self.assertEqual(model.calls, 2)


if __name__ == "__main__":
    unittest.main()
