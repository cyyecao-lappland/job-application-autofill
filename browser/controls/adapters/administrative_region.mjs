import {locationKey} from './region.mjs';
import {makeDriver, hierarchyValue} from '../driver.mjs';

export function exactAdministrativeOption(options,wanted){
  const matches=options.filter(o=>locationKey(o)===locationKey(wanted));
  if(matches.length!==1)throw new Error('administrative_option_not_unique');
  return matches[0];
}
export function administrativeSearchQuery(value){
  const text=String(value).normalize('NFKC').replace(/\s+/g,'');
  const city=text.match(/^(?:.*?省)?([^省市]+)市.+$/);
  return city?city[1]:text;
}

export async function runAdministrativeRegionAdapter(tab,packet,checkpoint){
  const trace=[],stage=async name=>{trace.push(name);await checkpoint({stage:name,trace});};
  const remaining=()=>{const ms=Math.min(5000,packet.deadline*1000-Date.now());if(ms<=0)throw new Error('control_deadline');return ms;};
  const dialog=()=>tab.playwright.locator('[role="dialog"].s-dialog:visible');
  const wait=async predicate=>{const end=Math.min(packet.deadline*1000,Date.now()+5000);do{if(await predicate())return;await new Promise(r=>setTimeout(r,80));}while(Date.now()<end);throw new Error('control_transition_timeout');};
  const target=tab.playwright.locator(packet.field_selector);
  if(await target.count()!==1||(await target.evaluate(el=>el.value))!==packet.before_value)throw new Error('control_user_value_changed');
  if(await dialog().count()){
    if(await dialog().count()!==1)throw new Error('dialog_not_unique');
    const search=dialog().locator('input[placeholder="搜索城市名/区县"]');
    const close=dialog().locator('.s-dialog__icon.s-icon-guanbi');
    if(await search.count()!==1||await close.count()!==1)throw new Error('unknown_dialog_adapter');
    await stage('preexisting_dialog_close_issued');await close.click({timeoutMs:remaining()});
    await wait(async()=>await dialog().count()===0);
  }
  await stage('trigger_issued');await target.click({timeoutMs:remaining()});
  await wait(async()=>await dialog().count()===1);
  const search=dialog().locator('input[placeholder="搜索城市名/区县"]');
  if(await search.count()!==1)throw new Error('unknown_dialog_adapter');
  await stage('search_issued');await search.fill(administrativeSearchQuery(packet.location),{timeoutMs:remaining()});
  const options=dialog().locator('li.s-option');
  await wait(async()=>await options.count()>0);
  const labels=[];for(let i=0;i<await options.count();i++)labels.push((await options.nth(i).innerText({timeoutMs:remaining()})).trim());
  const chosen=exactAdministrativeOption(labels,packet.location),index=labels.indexOf(chosen);
  await stage('option_issued');await options.nth(index).click({timeoutMs:remaining()});
  await wait(async()=>await dialog().count()===0);
  const value=await target.evaluate(el=>el.value);
  if(locationKey(value)!==locationKey(chosen))throw new Error('location_readback_mismatch');
  await stage('verified');return {adapter:'administrative_region_dialog_v1',value,trace,committed:true};
}


const meta = (name, kind) => ({name, adapterVersion:1, targetTypes:[kind], protocolVersion:1});
export const driver = makeDriver(meta('administrative_region_dialog_v1','hierarchy'),
  runAdministrativeRegionAdapter, target => {
    if (target?.kind === 'hierarchy' && target.path?.length === 1 && target.path[0].level === 'full_path' &&
        typeof target.path[0].label === 'string' && target.path[0].label.trim()) return target.path[0].label;
    return Object.values(hierarchyValue(target, ['province','city','district'])).join('-');
  });
