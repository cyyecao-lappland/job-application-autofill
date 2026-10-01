import {provinceCity} from '../../rules/recognition.mjs';
import {scopedControlTarget} from '../target.mjs';
import {classifyDialog} from '../../rules/recognition.mjs';
import {readControlEvidence} from '../../rules/dom.mjs';
import {makeDriver, hierarchyValue, incompleteRead} from '../driver.mjs';

export const driver = makeDriver(
  {name:'province_city_dialog_v1', adapterVersion:1, targetTypes:['hierarchy'], protocolVersion:1},
  runLocationAdapter, target => hierarchyValue(target, ['province','city']),
  evidence => evidence && provinceCity(evidence) ? 'compatible' : 'needs_probe'
);

export const locationKey=s=>String(s).normalize('NFKC').replace(/[\s-]+/g,'').replace(/省|市/g,'').replace(/^(上海|北京|天津|重庆)\1$/,'$1');
export function exactLocationOption(options,wanted){
  const matches=options.filter(o=>!['省','市'].includes(o.label)&&locationKey(o.label)===locationKey(wanted));
  if(matches.length!==1)throw new Error('location_option_not_unique');return matches[0];
}
export async function runLocationAdapter(tab,packet,checkpoint){
  const trace=[],stage=async name=>{trace.push(name);await checkpoint({stage:name,trace});};
  const remaining=()=>{const ms=Math.min(5000,packet.deadline*1000-Date.now());if(ms<=0)throw new Error('control_deadline');return ms;};
  const target=scopedControlTarget(tab,packet);
  const read=()=>tab.playwright.evaluate(readControlEvidence,{moduleSelector:packet.module_selector,
    fieldSelector:packet.allow_logical_selector?packet.field_selector:null},{timeoutMs:2000});
  const wait=async predicate=>{const end=Math.min(packet.deadline*1000,Date.now()+5000);do{const e=await read();if(predicate(e))return e;await new Promise(r=>setTimeout(r,80));}while(Date.now()<end);throw new Error('control_transition_timeout');};
  const binding=e=>{if(e.root_count!==1||e.dialogs.length!==1)throw new Error('dialog_not_unique');const b=classifyDialog(e.dialogs[0]);if(!b)throw new Error('unknown_dialog_adapter');return b;};
  const current=await read(),targets=current.fields.filter(f=>(packet.allow_logical_selector?f.matches_target:f.label===packet.field_label)&&f.tag==='INPUT'&&f.type==='text');
  if(targets.length!==1||(!packet.allow_logical_selector&&targets[0].selector!==packet.field_selector)||targets[0].disabled)throw new Error('control_identity_changed');
  const actualBefore=await target.evaluate(el=>el.value);
  if(!current.dialogs.length&&locationKey(actualBefore)===locationKey(packet.location.province+packet.location.city))
    return {adapter:'province_city_dialog_v1',committed:true,already_matched:true,actual:actualBefore,verification:'match'};
  if(Object.hasOwn(packet,'before_value')&&actualBefore!==packet.before_value)throw new Error('control_user_value_changed');
  if(current.dialogs.length){const old=binding(current);
    const selected=[old.province,old.city].map(c=>c.options.filter(o=>o.selected));
    const unchanged=selected.every(a=>a.length===1)&&
      locationKey(selected[0][0].label)===locationKey(packet.location.province)&&
      locationKey(selected[1][0].label)===locationKey(packet.location.city)&&
      locationKey(actualBefore)===locationKey(packet.location.province+packet.location.city);
    if(old.close&&unchanged){
      await stage('unchanged_dialog_close_issued');await tab.playwright.locator(old.close.selector).click({timeoutMs:remaining()});
      await wait(e=>e.dialogs.length===0);
      const actual=await target.evaluate(el=>el.value);
      if(actual!==actualBefore)throw new Error('control_user_value_changed');
      return {adapter:'province_city_dialog_v1',committed:true,already_matched:true,actual,verification:'match'};
    }
    if(!old.close||[old.province,old.city].some(c=>c.options.some(o=>o.selected&&!['省','市'].includes(o.label))))throw new Error('preexisting_selection_requires_review');
    await stage('empty_dialog_close_issued');await tab.playwright.locator(old.close.selector).click({timeoutMs:remaining()});
    await wait(e=>e.dialogs.length===0);
  }
  await stage('trigger_issued');await target.click({timeoutMs:remaining()});
  let b=binding(await wait(e=>e.dialogs.length>0));const p=exactLocationOption(b.province.options,packet.location.province);
  await stage('province_issued');await tab.playwright.locator(b.province.selector).selectOption({label:p.label},{timeoutMs:remaining()});
  const end=Math.min(packet.deadline*1000,Date.now()+3000);let c;
  do{b=binding(await read());try{c=exactLocationOption(b.city.options,packet.location.city);}catch{}if(c)break;await new Promise(r=>setTimeout(r,80));}while(Date.now()<end);
  if(!c)throw new Error('city_options_not_ready');
  await stage('city_issued');await tab.playwright.locator(b.city.selector).selectOption({label:c.label},{timeoutMs:remaining()});
  b=binding(await read());if(!b.province.options.some(o=>o.selected&&o.label===p.label)||!b.city.options.some(o=>o.selected&&o.label===c.label))throw new Error('selected_location_mismatch');
  await stage('confirm_issued');await tab.playwright.locator(b.confirm.selector).click({timeoutMs:remaining()});
  await wait(e=>e.dialogs.length===0);
  const value=await target.evaluate(el=>el.value??null);
  if(locationKey(value)!==locationKey(p.label+c.label)){
    if(packet.return_verification&&incompleteRead(value)){
      await stage('verification_skipped');
      return {adapter:b.adapter,value,actual:value,trace,committed:false,verification:'skipped',reason:'readback_incomplete'};
    }
    throw Object.assign(new Error('location_readback_mismatch'),{controlVerification:'mismatch',actual:value});
  }
  await stage('verified');return {adapter:b.adapter,value,actual:value,province:p.label,city:c.label,trace,committed:true,verification:'match'};
}
