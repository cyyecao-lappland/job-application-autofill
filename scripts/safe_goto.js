'use strict';

/*
 * Navigation for pages that react to debugger/runtime attachment. The caller
 * supplies one CDP session and this module keeps every command on that
 * session. It never opens a second tab, retries a navigation, or evaluates
 * page JavaScript while Runtime is disabled.
 */

function cdpError(message, code, extra = {}) {
  const error = new Error(message);
  error.code = code;
  Object.assign(error, extra);
  return error;
}

function send(session, method, params) {
  if (!session || typeof session.send !== 'function') {
    throw new TypeError('safeGoto requires a CDP session with send(method, params)');
  }
  return session.send(method, params);
}

function listen(session, event, listener) {
  if (typeof session.on === 'function') session.on(event, listener);
  else if (typeof session.addListener === 'function') session.addListener(event, listener);
  else throw new TypeError('safeGoto requires a CDP session event emitter');
}

function unlisten(session, event, listener) {
  if (typeof session.off === 'function') session.off(event, listener);
  else if (typeof session.removeListener === 'function') session.removeListener(event, listener);
  else if (typeof session.removeEventListener === 'function') session.removeEventListener(event, listener);
}

function isOptionalPageCommandError(error) {
  return /method not found|unknown command|not implemented|does not exist/i.test(error?.message || '');
}

function createNavigationWaiter(session, {timeoutMs = 120000, settleMs = 500, frameId} = {}) {
  let mainFrameId = frameId || null;
  let committed = false;
  let loaded = false;
  let lastUrl;
  let finished = false;
  let timeout;
  let settleTimer;
  let resolvePromise;
  let rejectPromise;
  const registrations = [];

  const cleanup = () => {
    clearTimeout(timeout);
    clearTimeout(settleTimer);
    for (const [event, listener] of registrations) unlisten(session, event, listener);
    registrations.length = 0;
  };
  const finish = result => {
    if (finished) return;
    finished = true;
    cleanup();
    resolvePromise(result);
  };
  const fail = error => {
    if (finished) return;
    finished = true;
    cleanup();
    rejectPromise(error);
  };
  const belongsToMainFrame = id => !mainFrameId || !id || id === mainFrameId;
  const armStableCompletion = () => {
    if (!committed || !loaded || finished) return;
    clearTimeout(settleTimer);
    settleTimer = setTimeout(() => finish({frameId: mainFrameId, url: lastUrl, loaded: true}), settleMs);
  };
  const noteFrame = frame => {
    if (!frame) return;
    if (!mainFrameId && !frame.parentId) mainFrameId = frame.id;
    if (frame.id !== mainFrameId) return;
    committed = true;
    loaded = false;
    lastUrl = frame.url || lastUrl;
    clearTimeout(settleTimer);
  };
  const noteSameDocument = event => {
    if (!belongsToMainFrame(event?.frameId)) return;
    if (!mainFrameId && event?.frameId) mainFrameId = event.frameId;
    committed = true;
    loaded = true;
    lastUrl = event?.url || lastUrl;
    armStableCompletion();
  };
  const noteLoaded = event => {
    const eventFrameId = event?.frameId;
    if (!belongsToMainFrame(eventFrameId)) return;
    if (!mainFrameId && eventFrameId) mainFrameId = eventFrameId;
    if (!committed) committed = true;
    loaded = true;
    armStableCompletion();
  };
  const noteLoading = event => {
    if (!belongsToMainFrame(event?.frameId)) return;
    loaded = false;
    clearTimeout(settleTimer);
  };

  const promise = new Promise((resolve, reject) => {
    resolvePromise = resolve;
    rejectPromise = reject;
  });
  const add = (event, listener) => {
    listen(session, event, listener);
    registrations.push([event, listener]);
  };
  add('Page.frameNavigated', event => noteFrame(event?.frame));
  add('Page.navigatedWithinDocument', noteSameDocument);
  add('Page.loadEventFired', noteLoaded);
  add('Page.lifecycleEvent', event => {
    if (event?.name === 'load' || event?.name === 'networkIdle') noteLoaded(event);
  });
  add('Page.frameStartedLoading', noteLoading);
  timeout = setTimeout(() => fail(cdpError('Page did not reach a stable load before timeout',
    'NAVIGATION_TIMEOUT', {settled: false, lastUrl})), timeoutMs);

  return {
    promise,
    setFrameId(id) {
      if (!mainFrameId && id) mainFrameId = id;
    },
    cancel(error = cdpError('Navigation waiter cancelled', 'NAVIGATION_CANCELLED', {settled: false})) {
      fail(error);
    }
  };
}

async function safeGoto(session, requestedUrl, options = {}) {
  let url;
  try { url = new URL(requestedUrl).toString(); }
  catch { throw cdpError('safeGoto requires a valid URL', 'INVALID_URL', {settled: true}); }
  if (!['http:', 'https:'].includes(new URL(url).protocol)) {
    throw cdpError('safeGoto only permits HTTP(S) navigation', 'INVALID_URL_SCHEME', {settled: true});
  }

  const timeoutMs = options.timeoutMs ?? 120000;
  const settleMs = options.settleMs ?? 500;
  if (!Number.isInteger(timeoutMs) || timeoutMs <= 0 || !Number.isInteger(settleMs) || settleMs < 0) {
    throw new RangeError('timeoutMs must be positive and settleMs must be non-negative');
  }

  let waiter;
  let runtimeTouched = false;
  let runtimeRestored = false;
  let result;
  let failure;
  try {
    // Page events are enabled while Runtime is still available, then the
    // watcher is armed before navigation so redirects cannot be missed.
    await send(session, 'Page.enable');
    try { await send(session, 'Page.setLifecycleEventsEnabled', {enabled: true}); }
    catch (error) { if (!isOptionalPageCommandError(error)) throw error; }
    waiter = createNavigationWaiter(session, {timeoutMs, settleMs});

    // Set the flag before dispatch: if Runtime.disable itself rejects, the
    // state is still unsafe to assume and the finally block restores it.
    runtimeTouched = true;
    await send(session, 'Runtime.disable');
    const navigation = await send(session, 'Page.navigate', {
      url,
      ...(options.referrer ? {referrer: options.referrer} : {}),
      ...(options.transitionType ? {transitionType: options.transitionType} : {})
    });
    if (navigation?.errorText) {
      waiter.cancel(cdpError(`Page.navigate failed: ${navigation.errorText}`, 'NAVIGATION_ERROR',
        {settled: true, navigation}));
      await waiter.promise;
    }
    waiter.setFrameId(navigation?.frameId);
    const navigationResult = await waiter.promise;
    let frameTree;
    try { frameTree = await send(session, 'Page.getFrameTree'); }
    catch (error) {
      // A stable Page event is already evidence that navigation completed;
      // keep the last event URL if the optional final read is unavailable.
      if (!isOptionalPageCommandError(error)) throw error;
    }
    const finalUrl = frameTree?.frameTree?.frame?.url || navigationResult.url;
    if (!finalUrl) throw cdpError('Navigation completed without a readable final URL',
      'NAVIGATION_URL_UNAVAILABLE', {settled: false});
    result = {status: 'navigated', requestedUrl: url, finalUrl,
      redirected: finalUrl !== url, frameId: navigationResult.frameId || navigation?.frameId,
      navigation};
  } catch (error) {
    failure = error;
    if (waiter) waiter.cancel(cdpError('Navigation aborted after a CDP error', 'NAVIGATION_ABORTED',
      {cause: failure, settled: false}));
  } finally {
    if (runtimeTouched) {
      try {
        await send(session, 'Runtime.enable');
        runtimeRestored = true;
      } catch (error) {
        const restoreError = cdpError('Could not restore the CDP Runtime domain',
          'RUNTIME_RESTORE_FAILED', {cause: error, settled: false});
        if (!failure) failure = restoreError;
        else {
          failure.runtimeRestored = false;
          failure.restoreError = restoreError.message;
        }
      }
    }
  }
  if (failure) {
    failure.requestedUrl = url;
    failure.runtimeRestored = runtimeRestored;
    throw failure;
  }
  result.runtimeRestored = runtimeRestored;
  return result;
}

module.exports = {safeGoto, createNavigationWaiter};
