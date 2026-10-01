/** Independent pages advance concurrently; each page keeps its own original journals. */
import {readFile,writeFile,mkdir,rename} from 'node:fs/promises';
import {resolve} from 'node:path';
import {runNativePlaywrightDriver} from '../host/native_playwright_driver.mjs';
import {runReviewedSubmission,settledForSubmission} from './run_reviewed_submission.mjs';
const queue=JSON.parse(await readFile(process.argv[2],'utf8'));
const destination=resolve(process.argv[3]||'private/mass-apply-20260930/batch');
await mkdir(destination,{recursive:true});
if(!Array.isArray(queue)||!queue.length)throw new Error('queue_required');
if(new Set(queue.map(x=>x.pageId)).size!==queue.length)throw new Error('duplicate_page_writer');
if(new Set(queue.map(x=>x.company)).size!==queue.length)throw new Error('duplicate_company_in_batch');
const results=[];
let cursor=0;
let progressWrite=Promise.resolve();
async function record(entry){
 results.push(entry);
 const snapshot=JSON.stringify(results,null,2);
 progressWrite=progressWrite.then(async()=>{
  const temporary=`${destination}/progress.json.tmp`;
  await writeFile(temporary,snapshot);await rename(temporary,`${destination}/progress.json`);
 });
 await progressWrite;console.log(JSON.stringify(entry));
}
async function worker(){
 while(cursor<queue.length){
  const item=queue[cursor++];
  if(!/^[a-z0-9-]+$/.test(item.company))throw new Error('invalid_company_key');
  const started=Date.now();
  try{
   const result=await runNativePlaywrightDriver({
    ...item,start:!item.resume,allowSave:true,
    agentTuning:item.resume?undefined:'on',controlAgent:'on',
    onStatus:s=>console.log(JSON.stringify({company:item.company,status:s.status,module:s.module_index,pending:s.pending_kind}))
   });
   await writeFile(`${destination}/${item.company}-result.json`,JSON.stringify(result,null,2));
   let submission;
   if(item.submission&&settledForSubmission(result)){
    submission=await runReviewedSubmission({...item,...item.submission,runDirectory:result.run_dir});
    await writeFile(`${destination}/${item.company}-submission-result.json`,JSON.stringify(submission,null,2));
   }
   await record({company:item.company,status:submission?.status||result.status,
    queue_disposition:result.queue_disposition,run_dir:result.run_dir,
    elapsed_seconds:(Date.now()-started)/1000,new_submissions:submission?.new_submissions||0});
  }catch(error){await record({company:item.company,status:'blocked',error:error.message,elapsed_seconds:(Date.now()-started)/1000,new_submissions:0});}
 }
}
await Promise.all(Array.from({length:Math.min(2,queue.length)},worker));
