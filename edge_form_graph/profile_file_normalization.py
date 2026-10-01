"""Resolve already-provided education transcript paths in a run checkpoint."""
from __future__ import annotations

import copy
import time
from pathlib import Path

from .contracts import ContractError
from .scope_isolation import assert_settled_history


STOPPED_STATUSES = {'incomplete_coverage', 'budget_exhausted', 'module_blocked', 'scope_review_blocked'}


def _inside(path: Path, base: Path) -> bool:
    try:
        path.relative_to(base)
        return True
    except ValueError:
        return False


def normalize_profile_files(values, next_nodes, folder, base_dir, basis, *, writer_lock_held=False):
    """Normalize supplied transcript file paths without editing source files or receipts."""
    command = values.get('command') or {}
    pending_observe = (tuple(next_nodes) == ('observe',)
        and values.get('status') == 'awaiting_observation'
        and command.get('kind') == 'observe' and command.get('command_id')
        and not (folder / 'writer' / (command['command_id'] + '.json')).exists())
    stopped = (not next_nodes and not command and values.get('status') in STOPPED_STATUSES)
    if not writer_lock_held or not isinstance(basis, str) or not basis.strip() or not (stopped or pending_observe):
        raise ContractError('profile_file_normalization_requires_writer_lock_and_quiescent_state')

    assert_settled_history(values, folder)
    try:
        base = Path(base_dir).resolve(strict=True)
    except (OSError, TypeError, ValueError) as exc:
        raise ContractError('profile_file_base_directory_missing') from exc
    if not base.is_dir():
        raise ContractError('profile_file_base_directory_missing')

    profile = copy.deepcopy(values['profile'])
    if not isinstance(profile.get('education'), list):
        raise ContractError('profile_education_records_missing')
    changes = []
    for index, record in enumerate(profile['education']):
        if not isinstance(record, dict):
            raise ContractError('profile_education_record_invalid')
        record_id = record.get('record_id') or str(index)
        entries = []
        for container_key, path_key in (('transcript', 'transcript.path'),
                                        ('transcript_usage_policy', 'transcript_usage_policy.path')):
            container = record.get(container_key)
            if not isinstance(container, dict) or 'path' not in container or container['path'] in (None, ''):
                continue
            raw = container['path']
            if not isinstance(raw, str):
                raise ContractError('profile_transcript_path_invalid')
            supplied = Path(raw)
            try:
                resolved = supplied.resolve(strict=True) if supplied.is_absolute() else (base / supplied).resolve(strict=True)
            except (OSError, RuntimeError, ValueError) as exc:
                raise ContractError('profile_transcript_file_missing') from exc
            if not _inside(resolved, base):
                raise ContractError('profile_transcript_path_outside_base')
            if not resolved.is_file():
                raise ContractError('profile_transcript_file_missing')
            entries.append((container, path_key, raw, resolved, supplied.is_absolute()))

        if len(entries) > 1 and any(item[3] != entries[0][3] for item in entries[1:]):
            raise ContractError('profile_transcript_paths_conflict')
        for container, path_key, raw, resolved, was_absolute in entries:
            normalized = raw if was_absolute else str(resolved)
            if normalized != raw:
                container['path'] = normalized
            changes.append({'record_id': record_id, 'field': path_key, 'before': raw, 'after': normalized})

    now = time.time()
    history = copy.deepcopy(values.get('inventory_history', []))
    history.append({'kind': 'profile_file_normalization', 'at': now, 'basis': basis,
        'base_dir': str(base), 'paths': changes,
        'superseded_undispatched_observe': copy.deepcopy(command) if pending_observe else None})
    return {'profile': profile, 'mapping_cache': {}, 'scope_reviews': {}, 'inventory_history': history}
