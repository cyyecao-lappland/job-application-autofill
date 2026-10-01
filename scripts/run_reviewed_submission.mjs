/** Continuous Moka review/preview/submit; existing dispatches are read-only reconciled. */
import {readFile,writeFile,mkdir,appendFile} from 'node:fs/promises';
import {join,resolve} from 'node:path';
import {fileURLToPath} from 'node:url';
import {connectEdgeCdp} from '../browser/cdp_connector.mjs';
import {runProcess,defaultPython} from '../host/native_playwright_driver.mjs';
import {captureSubmissionEvidence,dispatchReviewedSubmission,reconcileReviewedSubmission,
 acceptReviewedSubmissionOutcome} from '../browser/submission_supervisor.mjs';

const read=async path=>JSON.parse((await readFile(path,'utf8')).replace(/^\uFEFF/,''));
async function optional(path){try{return await read(path);}catch(e){if(e.code==='ENOENT')return null;throw e;}}
export function settledForSubmission(summary){
 return !summary.pending_kind && !summary.manual_review_count &&
  ['complete','incomplete_coverage','scope_draft'].includes(summary.status) &&
  (summary.coverage_gaps||[]).every(g=>g.reason==='scope_draft');
}

export async function runReviewedSubmission(config){
 const project=resolve(config.projectDirectory||'.'),run=resolve(config.runDirectory);
 const python=config.python||await defaultPython(project);
 const folder=join(run,'submission-reviews');await mkdir(folder,{recursive:true});
 const stamp=Date.now(), file=name=>join(folder,`${stamp}-${name}.json`);
 const save=async(name,data)=>{const path=file(name);await writeFile(path,JSON.stringify(data,null,2));return path;};
 const command=args=>runProcess(python,['-m','edge_form_graph.application_cli',...args],{cwd:project});
 const journalPath=phase=>join(run,'writer','submission',`${phase}-journal.json`);
 const connection=await connectEdgeCdp({pageId:config.pageId,endpoint:config.endpoint});
 const session=connection.session;
 async function successful(journal){
  if(!journal.success_evidence||!journal.outcome_review?.approved)return {status:'submitted_evidence_incomplete'};
  let newlyRecorded=false;
  if(config.ledger){
   let rows=[];try{rows=(await readFile(config.ledger,'utf8')).split('\n').filter(Boolean).map(JSON.parse);}catch(e){if(e.code!=='ENOENT')throw e;}
   if(!rows.some(r=>r.job.company===journal.job.company&&r.job.id===journal.job.id)){
    await appendFile(config.ledger,JSON.stringify({job:journal.job,confirmed_at:journal.observed_at,
     website_message:journal.outcome_review.website_evidence.text,
     submission_journal:journalPath(journal.phase),outcome_review:journal.outcome_review})+'\n');
    newlyRecorded=true;
   }
  }
  let closed=false;try{await connection.closePage?.();closed=!!connection.closePage;}catch{}
  return {status:'submitted',job:journal.job,new_submissions:newlyRecorded?1:0,page_closed:closed};
 }
 async function reviewPlan(phase,evidence,original,preview){
  const evidencePath=await save(`${phase}-evidence`,evidence),output=file(`${phase}-plan`);
  const args=['scripts/review_application_submission.py','--evidence',evidencePath,
   '--profile',resolve(config.profile),'--job',resolve(config.jobEvidence),
   '--history',resolve(config.historyEvidence),'--company',config.job.company,
   '--job-id',config.job.id,'--job-title',config.job.title,'--output',output];
  if(original)args.push('--original-plan',original);
  if(preview)args.push('--preview',await save('visible-preview',preview));
  const verdict=await runProcess(python,args,{cwd:project,timeoutMs:150000});
  const plan=await read(output);
  return {verdict,plan,path:output};
 }
 try{
  let confirm=await optional(journalPath('confirm')),submit=await optional(journalPath('submit'));
  for(const journal of [submit,confirm].filter(Boolean)){
   if(journal.job?.id!==config.job.id||journal.job?.company!==config.job.company)throw new Error('submission_job_configuration_changed');
  }
  if(confirm?.status==='submitted')return await successful(confirm);
  if(submit?.status==='submitted')return await successful(submit);
  const summary=await command(['status','--run-dir',run]);
  if(!settledForSubmission(summary))return {status:'application_coverage_blocked',summary};
  if(!submit){
   const gate=await reviewPlan('submit',await captureSubmissionEvidence(session));
   if(!gate.verdict.approved)return {status:'independent_review_blocked',issues:gate.verdict.issues};
   submit=await dispatchReviewedSubmission({session,runDirectory:run,plan:gate.plan,
    getSummary:()=>command(['status','--run-dir',run])});
  }
  for(let stage=0;stage<2;stage++){
   const phase=confirm?'confirm':'submit';
   let journal=confirm||submit;
   if(journal.status==='blocked_before_dispatch')return {status:journal.status,error:journal.error};
   journal=await reconcileReviewedSubmission({session,runDirectory:run,phase});
   if(journal.status==='submitted')return await successful(journal);
   if(journal.status==='awaiting_user_verification')return {status:journal.status,pageId:config.pageId};
   const evidence=await captureSubmissionEvidence(session);
   const preview=await session.tab.playwright.evaluate(()=>{
    const roots=[...document.querySelectorAll('.apply-form-preview')].filter(e=>e.getClientRects().length);
    if(roots.length!==1)return null;
    const buttons=[...roots[0].querySelectorAll('button')].filter(e=>e.getClientRects().length&&e.innerText.trim()==='确认提交');
    return buttons.length===1?{selector:'.apply-form-preview',text:roots[0].innerText,
     button:{selector:'.apply-form-preview button.btn-ok',text:'确认提交'}}:null;
   });
   if(phase==='submit'&&preview){
    if(journal.click_returned!==true)return {status:'submission_unknown'};
    const original=await save('original-submit-plan',journal.plan);
    const gate=await reviewPlan('confirm',evidence,original,preview);
    if(!gate.verdict.approved)return {status:'independent_preview_review_blocked',issues:gate.verdict.issues};
    const outcome={...gate.plan.review,phase:'submit',outcome:'confirmation',website_evidence:preview.button};
    await acceptReviewedSubmissionOutcome({session,runDirectory:run,phase:'submit',review:outcome});
    confirm=await dispatchReviewedSubmission({session,runDirectory:run,plan:gate.plan,
     getSummary:()=>command(['status','--run-dir',run])});
    continue;
   }
   const candidates=await session.tab.playwright.evaluate(()=>{
    const text='已成功提交申请！',elements=[...document.querySelectorAll('body *')].filter(e=>
     e.getClientRects().length&&e.innerText?.trim()===text&&![...e.children].some(c=>c.innerText?.trim()===text));
    if(elements.length!==1)return [];
    const e=elements[0],parts=[];let node=e;
    while(node&&node!==document.body){const peers=[...node.parentElement.children].filter(x=>x.tagName===node.tagName);
     parts.unshift(node.tagName.toLowerCase()+':nth-of-type('+(peers.indexOf(node)+1)+')');node=node.parentElement;}
    return [{id:'observed-success',selector:'body > '+parts.join(' > '),text}];
   });
   if(!candidates.length)return {status:'submission_unknown'};
   const evidencePath=await save('outcome-evidence',evidence),candidatePath=await save('outcome-candidates',candidates),output=file('outcome-review');
   const verdict=await runProcess(python,['scripts/review_submission_outcome.py','--evidence',evidencePath,
    '--journal',journalPath(phase),'--candidates',candidatePath,'--output',output],{cwd:project,timeoutMs:150000});
   if(!verdict.approved)return {status:'submission_unknown',issues:verdict.issues};
   journal=await acceptReviewedSubmissionOutcome({session,runDirectory:run,phase,review:await read(output)});
   if(journal.status==='submitted')return await successful(journal);
   return {status:journal.status};
  }
  return {status:'submission_unknown'};
 }finally{await connection.closeConnection();}
}

if(process.argv[1]&&resolve(process.argv[1])===fileURLToPath(import.meta.url)){
 try{console.log(JSON.stringify(await runReviewedSubmission(await read(process.argv[2]))));}
 catch(e){console.error(JSON.stringify({error:e.message}));process.exitCode=1;}
}
