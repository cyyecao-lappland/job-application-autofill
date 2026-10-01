"""Summarize existing evidence without modifying application journals or checkpoints."""
import json
from datetime import datetime, timezone
from pathlib import Path

root = Path('private/mass-apply-20260930')
path = root / 'application-progress.json'
progress = json.loads(path.read_text(encoding='utf-8-sig'))
items = {item['company']: item for item in progress['applications']}
for company in (root / 'recovery').iterdir():
    if not company.is_dir():
        continue
    summaries = list(company.glob('attempts/*/summary.json'))
    if not summaries:
        continue
    summary_path = max(summaries, key=lambda p: p.parent.name)
    summary = json.loads(summary_path.read_text(encoding='utf-8-sig'))
    item = {'company': company.name, 'run_dir': str(summary_path.parent.resolve()),
            'status': summary['status'], 'pending_kind': summary.get('pending_kind'),
            'coverage_gaps': summary.get('coverage_gaps', []),
            'saved_scopes': summary.get('scopes', {}), 'confirmed_submitted': False,
            'unanswerable_required_fields': summary.get('unanswerable_required_fields', [])}
    for phase in ['confirm', 'submit']:
        journal_path = summary_path.parent / 'writer' / 'submission' / (phase + '-journal.json')
        if not journal_path.exists():
            continue
        journal = json.loads(journal_path.read_text(encoding='utf-8-sig'))
        item['formal_submission_status'] = journal['status']
        item['submission_evidence'] = str(journal_path.resolve())
        item['confirmed_submitted'] = journal['status'] == 'submitted'
        break
    items[company.name] = {**items.get(company.name, {}), **item}
progress['applications'] = list(items.values())
confirmed = []
ledger = root / 'confirmed-submissions.jsonl'
for line in ledger.read_text(encoding='utf-8-sig').splitlines():
    if not line.strip():
        continue
    row = json.loads(line)
    journal_path = Path(row['submission_journal'])
    journal = json.loads(journal_path.read_text(encoding='utf-8-sig'))
    if journal['status'] == 'submitted' and journal.get('success_evidence'):
        confirmed.append((row['job']['company'], row['job']['id']))
progress['new_confirmed_submissions'] = len({company for company, job in confirmed})
progress['observed_at'] = datetime.now(timezone.utc).isoformat()
for key, page, reason in [
    ('step', '8E5BEA87B8365C55814317093F408D45', 'final_submission_slider_verification'),
    ('anker', 'B1E8512C23FC44AC92D4BE6CE0B3C9BA', 'website_login'),
    ('lilith', 'C2548C3F50067819F6DEADD55BA3282A', 'website_login'),
    ('kingdee', '01739AF8381D8B81FB7F93246DEB4FB7', 'website_login'),
    ('4399', 'B9647D34F83C29ECF304A1A0EFE1EB95', 'website_login')]:
    if not any(item['company'] == key for item in progress['user_actions']):
        progress['user_actions'].append({'company': key, 'page_id': page, 'reason': reason})
progress['runtime_status'] = 'no_active_application_driver; website drafts preserved'
progress['throughput_evidence']['worker_command_overhead'] = {
    'evidence': str(root / 'worker-overhead-measurement.json'),
    'scope': 'Registry command cold-process overhead versus reused worker. Not end-to-end application throughput.'}
progress.setdefault('latest_session_baseline_confirmed', 1)
progress['latest_session_new_confirmed_submissions'] = max(
    0, progress['new_confirmed_submissions'] - progress['latest_session_baseline_confirmed'])
temporary = path.with_suffix('.json.tmp')
temporary.write_text(json.dumps(progress, ensure_ascii=False, indent=2), encoding='utf-8')
temporary.replace(path)
print(json.dumps({'confirmed_companies': progress['new_confirmed_submissions'],
                  'applications': len(progress['applications']),
                  'step': items['step']['formal_submission_status'],
                  'hundsun': items['hundsun']['status']}, ensure_ascii=False))
