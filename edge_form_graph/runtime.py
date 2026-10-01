"""Invocation-local model routing and shared reusable knowledge, not user-global settings."""
import json
from pathlib import Path

from .model import CodexJsonModel
from .knowledge import KnowledgeStore

PROJECT = Path(__file__).resolve().parent.parent


def local_config():
    path = PROJECT/'private'/'local-config.json'
    return json.loads(path.read_text(encoding='utf-8-sig')) if path.exists() else {}


def local_path(value, *, writable=False):
    """Translate migrated run paths; prevent accidental OneDrive runtime creation."""
    path = Path(value).resolve()
    for entry in local_config().get('migration_paths', []):
        source = Path(entry['Source']).resolve()
        if path.is_relative_to(source):
            path = Path(entry['Target']).resolve()/path.relative_to(source)
            break
    if writable and any(part.casefold() == 'onedrive' or part.casefold().startswith('onedrive - ')
                        for part in path.parts):
        raise ValueError('runtime_directory_must_be_outside_onedrive')
    return path


def review_model():
    config=local_config()
    model = config.get('review_model', 'gpt-6-luna')
    if model != 'gpt-6-luna':
        raise ValueError('review_model_must_be_user_selected_gpt6_luna')
    timeout=config.get('review_timeout_seconds',600)
    if type(timeout) is not int or not 60<=timeout<=600:
        raise ValueError('review_timeout_seconds_out_of_range')
    return CodexJsonModel(model=model,timeout=timeout)


def components():
    config = local_config()
    model = config.get('semantic_model', 'gpt-6-luna')
    if model != 'gpt-6-luna':
        raise ValueError('semantic_model_must_be_user_selected_gpt6_luna')
    timeout=config.get('semantic_timeout_seconds',600)
    if type(timeout) is not int or not 60<=timeout<=600:
        raise ValueError('semantic_timeout_seconds_out_of_range')
    location = Path(config.get('knowledge_file', 'private/form-knowledge.json'))
    if not location.is_absolute():
        location = PROJECT/location
    return KnowledgeStore(location), CodexJsonModel(model=model,timeout=timeout)
