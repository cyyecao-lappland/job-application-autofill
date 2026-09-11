/* Host-neutral execution. Only use with a real, supported adapter and durable
 * checkpoint callback. Does not connect to a browser or grant authorization. */
const clone = value => JSON.parse(JSON.stringify(value));
// 恢复时比对稳定的工作目标；探测、批次范围和定位修复可更新，事实与原预算不可偷换。
function workflow(plan) {
  const copy = clone(plan);
  delete copy.execution.probe;
  delete copy.execution.dispatchIds;
  // New user authorization can be recorded without forgetting completed work.
  delete copy.execution.authorization;
  // A repaired locator/method does not change the intended fact or record.
  for (const op of [...(copy.operations || []), ...(copy.records || []).flatMap(r=>r.operations)]) {
    delete op.locator; delete op.methodEvidence; delete op.savedMethodEvidence;
  }
  return copy;
}
function flatten(plan) {
  return [...(plan.operations || []), ...(plan.records || []).flatMap(r =>
    r.operations.map(o => ({...o, anchor: {label: r.anchorLabel, value: r.name}})))];
}
function settledReadError(error) {
  // A host timeout is not evidence that a remote operation has ended.
  return error?.settled === true;
}

async function runBoundedBatch(plan, preflight, adapter, state, now = Date.now, options = {}) {
  if (preflight?.schemaVersion !== 2 || !preflight.ready || preflight.runId !== plan.runId ||
      JSON.stringify(preflight.checkedPlan) !== JSON.stringify(plan)) {
    throw new Error('Current v2 plan and probe checks required');
  }
  const config = plan.execution || {};
  if (config.authorization?.fill !== true || !config.authorization.basis) {
    throw new Error('Existing fill authorization must be recorded');
  }
  for (const method of ['identify', 'inspect', 'write', 'checkpoint']) {
    if (typeof adapter[method] !== 'function') throw new Error('Adapter missing ' + method);
  }
  if (config.driver?.mode === 'adapter' && adapter.source !== preflight.checkedBatch) {
    throw new Error('Loaded adapter source differs from the checked source');
  }
  const budget = config.budgetMs ?? 1800000;
  const timeout = config.operationTimeoutMs ?? 120000;
  const saveTimeout = config.saveTimeoutMs ?? 180000;
  const threshold = config.familyFailures ?? 2;
  if (![budget, timeout, saveTimeout, threshold].every(x => Number.isInteger(x) && x > 0) ||
      !Number.isSafeInteger(config.roundStartedAt) || config.roundStartedAt < 0 ||
      config.roundStartedAt > now()) throw new Error('Invalid limits or original round start');
  if (!Number.isSafeInteger(config.probe?.observedAt) || config.probe.observedAt > now() ||
      now() - config.probe.observedAt > 300000) throw new Error('Refresh the read-only probe');
  if (!state || state.runId !== plan.runId || !state.results) throw new Error('Missing state');
  const fresh = state.status === 'prepared' && !Object.keys(state.results).length && state.startedAt == null;
  if (!fresh && (state.version !== 2 || JSON.stringify(state.workflow) !== JSON.stringify(workflow(plan)))) {
    throw new Error('Legacy or changed workflow: reconcile records; do not reset existing progress');
  }
  if (options.maxOperations != null && (!Number.isInteger(options.maxOperations) || options.maxOperations < 1)) {
    throw new Error('maxOperations must be a positive integer');
  }
  if (fresh) Object.assign(state, {version: 2, workflow: workflow(plan), startedAt: config.roundStartedAt,
    results: {}, modules: {}, familyFailures: {}, readAttempts: {}, writeAttempts: {},
    pendingWrite: null, pendingSave: null, pendingCall: null});
  state.executionStartedAt = now();
  state.status = 'running';
  delete state.reason;
  let storageFailed = false;
  // 无法可靠保存进度就停止；否则重启后无法判断上次写入是否已发生。
  const checkpoint = async () => {
    try { await adapter.checkpoint(clone(state)); return true; }
    catch { storageFailed = true; state.status = 'halted'; state.reason = 'state-write-failed'; return false; }
  };
  const halt = reason => { state.status = 'halted'; state.reason = reason; };
  // 使用最初准备时间计算剩余预算，续作和拆分批次不会重新获得完整时限。
  const left = (limit = timeout) => {
    const remaining = budget - (now() - state.startedAt);
    if (remaining <= 0) { halt('budget-exhausted'); return 0; }
    return Math.min(limit, remaining);
  };
  const inspect = async op => {
    const timeoutMs = left();
    if (!timeoutMs) return null;
    try {
      const result = await adapter.inspect(op, {timeoutMs});
      return left() ? result : null;
    } catch (error) {
      if (!settledReadError(error)) {
        state.pendingCall = {kind:'read', id:op.id};
        halt('read-call-unsettled');
      }
      else {
        state.readAttempts[op.id] = (state.readAttempts[op.id] || 0) + 1;
        if (state.pendingWrite !== op.id) state.results[op.id] = state.writeAttempts[op.id]
          ? {status: 'written-unverified', reason: 'settled-read-error'}
          : {status: 'read-failed', reason: 'settled-read-error'};
      }
      return null;
    }
  };
  const matches = (op, actual) => actual?.count === 1 && actual.kind === op.kind &&
    actual.value === op.value && (!op.anchor || actual.anchorMatched === true);
  const operations = flatten(plan);
  const selected = new Set(preflight.dispatchIds || []);
  const byId = Object.fromEntries(operations.map(op => [op.id, op]));
  if (!await checkpoint() || !left()) return state;
  // pendingCall 表示远程调用可能仍在运行；先确认其结束，再发起新的页面读取。
  // pendingWrite/pendingSave 则记录调用结束后仍需核对的业务结果。
  if (state.pendingCall) {
    let confirmation;
    try { confirmation = await adapter.confirmSettled?.(clone(state.pendingCall)); }
    catch { /* no settlement proof */ }
    if (confirmation?.settled !== true || !confirmation.evidence) {
      halt('remote-call-unsettled'); await checkpoint(); return state;
    }
    state.pendingCall = null;
    if (!await checkpoint()) return state;
  }
  try {
    const target = await adapter.identify({timeoutMs: left()});
    if (!target || !Object.entries(config.target).every(([key, value]) => target[key] === value)) halt('target-changed');
  } catch (error) {
    if (!settledReadError(error)) state.pendingCall = {kind:'read',id:'target'};
    halt('target-unverified');
  }
  if (!left() || state.status !== 'running') { await checkpoint(); return state; }

  // An uncertain save must be resolved without refresh or another click.
  if (state.pendingSave) {
    const module = (plan.modules || []).find(m => m.id === state.pendingSave);
    let proof;
    try { if (module && adapter.verifyModule) proof = await adapter.verifyModule(module, {timeoutMs: left(saveTimeout), readOnly: true}); }
    catch (error) { if (!settledReadError(error)) state.pendingCall={kind:'read',id:'save-'+state.pendingSave}; }
    if (proof?.saved === true && proof.evidence) {
      state.modules[module.id] = {status: 'saved', evidence: proof.evidence};
      state.pendingSave = null;
    } else halt('save-uncertain');
    if (!await checkpoint() || state.status !== 'running') return state;
  }
  if (state.pendingWrite) {
    const op = byId[state.pendingWrite];
    if (!op) throw new Error('Pending operation absent from plan');
    const previous = state.results[op.id];
    const current = await inspect(op);
    if (state.status !== 'running') { await checkpoint(); return state; }
    if (matches(op, current)) {
      state.results[op.id] = {status: 'written', evidence: 'page', reconciled: true};
      state.pendingWrite = null;
    // 部分新增只允许找回自己创建的同一条空记录；名称或位置不足以证明身份。
    } else if (previous?.status === 'partial' && previous.recordRef &&
               current?.count === 1 && current.value === '' && current.recordRef === previous.recordRef &&
               current.owned === true && typeof adapter.completeRecord === 'function') {
      // completeRecord must initialize this exact recorded draft, never click Add.
      let result;
      if (!await checkpoint() || !left()) return state;
      state.results[op.id] = {status:'attempted',phase:'complete-record',recordRef:previous.recordRef};
      state.pendingCall = {kind:'write',id:op.id};
      if (!await checkpoint() || !left()) return state;
      try { result = await adapter.completeRecord(op, {recordRef: previous.recordRef, timeoutMs: left()}); }
      catch (error) { result = {status: 'unknown',settled:settledReadError(error)}; }
      if (result?.status === 'written') {
        state.pendingCall = null;
        state.results[op.id] = {status: 'written-unverified', recordRef: previous.recordRef};
        if (!await checkpoint()) return state;
        const actual = await inspect(op);
        if (matches(op, actual)) { state.results[op.id] = {status: 'written', evidence: 'page'}; state.pendingWrite = null; }
        else halt('write-uncertain');
      } else {
        if (result?.settled === true) state.pendingCall = null;
        state.results[op.id] = {status:'unknown',reason:'record-completion-uncertain',recordRef:previous.recordRef};
        halt('write-uncertain');
      }
    } else halt('write-uncertain');
    if (!await checkpoint() || state.status !== 'running') return state;
  }

  let dispatched = 0;
  for (const op of operations) {
    if (state.status !== 'running' || !left()) break;
    if (!['fill', 'add-record'].includes(op.action)) {
      state.results[op.id] ||= {status: op.action};
      continue;
    }
    if (!selected.has(op.id)) continue;
    const previous = state.results[op.id];
    if (previous?.status === 'read-failed' &&
        (!options.retryReadFailures || state.readAttempts[op.id] >= 2)) continue;
    if (previous && ['failed', 'manual', 'conflict'].includes(previous.status)) continue;
    if (options.maxOperations != null && dispatched >= options.maxOperations) {
      state.status = 'paused'; state.reason = 'batch-boundary'; break;
    }
    const current = await inspect(op);
    if (!current) { if (!await checkpoint()) break; continue; }
    if (matches(op, current)) {
      state.results[op.id] = {status: 'written', evidence: 'page', existing: !state.writeAttempts[op.id]};
      if (!await checkpoint()) break;
      continue;
    }
    // 已尝试过的字段发生变化时交接冲突，避免恢复流程覆盖用户的手工修改。
    if (previous?.status === 'written' || previous?.status === 'written-unverified' || state.writeAttempts[op.id]) {
      state.results[op.id] = {status: 'conflict', reason: 'previously-written-value-changed'};
      if (!await checkpoint()) break;
      continue;
    }
    if (op.dependsOn && state.results[op.dependsOn]?.status !== 'written') {
      state.results[op.id] = {status: 'not-attempted', reason: 'dependency-pending'};
      continue;
    }
    if ((state.familyFailures[op.family] || 0) >= threshold) {
      state.results[op.id] = {status: 'manual', reason: 'family-stopped'};
      continue;
    }
    const expected = op.action === 'add-record' ? 0 : 1;
    if (current.count !== expected || current.kind !== op.kind || (op.anchor && current.anchorMatched !== true)) {
      state.results[op.id] = {status: 'failed', reason: 'mapping-changed'};
      state.familyFailures[op.family] = (state.familyFailures[op.family] || 0) + 1;
      if (!await checkpoint()) break;
      continue;
    }
    if (current.value !== op.before) {
      state.results[op.id] = {status: 'kept', reason: 'old-value-changed'};
      if (!await checkpoint()) break;
      continue;
    }
    // 先持久化写入意图再调用网页；即使调用后进程中断，下次也必须先核对而非重放。
    state.pendingWrite = op.id;
    state.pendingCall = {kind:'write',id:op.id};
    state.results[op.id] = {status: 'attempted'};
    if (!await checkpoint()) break;
    if (!left()) {
      state.pendingWrite = null;
      state.pendingCall = null;
      state.results[op.id] = {status: 'not-attempted', reason: 'budget-exhausted-before-dispatch'};
      break;
    }
    state.writeAttempts[op.id] = (state.writeAttempts[op.id] || 0) + 1;
    dispatched++;
    let outcome;
    try { outcome = await adapter.write(op, {timeoutMs: left()}); }
    catch (error) { outcome = {status: 'unknown',settled:settledReadError(error)}; }
    // 只有明确结束且确认无副作用的失败才可清除未决标记；抛异常本身不够。
    if (outcome?.status === 'failed' && outcome.noEffect === true && outcome.settled === true) {
      state.pendingWrite = null;
      state.pendingCall = null;
      state.results[op.id] = {status: 'failed', reason: 'write-failed-no-effect'};
      state.familyFailures[op.family] = (state.familyFailures[op.family] || 0) + 1;
    } else if (outcome?.status === 'partial' && outcome.recordRef) {
      state.pendingCall = null;
      state.results[op.id] = {status: 'partial', reason: 'record-created-name-pending', recordRef: outcome.recordRef};
      halt('partial-record');
    } else if (outcome?.status !== 'written') {
      if (outcome?.settled === true) state.pendingCall = null;
      state.results[op.id] = {status: 'unknown', reason: 'write-uncertain'};
      halt('write-uncertain');
    } else {
      state.pendingCall = null;
      state.pendingWrite = null;
      state.results[op.id] = {status: 'written-unverified'};
      if (!await checkpoint()) break;
      const actual = await inspect(op);
      if (matches(op, actual)) {
        state.results[op.id] = {status: 'written', evidence: 'page'};
        state.familyFailures[op.family] = 0;
      } else if (actual && state.status === 'running') {
        state.results[op.id] = {status: 'conflict', reason: 'readback-mismatch'};
      }
    }
    if (!await checkpoint()) break;
  }

  // Save per website module, only after every relevant planned write is verified.
  if (['running', 'paused'].includes(state.status) && config.authorization.save === true) {
    for (const module of plan.modules || []) {
      if (!left(saveTimeout) || state.pendingWrite || state.pendingSave) break;
      if (state.modules[module.id]?.status === 'saved') continue;
      const ids = module.operationIds || [];
      if (!ids.length || ids.some(id => !['written', 'keep'].includes(state.results[id]?.status))) continue;
      if (!adapter.inspectModule || !adapter.saveModule || !adapter.verifyModule) {
        state.modules[module.id] = {status: 'not-attempted', reason: 'save-adapter-unavailable'}; continue;
      }
      // Earlier batches may have been followed by user edits; recheck before saving.
      let moduleMatches = true;
      for (const id of ids) {
        const op=byId[id];
        if (op?.action==='keep') continue;
        const actual=await inspect(op);
        if (!matches(op,actual)) {
          moduleMatches=false;
          if (actual) state.results[id]={status:'conflict',reason:'changed-before-save'};
        }
        if (state.status==='halted') break;
      }
      if (state.status==='halted') break;
      if (!moduleMatches) {
        state.modules[module.id]={status:'blocked',reason:'planned-values-not-current'};
        if (!await checkpoint()) break;
        continue;
      }
      let inspection;
      try { inspection = await adapter.inspectModule(module, {timeoutMs: left(saveTimeout)}); }
      catch (error) {
        state.modules[module.id] = {status: 'blocked', reason: 'required-fields-unverified'};
        if (!settledReadError(error)) {state.pendingCall={kind:'read',id:'module-'+module.id};halt('read-call-unsettled');break;}
        continue;
      }
      if (inspection?.canSave !== true || (inspection.missingRequired || []).length) {
        state.modules[module.id] = {status: 'blocked', reason: 'required-fields-missing',
          missingRequired: inspection?.missingRequired || []};
        if (!await checkpoint()) break;
        continue;
      }
      // 同一版本的必填校验失败不再点击保存；需现场状态变化后才允许重试。
      if (state.modules[module.id]?.status === 'validation-failed' &&
          state.modules[module.id].revision === inspection.revision) continue;
      if (!left(saveTimeout)) break;
      state.pendingSave = module.id;
      state.pendingCall = {kind:'save',id:module.id};
      state.modules[module.id] = {status: 'attempted'};
      if (!await checkpoint()) break;
      if (!left(saveTimeout)) {
        state.pendingSave = null;
        state.pendingCall = null;
        state.modules[module.id] = {status: 'not-attempted', reason: 'budget-exhausted-before-dispatch'};
        break;
      }
      let result;
      try { result = await adapter.saveModule(module, {timeoutMs: left(saveTimeout)}); }
      catch (error) { result = {status: 'unknown',settled:settledReadError(error)}; }
      if (result?.status === 'validation-failed' && result.settled === true) {
        state.pendingSave = null;
        state.pendingCall = null;
        state.modules[module.id] = {status: 'validation-failed', revision: inspection.revision,
          missingRequired: result.missingRequired || []};
      } else if (result?.status !== 'saved' && result?.settled !== true) {
        state.modules[module.id] = {status:'unknown'};
        halt('save-call-unsettled');
      } else {
        state.pendingCall = null;
        let proof;
        if (left(saveTimeout)) {
          try { proof = await adapter.verifyModule(module, {timeoutMs: left(saveTimeout), readOnly: true}); }
          catch (error) { if (!settledReadError(error)) state.pendingCall={kind:'read',id:'save-'+module.id}; }
        }
        if (proof?.saved === true && proof.evidence) {
          state.modules[module.id] = {status: 'saved', evidence: proof.evidence}; state.pendingSave = null;
        } else { state.modules[module.id] = {status: 'unknown'}; halt('save-uncertain'); }
      }
      if (!await checkpoint() || state.status === 'halted') break;
    }
  }
  // completed 仅表示执行循环结束；仍要查看逐项结果和模块状态，不能称整份申请已提交。
  if (state.status === 'running') state.status = 'completed';
  for (const op of operations) state.results[op.id] ||= {
    status: ['fill', 'add-record'].includes(op.action) ? 'not-attempted' : op.action,
    reason: state.reason || 'outside-current-batch'
  };
  state.elapsedMs = now() - state.startedAt;
  state.executionElapsedMs = now() - state.executionStartedAt;
  if (!storageFailed) await checkpoint();
  return state;
}

if (typeof module !== 'undefined') module.exports = {runBoundedBatch};
