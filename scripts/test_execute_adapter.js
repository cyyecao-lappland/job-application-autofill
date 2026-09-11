/* Real loader, disk persistence and process restart tests with a synthetic host.
 * No browser connections or real application data. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const os = require('node:os');
const path = require('node:path');
const {spawnSync} = require('node:child_process');
const {executeAdapter,probeAdapter} = require('./execute_adapter.js');

function fixture() {
  const root=fs.mkdtempSync(path.join(os.tmpdir(),'autofill-v2-test-'));
  const source=`module.exports.createAdapter = host => ({
    identify: async () => host.target,
    inspect: async op => ({count:1,kind:'text',value:host.values[op.id]||'',anchorMatched:true}),
    write: async op => {host.calls.push(op.id);host.values[op.id]=op.value;return {status:'written'};},
    checkpoint: async () => { throw new Error('placeholder must be replaced by actual disk storage'); }
  });\n`;
  const target={browser:'synthetic',url:'https://example.test/form'};
  const plan={runId:'disk-test',execution:{roundStartedAt:Date.now(),
    authorization:{fill:true,basis:'synthetic test'},target,
    driver:{mode:'adapter',name:'test factory',entryPoint:'createAdapter'},
    probe:{observedAt:Date.now()}},
    operations:['A','B'].map(id=>({id,action:'fill',label:id,value:id,before:'',kind:'text',family:'plain'}))};
  const preflight={schemaVersion:2,ready:true,planReady:true,runId:plan.runId,
    checkedPlan:plan,checkedBatch:source,dispatchIds:['A','B']};
  fs.writeFileSync(path.join(root,'batch.js'),source);
  fs.writeFileSync(path.join(root,'alignment-plan.json'),JSON.stringify(plan));
  fs.writeFileSync(path.join(root,'preflight.json'),JSON.stringify(preflight));
  return {root,source,plan,preflight,host:{target,values:{},calls:[]}};
}

(async()=>{
  let tests=0;
  async function test(name,fn){const f=fixture();try{await fn(f);tests++;console.log('PASS '+name);}finally{
    const resolved=path.resolve(f.root);
    assert.equal(path.dirname(resolved),path.resolve(os.tmpdir()));
    assert(path.basename(resolved).startsWith('autofill-v2-test-'));
    fs.rmSync(resolved,{recursive:true,force:true});
  }}
  await test('actual exported factory runs and state survives a fresh process',async f=>{
    const first=await executeAdapter({runDirectory:f.root,host:f.host,options:{maxOperations:1}});
    assert.equal(first.status,'paused');assert.deepEqual(f.host.calls,['A']);
    const disk=JSON.parse(fs.readFileSync(path.join(f.root,'execution-state.json'),'utf8'));
    assert.equal(disk.results.A.status,'written');
    const program=`const {executeAdapter}=require(${JSON.stringify(path.join(__dirname,'execute_adapter.js'))});
      const host=${JSON.stringify(f.host)};host.calls=[];
      executeAdapter({runDirectory:${JSON.stringify(f.root)},host}).then(s=>console.log(JSON.stringify({status:s.status,calls:host.calls}))).catch(e=>{console.error(e);process.exit(1)});`;
    const result=spawnSync(process.execPath,['-e',program],{encoding:'utf8'});
    assert.equal(result.status,0,result.stderr);assert.deepEqual(JSON.parse(result.stdout).calls,['B']);
  });
  await test('actual preflight CLI, loader and disk resume compose end to end',async f=>{
    const categories=['education','internship','project','research','award','campus','skill'];
    f.plan.jd={source:'synthetic JD',requirements:[{id:'R1',text:'test fields'}]};
    f.plan.inventory=[{id:'E1',source:'synthetic fixture'}];
    f.plan.coverage=[{id:'E1',priority:'P0',requirementIds:['R1'],decision:'include',operationIds:['A','B'],reason:'synthetic evidence'}];
    f.plan.inventoryReview=Object.fromEntries(categories.map(k=>[k,{status:'reviewed',source:'synthetic fixture'}]));
    f.plan.review={selectionReviewed:true,mappingReviewed:true};
    f.plan.operations.forEach(op=>Object.assign(op,{source:'synthetic fixture',path:'dom',locator:'synthetic '+op.id,methodEvidence:'real synthetic-host method'}));
    fs.writeFileSync(path.join(f.root,'before.json'),JSON.stringify({fields:['A','B'].map(label=>({label,value:''}))}));
    async function prepare(ids){
      f.plan.execution.dispatchIds=ids;
      fs.writeFileSync(path.join(f.root,'alignment-plan.json'),JSON.stringify(f.plan));
      f.plan.execution.probe={...await probeAdapter({runDirectory:f.root,host:f.host,operationIds:ids}),callId:'synthetic-test-tool-result'};
      fs.writeFileSync(path.join(f.root,'alignment-plan.json'),JSON.stringify(f.plan));
      const proc=spawnSync('python',['-B',path.join(__dirname,'preflight.py'),'--run',f.root],{encoding:'utf8'});
      assert.equal(proc.status,0,proc.stdout+proc.stderr);
    }
    await prepare(['A','B']);
    await executeAdapter({runDirectory:f.root,host:f.host,options:{maxOperations:1}});
    await prepare(['A','B']); // probe observes A already filled, not its original blank value
    const result=await executeAdapter({runDirectory:f.root,host:f.host});
    assert.equal(result.results.B.status,'written');assert.deepEqual(f.host.calls,['A','B']);
  });
  await test('manifest cannot masquerade as callable adapter',async f=>{
    const manifest='module.exports = {actions:["fill A"]};\n';
    fs.writeFileSync(path.join(f.root,'batch.js'),manifest);f.preflight.checkedBatch=manifest;
    fs.writeFileSync(path.join(f.root,'preflight.json'),JSON.stringify(f.preflight));
    await assert.rejects(()=>probeAdapter({runDirectory:f.root,host:f.host}),/not callable/);
    await assert.rejects(()=>executeAdapter({runDirectory:f.root,host:f.host}),/not callable/);
    assert.deepEqual(f.host.calls,[]);
  });
  await test('probe actually reads through exported adapter without writing',async f=>{
    f.host.values.A='user edit';
    const probe=await probeAdapter({runDirectory:f.root,host:f.host,operationIds:['A']});
    assert.equal(probe.operations[0].value,'user edit');assert.equal(probe.entryPoint,'createAdapter');
    assert.equal(probe.callId,undefined);assert.deepEqual(f.host.calls,[]);
    assert(!fs.existsSync(path.join(f.root,'execution-state.json')));
  });
  await test('edited code rejected before loading or writing',async f=>{
    fs.appendFileSync(path.join(f.root,'batch.js'),'\nthrow new Error("must not load");');
    await assert.rejects(()=>executeAdapter({runDirectory:f.root,host:f.host}),/changed after preflight/);
    assert.deepEqual(f.host.calls,[]);
  });
  await test('run lock prevents concurrent dispatch',async f=>{
    fs.writeFileSync(path.join(f.root,'execution-state.lock'),'occupied');
    await assert.rejects(()=>executeAdapter({runDirectory:f.root,host:f.host}),{code:'EEXIST'});
    assert.deepEqual(f.host.calls,[]);
  });
  console.log(`${tests} disk/loader checks passed`);
})().catch(e=>{console.error(e);process.exitCode=1;});
