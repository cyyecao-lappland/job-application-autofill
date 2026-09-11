/* Real local execution entry, for hosts that actually support loading adapters.
 * Direct browser-tool mode must not pretend to use this entry. */
const fs = require('node:fs');
const path = require('node:path');
const {runBoundedBatch} = require('./bounded_batch.js');

function atomicJson(filename, value) {
  const temporary = filename + '.' + process.pid + '.tmp';
  let descriptor;
  try {
    descriptor = fs.openSync(temporary, 'wx');
    fs.writeFileSync(descriptor, JSON.stringify(value, null, 2) + '\n', 'utf8');
    fs.fsyncSync(descriptor);
    fs.closeSync(descriptor); descriptor = null;
    fs.renameSync(temporary, filename);
  } finally {
    if (descriptor != null) fs.closeSync(descriptor);
    if (fs.existsSync(temporary)) fs.unlinkSync(temporary);
  }
}

async function executeAdapter({runDirectory, host, now = Date.now, options = {}}) {
  const root = path.resolve(runDirectory);
  const lockPath = path.join(root, 'execution-state.lock');
  const lock = fs.openSync(lockPath, 'wx');
  try {
    const read = name => JSON.parse(fs.readFileSync(path.join(root, name), 'utf8').replace(/^\uFEFF/, ''));
    const plan = read('alignment-plan.json');
    const preflight = read('preflight.json');
    if (preflight.schemaVersion !== 2 || !preflight.ready ||
        JSON.stringify(plan) !== JSON.stringify(preflight.checkedPlan) ||
        plan.execution?.driver?.mode !== 'adapter') throw new Error('Current adapter-mode preflight required');
    const filename = path.join(root, 'batch.js');
    const source = fs.readFileSync(filename, 'utf8').replace(/^\uFEFF/, '');
    if (source !== preflight.checkedBatch) throw new Error('Adapter changed after preflight');
    // Only load the declared, user-authorized local module at execution time.
    // Factories must be side-effect free until their inspect/write methods run.
    delete require.cache[require.resolve(filename)];
    const exported = require(filename);
    const factory = exported[plan.execution.driver.entryPoint];
    if (typeof factory !== 'function') throw new Error('Declared adapter entry point is not callable');
    const adapter = await factory(host);
    if (!adapter || typeof adapter !== 'object') throw new Error('Adapter factory did not return an adapter');
    adapter.source = source;
    // Override any model-provided in-memory/no-op checkpoint with real storage.
    adapter.checkpoint = async state => atomicJson(path.join(root, 'execution-state.json'), state);
    const statePath = path.join(root, 'execution-state.json');
    const state = fs.existsSync(statePath) ? read('execution-state.json')
      : {runId: plan.runId, status: 'prepared', results: {}};
    return await runBoundedBatch(plan, preflight, adapter, state, now, options);
  } finally {
    fs.closeSync(lock);
    fs.unlinkSync(lockPath);
  }
}

async function probeAdapter({runDirectory, host, operationIds, now = Date.now}) {
  const root=path.resolve(runDirectory);
  const plan=JSON.parse(fs.readFileSync(path.join(root,'alignment-plan.json'),'utf8').replace(/^\uFEFF/,''));
  if(plan.execution?.driver?.mode!=='adapter') throw new Error('Adapter mode required');
  const filename=path.join(root,'batch.js');
  delete require.cache[require.resolve(filename)];
  const factory=require(filename)[plan.execution.driver.entryPoint];
  if(typeof factory!=='function') throw new Error('Declared adapter entry point is not callable');
  const adapter=await factory(host);
  if(!adapter || typeof adapter.identify!=='function' || typeof adapter.inspect!=='function') {
    throw new Error('Adapter needs real identify and inspect methods');
  }
  const operations=[...(plan.operations||[]),...(plan.records||[]).flatMap(r=>
    r.operations.map(o=>({...o,anchor:{label:r.anchorLabel,value:r.name}})))];
  const ids=operationIds||plan.execution.dispatchIds||operations.filter(o=>['fill','add-record'].includes(o.action)).map(o=>o.id);
  const chosen=ids.map(id=>operations.find(o=>o.id===id));
  if(chosen.some(o=>!o)||new Set(ids).size!==ids.length) throw new Error('Invalid probe targets');
  const deadline=Math.min(now()+(plan.execution.operationTimeoutMs??120000),
    plan.execution.roundStartedAt+(plan.execution.budgetMs??1800000));
  const timeout=()=>{const remaining=deadline-now();if(remaining<=0)throw new Error('Probe budget exhausted');return remaining;};
  const target=await adapter.identify({timeoutMs:timeout()});
  if(!target || !Object.entries(plan.execution.target||{}).every(([k,v])=>target[k]===v)) throw new Error('Probe target changed');
  const rows=[];
  for(const op of chosen) {
    const observed=await adapter.inspect(op,{timeoutMs:timeout()});
    rows.push({id:op.id,...observed});
  }
  timeout();
  // The outer real tool call id is added from its returned result, never invented here.
  return {target:plan.execution.target,observedAt:now(),settled:true,
    entryPoint:plan.execution.driver.entryPoint,operations:rows};
}

module.exports = {executeAdapter, probeAdapter};
