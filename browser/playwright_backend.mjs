/**
 * Playwright session boundary used by the form executor.
 *
 * The executor only depends on this small Page-shaped contract.  A session can
 * come from the Codex browser host (which owns the existing signed-in tab) or
 * from a native Playwright Page supplied by another trusted host.  This module
 * never launches a browser, chooses a profile, reads cookies, or navigates.
 */

import {errors as playwrightErrors} from 'playwright-core';
const requiredPlaywrightMethods = ['evaluate', 'locator'];

function requireFunction(object, name, error) {
  if (!object || typeof object[name] !== 'function') throw new TypeError(error);
}

function assertPlaywrightSurface(playwright) {
  for (const name of requiredPlaywrightMethods)
    requireFunction(playwright, name, `playwright_${name}_required`);
  return playwright;
}

function timeout(options = {}) {
  return options.timeoutMs === undefined ? {} : {timeout: options.timeoutMs};
}

function wrapNativeLocator(locator) {
  const wrapped = {
    locator(selector, options = {}) { return wrapNativeLocator(locator.locator(selector, options)); },
    getByRole(role, options = {}) { return wrapNativeLocator(locator.getByRole(role, options)); },
    getByText(text, options = {}) { return wrapNativeLocator(locator.getByText(text, options)); },
    getByLabel(text, options = {}) { return wrapNativeLocator(locator.getByLabel(text, options)); },
    getByPlaceholder(text, options = {}) { return wrapNativeLocator(locator.getByPlaceholder(text, options)); },
    filter(options = {}) { return wrapNativeLocator(locator.filter(options)); },
    nth(index) { return wrapNativeLocator(locator.nth(index)); },
    first() { return wrapNativeLocator(locator.first()); },
    last() { return wrapNativeLocator(locator.last()); },
    count() { return locator.count(); },
    isVisible() { return locator.isVisible(); },
    isEnabled() { return locator.isEnabled(); },
    innerText(options = {}) { return locator.innerText(timeout(options)); },
    textContent(options = {}) { return locator.textContent(timeout(options)); },
    getAttribute(name, options = {}) { return locator.getAttribute(name, timeout(options)); },
    evaluate(fn, arg, options = {}) { return locator.evaluate(fn, arg, timeout(options)); },
    evaluateAll(fn, arg) { return locator.evaluateAll(fn, arg); },
    fill(value, options = {}) { return locator.fill(value, timeout(options)); },
    click(options = {}) { return locator.click({...options, ...timeout(options)}); },
    check(options = {}) { return locator.check({...options, ...timeout(options)}); },
    uncheck(options = {}) { return locator.uncheck({...options, ...timeout(options)}); },
    setChecked(value, options = {}) { return locator.setChecked(value, {...options, ...timeout(options)}); },
    selectOption(value, options = {}) { return locator.selectOption(value, timeout(options)); },
    press(value, options = {}) { return locator.press(value, timeout(options)); },
    type(value, options = {}) { return locator.type(value, timeout(options)); },
    pressSequentially(value, options = {}) { return locator.pressSequentially(value, timeout(options)); },
    setInputFiles(files, options = {}) { return locator.setInputFiles(files, timeout(options)); },
    waitFor(options = {}) {
      const {timeoutMs, ...rest} = options;
      return locator.waitFor({...rest, ...timeout({timeoutMs})});
    }
  };
  const terminalTimeoutMethods=new Set(['fill','click','check','uncheck','setChecked','selectOption',
    'press','type','pressSequentially','setInputFiles','waitFor','innerText','textContent','getAttribute']);
  return new Proxy(wrapped,{get(target,key){
    if(!terminalTimeoutMethods.has(key))return target[key];
    return async(...args)=>{
      try{return await target[key](...args);}
      catch(error){
        // Native RPC rejection proves only call completion. The executor keeps
        // write results unknown until an independent readback reconciles them.
        if(error instanceof playwrightErrors.TimeoutError)error.settled=true;
        throw error;
      }
    };
  }});
}

function nativePlaywrightSurface(page) {
  requireFunction(page, 'evaluate', 'native_playwright_page_required');
  requireFunction(page, 'locator', 'native_playwright_page_required');
  return {
    evaluate(fn, arg) { return page.evaluate(fn, arg); },
    locator(selector) { return wrapNativeLocator(page.locator(selector)); },
    getByRole(role, options = {}) { return wrapNativeLocator(page.getByRole(role, options)); },
    getByText(text, options = {}) { return wrapNativeLocator(page.getByText(text, options)); },
    getByLabel(text, options = {}) { return wrapNativeLocator(page.getByLabel(text, options)); },
    getByPlaceholder(text, options = {}) { return wrapNativeLocator(page.getByPlaceholder(text, options)); },
    waitForTimeout(ms) { return page.waitForTimeout(ms); },
    waitForLoadState(options = {}) {
      const {state, timeoutMs} = options;
      return page.waitForLoadState(state, timeout({timeoutMs}));
    },
    waitForURL(url, options = {}) {
      const {timeoutMs, ...rest} = options;
      return page.waitForURL(url, {...rest, ...timeout({timeoutMs})});
    }
  };
}

export function createHostedPlaywrightSession(browser, tab) {
  if (!browser || typeof browser.browserId !== 'string' || !browser.browserId)
    throw new TypeError('browser_identity_required');
  if (!tab || typeof tab.id !== 'string' || !tab.id)
    throw new TypeError('tab_identity_required');
  requireFunction(tab, 'url', 'tab_url_required');
  assertPlaywrightSurface(tab.playwright);
  return Object.freeze({
    backend: 'playwright',
    transport: 'hosted-existing-tab',
    browserId: browser.browserId,
    tab
  });
}

export function createNativePlaywrightSession(page, {browserId, tabId} = {}) {
  if (typeof browserId !== 'string' || !browserId || typeof tabId !== 'string' || !tabId)
    throw new TypeError('native_playwright_identity_required');
  const dialogs = [];
  if (typeof page.on === 'function') page.on('dialog', dialog => dialogs.push(dialog));
  const tab = {
    id: tabId,
    playwright: nativePlaywrightSurface(page),
    url: async () => page.url(),
    reload: async () => { await page.reload(); },
    getJsDialog: async () => {
      const dialog = dialogs.at(-1);
      if (!dialog) return undefined;
      return {type: dialog.type(), accept: text => dialog.accept(text), dismiss: () => dialog.dismiss()};
    }
  };
  return Object.freeze({backend: 'playwright', transport: 'native', browserId, tab});
}

export async function identifyPlaywrightSession(session) {
  if (!session || session.backend !== 'playwright') throw new TypeError('playwright_session_required');
  assertPlaywrightSurface(session.tab?.playwright);
  return {
    backend: session.backend,
    transport: session.transport,
    browser_id: session.browserId,
    tab_id: session.tab.id,
    url: await session.tab.url()
  };
}
