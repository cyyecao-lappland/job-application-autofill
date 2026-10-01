import {Deferred} from '../runtime.mjs';
import {scopedControlTarget} from '../target.mjs';
import {parseISODate} from '../date_value.mjs';
import {makeDriver, isoDate} from '../driver.mjs';

export async function runElementDateAdapter(tab,packet,checkpoint){
  const wanted=parseISODate(packet.location),trace=[],stage=async name=>{trace.push(name);await checkpoint({stage:name,trace});};
  const remaining=()=>{const ms=Math.min(5000,packet.deadline*1000-Date.now());if(ms<=0)throw new Deferred('control_deadline');return ms;};
  const wait=async predicate=>{const end=Math.min(packet.deadline*1000,Date.now()+5000);do{if(await predicate())return;await new Promise(r=>setTimeout(r,80));}while(Date.now()<end);throw new Deferred('control_transition_timeout');};
  const target=scopedControlTarget(tab,packet),panel=()=>tab.playwright.locator('.el-picker-panel.el-date-picker:visible');
  if(await target.count()!==1||(await target.evaluate(el=>el.value))!==packet.before_value)throw new Deferred('control_user_value_changed');
  if(await panel().count()&&!packet.preopened_panel){
    if(await panel().count()!==1)throw new Deferred('date_panel_not_unique');
    await target.press('Escape',{timeoutMs:remaining()});
    await wait(async()=>await panel().count()===0);
  }
  if(await panel().count()===0){
    await stage('trigger_issued');await target.click({timeoutMs:remaining()});
    await wait(async()=>await panel().count()===1);
  }else if(await panel().count()!==1)throw new Deferred('date_panel_not_unique');
  let p=panel(),yearLabel=p.locator('.el-date-picker__header-label').first();
  if(await yearLabel.count()!==1)throw new Deferred('date_year_label_not_unique');
  await stage('year_view_issued');await yearLabel.click({timeoutMs:remaining()});
  let yearCell=p.locator('.el-year-table td.available').filter({hasText:new RegExp('^'+wanted.year+'$')});
  for(let page=0;await yearCell.count()===0&&page<20;page++){
    const cells=p.locator('.el-year-table td.available'),years=[];
    for(let i=0;i<await cells.count();i++){
      const value=Number((await cells.nth(i).innerText({timeoutMs:remaining()})).trim());
      if(Number.isInteger(value))years.push(value);
    }
    if(!years.length)throw new Deferred('date_year_page_unreadable');
    const direction=wanted.year<Math.min(...years)?'prev':'next';
    const button=p.locator(direction==='prev'?'button.el-icon-d-arrow-left,button.d-arrow-left':'button.el-icon-d-arrow-right,button.d-arrow-right');
    if(await button.count()!==1)throw new Deferred('date_year_navigation_not_unique');
    const old=Math.min(...years);
    await stage('year_page_issued');await button.click({timeoutMs:remaining()});
    await wait(async()=>{
      const next=p.locator('.el-year-table td.available'),values=[];
      for(let i=0;i<await next.count();i++)values.push(Number((await next.nth(i).innerText({timeoutMs:remaining()})).trim()));
      return values.some(Number.isInteger)&&Math.min(...values.filter(Number.isInteger))!==old;
    });
    yearCell=p.locator('.el-year-table td.available').filter({hasText:new RegExp('^'+wanted.year+'$')});
  }
  if(await yearCell.count()!==1)throw new Deferred('date_year_not_unique');
  await stage('year_issued');await yearCell.click({timeoutMs:remaining()});
  const monthNames=['一月','二月','三月','四月','五月','六月','七月','八月','九月','十月','十一月','十二月'];
  const monthCell=p.locator('.el-month-table td').filter({hasText:new RegExp('^'+monthNames[wanted.month-1]+'$')});
  if(await monthCell.count()!==1)throw new Deferred('date_month_not_unique');
  await stage('month_issued');await monthCell.click({timeoutMs:remaining()});
  const dayCells=p.locator('.el-date-table td.available:not(.prev-month):not(.next-month)');
  const dayLabels=[];for(let i=0;i<await dayCells.count();i++)dayLabels.push((await dayCells.nth(i).innerText({timeoutMs:remaining()})).trim());
  const dayMatches=dayLabels.map((label,index)=>({label,index})).filter(x=>x.label===String(wanted.day));
  if(dayMatches.length!==1)throw new Deferred('date_day_not_unique');
  await stage('day_issued');await dayCells.nth(dayMatches[0].index).click({timeoutMs:remaining()});
  const autoCloseEnd=Math.min(packet.deadline*1000,Date.now()+500);
  while(await panel().count()&&Date.now()<autoCloseEnd)await new Promise(r=>setTimeout(r,80));
  if(await panel().count()){
    const confirm=panel().getByRole('button',{name:'确定',exact:true});
    if(await confirm.count()===1){
      await stage('confirm_issued');await confirm.click({timeoutMs:remaining()});
    }else if((await target.evaluate(el=>el.value))===packet.location){
      // Some Element UI date pickers commit immediately but remain open and
      // expose no confirm button. Close only after exact value read-back.
      await stage('committed_panel_close_issued');await target.press('Escape',{timeoutMs:remaining()});
    }else throw new Deferred('date_confirm_not_unique');
  }
  await wait(async()=>await panel().count()===0);
  const value=await target.evaluate(el=>el.value);
  if(value!==packet.location)throw new Deferred('date_readback_mismatch');
  await stage('verified');return {adapter:'element_date_picker_v1',value,trace,committed:true};
}

const meta = (name, kind) => ({name, adapterVersion:1, targetTypes:[kind], protocolVersion:1});
const dateValue = target => {
  if (target?.kind !== 'date') throw new Deferred('invalid_date_target');
  return isoDate(target.value);
};
export const driver = makeDriver(meta('element_date_picker_v1','date'), runElementDateAdapter, dateValue);
