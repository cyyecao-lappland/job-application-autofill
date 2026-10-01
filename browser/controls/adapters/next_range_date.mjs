import {scopedControlTarget} from '../target.mjs';
import {parseISODate} from '../date_value.mjs';
import {makeDriver, isoDate} from '../driver.mjs';

export async function runNextRangeDateAdapter(tab,packet,checkpoint){
  const target=scopedControlTarget(tab,packet);
  if(await target.count()!==1)throw new Error('next_range_structure_changed');
  const structure=await target.evaluate(el=>({range:!!el.closest('.next-range-picker'),readonly:el.readOnly,
    expanded:el.getAttribute('aria-expanded'),
    values:[...(el.closest('.next-range-picker')?.querySelectorAll('.next-range-picker-trigger-input input')||[])].map(e=>e.value)}));
  if(await target.count()!==1||!structure.range||!structure.readonly)throw new Error('next_range_structure_changed');
  const wanted=packet.location;
  const ongoing=wanted?.current===true&&wanted.end===null;
  if(!wanted||!/^\d{4}-\d{2}(?:-\d{2})?$/.test(wanted.start)||(!ongoing&&(!/^\d{4}-\d{2}(?:-\d{2})?$/.test(wanted.end)||wanted.start>wanted.end)))
    throw new Error('explicit_date_range_required');
  // Validate month values using a temporary validation date, never pass an invented day to the page.
  parseISODate(wanted.start.length===7?wanted.start+'-01':wanted.start);
  if(!ongoing)parseISODate(wanted.end.length===7?wanted.end+'-01':wanted.end);
  let currentControl=null;
  if(ongoing){
    currentControl=tab.playwright.locator(packet.module_selector).locator('label.next-checkbox-wrapper').filter({hasText:'至今'});
    if(await currentControl.count()!==1)throw new Error('current_range_requires_present_checkbox');
  }
  const values=[wanted.start.slice(0,7),ongoing?'':wanted.end.slice(0,7)];
  if(structure.expanded!=='true'&&JSON.stringify(structure.values)===JSON.stringify(values)&&
      (!ongoing||await currentControl.locator('input[type="checkbox"]').getAttribute('aria-checked')==='true'))return {adapter:'next_range_date_v1',committed:true,already_matched:true};
  if(await target.evaluate(el=>el.value)!==packet.before_value)throw new Error('control_user_value_changed');
  if(structure.expanded!=='true'){
    await checkpoint({stage:'calendar_open_issued'});
    await target.click({timeoutMs:5000});
    await checkpoint({stage:'calendar_open_returned'});
  }
  const panel=tab.playwright.locator('.next-range-picker-body:visible');
  const panelDeadline=Date.now()+1500;
  while(await panel.count()!==1&&Date.now()<panelDeadline)await new Promise(resolve=>setTimeout(resolve,60));
  if(await panel.count()!==1)throw new Error('range_panel_not_unique');
  const owner=await target.evaluate(el=>{const r=el.closest('.next-range-picker').getBoundingClientRect();
    const panels=[...document.querySelectorAll('.next-range-picker-body')].filter(e=>e.getClientRects().length);
    if(panels.length!==1)return false;const p=panels[0].getBoundingClientRect();
    const expanded=[...document.querySelectorAll('.next-range-picker-trigger[aria-expanded="true"]')];
    // Fusion clamps its popup inside the viewport, sometimes over the trigger.
    // Require the unique expanded owner instead of assuming a vertical gap.
    return expanded.length===1&&expanded[0]===el.closest('.next-range-picker-trigger')&&
      el.getAttribute('aria-expanded')==='true'&&Math.min(p.right,r.right)>Math.max(p.left,r.left);});
  if(!owner)throw new Error('range_panel_ownership_unconfirmed');
  const inputs=[panel.locator('.next-range-picker-panel-input-start-date input'),panel.locator('.next-range-picker-panel-input-end-date input')];
  for(let i=0;i<2;i++){
    if(await inputs[i].count()!==1||await inputs[i].getAttribute('placeholder')!=='YYYY-MM')throw new Error('range_precision_not_supported');
    await checkpoint({stage:'range_endpoint_fill_issued',endpoint:i});
    await inputs[i].fill(values[i],{timeoutMs:5000});
    await inputs[i].press('Tab',{timeoutMs:5000});
    await checkpoint({stage:'range_endpoint_fill_returned',endpoint:i});
  }
  const confirm=panel.getByRole('button',{name:'确定',exact:true});
  if(ongoing){
    // The partial start is committed on blur; the explicit present checkbox
    // supplies the other endpoint. Never invent a completed end date.
    await target.press('Escape',{timeoutMs:5000});
    await tab.playwright.locator(packet.module_selector).locator('.next-form-item-label').first().click({timeoutMs:5000});
    const checkbox=currentControl.locator('input[type="checkbox"]');
    if(await checkbox.getAttribute('aria-checked')!=='true')await currentControl.locator('.next-checkbox-label').click({timeoutMs:5000});
  }else{
    if(await confirm.count()!==1||!await confirm.isEnabled())throw new Error('range_confirmation_not_ready');
    await checkpoint({stage:'range_confirm_issued'});await confirm.click({timeoutMs:5000});
    await checkpoint({stage:'range_confirm_returned'});
  }
  await panel.waitFor({state:'hidden',timeoutMs:5000});
  const actual=await target.evaluate(el=>[...el.closest('.next-range-picker').querySelectorAll('.next-range-picker-trigger-input input')].map(e=>e.value));
  if(JSON.stringify(actual)!==JSON.stringify(values))throw new Error('range_readback_mismatch');
  if(ongoing){
    const checkbox=currentControl.locator('input[type="checkbox"]');
    if(await checkbox.getAttribute('aria-checked')!=='true')await currentControl.locator('.next-checkbox-label').click({timeoutMs:5000});
    if(await checkbox.getAttribute('aria-checked')!=='true')throw new Error('current_range_checkbox_not_committed');
  }
  await checkpoint({stage:'verified'});return {adapter:'next_range_date_v1',committed:true,precision:'month',endpoints_verified:2};
}


const meta = (name, kind) => ({name, adapterVersion:1, targetTypes:[kind], protocolVersion:1});
export const driver = makeDriver(meta('next_range_date_v1','date_range'), runNextRangeDateAdapter, target => {
  if (target?.kind !== 'date_range') throw new Error('invalid_date_range_target');
  // The existing range driver now accepts real month precision; no invented day.
  const format = value => isoDate(value, value?.precision === 'month' ? 'month' : 'day');
  const start = format(target.start);
  if (target.end?.kind === 'current') return {start, end:null, current:true};
  if (target.end?.kind !== 'date') throw new Error('date_range_end_unknown');
  const end = format(target.end.value);
  if (start.slice(0,7) > end.slice(0,7) || (start.length === end.length && start > end)) throw new Error('invalid_date_range_order');
  return {start, end};
});
