import test from 'node:test';
import assert from 'node:assert/strict';
import {runNativePlaywrightDriver} from '../host/native_playwright_driver.mjs';

test('missing required fact skips remaining modules without another browser action',async()=>{
 const calls=[];
 const result=await runNativePlaywrightDriver({runDirectory:'C:/run',python:'python'}, {
  connect:async()=>({session:{},closeConnection:async()=>{calls.push('close');}}),
  command:async args=>{calls.push(args[0]);return args[0]==='check-control-registry'?
   {status:'registry_matched'}:{status:'module_blocked',pending_kind:null,program_inventory:true,
    control_exception_count:1,unanswerable_required_fields:[{label:'unknown',reason:'missing: no source'}]};},
  executeRequest:async()=>assert.fail('missing fact must not dispatch more actions')});
 assert.equal(result.queue_disposition,'skipped_missing_required_answer');
 assert.deepEqual(calls,['check-control-registry','status','close']);
});

test('an interrupted pure review continues without replaying a browser action',async()=>{
 const calls=[];
 const statuses=[{status:'module_filled',pending_kind:null,pure_review_pending:true},
  {status:'scope_draft',pending_kind:null}];
 const result=await runNativePlaywrightDriver({runDirectory:'C:/run',python:'python'},{
  connect:async()=>({session:{},closeConnection:async()=>{}}),
  command:async args=>{if(args[0]==='check-control-registry')return {status:'registry_matched'};calls.push(args[0]);return statuses.shift();},
  executeRequest:async()=>assert.fail('pure review must not execute a browser request')});
 assert.equal(result.status,'scope_draft');assert.deepEqual(calls,['status','resume-pure-review']);
});

test('targeted repair preserves but does not dispatch the next module command',async()=>{
 const calls=[];let closed=false;
 const statuses=[{status:'observed',module_index:3,pending_kind:'fill'},
  {status:'scope_draft',module_index:3,pending_kind:null,program_inventory:true},
  {status:'awaiting_observation',module_index:4,pending_kind:'observe'}];
 const result=await runNativePlaywrightDriver({runDirectory:'C:/run',python:'python',stopAtModuleBoundary:true},{
  connect:async()=>({session:{},closeConnection:async()=>{closed=true;}}),
  command:async args=>{if(args[0]==='check-control-registry')return {status:'registry_matched'};calls.push(args[0]);return statuses.shift();},
  executeRequest:async()=>calls.push('execute')});
 assert.deepEqual(calls,['status','execute','resume','defer-independent']);
 assert.equal(result.module_index,4);assert.equal(result.pending_kind,'observe');assert.equal(closed,true);
});

test('control agent uses same executor, refreshes diagnosis and reobserves after commit',async()=>{
  const calls=[];
  const pending=kind=>({status:'awaiting',pending_kind:kind});
  const stopped={status:'module_blocked',pending_kind:null};
  const statuses=[stopped,pending('observe'),stopped,
    {ready:[{label:'Date',source:'/education/0/start_date',adapter:'next_range_date_v1'}]},
    pending('observe'),stopped,pending('control_fill'),
    {status:'control_committed',pending_kind:null},pending('observe'),{status:'complete',pending_kind:null}];
  const result=await runNativePlaywrightDriver({runDirectory:'C:/run',python:'python'}, {
    connect:async()=>({session:{},closeConnection:async()=>{}}),
    command:async args=>{if(args[0]==='check-control-registry')return {status:'registry_matched'};calls.push(args[0]);return statuses.shift();},
    executeRequest:async()=>calls.push('execute')});
  assert.equal(result.status,'complete');
  assert.deepEqual(calls,['status','inspect-controls','execute','resume','resolve-controls',
    'inspect-controls','execute','resume','apply-control','execute','resume',
    'continue-after-control','execute','resume']);
});

test('control switch off and unknown save never call the control agent',async()=>{
  for(const options of [{controlAgent:'off',status:'module_blocked'},{status:'needs_reconciliation'}]){
    const calls=[];
    await runNativePlaywrightDriver({runDirectory:'C:/run',python:'python',controlAgent:options.controlAgent},{
      connect:async()=>({session:{},closeConnection:async()=>{}}),
      command:async args=>{if(args[0]==='check-control-registry')return {status:'registry_matched'};calls.push(args[0]);return {status:options.status,pending_kind:null};}});
    assert.deepEqual(calls,['status']);
  }
});
test('a stopped module with no control exception skips diagnostics and advances',async()=>{
 const calls=[];
 const statuses=[{status:'module_blocked',pending_kind:null,program_inventory:true,control_exception_count:0},
  {status:'incomplete_coverage',pending_kind:null,program_inventory:true}];
 const result=await runNativePlaywrightDriver({runDirectory:'C:/run',python:'python'},{
  connect:async()=>({session:{},closeConnection:async()=>{}}),
  command:async args=>{if(args[0]==='check-control-registry')return {status:'registry_matched'};calls.push(args[0]);return statuses.shift();}});
 assert.equal(result.status,'incomplete_coverage');assert.deepEqual(calls,['status','defer-independent']);
});

test('standalone native driver loops graph requests on one CDP session', async () => {
  const calls=[];
  const statuses=[
    {status:'awaiting_observation',pending_kind:'observe',run_dir:'C:/run/attempt'},
    {status:'awaiting_edge',pending_kind:'fill',run_dir:'C:/run/attempt'},
    {status:'complete',pending_kind:null,run_dir:'C:/run/attempt'}
  ];
  const connection={session:{backend:'playwright'},closeConnection:async()=>calls.push('close')};
  const result=await runNativePlaywrightDriver({projectDirectory:'C:/project',runDirectory:'C:/run',manifest:'C:/manifest.json',
    priorRoot:'C:/prior',start:true,allowSave:true,python:'python',agentTuning:'off'}, {
      connect:async options=>{calls.push(['connect',options]);return connection;},
      command:async args=>{if(args[0]==='check-control-registry')return {status:'registry_matched'};calls.push(['command',...args]);return statuses.shift();},
      executeRequest:async(session,dir)=>calls.push(['request',session,dir])
    });
  assert.equal(result.status,'complete');
  assert.equal(calls.filter(call=>Array.isArray(call)&&call[0]==='request').length,2);
  assert.ok(calls.some(call=>Array.isArray(call)&&call.includes('--allow-save')));
  assert.ok(calls.some(call=>Array.isArray(call)&&call.includes('--agent-tuning')&&call.includes('off')));
  assert.equal(calls.at(-1),'close');
});

test('resume cannot silently override the stored tuning policy',async()=>{
  await assert.rejects(runNativePlaywrightDriver({start:false,agentTuning:'off'}, {
    connect:async()=>{throw new Error('must not connect');}
  }),/agent_tuning_is_persisted_at_start/);
});

test('standalone native driver disconnects after command failure', async () => {
  let closed=false;
  await assert.rejects(runNativePlaywrightDriver({projectDirectory:'C:/project',runDirectory:'C:/run',start:false,python:'python'}, {
    connect:async()=>({session:{},closeConnection:async()=>{closed=true;}}),
    command:async()=>{throw new Error('failed');}
  }),/failed/);
  assert.equal(closed,true);
});

test('standalone native driver opens a collapsed card and continues automatically', async () => {
  const calls=[];
  const statuses=[
    {status:'module_edit_not_ready',pending_kind:null,run_dir:'C:/run/attempt'},
    {status:'awaiting_module_edit',pending_kind:'edit_module',run_dir:'C:/run/attempt'},
    {status:'awaiting_observation',pending_kind:'observe',run_dir:'C:/run/attempt'},
    {status:'complete',pending_kind:null,run_dir:'C:/run/attempt'}
  ];
  const result=await runNativePlaywrightDriver({projectDirectory:'C:/project',runDirectory:'C:/run/attempt',start:false,python:'python'}, {
    connect:async()=>({session:{},closeConnection:async()=>{}}),
    command:async args=>{if(args[0]==='check-control-registry')return {status:'registry_matched'};calls.push(args);return statuses.shift();},
    executeRequest:async()=>calls.push(['request'])
  });
  assert.equal(result.status,'complete');
  assert.ok(calls.some(args=>args[0]==='open-module'));
  assert.equal(calls.filter(args=>args[0]==='request').length,2);
});
