"""Audit a finished, read-only observation whose local result was never published."""
import copy
import json
import time

from .contracts import ContractError


def _finished_reads(journal):
    events = journal.get('instrumentation') or []
    return (journal.get('kind') == 'observe' and journal.get('status') == 'pending'
            and not journal.get('receipt') and bool(events)
            and all(e.get('method') == 'evaluate' and e.get('stage') == 'module_readback'
                    and e.get('status') == 'returned' and type(e.get('duration_ms')) in (int, float)
                    and e['duration_ms'] >= 0 for e in events))


def recover_observation(values, next_nodes, folder, basis, *, writer_lock_held=False):
    command = values.get('command') or {}
    if (not writer_lock_held or tuple(next_nodes) != ('observe',)
            or values.get('status') != 'awaiting_observation' or command.get('kind') != 'observe'
            or not isinstance(basis, str) or not basis.strip()):
        raise ContractError('observation_audit_requires_current_read_only_interrupt')
    path = folder / 'writer' / (command['command_id'] + '.json')
    journal = json.loads(path.read_text(encoding='utf-8'))
    if journal.get('command_id') != command['command_id'] or not _finished_reads(journal):
        raise ContractError('observation_audit_requires_all_known_reads_returned')
    proof = {'version': 1, 'kind': 'finished_observation_without_result',
             'original_command': copy.deepcopy(command), 'at': time.time(), 'basis': basis,
             'instrumentation': copy.deepcopy(journal['instrumentation']),
             'value_verified': False, 'write_verified': False}
    receipt = {'command_id': command['command_id'], 'kind': 'observe', 'target': command['target'],
               'settled': True, 'status': 'unconfirmed', 'results': [], 'snapshot': None,
               'evidence': {'reason': 'observation_result_missing_after_finished_dom_read',
                            'local_read_completion_audit': True}}
    return proof, receipt


def audited_observation(folder, journal):
    path = folder / 'observation-audits' / (str(journal.get('command_id')) + '.json')
    if not path.is_file() or not _finished_reads(journal):
        return False
    proof = json.loads(path.read_text(encoding='utf-8'))
    return (proof.get('version') == 1 and proof.get('kind') == 'finished_observation_without_result'
            and proof.get('original_command', {}).get('kind') == 'observe'
            and proof['original_command'].get('command_id') == journal['command_id']
            and proof.get('instrumentation') == journal['instrumentation']
            and proof.get('value_verified') is False and proof.get('write_verified') is False)


def persist_observation_audit(folder, proof):
    """Create once; a retry may reuse only the exact audited command and calls."""
    command = proof['original_command']
    path = folder / 'observation-audits' / (command['command_id'] + '.json')
    path.parent.mkdir(exist_ok=True)
    try:
        with path.open('x', encoding='utf-8') as stream:
            json.dump(proof, stream, ensure_ascii=False, indent=2)
    except FileExistsError:
        existing = json.loads(path.read_text(encoding='utf-8'))
        journal = json.loads((folder / 'writer' / (command['command_id'] + '.json')).read_text(encoding='utf-8'))
        if (not audited_observation(folder, journal)
                or existing.get('original_command') != command
                or existing.get('instrumentation') != proof.get('instrumentation')):
            raise ContractError('observation_audit_exists_but_differs')
        return existing
    return proof
