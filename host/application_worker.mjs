/** One sequential Python host per page; lost responses never trigger retries. */
import {spawn} from 'node:child_process';
import {createInterface} from 'node:readline';

export function createApplicationWorker(python,{cwd,timeoutMs=600_000}={}){
 const child=spawn(python,['-m','edge_form_graph.application_worker'],{
  cwd,windowsHide:true,stdio:['pipe','pipe','pipe'],
  env:{...process.env,PYTHONUTF8:'1',PYTHONIOENCODING:'utf-8'}});
 let nextId=0,pending=null,closed=false,stderr='';
 const fail=error=>{if(pending){clearTimeout(pending.timer);pending.reject(error);pending=null;}};
 child.stderr.setEncoding('utf8').on('data',chunk=>{stderr=(stderr+chunk).slice(-8000);});
 child.stdin.on('error',error=>{closed=true;fail(error);});
 child.on('error',error=>{closed=true;fail(error);});
 child.on('close',code=>{closed=true;fail(new Error(`application_worker_closed:${code}:${stderr}`));});
 const lines=createInterface({input:child.stdout});
 lines.on('line',line=>{
  let message;try{message=JSON.parse(line);}catch{closed=true;fail(new Error('application_worker_invalid_json'));child.kill();return;}
  if(!pending||message.id!==pending.id){closed=true;fail(new Error('application_worker_response_mismatch'));child.kill();return;}
  const request=pending;pending=null;clearTimeout(request.timer);
  if(message.error!==undefined)request.reject(new Error(`application_command_failed:${message.error}:${message.diagnostic||''}`));
  else request.resolve(message.result);
 });
 return {
  command(args){
   if(closed)return Promise.reject(new Error('application_worker_closed'));
   if(pending)return Promise.reject(new Error('application_worker_concurrent_request'));
   const id=++nextId;
   return new Promise((resolve,reject)=>{
    const timer=setTimeout(()=>{closed=true;fail(new Error('application_command_timeout'));child.kill();},timeoutMs);
    pending={id,resolve,reject,timer};
    child.stdin.write(JSON.stringify({id,args})+'\n');
   });
  },
  close(){closed=true;fail(new Error('application_worker_closed'));child.stdin.end();lines.close();child.kill();}
 };
}
