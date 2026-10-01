"""Preserve fields the user explicitly assigned to themselves or has no files for."""
import copy

from .field_mapping import normalize


def preserved_field_reason(profile, snapshot, field):
    label = normalize(field.get('label', ''))
    context=snapshot.get('mapping_context') or {}
    if context.get('record_collection')=='/projects':
        matches=[r for r in profile.get('projects',[]) if r.get('record_id')==context.get('record_id')]
        if len(matches)==1 and label=='项目链接' and matches[0].get('url') in (None,''):
            return 'missing: canonical_project_url_unavailable'
    if field.get('control_pattern')=='ud_date_pair' and field.get('range_endpoint') in {'start','end'}:
        collection=context.get('record_collection','').lstrip('/')
        matches=[r for r in profile.get(collection,[]) if isinstance(r,dict) and r.get('record_id')==context.get('record_id')]
        if len(matches)==1 and matches[0].get(field['range_endpoint']+'_date') in (None,''):
            return 'missing: canonical_date_endpoint_unavailable'
    if (field.get('kind') == 'file' and label in {'上传附件', '其他附件', '附加附件'}
            and field.get('required') is False and not field.get('value_present')
            and not any(isinstance(a, dict) and a.get('kind') != 'resume'
                        and a.get('value_status') == 'source_backed' and a.get('path')
                        for a in profile.get('attachments', []))):
        return 'attachment_purpose_mismatch: resume_does_not_answer_optional_misc_attachment'
    if (field.get('kind')=='file' and label in {'上传附件','其他附件','附加附件'}
            and field.get('required') is False and not field.get('value_present')):
        return 'unsupported: unspecified_optional_misc_attachment_purpose'
    if (label in {'推荐码', '内推码', 'referral code'}
            and profile.get('company_answer_defaults', {}).get('referral_code_entry_policy') == 'user_managed'):
        return 'unsupported: user_managed_referral_code'
    portfolio_field = (field.get('kind') == 'file' and (
        label == '作品附件' or (label in {'file', ''} and normalize(snapshot.get('module_label', '')) == '作品上传')
        or label == normalize('相关项目或作品链接（如有则填写 GitHub/Demo/ 作品集等地址，可文档中附相关链接）')))
    if portfolio_field and profile.get('collection_status', {}).get('portfolio_attachments') == 'explicit_none':
        return 'missing: explicit_none_portfolio_attachment'
    return None


def apply_field_policies(profile, snapshot, proposal):
    reasons = {f['id']: reason for f in snapshot['fields']
               if (reason := preserved_field_reason(profile, snapshot, f))}
    if not reasons:
        return proposal
    result = copy.deepcopy(proposal)
    result['mappings'] = [m for m in result['mappings'] if m['field_id'] not in reasons]
    result['deferred'] = [d for d in result['deferred'] if d['field_id'] not in reasons]
    result['deferred'].extend({'field_id': fid, 'reason': reason} for fid, reason in reasons.items())
    return result
