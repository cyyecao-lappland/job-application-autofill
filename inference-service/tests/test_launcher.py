import importlib.util
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time

import pytest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("e5_launcher", ROOT / "start_service.py")
launcher = importlib.util.module_from_spec(spec)
spec.loader.exec_module(launcher)


def test_reuses_only_identical_service(tmp_path, monkeypatch):
    monkeypatch.setenv("E5_DATA_DIR", str(tmp_path))
    settings = launcher.Settings.from_env()
    health = {"service": "local-e5-field-embedding", "configuration": settings.identity(),
              "api_version": launcher.SERVICE_API_VERSION,
              "status": "ready", "pid": 123}
    monkeypatch.setattr(launcher, "_health", lambda _: health)
    monkeypatch.setattr(launcher.subprocess, "Popen", lambda *a, **kw: pytest.fail("must not spawn"))
    assert launcher.ensure_running()["status"] == "reused"
    health["api_version"] = "old"
    with pytest.raises(RuntimeError, match="different service"):
        launcher.ensure_running()
    health["api_version"] = launcher.SERVICE_API_VERSION
    health["configuration"]["model_version"] = "other"
    with pytest.raises(RuntimeError, match="different service"):
        launcher.ensure_running()


def test_occupied_port_is_not_modified(tmp_path, monkeypatch):
    monkeypatch.setenv("E5_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(launcher, "_health", lambda _: None)
    monkeypatch.setattr(launcher.subprocess, "Popen", lambda *a, **kw: pytest.fail("must not spawn"))
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        sock.listen()
        with pytest.raises(RuntimeError, match="occupied"):
            launcher.ensure_running(port=sock.getsockname()[1])


def test_missing_model_does_not_spawn(tmp_path, monkeypatch):
    monkeypatch.setenv("E5_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("E5_MODEL_DIR", str(tmp_path / "missing-model"))
    monkeypatch.setattr(launcher, "_health", lambda _: None)
    monkeypatch.setattr(launcher.subprocess, "Popen", lambda *a, **kw: pytest.fail("must not spawn"))
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    with pytest.raises(FileNotFoundError, match="model file"):
        launcher.ensure_running(port=port, python_executable=sys.executable)


@pytest.mark.parametrize("model_version", ["v1-base", "v1-fp16"])
def test_real_resident_survives_launcher_and_concurrent_reuse(tmp_path, model_version):
    model_dir = ROOT / "models" / model_version
    if not (model_dir / "model.onnx").exists():
        pytest.skip("local real model not installed")
    from concurrent.futures import ThreadPoolExecutor
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        port = sock.getsockname()[1]
    env = dict(os.environ, E5_DATA_DIR=str(tmp_path),
               E5_MODEL_DIR=str(model_dir), E5_MODEL_VERSION=model_version)
    # Separate callers exit; their child must stay alive and be reused.
    def launch():
        result = subprocess.run(
            [sys.executable, str(ROOT / "start_service.py"), "--port", str(port), "--timeout", "30"],
            env=env, cwd=ROOT, capture_output=True, text=True, timeout=45,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        return json.loads(result.stdout)
    try:
        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda _: launch(), range(2)))
        assert sorted(r["status"] for r in results) == ["reused", "started"]
        assert len({r["pid"] for r in results}) == 1
        assert launcher._health(port)["status"] == "ready"
        assert launcher._health(port)["precision"] == ("FP16" if model_version == "v1-fp16" else "INT8")
        assert launch()["status"] == "reused"
        state = json.loads((tmp_path / "resident.json").read_text(encoding="utf-8"))
        assert state["pid"] == results[0]["pid"]
        assert (tmp_path / "service.log").exists()
    finally:
        state_path = tmp_path / "resident.json"
        if state_path.exists():
            state = json.loads(state_path.read_text(encoding="utf-8"))
            Path(state["stop_file"]).touch()
            deadline = time.monotonic() + 15
            while time.monotonic() < deadline and launcher._health(port) is not None:
                time.sleep(0.1)
            # Lock release verifies shutdown has drained and closed SQLite.
            while True:
                try:
                    lock = launcher.InstanceLock(tmp_path)
                    lock.close()
                    break
                except RuntimeError:
                    if time.monotonic() >= deadline:
                        raise
                    time.sleep(0.1)
            assert launcher._health(port) is None
