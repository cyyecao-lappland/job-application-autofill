'use strict';
// A concrete port for a supported Playwright page. All selectors/record attributes
// are observed at runtime; this file contains no site-specific selectors.
function createPlaywrightHost(page, identify, capabilities, structureConfig = {}) {
  function readDom({op,config,structure}) {
    const visible = el => !!(el.getClientRects().length && getComputedStyle(el).visibility !== 'hidden');
    const kind = el => el.tagName === 'SELECT' ? 'select' :
      el.tagName === 'TEXTAREA' ? 'text' : el.tagName === 'INPUT' ?
      (['text','email','tel','url','search','number'].includes(el.type) ? 'text' : el.type) : el.getAttribute('role') || 'unknown';
    if (structure) {
      const records = (config.records || []).flatMap(r => [...document.querySelectorAll(r.selector)].map(el =>
        ({id:el.getAttribute(r.identityAttribute),sectionId:r.sectionId})));
      if (records.some(r => !r.id) || new Set(records.map(r => r.sectionId+':'+r.id)).size !== records.length)
        return {readStatus:'unknown'};
      const fields = [...document.querySelectorAll('input,textarea,select,[role="combobox"],[role="radio"],[contenteditable="true"]')]
        .filter(el => !['hidden','password'].includes(el.type))
        .map(el => {
          const record=(config.records || []).find(r => el.closest(r.selector));
          return {key:el.id || el.getAttribute('name') || el.getAttribute('aria-label') || el.labels?.[0]?.textContent?.trim() || '',
            recordId:record ? el.closest(record.selector).getAttribute(record.identityAttribute) : null,
            kind:kind(el),visible:visible(el),required:el.required || el.getAttribute('aria-required') === 'true',enabled:!el.disabled};
        });
      const sections=(config.sections || []).flatMap(s => [...document.querySelectorAll(s.selector)].map(el =>
        ({id:s.id,expanded:el.getAttribute('aria-expanded'),loaded:visible(el)})));
      return {readStatus:'known',route:location.href,
        step:config.stepSelector ? document.querySelector(config.stepSelector)?.textContent?.trim() : null,
        modal:[...document.querySelectorAll('[role="dialog"],dialog')].filter(visible).map(el => el.id || el.getAttribute('aria-label') || 'dialog').join('|'),
        sections,records,fields};
    }
    const l=op.locator;
    const scopes=l.recordSelector ? [...document.querySelectorAll(l.recordSelector)] : [document];
    if (scopes.length !== 1) return {count:0,readStatus:'unknown'};
    const record=scopes[0];
    const matches=[...record.querySelectorAll(l.selector)];
    if (matches.length !== 1) return {count:matches.length,readStatus:'unknown'};
    const el=matches[0];
    const identity=l.recordAttribute ? record.getAttribute(l.recordAttribute) : undefined;
    const anchors=l.anchorSelector ? [...record.querySelectorAll(l.anchorSelector)] : [];
    const anchorValue=anchors.length === 1 ?
      (anchors[0].tagName === 'SELECT' ? anchors[0].selectedOptions[0]?.label :
        ('value' in anchors[0] ? anchors[0].value : anchors[0].textContent?.trim())) : undefined;
    const known=['INPUT','TEXTAREA','SELECT'].includes(el.tagName) &&
      !['password','file','hidden','checkbox','range','button','submit','reset','color'].includes(el.type) && visible(el) && !el.disabled && !el.readOnly;
    return {count:1,kind:kind(el),readStatus:known ? 'known' : 'unknown',
      value:known ? (el.type === 'radio' ? el.checked : el.tagName === 'SELECT' ? el.selectedOptions[0]?.label : el.value) : undefined,
      recordRef:identity,anchorMatched:!!l.recordSelector && !!l.recordRef && identity === l.recordRef && anchors.length === 1 && anchorValue === op.anchor?.value};
  }
  const inspect = op => page.evaluate(readDom,{op,structure:false});
  return {
    identify,capabilities,
    supports:op => ["text","select"].includes(op.kind) || (op.kind === "radio" && op.value === true),
    structure:() => page.evaluate(readDom,{config:structureConfig,structure:true}),
    inspect,
    async write(op,{timeoutMs,deadline}) {
      const expiresAt=deadline ?? Date.now()+timeoutMs;
      if (!(timeoutMs > 0)) return {status:'failed',noEffect:true,settled:true};
      const l=op.locator;
      const scope=l.recordSelector ? page.locator(l.recordSelector) : page;
      const target=scope.locator(l.selector);
      // Resolve and check again immediately before the action; never retain ElementHandles.
      const current=await inspect(op);
      timeoutMs=expiresAt-Date.now();
      if (!(timeoutMs > 0)) return {status:'failed',noEffect:true,settled:true};
      if (current.count !== 1 || current.readStatus !== 'known' || current.kind !== op.kind ||
          (op.anchor && current.anchorMatched !== true) || current.value !== op.before)
        return {status:'failed',noEffect:true,settled:true};
      if (op.kind === 'text') await target.fill(op.value,{timeout:timeoutMs});
      else if (op.kind === 'radio' && op.value === true) await target.check({timeout:timeoutMs});
      else if (op.kind === 'select') await target.selectOption({label:op.value},{timeout:timeoutMs});
      else return {status:'failed',noEffect:true,settled:true};
      return {status:'written',settled:true};
    }
  };
}
if (typeof module !== 'undefined') module.exports={createPlaywrightHost};
