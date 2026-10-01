from __future__ import annotations

import copy
import re
import time
from datetime import date
from pathlib import Path
from urllib.parse import urlsplit


class ContractError(ValueError):
    pass


KINDS = {"text", "checkbox", "radio", "radio_group", "select", "combobox", "file"}
TRANSFORMS = {"identity", "string", "yes_no", "not_yes_no", "present_absent", "not_bool", "join_text", "join_location", "year_month", "date_year", "date_month", "enrolled_no_diploma", "normalize_exam_level", "age_from_birth_date", "none_string_no", "explicit_none_text", "degree_category", "education_category", "last_clause", "address_city", "china_city_path"}
# User policy: supplied personal values use the ordinary value path.
# Keep this compatibility symbol for old imports; it no longer classifies fields.
SENSITIVE = re.compile(r"(?!)")


def file_matches(field, value):
    return (isinstance(value, str) and field.get('value_present') is True
            and field.get('upload_ready') is True
            and field.get('value') == re.split(r'[\\/]', value)[-1])


def field_writable(field):
    """Return whether an observed field has a supported programmatic write path.

    Element UI selects expose a readonly text input by design; their adapter
    writes through the surrounding component.  Other readonly controls remain
    non-writable.
    """
    if not field.get("selector") or field.get("disabled"):
        return False
    readonly = field.get("readonly") or field.get("readOnly")
    return (not readonly
            or (field.get('kind') == 'combobox' and field.get('control_pattern') == 'next_range_date')
            or (field.get("kind") in {"select", "combobox"}
                and field.get("component") in {"element-select", "next-select", "ant-select", "moka-select", "phoenix-select", "atsx-select", "ud-select"}
                and field.get("control_status") == "recognized")
            or (field.get("kind") == "text"
                and field.get("component") in {"element-date", "element-date-now", "ant-date", "ant-picker", "moka-date"}
                and field.get("control_status") == "recognized")
            or (field.get("kind") == "file"
                and field.get("component") == "native-file"
                and field.get("control_status") == "recognized"))


def closed(obj, keys, required=None):
    if not isinstance(obj, dict) or set(obj) - set(keys) or set(required or keys) - set(obj):
        raise ContractError("unexpected_or_missing_fields")


def json_value(profile, pointer):
    if isinstance(pointer, str) and pointer.startswith("record_id:"):
        record_spec, sep, field_path = pointer.partition("/")
        record_id = record_spec.removeprefix("record_id:")
        if not record_id or not sep or not field_path or SENSITIVE.search(field_path):
            raise ContractError("invalid_or_protected_source")
        matches = []
        for section in ("education", "employment", "projects", "research", "awards", "campus", "competitions", "company_answers", "attachments"):
            records = profile.get(section, [])
            if isinstance(records, list):
                matches.extend(record for record in records if isinstance(record, dict) and record.get("record_id") == record_id)
        if len(matches) != 1:
            raise ContractError("source_record_not_unique")
        value = matches[0]
        try:
            for token in field_path.split("/"):
                token = token.replace("~1", "/").replace("~0", "~")
                value = value[int(token)] if isinstance(value, list) and token.isdigit() else value[token]
        except (KeyError, IndexError, TypeError, ValueError):
            raise ContractError("source_not_found") from None
        if value is None or not isinstance(value, (str, int, float, bool, list)):
            raise ContractError("source_is_not_an_answer")
        if isinstance(value, list) and (not value or not all(isinstance(v, str) for v in value)):
            raise ContractError("source_is_not_an_answer")
        return copy.deepcopy(value)
    if not isinstance(pointer, str) or not pointer.startswith("/") or SENSITIVE.search(pointer):
        raise ContractError("invalid_or_protected_source")
    value = profile
    try:
        for token in pointer[1:].split("/"):
            token = token.replace("~1", "/").replace("~0", "~")
            value = value[int(token)] if isinstance(value, list) and token.isdigit() else value[token]
    except (KeyError, IndexError, TypeError, ValueError):
        raise ContractError("source_not_found") from None
    if isinstance(value, dict) and {'province', 'city'} <= set(value) <= {'province', 'city', 'district','full'} and all(isinstance(v, str) and v.strip() for v in value.values()):
        return copy.deepcopy(value)
    if value is None or not isinstance(value, (str, int, float, bool, list)):
        raise ContractError("source_is_not_an_answer")
    if isinstance(value, list) and (not value or not all(isinstance(v, str) for v in value)):
        raise ContractError("source_is_not_an_answer")
    return copy.deepcopy(value)


def transform(value, rule):
    if rule == 'china_city_path':
        city = transform(value, 'address_city')
        province = re.match(r'^([^省]+)省', value.strip())
        if not province and not re.match(r'^(北京市|上海市|天津市|重庆市)', value.strip()):
            raise ContractError('province_not_explicit_in_address')
        return '中国-' + (province.group(1) if province else city) + '-' + city
    if rule == 'address_city':
        if not isinstance(value,str):
            raise ContractError('city_requires_address')
        municipality=re.match(r'^(北京市|上海市|天津市|重庆市)',value.strip())
        if municipality:
            return municipality.group(1).removesuffix('市')
        city=re.match(r'^[^省]+省([^市]+市)',value.strip())
        if city:
            return city.group(1).removesuffix('市')
        raise ContractError('city_not_explicit_in_address')
    if rule == 'education_category':
        categories = {'本科':'本科','硕士研究生':'硕士','硕士':'硕士',
                      '博士研究生':'博士','博士':'博士','博士后':'博士后','大专':'大专','专科':'大专'}
        if not isinstance(value, str) or value.strip() not in categories:
            raise ContractError('education_category_not_explicit')
        return categories[value.strip()]
    if rule not in TRANSFORMS:
        raise ContractError("unsupported_inference")
    if rule == 'explicit_none_text':
        if value != 'explicit_none':
            raise ContractError('explicit_none_required')
        return '无'
    if rule == 'join_location':
        if not isinstance(value, dict) or not {'province', 'city'} <= set(value) <= {'province', 'city', 'district','full'} or not all(isinstance(v, str) and v.strip() for v in value.values()):
            raise ContractError('join_requires_location_object')
        return ' '.join(value[k] for k in ('province', 'city', 'district') if k in value)
    if rule == 'age_from_birth_date':
        if not isinstance(value, str) or not re.fullmatch(r'\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])', value):
            raise ContractError('age_requires_iso_birth_date')
        born = date.fromisoformat(value); today = date.today()
        return str(today.year - born.year - ((today.month, today.day) < (born.month, born.day)))
    if rule == 'none_string_no':
        if not isinstance(value, str) or value.strip() not in {'无', '无业'}:
            raise ContractError('none_string_condition_not_met')
        return '否'
    if rule == 'degree_category':
        if not isinstance(value, str):
            raise ContractError('degree_category_requires_text')
        matches = [suffix for suffix in ('学士', '硕士', '博士') if value.strip().endswith(suffix)]
        if len(matches) != 1:
            raise ContractError('degree_category_not_explicit')
        return matches[0]
    if rule == 'last_clause':
        if not isinstance(value, str):
            raise ContractError('last_clause_requires_text')
        parts = [part.strip() for part in re.split(r'[,，;；]', value) if part.strip()]
        if len(parts) < 2:
            raise ContractError('last_clause_not_explicit')
        return parts[-1]
    if isinstance(value, dict):
        raise ContractError('location_requires_join_transform')
    if rule == 'enrolled_no_diploma':
        if value is not True:
            raise ContractError('inference_condition_not_met')
        return '否'
    if rule == 'not_bool':
        if type(value) is not bool:
            raise ContractError('inference_requires_explicit_boolean')
        return not value
    if rule == 'present_absent':
        if type(value) is not bool:
            raise ContractError('inference_requires_explicit_boolean')
        return '有' if value else '无'
    if rule == 'join_text':
        if not isinstance(value, list) or not value or not all(isinstance(v, str) and v.strip() for v in value):
            raise ContractError('join_requires_nonempty_string_list')
        return '、'.join(value)
    if rule in {'date_year','date_month'}:
        reduced=transform(value,'year_month')
        return reduced[:4] if rule=='date_year' else str(int(reduced[5:7]))
    if rule == 'year_month':
        if not isinstance(value, str) or not re.fullmatch(r'\d{4}-(0[1-9]|1[0-2])(?:-(0[1-9]|[12]\d|3[01]))?', value):
            raise ContractError('year_month_requires_iso_date')
        return value[:7]
    if rule == 'normalize_exam_level':
        if not isinstance(value, str) or not re.fullmatch(r'(?:CET|TEM)-?[468]', value, re.I):
            raise ContractError('exam_level_requires_known_code')
        return value.upper().replace('-', '')
    if rule in {"yes_no", "not_yes_no"}:
        if type(value) is not bool:
            raise ContractError("inference_requires_explicit_boolean")
        return "是" if (value if rule == "yes_no" else not value) else "否"
    if rule == "string":
        if isinstance(value, list):
            raise ContractError("cannot_stringify_list")
        return str(value)
    return value


def validate_target(target):
    closed(target, {"browser", "browser_id", "tab_id", "url"})
    if target["browser"] != "edge" or not all(isinstance(v, str) and v for v in target.values()):
        raise ContractError("edge_existing_tab_required")
    if urlsplit(target["url"]).scheme not in {"http", "https"}:
        raise ContractError("unsupported_page")


def validate_snapshot(snapshot):
    validate_target(snapshot["target"])
    if not snapshot.get("snapshot_id") or not snapshot.get("module_id"):
        raise ContractError("missing_snapshot_identity")
    if not isinstance(snapshot.get("observed_at"), (float, int)):
        raise ContractError("missing_observation_time")
    if snapshot["observed_at"] > time.time() + 30 or time.time() - snapshot["observed_at"] > 300:
        raise ContractError("stale_snapshot")
    fields = snapshot.get("fields")
    if not isinstance(fields, list) or len({f["id"] for f in fields}) != len(fields):
        raise ContractError("duplicate_fields")
    if not fields:
        raise ContractError("no_editable_fields_in_module")


def compile_plan(profile, snapshot, proposal):
    """No model-provided selectors, JS, target, authorization, or literal values."""
    validate_snapshot(snapshot)
    closed(proposal, {"mappings", "deferred"})
    fields = {f["id"]: f for f in snapshot["fields"]}
    seen, operations = set(), []
    for item in proposal["mappings"]:
        closed(item, {"field_id", "source", "transform", "depends_on"})
        fid = item["field_id"]
        if fid in seen or fid not in fields:
            raise ContractError("duplicate_or_unknown_field")
        seen.add(fid)
        f = fields[fid]
        if f["kind"] not in KINDS or not f.get("selector") or (f.get("disabled") and not item["depends_on"]):
            raise ContractError("field_not_writable")
        effective_source = item["source"]
        if f.get('control_pattern') == 'ud_date_pair' and f.get('range_endpoint') in {'start','end'}:
            from .knowledge import resolve_record
            expected=resolve_record(profile,snapshot)+'/'+f['range_endpoint']+'_date'
            if effective_source != expected:
                raise ContractError('date_endpoint_source_mismatch')
        context_fields = snapshot['fields'] + snapshot.get('context_fields', [])
        overseas_context = any('证件' in str(other.get('label', '')) and
            ('出国' in str(other.get('label', '')) or '出境' in str(other.get('label', '')))
            for other in context_fields)
        if (overseas_context and f.get('label', '').strip() in {'证件类型', '证件号', '证件号码'}
                and re.fullmatch(r'/identity/identity_document_(number|type|issuer|valid_from|valid_to)', str(effective_source))):
            raise ContractError('overseas_document_requires_own_source')
        raw_value = json_value(profile, effective_source)
        effective_transform = item["transform"]
        # Numeric facts and flat string lists have one deterministic text
        # representation. Normalize those at the program boundary so a valid
        # source mapping is not lost merely because identity was requested.
        if f["kind"] == "text" and effective_transform in {"identity", "string", "join_text"}:
            if isinstance(raw_value, str):
                effective_transform = "identity"
            elif isinstance(raw_value, (int, float)) and not isinstance(raw_value, bool):
                effective_transform = "string"
            elif isinstance(raw_value, list) and raw_value and all(isinstance(v, str) and v.strip() for v in raw_value):
                effective_transform = "join_text"
        # A boolean cannot be typed into an option control.  Normalize the
        # representation at the program boundary; the executor still requires
        # an exact live option and will defer to enum review when the site uses
        # an alias such as 有/无. Element selects often expose no options until
        # opened, so this must not depend on discovery-time option visibility.
        if (f["kind"] in {"select", "combobox", "radio_group"} and type(raw_value) is bool
                and effective_transform in {"identity", "string"}):
            effective_transform = "yes_no"
        value = transform(raw_value, effective_transform)
        max_length = f.get("max_length")
        if (f["kind"] == "text" and isinstance(value, str)
                and isinstance(max_length, int) and max_length > 0 and len(value) > max_length):
            # Prefer an explicitly curated short variant from the same record;
            # never let the browser silently truncate a longer narrative.
            parts = effective_source.rsplit("/", 1)
            short_source = parts[0] + "/texts/short" if len(parts) == 2 else ""
            try:
                short_value = json_value(profile, short_source)
            except ContractError:
                short_value = None
            if not isinstance(short_value, str) or len(short_value) > max_length:
                raise ContractError("text_exceeds_max_length")
            effective_source, raw_value, effective_transform, value = short_source, short_value, "identity", short_value
        if f["kind"] in {"checkbox", "radio"}:
            if type(value) is not bool or (f["kind"] == "radio" and not value):
                raise ContractError("invalid_checked_value")
        elif f["kind"] == "text" and not isinstance(value, str):
            raise ContractError("text_requires_string")
        elif f["kind"] == "file":
            if not isinstance(value, str) or not value:
                raise ContractError("file_requires_path")
            path = Path(value)
            if not path.is_absolute() or not path.is_file():
                raise ContractError("file_path_missing")
        elif f["kind"] in {"select", "combobox", "radio_group"}:
            if not isinstance(value, (str, list)) or (isinstance(value, list) and not f.get("multiple")):
                raise ContractError("invalid_option_value")
            # The executor defers nonexact labels and reports live candidates.
            # Compilation must not guess aliases or reject the entire module.
        deps = item["depends_on"]
        if not isinstance(deps, list) or len(set(deps)) != len(deps) or fid in deps or any(d not in fields for d in deps):
            raise ContractError("invalid_dependency")
        operations.append({"id": fid, "source": effective_source, "transform": effective_transform,
                           "field": copy.deepcopy(f), "value": value, "depends_on": deps})
    deferred = []
    for item in proposal["deferred"]:
        closed(item, {"field_id", "reason"})
        if item["field_id"] in seen or item["field_id"] not in fields or not item["reason"]:
            raise ContractError("invalid_deferred_field")
        seen.add(item["field_id"])
        deferred.append(copy.deepcopy(item))
    if seen != set(fields):
        raise ContractError("incomplete_coverage")
    # Topological order, including a dependency on a deferred field (blocked below).
    ordered, remaining = [], operations[:]
    available = {d["field_id"] for d in deferred}
    while remaining:
        ready = [op for op in remaining if set(op["depends_on"]) <= available]
        if not ready:
            raise ContractError("dependency_cycle")
        for op in ready:
            ordered.append(op)
            available.add(op["id"])
            remaining.remove(op)
    return ordered, deferred


def validate_receipt(command, receipt):
    closed(receipt, {"command_id", "kind", "target", "settled", "status", "results", "snapshot", "evidence"})
    if receipt["command_id"] != command["command_id"] or receipt["kind"] != command["kind"]:
        raise ContractError("wrong_command_receipt")
    if receipt["target"] != command["target"]:
        raise ContractError("wrong_target_receipt")
    if receipt["status"] not in {"completed", "partial", "unknown", "conflict", "saved", "unconfirmed"}:
        raise ContractError("invalid_receipt_status")
    if type(receipt["settled"]) is not bool or not isinstance(receipt["evidence"], dict):
        raise ContractError("invalid_receipt")
    if 'controlRegistry' in receipt['evidence']:
        from .control_registry import assert_registry
        assert_registry(receipt['evidence']['controlRegistry'])
    if receipt["snapshot"] is not None and receipt["snapshot"]["target"] != command["target"]:
        raise ContractError("wrong_snapshot_target")
    if command['kind'] == 'control_fill' and receipt['status'] == 'completed' and 'controlTarget' in command:
        from .control_contract import validate_control_outcome
        validate_control_outcome(command, receipt['evidence'], receipt['settled'])
    if command["kind"] == "fill":
        expected = [op["id"] for op in command["operations"]]
        actual = [r["id"] for r in receipt["results"]]
        if actual != expected:
            raise ContractError("receipt_coverage_or_order")
        for result in receipt["results"]:
            closed(result, {"id", "status", "reason"})
            if result["status"] not in {"written", "already_matched", "verification_skipped", "deferred", "conflict", "unknown", "unattempted"}:
                raise ContractError("invalid_field_status")
            if result['status'] == 'verification_skipped' and (not receipt['settled'] or not result['reason']):
                raise ContractError('skipped_verification_requires_finished_call')
    from .enum_repair import candidates_for
    # Enum candidates are actionable only after the fill is fully settled and
    # contains no unknown/conflict result. An unsettled browser receipt must be
    # accepted as recovery evidence first; trying to validate its optional enum
    # payload here used to crash the graph before it could enter reconciliation.
    if (receipt['settled'] is True and receipt['status'] in {'completed', 'partial'}
            and not any(result.get('status') in {'unknown', 'conflict'} for result in receipt['results'])):
        candidates_for(command, receipt)
    return copy.deepcopy(receipt)


def page_matches(operations, snapshot):
    fields = {f["id"]: f for f in snapshot["fields"]}
    def equal(left, right):
        return sorted(left) == sorted(right) if isinstance(left, list) and isinstance(right, list) else left == right
    from .identity import SECRET_REF, SOURCE
    def matches(op):
        field = fields.get(op['id'])
        if field is None:
            # A recovery can keep the semantic operation id while replacing a
            # mutable DOM selector with a freshly observed structural selector.
            # Accept only one selector + signature + control-shape match.
            expected = op.get('field') or {}
            candidates = [f for f in snapshot['fields']
                          if expected.get('selector') and f.get('selector') == expected.get('selector')
                          and f.get('signature') == expected.get('signature')
                          and f.get('kind') == expected.get('kind')
                          and f.get('protected') == expected.get('protected')]
            field = candidates[0] if len(candidates) == 1 else None
        if field is None:
            return False
        if op.get('verification') == 'skipped':
            from .verification import incomplete
            if incomplete(field) and field.get('signature') == op['field'].get('signature'):
                return True  # Accepted omission, not a successful value comparison.
        if field.get('value_readable') is False or field.get('expanded') in (True, 'true'):
            return False
        if 'secret_ref' in op:
            return (op['secret_ref'] == SECRET_REF and op.get('source') == SOURCE
                    and op.get('value') is None and field.get('protected') is True
                    and field.get('secret_match') is True)
        if op.get('field', {}).get('kind') == 'file':
            return file_matches(field, op['value'])
        return equal(field['value'], op['value'])
    return all(matches(op) for op in operations)


def redacted_profile(profile):
    if isinstance(profile, dict):
        return {k: redacted_profile(v) for k, v in profile.items() if not SENSITIVE.search(k)}
    if isinstance(profile, list):
        return [redacted_profile(v) for v in profile]
    return profile
