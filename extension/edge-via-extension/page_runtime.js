'use strict';

/*
 * This file is serialized into the target page by scripting.executeScript.
 * Keep the entry point self-contained: it must not rely on extension APIs or
 * imports after it crosses into the page's MAIN world.
 */
function runPageCommand(command) {
  const SENSITIVE = /证件号码|证件号|身份证|护照|身份号码|national.?id|passport|social.?security/i;
  const DANGEROUS_BUTTON = /提交|投递|申请职位|submit|apply|confirm/i;
  const DANGEROUS_CLICK = /提交|投递|申请|确认|保存|下一步|预览|发布|删除|取消|submit|apply|confirm|save|next|preview|publish|delete|cancel/i;
  const SAFE_CLICK = /^(?:编辑|去补充|展开)(?:\s*[:：]?\s*[\u4e00-\u9fffA-Za-z0-9·（）()/_-]{0,24})?$/;
  const INTERACTIVE_SELECTOR = 'button,a[href],[role="button"],[role="link"],[role="option"],[role="combobox"],[class*="option"],[tabindex]:not([tabindex="-1"]),input[type="button"],input[type="submit"],summary,[onclick]';
  const text = value => String(value == null ? '' : value).replace(/\s+/g, ' ').trim();
  const sameText = (a, b) => text(a) === text(b);
  const page = document;

  const failure = (code, message, extra = {}) => ({
    ok: false,
    status: 'failed',
    error: {code, message},
    ...extra
  });
  const isVisible = element => {
    if (!element || element.nodeType !== 1) return false;
    const view = element.ownerDocument?.defaultView;
    const style = view?.getComputedStyle?.(element);
    if (style && (style.display === 'none' || style.visibility === 'hidden')) return false;
    if (element.getAttribute?.('aria-hidden') === 'true') return false;
    return true;
  };
  const all = (root, selector) => {
    try { return Array.from(root.querySelectorAll(selector)).filter(isVisible); }
    catch { return null; }
  };
  const cssEscape = value => {
    const raw = String(value);
    try {
      if (globalThis.CSS?.escape) return globalThis.CSS.escape(raw);
    } catch {}
    return raw.replace(/[^a-zA-Z0-9_-]/g, character => `\\${character}`);
  };
  const elementText = element => text(element?.innerText) || text(element?.textContent) ||
    text(element?.getAttribute?.('aria-label')) || text(element?.getAttribute?.('title')) ||
    text(element?.getAttribute?.('placeholder'));
  const isSafeClickText = value => {
    const label = text(value);
    return Boolean(label) && !DANGEROUS_CLICK.test(label) && SAFE_CLICK.test(label);
  };
  const isOptionElement = element => Boolean(element?.matches?.('[role="option"], [class*="option"]') ||
    element?.closest?.('[role="option"], [class*="option"]'));
  const isComboboxElement = element => Boolean(element?.matches?.('[role="combobox"]') ||
    element?.getAttribute?.('role') === 'combobox');
  const uniqueSelector = (element, options = {}) => {
    const deadline = Number(options.deadline || 0);
    const expired = () => deadline > 0 && Date.now() >= deadline;
    const isUnique = selector => {
      if (expired()) return false;
      try { return page.querySelectorAll(selector).length === 1; }
      catch { return false; }
    };
    if (expired()) return null;
    const id = element.getAttribute?.('id');
    if (id && isUnique(`#${cssEscape(id)}`)) return `#${cssEscape(id)}`;
    for (const attribute of ['data-testid', 'data-test', 'data-qa', 'data-cy', 'data-key', 'data-id', 'name', 'aria-label']) {
      const value = element.getAttribute?.(attribute);
      if (value && isUnique(`[${attribute}=${cssEscape(value)}]`)) return `[${attribute}=${cssEscape(value)}]`;
    }
    const classes = String(element.getAttribute?.('class') || '').split(/\s+/)
      .filter(Boolean).slice(0, 4).map(cssEscape);
    if (classes.length) {
      const classSelector = `${String(element.tagName || '').toLowerCase()}.${classes.join('.')}`;
      if (isUnique(classSelector)) return classSelector;
    }
    const segments = [];
    let current = element;
    for (let depth = 0; current && current.nodeType === 1 && depth < 12; depth += 1) {
      if (expired()) return null;
      let segment = String(current.tagName || '').toLowerCase();
      const currentId = current.getAttribute?.('id');
      if (currentId) segment += `#${cssEscape(currentId)}`;
      const parent = current.parentElement;
      if (parent?.children) {
        const siblings = Array.from(parent.children).filter(child => child.tagName === current.tagName);
        if (siblings.length > 1) segment += `:nth-of-type(${siblings.indexOf(current) + 1})`;
      }
      segments.unshift(segment);
      const candidate = segments.join(' > ');
      if (isUnique(candidate)) return candidate;
      current = parent;
    }
    const fallback = segments.join(' > ');
    return fallback && isUnique(fallback) ? fallback : null;
  };
  const unique = (root, selector, label) => {
    if (!selector || typeof selector !== 'string') return {error: failure('locator-missing', `${label} requires a CSS selector`)};
    const matches = all(root, selector);
    if (!matches) return {error: failure('locator-invalid', `Invalid selector for ${label}`)};
    if (matches.length !== 1) {
      return {error: failure(matches.length ? 'locator-ambiguous' : 'locator-missing',
        `${label} matched ${matches.length} visible elements`, {count: matches.length})};
    }
    return {element: matches[0]};
  };
  const labelFor = element => {
    if (!element) return '';
    if (element.labels?.length) return text(Array.from(element.labels).map(label => label.textContent).join(' '));
    const labelledBy = element.getAttribute?.('aria-labelledby');
    if (labelledBy) return text(labelledBy.split(/\s+/).map(id => page.getElementById(id)?.textContent || '').join(' '));
    const explicit = element.getAttribute?.('id');
    if (explicit) {
      const label = page.querySelector?.(`label[for="${CSS.escape(explicit)}"]`);
      if (label) return text(label.textContent);
    }
    const parentLabel = element.closest?.('label');
    return text(parentLabel?.textContent || element.getAttribute?.('aria-label') ||
      element.getAttribute?.('placeholder') || element.getAttribute?.('name') || '');
  };
  const findByLabel = (root, wanted) => {
    const labels = all(root, 'label') || [];
    const matched = labels.filter(label => sameText(label.textContent, wanted));
    const controls = [];
    for (const label of matched) {
      const id = label.getAttribute('for');
      if (id) {
        const control = page.getElementById(id);
        if (control && isVisible(control)) controls.push(control);
      } else {
        controls.push(...(all(label, 'input,textarea,select,[contenteditable="true"]') || []));
      }
    }
    const uniqueControls = Array.from(new Set(controls));
    if (uniqueControls.length !== 1) {
      return {error: failure(uniqueControls.length ? 'locator-ambiguous' : 'locator-missing',
        `Label ${wanted} resolved to ${uniqueControls.length} controls`, {count: uniqueControls.length})};
    }
    return {element: uniqueControls[0]};
  };
  const resolve = (operation, override = {}) => {
    const locator = typeof operation?.locator === 'string' ? {selector: operation.locator} :
      {...(operation?.locator || {}), ...override};
    let root = page;
    if (locator.recordRef) {
      const recordSelector = locator.recordSelector || locator.scopeSelector;
      const recordAttribute = locator.recordRefAttribute || 'data-record-id';
      const records = recordSelector ? (all(page, recordSelector) || []).filter(element =>
        element.getAttribute?.(recordAttribute) === String(locator.recordRef)) : [];
      if (records.length !== 1) {
        return {error: failure(records.length ? 'record-ambiguous' : 'record-missing',
          `Record reference ${String(locator.recordRef)} matched ${records.length} records`, {count: records.length})};
      }
      root = records[0];
    } else if (locator.scopeSelector) {
      const scoped = unique(page, locator.scopeSelector, `${operation.label || 'field'} scope`);
      if (scoped.error) return scoped;
      root = scoped.element;
    }
    let anchorMatched = true;
    if (locator.anchorSelector) {
      const anchor = unique(root, locator.anchorSelector, `${operation.label || 'field'} anchor`);
      if (anchor.error) return anchor;
      const wanted = locator.anchorText ?? operation.anchor?.value;
      anchorMatched = wanted == null || sameText(anchor.element.textContent, wanted);
      if (!anchorMatched) return {error: failure('anchor-mismatch', `Anchor text did not match for ${operation.label || 'field'}`)};
    }
    let found;
    if (locator.selector) found = unique(root, locator.selector, operation.label || 'field');
    else if (locator.label) found = findByLabel(root, locator.label);
    else return {error: failure('locator-missing', `${operation.label || 'field'} has no observed selector or label`)};
    if (found.error) return found;
    return {element: found.element, root, anchorMatched,
      recordRef: locator.recordRef || found.element.getAttribute?.(locator.recordRefAttribute || 'data-record-id') ||
        found.element.closest?.('[data-record-id]')?.getAttribute?.('data-record-id') || null};
  };
  const radioLabel = radio => labelFor(radio) || radio.value || '';
  const read = (element, operation = {}) => {
    const tag = String(element.tagName || '').toLowerCase();
    const type = String(element.type || '').toLowerCase();
    if (tag === 'select') {
      const selected = element.selectedOptions?.[0] || element.options?.[element.selectedIndex];
      const selectedValue = text(selected?.textContent || selected?.label || element.value || '');
      return {value: selectedValue, selectedValue, optionValue: element.value || ''};
    }
    if (type === 'radio') {
      const checked = element.checked ? element : null;
      const selectedValue = checked ? text(radioLabel(checked)) : '';
      return {value: selectedValue, selectedValue, optionValue: checked?.value || ''};
    }
    if (operation.kind === 'radio' && !element.matches?.('input[type="radio"]')) {
      const checked = (all(element, 'input[type="radio"]') || []).find(input => input.checked);
      const selectedValue = checked ? text(radioLabel(checked)) : '';
      return {value: selectedValue, selectedValue, optionValue: checked?.value || ''};
    }
    if (type === 'checkbox') return {value: element.checked ? 'true' : 'false'};
    if (element.isContentEditable || element.getAttribute?.('contenteditable') === 'true') {
      return {value: text(element.innerText ?? element.textContent ?? '')};
    }
    return {value: text('value' in element ? element.value : element.textContent)};
  };
  const kindOf = (element, operation) => {
    if (operation.kind) return operation.kind;
    const tag = String(element.tagName || '').toLowerCase();
    const type = String(element.type || '').toLowerCase();
    if (tag === 'select') return 'select';
    if (type === 'radio') return 'radio';
    if (type === 'date') return 'date';
    if (element.isContentEditable) return 'editor';
    return 'text';
  };
  const inspectOne = operation => {
    if (!operation || typeof operation !== 'object') return failure('operation-invalid', 'Operation must be an object');
    if (SENSITIVE.test(String(operation.label || '')) || SENSITIVE.test(String(operation.anchor?.label || ''))) {
      return failure('sensitive-field', 'Identity-document fields are excluded from this channel');
    }
    const resolved = resolve(operation);
    if (resolved.error) return resolved.error;
    const controlType = String(resolved.element.type || '').toLowerCase();
    if (operation.kind === 'password' || operation.kind === 'file' || controlType === 'password' || controlType === 'file') {
      return failure('sensitive-control', 'Password and file controls are excluded from this channel');
    }
    const current = read(resolved.element, operation);
    return {ok: true, status: 'inspected', count: 1, kind: kindOf(resolved.element, operation),
      value: current.value, ...(current.selectedValue !== undefined ? {selectedValue: current.selectedValue} : {}),
      anchorMatched: resolved.anchorMatched, recordRef: resolved.recordRef};
  };
  const setNativeValue = (element, value) => {
    const stringValue = String(value);
    const proto = element instanceof HTMLTextAreaElement ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
    const descriptor = Object.getOwnPropertyDescriptor(proto, 'value');
    if (descriptor?.set) descriptor.set.call(element, stringValue);
    else element.value = stringValue;
    element.dispatchEvent(new Event('input', {bubbles: true}));
    element.dispatchEvent(new Event('change', {bubbles: true}));
  };
  const setContent = (element, value) => {
    element.focus?.();
    const selection = page.getSelection?.();
    const range = page.createRange?.();
    if (selection && range) {
      range.selectNodeContents(element);
      selection.removeAllRanges();
      selection.addRange(range);
    }
    const inserted = page.execCommand?.('insertText', false, String(value));
    if (!inserted || text(element.innerText ?? element.textContent) !== text(value)) element.textContent = String(value);
    element.dispatchEvent(new InputEvent('input', {bubbles: true, inputType: 'insertText', data: String(value)}));
    element.dispatchEvent(new Event('change', {bubbles: true}));
  };
  const writeElement = (element, operation) => {
    const tag = String(element.tagName || '').toLowerCase();
    const type = String(element.type || '').toLowerCase();
    if (operation.kind === 'editor' || element.isContentEditable || element.getAttribute?.('contenteditable') === 'true') {
      setContent(element, operation.value);
      return;
    }
    if (tag === 'select' || operation.kind === 'select') {
      const options = Array.from(element.options || []);
      const matches = options.filter(option => option.value === String(operation.value) ||
        sameText(option.textContent || option.label, operation.value));
      if (matches.length !== 1) throw Object.assign(new Error(`Select value matched ${matches.length} options`),
        {code: matches.length ? 'select-ambiguous' : 'select-option-missing'});
      element.value = matches[0].value;
      element.dispatchEvent(new Event('input', {bubbles: true}));
      element.dispatchEvent(new Event('change', {bubbles: true}));
      return;
    }
    if (operation.kind === 'radio' || type === 'radio') {
      if (!element.checked) element.click();
      return;
    }
    if (type === 'checkbox') {
      const wanted = String(operation.value) === 'true';
      if (Boolean(element.checked) !== wanted) element.click();
      return;
    }
    setNativeValue(element, operation.value);
    element.dispatchEvent(new Event('blur', {bubbles: true}));
  };
  const fillOne = operation => {
    const inspected = inspectOne(operation);
    if (!inspected.ok) return inspected;
    if (operation.before == null) return failure('before-value-missing', `${operation.label || 'field'} has no known before value`);
    if (!sameText(inspected.value, operation.before)) {
      return {ok: false, status: 'conflict', code: 'old-value-changed', value: inspected.value,
        anchorMatched: inspected.anchorMatched};
    }
    const resolved = resolve(operation);
    if (resolved.error) return resolved.error;
    try { writeElement(resolved.element, operation); }
    catch (error) { return failure(error.code || 'write-failed', error.message || 'Page control rejected the value'); }
    const after = read(resolved.element, operation);
    if (!sameText(after.value, operation.value)) {
      return {ok: false, status: 'failed', code: 'readback-mismatch', value: after.value,
        anchorMatched: resolved.anchorMatched};
    }
    return {ok: true, status: 'written', count: 1, kind: kindOf(resolved.element, operation),
      value: after.value, anchorMatched: resolved.anchorMatched, recordRef: resolved.recordRef};
  };
  const findButton = module => {
    const locator = module?.saveLocator || module?.locator;
    const operation = {label: module?.label || 'save button', locator};
    const resolved = resolve(operation);
    if (resolved.error) return resolved;
    const buttonText = text(resolved.element.textContent || resolved.element.getAttribute?.('aria-label'));
    if (!buttonText) return {error: failure('save-label-missing', 'A visible save label is required before clicking')};
    if (DANGEROUS_BUTTON.test(buttonText) || DANGEROUS_BUTTON.test(String(module?.expectedLabel || ''))) {
      return {error: failure('submit-control-blocked', 'Only a non-submitting save control is allowed')};
    }
    if (module?.expectedLabel && !sameText(buttonText, module.expectedLabel)) {
      return {error: failure('save-label-mismatch', 'Observed save label did not match')};
    }
    return {element: resolved.element, buttonText};
  };
  const inspectModule = module => {
    const missingRequired = [];
    for (const field of module?.requiredFields || []) {
      const checked = resolve({label: field.label, kind: field.kind, locator: field.locator});
      if (checked.error) { missingRequired.push(field.label); continue; }
      const value = read(checked.element, field).value;
      if (!value && field.required !== false) missingRequired.push(field.label);
    }
    const save = findButton(module);
    if (save.error) return {...save.error, canSave: false, missingRequired};
    return {ok: true, status: 'inspected', canSave: missingRequired.length === 0,
      missingRequired, buttonLabel: save.buttonText};
  };
  const verifyModule = module => {
    const locator = module?.verifyLocator;
    if (!locator) return failure('verification-locator-missing', 'A real save indicator locator is required');
    const resolved = resolve({label: 'save indicator', locator});
    if (resolved.error) return {...resolved.error, saved: false};
    const actual = text(resolved.element.textContent || resolved.element.getAttribute?.('aria-label'));
    const expected = module.expectedIndicator;
    const matched = expected ? (module.indicatorContains ? actual.includes(text(expected)) : sameText(actual, expected)) : Boolean(actual);
    return {ok: matched, saved: matched, status: matched ? 'verified' : 'not-verified',
      evidence: matched ? {indicator: actual, url: location.href} : undefined};
  };
  const discover = () => {
    const options = command?.discoverOptions || {};
    const maxMs = Math.max(250, Math.min(Number(options.maxMs) || 2500, 5000));
    const maxElements = Math.max(10, Math.min(Number(options.maxElements) || 150, 250));
    const maxInteractive = Math.max(50, Math.min(Number(options.maxInteractive) || 800, 1500));
    const maxTextCandidates = Math.max(100, Math.min(Number(options.maxTextCandidates) || 1200, 3000));
    const deadline = Date.now() + maxMs;
    const elements = [];
    const seen = new Set();
    const add = (element, labelOverride = '') => {
      if (Date.now() >= deadline || elements.length >= maxElements) return;
      if (!element || seen.has(element) || !isVisible(element)) return;
      const label = text(labelOverride) || elementText(element) || labelFor(element);
      if (!label || label.length > 120) return;
      const selector = uniqueSelector(element, {deadline});
      if (!selector) return;
      seen.add(element);
      const dangerous = DANGEROUS_CLICK.test(label);
      elements.push({
        tag: String(element.tagName || '').toLowerCase(),
        text: label,
        ...(element.getAttribute?.('aria-label') ? {ariaLabel: text(element.getAttribute('aria-label'))} : {}),
        ...(element.getAttribute?.('role') ? {role: text(element.getAttribute('role'))} : {}),
        locator: {selector},
        clickAllowed: isSafeClickText(label),
        ...(isOptionElement(element) ? {optionAllowed: true} : {}),
        ...(isComboboxElement(element) ? {openSelectAllowed: true} : {}),
        ...(dangerous ? {blockedReason: 'dangerous-label'} : {})
      });
    };
    let interactive = [];
    try { interactive = Array.from(page.querySelectorAll(INTERACTIVE_SELECTOR)); } catch {}
    for (const element of interactive.slice(0, maxInteractive)) {
      if (Date.now() >= deadline || elements.length >= maxElements) break;
      if (!isVisible(element)) continue;
      const label = elementText(element);
      if (!label || (!isSafeClickText(label) && !isOptionElement(element) && !isComboboxElement(element))) continue;
      add(element, label);
    }
    let textCandidates = [];
    try { textCandidates = Array.from(page.querySelectorAll('body span, body div')); } catch {}
    for (const element of textCandidates.slice(0, maxTextCandidates)) {
      if (Date.now() >= deadline || elements.length >= maxElements) break;
      const label = text(Array.from(element.childNodes || [])
        .filter(node => node.nodeType === 3).map(node => node.nodeValue || '').join(' '));
      if (!isSafeClickText(label)) continue;
      if (element.matches?.(INTERACTIVE_SELECTOR) || element.closest?.(INTERACTIVE_SELECTOR)) continue;
      add(element, label);
    }
    return {ok: true, status: 'discovered', url: location.href, title: document.title,
      elements, truncated: Date.now() >= deadline || elements.length >= maxElements ||
        interactive.length > maxInteractive || textCandidates.length > maxTextCandidates};
  };
  const clickOne = target => {
    if (!target || typeof target !== 'object') return failure('click-target-invalid', 'Click needs a target object');
    const selector = typeof target.locator === 'string' ? target.locator : target.locator?.selector;
    const expectedText = target.expectedText ?? target.text;
    if (!selector || typeof selector !== 'string') return failure('locator-missing', 'Click requires a CSS selector');
    if (typeof expectedText !== 'string' || !text(expectedText)) return failure('click-text-missing', 'Click requires expected text');
    const resolved = unique(page, selector, 'click target');
    if (resolved.error) return resolved.error;
    const actualText = elementText(resolved.element) || labelFor(resolved.element);
    if (!sameText(actualText, expectedText)) {
      return failure('click-text-mismatch', 'Click target text did not match', {text: actualText});
    }
    const selectingOption = target.purpose === 'select-option';
    const openingSelect = target.purpose === 'open-select';
    const openingDate = target.purpose === 'open-date';
    if (selectingOption) {
      if (!isOptionElement(resolved.element)) return failure('option-target-invalid', 'Select-option clicks require a visible option element');
    } else if (openingSelect) {
      if (!isComboboxElement(resolved.element)) return failure('combobox-target-invalid', 'Open-select clicks require a combobox element');
    } else if (openingDate) {
      const tag = String(resolved.element.tagName || '').toLowerCase();
      if (tag !== 'input' || !resolved.element.readOnly) {
        return failure('date-target-invalid', 'Open-date clicks require a visible readonly input');
      }
    } else if (!isSafeClickText(expectedText) || !isSafeClickText(actualText)) {
      return failure('click-label-blocked', 'Only 编辑/去补充/展开 controls may be clicked');
    }
    if (resolved.element.disabled || resolved.element.getAttribute?.('aria-disabled') === 'true') {
      return failure('click-disabled', 'The observed control is disabled');
    }
    try {
      if (openingSelect) {
        const container = resolved.element.closest?.('.ant-select, [class*="select"], [role="combobox"]') || resolved.element;
        if (typeof container.dispatchEvent === 'function' && globalThis.MouseEvent) {
          container.dispatchEvent(new MouseEvent('mousedown', {bubbles: true, cancelable: true, view: globalThis}));
        }
        container.click();
      } else if (openingDate) {
        resolved.element.click();
      } else {
        resolved.element.click();
      }
    }
    catch (error) { return failure('click-failed', error.message || 'The page rejected the click'); }
    return {ok: true, status: 'clicked', count: 1, text: actualText, locator: {selector}, url: location.href};
  };
  const snapshot = () => {
    const controls = all(page, 'input,textarea,select,[contenteditable="true"]') || [];
    const fields = [];
    for (const element of controls.slice(0, 500)) {
      const label = labelFor(element);
      if (!label) continue;
      const selector = uniqueSelector(element);
      const row = {label, kind: kindOf(element, {}), visible: true,
        tag: String(element.tagName || '').toLowerCase(),
        ...(element.type ? {inputType: String(element.type).toLowerCase()} : {}),
        ...(element.getAttribute?.('name') ? {name: element.getAttribute('name')} : {}),
        ...(element.getAttribute?.('placeholder') ? {placeholder: element.getAttribute('placeholder')} : {}),
        ...(element.getAttribute?.('role') ? {role: element.getAttribute('role')} : {}),
        ...(element.disabled ? {disabled: true} : {}),
        ...(element.readOnly ? {readOnly: true} : {}),
        ...(selector ? {locator: {selector}} : {readStatus: 'unavailable'})};
      if (element.required || element.getAttribute?.('aria-required') === 'true') row.required = true;
      const record = element.closest?.('[data-record-id]');
      if (record) row.recordIndex = record.getAttribute('data-record-id');
      const controlType = String(element.type || '').toLowerCase();
      if (SENSITIVE.test(label) || controlType === 'password' || controlType === 'file') {
        delete row.locator;
        row.excluded = true;
      }
      else Object.assign(row, read(element, {}));
      fields.push(row);
    }
    return {ok: true, status: 'snapshot', url: location.href, title: document.title, fields};
  };
  if (!command || typeof command !== 'object') return failure('command-invalid', 'Command must be an object');
  if (command.kind === 'identify') return {ok: true, status: 'identified', url: location.href, title: document.title};
  if (command.kind === 'snapshot') return snapshot();
  if (command.kind === 'discover') return discover();
  if (command.kind === 'click') return clickOne(command.target || command.operation || command);
  if (command.kind === 'inspect') return inspectOne(command.operation);
  if (command.kind === 'fill') {
    const operations = Array.isArray(command.operations) ? command.operations : [command.operation];
    if (!operations.length || operations.some(operation => !operation)) return failure('operation-missing', 'Fill needs at least one operation');
    const results = operations.map(fillOne);
    return {ok: results.every(result => result.ok), status: results.every(result => result.ok) ? 'completed' : 'partial', operations: results};
  }
  if (command.kind === 'inspectModule') return inspectModule(command.module);
  if (command.kind === 'saveModule') {
    const save = findButton(command.module);
    if (save.error) return save.error;
    save.element.click();
    return {ok: true, status: 'clicked', label: save.buttonText};
  }
  if (command.kind === 'verifyModule') return verifyModule(command.module);
  if (command.kind === 'completeRecord') {
    const operation = {...command.operation, locator: {...(command.operation?.locator || {}),
      recordRef: command.recordRef}};
    return fillOne(operation);
  }
  return failure('command-not-allowed', `Page command ${String(command.kind)} is not allowlisted`);
}

if (typeof module !== 'undefined' && module.exports) {
  module.exports = {runPageCommand};
} else {
  // background.js imports this file for its function value. The function is
  // still serialized into the target page by scripting.executeScript; this
  // worker-local name is not a page API and does not accept arbitrary code.
  globalThis.__jobApplicationAutofillRunPageCommand = runPageCommand;
}
