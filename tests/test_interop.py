import json
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest

from langgraph.checkpoint.sqlite import SqliteSaver
from langgraph.types import Command

from edge_form_graph.cli import atomic_json, load, publish
from edge_form_graph.graph import build_graph, initial_state
from tests.test_graph import Model, snapshot


@unittest.skipUnless(shutil.which("node"), "Node runtime required")
class InteropTests(unittest.TestCase):
    def test_graph_packet_executes_in_javascript_and_resumes_after_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            folder = Path(tmp); config = {"configurable": {"thread_id": "module"}}
            before = snapshot(); model = Model(); db = str(folder / "checkpoints.sqlite")
            atomic_json(folder / "policy.json", {"target": before["target"], "module_id": before["module_id"],
                        "module_selector": before["module_selector"], "fill": True, "save": True})
            atomic_json(folder / "fixture-snapshot.json", before)
            with SqliteSaver.from_conn_string(db) as saver:
                graph = build_graph(model, saver)
                graph.invoke(initial_state({"school": "示例大学"}, before, allow_save=True), config)
                publish(folder, graph.get_state(config))
            runner = Path(__file__).with_name("fixture_host.mjs")
            first = subprocess.run([shutil.which("node"), str(runner), tmp], capture_output=True, text=True, encoding="utf-8", timeout=30)
            self.assertEqual(first.returncode, 0, first.stderr)
            result = load(folder / "receipt.json")
            self.assertEqual(result["results"][0]["status"], "written")
            journal = load(folder / "writer" / (result["command_id"] + ".json"))
            self.assertEqual(journal["receipt"], result)
            with SqliteSaver.from_conn_string(db) as saver:
                graph = build_graph(model, saver)
                graph.invoke(Command(resume=result), config)
                state = graph.get_state(config)
                self.assertEqual(state.values["status"], "awaiting_save")
                publish(folder, state)
                atomic_json(folder / "fixture-snapshot.json", state.values["current"])
            saved = subprocess.run([shutil.which("node"), str(runner), tmp], capture_output=True, text=True, encoding="utf-8", timeout=30)
            self.assertEqual(saved.returncode, 0, saved.stderr)
            result = load(folder / "receipt.json")
            self.assertEqual(result["status"], "saved")
            with SqliteSaver.from_conn_string(db) as saver:
                graph = build_graph(model, saver)
                graph.invoke(Command(resume=result), config)
                self.assertEqual(graph.get_state(config).values["status"], "saved")
            self.assertEqual(model.calls, 2)
