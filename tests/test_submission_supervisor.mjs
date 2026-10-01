import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,mkdir,readFile,writeFile,rm} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {assertSubmissionReview,dispatchReviewedSubmission,observeSubmission,captureSubmissionEvidence,releaseBlockedSubmissionReservation,acceptReviewedSubmissionOutcome} from '../browser/submission_supervisor.mjs';
const live={target:{browser_id:'edge',tab_id:'one',url:'https://example.org/job/1/apply'},snapshot:{fields:[{id:'name',value:'verified'}]},text:'reviewed application'};
const summary={status:'scope_draft',pending_kind:null,manual_review_count:0,coverage_gaps:[]};
test('captured evidence equals its saved JSON representation',async()=>{
 const session={browserId:'edge',tab:{id:'one',url:async()=>live.target.url,playwright:{evaluate:async()=>({fields:[{id:'name',value:'verified',upload_ready:undefined}]}),locator:()=>({innerText:async()=>live.text})}}};
 const e=await captureSubmissionEvidence(session);
 assert.deepEqual(e,JSON.parse(JSON.stringify(e)));
});
function plan(){return {phase:'submit',job:{company:'Company',id:'1',title:'AI',detail_url:'https://example.org/job/1'},
 action:{selector:'#submit',text:'提交简历'},success:{selector:'#success',text:'投递成功'},
 review:{model:'gpt-6-luna',approved:true,coverage_complete:true,eligible:true,history_clear:true,issues:[],evidence:structuredClone(live),job_evidence:{official:true},history_evidence:{clear:true},profile_evidence:{source:'canonical'}}};}
test('stale values, wrong reviewer, incomplete coverage, unknown eligibility, and pending writer block submission',()=>{
 assert.doesNotThrow(()=>assertSubmissionReview(plan(),live,summary));
 for(const mutate of [p=>p.review.evidence.snapshot.fields[0].value='changed',p=>p.review.model='program',p=>p.review.coverage_complete=false,p=>p.review.eligible=false,p=>p.review.history_clear=false]){
  const p=plan();mutate(p);assert.throws(()=>assertSubmissionReview(p,live,summary));
 }
 assert.throws(()=>assertSubmissionReview(plan(),live,{...summary,pending_kind:'fill'}));
 assert.throws(()=>assertSubmissionReview(plan(),live,{...summary,coverage_gaps:[{reason:'module_blocked'}]}));
});
test('unseen website result may be inspected independently without inventing a success selector',()=>{
 const p=plan();delete p.success;p.outcome_mode='independent_observation';
 assert.doesNotThrow(()=>assertSubmissionReview(p,live,summary));
 delete p.outcome_mode;assert.throws(()=>assertSubmissionReview(p,live,summary),/success_evidence_rule/);
});
async function harness(t,{timeout=false,pending=false,change=false}={}){
 const directory=await mkdtemp(join(tmpdir(),'application-submit-'));
 t.after(()=>rm(directory,{recursive:true,force:true}));
 await mkdir(join(directory,'writer'));
 if(pending)await writeFile(join(directory,'writer','old.json'),JSON.stringify({kind:'save',receipt:{settled:false,status:'unknown'}}));
 let clicks=0,captures=0;
 const button={count:async()=>1,isVisible:async()=>true,isEnabled:async()=>true,innerText:async()=> '提交简历',click:async()=>{
  clicks++;
  const j=JSON.parse(await readFile(join(directory,'writer','submission','submit-journal.json'),'utf8'));
  assert.equal(j.status,'pending');
  if(timeout)throw new Error('timeout');
 }};
 const session={tab:{playwright:{locator:selector=>selector==='#submit'?button:{count:async()=>0}}}};
 const options={session,runDirectory:directory,plan:plan(),getSummary:async()=>summary,
  capture:async()=>{captures++;return change&&captures>1?{...live,text:'changed'}:structuredClone(live);},
  observe:async(_s,_p,j,checkpoint)=>{j.status='submission_unknown';await checkpoint(j);return j;}};
 return {options,clicks:()=>clicks};
}
test('journal exists before click, and a completed unknown action cannot be replayed',async t=>{
 const h=await harness(t);assert.equal((await dispatchReviewedSubmission(h.options)).status,'submission_unknown');
 assert.equal(h.clicks(),1);await assert.rejects(()=>dispatchReviewedSubmission(h.options),/already_dispatched/);assert.equal(h.clicks(),1);
});
test('timeout remains unknown and cannot replay',async t=>{
 const h=await harness(t,{timeout:true});const r=await dispatchReviewedSubmission(h.options);
 assert.equal(r.status,'submission_unknown');assert.equal(r.click_returned,false);
 await assert.rejects(()=>dispatchReviewedSubmission(h.options));assert.equal(h.clicks(),1);
 await assert.rejects(()=>releaseBlockedSubmissionReservation(h.options.runDirectory),/preflight_only/);
});
test('preflight failure can release a reservation while preserving original journal',async t=>{
 const h=await harness(t,{pending:true});
 assert.equal((await dispatchReviewedSubmission(h.options)).status,'blocked_before_dispatch');
 assert.equal((await releaseBlockedSubmissionReservation(h.options.runDirectory)).status,'preflight_reservation_released');
 assert.equal(h.clicks(),0);
});
test('pending original save and values changed before click dispatch nothing',async t=>{
 for(const options of [{pending:true},{change:true}]){
  const h=await harness(t,options);assert.equal((await dispatchReviewedSubmission(h.options)).status,'blocked_before_dispatch');assert.equal(h.clicks(),0);
 }
});
test('an Add stopped during read-only baseline checks does not block a reviewed submission',async t=>{
 const h=await harness(t);
 const journal={kind:'add_module_record',receipt:{settled:true,status:'unconfirmed',results:[],evidence:{reason:'open_record_count_changed'}},instrumentation:[{method:'count',status:'returned'},{method:'evaluate',status:'returned'}]};
 await writeFile(join(h.options.runDirectory,'writer','add.json'),JSON.stringify(journal));
 assert.equal((await dispatchReviewedSubmission(h.options)).status,'submission_unknown');assert.equal(h.clicks(),1);
});
test('an Add with any click or unfinished read remains a submission blocker',async t=>{
 for(const step of [{method:'click',status:'returned'},{method:'evaluate',status:'failed'}]){
  const h=await harness(t);
  const journal={kind:'add_module_record',receipt:{settled:true,status:'unconfirmed',results:[],evidence:{reason:'open_record_count_changed'}},instrumentation:[step]};
  await writeFile(join(h.options.runDirectory,'writer','add.json'),JSON.stringify(journal));
  assert.equal((await dispatchReviewedSubmission(h.options)).status,'blocked_before_dispatch');assert.equal(h.clicks(),0);
 }
});
test('confirmation cannot dispatch without observed reviewed first phase',async t=>{
 const h=await harness(t);h.options.plan.phase='confirm';
 assert.equal((await dispatchReviewedSubmission(h.options)).status,'blocked_before_dispatch');assert.equal(h.clicks(),0);
});
test('new website success needs current independent review and never clicks again',async t=>{
 const directory=await mkdtemp(join(tmpdir(),'application-outcome-'));
 t.after(()=>rm(directory,{recursive:true,force:true}));
 const folder=join(directory,'writer','submission');await mkdir(folder,{recursive:true});
 const evidence={...structuredClone(live),text:'投递成功'};
 const session={browserId:'edge',tab:{id:'one',url:async()=>live.target.url,playwright:{
   evaluate:async()=>structuredClone(live.snapshot),
   locator:s=>s==='body'?{innerText:async()=>evidence.text}:{count:async()=>1,isVisible:async()=>true,innerText:async()=>evidence.text}}}};
 const journal={status:'awaiting_user_verification',plan:plan(),before:structuredClone(live)};
 await writeFile(join(folder,'confirm-journal.json'),JSON.stringify(journal));
 const review={model:'gpt-6-luna',approved:true,issues:[],outcome:'submitted',evidence,
   website_evidence:{selector:'#success',text:'投递成功'}};
 await assert.rejects(()=>acceptReviewedSubmissionOutcome({session,runDirectory:directory,phase:'confirm',review:{...review,evidence:live}}),/stale/);
 await assert.rejects(()=>acceptReviewedSubmissionOutcome({session,runDirectory:directory,phase:'confirm',review:{...review,model:'program'}}),/independent/);
 assert.equal((await acceptReviewedSubmissionOutcome({session,runDirectory:directory,phase:'confirm',review})).status,'submitted');
});
