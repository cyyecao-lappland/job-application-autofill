"""Persist connection attempts before dispatch; no browser access or cancellation."""
import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import tempfile

ROUTES = {"getTab", "tabs.get", "user.claimTab"}
RETRYABLE = {"unsupported", "stale", "unattached"}
OUTCOMES = RETRYABLE | {"verified", "occupied", "timeout", "unknown", "interrupted"}


class Rejected(ValueError):
    pass


def target_matches(target, observed):
    return all(observed.get(k) == v for k, v in target.items())


def transition(state, action, data):
    if action == "init":
        if state is not None:
            raise Rejected("Existing state cannot be reset; inspect it")
        target = {"browser": data["browser"], "url": data["url"]}
        for key in ("title", "providerTabId"):
            if data.get(key):
                target[key] = data[key]
        return {"version": 1, "target": target, "status": "prepared", "attempts": []}
    if not isinstance(state, dict) or state.get("version") != 1:
        raise Rejected("Missing or unsupported state")
    if action == "reserve":
        if state["status"] not in {"prepared", "retryable"}:
            raise Rejected("Connection is pending, verified, or stopped")
        if len(state["attempts"]) >= 2:
            raise Rejected("Connection attempt limit reached")
        route = data["route"]
        if route not in ROUTES or any(a["route"] == route for a in state["attempts"]):
            raise Rejected("Unsupported or repeated route")
        if not data.get("tabId") or not target_matches(state["target"], data):
            raise Rejected("Candidate does not match the intended browser/page")
        # reserve 先记录 pending，update 落盘后才返回派发许可；中断不清零尝试次数。
        state["attempts"].append({"id": len(state["attempts"]) + 1,
                                  "route": route, "tabId": data["tabId"],
                                  "status": "pending"})
        state["status"] = "pending"
        return state
    if action == "finish":
        if state["status"] != "pending" or data["attempt"] != state["attempts"][-1]["id"]:
            raise Rejected("No matching pending attempt")
        outcome = data["outcome"]
        if outcome not in OUTCOMES:
            raise Rejected("Unsupported outcome")
        # Host timeouts do not prove that remote work has ended.
        settled = data.get("settled") is True
        if outcome == "verified":
            if not settled or data.get("probePassed") is not True or not target_matches(state["target"], data):
                raise Rejected("Verification needs settled call and matching read-only probe")
        state["attempts"][-1].update(status=outcome, settled=settled)
        if outcome == "verified":
            state["status"] = "verified"
        elif settled and outcome in RETRYABLE and len(state["attempts"]) < 2:
            state["status"] = "retryable"
        else:
            state["status"] = "stopped"
        return state
    raise Rejected("Unsupported action")


@contextmanager
def locked(root):
    root.mkdir(parents=True, exist_ok=True)
    # 互斥范围仅是本运行目录的状态文件，不代表取得了浏览器标签页的独占权。
    lock = root / "connection-state.lock"
    try:
        fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError as exc:
        raise Rejected("State is locked; do not dispatch or remove a lock blindly") from exc
    try:
        os.close(fd)
        yield
    finally:
        lock.unlink()


def update(root, action, data):
    with locked(root):
        path = root / "connection-state.json"
        state = json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
        if action == "show":
            if state is None:
                raise Rejected("Missing state")
            return state
        state = transition(state, action, data)
        # 同目录临时文件写完并刷盘后替换，避免下一次读取到半份 JSON。
        fd, temporary = tempfile.mkstemp(prefix="connection-state-", suffix=".tmp", dir=root)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as stream:
                json.dump(state, stream, ensure_ascii=False, indent=2)
                stream.write("\n")
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, path)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return state


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--run", type=Path, required=True)
    commands = parser.add_subparsers(dest="action", required=True)
    init = commands.add_parser("init")
    reserve = commands.add_parser("reserve")
    finish = commands.add_parser("finish")
    commands.add_parser("show")
    for command in (init, reserve, finish):
        command.add_argument("--browser", required=command is not finish)
        command.add_argument("--url", required=command is not finish)
        command.add_argument("--title")
        command.add_argument("--provider-tab-id", dest="providerTabId")
    reserve.add_argument("--route", choices=sorted(ROUTES), required=True)
    reserve.add_argument("--tab-id", dest="tabId", required=True)
    finish.add_argument("--attempt", type=int, required=True)
    finish.add_argument("--outcome", choices=sorted(OUTCOMES), required=True)
    finish.add_argument("--settled", action="store_true")
    finish.add_argument("--probe-passed", dest="probePassed", action="store_true")
    args = parser.parse_args()
    try:
        state = update(args.run, args.action, vars(args))
        result = {"ok": True, "state": state}
        if args.action == "reserve":
            result.update(dispatchAllowed=True, attempt=state["attempts"][-1]["id"],
                          operationTimeoutMs=120000, minimumOuterTimeoutMs=125000)
        print(json.dumps(result, ensure_ascii=False))
    except (Rejected, OSError, ValueError, KeyError, TypeError) as exc:
        # No raw browser errors or form values are accepted or logged.
        print(json.dumps({"ok": False, "dispatchAllowed": False,
                          "reason": str(exc) if isinstance(exc, Rejected) else "State read/write failed"}))
        raise SystemExit(1)


if __name__ == "__main__":
    main()
