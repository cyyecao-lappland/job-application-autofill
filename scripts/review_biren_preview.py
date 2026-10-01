"""Review observed confirmation page before the final irreversible click."""
import argparse
import json
from pathlib import Path
from edge_form_graph.model import CodexJsonModel, obj
parser = argparse.ArgumentParser(description=__doc__)
parser.add_argument('--recovery-root', type=Path, required=True)
root = parser.parse_args().recovery_root
def read(name):
    return json.loads((root/name).read_text(encoding='utf-8'))
first = read('biren-submit-plan.json')
evidence = read('biren-after-preview.json')
data = {'preview':evidence, 'original_form':first['review']['evidence'],
        'profile':first['review']['profile_evidence'],
        'job_sources':first['review']['job_evidence'],
        'history':first['review']['history_evidence']}
model = CodexJsonModel(model='gpt-6-luna', timeout=120)
v = model.ask('Independently review the actual website preview and original facts. Verify '
    'name, phone, email, Shanghai intent and resume filenames, job and hard eligibility. '
    'Confirm this is a preview requiring the observed 确认提交 button, not success. '
    'All current required fields must be represented; optional blank referral is allowed. '
    'Second identical truthful resume is redundant but valid. Use official school source '
    'for 985 status, master is 本科及以上. Technical fit gaps are not hard gates. '
    'No prior success is needed before final submit. Issues contains ONLY actual errors. '
    'Approve only if the current preview is source-grounded and safe to submit.',data,
    obj({'approved':{'type':'boolean'},'coverage_complete':{'type':'boolean'},
         'eligible':{'type':'boolean'},'history_clear':{'type':'boolean'},
         'is_confirmation':{'type':'boolean'},'issues':{'type':'array','items':{'type':'string'}}}))
review = {**v, 'model':'gpt-6-luna','evidence':evidence,
          'job_evidence':data['job_sources'],'history_evidence':data['history'],
          'profile_evidence':data['profile'],'timings':model.last_timings}
outcome = {**review,'approved':v['approved'] and v['is_confirmation'],
           'phase':'submit','outcome':'confirmation',
           'website_evidence':{'selector':'button:has-text("确认提交")','text':'确认提交'}}
plan = {**first,'phase':'confirm','review':review,
        'action':{'selector':'button:has-text("确认提交")','text':'确认提交'}}
for name,value in [('biren-preview-outcome.json',outcome),('biren-confirm-plan.json',plan)]:
    (root/name).write_text(json.dumps(value,ensure_ascii=False,indent=2),encoding='utf-8')
print(json.dumps(v, ensure_ascii=True))
