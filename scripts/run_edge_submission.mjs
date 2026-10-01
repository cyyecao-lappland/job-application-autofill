/** Capture for Luna review, dispatch the reviewed control, or reconcile without clicking. */
import {readFile,writeFile} from 'node:fs/promises';
import {resolve} from 'node:path';
import {connectEdgeCdp} from '../browser/cdp_connector.mjs';
import {runProcess,defaultPython} from '../host/native_playwright_driver.mjs';
import {captureSubmissionEvidence,dispatchReviewedSubmission,reconcileReviewedSubmission,acceptReviewedSubmissionOutcome,releaseBlockedSubmissionReservation} from '../browser/submission_supervisor.mjs';
const [action,pageId,runDirectory,argument]=process.argv.slice(2);
if(!['capture','dispatch','reconcile','accept-outcome','release-preflight'].includes(action)||!pageId||!runDirectory)throw new Error('action_page_run_required');
const connection=await connectEdgeCdp({pageId});
try{
 let result;
 if(action==='capture'){
  if(!argument)throw new Error('evidence_output_required');
  const evidence=await captureSubmissionEvidence(connection.session);
  await writeFile(argument,JSON.stringify(evidence,null,2));
  result={status:'captured_for_independent_review',output:resolve(argument)};
 }else if(action==='release-preflight'){
  result=await releaseBlockedSubmissionReservation(runDirectory,argument||'submit');
 }else if(action==='accept-outcome'){
  const review=JSON.parse(await readFile(argument,'utf8'));
  result=await acceptReviewedSubmissionOutcome({session:connection.session,runDirectory,phase:review.phase||'submit',review});
 }else if(action==='reconcile'){
  result=await reconcileReviewedSubmission({session:connection.session,runDirectory,phase:argument||'submit'});
 }else{
  const python=await defaultPython(resolve('.'));
  const plan=JSON.parse(await readFile(argument,'utf8'));
  result=await dispatchReviewedSubmission({session:connection.session,runDirectory,plan,
   getSummary:()=>runProcess(python,['-m','edge_form_graph.application_cli','status','--run-dir',runDirectory],{cwd:resolve('.')})});
 }
 console.log(JSON.stringify({status:result.status,output:result.output,error:result.error,job:result.job}));
}finally{await connection.closeConnection();}
