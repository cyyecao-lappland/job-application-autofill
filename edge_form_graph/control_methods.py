"""Durable method recipes, never personal answers or transient DOM selectors."""
import copy
from pathlib import Path
from .cli import atomic_json, load, owner_lock
from .contracts import ContractError
from .control_registry import adapter_names

DEFAULT_PATH=Path(__file__).resolve().parent.parent/'private'/'control-methods.json'
KEYS={'target_url','module_id','signature','adapter','source','verified_command_id','original_command_id'}

def sync_methods(methods, path=DEFAULT_PATH):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    with owner_lock(path.parent):
        entries=load(path).get('methods',[]) if path.exists() else []
        for method in methods:
            if set(method)-KEYS or not method.get('verified_command_id') or method.get('adapter') not in adapter_names():
                raise ContractError('invalid_control_method')
            key=lambda m:(m['target_url'],m['module_id'],m['signature'])
            entries=[m for m in entries if key(m)!=key(method)]+[copy.deepcopy(method)]
        if methods:atomic_json(path,{'version':1,'methods':entries})
    return entries
