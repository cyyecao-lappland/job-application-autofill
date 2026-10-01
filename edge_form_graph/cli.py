from __future__ import annotations

import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import sys
import time
import uuid

from .storage import SqliteSaver
from langgraph.types import Command

from .contracts import ContractError, validate_snapshot
from .graph import build_graph, initial_state
from .model import CodexJsonModel


def load(path):
    from .runtime import local_path
    return json.loads(local_path(path).read_text(encoding="utf-8-sig"))


def atomic_json(path, value):
    tmp = path.with_name(path.name + "." + str(uuid.uuid4()) + ".tmp")
    with tmp.open("x", encoding="utf-8") as file:
        json.dump(value, file, ensure_ascii=False, indent=2)
        file.flush()
        os.fsync(file.fileno())
    os.replace(tmp, path)


@contextmanager
def owner_lock(folder):
    """OS-released coordinator lock, not a claim to lock other browser clients."""
    with (folder / "coordinator.lock").open("a+b") as file:
        file.seek(0, 2)
        if not file.tell():
            file.write(b"0"); file.flush()
        file.seek(0)
        if os.name == "nt":
            import msvcrt
            msvcrt.locking(file.fileno(), msvcrt.LK_NBLCK, 1)
        else:
            import fcntl
            fcntl.flock(file, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield


def summary(state):
    values = state.values
    return {"status": values.get("status"), "next": list(state.next),
            "total_fields": len(values.get("snapshot", {}).get("fields", [])),
            "results": {key: value["status"] for key, value in values.get("results", {}).items()},
            "metrics": values.get("metrics", {}), "elapsed_seconds": round(time.time()-values.get("started_at", time.time()), 3),
            "has_pending_command": values.get("command") is not None}


def publish(folder, state):
    from .control_registry import registry_report
    command = state.values.get("command")
    # Only a graph interrupt may export a runnable command. Unknown results cannot reissue it.
    if state.next and state.values.get("status") in {"awaiting_edge", "awaiting_save"}:
        atomic_json(folder / "request.json", {"phase": state.values["status"], "command": command, "controlRegistry": registry_report()})
    else:
        atomic_json(folder / "request.json", {"phase": state.values.get("status"), "command": None})
    atomic_json(folder / "summary.json", summary(state))
    return summary(state)


def operate(args):
    from .runtime import local_path
    folder = local_path(args.run_dir, writable=True)
    folder.mkdir(parents=True, exist_ok=True)
    with owner_lock(folder), SqliteSaver.from_conn_string(str(folder / "checkpoints.sqlite")) as saver:
        config = {"configurable": {"thread_id": "module"}, "recursion_limit": 500}
        model_name = getattr(args, "model", None)
        if model_name is None and (folder / "policy.json").exists():
            model_name = load(folder / "policy.json").get("model")
        if model_name is None:
            from .runtime import review_model
            model_name = review_model().model
        graph = build_graph(CodexJsonModel(model_name), saver)
        existing = graph.get_state(config)
        if args.action == "start":
            if existing.values or (folder / "policy.json").exists():
                raise ContractError("run_already_exists_use_resume")
            state = initial_state(load(args.profile), load(args.snapshot), allow_save=args.allow_save)
            atomic_json(folder / "policy.json", {"target": state["snapshot"]["target"],
                "module_id": state["snapshot"]["module_id"], "module_selector": state["snapshot"]["module_selector"],
                "fill": True, "save": args.allow_save, "submit": False, "model": model_name})
            graph.invoke(state, config, durability="sync")
        elif args.action == "save":
            values = existing.values
            if values.get("status") != "verified_draft" or values.get("command") is not None or values.get("reviewed_revision") != values.get("revision"):
                raise ContractError("save_requires_verified_draft")
            fresh = load(args.snapshot)
            validate_snapshot(fresh)
            for key in ("target", "module_id", "module_selector"):
                if fresh[key] != values["current"][key]:
                    raise ContractError("save_scope_changed")
            if fresh["fields"] != values["current"]["fields"]:
                raise ContractError("reviewed_fields_changed")
            authorization = {**values["authorization"], "save": True}
            original = {**values["snapshot"], "capture": fresh.get("capture", {})}
            policy = load(folder / "policy.json")
            atomic_json(folder / "policy.json", {**policy, "save": True})
            graph.update_state(config, {"current": fresh, "snapshot": original, "authorization": authorization, "status": "verified"}, as_node="verify")
            graph.invoke(None, config, durability="sync")
        elif args.action == "resume":
            if existing.next and set(existing.next) <= {"map", "verify"} and existing.values.get("command") is None:
                # Model-only failures are safe to retry; these nodes cannot touch a browser.
                graph.invoke(None, config, durability="sync")
                return publish(folder, graph.get_state(config))
            if not existing.next or existing.values.get("status") not in {"awaiting_edge", "awaiting_save"}:
                raise ContractError("run_not_awaiting_receipt")
            receipt_path = Path(args.receipt).resolve() if args.receipt else folder / "receipt.json"
            # Only the trusted writer journal is accepted, not arbitrary model text.
            receipt = load(receipt_path)
            command = existing.values["command"]
            journal = load(folder / "writer" / (command["command_id"] + ".json"))
            if journal.get("receipt") != receipt:
                raise ContractError("receipt_not_in_writer_journal")
            graph.invoke(Command(resume=receipt), config, durability="sync")
        elif not existing.values:
            raise ContractError("run_not_found")
        return publish(folder, graph.get_state(config))


def main():
    parser = argparse.ArgumentParser(description="JSON-only LangGraph coordinator for a claimed existing Edge tab")
    sub = parser.add_subparsers(dest="action", required=True)
    start = sub.add_parser("start")
    local_config = Path(__file__).resolve().parent.parent / "private" / "local-config.json"
    default_profile = load(local_config).get("profile") if local_config.exists() else None
    start.add_argument("--profile", default=default_profile, required=default_profile is None)
    start.add_argument("--snapshot", required=True)
    start.add_argument("--allow-save", action="store_true")
    start.add_argument("--model")
    resume = sub.add_parser("resume")
    resume.add_argument("--receipt")
    resume.add_argument("--model")
    status = sub.add_parser("status")
    save = sub.add_parser("save", help="Authorize saving an independently verified draft using a fresh unchanged snapshot")
    save.add_argument("--snapshot", required=True)
    for command in (start, resume, status, save):
        command.add_argument("--run-dir", required=True)
    args = parser.parse_args()
    try:
        print(json.dumps(operate(args), ensure_ascii=False, indent=2))
    except (ContractError, OSError, ValueError) as exc:
        print(json.dumps({"error": type(exc).__name__, "reason": str(exc)}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(2)


if __name__ == "__main__":
    main()
