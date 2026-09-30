'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const {createDirectRuntime}=require('./direct_batch');
const {beginCall,finishCall,summary}=require('./direct_metrics');
const {executeBatch,nextAction,signature}=createDirectRuntime();
function fixture() {
  const target={browser:'test',tabId:'1',url:'https://fixture/form'};
  const operations=['A','B','C'].map(id=>({id,action:'fill',kind:'text',before:'',value:id,source:'fixture',label:id,
    locator:{selector:'#'+id},methodEvidence:'observed',family:'native',path:'dom'}));
  const execution={target,driver:{name:'fixture',mode:'direct-tool',channel:'browser'},authorization:{fill:true},
    roundStartedAt:100,dispatchIds:['A','B','C']};
  execution.probe={target,driver:execution.driver,callId:'probe',settled:true,observedAt:100};
  const plan={execution,operations};
  const report={ready:true,batchReady:true,checkedPlan:structuredClone(plan),dispatchIds:['A','B','C']};
  const values={A:'',B:'',C:''};
  const shape={readStatus:'known',route:target.url,fields:operations.map(o=>({key:o.id,kind:'text',visible:true})),records:[],sections:[]};
  const capabilities=Object.fromEntries(operations.map(o=>[o.id,{verified:true,structureStable:true,ready:true}]));
  let writes=0;
  const host={capabilities,identify:async()=>target,structure:async()=>shape,
    inspect:async op=>({count:1,kind:'text',readStatus:'known',value:values[op.id]}),
    write:async op=>{writes++;values[op.id]=op.value;return {status:'written',settled:true};}};
  return {plan,report,values,shape,host,capabilities,state:{results:{}},writes:()=>writes,
    run(limits={},state={results:{}}){return executeBatch(plan,report,host,state,limits,()=>100);}};
}
test('one invocation fills several fields and tolerates validation decoration',async()=>{
  const f=fixture(),write=f.host.write;
  f.host.write=async op=>{f.shape.validationIcon='green';return write(op);};
  const r=await f.run();assert.deepEqual(r.written,['A','B','C']);assert.equal(r.reason,'complete');
});
test('conditional field and added record stop after verified write',async()=>{
  for (const change of [s=>s.fields.push({key:'conditional',kind:'text'}),s=>s.records.push({id:'new',sectionId:'education'})]) {
    const f=fixture(),write=f.host.write;f.host.write=async op=>{const r=await write(op);change(f.shape);return r;};
    const r=await f.run();assert.deepEqual(r.written,['A']);assert.equal(r.structureChanged,true);assert.equal(f.writes(),1);
  }
});
test('unknown write stops and does not replay',async()=>{
  const f=fixture();f.host.write=async()=>{throw new Error('timeout');};
  const r=await f.run();assert.deepEqual(r.unknown,['A']);assert.deepEqual(r.unattempted,['B','C']);
  await assert.rejects(f.run({}, {pendingCall:{id:'old'}}),/unresolved/);
});
test('user conflict is preserved while independent fields continue',async()=>{
  const f=fixture();f.values.A='user';const r=await f.run();
  assert.deepEqual(r.conflict,['A']);assert.equal(f.values.A,'user');assert.deepEqual(r.written,['B','C']);
});
test('unknown or changed kind never writes',async()=>{
  const f=fixture();f.host.inspect=async()=>({count:1,kind:'select',readStatus:'unknown'});
  assert.equal((await f.run()).reason,'locator-or-record-changed');assert.equal(f.writes(),0);
});
test('operation and elapsed limits are bounded',async()=>{
  const f=fixture();assert.equal((await f.run({maxOperations:1})).written.length,1);
  let n=99;const g=fixture();const r=await executeBatch(g.plan,g.report,g.host,g.state,{maxBatchTime:3},()=>++n);
  assert.equal(g.writes(),0);assert.ok(r.unattempted.length);
});
test('modified plan or stale probe cannot reuse report',async()=>{
  const f=fixture();f.plan.operations[0].value='changed';await assert.rejects(f.run(),/preflight/);
  const g=fixture();await assert.rejects(executeBatch(g.plan,g.report,g.host,g.state,{},()=>400000),/stale/);
});
test('scheduler picks unlocking slow and reconsiders deferred capability',()=>{
  const f=fixture();f.plan.operations=[{id:'date',action:'fill'}, {id:'add',action:'add-record'},
    ...Array.from({length:10},(_,i)=>({id:'child'+i,action:'fill',dependsOn:'add'}))];
  assert.equal(nextAction(f.plan,f.state,{date:{ready:true},add:{ready:true}}).dispatchIds[0],'add');
  const g=fixture();assert.equal(nextAction(g.plan,g.state,{}).type,'deferred');
  assert.equal(nextAction(g.plan,g.state,g.capabilities).type,'fast');
});
test('module save needs authorization and revision-bound independent review',()=>{
  const f=fixture();f.plan.modules=[{id:'education',operationIds:['A']}];
  f.state.results.A={status:'written'};f.state.modules={education:{reviewPassed:true,reviewEvidence:'review-call',
    requiredSatisfied:true,revision:'v2',reviewedRevision:'v2'}};
  assert.notEqual(nextAction(f.plan,f.state,f.capabilities).type,'save');
  f.plan.execution.authorization.save=true;
  assert.equal(nextAction(f.plan,f.state,f.capabilities).type,'save');
  f.state.modules.education.reviewedRevision='v1';assert.notEqual(nextAction(f.plan,f.state,f.capabilities).type,'save');
});
test('signature ignores decoration but detects disabled controls',()=>{
  const f=fixture(),s=signature(f.shape);f.shape.counter=1;assert.equal(signature(f.shape),s);
  f.shape.fields[0].enabled=false;assert.notEqual(signature(f.shape),s);
});
test('metrics receipts guard pending and count distinct completed fields',async()=>{
  const f=fixture(),state={};beginCall(state,'call1',['A','B','C'],()=>90);
  assert.throws(()=>beginCall(state,'call2',[]),/unresolved/);
  const r=await f.run();finishCall(state,'call1',{callId:'actual-tool-id',settled:true},r,()=>110);
  assert.equal(summary(state,()=>110).successful_writes,3);assert.equal(summary(state).ttffMs,10);
  beginCall(state,'call2',['A'],()=>120);
  finishCall(state,'call2',{callId:'actual2',settled:true},{results:{},unknown:['A']},()=>130);
  assert.ok(state.pendingCall);
});
test('registered current call can run once; an old call cannot',async()=>{
  const f=fixture();beginCall(f.state,'new',['A','B','C'],()=>90);
  const r=await f.run({callKey:'new'},f.state);assert.equal(r.written.length,3);
  await assert.rejects(f.run({callKey:'new'},f.state),/unresolved/);
  finishCall(f.state,'new',{callId:'receipt',settled:true},r,()=>110);assert.equal(f.state.pendingCall,null);
});
test('unsettled read survives outer receipt and blocks scheduling',async()=>{
  const f=fixture();f.host.inspect=async()=>{throw Object.assign(new Error('timeout'),{settled:false,callId:'inner'});};
  beginCall(f.state,'new',['A','B','C'],()=>90);
  const r=await f.run({callKey:'new'},f.state);assert.equal(r.pendingRemote.callId,'inner');
  finishCall(f.state,'new',{callId:'receipt',settled:true},r,()=>110);
  assert.equal(nextAction(f.plan,f.state,f.capabilities).type,'blocked');
});
test('structure-read failure preserves confirmed written classification',async()=>{
  const f=fixture();let n=0;f.host.structure=async()=>{if(++n===3)throw new Error('read failure');return f.shape;};
  const r=await f.run();assert.deepEqual(r.written,['A']);assert.deepEqual(r.unknown,[]);
  assert.equal(r.results.A.status,'written');assert.ok(r.pendingRemote);
});
test('hung read returns within bounded call and remains pending',async()=>{
  const f=fixture();f.host.inspect=()=>new Promise(()=>{});
  const r=await f.run({maxBatchTime:10});assert.ok(r.pendingRemote);assert.equal(f.writes(),0);
});
test('field dependency is rechecked before child dispatch',async()=>{
  const f=fixture();f.plan.operations[1].dependsOn='A';f.report.checkedPlan=structuredClone(f.plan);
  const r=await f.run();assert.deepEqual(r.written,['A','B','C']);
  const g=fixture();g.plan.operations[1].dependsOn='A';g.report.checkedPlan=structuredClone(g.plan);
  g.values.A='user';const blocked=await g.run();assert.equal(blocked.reason,'dependency-changed');
  assert.equal(g.values.B,'');
});
