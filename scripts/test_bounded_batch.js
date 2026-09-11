/* Synthetic adapters only: no browser, filesystem writes, or external actions. */
const assert = require("node:assert/strict");
const {runBoundedBatch} = require("./bounded_batch.js");

function fixture() {
  const operations = ["A", "B", "C", "D"].map((id, i) => ({id, action: "fill", label: id,
    before: "", value: id, kind: "text", family: i < 3 ? "plain" : "radio"}));
  const plan = {runId: "synthetic", execution: {roundStartedAt: Date.now(),
    authorization: {fill:true,basis:'user requested fill'},
    driver:{mode:'direct-tool',name:'synthetic'}, target:{browser:'test',url:'https://example.test/form'},
    probe:{observedAt:Date.now()}}, operations};
  const preflight = {schemaVersion:2, ready: true, runId: plan.runId, dispatchIds:operations.map(o=>o.id), checkedPlan: JSON.parse(JSON.stringify(plan))};
  const state = {runId: plan.runId, status: "prepared", results: {}};
  const values = {}, calls = [], checkpoints = [];
  const adapter = {
    async identify() { return {...plan.execution.target}; },
    async inspect(op) { return {count: 1, kind: op.kind, value: values[op.id] || "", anchorMatched: true}; },
    async write(op) { calls.push(op.id); values[op.id] = op.value; return {status: "written"}; },
    async checkpoint(s) { checkpoints.push(s); }
  };
  return {plan, preflight, state, adapter, calls, values, checkpoints};
}

(async () => {
  let tests = 0;
  async function test(name, fn) { await fn(); tests++; process.stdout.write("PASS " + name + "\n"); }
  await test("successful readback and reserve before writing", async () => {
    const f = fixture();
    const s = await runBoundedBatch(f.plan, f.preflight, f.adapter, f.state);
    assert.equal(s.results.A.status, "written");
    assert.equal(s.results.A.evidence, "page");
    assert(f.checkpoints.some(s => s.pendingWrite === "A" && s.results.A.status === "attempted"));
    await runBoundedBatch(f.plan, f.preflight, f.adapter, structuredClone(s));
    assert.equal(f.calls.length, 4); // A restored successful run only reads; it never rewrites.
  });
  await test("changed plan rejected before writing", async () => {
    const f = fixture(); f.plan.operations[0].value = "changed";
    await assert.rejects(() => runBoundedBatch(f.plan, f.preflight, f.adapter, f.state));
    assert.equal(f.calls.length, 0);
  });
  await test("old value changed is preserved", async () => {
    const f = fixture(); f.values.A = "user edit";
    const s = await runBoundedBatch(f.plan, f.preflight, f.adapter, f.state);
    assert.equal(s.results.A.status, "kept"); assert(!f.calls.includes("A"));
  });
  await test("two mapping failures stop family but allow independent target", async () => {
    const f = fixture(); const inspect = f.adapter.inspect;
    f.adapter.inspect = async op => op.family === "plain" ? {count: 2} : inspect(op);
    const s = await runBoundedBatch(f.plan, f.preflight, f.adapter, f.state);
    assert.equal(s.results.C.reason, "family-stopped"); assert.deepEqual(f.calls, ["D"]);
  });
  await test("wrong control kind never receives text", async () => {
    const f = fixture(); const inspect = f.adapter.inspect;
    f.adapter.inspect = async op => op.id === "A" ? {count: 1, kind: "date", value: ""} : inspect(op);
    await runBoundedBatch(f.plan, f.preflight, f.adapter, f.state);
    assert(!f.calls.includes("A"));
  });
  await test("uncertain write stops all later writes", async () => {
    const f = fixture();
    f.adapter.write = async op => { f.calls.push(op.id); throw new Error("timeout"); };
    const s = await runBoundedBatch(f.plan, f.preflight, f.adapter, f.state);
    assert.deepEqual(f.calls, ["A"]); assert.equal(s.pendingWrite, "A");
    assert.equal(s.results.B.status, "not-attempted");
  });
  await test("budget includes read latency and does not dispatch late write", async () => {
    const f = fixture(); let time = 0;
    f.plan.execution.roundStartedAt = 0;
    f.plan.execution.probe.observedAt = 0;
    f.preflight.checkedPlan = JSON.parse(JSON.stringify(f.plan));
    f.adapter.inspect = async op => { time += 1800001; return {count: 1, kind: op.kind, value: ""}; };
    const s = await runBoundedBatch(f.plan, f.preflight, f.adapter, f.state, () => time);
    assert.equal(s.reason, "budget-exhausted"); assert.equal(f.calls.length, 0);
  });
  await test("preparation consumes round budget and remaining timeout is passed down", async () => {
    const f = fixture(); let time = 1799990;
    f.plan.execution.roundStartedAt = 0;
    f.plan.execution.probe.observedAt = 1799990;
    f.preflight.checkedPlan = JSON.parse(JSON.stringify(f.plan));
    const inspect = f.adapter.inspect;
    f.adapter.inspect = async (op, options) => {
      assert.equal(options.timeoutMs, 10);
      time += 11;
      return inspect(op);
    };
    const s = await runBoundedBatch(f.plan, f.preflight, f.adapter, f.state, () => time);
    assert.equal(s.reason, "budget-exhausted"); assert.equal(f.calls.length, 0);
  });
  await test("default round can continue beyond five minutes", async () => {
    const f = fixture();
    f.plan.execution.roundStartedAt = 0;
    f.plan.execution.probe.observedAt = 600000;
    f.preflight.checkedPlan = JSON.parse(JSON.stringify(f.plan));
    const s = await runBoundedBatch(f.plan, f.preflight, f.adapter, f.state, () => 600000);
    assert.equal(s.status, "completed"); assert.equal(f.calls.length, 4);
  });
  await test("checkpoint failure prevents writes", async () => {
    const f = fixture(); f.adapter.checkpoint = async () => { throw new Error("disk unavailable"); };
    const s = await runBoundedBatch(f.plan, f.preflight, f.adapter, f.state);
    assert.equal(s.reason, "state-write-failed"); assert.equal(f.calls.length, 0);
  });
  await test("manual fields skipped and readback mismatch not retried", async () => {
    const f = fixture(); f.plan.operations[0].action = "manual";
    f.preflight.checkedPlan = JSON.parse(JSON.stringify(f.plan));
    f.adapter.write = async op => { f.calls.push(op.id); return {status: "written"}; };
    const s = await runBoundedBatch(f.plan, f.preflight, f.adapter, f.state);
    assert(!f.calls.includes("A")); assert.equal(s.results.B.reason, "readback-mismatch");
    assert.equal(f.calls.filter(x => x === "B").length, 1);
  });
  await test("unconfirmed record creation blocks dependent fields", async () => {
    const f = fixture();
    f.plan.operations[1].dependsOn = "A";
    f.plan.operations[0].action = "add-record";
    f.plan.operations[0].kind = "record";
    f.preflight.checkedPlan = JSON.parse(JSON.stringify(f.plan));
    // Existing record means creation's expected count=0 no longer matches.
    const s = await runBoundedBatch(f.plan, f.preflight, f.adapter, f.state);
    assert.equal(s.results.B.reason, "dependency-pending"); assert(!f.calls.includes("B"));
  });
  await test('settled read failure does not block independent fields', async () => {
    const f=fixture(), inspect=f.adapter.inspect;
    f.adapter.inspect=async op=>{if(op.id==='A')throw Object.assign(new TypeError('unsupported method'),{settled:true});return inspect(op);};
    const s=await runBoundedBatch(f.plan,f.preflight,f.adapter,f.state);
    assert.equal(s.results.A.status,'read-failed'); assert.deepEqual(f.calls,['B','C','D']);
    f.adapter.inspect=inspect;
    f.plan.operations[0].locator='corrected observed selector';
    f.preflight.checkedPlan=structuredClone(f.plan);
    await runBoundedBatch(f.plan,f.preflight,f.adapter,structuredClone(s),Date.now,{retryReadFailures:true});
    assert.deepEqual(f.calls,['B','C','D','A']);
  });
  await test('unsettled read call still blocks writes', async () => {
    const f=fixture(); f.adapter.inspect=async()=>{throw new Error('host timed out');};
    const s=await runBoundedBatch(f.plan,f.preflight,f.adapter,f.state);
    assert.equal(s.reason,'read-call-unsettled'); assert.equal(f.calls.length,0);
  });
  await test('batch boundary resumes from checkpoint without duplicates', async () => {
    const f=fixture();
    const s=await runBoundedBatch(f.plan,f.preflight,f.adapter,f.state,Date.now,{maxOperations:2});
    assert.equal(s.status,'paused'); assert.deepEqual(f.calls,['A','B']);
    await runBoundedBatch(f.plan,f.preflight,f.adapter,structuredClone(s));
    assert.deepEqual(f.calls,['A','B','C','D']);
  });
  await test('resume preserves intervening user edits', async () => {
    const f=fixture(); const s=await runBoundedBatch(f.plan,f.preflight,f.adapter,f.state,Date.now,{maxOperations:1});
    f.values.A='user changed it';
    const resumed=await runBoundedBatch(f.plan,f.preflight,f.adapter,structuredClone(s));
    assert.equal(resumed.results.A.status,'conflict'); assert.equal(f.values.A,'user changed it');
    assert.equal(f.calls.filter(id=>id==='A').length,1);
  });
  await test('unknown write is reconciled by real value without replay', async () => {
    const f=fixture(), write=f.adapter.write;
    f.adapter.write=async op=>{await write(op);return {status:'unknown',settled:true};};
    const s=await runBoundedBatch(f.plan,f.preflight,f.adapter,f.state);
    assert.equal(s.pendingWrite,'A'); f.adapter.write=write;
    const resumed=await runBoundedBatch(f.plan,f.preflight,f.adapter,structuredClone(s));
    assert.equal(resumed.pendingWrite,null); assert.deepEqual(f.calls,['A','B','C','D']);
  });
  await test('unresolved write is not retried on resume', async () => {
    const f=fixture();f.adapter.write=async op=>{f.calls.push(op.id);return {status:'unknown',settled:true};};
    const s=await runBoundedBatch(f.plan,f.preflight,f.adapter,f.state);
    const resumed=await runBoundedBatch(f.plan,f.preflight,f.adapter,structuredClone(s));
    assert.equal(resumed.reason,'write-uncertain'); assert.deepEqual(f.calls,['A']);
  });
  await test('partial new record resumes the exact draft without another add', async () => {
    const f=fixture(); f.plan.operations[0].action='add-record'; f.plan.operations[0].kind='record';
    f.preflight.checkedPlan=structuredClone(f.plan);
    let created=false,completed=0;const inspect=f.adapter.inspect,write=f.adapter.write;
    f.adapter.inspect=async op=>op.id!=='A'?inspect(op):
      ({count:created?1:0,kind:'record',value:f.values.A||'',anchorMatched:!created||!!f.values.A,recordRef:created?'draft-1':null,owned:created});
    f.adapter.write=async op=>{if(op.id!=='A')return write(op);f.calls.push('ADD');created=true;return {status:'partial',recordRef:'draft-1'};};
    f.adapter.completeRecord=async(op,opts)=>{assert.equal(opts.recordRef,'draft-1');completed++;f.values.A='A';return {status:'written'};};
    const s=await runBoundedBatch(f.plan,f.preflight,f.adapter,f.state);
    assert.equal(s.reason,'partial-record');
    await runBoundedBatch(f.plan,f.preflight,f.adapter,structuredClone(s));
    assert.equal(completed,1);assert.equal(f.calls.filter(id=>id==='ADD').length,1);
  });
  await test('existing named record satisfies dependent fields without adding', async () => {
    const f=fixture(); f.plan.operations[0].action='add-record';f.plan.operations[0].kind='record';
    f.plan.operations[1].dependsOn='A';f.values.A='A';f.preflight.checkedPlan=structuredClone(f.plan);
    const s=await runBoundedBatch(f.plan,f.preflight,f.adapter,f.state);
    assert.equal(s.results.A.status,'written');assert(!f.calls.includes('A'));assert(f.calls.includes('B'));
  });
  await test('completed modules save separately once using existing authorization', async () => {
    const f=fixture();f.plan.execution.authorization.save=true;
    f.plan.modules=[{id:'education',operationIds:['A','B']},{id:'projects',operationIds:['C','D']}];
    f.preflight.checkedPlan=structuredClone(f.plan);const saves=[];
    f.adapter.inspectModule=async()=>({canSave:true});
    f.adapter.saveModule=async(m,opts)=>{assert(opts.timeoutMs<=180000);assert(opts.timeoutMs>120000);saves.push(m.id);return {status:'saved'};};
    f.adapter.verifyModule=async m=>({saved:saves.includes(m.id),evidence:{callId:'read-'+m.id,indicator:'saved card'}});
    const s=await runBoundedBatch(f.plan,f.preflight,f.adapter,f.state);
    assert.deepEqual(saves,['education','projects']);
    await runBoundedBatch(f.plan,f.preflight,f.adapter,structuredClone(s));
    assert.equal(saves.length,2);
  });
  await test('missing required fields block only their module save', async () => {
    const f=fixture();f.plan.execution.authorization.save=true;
    f.plan.modules=[{id:'blocked',operationIds:['A']},{id:'ready',operationIds:['B','C','D']}];
    f.preflight.checkedPlan=structuredClone(f.plan);const saves=[];
    f.adapter.inspectModule=async m=>({canSave:m.id==='ready',missingRequired:m.id==='ready'?[]:['start date']});
    f.adapter.saveModule=async m=>{saves.push(m.id);return {status:'saved'};};
    f.adapter.verifyModule=async()=>({saved:true,evidence:'saved card'});
    const s=await runBoundedBatch(f.plan,f.preflight,f.adapter,f.state);
    assert.equal(s.modules.blocked.status,'blocked');assert.deepEqual(saves,['ready']);
  });
  await test('no save authorization means no save calls', async () => {
    const f=fixture();f.plan.modules=[{id:'education',operationIds:['A']}];f.preflight.checkedPlan=structuredClone(f.plan);
    f.adapter.saveModule=async()=>{throw new Error('must not be called');};
    const s=await runBoundedBatch(f.plan,f.preflight,f.adapter,f.state);assert.deepEqual(s.modules,{});
  });
  await test('unknown save blocks replay and downstream saves', async () => {
    const f=fixture();f.plan.execution.authorization.save=true;f.plan.modules=[{id:'one',operationIds:['A']},{id:'two',operationIds:['B']}];
    f.preflight.checkedPlan=structuredClone(f.plan);const saves=[];
    f.adapter.inspectModule=async()=>({canSave:true});f.adapter.saveModule=async m=>{saves.push(m.id);return {status:'unknown'};};
    f.adapter.verifyModule=async()=>({saved:false});
    const s=await runBoundedBatch(f.plan,f.preflight,f.adapter,f.state);
    await runBoundedBatch(f.plan,f.preflight,f.adapter,structuredClone(s));
    assert.deepEqual(saves,['one']);assert.equal(s.pendingSave,'one');
  });
  await test('wrong page and stale probes prevent dispatch', async () => {
    const f=fixture();f.adapter.identify=async()=>({browser:'test',url:'https://example.test/other'});
    const s=await runBoundedBatch(f.plan,f.preflight,f.adapter,f.state);assert.equal(s.reason,'target-changed');assert.equal(f.calls.length,0);
    const g=fixture();g.plan.execution.probe.observedAt=0;g.preflight.checkedPlan=structuredClone(g.plan);
    await assert.rejects(()=>runBoundedBatch(g.plan,g.preflight,g.adapter,g.state),/probe/);
  });
  await test('loaded adapter code must match checked code', async () => {
    const f=fixture();f.plan.execution.driver.mode='adapter';f.preflight.checkedBatch='code-v1';f.adapter.source='code-v2';f.preflight.checkedPlan=structuredClone(f.plan);
    await assert.rejects(()=>runBoundedBatch(f.plan,f.preflight,f.adapter,f.state),/source/);
    assert.equal(f.calls.length,0);
  });
  await test('unsettled remote call prevents even a new page read on resume', async () => {
    const f=fixture();let reads=0;
    f.adapter.inspect=async()=>{reads++;throw new Error('remote call still running');};
    const s=await runBoundedBatch(f.plan,f.preflight,f.adapter,f.state);
    const resumed=await runBoundedBatch(f.plan,f.preflight,f.adapter,structuredClone(s));
    assert.equal(resumed.reason,'remote-call-unsettled');assert.equal(reads,1);
  });
  await test('uncertain completion of a partial record is never blindly replayed', async () => {
    const f=fixture(); f.plan.operations[0].action='add-record';f.plan.operations[0].kind='record';f.preflight.checkedPlan=structuredClone(f.plan);
    let created=false,completions=0;
    f.adapter.inspect=async()=>({count:created?1:0,kind:'record',value:'',anchorMatched:!created,recordRef:created?'draft-1':null,owned:created});
    f.adapter.write=async()=>{created=true;return {status:'partial',recordRef:'draft-1'};};
    f.adapter.completeRecord=async()=>{completions++;return {status:'unknown',settled:true};};
    let s=await runBoundedBatch(f.plan,f.preflight,f.adapter,f.state);
    s=await runBoundedBatch(f.plan,f.preflight,f.adapter,structuredClone(s));
    s=await runBoundedBatch(f.plan,f.preflight,f.adapter,structuredClone(s));
    assert.equal(completions,1);assert.equal(s.reason,'write-uncertain');
  });
  await test('confirmed settlement permits reconciliation without repeating a write', async () => {
    const f=fixture(),write=f.adapter.write;
    f.adapter.write=async op=>{await write(op);throw new Error('host lost result');};
    let s=await runBoundedBatch(f.plan,f.preflight,f.adapter,f.state);
    f.adapter.write=write;f.adapter.confirmSettled=async pending=>({settled:true,evidence:'host confirmed completed '+pending.id});
    s=await runBoundedBatch(f.plan,f.preflight,f.adapter,structuredClone(s));
    assert.equal(s.pendingWrite,null);assert.deepEqual(f.calls,['A','B','C','D']);
  });
  process.stdout.write(`${tests} behavioral checks passed\n`);
})().catch(error => { process.stderr.write(error.stack + "\n"); process.exitCode = 1; });
