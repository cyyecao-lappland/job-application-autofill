/* Checks declared evidence, not a sandbox or proof of host tool behavior. */
const {isDeepStrictEqual: same} = require('node:util');
function checkBrowserPolicy(plan, {probe = true, now = Date.now} = {}) {
  const e = plan.execution || {}, d = e.driver || {}, t = e.target || {};
  const policy = e.interactionPolicy || {}, cua = policy.cua ?? 'disabled';
  const validId = value => (typeof value === 'string' && value.trim().length > 0) ||
    (Number.isSafeInteger(value) && value >= 0);
  if (!['disabled', 'enabled', 'denied'].includes(cua) ||
      (cua !== 'disabled' && !(typeof policy.basis === 'string' && policy.basis.trim())))
    throw new Error('Explicit CUA choice needs a user instruction basis');
  if (!['browser', 'cua'].includes(d.channel) || !d.name)
    throw new Error('Actual driver name and channel required');
  if (d.channel === 'cua' && cua !== 'enabled') throw new Error('CUA is disabled');
  if (!t.browser || !t.url || ![t.tabId, t.pageId].some(validId))
    throw new Error('Observed browser and tabId/pageId required');
  const discovery = e.discovery || {};
  if (!discovery.callId || !discovery.tool || discovery.settled !== true ||
      discovery.existing !== true || !same(discovery.target, t))
    throw new Error('Discovery must identify the existing target page');
  const ops = [...(plan.operations || []), ...(plan.records || []).flatMap(r => r.operations)];
  const ids = new Set(e.dispatchIds || ops.filter(o => ['fill', 'add-record'].includes(o.action)).map(o => o.id));
  for (const op of ops.filter(o => ids.has(o.id))) {
    if ((op.channel && op.channel !== d.channel) || (op.path === 'visual' && d.channel !== 'cua'))
      throw new Error('Operation channel differs from the current driver');
  }
  if (probe && !same(e.probe?.driver, d))
    throw new Error('Fresh probe from the current driver required');
  if (probe && (!same(e.probe?.target, t) || e.probe?.settled !== true || !e.probe?.callId ||
      !Number.isSafeInteger(e.probe.observedAt) || e.probe.observedAt > now() ||
      now() - e.probe.observedAt > 300000)) throw new Error('Current settled target probe required');
}
module.exports = {checkBrowserPolicy};
