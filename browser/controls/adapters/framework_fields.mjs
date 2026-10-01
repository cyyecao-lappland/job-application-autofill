import {readFrameworkDate} from '../../rules/framework_fields.mjs';
import {Deferred,Conflict,milliseconds,fieldLocator} from '../runtime.mjs';

export const phoenixRadio={
 declaration:{name:'phoenix_radio_group_v1',adapterVersion:1,targetTypes:['choice'],protocolVersion:1},
 async applyField({tab,packet,field,op,end,markAction,readField}){
  const matches=field.options.filter(o=>o.label===op.value&&!o.disabled&&o.selector);
  if(matches.length!==1)throw new Deferred('radio_option_missing_or_ambiguous');
  if(field.value===op.value)return;
  markAction();await tab.playwright.locator(packet.module_selector).locator(matches[0].selector).click({timeoutMs:milliseconds(end)});
  const observed=await readField(end);if(observed.field.value!==op.value)throw new Conflict('radio_selection_not_committed');
 }
};
function dateDriver(name,component){return {
 declaration:{name,adapterVersion:component==='ant-picker'?2:1,targetTypes:['date'],protocolVersion:1},
 async applyField({tab,packet,field,op,end,markAction}){
  if(typeof op.value!=='string'||!/^\d{4}-\d{2}(?:-\d{2})?$/.test(op.value))throw new Deferred('confirmed_date_required');
  const [year,month,day]=op.value.split('-').map(Number);if(month<1||month>12||(day!==undefined&&(day<1||day>new Date(year,month,0).getDate())))throw new Deferred('invalid_date');
  let baseline=null;const read=()=>tab.playwright.evaluate(readFrameworkDate,{moduleSelector:packet.module_selector,selector:field.selector,component,baseline});
  let state=await read();if(state.error)throw new Deferred(state.error);
  if(state.disabled)throw new Deferred('date_input_disabled');
  if(state.actual===op.value&&state.committed)return;
  if(state.visible.length)throw new Deferred('preexisting_calendar');
  if(component==='ud-date'&&state.placeholder&&/YYYY-MM-DD/.test(state.placeholder)&&day===undefined)throw new Deferred('date_requires_confirmed_day');
  if(component==='ud-date'&&state.placeholder==='YYYY-MM'&&day!==undefined)throw new Deferred('date_precision_requires_explicit_month_transform');
  baseline={owner:state.owner,visible:state.visible};const target=fieldLocator(tab,packet,field);
  let opened=false;
  try{
   markAction();await tab.playwright.locator(state.owner).click({timeoutMs:milliseconds(end)});opened=true;
   const until=Math.min(end,Date.now()+2500);
   do{state=await read();if(state.error)throw new Deferred(state.error);if(state.panel_ambiguous)throw new Deferred('date_panel_ambiguous');if(state.menu&&(state.editor||component==='ant-picker'&&state.readonly&&state.precision==='month'))break;await new Promise(r=>setTimeout(r,60));}while(Date.now()<until);
   if(!state.menu)throw new Deferred('owned_date_editor_missing');
   if(component==='ant-picker'&&((state.precision==='month'&&day!==undefined)||(state.precision==='day'&&day===undefined)||!state.precision))throw new Deferred('date_precision_requires_matching_panel');
   if(component==='ant-picker'&&state.readonly){
    if(state.precision!=='month'||!Number.isInteger(state.month_year)||Math.abs(state.month_year-year)>25)throw new Deferred('readonly_month_panel_required');
    let moves=0;
    while(state.month_year!==year){
     if(++moves>25)throw new Deferred('date_year_navigation_limit');
     const priorYear=state.month_year,step=priorYear<year?1:-1;
     const navigation=step===1?state.month_next:state.month_previous;
     if(!navigation)throw new Deferred('date_year_navigation_unavailable');
     markAction();await tab.playwright.locator(navigation).click({timeoutMs:milliseconds(end)});
     const changeEnd=Math.min(end,Date.now()+1200);
     do{state=await read();if(state.month_year!==priorYear)break;await new Promise(r=>setTimeout(r,60));}while(Date.now()<changeEnd);
     if(state.error||!state.menu||state.precision!=='month'||state.month_year!==priorYear+step)throw new Deferred('date_year_navigation_not_confirmed');
    }
    const cells=state.month_cells.filter(cell=>cell.value===op.value&&!cell.disabled);
    if(cells.length!==1)throw new Deferred('date_month_missing_or_ambiguous');
    markAction();await tab.playwright.locator(cells[0].selector).click({timeoutMs:milliseconds(end)});
   }else{
    if(!state.editor)throw new Deferred('owned_date_editor_missing');
    const editor=tab.playwright.locator(state.editor);markAction();await editor.fill(op.value,{timeoutMs:milliseconds(end)});await editor.press('Enter',{timeoutMs:milliseconds(end)});
   }
   if(state.dismiss)await tab.playwright.locator(state.dismiss).click({timeoutMs:milliseconds(end)});
   const verifyEnd=Math.min(end,Date.now()+1800);
   do{state=await read();if(state.actual===op.value&&state.committed)return;await new Promise(r=>setTimeout(r,60));}while(Date.now()<verifyEnd);
   throw new Conflict('date_selection_not_committed');
  }finally{
   if(opened){state=await read();if(state.menu){await target.press('Escape',{timeoutMs:milliseconds(end)});if(state.dismiss)await tab.playwright.locator(state.dismiss).click({timeoutMs:milliseconds(end)});}}
  }
 }
};}
export const udDate=dateDriver('ud_date_input_v1','ud-date');
export const phoenixDate=dateDriver('phoenix_date_input_v1','phoenix-date');
export const antPicker=dateDriver('ant_picker_input_v1','ant-picker');
