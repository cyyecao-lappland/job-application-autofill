'use strict';

// Self-contained: paste this factory into a supported browser JavaScript tool,
// or require it in a host that explicitly supports local module loading.
function createDirectRuntime() {
  const same = (a, b) => JSON.stringify(a) === JSON.stringify(b);
  const flatten = plan => [...(plan.operations || []), ...(plan.records || []).flatMap(r =>
    r.operations.map(o => ({...o,anchor:{label:r.anchorLabel,value:r.name}})))];
  const done = r => ['written', 'alreadyMatched'].includes(r?.status);
  const complex = new Set(['date', 'cascade', 'search', 'multi', 'editor', 'file', 'captcha']);
  const sensitive = /证件号码|证件号|身份证|护照|身份号码|national.?id|passport|social.?security/i;
  function signature(s) {
    if (!s || s.readStatus !== 'known') throw Object.assign(new Error('structure-unavailable'),{settled:true});
    // Compare semantic projections directly, without hashes or DOM mutation counts.
    const sorted = xs => [...xs].sort((a,b) => JSON.stringify(a).localeCompare(JSON.stringify(b)));
    return JSON.stringify({route:s.route, step:s.step, modal:s.modal,
      sections:sorted((s.sections || []).map(x => [x.id,x.expanded,x.loaded])),
      records:sorted((s.records || []).map(x => [x.id,x.sectionId])),
      fields:sorted((s.fields || []).map(x => [x.key,x.recordId,x.kind,x.visible,x.required,x.enabled]))});
  }
  function capable(op, capability) {
    return op.action === 'fill' && capability?.verified === true &&
      capability?.structureStable === true && !!op.locator && !!op.methodEvidence &&
      (!complex.has(op.kind) || !!op.savedMethodEvidence);
  }
  function nextAction(plan, state, capabilities) {
    if (state.pendingCall || state.pendingWrite || state.pendingSave)
      return {type:'blocked', reason:'unresolved-operation'};
    const operations = flatten(plan), results = state.results || {};
    const ready = op => capabilities[op.id]?.ready === true &&
      (!op.dependsOn || (done(results[op.dependsOn]) &&
        (operations.find(o => o.id === op.dependsOn)?.action !== 'add-record' ||
         (results[op.dependsOn].recordRef && capabilities[op.id]?.recordRef === results[op.dependsOn].recordRef))));
    // Site modules can only save after real independent review and a fresh required-field check.
    const module = (plan.modules || []).find(m => m.operationIds.length &&
      m.operationIds.every(id => done(results[id])) && state.modules?.[m.id]?.reviewPassed === true &&
      state.modules[m.id].reviewEvidence && state.modules[m.id].requiredSatisfied === true &&
      state.modules[m.id].revision === state.modules[m.id].reviewedRevision &&
      state.modules[m.id].revision != null && state.modules[m.id].status !== 'saved' &&
      state.modules[m.id].status !== 'unknown' &&
      !(state.modules[m.id].status === 'validation-failed' &&
        state.modules[m.id].failedRevision === state.modules[m.id].revision));
    if (module && plan.execution?.authorization?.save === true) return {type:'save', moduleId:module.id};
    const pending = operations.filter(o => ['fill','add-record'].includes(o.action) && !done(results[o.id]) &&
      !['conflict','failed','manual'].includes(results[o.id]?.status));
    const fast = pending.filter(o => ready(o) && capable(o,capabilities[o.id]));
    if (fast.length) return {type:'fast', dispatchIds:fast.map(o => o.id)};
    const slow = pending.filter(ready).sort((a,b) => {
      const score = op => pending.filter(child => child.dependsOn === op.id).length * 10 +
        (capabilities[op.id]?.neededForSave ? 1 : 0);
      return score(b)-score(a);
    });
    if (slow.length) return {type:'slow', dispatchIds:[slow[0].id]};
    return {type:'deferred', deferredIds:pending.map(o => o.id)};
  }
  async function executeBatch(plan, report, host, state, limits = {}, now = Date.now) {
    const e = plan.execution || {}, start = now();
    const maxOperations = limits.maxOperations ?? 30, maxBatchTime = limits.maxBatchTime ?? 8000;
    if (!Number.isInteger(maxOperations) || maxOperations < 1 || !Number.isFinite(maxBatchTime) || maxBatchTime <= 0)
      throw new Error('invalid-limits');
    const ownCall = state.pendingCall?.kind === 'direct-batch' && state.pendingCall.phase === 'prepared' &&
      limits.callKey && state.pendingCall.callKey === limits.callKey;
    if ((state.pendingCall && !ownCall) || state.pendingWrite || state.pendingSave) throw new Error('unresolved-operation');
    if (!report.batchReady || !report.ready || !same(report.checkedPlan,plan) ||
        e.driver?.mode !== 'direct-tool' || e.driver?.channel !== 'browser' || e.authorization?.fill !== true)
      throw new Error('current-direct-preflight-required');
    if (!e.probe?.settled || !e.probe.callId || !same(e.probe.target,e.target) ||
        !same(e.probe.driver,e.driver) || start-e.probe.observedAt > 300000 || start < e.probe.observedAt)
      throw new Error('stale-probe');
    const ids = report.dispatchIds, operations = flatten(plan);
    if (!Array.isArray(ids) || !ids.length || new Set(ids).size !== ids.length ||
        !same(ids,e.dispatchIds ?? operations.filter(o => ['fill','add-record'].includes(o.action)).map(o => o.id)))
      throw new Error('dispatch-mismatch');
    const selected = ids.map(id => operations.find(o => o.id === id));
    if (selected.some(op => !op || sensitive.test(op.label) || sensitive.test(op.anchor?.label || '') ||
        !capable(op,host.capabilities[op.id]) || (host.supports && !host.supports(op)))) throw new Error('not-fast-capable');
    if (ownCall && !same(state.pendingCall.dispatchIds,ids)) throw new Error('pending-dispatch-mismatch');
    if (ownCall) state.pendingCall.phase='running';
    const deadline = Math.min(start+maxBatchTime,e.roundStartedAt+(e.budgetMs ?? 1800000));
    const remaining = () => Math.max(0,deadline-now());
    const out = {written:[],alreadyMatched:[],conflict:[],failed:[],unknown:[],unattempted:[],
      results:{}, reason:'complete', structureChanged:false, startedAt:start,firstWriteAt:null};
    const add = (bucket,op,extra={}) => {
      out[bucket].push(op.id); out.results[op.id]={status:bucket === 'written' ? 'written' : bucket,...extra};
    };
    async function invoke(method, ...args) {
      const timeoutMs=remaining();
      if (!timeoutMs) throw Object.assign(new Error('budget-exhausted'),{settled:true});
      let timer;
      // Timeout ends this batch, not the underlying host request. Keep pending until reconciled.
      try {
        return await Promise.race([
          Promise.resolve().then(() => host[method](...args,{timeoutMs,deadline})),
          new Promise((_,reject) => {timer=setTimeout(() => reject(
            Object.assign(new Error('host-timeout'),{settled:false})),timeoutMs);})
        ]);
      } finally {clearTimeout(timer);}
    }
    function readFailure(error, reason) {
      out.reason=reason;
      if (error?.settled !== true) out.pendingRemote={kind:'read',settled:false,...(error?.requestId ? {requestId:error.requestId} : {}),
        ...(error?.callId ? {callId:error.callId} : {})};
    }
    let base;
    try {
      if (!remaining()) {out.reason='time-budget'; return finish();}
      if (!same(await invoke('identify'),e.target)) throw Object.assign(new Error('target-changed'),{settled:true});
      base=signature(await invoke('structure'));
    } catch (error) {readFailure(error,'observation-unavailable'); return finish();}
    let count=0;
    for (const op of selected) {
      if (!remaining() || count >= maxOperations) {out.reason='batch-limit'; break;}
      let current;
      try {
        if (!same(await invoke('identify'),e.target) || signature(await invoke('structure')) !== base) {
          out.reason='structure-changed'; out.structureChanged=true; break;
        }
        current=await invoke('inspect',op);
      } catch (error) {readFailure(error,'read-unavailable'); break;}
      if (current.count !== 1 || current.kind !== op.kind || current.readStatus !== 'known' ||
          (op.anchor && current.anchorMatched !== true) || (op.recordRef && current.recordRef !== op.recordRef)) {
        out.reason='locator-or-record-changed'; break;
      }
      if (op.dependsOn) {
        const parent=operations.find(o => o.id === op.dependsOn);
        const dependency=out.results[op.dependsOn] || state.results?.[op.dependsOn];
        if (!done(dependency)) {out.reason='dependency-changed'; break;}
        if (parent.action === 'add-record') {
          if (!dependency.recordRef || current.recordRef !== dependency.recordRef) {
            out.reason='dependency-changed'; break;
          }
        } else {
          try {
            const observed=await invoke('inspect',parent);
            if (observed.count !== 1 || observed.readStatus !== 'known' || observed.kind !== parent.kind ||
                (parent.anchor && observed.anchorMatched !== true) || !same(observed.value,parent.value)) {
              out.reason='dependency-changed'; break;
            }
          } catch (error) {readFailure(error,'dependency-changed'); break;}
        }
      }
      count++;
      if (same(current.value,op.value)) {add('alreadyMatched',op,{recordRef:current.recordRef,evidence:'page'}); continue;}
      if (!same(current.value,op.before) || done(state.results?.[op.id])) {add('conflict',op); continue;}
      if (!remaining()) {out.reason='time-budget'; break;}
      let outcome;
      try {outcome=await invoke('write',op);}
      catch (error) {outcome={status:'unknown',settled:error?.settled === true,
        ...(error?.requestId ? {requestId:error.requestId} : {})};}
      if (outcome?.status === 'failed' && outcome.noEffect === true && outcome.settled === true) {
        add('failed',op); continue;
      }
      if (outcome?.status !== 'written' || outcome.settled !== true) {
        add('unknown',op,{settled:outcome?.settled === true,...(outcome?.requestId ? {requestId:outcome.requestId} : {})}); out.reason='unknown-write'; break;
      }
      out.firstWriteAt ??= now();
      try {
        const actual=await invoke('inspect',op);
        if (actual.readStatus !== 'known' || actual.count !== 1 || actual.kind !== op.kind ||
            (op.anchor && actual.anchorMatched !== true) || actual.recordRef !== current.recordRef) {
          add('unknown',op,{settled:true}); out.reason='readback-unavailable'; break;
        }
        add(same(actual.value,op.value) ? 'written' : 'conflict',op,{recordRef:actual.recordRef,evidence:'page'});
      } catch (error) {
        add('unknown',op,{settled:true}); readFailure(error,'readback-unavailable'); break;
      }
      try {
        if (signature(await invoke('structure')) !== base) {
          out.structureChanged=true; out.reason='structure-changed'; break;
        }
      } catch (error) {readFailure(error,'structure-unavailable'); break;}
    }
    return finish();
    function finish() {
      out.unattempted=ids.filter(id => !out.results[id]);
      out.endedAt=now(); out.durationMs=out.endedAt-start;
      return out;
    }
  }
  return {executeBatch,nextAction,signature,capable};
}
if (typeof module !== 'undefined') module.exports = {createDirectRuntime};
