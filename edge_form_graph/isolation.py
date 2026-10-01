"""Capture Codex's actual outbound tool inventory against a local mock model.

No real inference, credentials, browser, profile data or external writes. This is
a contract test of the installed CLI, not a simulated website success.
"""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import threading
import time

from edge_form_graph.model import worker_args


class Handler(BaseHTTPRequestHandler):
    inventory = None
    model_name = None
    websocket_attempts = 0
    post_count = 0
    auth_header_present = False
    def log_message(self, *args):
        pass
    def do_GET(self):
        if self.headers.get('Upgrade', '').lower() == 'websocket':
            Handler.websocket_attempts += 1
        self.send_response(400)
        self.end_headers()
    def do_POST(self):
        Handler.post_count += 1
        Handler.auth_header_present |= bool(self.headers.get('Authorization'))
        payload = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        Handler.inventory = payload.get("tools", [])
        Handler.model_name = payload.get("model")
        answer = '{"answer":"contract-test"}'
        output = {"id": "msg_test", "type": "message", "role": "assistant", "status": "completed",
                  "content": [{"type": "output_text", "text": answer, "annotations": []}]}
        response = {"id": "resp_test", "object": "response", "created_at": 1, "status": "completed", "model": "contract-test",
                    "output": [output], "usage": {"input_tokens": 1, "output_tokens": 1, "total_tokens": 2}}
        events = [
            {"type": "response.created", "response": {**response, "status": "in_progress", "output": []}},
            {"type": "response.output_item.added", "output_index": 0, "item": {**output, "status": "in_progress", "content": []}},
            {"type": "response.content_part.added", "item_id": "msg_test", "output_index": 0, "content_index": 0,
             "part": {"type": "output_text", "text": "", "annotations": []}},
            {"type": "response.output_text.delta", "item_id": "msg_test", "output_index": 0, "content_index": 0, "delta": answer},
            {"type": "response.output_text.done", "item_id": "msg_test", "output_index": 0, "content_index": 0, "text": answer},
            {"type": "response.output_item.done", "output_index": 0, "item": output},
            {"type": "response.completed", "response": response},
        ]
        body = "".join("event: " + e["type"] + "\ndata: " + json.dumps(e) + "\n\n" for e in events).encode()
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def probe(model=None):
    provider = 'contract_test'
    Handler.inventory = None
    Handler.model_name = None
    Handler.websocket_attempts = Handler.post_count = 0
    Handler.auth_header_present = False
    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
    try:
        with tempfile.TemporaryDirectory(prefix="edge-model-contract-") as tmp:
            root = Path(tmp); schema = root / "schema.json"; output = root / "out.json"
            schema.write_text(json.dumps({'type': 'object', 'properties': {'answer': {'type': 'string'}},
                'required': ['answer'], 'additionalProperties': False}), encoding="utf-8")
            args = worker_args(shutil.which("codex"), root, schema, output, model)
            args += ["-c", f'model_provider="{provider}"']
            args += ["-c", f'model_providers.{provider}.name="Local contract test"',
                     "-c", f'model_providers.{provider}.base_url="http://127.0.0.1:{server.server_port}"',
                     "-c", f'model_providers.{provider}.wire_api="responses"',
                     "-c", f'model_providers.{provider}.requires_openai_auth=false']
            args += ["-c", 'model_providers.contract_test.supports_websockets=false']
            args += ['-']
            env = dict(os.environ)
            for key in list(env):
                if key.upper().startswith(('OPENAI_', 'CODEX_')):
                    env.pop(key)
            # A local mock never reads account credentials or writes global state.
            env['CODEX_HOME'] = str(root/'codex-home')
            Path(env['CODEX_HOME']).mkdir()
            env['NO_PROXY'] = env['no_proxy'] = '127.0.0.1,localhost'
            proc = subprocess.run(args, input="Reply with the required JSON only.", capture_output=True, text=True,
                encoding="utf-8", errors="replace", timeout=45, env=env,
                creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
            tools = Handler.inventory
            # Names/type only: do not dump system prompts, credentials or request metadata.
            names = None if tools is None else [t.get("name", t.get("type")) for t in tools]
            result = {"codex_exit": proc.returncode, "request_captured": tools is not None, "tools": names,
                      "empty_tool_inventory": tools == [], "structured_output": output.exists(), "model": Handler.model_name,
                      "provider": provider, "websocket_attempts": Handler.websocket_attempts,
                      "post_count": Handler.post_count, "auth_header_present": Handler.auth_header_present,
                      "config_error": 'reserved_builtin_provider' if 'Built-in providers cannot be overridden' in proc.stderr else None}
            return result
    finally:
        server.shutdown(); server.server_close()


def ensure_isolated(model=None):
    """Fail before inference if this CLI exposes tools; cache exact settings for a day."""
    exe = shutil.which("codex")
    if not exe:
        raise RuntimeError("codex_not_installed")
    info = Path(exe).stat()
    version = subprocess.run([exe, "--version"], text=True, capture_output=True, timeout=10,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0).stdout.strip()
    identity = {"executable": exe, "version": version, "mtime_ns": info.st_mtime_ns, "size": info.st_size,
                "probe_contract": 'credential-free-custom-provider-sse-v2',
                "args": worker_args(exe, "WORKER", "SCHEMA", "OUTPUT", model)}
    cache = Path(__file__).resolve().parent.parent / "private" / "isolation.json"
    if cache.exists():
        try:
            prior = json.loads(cache.read_text(encoding="utf-8"))
            cached = prior.get('result', {})
            if (prior.get("identity") == identity and time.time()-prior["checked_at"] < 86400
                    and cached.get("empty_tool_inventory") is True and cached.get('codex_exit') == 0
                    and cached.get('structured_output') is True and cached.get('websocket_attempts') == 0
                    and cached.get('auth_header_present') is False):
                return prior["result"]
        except (ValueError, KeyError):
            pass
    result = probe(model)
    if (not result["empty_tool_inventory"] or result["codex_exit"] or not result["structured_output"]
            or result['websocket_attempts'] or result['auth_header_present']):
        raise RuntimeError("model_tool_isolation_not_verified")
    cache.parent.mkdir(parents=True, exist_ok=True)
    from .cli import atomic_json
    atomic_json(cache, {"identity": identity, "checked_at": time.time(), "result": result})
    return result


if __name__ == "__main__":
    print(json.dumps(ensure_isolated(), ensure_ascii=False, indent=2))
