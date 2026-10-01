"""Standard-library-only windowed launch API; importing never starts a service."""
import argparse
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import time
import urllib.error
import urllib.request
from uuid import uuid4

from app.config import Settings
from app import SERVICE_API_VERSION
from app.lock import InstanceLock

ROOT = Path(__file__).resolve().parent


def _health(port):
    # Local traffic must not be sent through environment-configured proxies.
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    try:
        with opener.open(f"http://127.0.0.1:{port}/health", timeout=0.5) as response:
            return json.loads(response.read(65536))
    except (OSError, ValueError, urllib.error.URLError):
        return None


def _check(health, settings):
    if (not isinstance(health, dict) or health.get("service") != "local-e5-field-embedding"
            or health.get("configuration") != settings.identity()
            or health.get("api_version") != SERVICE_API_VERSION):
        raise RuntimeError("Port belongs to a different service or E5 configuration; no process was changed")
    return health.get("status") == "ready"


def ensure_running(*, port=8765, timeout=90.0, python_executable=None):
    """Return {status, url, pid} only after READY; reuse a matching service.

    The child survives this function and the caller's normal exit. Configuration
    comes from E5_* environment variables. No model download or dependency install.
    """
    if not 1 <= port <= 65535 or timeout <= 0:
        raise ValueError("port must be 1..65535 and timeout must be positive")
    settings = Settings.from_env()
    deadline = time.monotonic() + timeout
    lock = None
    while lock is None:
        try:
            lock = InstanceLock(settings.data_dir, "launcher.lock")
        except RuntimeError:
            if time.monotonic() >= deadline:
                raise TimeoutError("Another launcher is still starting E5") from None
            time.sleep(0.1)
    process = None
    stop_file = None
    try:
        health = _health(port)
        if health is not None and _check(health, settings):
            return {"status": "reused", "url": f"http://127.0.0.1:{port}", "pid": health["pid"]}
        with socket.socket() as probe:
            try:
                probe.bind(("127.0.0.1", port))
            except OSError:
                raise RuntimeError("Requested port is occupied but no matching READY service was found") from None
        runtime = Path(python_executable) if python_executable else (
            ROOT.parent / ".venv-embedding" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        )
        if not runtime.is_file():
            raise FileNotFoundError("Embedding Python runtime is missing; install requirements first or pass python_executable")
        for name in ("model.onnx", "tokenizer.json"):
            if not (settings.model_dir / name).is_file():
                raise FileNotFoundError(f"Local model file missing: {name}; run prepare_model.py first")
        stop_file = settings.data_dir.resolve() / f"stop-{uuid4().hex}"
        env = dict(os.environ, E5_DATA_DIR=str(settings.data_dir.resolve()),
                   E5_MODEL_DIR=str(settings.model_dir.resolve()))
        command = [str(runtime.resolve()), "-m", "app.daemon", "--port", str(port), "--stop-file", str(stop_file)]
        kwargs = {"creationflags": subprocess.CREATE_NEW_CONSOLE} if os.name == "nt" else {"start_new_session": True}
        process = subprocess.Popen(command, cwd=ROOT, env=env, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **kwargs)
        while time.monotonic() < deadline:
            if process.poll() is not None:
                raise RuntimeError(f"E5 exited before READY; inspect {settings.data_dir / 'service.log'}")
            health = _health(port)
            if health is not None and _check(health, settings):
                state = {"pid": health["pid"], "stop_file": str(stop_file), "port": port,
                         "configuration": settings.identity()}
                state_path = settings.data_dir / "resident.json"
                temporary = state_path.with_suffix(".tmp")
                temporary.write_text(json.dumps(state), encoding="utf-8")
                temporary.replace(state_path)
                return {"status": "started", "url": f"http://127.0.0.1:{port}", "pid": health["pid"]}
            time.sleep(0.1)
        raise TimeoutError("E5 did not become READY in time; shutdown requested for this launch")
    except BaseException:
        if process is not None and stop_file is not None and process.poll() is None:
            # Never kill an existing service or another launcher's process.
            stop_file.touch()
        raise
    finally:
        lock.close()


def main():
    parser = argparse.ArgumentParser(description="Ensure the local E5 service is resident and READY")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--timeout", type=float, default=90)
    parser.add_argument("--python", dest="python_executable")
    args = parser.parse_args()
    try:
        print(json.dumps(ensure_running(**vars(args))))
    except Exception as exc:
        print(json.dumps({"status": "error", "error": str(exc)}))
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
