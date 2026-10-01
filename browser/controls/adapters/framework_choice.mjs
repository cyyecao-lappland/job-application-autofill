import {readFrameworkChoice} from '../../rules/framework_choice.mjs';
import {Deferred,Conflict,milliseconds,fieldLocator,actionStage,sameValue} from '../runtime.mjs';

function driver(name,component,multi=false){return {
 declaration:{name,adapterVersion:component==='moka-select'?4:component==='atsx-select'?3:2,targetTypes:multi?['choice','choice_set']:['choice'],protocolVersion:1},
 async applyField({tab,packet,field,op,end,markAction}){
  const wanted=Array.isArray(op.value)?op.value:[op.value];
  if(!wanted.length||wanted.some(v=>typeof v!=='string'||!v.trim())||new Set(wanted).size!==wanted.length)throw new Deferred('choice_labels_required');
  let baseline=null;
  const read=async()=>{const current=await tab.playwright.evaluate(readFrameworkChoice,{moduleSelector:packet.module_selector,selector:field.selector,component,baseline});if(baseline&&current.menu)baseline.bound_menu=current.menu;return current;};
  let state=await read();if(state.error||state.disabled)throw new Deferred(state.error||'control_disabled');
  if(state.multiple&&!multi||!state.multiple&&Array.isArray(op.value))throw new Deferred('unsupported_choice_cardinality');
  if(sameValue(state.actual,op.value)&&state.committed&&state.selection_present)return;
  if(state.visible.length)throw new Deferred('preexisting_popup');
  baseline={owner:state.owner,visible:state.visible};
  const target=fieldLocator(tab,packet,field),deadline=Math.min(end,Date.now()+8500);
  let opened=false;
  const dismiss=async()=>{
   const current=await read();if(current.error)throw new Deferred(current.error);
   if(component==='moka-select'&&current.owner!==baseline.owner)throw new Deferred('framework_owner_changed');
   if(!current.visible.length)return;
   if(!current.menu)throw new Deferred('popup_ownership_lost');
   markAction();await target.press('Escape',{timeoutMs:milliseconds(end)});
   if(current.dismiss){markAction();await tab.playwright.locator(current.dismiss).click({timeoutMs:milliseconds(end)});}
   else await target.press('Tab',{timeoutMs:milliseconds(end)});
   const until=Math.min(end,Date.now()+1200);
   do{state=await read();if(!state.visible.length)return;await new Promise(r=>setTimeout(r,60));}while(Date.now()<until);
   throw new Deferred('owned_popup_not_closed');
  };
  try{
   actionStage(tab,'popup_open');markAction();await tab.playwright.locator(state.owner).click({timeoutMs:milliseconds(deadline)});opened=true;
   let searched=false,selected=false;const observedMokaOptions=new Set();
   while(Date.now()<deadline){
    state=await read();if(state.error)throw new Deferred(state.error);
    if(component==='moka-select'&&state.owner!==baseline.owner)throw new Deferred('framework_owner_changed');
    if(state.variant!=='choice')throw new Deferred('framework_popup_requires_'+state.variant+'_driver');
    if(state.multiple&&!multi)throw new Deferred('framework_multiselect_not_supported');
    const available=state.options.filter(o=>!o.disabled);
    if(component==='moka-select'&&state.menu)for(const option of available)observedMokaOptions.add(option.label);
    if(state.multiple){
     const actual=Array.isArray(state.actual)?state.actual:[];
     if(actual.some(v=>!wanted.includes(v)))throw new Deferred('multiselect_existing_extra_values');
     if(wanted.every(v=>actual.includes(v)))break;
     if(state.menu&&available.length){
      const missing=wanted.filter(v=>!actual.includes(v));
      if(!missing.every(v=>available.filter(o=>o.label===v).length===1))throw new Deferred('multiselect_option_missing_or_ambiguous');
      const option=available.find(o=>o.label===missing[0]);markAction();await tab.playwright.locator(option.selector).click({timeoutMs:milliseconds(deadline)});
      const settledUntil=Math.min(deadline,Date.now()+1200);
      do{state=await read();if(Array.isArray(state.actual)&&state.actual.includes(missing[0]))break;await new Promise(r=>setTimeout(r,60));}while(Date.now()<settledUntil);
      if(!Array.isArray(state.actual)||!state.actual.includes(missing[0]))throw new Conflict('multiselect_token_not_committed');
      continue;
     }
    }else{
     const matches=available.filter(o=>o.label===wanted[0]);
     if(matches.length>1)throw new Deferred('option_ambiguous');
     if(matches.length===1){actionStage(tab,'popup_select');markAction();await tab.playwright.locator(matches[0].selector).click({timeoutMs:milliseconds(deadline)});selected=true;break;}
     if((state.menu||state.pre_popup_search)&&state.search&&!searched){
      const prePopup=state.pre_popup_search,searchSelector=state.search;
      searched=true;markAction();await tab.playwright.locator(searchSelector).fill(wanted[0],{timeoutMs:milliseconds(deadline)});
      if(component==='moka-select'){
       state=await read();if(state.error)throw new Deferred(state.error);
       if(state.owner!==baseline.owner)throw new Deferred('framework_owner_changed');
       if(state.search!==searchSelector||(prePopup&&!state.menu&&!state.pre_popup_search))throw new Deferred('search_input_changed');
      }
     }
     else if(state.menu&&available.length&&!state.search)break;
    }
    await new Promise(r=>setTimeout(r,80));
   }
   if(state.multiple)await dismiss();
   const verifyEnd=Math.min(end,Date.now()+1500);
   do{state=await read();if(sameValue(state.actual,op.value)&&state.committed&&state.selection_present)return;await new Promise(r=>setTimeout(r,60));}while(Date.now()<verifyEnd);
   if(state.menu){const error=new Deferred('option_missing_or_uncommitted');const options=component==='moka-select'?[...observedMokaOptions]:[...new Set(state.options.filter(o=>!o.disabled).map(o=>o.label))];if(options.length)error.enumCandidate={field_id:field.id,source_value:op.value,options};throw error;}
   if(!selected&&!state.multiple)throw new Deferred('framework_owned_menu_unavailable');
   throw new Conflict('framework_selection_not_committed');
  }catch(error){
   if(error instanceof Deferred||error instanceof Conflict){
    const detail=await tab.playwright.evaluate(readFrameworkChoice,{moduleSelector:packet.module_selector,selector:field.selector,component,baseline,diagnostic:true});
    error.controlDiagnostic={field_id:field.id,component,reason:error.message,...detail};
   }
   throw error;
  }finally{if(opened)await dismiss();}
 }
};}
export const antSelect=driver('ant_select_v1','ant-select');
export const mokaSelect=driver('moka_select_v1','moka-select');
export const phoenixSelect=driver('phoenix_select_v1','phoenix-select');
export const atsxSelect=driver('atsx_select_v1','atsx-select',true);
export const udSelect=driver('ud_select_v1','ud-select');
