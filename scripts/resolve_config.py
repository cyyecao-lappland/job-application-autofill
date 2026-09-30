"""Resolve installation paths without reading personal answers or starting a browser."""
import argparse
import json
import os
from pathlib import Path
from urllib.parse import urlparse


def read_object(path):
    value = json.loads(path.read_text(encoding="utf-8-sig"))
    if not isinstance(value, dict):
        raise ValueError(f"config_must_be_object:{path}")
    return value


def resolve_path(base, value, key):
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"config_path_required:{key}")
    path = Path(os.path.expandvars(value)).expanduser()
    return (path if path.is_absolute() else base / path).resolve()


def resolve_config(config_path):
    config_path = Path(config_path).resolve()
    config = read_object(config_path)
    root = resolve_path(config_path.parent, config.get("workspace_root"), "workspace_root")
    project = resolve_path(root, config.get("project_directory"), "project_directory")
    profile = resolve_path(root, config.get("profile_file"), "profile_file")
    python = resolve_path(project, config.get("python_executable"), "python_executable")
    endpoint = config.get("cdp_endpoint")
    if not isinstance(endpoint, str):
        raise ValueError("config_endpoint_required")
    url = urlparse(endpoint)
    if (url.scheme != "http" or url.hostname not in {"127.0.0.1", "localhost", "::1"}
            or not url.port or url.username or url.password or url.query or url.fragment
            or url.path not in {"", "/"}):
        raise ValueError("cdp_endpoint_must_be_local_http_with_port")
    runtime_config_path = project / "private" / "local-config.json"
    runtime_config = read_object(runtime_config_path) if runtime_config_path.is_file() else {}
    knowledge = resolve_path(project, runtime_config.get("knowledge_file", "private/form-knowledge.json"), "knowledge_file")
    prior_root = project / "private"
    native_entry = project / "scripts" / "run_edge_cdp_application.mjs"
    missing = [str(path) for path in (profile, python, knowledge, native_entry) if not path.is_file()]
    missing += [str(path) for path in (root, project, prior_root) if not path.is_dir()]
    # Match the existing executor's writable-directory restriction.
    invalid_storage = any(part.casefold() == "onedrive" or part.casefold().startswith("onedrive - ")
                          for part in project.parts)
    errors = (["runtime_directory_must_be_outside_onedrive"] if invalid_storage else [])
    return {
        "status": "ready" if not missing and not errors else "blocked",
        "config_file": str(config_path),
        "workspace_root": str(root),
        "project_directory": str(project),
        "profile_file": str(profile),
        "knowledge_file": str(knowledge),
        "control_methods_file": str(project / "private" / "control-methods.json"),
        "runtime_config_file": str(runtime_config_path),
        "python_executable": str(python),
        "cdp_endpoint": endpoint.rstrip("/"),
        "native_entry": str(native_entry),
        "prior_root": str(prior_root),
        "missing_paths": missing,
        "errors": errors,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", type=Path, default=Path(__file__).resolve().parent.parent / "config.local.json")
    args = parser.parse_args()
    try:
        result = resolve_config(args.config)
    except (OSError, ValueError) as error:
        print(json.dumps({"status": "blocked", "error": str(error)}, ensure_ascii=False, indent=2))
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result["status"] == "ready" else 1


if __name__ == "__main__":
    raise SystemExit(main())
