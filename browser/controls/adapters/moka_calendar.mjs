import {readMokaCalendar} from '../../rules/moka_calendar.mjs';
import {Deferred,milliseconds,actionStage} from '../runtime.mjs';

export const mokaCalendar={
 declaration:{name:'moka_calendar_v1',adapterVersion:4,targetTypes:['date'],protocolVersion:1},
 async applyField({tab,packet,field,op,end,markAction}){
  const monthPrecision=field.date_precision==='month'&&typeof op.value==='string'&&/^\d{4}-\d{2}$/.test(op.value);
  const iso=typeof op.value==='string'?(monthPrecision?op.value+'-01':op.value):null;
  const parsed=iso?new Date(iso+'T00:00:00Z'):null;
  if(!iso||!/^\d{4}-\d{2}-\d{2}$/.test(iso)||!Number.isFinite(parsed?.getTime())||
      parsed.toISOString().slice(0,10)!==iso)throw new Deferred('confirmed_day_date_required');
  let inputSelector=field.selector;
  let trackedPopupSelector=null;
  const read=()=>tab.playwright.evaluate(readMokaCalendar,{moduleSelector:packet.module_selector,selector:inputSelector,trackedPopupSelector});
  let state=await read();if(state.error||state.disabled)throw new Deferred(state.error||'date_input_disabled');
  if(state.committed&&state.actual===op.value){if(monthPrecision)return;throw new Deferred('moka_calendar_readback_only');}
  const baseline={input:state.input,owner:state.owner,container:state.container};
  inputSelector=baseline.input;
  const sameOwner=s=>!s.error&&!s.disabled&&s.input===baseline.input&&s.owner===baseline.owner&&s.container===baseline.container;
  const diagnostic=(reason,s)=>{const error=new Deferred(reason);error.controlDiagnostic={field_id:field.id,component:'moka-date',reason,
   owned_calendar_confirmed:!!s.calendar,cleanup_attempted:false,observed_only:true,
   full_date_readback:s.actual===op.value,closure_confirmed:s.committed&&s.tracked_popup_visible===false,...s};return error;};
  const click=async(selector,stage)=>{actionStage(tab,stage);markAction();await tab.playwright.locator(packet.module_selector).locator(selector).click({timeoutMs:milliseconds(end)});};
  if(!state.popup_candidates.length)await click(state.owner,'popup_open');
  else if(!state.calendar)throw diagnostic('preexisting_calendar',state);
   const until=Math.min(end,Date.now()+2000);
   do{state=await read();if(state.error)throw new Deferred(state.error);
      if(!sameOwner(state))throw diagnostic('moka_calendar_owner_changed',state);
      if(state.popup_candidates.length)break;await new Promise(r=>setTimeout(r,60));}while(Date.now()<until);
   if(!state.calendar||state.popups.length!==1)throw diagnostic(state.popup_candidates.length?'moka_calendar_variant_requires_driver':'moka_calendar_popup_unavailable',state);
   trackedPopupSelector=state.calendar.popup;
   const panelSelector=state.calendar.panel;
   const targetYear=Number(op.value.slice(0,4)),targetMonth=Number(op.value.slice(5,7));
   let moves=0;
   while(state.calendar.year!==targetYear){
    const previous=state.calendar.year,direction=targetYear>previous?1:-1;
    if(++moves>40)throw diagnostic('moka_calendar_year_navigation_budget',state);
    const control=direction>0?state.calendar.next:state.calendar.previous;
    if(!control)throw diagnostic('moka_calendar_year_navigation_disabled',state);
    await click(control,'calendar_year_navigation');
    const changedUntil=Math.min(end,Date.now()+1500);
    do{state=await read();if(!sameOwner(state)||!state.calendar||state.calendar.panel!==panelSelector||state.calendar.popup!==trackedPopupSelector)throw diagnostic('moka_calendar_owner_or_variant_changed',state);
       if(state.calendar.year!==previous)break;await new Promise(r=>setTimeout(r,50));}while(Date.now()<changedUntil);
    if(state.calendar.year!==previous+direction)throw diagnostic('moka_calendar_year_navigation_unconfirmed',state);
   }
   const month=state.calendar.months.find(m=>m.month===targetMonth);
   if(!month||month.disabled)throw diagnostic('moka_calendar_month_unavailable',state);
   await click(month.selector,'calendar_month_selection');
   const changedUntil=Math.min(end,Date.now()+1500);
   do{state=await read();if(!sameOwner(state))throw diagnostic('moka_calendar_owner_changed',state);
      if(state.committed||state.calendar?.mode!=='month')break;await new Promise(r=>setTimeout(r,50));}while(Date.now()<changedUntil);
   if(monthPrecision&&state.committed&&state.tracked_popup_visible===false&&state.actual===op.value)return;
   throw diagnostic(state.committed&&state.tracked_popup_visible===false&&state.actual===op.value.slice(0,7)?'moka_calendar_month_precision_observed':state.actual===op.value?'moka_calendar_full_date_readback_observed':'moka_calendar_day_variant_requires_driver',state);
 }
};
