// Load the shared page command as a side-effect module. The runtime file is
// intentionally dual-mode: CommonJS for the synthetic Node tests and an
// internal service-worker global for the MV3 extension. A named ESM import
// would make the extension worker fail to load because the same source file
// must remain require()-able by the tests.
const DEFAULTS = {bridgeUrl: 'http://127.0.0.1:43127', token: ''};
const ALARM = 'job-autofill-bridge-poll';
// A page is a shared mutable state machine: only one command may touch a tab
// at a time. Different tabs are independent, so they can be serviced in
// parallel. Keep this cap finite so a browser with many open recruitment tabs
// does not create an unbounded burst of executeScript calls.
const MAX_PARALLEL_TABS = 6;
const PAGE_COMMAND_TIMEOUTS = Object.freeze({
  identify: 10000,
  snapshot: 15000,
  discover: 10000,
  click: 15000,
  inspect: 10000,
  fill: 30000,
  inspectModule: 15000,
  saveModule: 30000,
  verifyModule: 15000,
  completeRecord: 30000
});
const NAVIGATION_TIMEOUT_MS = 30000;
const activeTabs = new Set();
const blockedTabs = new Set();
let pollScheduled = false;

function allowedPageUrl(value) {
  try {
    const url = new URL(value);
    if (url.protocol !== 'https:') return false;
    return url.hostname === 'liepin.com' || url.hostname.endsWith('.liepin.com') ||
      url.hostname === 'zhipin.com' || url.hostname.endsWith('.zhipin.com');
  } catch { return false; }
}

function normalizeBridgeUrl(value) {
  const url = new URL(value || DEFAULTS.bridgeUrl);
  if (url.protocol !== 'http:' || !['127.0.0.1', 'localhost', '[::1]'].includes(url.hostname)) {
    throw new Error('The bridge must use a loopback HTTP URL');
  }
  return url.toString().replace(/\/$/, '');
}

async function config() {
  const value = await chrome.storage.local.get(DEFAULTS);
  return {bridgeUrl: normalizeBridgeUrl(value.bridgeUrl), token: String(value.token || '')};
}

function authHeaders(token) {
  return {accept: 'application/json', 'content-type': 'application/json', authorization: `Bearer ${token}`};
}

async function bridgeRequest(path, options = {}) {
  const settings = await config();
  if (!settings.token) throw new Error('Bridge token is not configured in the extension options');
  const response = await fetch(settings.bridgeUrl + path, {
    ...options,
    headers: {...authHeaders(settings.token), ...(options.headers || {})}
  });
  if (response.status === 204) return null;
  const body = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(body.error || `Bridge HTTP ${response.status}`);
  return body;
}

async function targetTabs() {
  const tabs = await chrome.tabs.query({});
  return tabs.filter(tab => tab.id != null &&
    (allowedPageUrl(tab.url || '') || String(tab.url || '') === 'about:blank'));
}

function pageResult(result) {
  if (!result || !Object.prototype.hasOwnProperty.call(result, 'result')) {
    throw new Error('The page returned no structured result');
  }
  return result.result;
}

async function executePage(tabId, command) {
  const invocation = chrome.tabs.sendMessage(tabId, {type: 'page-command', command})
    .then(pageResult).catch(error => {
      // A missing receiver is a known no-effect condition: it means the
      // document predates the content-script listener and must be navigated
      // before another page command. Other failures remain uncertain because
      // the page may have run part of the command before the channel failed.
      if (!/Receiving end does not exist|Could not establish connection/i.test(String(error?.message || error))) {
        error.uncertain = true;
      }
      throw error;
    });
  const timeoutMs = PAGE_COMMAND_TIMEOUTS[command.kind] || 15000;
  // The timeout only releases other tabs; an uncertain tab remains blocked
  // until a navigation or reconciliation clears the uncertainty. The
  // underlying promise is still observed so a late rejection is harmless.
  let timer;
  const timeout = new Promise((_, reject) => {
    timer = setTimeout(() => {
      const error = new Error('Page command did not settle before timeout');
      error.uncertain = true;
      reject(error);
    }, timeoutMs);
  });
  try {
    return await Promise.race([invocation, timeout]);
  } finally {
    clearTimeout(timer);
    invocation.catch(() => {});
  }
}

function sameDestination(requestedUrl, actualUrl) {
  try {
    const expected = new URL(requestedUrl);
    const actual = new URL(actualUrl);
    return expected.protocol === actual.protocol && expected.hostname === actual.hostname &&
      actual.pathname.startsWith(expected.pathname);
  } catch { return false; }
}

function waitForTabComplete(tabId, requestedUrl, timeoutMs = 120000) {
  let rejectCompletion;
  let cleanupCompletion = () => {};
  const promise = new Promise((resolve, reject) => {
    rejectCompletion = reject;
    let timer;
    let poll;
    let settled = false;
    const cleanup = () => {
      clearTimeout(timer);
      clearInterval(poll);
      chrome.tabs.onUpdated.removeListener(onUpdated);
    };
    cleanupCompletion = cleanup;
    const finishIfReady = tab => {
      if (settled || !tab || tab.id !== tabId || tab.status !== 'complete') return;
      if (!allowedPageUrl(tab.url || '') || (requestedUrl && !sameDestination(requestedUrl, tab.url))) return;
      settled = true;
      cleanup();
      resolve({url: tab.url});
    };
    const onUpdated = (updatedTabId, changeInfo, tab) => {
      if (updatedTabId !== tabId) return;
      finishIfReady(tab);
    };
    chrome.tabs.onUpdated.addListener(onUpdated);
    const pollTab = () => chrome.tabs.get(tabId).then(finishIfReady).catch(() => {});
    pollTab();
    poll = setInterval(pollTab, 250);
    timer = setTimeout(() => {
      if (settled) return;
      settled = true;
      cleanup();
      const error = new Error('Tab did not finish navigation before timeout');
      error.uncertain = true;
      reject(error);
    }, timeoutMs);
  });
  promise.cancel = error => { cleanupCompletion(); rejectCompletion(error); };
  return promise;
}

async function dispatch(tab, command) {
  if (String(tab.url || '') === 'about:blank' && command.kind !== 'navigate') {
    throw new Error('Only an explicit navigation command may run from about:blank');
  }
  if (command.kind === 'navigate') {
    const targetUrl = new URL(command.url);
    if (!allowedPageUrl(targetUrl.toString())) throw new Error('Navigation target is outside the supported recruitment sites');
    const completion = waitForTabComplete(tab.id, targetUrl.toString(), NAVIGATION_TIMEOUT_MS);
    let tabUpdate;
    try { tabUpdate = await chrome.tabs.update(tab.id, {url: targetUrl.toString(), active: true}); }
    catch (error) { completion.cancel(error); await completion.catch(() => {}); throw error; }
    const completed = await completion;
    return {ok: true, status: 'navigated', requestedUrl: targetUrl.toString(),
      finalUrl: completed.url || tabUpdate.url || targetUrl.toString()};
  }
  if (!['identify', 'snapshot', 'discover', 'click', 'inspect', 'fill', 'inspectModule', 'saveModule', 'verifyModule', 'completeRecord'].includes(command.kind)) {
    throw new Error(`Command ${String(command.kind)} is not allowlisted`);
  }
  return await executePage(tab.id, command);
}

function schedulePoll() {
  if (pollScheduled) return;
  pollScheduled = true;
  Promise.resolve().then(async () => {
    pollScheduled = false;
    try { await pollOnce(); } catch { /* the next alarm or page-ready retries */ }
  });
}

async function pollTab(tab) {
  const tabId = String(tab.id);
  if (activeTabs.has(tabId)) return false;
  const tabWasBlocked = blockedTabs.has(tabId);
  activeTabs.add(tabId);
  let dispatched = false;
  try {
    const query = `?tabId=${encodeURIComponent(tabId)}&url=${encodeURIComponent(tab.url || '')}`;
    let next;
    try {
      next = await bridgeRequest('/v1/commands/next' + query);
    } catch {
      // A stopped local bridge or a tab that is navigating is not a reason to
      // manufacture a completion result. The bridge keeps the command state.
      return false;
    }
    if (!next) return false;
    dispatched = true;
    let state = 'done';
    let result;
    try {
      if (tabWasBlocked && next.command.kind !== 'navigate') {
        // A command can be assigned while a tab is blocked only so it can be
        // settled without touching the page. The runner must reconcile its
        // own pending operation before sending another ordinary command.
        state = 'failed';
        result = {ok: false, status: 'failed', noEffect: true, error: {
          code: 'tab-blocked-recovery',
          message: 'Tab is blocked after an uncertain command; navigate first or reconcile the pending request'
        }};
      } else {
        // An explicit navigation is the only automatic recovery boundary for
        // a blocked tab. The bridge now matches navigate by tabId even when
        // the current URL is about:blank.
        if (tabWasBlocked && next.command.kind === 'navigate') blockedTabs.delete(tabId);
        result = await dispatch(tab, next.command);
      }
    } catch (error) {
      state = error.uncertain ? 'unknown' : 'failed';
      if (error.uncertain) blockedTabs.add(tabId);
      result = {ok: false, status: state, error: {message: error.message || 'Extension dispatch failed'}};
    }
    try {
      await bridgeRequest('/v1/results', {method: 'POST', body: JSON.stringify({requestId: next.requestId, state, result})});
    } catch {
      // The command is already dispatched. Do not replay it if the bridge
      // disappears while the result is being posted.
    }
    return true;
  } finally {
    activeTabs.delete(tabId);
    // Drain additional queued commands on this same tab on a later turn;
    // activeTabs still guarantees that they never overlap.
    if (dispatched) schedulePoll();
  }
}

async function pollOnce() {
  const tabs = await targetTabs();
  let nextIndex = 0;
  const workerCount = Math.min(MAX_PARALLEL_TABS, tabs.length);
  await Promise.all(Array.from({length: workerCount}, async () => {
    while (nextIndex < tabs.length) {
      const tab = tabs[nextIndex++];
      await pollTab(tab);
    }
  }));
}

chrome.runtime.onInstalled.addListener(() => {
  chrome.alarms.create(ALARM, {periodInMinutes: 0.5});
  schedulePoll();
});
chrome.runtime.onStartup.addListener(() => {
  chrome.alarms.create(ALARM, {periodInMinutes: 0.5});
  schedulePoll();
});
chrome.alarms.onAlarm.addListener(alarm => { if (alarm.name === ALARM) schedulePoll(); });
chrome.runtime.onMessage.addListener(message => {
  if (message?.type === 'page-ready' || message?.type === 'config-updated') schedulePoll();
});

chrome.tabs.onUpdated.addListener((tabId, changeInfo) => {
  // A real navigation cancels page-side work in the old document, so it is a
  // valid boundary for clearing an uncertain tab. A navigate command is also
  // allowed to establish this boundary without requiring a manual reload.
  if (changeInfo.status === 'loading') blockedTabs.delete(String(tabId));
  if (changeInfo.status === 'complete') schedulePoll();
});
chrome.tabs.onRemoved.addListener(tabId => {
  activeTabs.delete(String(tabId));
  blockedTabs.delete(String(tabId));
});

// A service worker may be loaded without an install event during development.
chrome.alarms.create(ALARM, {periodInMinutes: 0.5});
schedulePoll();
