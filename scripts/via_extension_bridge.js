'use strict';

/*
 * Loopback command bridge for the Edge MV3 channel. The extension polls this
 * server, executes allowlisted page commands, and posts structured results.
 * Commands for independent tabs may be dispatched in parallel, but the bridge
 * keeps one in-flight command per tab. It never retries an uncertain command.
 */
const http = require('node:http');
const crypto = require('node:crypto');
const {URL} = require('node:url');

const ALLOWED_COMMANDS = new Set(['identify', 'snapshot', 'discover', 'click', 'inspect', 'fill',
  'inspectModule', 'saveModule', 'verifyModule', 'completeRecord', 'navigate']);
const TERMINAL_STATES = new Set(['done', 'failed', 'unknown']);
const MAX_BODY_BYTES = 2 * 1024 * 1024;
const BRIDGE_PROTOCOL_VERSION = '0.1.7';

function json(value) {
  return JSON.stringify(value);
}

function makeToken() {
  return crypto.randomBytes(32).toString('base64url');
}

function normalizeHost(host) {
  if (!['127.0.0.1', 'localhost', '::1'].includes(host)) throw new Error('Bridge host must be loopback');
  return host;
}

function normalizeBridgeBaseUrl(value) {
  const url = new URL(value);
  if (url.protocol !== 'http:' || !['127.0.0.1', 'localhost', '[::1]'].includes(url.hostname) ||
      url.username || url.password) throw new Error('Bridge client URL must be an unauthenticated loopback HTTP URL');
  return url.toString().replace(/\/$/, '');
}

function writeJson(response, status, value) {
  const body = json(value);
  response.writeHead(status, {
    'content-type': 'application/json; charset=utf-8',
    'content-length': Buffer.byteLength(body),
    'cache-control': 'no-store',
    'access-control-allow-origin': '*',
    'access-control-allow-headers': 'authorization, content-type',
    'access-control-allow-methods': 'GET, POST, OPTIONS'
  });
  response.end(body);
}

function noContent(response) {
  response.writeHead(204, {'cache-control': 'no-store', 'access-control-allow-origin': '*'});
  response.end();
}

function readBody(request, maxBytes = MAX_BODY_BYTES) {
  return new Promise((resolve, reject) => {
    let size = 0;
    const chunks = [];
    request.on('data', chunk => {
      size += chunk.length;
      if (size > maxBytes) {
        reject(Object.assign(new Error('Request body is too large'), {statusCode: 413}));
        request.destroy();
        return;
      }
      chunks.push(chunk);
    });
    request.on('end', () => {
      try { resolve(JSON.parse(Buffer.concat(chunks).toString('utf8') || '{}')); }
      catch { reject(Object.assign(new Error('Request body must be JSON'), {statusCode: 400})); }
    });
    request.on('error', reject);
  });
}

function bearer(request) {
  const value = String(request.headers.authorization || '');
  return value.startsWith('Bearer ') ? value.slice(7) : '';
}

function targetMatches(command, query) {
  const target = command.payload.target || {};
  const get = name => typeof query?.get === 'function' ? query.get(name) : query?.[name];
  if (target.tabId != null && String(target.tabId) !== String(get('tabId') || '')) return false;
  const currentUrl = String(get('url') || '');
  const kind = String(command.payload.payload?.kind || '');
  const isNavigate = kind === 'navigate';
  if (currentUrl === 'about:blank' && !isNavigate) return false;
  // Navigation is the recovery boundary for a blank or previously blocked
  // tab. Its destination is validated by dispatch(), so matching it against
  // the tab's *current* URL would strand the command in the queue.
  if (!isNavigate) {
    if (target.urlPrefix && !currentUrl.startsWith(String(target.urlPrefix))) return false;
    if (Array.isArray(target.urlPrefixes) && target.urlPrefixes.length &&
        !target.urlPrefixes.some(prefix => currentUrl.startsWith(String(prefix)))) return false;
  }
  return true;
}

function validCommand(payload) {
  return payload && typeof payload === 'object' && ALLOWED_COMMANDS.has(payload.kind);
}

function queryTabKey(query) {
  const tabId = typeof query?.get === 'function' ? query.get('tabId') : query?.tabId;
  return tabId ? `tab:${String(tabId)}` : null;
}

function currentCommand(entry) {
  return {requestId: entry.requestId, state: entry.state, result: entry.result};
}

function createBridgeServer({host = '127.0.0.1', port = 43127, token = makeToken(),
  maxBodyBytes = MAX_BODY_BYTES} = {}) {
  normalizeHost(host);
  if (!Number.isInteger(port) || port < 0 || port > 65535) throw new RangeError('Invalid bridge port');
  if (typeof token !== 'string' || token.length < 16) throw new Error('Bridge token must be at least 16 characters');
  if (!Number.isInteger(maxBodyBytes) || maxBodyBytes < 1024) throw new RangeError('Invalid bridge body limit');
  const commands = new Map();
  const queue = [];
  const server = http.createServer(async (request, response) => {
    if (request.method === 'OPTIONS') {
      response.writeHead(204, {'access-control-allow-origin': '*',
        'access-control-allow-headers': 'authorization, content-type',
        'access-control-allow-methods': 'GET, POST, OPTIONS'});
      response.end();
      return;
    }
    if (bearer(request) !== token) { writeJson(response, 401, {error: 'Unauthorized'}); return; }
    const parsed = new URL(request.url || '/', `http://${host}`);
    try {
      if (request.method === 'GET' && parsed.pathname === '/v1/health') {
        writeJson(response, 200, {
          ok: true,
          service: 'edge-via-extension',
          protocolVersion: BRIDGE_PROTOCOL_VERSION,
          capabilities: {reconcile: true, navigateFromBlank: true}
        }); return;
      }
      if (request.method === 'POST' && parsed.pathname === '/v1/commands') {
        const payload = await readBody(request, maxBodyBytes);
        if (!validCommand(payload.command)) { writeJson(response, 400, {error: 'Command is not allowlisted'}); return; }
        const id = crypto.randomUUID();
        const entry = {requestId: id, payload: payload.command, target: payload.target || {},
          state: 'queued', createdAt: Date.now(), updatedAt: Date.now(), result: null};
        commands.set(id, entry); queue.push(id);
        writeJson(response, 202, {requestId: id, state: entry.state}); return;
      }
      if (request.method === 'GET' && parsed.pathname === '/v1/commands/next') {
        const tabKey = queryTabKey(parsed.searchParams);
        // The extension may have more than one polling worker, and a service
        // worker reload loses its in-memory lock. Keep the safety lock here so
        // two commands can never be assigned to the same tab concurrently.
        if (tabKey && Array.from(commands.values()).some(entry =>
          entry.state === 'dispatched' && entry.dispatchTabKey === tabKey)) {
          noContent(response); return;
        }
        const index = queue.findIndex(id => {
          const entry = commands.get(id);
          return entry && entry.state === 'queued' && targetMatches({payload: entry}, parsed.searchParams);
        });
        if (index < 0) { noContent(response); return; }
        const id = queue.splice(index, 1)[0];
        const entry = commands.get(id);
        entry.state = 'dispatched'; entry.dispatchTabKey = tabKey; entry.updatedAt = Date.now();
        writeJson(response, 200, {requestId: id, state: entry.state, command: entry.payload}); return;
      }
      if (request.method === 'POST' && parsed.pathname === '/v1/results') {
        const payload = await readBody(request, maxBodyBytes);
        const entry = commands.get(payload.requestId);
        if (!entry) { writeJson(response, 404, {error: 'Unknown requestId'}); return; }
        if (!TERMINAL_STATES.has(payload.state)) { writeJson(response, 400, {error: 'Invalid terminal state'}); return; }
        if (entry.state === 'done' || entry.state === 'failed' || entry.state === 'unknown') {
          writeJson(response, 200, {requestId: entry.requestId, state: entry.state, result: entry.result}); return;
        }
        entry.state = payload.state;
        entry.result = payload.result ?? null;
        entry.updatedAt = Date.now();
        writeJson(response, 200, {requestId: entry.requestId, state: entry.state}); return;
      }
      if (request.method === 'POST' && parsed.pathname === '/v1/reconcile') {
        const payload = await readBody(request, maxBodyBytes);
        const entry = commands.get(payload.requestId);
        if (!entry) { writeJson(response, 404, {error: 'Unknown requestId'}); return; }
        if (entry.state === 'queued') {
          writeJson(response, 409, {error: 'Queued commands must not be reconciled'}); return;
        }
        if (entry.state === 'dispatched') {
          // This endpoint only abandons an explicitly unresolved dispatch. It
          // never claims success and never puts the command back in the queue.
          entry.state = 'unknown';
          entry.result = {ok: false, status: 'unknown', error: {
            code: 'dispatch-reconciled', message: String(payload.reason || 'Dispatch was reconciled as unknown')
          }};
          entry.updatedAt = Date.now();
        }
        writeJson(response, 200, currentCommand(entry)); return;
      }
      const statusMatch = parsed.pathname.match(/^\/v1\/status\/([^/]+)$/);
      if (request.method === 'GET' && statusMatch) {
        const entry = commands.get(statusMatch[1]);
        if (!entry) { writeJson(response, 404, {error: 'Unknown requestId'}); return; }
        writeJson(response, 200, {requestId: entry.requestId, state: entry.state, result: entry.result,
          createdAt: entry.createdAt, updatedAt: entry.updatedAt}); return;
      }
      writeJson(response, 404, {error: 'Not found'});
    } catch (error) {
      writeJson(response, error.statusCode || 500, {error: error.message || 'Bridge error'});
    }
  });
  return {
    server,
    token,
    async listen() {
      await new Promise((resolve, reject) => {
        const onError = error => { server.off('listening', onListening); reject(error); };
        const onListening = () => { server.off('error', onError); resolve(); };
        server.once('error', onError); server.once('listening', onListening); server.listen(port, host);
      });
      return server.address();
    },
    async close() { if (server.listening) await new Promise(resolve => server.close(resolve)); },
    _commands: commands
  };
}

class ExtensionBridgeClient {
  constructor({baseUrl = 'http://127.0.0.1:43127', token, fetchImpl = globalThis.fetch,
    pollMs = 100, defaultTimeoutMs = 120000, defaultTarget} = {}) {
    if (!token || typeof token !== 'string') throw new Error('Bridge token is required');
    this.baseUrl = normalizeBridgeBaseUrl(baseUrl);
    this.token = token;
    this.fetchImpl = fetchImpl;
    this.pollMs = pollMs;
    this.defaultTimeoutMs = defaultTimeoutMs;
    this.defaultTarget = defaultTarget;
    if (typeof fetchImpl !== 'function') throw new Error('Node fetch is required for bridge client');
  }
  async request(command, {target = this.defaultTarget, timeoutMs = this.defaultTimeoutMs} = {}) {
    if (!validCommand(command)) throw new Error('Command is not allowlisted');
    const created = await this.#fetch('/v1/commands', {method: 'POST', body: json({command, target})});
    const requestId = created.requestId;
    const deadline = Date.now() + timeoutMs;
    while (Date.now() < deadline) {
      let status;
      try { status = await this.status(requestId); }
      catch (error) {
        error.requestId = requestId;
        error.settled = false;
        throw error;
      }
      if (TERMINAL_STATES.has(status.state)) return {requestId, status: status.state, result: status.result};
      await new Promise(resolve => setTimeout(resolve, this.pollMs));
    }
    // Do not cancel or resubmit: the extension may still be operating on the
    // page. The caller must reconcile this requestId before continuing.
    return {requestId, status: 'unknown', settled: false, reason: 'bridge-timeout'};
  }
  async status(requestId) { return await this.#fetch('/v1/status/' + encodeURIComponent(requestId)); }
  async health() { return await this.#fetch('/v1/health'); }
  async reconcileUnknown(requestId, reason = 'Dispatch was reconciled after the worker was reloaded') {
    return await this.#fetch('/v1/reconcile', {method: 'POST', body: json({requestId, reason})});
  }
  async #fetch(path, options = {}) {
    const response = await this.fetchImpl(this.baseUrl + path, {method: 'GET', ...options,
      headers: {accept: 'application/json', 'content-type': 'application/json',
        authorization: `Bearer ${this.token}`, ...(options.headers || {})}});
    const body = await response.json().catch(() => ({}));
    if (!response.ok) throw Object.assign(new Error(body.error || `Bridge HTTP ${response.status}`), {settled: true});
    return body;
  }
}

function unsafeOperation(operation) {
  const sensitive = /证件号码|证件号|身份证|护照|身份号码|national.?id|passport|social.?security/i;
  if (sensitive.test(String(operation?.label || '')) || sensitive.test(String(operation?.anchor?.label || ''))) {
    throw new Error('Identity-document fields cannot enter the extension command channel');
  }
  if (['password', 'file'].includes(String(operation?.kind || '').toLowerCase())) {
    throw new Error('Password and file controls cannot enter the extension command channel');
  }
  return JSON.parse(JSON.stringify(operation));
}

function bridgeError(message, settled) {
  const error = new Error(message);
  error.settled = settled;
  return error;
}

function createViaExtensionAdapter({client, browser = 'Edge', targetHost, target} = {}) {
  if (!client || typeof client.request !== 'function') throw new Error('A bridge client is required');
  const call = async (command, options = {}) => await client.request(command,
    target && options.target === undefined ? {...options, target} : options);
  const unwrapRead = async (command, options) => {
    const response = await call(command, options);
    if (response.status === 'unknown') {
      const error = bridgeError('Extension read did not settle', false);
      error.requestId = response.requestId;
      error.response = response;
      throw error;
    }
    if (response.status !== 'done' || !response.result?.ok) {
      const error = bridgeError(response.result?.error?.message || 'Extension read failed', true);
      error.requestId = response.requestId;
      error.response = response;
      throw error;
    }
    return response.result;
  };
  return {
    async identify(options = {}) {
      const result = await unwrapRead({kind: 'identify'}, options);
      if (targetHost && !new URL(result.url).hostname.endsWith(targetHost)) throw bridgeError('Extension target host changed', true);
      return {browser, url: result.url, title: result.title};
    },
    async discover(options = {}) { return await unwrapRead({kind: 'discover'}, options); },
    async click(target, options = {}) {
      const selector = typeof target?.locator === 'string' ? target.locator : target?.locator?.selector;
      const expectedText = target?.expectedText ?? target?.text;
      if (!selector || typeof expectedText !== 'string' || !expectedText.trim()) {
        throw new Error('A click target needs a selector and expected text');
      }
      const clickTarget = {locator: {selector}, expectedText};
      if (target?.purpose) clickTarget.purpose = target.purpose;
      const response = await call({kind: 'click', target: clickTarget}, options);
      if (response.status === 'unknown') return {status: 'unknown', requestId: response.requestId, settled: false};
      if (response.status === 'done' && response.result?.status === 'clicked') {
        return {status: 'clicked', text: response.result.text, locator: response.result.locator};
      }
      return {status: 'failed', noEffect: true, settled: response.status === 'failed',
        error: response.result?.error};
    },
    async inspect(operation, options = {}) {
      return await unwrapRead({kind: 'inspect', operation: unsafeOperation(operation)}, options);
    },
    async write(operation, options = {}) {
      const response = await call({kind: 'fill', operations: [unsafeOperation(operation)]}, options);
      if (response.status === 'unknown') return {status: 'unknown', requestId: response.requestId, settled: false};
      const result = response.result?.operations?.[0];
      if (response.status === 'done' && result?.status === 'written') return {status: 'written'};
      if (response.status === 'failed' && result?.status === 'conflict') return {status: 'failed', noEffect: true, settled: true};
      if (response.status === 'done' && result?.status === 'conflict') return {status: 'failed', noEffect: true, settled: true};
      if (response.status === 'done' && result?.status === 'failed') return {status: 'failed', noEffect: true, settled: true};
      return {status: 'unknown', requestId: response.requestId, settled: false};
    },
    async inspectModule(module, options = {}) { return await unwrapRead({kind: 'inspectModule', module}, options); },
    async saveModule(module, options = {}) {
      const response = await call({kind: 'saveModule', module}, options);
      if (response.status === 'unknown') return {status: 'unknown', requestId: response.requestId, settled: false};
      if (response.status === 'done' && response.result?.status === 'clicked') return {status: 'saved', settled: true};
      if (response.status === 'done' && response.result?.status === 'failed') {
        return {status: 'validation-failed', settled: true, missingRequired: response.result?.missingRequired || []};
      }
      if (response.status === 'failed') return {status: 'validation-failed', settled: true, missingRequired: response.result?.missingRequired || []};
      return {status: 'unknown', requestId: response.requestId, settled: false};
    },
    async verifyModule(module, options = {}) {
      const response = await call({kind: 'verifyModule', module}, options);
      if (response.status === 'unknown') return {saved: false};
      return {saved: response.status === 'done' && response.result?.saved === true,
        evidence: response.result?.evidence};
    },
    async completeRecord(operation, {recordRef, ...options} = {}) {
      const response = await call({kind: 'completeRecord', recordRef, operation: unsafeOperation(operation)}, options);
      if (response.status === 'unknown') return {status: 'unknown', requestId: response.requestId, settled: false};
      if (response.status === 'done' && response.result?.status === 'written') return {status: 'written'};
      return {status: 'unknown', requestId: response.requestId, settled: false};
    },
    async confirmSettled(pendingCall) {
      if (!pendingCall?.requestId) return {settled: false};
      const status = await client.status(pendingCall.requestId);
      if (TERMINAL_STATES.has(status.state)) return {settled: true, evidence: {requestId: pendingCall.requestId, state: status.state}};
      return {settled: false};
    },
    async navigate(url, options = {}) {
      const response = await call({kind: 'navigate', url}, options);
      if (response.status === 'unknown') return {status: 'unknown', requestId: response.requestId, settled: false};
      if (response.status === 'done' && response.result?.status === 'navigated') return response.result;
      return {status: 'failed', noEffect: true, settled: true};
    },
    checkpoint: async () => {}
  };
}

if (require.main === module) {
  const args = process.argv.slice(2);
  const get = name => { const index = args.indexOf(name); return index >= 0 ? args[index + 1] : undefined; };
  const port = Number(get('--port') || 43127);
  const supplied = get('--token');
  const quiet = args.includes('--quiet');
  const bridge = createBridgeServer({port, token: supplied || makeToken()});
  bridge.listen().then(address => {
    if (!quiet) process.stdout.write(json({endpoint: `http://127.0.0.1:${address.port}`, token: bridge.token}) + '\n');
  }).catch(error => { process.stderr.write(error.stack + '\n'); process.exitCode = 1; });
}

module.exports = {createBridgeServer, ExtensionBridgeClient, createViaExtensionAdapter,
  makeToken, ALLOWED_COMMANDS, BRIDGE_PROTOCOL_VERSION, targetMatches};
