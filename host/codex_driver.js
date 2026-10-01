/* Trusted host glue for Codex code-mode. This file is NOT model-generated plan data.
 * Load once using new AsyncFunction('tools','notify','options', source), then call it.
 * Browser bootstrap/documentation and the existing-tab claim happen beforehand.
 * No model reasoning is needed between batches. Only mcp__node_repl__js controls Edge.
 */
const quotePS = value => "'" + String(value).replace(/'/g,"''") + "'";
const binding = value => {
  if(!/^[A-Za-z_$][\w$]*$/.test(value))throw new Error('invalid_binding');
  return value;
};
const edgeBinding=binding(options.edgeBinding || 'edge');
const tabBinding=binding(options.tabBinding || 'resumeTab');
const executorBinding=binding(options.executorBinding || 'edgeFormExecutor');
const runDir=options.runDirectory;
if(!runDir || !options.projectDirectory)throw new Error('paths_required');
const launcher=options.projectDirectory.replace(/[\\/]$/,'')+'/run.ps1';

async function command(args){
  if(options.application)args=['application',...args];
  let lastNotice=Date.now();
  let result=await tools.exec_command({cmd:'& '+quotePS(launcher)+' '+args.map(quotePS).join(' '),
    workdir:options.projectDirectory,max_output_tokens:3000,yield_time_ms:1000});
  let output=result.output;
  while(result.session_id){
    result=await tools.write_stdin({session_id:result.session_id,chars:'',yield_time_ms:1000,max_output_tokens:3000});
    output+=result.output;
    if(Date.now()-lastNotice>45000){notify({status:'model_processing_module'});lastNotice=Date.now();}
  }
  if(result.exit_code!==0)throw new Error('graph_command_failed: '+output);
  return JSON.parse(output);
}

const first=options.start?['start','--run-dir',runDir,...(options.profile?['--profile',options.profile]:[]),
  ...(options.application?['--manifest',options.manifest,'--prior-root',options.priorRoot]:['--snapshot',options.snapshot]),
  ...(options.allowSave?['--allow-save']:[])]:['status','--run-dir',runDir];
let status=await command(first);
notify({status:status.status,metrics:status.metrics});
for(let batch=0;batch<100&&(options.application?!!status.pending_kind:['awaiting_edge','awaiting_save'].includes(status.status));batch++){
  const result=await tools.mcp__node_repl__js({
    code:'nodeRepl.write(await '+executorBinding+'.runRequest('+edgeBinding+','+tabBinding+','+JSON.stringify(runDir)+'));',
    title:status.pending_kind==='observe'?'读取当前简历模块':status.status==='awaiting_save'||status.pending_kind==='save_scope'?'保存已核验的简历范围':'执行 Edge 表单批次',timeout_ms:120000});
  if(result.isError)throw new Error('edge_host_error_pending_preserved');
  // No receipt is synthesized here. The coordinator accepts only the writer's on-disk journal.
  status=await command(['resume','--run-dir',runDir]);
  notify({status:status.status,metrics:status.metrics});
}
return status;
