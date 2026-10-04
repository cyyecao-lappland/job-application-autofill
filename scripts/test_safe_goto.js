/* Synthetic CDP only: no browser, no network, and no user data. */
const assert = require('node:assert/strict');
const {EventEmitter} = require('node:events');
const {safeGoto} = require('./safe_goto.js');
const {connectCdpTarget} = require('./cdp_connection.js');

class FakeCdp extends EventEmitter {
  constructor({emitNavigation = true, navigateError = null} = {}) {
    super();
    this.calls = [];
    this.runtimeDisabled = false;
    this.emitNavigation = emitNavigation;
    this.navigateError = navigateError;
    this.currentUrl = 'https://c.liepin.com/resume/edit';
  }
  async send(method, params = {}) {
    this.calls.push({method, params, runtimeDisabled: this.runtimeDisabled});
    if (method === 'Runtime.disable') { this.runtimeDisabled = true; return {}; }
    if (method === 'Runtime.enable') { assert.equal(this.runtimeDisabled, true); this.runtimeDisabled = false; return {}; }
    if (method === 'Page.navigate') {
      assert.equal(this.runtimeDisabled, true, 'navigation must happen while Runtime is disabled');
      if (this.navigateError) return {errorText: this.navigateError};
      if (this.emitNavigation) {
        setImmediate(() => {
          this.emit('Page.frameStartedLoading', {frameId: 'main'});
          this.currentUrl = 'https://c.liepin.com/login';
          this.emit('Page.frameNavigated', {frame: {id: 'main', url: this.currentUrl}});
          this.emit('Page.loadEventFired', {});
          setTimeout(() => {
            this.emit('Page.frameStartedLoading', {frameId: 'main'});
            this.currentUrl = params.url;
            this.emit('Page.frameNavigated', {frame: {id: 'main', url: this.currentUrl}});
            this.emit('Page.loadEventFired', {});
          }, 4);
        });
      }
      return {frameId: 'main'};
    }
    if (method === 'Page.getFrameTree') {
      assert.equal(this.runtimeDisabled, true, 'final Page read may remain on the same disabled Runtime session');
      return {frameTree: {frame: {id: 'main', url: this.currentUrl}}};
    }
    return {};
  }
}

class FakeWebSocket {
  constructor(url) {
    this.url = url;
    this.listeners = new Map();
    setImmediate(() => this.emit('open', {}));
  }
  addEventListener(name, listener) {
    if (!this.listeners.has(name)) this.listeners.set(name, new Set());
    this.listeners.get(name).add(listener);
  }
  removeEventListener(name, listener) { this.listeners.get(name)?.delete(listener); }
  emit(name, value) { for (const listener of this.listeners.get(name) || []) listener(value); }
  send(payload) {
    const request = JSON.parse(payload);
    setImmediate(() => this.emit('message', {data: JSON.stringify({id: request.id, result: {method: request.method}})}));
  }
  close() { this.emit('close', {}); }
}

(async () => {
  const session = new FakeCdp();
  const result = await safeGoto(session, 'https://c.liepin.com/resume/edit?editType=attachment', {settleMs: 5, timeoutMs: 500});
  assert.equal(result.status, 'navigated');
  assert.equal(result.finalUrl, 'https://c.liepin.com/resume/edit?editType=attachment');
  assert.equal(result.redirected, false);
  assert.equal(session.runtimeDisabled, false);
  const order = session.calls.map(call => call.method);
  assert(order.indexOf('Runtime.disable') < order.indexOf('Page.navigate'));
  assert(order.indexOf('Page.navigate') < order.indexOf('Page.getFrameTree'));
  assert(order.indexOf('Page.getFrameTree') < order.indexOf('Runtime.enable'));

  const timeoutSession = new FakeCdp({emitNavigation: false});
  await assert.rejects(() => safeGoto(timeoutSession, 'https://c.liepin.com/resume/edit', {settleMs: 1, timeoutMs: 20}), error => {
    assert.equal(error.code, 'NAVIGATION_TIMEOUT');
    assert.equal(error.settled, false);
    assert.equal(error.runtimeRestored, true);
    return true;
  });
  assert.equal(timeoutSession.calls.filter(call => call.method === 'Page.navigate').length, 1);
  assert.equal(timeoutSession.runtimeDisabled, false);

  const errorSession = new FakeCdp({navigateError: 'ERR_ABORTED'});
  await assert.rejects(() => safeGoto(errorSession, 'https://c.liepin.com/resume/edit', {settleMs: 1, timeoutMs: 100}), error => {
    assert.equal(error.code, 'NAVIGATION_ERROR');
    assert.equal(error.runtimeRestored, true);
    return true;
  });

  const connection = connectCdpTarget('ws://127.0.0.1:9222/devtools/page/synthetic', {WebSocketImpl: FakeWebSocket});
  await connection.opened;
  assert.deepEqual(await connection.client.send('Page.enable'), {method: 'Page.enable'});
  await connection.client.close();
  assert.throws(() => connectCdpTarget('ws://192.168.1.10:9222/devtools/page/x', {WebSocketImpl: FakeWebSocket}), /loopback/);
  console.log('safeGoto and CDP connection checks passed');
})().catch(error => { console.error(error.stack); process.exitCode = 1; });
