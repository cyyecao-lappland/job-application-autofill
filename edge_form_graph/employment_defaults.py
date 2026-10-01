"""Source-backed defaults for explicitly named prior-employer questions."""
import re


def prior_employment_default_source(profile, label):
    match=re.fullmatch(r'是否曾在([\u4e00-\u9fffA-Za-z0-9]{2,20})任职',label)
    if not match:return None
    employer=match.group(1)
    if employer in {'本公司','本集团','公司','集团','贵司','贵公司','贵集团'}:return None
    default=profile.get('company_answer_defaults',{}).get('prior_group_employment',{})
    exceptions=default.get('exceptions')
    if default.get('default') is not False or not isinstance(exceptions,list):return None
    if any(not isinstance(name,str) or not name.strip() for name in exceptions):return None
    names=list(exceptions)
    for root in ('employment','work','internships'):
        for record in profile.get(root,[]):
            if isinstance(record,dict):
                names.extend(record[key] for key in ('company','organization','employer')
                             if isinstance(record.get(key),str) and record[key].strip())
    # A current fact or an exception always wins over the user's default.
    if any(employer.casefold() in name.casefold() or name.casefold() in employer.casefold() for name in names):return None
    return '/company_answer_defaults/prior_group_employment/default'
