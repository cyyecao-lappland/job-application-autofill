/* Synthetic DOM/bridge checks only: no Edge connection and no real profile. */
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const {runPageCommand} = require('../extension/edge-via-extension/page_runtime.js');
const {createBridgeServer, ExtensionBridgeClient, createViaExtensionAdapter,
  BRIDGE_PROTOCOL_VERSION, targetMatches} = require('./via_extension_bridge.js');

class FakeInput {
  constructor(id, value = '') {
    this.nodeType = 1; this.tagName = 'INPUT'; this.type = 'text'; this.id = id;
    this.value = value; this.ownerDocument = global.document; this.events = [];
    this.isContentEditable = false;
  }
  getAttribute(name) { return name === 'id' ? this.id : null; }
  dispatchEvent(event) { this.events.push(event.type); return true; }
  closest() { return null; }
  matches(selector) { return selector === 'input[type="radio"]' ? false : selector === '#name' && this.id === 'name'; }
}

class FakeDocument {
  constructor(input) {
    this.title = 'Synthetic'; this.input = input; this.defaultView = {getComputedStyle: () => ({})};
    this.input.ownerDocument = this;
  }
  querySelectorAll(selector) {
    if (selector === '#name') return this.input.id === 'name' ? [this.input] : [];
    if (selector.includes('input')) return [this.input];
    return [];
  }
  getElementById(id) { return id === this.input.id ? this.input : null; }
  querySelector() { return null; }
}

global.HTMLInputElement = FakeInput;
global.HTMLTextAreaElement = class {};
global.Event = class { constructor(type) { this.type = type; } };
global.InputEvent = global.Event;
global.CSS = {escape: value => String(value)};
global.location = {href: 'https://c.liepin.com/resume/edit'};
const input = new FakeInput('name');
global.document = new FakeDocument(input);

(async () => {
  const written = runPageCommand({kind: 'fill', operation: {
    id: 'F1', label: 'Synthetic field', kind: 'text', before: '', value: 'target', locator: {selector: '#name'}
  }});
  assert.equal(written.ok, true);
  assert.equal(written.operations[0].status, 'written');
  assert.equal(input.value, 'target');
  const conflict = runPageCommand({kind: 'fill', operation: {
    id: 'F1', label: 'Synthetic field', kind: 'text', before: '', value: 'other', locator: {selector: '#name'}
  }});
  assert.equal(conflict.operations[0].status, 'conflict');
  const forbidden = runPageCommand({kind: 'evaluate', code: 'document.body.innerHTML="bad"'});
  assert.equal(forbidden.error.code, 'command-not-allowed');
  const sensitive = runPageCommand({kind: 'inspect', operation: {
    id: 'S1', label: 'Synthetic password', kind: 'password', locator: {selector: '#name'}
  }});
  assert.equal(sensitive.error.code, 'sensitive-control');

  const clickEvents = [];
  const clickButton = {
    nodeType: 1, tagName: 'BUTTON', type: 'button', ownerDocument: global.document,
    innerText: '编辑', textContent: '编辑', parentElement: null,
    getAttribute(name) { return name === 'class' ? 'resume-edit' : null; },
    getClientRects() { return [{}]; }, querySelectorAll() { return []; },
    click() { clickEvents.push('clicked'); }
  };
  const dangerousButton = {
    nodeType: 1, tagName: 'BUTTON', type: 'button', ownerDocument: global.document,
    innerText: '提交', textContent: '提交', parentElement: null,
    getAttribute(name) { return name === 'class' ? 'resume-submit' : null; },
    getClientRects() { return [{}]; }, querySelectorAll() { return []; },
    click() { clickEvents.push('dangerous'); }
  };
  const optionButton = {
    nodeType: 1, tagName: 'DIV', type: '', ownerDocument: global.document,
    innerText: '在校生', textContent: '在校生', parentElement: null,
    getAttribute(name) {
      if (name === 'class') return 'ant-select-item-option';
      if (name === 'role') return 'option';
      return null;
    },
    getClientRects() { return [{}]; }, querySelectorAll() { return []; },
    matches(selector) { return selector.includes('[role="option"]') || selector.includes('[class*="option"]'); },
    click() { clickEvents.push('option'); }
  };
  const comboButton = {
    nodeType: 1, tagName: 'INPUT', type: 'search', ownerDocument: global.document,
    innerText: '', textContent: '', parentElement: null, readOnly: true,
    getAttribute(name) {
      if (name === 'class') return 'resume-combo';
      if (name === 'role') return 'combobox';
      if (name === 'placeholder') return '目前状态';
      return null;
    },
    getClientRects() { return [{}]; }, querySelectorAll() { return []; },
    matches(selector) { return selector.includes('[role="combobox"]'); },
    click() { clickEvents.push('open-select'); }
  };
  const originalQuerySelectorAll = global.document.querySelectorAll.bind(global.document);
  global.document.querySelectorAll = selector => {
    if (selector === 'button,a[href],[role="button"],[role="link"],[role="option"],[role="combobox"],[class*="option"],[tabindex]:not([tabindex="-1"]),input[type="button"],input[type="submit"],summary,[onclick]') {
      return [clickButton, dangerousButton, optionButton, comboButton];
    }
    if (selector === 'body *') return [clickButton, dangerousButton, optionButton, comboButton];
    if (selector === 'button.resume-edit' || selector === '.resume-edit') return [clickButton];
    if (selector === 'button.resume-submit' || selector === '.resume-submit') return [dangerousButton];
    if (selector === 'div.ant-select-item-option' || selector === '.resume-option') return [optionButton];
    if (selector === 'input.resume-combo' || selector === '.resume-combo') return [comboButton];
    return originalQuerySelectorAll(selector);
  };
  const discovered = runPageCommand({kind: 'discover'});
  assert.equal(discovered.ok, true);
  assert.equal(discovered.elements.find(element => element.text === '编辑')?.clickAllowed, true);
  const clicked = runPageCommand({kind: 'click', target: {
    locator: {selector: '.resume-edit'}, expectedText: '编辑'
  }});
  assert.equal(clicked.status, 'clicked');
  assert.deepEqual(clickEvents, ['clicked']);
  const opened = runPageCommand({kind: 'click', target: {
    purpose: 'open-select', locator: {selector: '.resume-combo'}, expectedText: '目前状态'
  }});
  assert.equal(opened.status, 'clicked');
  assert.deepEqual(clickEvents, ['clicked', 'open-select']);
  const optionClicked = runPageCommand({kind: 'click', target: {
    purpose: 'select-option', locator: {selector: '.resume-option'}, expectedText: '在校生'
  }});
  assert.equal(optionClicked.status, 'clicked');
  assert.deepEqual(clickEvents, ['clicked', 'open-select', 'option']);
  const blockedClick = runPageCommand({kind: 'click', target: {
    locator: {selector: '.resume-submit'}, expectedText: '提交'
  }});
  assert.equal(blockedClick.error.code, 'click-label-blocked');
  global.document.querySelectorAll = originalQuerySelectorAll;

  const extensionDir = path.join(__dirname, '..', 'extension', 'edge-via-extension');
  const manifest = JSON.parse(fs.readFileSync(path.join(extensionDir, 'manifest.json'), 'utf8'));
  assert(!manifest.permissions.includes('debugger'));
  assert(!fs.readFileSync(path.join(extensionDir, 'background.js'), 'utf8').includes('chrome.debugger'));
  assert(manifest.permissions.includes('scripting'));
  assert(manifest.host_permissions.includes('http://127.0.0.1/*'));
  assert.deepEqual(manifest.content_scripts[0].js, ['page_runtime.js', 'content.js']);
  const contentScript = fs.readFileSync(path.join(extensionDir, 'content.js'), 'utf8');
  assert(contentScript.includes('chrome.runtime.onMessage.addListener'));
  assert(contentScript.includes("'page-command'"));

  const bridge = createBridgeServer({port: 0, token: 'synthetic-bridge-token-123456'});
  await bridge.listen();
  const address = bridge.server.address();
  const baseUrl = `http://127.0.0.1:${address.port}`;
  const client = new ExtensionBridgeClient({baseUrl, token: bridge.token, pollMs: 5, defaultTimeoutMs: 100});
  const health = await client.health();
  assert.equal(health.ok, true);
  assert.equal(health.protocolVersion, BRIDGE_PROTOCOL_VERSION);
  assert.equal(health.capabilities.reconcile, true);
  assert.equal(health.capabilities.navigateFromBlank, true);

  const navigateEntry = {payload: {
    target: {tabId: 42, urlPrefix: 'https://c.liepin.com/'},
    payload: {kind: 'navigate'}
  }};
  const blankQuery = new URLSearchParams({tabId: '42', url: 'about:blank'});
  assert.equal(targetMatches(navigateEntry, blankQuery), true);
  assert.equal(targetMatches({payload: {
    target: navigateEntry.payload.target,
    payload: {kind: 'inspect'}
  }}, blankQuery), false);

  const navigateCreate = await fetch(`${baseUrl}/v1/commands`, {method: 'POST', headers: {
    authorization: `Bearer ${bridge.token}`, 'content-type': 'application/json'
  }, body: JSON.stringify({command: {kind: 'navigate', url: 'https://c.liepin.com/resume/edit'},
    target: {tabId: 42, urlPrefix: 'https://c.liepin.com/'}})});
  assert.equal(navigateCreate.status, 202);
  const navigateCreated = await navigateCreate.json();
  const blankNext = await fetch(`${baseUrl}/v1/commands/next?tabId=42&url=about%3Ablank`, {
    headers: {authorization: `Bearer ${bridge.token}`}
  });
  assert.equal(blankNext.status, 200);
  const blankCommand = await blankNext.json();
  assert.equal(blankCommand.requestId, navigateCreated.requestId);
  assert.equal(blankCommand.command.kind, 'navigate');
  await fetch(`${baseUrl}/v1/results`, {method: 'POST', headers: {
    authorization: `Bearer ${bridge.token}`, 'content-type': 'application/json'
  }, body: JSON.stringify({requestId: blankCommand.requestId, state: 'done',
    result: {ok: true, status: 'navigated'}})});

  const pending = client.request({kind: 'fill', operations: [{id: 'F1', value: 'target'}]},
    {target: {tabId: 7, urlPrefix: 'https://c.liepin.com/'}, timeoutMs: 200});
  await new Promise(resolve => setTimeout(resolve, 10));
  const wrong = await fetch(`${baseUrl}/v1/commands/next?tabId=8&url=https%3A%2F%2Fc.liepin.com%2Fresume`, {
    headers: {authorization: `Bearer ${bridge.token}`}
  });
  assert.equal(wrong.status, 204);
  let nextResponse;
  for (let attempt = 0; attempt < 50; attempt++) {
    nextResponse = await fetch(`${baseUrl}/v1/commands/next?tabId=7&url=https%3A%2F%2Fc.liepin.com%2Fresume`, {
      headers: {authorization: `Bearer ${bridge.token}`}
    });
    if (nextResponse.status !== 204) break;
    await new Promise(resolve => setTimeout(resolve, 5));
  }
  assert.equal(nextResponse.status, 200);
  const next = await nextResponse.json();
  assert.equal(next.command.kind, 'fill');
  await fetch(`${baseUrl}/v1/results`, {method: 'POST', headers: {
    authorization: `Bearer ${bridge.token}`, 'content-type': 'application/json'
  }, body: JSON.stringify({requestId: next.requestId, state: 'done', result: {ok: true, status: 'completed'}})});
  const completed = await pending;
  assert.equal(completed.status, 'done');
  const noDuplicate = await fetch(`${baseUrl}/v1/commands/next?tabId=7&url=https%3A%2F%2Fc.liepin.com%2Fresume`, {
    headers: {authorization: `Bearer ${bridge.token}`}
  });
  assert.equal(noDuplicate.status, 204);

  // Independent tabs may be dispatched at the same time.
  const parallelA = client.request({kind: 'inspect', operation: {id: 'A'}},
    {target: {tabId: 9, urlPrefix: 'https://c.liepin.com/'}, timeoutMs: 500});
  const parallelB = client.request({kind: 'inspect', operation: {id: 'B'}},
    {target: {tabId: 10, urlPrefix: 'https://c.liepin.com/'}, timeoutMs: 500});
  await new Promise(resolve => setTimeout(resolve, 10));
  const parallelNext = await Promise.all([9, 10].map(async tabId => {
    const response = await fetch(`${baseUrl}/v1/commands/next?tabId=${tabId}&url=https%3A%2F%2Fc.liepin.com%2Fresume`, {
      headers: {authorization: `Bearer ${bridge.token}`}
    });
    assert.equal(response.status, 200);
    return await response.json();
  }));
  await Promise.all(parallelNext.map(command => fetch(`${baseUrl}/v1/results`, {method: 'POST', headers: {
    authorization: `Bearer ${bridge.token}`, 'content-type': 'application/json'
  }, body: JSON.stringify({requestId: command.requestId, state: 'done', result: {ok: true}})})));
  assert.deepEqual((await Promise.all([parallelA, parallelB])).map(result => result.status), ['done', 'done']);

  // The same tab is serialized, and an explicitly reconciled dispatch frees
  // the tab without replaying the old command.
  const lockedA = client.request({kind: 'inspect', operation: {id: 'locked-a'}},
    {target: {tabId: 11, urlPrefix: 'https://c.liepin.com/'}, timeoutMs: 500});
  const lockedB = client.request({kind: 'inspect', operation: {id: 'locked-b'}},
    {target: {tabId: 11, urlPrefix: 'https://c.liepin.com/'}, timeoutMs: 500});
  await new Promise(resolve => setTimeout(resolve, 10));
  const lockedFirstResponse = await fetch(`${baseUrl}/v1/commands/next?tabId=11&url=https%3A%2F%2Fc.liepin.com%2Fresume`, {
    headers: {authorization: `Bearer ${bridge.token}`}
  });
  assert.equal(lockedFirstResponse.status, 200);
  const lockedFirst = await lockedFirstResponse.json();
  const lockedWhileBusy = await fetch(`${baseUrl}/v1/commands/next?tabId=11&url=https%3A%2F%2Fc.liepin.com%2Fresume`, {
    headers: {authorization: `Bearer ${bridge.token}`}
  });
  assert.equal(lockedWhileBusy.status, 204);
  await client.reconcileUnknown(lockedFirst.requestId, 'Synthetic worker reload reconciliation');
  assert.equal((await lockedA).status, 'unknown');
  const lockedSecondResponse = await fetch(`${baseUrl}/v1/commands/next?tabId=11&url=https%3A%2F%2Fc.liepin.com%2Fresume`, {
    headers: {authorization: `Bearer ${bridge.token}`}
  });
  assert.equal(lockedSecondResponse.status, 200);
  const lockedSecond = await lockedSecondResponse.json();
  await fetch(`${baseUrl}/v1/results`, {method: 'POST', headers: {
    authorization: `Bearer ${bridge.token}`, 'content-type': 'application/json'
  }, body: JSON.stringify({requestId: lockedSecond.requestId, state: 'done', result: {ok: true}})});
  assert.equal((await lockedB).status, 'done');

  const unknownPending = client.request({kind: 'inspect', operation: {id: 'F1'}},
    {target: {tabId: 7, urlPrefix: 'https://c.liepin.com/'}, timeoutMs: 60});
  let unknownCommand;
  for (let attempt = 0; attempt < 50; attempt++) {
    const response = await fetch(`${baseUrl}/v1/commands/next?tabId=7&url=https%3A%2F%2Fc.liepin.com%2Fresume`, {
      headers: {authorization: `Bearer ${bridge.token}`}
    });
    if (response.status !== 204) { unknownCommand = await response.json(); break; }
    await new Promise(resolve => setTimeout(resolve, 5));
  }
  assert.equal(unknownCommand.command.kind, 'inspect');
  const unknown = await unknownPending;
  assert.equal(unknown.status, 'unknown');
  const noReplay = await fetch(`${baseUrl}/v1/commands/next?tabId=7&url=https%3A%2F%2Fc.liepin.com%2Fresume`, {
    headers: {authorization: `Bearer ${bridge.token}`}
  });
  assert.equal(noReplay.status, 204);
  await fetch(`${baseUrl}/v1/results`, {method: 'POST', headers: {
    authorization: `Bearer ${bridge.token}`, 'content-type': 'application/json'
  }, body: JSON.stringify({requestId: unknownCommand.requestId, state: 'done', result: {ok: true}})});

  const readTimeoutClient = new ExtensionBridgeClient({
    baseUrl: 'http://127.0.0.1:43127', token: 'synthetic-read-timeout-token', pollMs: 1,
    defaultTimeoutMs: 8,
    fetchImpl: async url => {
      const pathname = new URL(url).pathname;
      if (pathname === '/v1/commands') {
        return {ok: true, status: 202, json: async () => ({requestId: 'synthetic-read-timeout', state: 'queued'})};
      }
      return {ok: true, status: 200, json: async () => ({
        requestId: 'synthetic-read-timeout', state: 'dispatched', result: null
      })};
    }
  });
  const readTimeoutAdapter = createViaExtensionAdapter({client: readTimeoutClient, targetHost: 'c.liepin.com'});
  await assert.rejects(
    () => readTimeoutAdapter.inspect({id: 'read-timeout', label: 'Synthetic', kind: 'text',
      locator: {selector: '#name'}}),
    error => error.requestId === 'synthetic-read-timeout' && error.settled === false
  );

  const calls = [];
  let pageValue = '';
  const adapter = createViaExtensionAdapter({client: {
    async request(command) {
      calls.push(command.kind);
      if (command.kind === 'identify') return {status: 'done', result: {ok: true, url: 'https://c.liepin.com/resume/edit', title: 'Synthetic'}};
      if (command.kind === 'inspect') return {status: 'done', result: {ok: true, count: 1, kind: 'text', value: pageValue, anchorMatched: true}};
      if (command.kind === 'fill') return {status: 'unknown', requestId: 'synthetic-request', settled: false};
      return {status: 'done', result: {ok: true}};
    },
    async status() { return {state: 'done', result: {ok: true}}; }
  }, targetHost: 'c.liepin.com'});
  const unknownWrite = await adapter.write({id: 'F1', label: 'Synthetic', kind: 'text', before: '', value: 'target', locator: {selector: '#name'}});
  assert.equal(unknownWrite.status, 'unknown');
  assert.equal((await adapter.confirmSettled({requestId: 'synthetic-request'})).settled, true);
  assert.deepEqual(calls, ['fill']);
  await bridge.close();
  console.log('via-extension bridge and page-channel checks passed');
})().catch(error => { console.error(error.stack); process.exitCode = 1; });
