export class Deferred extends Error {}
export class Conflict extends Error {}
export const comparable = value => JSON.stringify(value,(key,value)=>['verification_skipped','verification_skip_command'].includes(key)?undefined:value);
export const same = (a,b) => comparable(a) === comparable(b);
export const sameValue = (a,b) => Array.isArray(a)&&Array.isArray(b)?same([...a].sort(),[...b].sort()):same(a,b);
export const normalize = text => String(text).normalize('NFKC').trim().replace(/\s+/g,' ');
export function actionStage(tab,stage){if(tab.instrumentationContext)tab.instrumentationContext.stage=stage;}
export function milliseconds(deadline){const ms=Math.floor(deadline-Date.now());if(ms<=0)throw new Deferred('operation_budget');return ms;}
export function fieldLocator(tab,packet,field){
  return tab.playwright.locator(packet.module_selector).locator(field.selector);
}

export const fileMatches = (field,value) => typeof value==='string'&&field?.value_present===true&&field.upload_ready===true&&field.value===value.split(/[\\/]/).pop();
