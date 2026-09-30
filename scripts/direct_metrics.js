'use strict';
// Outer-tool accounting. Caller persists state using the existing durable mechanism.
// This module never issues browser calls or stores field values.
function beginCall(state, callKey, dispatchIds, now=Date.now) {
  if (state.pendingCall || state.pendingWrite || state.pendingSave) throw new Error('unresolved-operation');
  if (!callKey || state.metrics?.calls?.[callKey]) throw new Error('duplicate-call');
  const at=now();
  state.metrics ||= {run_start:at,first_write:null,run_end:null,calls:{}};
  state.metrics.calls[callKey]={startedAt:at,status:'pending',dispatchIds:[...dispatchIds]};
  state.pendingCall={kind:"direct-batch",phase:"prepared",callKey,dispatchIds:[...dispatchIds]};
}
function finishCall(state, callKey, receipt, result, now=Date.now) {
  const call=state.metrics?.calls?.[callKey];
  if (state.pendingCall?.callKey !== callKey || !call || !receipt?.callId || receipt.settled !== true)
    throw new Error('settled-tool-receipt-required');
  call.endedAt=now(); call.wallTimeMs=call.endedAt-call.startedAt; call.callId=receipt.callId;
  call.status='returned'; call.batchDurationMs=result.durationMs;
  if (result.firstWriteAt != null) state.metrics.first_write ??= result.firstWriteAt;
  state.results ||= {};
  Object.assign(state.results,result.results);
  state.metrics.writtenIds=[...new Set([...(state.metrics.writtenIds || []),...(result.written || [])])];
  // A returned tool may report an inner call whose status is still unknown.
  if (result.unknown?.length || result.pendingRemote) {
    state.pendingCall={callKey,unknownIds:[...(result.unknown || [])],pendingRemote:result.pendingRemote,outerSettled:true};
  } else state.pendingCall=null;
}
function summary(state, now=Date.now) {
  const m=state.metrics || {}, calls=Object.values(m.calls || {}), results=Object.values(state.results || {});
  const count=status => results.filter(r => r.status === status).length;
  const written=(m.writtenIds || []).length;
  return {run_start:m.run_start,first_write:m.first_write,run_end:m.run_end,
    ttffMs:m.first_write == null ? null : m.first_write-m.run_start,
    elapsedMs:m.run_start == null ? null : (m.run_end ?? now())-m.run_start,
    browser_tool_calls:calls.length,batch_count:calls.filter(c => c.batchDurationMs != null).length,
    batch_duration:calls.filter(c => c.batchDurationMs != null).map(c => c.batchDurationMs),
    successful_writes:written,alreadyMatched:count('alreadyMatched'),failed:count('failed'),
    conflict:count('conflict'),unknown:count('unknown'),deferred:count('deferred'),manual:count('manual'),
    successfulFieldsPerCall:calls.length ? written/calls.length : null};
}
function recordObservation(state, receipt, startedAt, endedAt) {
  if (!receipt?.callId || receipt.settled !== true || !Number.isFinite(startedAt) || endedAt < startedAt)
    throw new Error('completed-observation-required');
  state.metrics ||= {run_start:startedAt,first_write:null,run_end:null,calls:{}};
  const key=receipt.callId;
  if (state.metrics.calls[key]) throw new Error('duplicate-call');
  state.metrics.calls[key]={callId:key,status:'returned',startedAt,endedAt,wallTimeMs:endedAt-startedAt};
}
module.exports={beginCall,finishCall,recordObservation,summary};
