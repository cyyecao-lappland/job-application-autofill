'use strict';

/*
 * Small dependency-free CDP client for an already running Chromium/Edge
 * instance. It deliberately attaches only to an explicitly supplied loopback
 * DevTools target; it never launches a browser or changes its flags.
 */
const {URL} = require('node:url');

function isLoopbackHost(hostname) {
  const host = String(hostname || '').toLowerCase();
  return host === 'localhost' || host === '127.0.0.1' || host === '[::1]' || host === '::1';
}

function assertLoopbackHttpEndpoint(endpoint) {
  const url = new URL(endpoint);
  if (!['http:', 'https:'].includes(url.protocol) || !isLoopbackHost(url.hostname) ||
      url.username || url.password) {
    throw new Error('CDP discovery endpoint must be an unauthenticated loopback HTTP URL');
  }
  return url;
}

function assertLoopbackWebSocketEndpoint(endpoint) {
  const url = new URL(endpoint);
  if (!['ws:', 'wss:'].includes(url.protocol) || !isLoopbackHost(url.hostname) ||
      url.username || url.password) {
    throw new Error('CDP target must be an unauthenticated loopback WebSocket URL');
  }
  return url;
}

async function listCdpTargets({endpoint = 'http://127.0.0.1:9222', fetchImpl = globalThis.fetch,
  timeoutMs = 5000} = {}) {
  if (typeof fetchImpl !== 'function') throw new Error('Node fetch is required for CDP discovery');
  const base = assertLoopbackHttpEndpoint(endpoint);
  const url = new URL('/json/list', base);
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), timeoutMs);
  try {
    const response = await fetchImpl(url, {method: 'GET', signal: controller.signal,
      headers: {'accept': 'application/json'}});
    if (!response.ok) throw new Error(`CDP discovery returned HTTP ${response.status}`);
    const rows = await response.json();
    if (!Array.isArray(rows)) throw new Error('CDP discovery returned a non-list response');
    return rows.filter(row => row && row.type === 'page' && typeof row.webSocketDebuggerUrl === 'string');
  } finally {
    clearTimeout(timer);
  }
}

function addEventListener(socket, name, listener) {
  if (typeof socket.addEventListener === 'function') socket.addEventListener(name, listener);
  else if (typeof socket.on === 'function') socket.on(name, listener);
  else throw new Error('WebSocket implementation does not expose event listeners');
}

function removeEventListener(socket, name, listener) {
  if (typeof socket.removeEventListener === 'function') socket.removeEventListener(name, listener);
  else if (typeof socket.off === 'function') socket.off(name, listener);
  else if (typeof socket.removeListener === 'function') socket.removeListener(name, listener);
}

function errorWithCode(message, code, extra = {}) {
  const error = new Error(message);
  error.code = code;
  Object.assign(error, extra);
  return error;
}

function connectCdpTarget(target, {WebSocketImpl = globalThis.WebSocket, timeoutMs = 5000} = {}) {
  const websocketUrl = typeof target === 'string' ? target : target?.webSocketDebuggerUrl;
  if (!websocketUrl) throw new Error('A CDP target webSocketDebuggerUrl is required');
  assertLoopbackWebSocketEndpoint(websocketUrl);
  if (typeof WebSocketImpl !== 'function') {
    throw new Error('This Node runtime has no WebSocket implementation; use Node 22+ or inject one');
  }

  const socket = new WebSocketImpl(websocketUrl);
  const pending = new Map();
  const listeners = new Map();
  let nextId = 1;
  let closed = false;
  let opened = false;
  let openTimer;
  let resolveOpen;
  let rejectOpen;

  const openPromise = new Promise((resolve, reject) => {
    resolveOpen = resolve;
    rejectOpen = reject;
  });

  const rejectAll = error => {
    for (const {reject} of pending.values()) reject(error);
    pending.clear();
  };

  const onOpen = () => {
    if (opened || closed) return;
    opened = true;
    clearTimeout(openTimer);
    resolveOpen();
  };
  const onError = event => {
    const detail = event?.error?.message || event?.message || 'CDP WebSocket error';
    const error = errorWithCode(detail, 'CDP_CONNECTION_ERROR', {settled: false});
    if (!opened) rejectOpen(error);
    rejectAll(error);
  };
  const onClose = () => {
    if (closed) return;
    closed = true;
    clearTimeout(openTimer);
    const error = errorWithCode('CDP WebSocket closed', 'CDP_CONNECTION_CLOSED', {settled: false});
    if (!opened) rejectOpen(error);
    rejectAll(error);
  };
  const onMessage = event => {
    let message;
    try {
      const data = event?.data ?? event;
      message = JSON.parse(typeof data === 'string' ? data : String(data));
    } catch {
      return;
    }
    if (message.id != null) {
      const waiter = pending.get(message.id);
      if (!waiter) return;
      pending.delete(message.id);
      if (message.error) {
        waiter.reject(errorWithCode(message.error.message || 'CDP command failed',
          message.error.code || 'CDP_PROTOCOL_ERROR', {data: message.error, settled: true}));
      } else waiter.resolve(message.result);
      return;
    }
    if (!message.method) return;
    for (const listener of listeners.get(message.method) || []) listener(message.params);
  };

  addEventListener(socket, 'open', onOpen);
  addEventListener(socket, 'error', onError);
  addEventListener(socket, 'close', onClose);
  addEventListener(socket, 'message', onMessage);
  openTimer = setTimeout(() => {
    const error = errorWithCode('CDP WebSocket open timed out', 'CDP_OPEN_TIMEOUT', {settled: false});
    if (!opened) rejectOpen(error);
    try { socket.close(); } catch { /* best effort */ }
  }, timeoutMs);

  const client = {
    async send(method, params = {}) {
      await openPromise;
      if (closed) throw errorWithCode('CDP WebSocket is closed', 'CDP_CONNECTION_CLOSED', {settled: false});
      const id = nextId++;
      return await new Promise((resolve, reject) => {
        pending.set(id, {resolve, reject});
        try { socket.send(JSON.stringify({id, method, params})); }
        catch (error) {
          pending.delete(id);
          error.settled = false;
          reject(error);
        }
      });
    },
    on(method, listener) {
      if (typeof listener !== 'function') throw new TypeError('CDP event listener must be a function');
      if (!listeners.has(method)) listeners.set(method, new Set());
      listeners.get(method).add(listener);
      return client;
    },
    off(method, listener) {
      listeners.get(method)?.delete(listener);
      return client;
    },
    async close() {
      if (closed) return;
      closed = true;
      clearTimeout(openTimer);
      rejectAll(errorWithCode('CDP client closed', 'CDP_CONNECTION_CLOSED', {settled: false}));
      try { socket.close(); } catch { /* best effort */ }
    },
    target: typeof target === 'string' ? {webSocketDebuggerUrl: target} : {...target}
  };
  return {client, opened: openPromise};
}

async function connectCdpTargetByUrl({endpoint = 'http://127.0.0.1:9222', url,
  WebSocketImpl = globalThis.WebSocket, fetchImpl = globalThis.fetch, timeoutMs = 5000} = {}) {
  if (!url) throw new Error('A target URL is required');
  const targets = await listCdpTargets({endpoint, fetchImpl, timeoutMs});
  const target = targets.find(row => row.url === url) ||
    targets.find(row => row.url?.startsWith(url));
  if (!target) throw errorWithCode('No matching CDP page target found', 'CDP_TARGET_NOT_FOUND');
  const connection = connectCdpTarget(target, {WebSocketImpl, timeoutMs});
  await connection.opened;
  return connection.client;
}

module.exports = {listCdpTargets, connectCdpTarget, connectCdpTargetByUrl,
  isLoopbackHost};
