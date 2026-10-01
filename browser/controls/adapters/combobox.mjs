import {Deferred,Conflict,milliseconds,fieldLocator,normalize,sameValue,actionStage} from '../runtime.mjs';
import {readPopupDOM,readTagSelectionDOM,readComboCommitDOM} from '../../rules/popup.mjs';
import {readModuleDOM} from '../../rules/dom.mjs';
import {classifyComboInteraction} from '../../rules/recognition.mjs';
import {firstEnabledOption} from '../../fallback_policy.mjs';
import {searchOwnedOptions,SearchSelectError,optionMatches} from '../search_select.mjs';
async function popup(tab,packet,field,deadline,baseline=null){
  let state;
  do{
    milliseconds(deadline);
    state=await tab.playwright.evaluate(readPopupDOM,{moduleSelector:packet.module_selector,selector:field.selector,baseline},{timeoutMs:milliseconds(deadline)});
    if(!state.error&&!state.busy)return state;
    if(state.error&&state.error!=='popup_not_unique_or_not_loaded')throw new Deferred(state.error);
    // Short condition polling; never fixed multi-second waits or an LLM turn per poll.
    await new Promise(resolve=>setTimeout(resolve,60));
  }while(Date.now()<deadline);
  throw new Deferred('popup_not_ready');
}

export async function chooseCombo(tab,packet,field,value,deadline,markAction,trace=async()=>{},firstOption=false){
  actionStage(tab,'popup_open');
  const target=fieldLocator(tab,packet,field);
  if(Array.isArray(value))throw new Deferred('custom_multi_requires_verified_selected_value_reader');
  if(field.selection_mode==='tag'&&!firstOption){
    let selected;
    for(let i=0;i<20;i++){
      selected=await tab.playwright.evaluate(readTagSelectionDOM,{moduleSelector:packet.module_selector,selector:field.selector});
      if(selected.error)throw new Deferred(selected.error);
      const extra=selected.tags.find(t=>normalize(t.label)!==normalize(value));
      if(!extra)break;
      if(!extra.remove)throw new Deferred('tag_removal_unavailable');
      markAction();await tab.playwright.locator(extra.remove).click({timeoutMs:milliseconds(deadline)});
    }
    if(selected.tags.some(t=>normalize(t.label)!==normalize(value)))throw new Deferred('tag_removal_limit');
    if(selected.tags.length===1){
      markAction();await target.press('Escape',{timeoutMs:milliseconds(deadline)});
      markAction();await target.press('Tab',{timeoutMs:milliseconds(deadline)});
      return value;
    }
    field={...field,expanded:selected.expanded};
  }
  let baseline=null;
  let menu=null;
  if(field.component==='element-select'){
    baseline=await tab.playwright.evaluate(readPopupDOM,{moduleSelector:packet.module_selector,selector:field.selector,captureBaseline:true},{timeoutMs:milliseconds(deadline)});
    if(baseline.error)throw new Deferred(baseline.error);
    if(baseline.visible.length){
      menu=await tab.playwright.evaluate(readPopupDOM,{moduleSelector:packet.module_selector,selector:field.selector,baseline,resumeExisting:true},{timeoutMs:milliseconds(deadline)});
      if(menu.error){
        // Focusing/clicking the observed select is an ordinary UI operation, not
        // permission to select an option from an unrelated existing popup.
        markAction();await target.click({timeoutMs:milliseconds(deadline)});
        const settleEnd=Math.min(deadline,Date.now()+1200);
        do{
          menu=await tab.playwright.evaluate(readPopupDOM,{moduleSelector:packet.module_selector,selector:field.selector,baseline,resumeExisting:true},{timeoutMs:milliseconds(deadline)});
          if(!menu.error)break;
          const visible=await tab.playwright.evaluate(readPopupDOM,{moduleSelector:packet.module_selector,selector:field.selector,captureBaseline:true},{timeoutMs:milliseconds(deadline)});
          if(!visible.error&&!visible.visible.length)break;
          // Element UI enter/leave animation can briefly show both menus after
          // click has returned. Wait for a condition, not another model turn.
          if(Date.now()<settleEnd)await new Promise(resolve=>setTimeout(resolve,60));
        }while(Date.now()<settleEnd);
        if(menu.error){
          baseline=await tab.playwright.evaluate(readPopupDOM,{moduleSelector:packet.module_selector,selector:field.selector,captureBaseline:true},{timeoutMs:milliseconds(deadline)});
          if(baseline.error||baseline.visible.length)throw new Deferred('preexisting_popup');
          menu=null; // The click closed this menu. Open once below with a clean baseline.
          field={...field,expanded:'false'};
        }
      }
    }
    await trace('baseline_read',{baseline});
  }
  if(!menu&&field.expanded!=='true'){await trace('open_issued');markAction();await target.click({timeoutMs:milliseconds(deadline)});await trace('open_returned');}
  if(!menu&&field.component==='element-select'){
    // Filterable Element inputs may be readonly until their first focus.
    // Re-read the same owned control after opening before deciding whether
    // search is supported; never infer editability from its placeholder.
    const snapshot=await tab.playwright.evaluate(readModuleDOM,{moduleSelector:packet.module_selector});
    const candidates=snapshot.fields.filter(f=>f.id===field.id&&f.selector===field.selector&&f.label===field.label&&f.kind===field.kind);
    if(candidates.length!==1)throw new Conflict('control_identity_changed_after_open');
    const fresh=candidates[0];
    field={...field,readonly:fresh.readonly,disabled:fresh.disabled,search_evidence:fresh.search_evidence};
  }
  if(!menu&&classifyComboInteraction(field).method==='search_select'){
    menu=await tab.playwright.evaluate(readPopupDOM,{moduleSelector:packet.module_selector,selector:field.selector,baseline},{timeoutMs:milliseconds(deadline)});
    if(menu.error==='popup_not_unique_or_not_loaded'){
      if(field.readonly||field.disabled)throw new Deferred('search_input_not_editable');
      menu={options:[],search:{location:'field'},pending:true};
    }
    else if(menu.error)throw new Deferred(menu.error);
  }
  menu=menu||await popup(tab,packet,field,deadline,baseline);
  await trace('menu_identified',{selector:menu.selector,ownership:menu.ownership});
  if(firstOption){
    const first=firstEnabledOption(menu.options);
    if(!first)throw new Deferred(menu.search?'search_query_required':'fallback_no_available_option');
    value=first.label;
    if(field.selection_mode==='tag'){
      let selected=await tab.playwright.evaluate(readTagSelectionDOM,{moduleSelector:packet.module_selector,selector:field.selector});
      if(selected.error)throw new Deferred(selected.error);
      for(const extra of selected.tags.filter(t=>normalize(t.label)!==normalize(value))){
        if(!extra.remove)throw new Deferred('tag_removal_unavailable');
        markAction();await tab.playwright.locator(extra.remove).click({timeoutMs:milliseconds(deadline)});
      }
      selected=await tab.playwright.evaluate(readTagSelectionDOM,{moduleSelector:packet.module_selector,selector:field.selector});
      if(selected.tags.length===1&&normalize(selected.tags[0].label)===normalize(value)){
        markAction();await target.press('Escape',{timeoutMs:milliseconds(deadline)});
        markAction();await target.press('Tab',{timeoutMs:milliseconds(deadline)});
        return value; // Clicking an already selected tag would deselect it.
      }
      menu=await popup(tab,packet,field,deadline,baseline);
    }
  }
  const find=()=>menu.options.filter(o=>optionMatches(o.label,value)&&!o.disabled);
  // A finite owned Element list is enum evidence. Do not turn the source label
  // into search text before the enum reviewer has selected an exact site label.
  if((find().length!==1||menu.busy)&&menu.search&&!(field.component==='element-select'&&menu.options.length)){
    const search=menu.search.location==='field'?target:menu.search.location==='inside'?target.locator(menu.search.selector):
      tab.playwright.locator(menu.selector).locator(menu.search.selector);
    actionStage(tab,'popup_search');
    if(await search.count()!==1)throw new Deferred('search_not_unique');
    if((menu.search.location==='field'&&field.readonly)||
        typeof search.getAttribute==='function'&&await search.getAttribute('readonly')!==null)
      throw new Deferred('search_input_not_editable');
    try{
      menu=await searchOwnedOptions({query:value,
        searchText:/^[^-]+(?:-[^-]+){2,}$/.test(value)?value.split('-').at(-1):value,deadline,
        fill:async query=>{markAction();await search.fill(query,{timeoutMs:milliseconds(deadline)});},
        readMenu:()=>tab.playwright.evaluate(readPopupDOM,{moduleSelector:packet.module_selector,selector:field.selector,baseline},{timeoutMs:milliseconds(deadline)})});
    }catch(error){
      if(error instanceof SearchSelectError){
        const deferred=new Deferred(error.message);
        if(error.menu?.options?.length)deferred.enumCandidate={field_id:field.id,source_value:value,
          options:[...new Set(error.menu.options.filter(o=>!o.disabled).map(o=>o.label))]};
        throw deferred;
      }
      throw error;
    }
  }
  if(find().length!==1){
    // Only a proven owned Element menu can be toggled closed without choosing an answer.
    if(field.component==='element-select'&&menu.ownership){
      actionStage(tab,'popup_close');
      markAction();await target.click({timeoutMs:milliseconds(deadline)});
      await tab.playwright.locator(menu.selector).waitFor({state:'hidden',timeoutMs:milliseconds(deadline)});
      const state=await tab.playwright.evaluate(readModuleDOM,{moduleSelector:packet.module_selector,...packet.capture},{timeoutMs:milliseconds(deadline)});
      if(!sameValue(state.fields?.find(f=>f.id===field.id)?.value,field.value))throw new Conflict('value_changed_during_popup_close');
      const error=new Deferred('option_missing_or_ambiguous');
      error.enumCandidate={field_id:field.id,source_value:value,options:[...new Set(menu.options.filter(o=>!o.disabled).map(o=>o.label))]};
      throw error;
    }
    throw new Deferred('option_missing_or_ambiguous');
  }
  const chosen=find()[0];
  actionStage(tab,'popup_select');
  const option=chosen.selector?tab.playwright.locator(chosen.selector):tab.playwright.locator(menu.selector).getByRole('option',{name:chosen.label,exact:true});
  if(await option.count()!==1)throw new Deferred('option_not_unique');
  await trace('select_issued');markAction();await option.click({timeoutMs:milliseconds(deadline)});await trace('select_returned');
  if(field.component==='next-select'){
    if(field.selection_mode==='tag'){
      markAction();await target.press('Escape',{timeoutMs:milliseconds(deadline)});
      markAction();await target.press('Tab',{timeoutMs:milliseconds(deadline)});
    }
    await tab.playwright.locator(menu.selector).waitFor({state:'hidden',timeoutMs:milliseconds(deadline)});
  }
  if(field.component==='element-select'){
    actionStage(tab,'popup_close');
    await tab.playwright.locator(menu.selector).waitFor({state:'hidden',timeoutMs:milliseconds(deadline)});
    await target.evaluate(el=>el.blur());
    let committed=false;
    const commitEnd=Math.min(deadline,Date.now()+1200);
    do{
      committed=await tab.playwright.evaluate(readComboCommitDOM,{moduleSelector:packet.module_selector,
        selector:field.selector,menuSelector:menu.selector,value},{timeoutMs:milliseconds(deadline)});
      if(committed)break;
      if(Date.now()<commitEnd)await new Promise(resolve=>setTimeout(resolve,60));
    }while(Date.now()<commitEnd);
    if(!committed)throw new Conflict('selection_not_committed');
    await trace('selection_verified');
  }
  return value;
}


export const driver = {
  declaration:{"name": "combobox_v1", "adapterVersion": 1, "targetTypes": ["choice"], "protocolVersion": 1},
  async applyField({tab,packet,field,op,end,markAction,readField}) {
    const chosen=await chooseCombo(tab,packet,field,op.value,end,markAction,async()=>{},op.fallback==='first_option');
    if(op.fallback==='first_option')op.value=chosen;
  }
};
