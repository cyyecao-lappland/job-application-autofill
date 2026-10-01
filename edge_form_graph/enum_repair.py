"""Bounded, command-scoped aliases; a model can only choose an observed label."""
import copy
import math
import re

from .contracts import ContractError, closed, json_value, transform, validate_snapshot

MAX_ROUNDS = 2
ENUM_CONTEXT_KEYS = ('degree_type', 'discipline_category', 'education_level', 'major',
                     'rank_position', 'rank_total', 'rank_band',
                     'study_mode', 'training_mode', 'education_type', 'country_region')


def source_context(profile, operation):
    """Return a small, explicit qualifier set from the source record.

    A leaf such as ``degree=硕士`` can be ambiguous while its canonical sibling
    ``degree_type=工学`` makes one observed option exact.  Keep this bounded and
    deterministic; do not send the full record or unrelated applicant data.
    """
    pointer = operation.get('source')
    if not isinstance(pointer, str) or '/' not in pointer[1:]:
        return {}
    parent_pointer = pointer.rsplit('/', 1)[0]
    try:
        parent = json_value(profile, parent_pointer)
    except ContractError:
        # json_value intentionally rejects general objects as field answers.
        current = profile
        try:
            for token in parent_pointer.split('/')[1:]:
                token = token.replace('~1', '/').replace('~0', '~')
                current = current[int(token)] if isinstance(current, list) else current[token]
            parent = current
        except (KeyError, IndexError, TypeError, ValueError):
            return {}
    if not isinstance(parent, dict):
        return {}
    return {key: copy.deepcopy(parent[key]) for key in ENUM_CONTEXT_KEYS
            if isinstance(parent.get(key), (str, int, float, bool)) and parent.get(key) not in ('', None)}


def rank_option(request):
    """Choose an observed bucket only when explicit rank facts fit its bounds."""
    context=request.get('source_context',{})
    position,total=context.get('rank_position'),context.get('rank_total')
    numeric=(type(position) is int and type(total) is int and 1<=position<=total)
    percent=100*position/total if numeric else None
    band=context.get('rank_band')
    def interval(label):
        match=re.fullmatch(r'\s*(\d+(?:\.\d+)?)%\s*[-~～至]\s*(\d+(?:\.\d+)?)%\s*',label)
        if not match:return None
        low,high=map(float,match.groups())
        return (low,high) if 0<=low<=high<=100 else None
    known=interval(band) if isinstance(band,str) else None
    top=re.fullmatch(r'前\s*(\d+(?:\.\d+)?)%',band.strip()) if isinstance(band,str) else None
    if top:
        high=float(top.group(1));known=(0,high) if 0<high<=100 else None
    if numeric and known and not known[0]<=percent<=known[1]:return None
    if not numeric and (position is not None or total is not None):return None
    options=request.get('options',[])
    if any(re.fullmatch(r'\s*\d+(?:\.\d+)?%\s*[-~～至]\s*\d+(?:\.\d+)?%\s*',option)
           and interval(option) is None for option in options):return None
    thresholds=[]
    for option in options:
        match=re.fullmatch(r'前\s*(\d+(?:\.\d+)?)%',option.strip())
        if match:
            limit=float(match.group(1))
            if 0<limit<=100 and (numeric and percent<=limit or not numeric and known and known[1]==limit):
                thresholds.append((limit,option))
    if thresholds:
        best=min(limit for limit,_ in thresholds)
        matches=[label for limit,label in thresholds if limit==best]
        return matches[0] if len(matches)==1 else None
    intervals=[(interval(option),option) for option in options if interval(option)]
    if numeric:
        # Integer percentile buckets such as 1-5, 6-10, ..., 71-100 use
        # one-based percentile positions. Require the full unambiguous partition.
        ordered=sorted(bounds for bounds,_ in intervals)
        if (not ordered or ordered[0][0]!=1 or ordered[-1][1]!=100
                or any(not low.is_integer() or not high.is_integer() for low,high in ordered)
                or any(right[0]!=left[1]+1 for left,right in zip(ordered,ordered[1:]))):return None
        percentile=math.ceil(percent)
        matches=[label for (low,high),label in intervals if low<=percentile<=high]
    elif known:
        matches=[label for (low,high),label in intervals if low<=known[0] and known[1]<=high]
    else:return None
    return matches[0] if len(matches)==1 else None


def context_decisions(requests):
    """Resolve enum labels only when explicit same-record qualifiers compose one exact option."""
    decisions = []
    for request in requests:
        context = request.get('source_context', {})
        if (request.get('field_label','').strip(' *:：') in {'学历','学历层次','最高学历'}
                and isinstance(context,dict) and context.get('education_level')==request.get('source_value')):
            levels=({'本科','大学本科'}, {'硕士','硕士研究生'}, {'博士','博士研究生'})
            labels=next((group for group in levels if request['source_value'] in group),set())
            matches=[option for option in request.get('options',[]) if option in labels]
            if len(matches)==1:
                decisions.append({'field_id':request['field_id'],'source_value':request['source_value'],
                                  'option':matches[0],'reason':'explicit_same_record_education_level'})
                continue
        if '受教育类型' in request.get('field_label', '') and isinstance(context, dict):
            mode = context.get('study_mode')
            if (mode == '全日制' and request.get('source_value') == '全日制统分统招'
                    and request.get('options', []).count('全日制统招') == 1):
                decisions.append({'field_id': request['field_id'],
                                  'source_value': request['source_value'],
                                  'option': '全日制统招',
                                  'reason': 'explicit_full_time_unified_enrollment'})
                continue
            if isinstance(mode, str) and mode.strip() in {'全日制', '非全日制', '在职'}:
                wanted = mode.strip() + '教育'
                matches = [option for option in request.get('options', []) if option.strip() == wanted]
                if len(matches) == 1:
                    decisions.append({'field_id': request['field_id'],
                                      'source_value': request['source_value'],
                                      'option': matches[0],
                                      'reason': 'explicit_same_record_study_mode'})
                    continue
        if '排名' in request.get('field_label', '') and isinstance(context, dict):
            option=rank_option(request)
            if option is not None:
                reason=('explicit_rank_position_over_total' if context.get('rank_position') is not None
                        else 'explicit_rank_band_upper_bound') if option.startswith('前') else 'explicit_rank_facts_fit_observed_bucket'
                decisions.append({'field_id':request['field_id'],'source_value':request['source_value'],
                                  'option':option,'reason':reason})
            continue
        if '学位' not in request.get('field_label', '') or not isinstance(context, dict):
            continue
        degree_type, degree = context.get('degree_type'), request.get('source_value')
        if not isinstance(degree_type, str) or not degree_type.strip() or not isinstance(degree, str):
            continue
        wanted = {degree_type.strip()+degree.strip(), degree_type.strip()+degree.strip()+'学位'}
        matches = [option for option in request.get('options', []) if option.strip() in wanted]
        if len(matches) == 1:
            decisions.append({'field_id': request['field_id'], 'source_value': degree,
                              'option': matches[0], 'reason': 'explicit_same_record_degree_type'})
    return decisions


def candidates_for(command, receipt):
    candidates = receipt['evidence'].get('enum_candidates', [])
    if not isinstance(candidates, list) or len(candidates) > 64:
        raise ContractError('invalid_enum_candidates')
    if not candidates:
        return []
    if (command['kind'] != 'fill' or receipt['settled'] is not True
            or receipt['status'] not in {'completed', 'partial'}
            or any(r['status'] in {'unknown', 'conflict'} for r in receipt['results'])):
        raise ContractError('enum_requires_settled_fill')
    operations = {o['id']: o for o in command['operations']}
    results = {r['id']: r for r in receipt['results']}
    snapshot = receipt.get('snapshot')
    if not snapshot:
        raise ContractError('enum_requires_current_snapshot')
    validate_snapshot(snapshot)
    if (snapshot['module_id'] != command['module_id']
            or snapshot.get('module_selector') != command['module_selector']):
        raise ContractError('enum_wrong_module')
    fields = {f['id']: f for f in snapshot['fields']}
    seen = set()
    for candidate in candidates:
        closed(candidate, {'field_id', 'source_value', 'options'})
        fid, source, options = candidate['field_id'], candidate['source_value'], candidate['options']
        if not isinstance(fid, str) or fid in seen or fid not in operations:
            raise ContractError('enum_wrong_operation')
        seen.add(fid)
        op = operations[fid]
        if fid not in fields or any(fields[fid].get(k) != op['field'].get(k)
                                   for k in ('selector', 'signature', 'kind', 'protected')):
            raise ContractError('enum_field_identity_changed')
        if (op['field']['kind'] not in {'select', 'combobox'} or op['field'].get('protected')
                or results[fid]['status'] != 'deferred' or not isinstance(source, str)
                or source != op['value']):
            raise ContractError('enum_wrong_source_or_status')
        if (not isinstance(options, list) or not 1 <= len(options) <= 256
                or any(not isinstance(x, str) or not x.strip() or len(x) > 1000 for x in options)
                or len(set(options)) != len(options)):
            raise ContractError('enum_invalid_observed_labels')
    return copy.deepcopy(candidates)


def review_requests(profile, operations, candidates):
    by_id = {op['id']: op for op in operations}
    requests = []
    for candidate in candidates:
        op = by_id[candidate['field_id']]
        if transform(json_value(profile, op['source']), op['transform']) != candidate['source_value']:
            raise ContractError('enum_source_changed')
        requests.append({**copy.deepcopy(candidate), 'source': op['source'],
                         'transform': op['transform'], 'field_label': op['field'].get('label', ''),
                         'source_context': source_context(profile, op)})
    return requests


def validate_review(review, requests):
    closed(review, {'decisions'})
    decisions = review['decisions']
    if not isinstance(decisions, list) or len(decisions) != len(requests):
        raise ContractError('enum_review_coverage')
    expected = {r['field_id']: r for r in requests}
    seen, accepted = set(), {}
    for decision in decisions:
        closed(decision, {'field_id', 'source_value', 'option', 'reason'})
        fid = decision['field_id']
        if not isinstance(fid, str) or fid not in expected or fid in seen:
            raise ContractError('enum_review_field')
        seen.add(fid)
        request = expected[fid]
        if (decision['source_value'] != request['source_value']
                or not isinstance(decision['reason'], str) or not decision['reason'].strip()):
            raise ContractError('enum_review_source_or_reason')
        option = decision['option']
        if option is not None:
            if not isinstance(option, str) or option not in request['options']:
                raise ContractError('enum_review_unobserved_option')
            if ('排名' in request.get('field_label','')
                    and any(key in request.get('source_context',{}) for key in ('rank_position','rank_total','rank_band'))
                    and option!=rank_option(request)):
                continue
            accepted[fid] = copy.deepcopy(decision)
    return accepted


def review_plan(proposal, operations):
    plan = copy.deepcopy(proposal)
    aliases = [copy.deepcopy(op['enum_provenance']) for op in operations if op.get('enum_provenance')]
    if aliases:
        plan['enum_aliases'] = aliases
    return plan


def invalidate_unsupported_rank_aliases(profile, modules, results):
    """Retain historical writes, but hold unsupported old rank choices before save."""
    updated=copy.deepcopy(results)
    for module in modules:
        result=updated.get(module['id'],{})
        for operation in result.get('operations',[]):
            provenance=operation.get('enum_provenance')
            fid=operation['id']
            if (not provenance or '排名' not in operation['field'].get('label','')
                    or result.get('results',{}).get(fid,{}).get('status') not in {'written','already_matched'}):continue
            request={**copy.deepcopy(provenance),'field_label':operation['field']['label'],
                     'source_context':source_context(profile,operation)}
            if provenance['option']==rank_option(request):continue
            result.setdefault('invalid_enum_history',[]).append({'operation':copy.deepcopy(operation),
                'previous_result':copy.deepcopy(result['results'][fid]),'reason':'rank_enum_not_supported_by_confirmed_facts'})
            result['results'][fid]={'status':'deferred','reason':'rank_enum_not_supported_by_confirmed_facts'}
            result.update(coverage_hold='unsupported_rank_enum',review={},reviewed_revision=None,status='review_rejected')
    return updated


def carry_reviewed_enums(profile, operations, prior_operations):
    """Keep reviewed labels across recompilation only for unchanged sources."""
    prior_by_id = {op['id']: op for op in prior_operations}
    for operation in operations:
        prior = prior_by_id.get(operation['id'])
        provenance = (prior or {}).get('enum_provenance')
        if not provenance:
            continue
        if any(prior.get(key) != operation.get(key) for key in ('source', 'transform')):
            continue
        if any(prior['field'].get(key) != operation['field'].get(key) for key in ('label', 'kind')):
            continue
        if any(provenance.get(key, operation.get(key)) != operation.get(key)
               for key in ('source', 'transform')):
            continue
        if (not isinstance(provenance.get('source_context'), dict)
                or provenance['source_context'] != source_context(profile, operation)):
            continue
        try:
            requests = review_requests(profile, [operation], [
                {key: provenance[key] for key in ('field_id', 'source_value', 'options')}])
            accepted = validate_review({'decisions': [
                {key: provenance[key] for key in ('field_id', 'source_value', 'option', 'reason')}]}, requests)
        except (ContractError, KeyError):
            continue
        if (operation['id'] in accepted and provenance.get('field_id') == operation['id']
                and prior.get('value') == provenance['option']):
            operation.update(value=provenance['option'], enum_provenance=copy.deepcopy(provenance))
    return operations
