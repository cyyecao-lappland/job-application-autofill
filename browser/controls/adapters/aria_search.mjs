import {ariaSearch} from '../../rules/recognition.mjs';
import {readSearchDOM} from '../../rules/aria_search.mjs';
export {readSearchDOM} from '../../rules/aria_search.mjs';
export const driver={
  declaration:{name:'aria_search_select_v1',adapterVersion:1,targetTypes:['choice'],protocolVersion:1},
  detect:evidence=>evidence&&ariaSearch(evidence)?'compatible':'needs_probe',
  async prepare(ctx,packet,target){
    if(target?.kind!=='choice'||target.cardinality!=='single'||typeof target.choice?.label!=='string'||!target.choice.label.trim())
      throw new Error('single_choice_target_required');
    const read=()=>ctx.tab.playwright.evaluate(readSearchDOM,{moduleSelector:packet.module_selector,fieldSelector:packet.field_selector});
    const before=await read();
    if(before.error)throw new Error(before.error);
    if(before.disabled)throw new Error('control_disabled');
    if(before.query!==packet.before_value)throw new Error('control_user_value_changed');
    return {...packet,read,before,choice:target.choice.label};
  },
  async apply(ctx,packet){
    const {read,before,choice}=packet;
    if(before.committed&&before.actual===choice)return {already_matched:true};
    const remaining=()=>{const ms=Math.min(5000,packet.deadline*1000-Date.now());if(ms<=0)throw new Error('SEARCH_INCOMPLETE');return ms;};
    const target=()=>ctx.tab.playwright.locator(packet.module_selector).locator(packet.field_selector);
    await ctx.checkpoint({stage:'search_focus_issued'});await target().click({timeoutMs:remaining()});
    await ctx.checkpoint({stage:'search_fill_issued'});await target().fill(choice,{timeoutMs:remaining()});
    await ctx.checkpoint({stage:'search_fill_returned'});
    let observedBusy=false;
    const initial=JSON.stringify(before.options);
    while(Date.now()<packet.deadline*1000){
      const state=await read();
      if(state.error)throw new Error(state.error);
      if(state.query!==choice)throw new Error('control_user_value_changed');
      observedBusy ||= state.busy;
      const fresh=observedBusy||JSON.stringify(state.options)!==initial;
      if(!state.busy&&state.visible&&fresh){
        const matches=state.options.filter(o=>!o.disabled&&o.label===choice);
        if(matches.length>1)throw new Error('OPTION_AMBIGUOUS');
        if(matches.length===1){
          // Re-read and reacquire after every asynchronous list replacement.
          const latest=await read();
          if(latest.busy||latest.query!==choice)continue;
          const selected=latest.options.filter(o=>!o.disabled&&o.label===choice);
          if(selected.length!==1)continue;
          await ctx.checkpoint({stage:'search_select_issued'});
          await ctx.tab.playwright.locator(latest.menuSelector).getByRole('option',{name:choice,exact:true}).click({timeoutMs:remaining()});
          await ctx.checkpoint({stage:'search_select_returned'});
          return {already_matched:false};
        }
      }
      await new Promise(resolve=>setTimeout(resolve,Math.min(50,remaining())));
    }
    throw new Error('SEARCH_INCOMPLETE');
  },
  async read(ctx,packet,applied){
    const state=await packet.read();
    if(state.error)throw new Error(state.error);
    if(!state.committed)throw new Error('selection_not_committed');
    return {actual:state.actual,applied,reason:state.actual===null?'readback_incomplete':''};
  },
  compare(observation,target){
    return observation.actual===null?'skipped':observation.actual===target.choice.label?'match':'mismatch';
  }
};
