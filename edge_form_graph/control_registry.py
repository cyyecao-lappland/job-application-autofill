"""One declaration source; readable, order-independent comparison without digests."""
import copy
import json
from functools import lru_cache
from pathlib import Path

from .contracts import ContractError

CATALOG = Path(__file__).with_name('control_catalog.json')
DECLARATION_KEYS = {'name', 'adapterVersion', 'targetTypes', 'protocolVersion'}


def canonical_report(report):
    if not isinstance(report, dict) or set(report) != {'protocolVersion', 'registryVersion', 'controls'}:
        raise ContractError('CONTROL_REGISTRY_MISMATCH: invalid report')
    if any(type(report[k]) is not int or report[k] < 1 for k in ('protocolVersion', 'registryVersion')):
        raise ContractError('CONTROL_REGISTRY_MISMATCH: invalid version')
    if not isinstance(report['controls'], list):
        raise ContractError('CONTROL_REGISTRY_MISMATCH: invalid controls')
    controls, names = [], set()
    for item in report['controls']:
        if (not isinstance(item, dict) or set(item) != DECLARATION_KEYS
                or not isinstance(item['name'], str) or not item['name'] or item['name'] in names
                or any(type(item[k]) is not int or item[k] < 1 for k in ('adapterVersion', 'protocolVersion'))
                or not isinstance(item['targetTypes'], list) or not item['targetTypes']
                or any(not isinstance(t, str) or not t for t in item['targetTypes'])
                or len(set(item['targetTypes'])) != len(item['targetTypes'])):
            raise ContractError('CONTROL_REGISTRY_MISMATCH: invalid or duplicate declaration')
        names.add(item['name'])
        controls.append({**item, 'targetTypes': sorted(item['targetTypes'])})
    return {**report, 'controls': sorted(controls, key=lambda item: item['name'])}


@lru_cache(maxsize=1)
def catalog():
    data = json.loads(CATALOG.read_text(encoding='utf-8'))
    canonical_report({**data, 'controls': [{k: c[k] for k in DECLARATION_KEYS} for c in data['controls']]})
    return data


def registry_report():
    data = catalog()
    return canonical_report({**data, 'controls': [{k: c[k] for k in DECLARATION_KEYS} for c in data['controls']]})


def adapter_names():
    return frozenset(item['name'] for item in catalog()['controls'] if item.get('execution') != 'field')


def declaration(name):
    found = next((c for c in catalog()['controls'] if c['name'] == name), None)
    if found is None:
        raise ContractError('unsupported_control_adapter:' + str(name))
    return copy.deepcopy(found)


def assert_registry(executor, expected=None):
    expected = canonical_report(expected or registry_report())
    actual = canonical_report(executor)
    differences = []
    for key in ('protocolVersion', 'registryVersion'):
        if expected[key] != actual[key]:
            differences.append(f'{key}: Python expects {expected[key]}, executor provides {actual[key]}')
    left = {c['name']: c for c in expected['controls']}
    right = {c['name']: c for c in actual['controls']}
    for name in sorted(left.keys() | right.keys()):
        if name not in left or name not in right:
            differences.append(f'{name}: ' + ('unexpected' if name not in left else 'missing'))
        else:
            for key in sorted(DECLARATION_KEYS - {'name'}):
                if left[name][key] != right[name][key]:
                    differences.append(f'{name}.{key}: Python expects {left[name][key]}, executor provides {right[name][key]}')
    if differences:
        raise ContractError('CONTROL_REGISTRY_MISMATCH: ' + '; '.join(differences))
    return actual
