// Zhaopin wraps some end-date pickers with a mutually exclusive "至今"
// overlay.  Disable that current-role state first, then reuse the ordinary
// date picker contract.  This stays separate from the semantic field mapping:
// callers still supply one factual ISO date from the bound profile record.

import {scopedControlTarget} from '../target.mjs';
import {parseISODate} from '../date_value.mjs';
import {runElementDateAdapter} from './element_date.mjs';
import {makeDriver, isoDate} from '../driver.mjs';

export async function runElementDateNowAdapter(tab,packet,checkpoint){
  parseISODate(packet.location);
  const target=scopedControlTarget(tab,packet);
  if(await target.count()!==1)throw new Error('date_now_target_not_unique');
  const wrapper=target.locator('xpath=ancestor::div[contains(concat(" ", normalize-space(@class), " "), " apply-form-date-now ")][1]');
  if(await wrapper.count()!==1)throw new Error('date_now_wrapper_not_unique');
  const current=wrapper.locator('.apply-form-date-now__ipt input');
  const mask=wrapper.locator('.apply-form-date-now__mask');
  if(await current.count()!==1||await mask.count()!==1)throw new Error('date_now_toggle_not_unique');
  const currentValue=await current.evaluate(el=>el.value);
  if(String(currentValue).trim()==='至今'){
    const panel=()=>tab.playwright.locator('.el-picker-panel.el-date-picker:visible');
    if(await panel().count()===0){
      await checkpoint({stage:'current_date_picker_issued'});
      await mask.click({timeoutMs:Math.min(5000,packet.deadline*1000-Date.now())});
    }
    const end=Math.min(packet.deadline*1000,Date.now()+3000);
    while(await panel().count()!==1&&Date.now()<end)
      await new Promise(resolve=>setTimeout(resolve,80));
    if(await panel().count()!==1)throw new Error('date_now_picker_not_open');
    await checkpoint({stage:'current_date_picker_opened'});
  }
  const before=await target.evaluate(el=>el.value);
  const outcome=await runElementDateAdapter(tab,{...packet,before_value:before,preopened_panel:true},checkpoint);
  if(String(await current.evaluate(el=>el.value)).trim()==='至今')throw new Error('date_now_toggle_not_committed');
  return {...outcome,adapter:'element_date_now_picker_v1'};
}

const meta = (name, kind) => ({name, adapterVersion:1, targetTypes:[kind], protocolVersion:1});
const dateValue = target => {
  if (target?.kind !== 'date') throw new Error('invalid_date_target');
  return isoDate(target.value);
};
export const driver = makeDriver(meta('element_date_now_picker_v1','date'), runElementDateNowAdapter, dateValue);
