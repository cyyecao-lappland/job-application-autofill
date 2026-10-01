"""Opt-in real Luna call with fictitious data only; no browser or applicant JSON."""
import json
from pathlib import Path
import sys
import time

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from edge_form_graph.model import CodexJsonModel
from edge_form_graph.semantic import compile_semantic

profile = {'education': [{'record_id': 'synthetic_master', 'school': '示例大学', 'start_date': '2024-09', 'still_enrolled': True}]}
snapshot = {'snapshot_id': 'synthetic-luna-smoke', 'observed_at': time.time(), 'module_id': 'education',
    'module_label': '硕士教育经历', 'module_selector': '#synthetic',
    'mapping_context': {'module_type': 'education', 'record_collection': '/education', 'record_id': 'synthetic_master'},
    'target': {'browser': 'edge', 'browser_id': 'synthetic', 'tab_id': 'synthetic', 'url': 'https://example.test/resume'},
    'fields': [{'id': '#start', 'selector': '#start', 'label': '就读起始年月', 'kind': 'text', 'value': ''},
               {'id': '#diploma', 'selector': '#diploma', 'label': '是否已取得本阶段毕业证', 'kind': 'select', 'value': '',
                'options': [{'label': '是'}, {'label': '否'}]}]}
started = time.monotonic()
model = CodexJsonModel(model='gpt-6-luna', timeout=600)
response = model.match_unknown(profile, snapshot)
proposal, bound, notes = compile_semantic(profile, snapshot, response)
from edge_form_graph.contracts import compile_plan
operations, deferred = compile_plan(profile, bound, proposal)
actual = {op['id']: op['value'] for op in operations}
result = {'model': model.model, 'elapsed_seconds': round(time.monotonic()-started, 3),
    'fields': len(snapshot['fields']), 'model_calls': model.calls,
    'passed': actual == {'#start': '2024-09', '#diploma': '否'} and not deferred,
    'decisions': notes, 'timings': model.last_timings, 'evidence': 'real_model_synthetic_data_no_browser'}
print(json.dumps(result, ensure_ascii=False))
if not result['passed']:
    raise SystemExit(1)
