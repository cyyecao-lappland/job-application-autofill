"""Independent source-bound Luna review of the recovered Biren application."""
import argparse
import json
from pathlib import Path
from edge_form_graph.model import CodexJsonModel, obj

parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--run-root', type=Path, required=True)
parser.add_argument('--profile', type=Path, required=True)
args = parser.parse_args()
root = args.run_root
def read(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))
evidence = read(root/'recovery/biren-submit-evidence.json')
profile = read(args.profile)
keys = ['identity', 'contact', 'education', 'preferences', 'attachments', 'batch_answers', 'company_answer_defaults']
source = {k:profile[k] for k in keys if k in profile}
data = {'application':evidence, 'profile':source,
        'job':read(root/'discovery/biren-ai-developer-detail-live.json'),
        'school_official':read(root/'discovery/ecnu-school-profile-live.json'),
        'history':read(root/'recovery/biren-account-history.json')}
model = CodexJsonModel(model='gpt-6-luna', timeout=120)
verdict = model.ask(
    'Independently review the complete CURRENT application page fields, official job, '
    'school source and candidate history. Check field values and files against canonical '
    'facts, current-page required coverage, role/city, duplicate history and hard eligibility. '
    'Use supplied official school evidence rather than outside school classifications. '
    '本科及以上学历（985学校）, 最好是研究生 allows the supplied 985 master education; '
    'it does not explicitly require both undergraduate and master universities to be 985. '
    'Ordinary technical fit gaps do not bar truthful applications. Empty optional referral '
    'is valid. Two same truthful resume files are redundant but not false. Workflow bool '
    'authorizes synchronize_online_resume. This is the PREVIEW gate: no submission-success '
    'evidence is required yet. All visible current-page fields are supplied, no required flag '
    'does not mean missing coverage. Later preview pages are separately reviewed. History '
    'explicitly 暂无投递记录 clears the two-roles quota. List ONLY actual errors in issues, '
    'never positive observations. Return true checks only where evidence supports them.',
    data, obj({'approved':{'type':'boolean'}, 'coverage_complete':{'type':'boolean'},
               'eligible':{'type':'boolean'}, 'history_clear':{'type':'boolean'},
               'issues':{'type':'array','items':{'type':'string'}}}))
review = {**verdict, 'model':'gpt-6-luna', 'evidence':evidence,
          'job_evidence':{'job':data['job'],'school':data['school_official']},
          'history_evidence':data['history'], 'profile_evidence':source, 'timings':model.last_timings}
plan = {'phase':'submit', 'job':{'company':'壁仞科技',
        'id':'19cef024-7e16-4889-b016-028b2ab084ac','title':'2027校招-AI开发工程师',
        'detail_url':data['job']['live_page']['url']},
        'action':{'selector':'button:has-text("预览并提交")','text':'预览并提交'},
        'outcome_mode':'independent_observation','review':review}
(root/'recovery/biren-submit-plan.json').write_text(json.dumps(plan,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(verdict, ensure_ascii=True))
