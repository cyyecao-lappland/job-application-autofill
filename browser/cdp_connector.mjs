/**
 * Native Playwright connection to an Edge instance that already exposes CDP.
 *
 * This boundary is intentionally read-only until a caller passes the selected
 * session to the form executor. It never launches Edge, navigates, or reads
 * browser storage. Page selection fails closed when it is ambiguous.
 */
import {chromium as defaultChromium} from 'playwright-core';
import {createNativePlaywrightSession} from './playwright_backend.mjs';

export const DEFAULT_CDP_ENDPOINT = 'http://127.0.0.1:9333';

function requireText(value, error) {
  if (typeof value !== 'string' || !value.trim()) throw new TypeError(error);
  return value.trim();
}

async function cdpPageId(page) {
  const context = page.context?.();
  if (!context || typeof context.newCDPSession !== 'function')
    throw new TypeError('cdp_page_identity_unavailable');
  const cdp = await context.newCDPSession(page);
  try {
    const result = await cdp.send('Target.getTargetInfo');
    return requireText(result?.targetInfo?.targetId, 'cdp_page_identity_unavailable');
  } finally {
    await cdp.detach();
  }
}

export async function describeCdpPages(browser) {
  const descriptions = [];
  for (const context of browser.contexts()) {
    for (const page of context.pages()) {
      descriptions.push(Object.freeze({
        pageId: await cdpPageId(page),
        title: await page.title(),
        url: page.url(),
        page
      }));
    }
  }
  return descriptions;
}

export function selectCdpPage(pages, {exactUrl, pageId} = {}) {
  if (exactUrl !== undefined && pageId !== undefined)
    throw new TypeError('choose_exact_url_or_page_id');
  let matches = pages;
  if (exactUrl !== undefined) {
    const wanted = requireText(exactUrl, 'exact_url_required');
    matches = pages.filter(candidate => candidate.url === wanted);
  } else if (pageId !== undefined) {
    const wanted = requireText(pageId, 'page_id_required');
    matches = pages.filter(candidate => candidate.pageId === wanted);
  }
  if (matches.length === 0) {
    const error = new Error('cdp_page_not_found');
    error.pages = pages.map(({page, ...description}) => description);
    throw error;
  }
  if (matches.length !== 1) {
    const error = new Error('cdp_page_selection_ambiguous');
    error.pages = pages.map(({page, ...description}) => description);
    throw error;
  }
  return matches[0];
}

export async function connectEdgeCdp({
  endpoint = DEFAULT_CDP_ENDPOINT,
  exactUrl,
  pageId,
  chromium = defaultChromium,
  timeoutMs = 10_000
} = {}) {
  endpoint = requireText(endpoint, 'cdp_endpoint_required');
  if (!chromium || typeof chromium.connectOverCDP !== 'function')
    throw new TypeError('chromium_connect_over_cdp_required');

  const browser = await chromium.connectOverCDP(endpoint, {timeout: timeoutMs});
  let disconnected = false;
  try {
    const pages = await describeCdpPages(browser);
    const selected = selectCdpPage(pages, {exactUrl, pageId});
    const browserId = `edge-cdp:${endpoint}`;
    const session = createNativePlaywrightSession(selected.page, {
      browserId,
      tabId: selected.pageId
    });
    return Object.freeze({
      endpoint,
      browserId,
      pages: Object.freeze(pages.map(({page, ...description}) => Object.freeze(description))),
      session,
      async activate() { await selected.page.bringToFront(); },
      async closePage() { await selected.page.close(); },
      async closeConnection() {
        if (disconnected) return;
        disconnected = true;
        await browser.close();
      }
    });
  } catch (error) {
    await browser.close();
    throw error;
  }
}
