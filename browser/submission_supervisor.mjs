/** A reviewed UI submission, recorded before dispatch; never replays uncertain clicks. */
import {mkdir,readFile,writeFile,rename,readdir,rmdir} from 'node:fs/promises';
import {join} from 'node:path';
import {isDeepStrictEqual} from 'node:util';
import {readModuleDOM} from './rules/dom.mjs';

async function atomic(path,value){
 const temporary=`${path}.tmp`;
 await writeFile(temporary,JSON.stringify(value,null,2));
 await rename(temporary,path);
}
export async function captureSubmissionEvidence(session){
 return JSON.parse(JSON.stringify({target:{browser_id:session.browserId,tab_id:session.tab.id,url:await session.tab.url()},
  snapshot:await session.tab.playwright.evaluate(readModuleDOM,{moduleSelector:'body'}),
  text:await session.tab.playwright.locator('body').innerText({timeoutMs:5000})}));
}
export async function releaseBlockedSubmissionReservation(runDirectory,phase='submit'){
 if(!['submit','confirm'].includes(phase))throw new Error('submission_phase_required');
 const folder=join(runDirectory,'writer','submission');
 const file=join(folder,`${phase}-journal.json`);
 const journal=JSON.parse(await readFile(file,'utf8'));
 if(journal.status!=='blocked_before_dispatch' || journal.before || journal.plan ||
    journal.click_returned!==undefined)throw new Error('preflight_only_release_required');
 await rename(file,join(folder,`${phase}-blocked-${Date.now()}.json`));
 await rmdir(join(folder,`dispatch-${phase}.lock`));
 return {status:'preflight_reservation_released',phase};
}
export function assertSubmissionReview(plan,live,summary){
 if(!plan || !['submit','confirm'].includes(plan.phase))throw new Error('submission_phase_required');
 if(!summary || summary.pending_kind || summary.manual_review_count ||
    !['complete','incomplete_coverage','scope_draft'].includes(summary.status))throw new Error('application_not_settled');
 if((summary.coverage_gaps||[]).some(g=>g.reason!=='scope_draft'))throw new Error('application_coverage_blocked');
 if(!plan.job?.company || !plan.job.id || !plan.job.title || !plan.job.detail_url)throw new Error('verified_job_required');
 const review=plan.review;
 if(!review || review.model!=='gpt-6-luna' || review.approved!==true ||
    review.coverage_complete!==true || review.eligible!==true || review.history_clear!==true ||
    !Array.isArray(review.issues) || review.issues.length)throw new Error('independent_submission_review_required');
 if(!isDeepStrictEqual(review.evidence,live))throw new Error('submission_review_stale');
 if(!review.job_evidence || !review.history_evidence || !review.profile_evidence)throw new Error('submission_sources_required');
 if(!plan.action?.selector || !/^(预览并提交|提交简历|投递简历|确认提交|确认投递|提交申请|Submit|Apply)$/i.test(plan.action.text))throw new Error('reviewed_submission_control_required');
 if(plan.outcome_mode!=='independent_observation' && (!plan.success?.selector || !plan.success.text))throw new Error('success_evidence_rule_required');
}
async function assertOriginalWritersSettled(runDirectory){
 for(const name of await readdir(join(runDirectory,'writer'))){
  if(!name.endsWith('.json'))continue;
  const journal=JSON.parse(await readFile(join(runDirectory,'writer',name),'utf8'));
  if(journal.kind==='observe')continue;
  const receipt=journal.receipt;
  // This original Add failed during read-only baseline checks. Its journal is
  // retained; it cannot represent an uncertain insertion because no action ran.
  const steps=journal.instrumentation||[];
  if(journal.kind==='add_module_record' && !journal.remote_unsettled && receipt?.settled===true &&
     receipt.status==='unconfirmed' && !receipt.results?.length &&
     receipt.evidence?.reason==='open_record_count_changed' && steps.length &&
     steps.every(s=>['count','evaluate'].includes(s.method)&&s.status==='returned'))continue;
  if(journal.remote_unsettled || !receipt?.settled || ['unknown','unconfirmed'].includes(receipt.status) ||
     receipt.results?.some(r=>r.status==='unknown'))throw new Error('original_writer_reconciliation_required');
 }
}
async function visibleExact(session,rule){
 const locator=session.tab.playwright.locator(rule.selector);
 if(await locator.count()!==1 || !await locator.isVisible())return false;
 return (await locator.innerText({timeoutMs:3000})).trim()===rule.text;
}
export async function observeSubmission(session,plan,journal,checkpoint){
 const evidence=await captureSubmissionEvidence(session);
 // Even a timeout may have submitted. Only a new, exact website success is proof.
 if(plan.success && await visibleExact(session,plan.success)){
  journal.status='submitted'; journal.success_evidence=evidence;
 }else if(plan.confirmation && await visibleExact(session,plan.confirmation)){
  journal.status='awaiting_confirmation_review';journal.confirmation_evidence=evidence;
 }else if(evidence.text.includes('请完成安全验证')&&evidence.text.includes('向右拖动滑块填充拼图')){
  journal.status='awaiting_user_verification';journal.observation=evidence;
 }else{journal.status='submission_unknown';journal.observation=evidence;}
 journal.observed_at=new Date().toISOString();await checkpoint(journal);
 return journal;
}
export async function dispatchReviewedSubmission({session,runDirectory,plan,getSummary,
 capture=captureSubmissionEvidence,observe=observeSubmission}){
 const folder=join(runDirectory,'writer','submission');
 await mkdir(folder,{recursive:true});
 if(!['submit','confirm'].includes(plan?.phase))throw new Error('submission_phase_required');
 const lock=join(folder,`dispatch-${plan.phase}.lock`);
 // Retained after any dispatch, including process crashes. Recovery is read-only.
 try{await mkdir(lock);}catch(error){if(error.code==='EEXIST')throw new Error('submission_already_dispatched_or_reserved');throw error;}
 const file=join(folder,`${plan.phase}-journal.json`);
 let dispatched=false;
 const journal={kind:'formal_submission',phase:plan?.phase,job:plan?.job,status:'reserved',created_at:new Date().toISOString()};
 const checkpoint=value=>atomic(file,value);
 await checkpoint(journal);
 try{
  if(plan.phase==='confirm'){
   const parent=JSON.parse(await readFile(join(folder,'submit-journal.json'),'utf8'));
   if(!['awaiting_confirmation_review','submission_unknown'].includes(parent.status) || parent.click_returned!==true ||
      !isDeepStrictEqual(parent.job,plan.job) || !isDeepStrictEqual(parent.confirmation_evidence,plan.review?.evidence))
    throw new Error('reviewed_confirmation_parent_required');
  }
  await assertOriginalWritersSettled(runDirectory);
  const live=await capture(session);
  assertSubmissionReview(plan,live,await getSummary());
  if(plan.success && await visibleExact(session,plan.success))throw new Error('preexisting_success_requires_history_reconciliation');
  const button=session.tab.playwright.locator(plan.action.selector);
  if(await button.count()!==1 || !await button.isVisible() || !await button.isEnabled() ||
     (await button.innerText({timeoutMs:3000})).trim()!==plan.action.text)throw new Error('submission_control_changed');
  // Bind the values again immediately before persisting dispatch.
  if(!isDeepStrictEqual(live,await capture(session)))throw new Error('submission_values_changed');
  journal.status='pending';journal.plan=plan;journal.before=live;
  await checkpoint(journal);dispatched=true;
  try{await button.click({timeoutMs:15000});journal.click_returned=true;}
  catch(error){journal.click_returned=false;journal.click_error=error.name;}
  await checkpoint(journal);
  return await observe(session,plan,journal,checkpoint);
 }catch(error){
  journal.status=dispatched?'submission_unknown':'blocked_before_dispatch';journal.error=error.message;
  await checkpoint(journal);return journal;
 }
}
export async function acceptReviewedSubmissionOutcome({session,runDirectory,phase='submit',review}){
 const file=join(runDirectory,'writer','submission',`${phase}-journal.json`);
 const journal=JSON.parse(await readFile(file,'utf8'));
 if(!journal.plan || !['submission_unknown','awaiting_confirmation_review','awaiting_user_verification'].includes(journal.status))throw new Error('dispatched_outcome_required');
 const evidence=await captureSubmissionEvidence(session);
 if(!isDeepStrictEqual(review?.evidence,evidence) || evidence.target.tab_id!==journal.before.target.tab_id ||
    evidence.target.browser_id!==journal.before.target.browser_id)throw new Error('outcome_review_stale');
 if(review.model!=='gpt-6-luna' || review.approved!==true || !Array.isArray(review.issues) || review.issues.length ||
    !['submitted','confirmation'].includes(review.outcome) || !review.website_evidence?.selector || !review.website_evidence.text)
    throw new Error('independent_outcome_review_required');
 if(!await visibleExact(session,review.website_evidence))throw new Error('website_outcome_evidence_changed');
 if(review.outcome==='confirmation'){
   if(journal.click_returned!==true || phase!=='submit')throw new Error('confirmation_parent_unsettled');
   journal.status='awaiting_confirmation_review';journal.confirmation_evidence=evidence;
 }else{
   if(journal.before.text.includes(review.website_evidence.text))throw new Error('preexisting_success_requires_history_reconciliation');
   journal.status='submitted';journal.success_evidence=evidence;
 }
 journal.outcome_review=review;journal.observed_at=new Date().toISOString();
 await atomic(file,journal);return journal;
}
export async function reconcileReviewedSubmission({session,runDirectory,phase='submit'}){
 if(!['submit','confirm'].includes(phase))throw new Error('submission_phase_required');
 const file=join(runDirectory,'writer','submission',`${phase}-journal.json`);
 const journal=JSON.parse(await readFile(file,'utf8'));
 if(!journal.plan || journal.status==='blocked_before_dispatch')throw new Error('no_dispatched_submission');
 if(journal.status==='submitted')return journal;
 if(session.tab.id!==journal.before.target.tab_id || session.browserId!==journal.before.target.browser_id)throw new Error('submission_target_changed');
 return observeSubmission(session,journal.plan,journal,value=>atomic(file,value));
}
