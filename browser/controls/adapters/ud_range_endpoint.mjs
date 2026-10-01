import {Deferred,Conflict,milliseconds,fieldLocator} from '../runtime.mjs';

export const driver={
 declaration:{name:'ud_range_endpoint_v1',adapterVersion:1,targetTypes:['date'],protocolVersion:1},
 async applyField({tab,packet,field,op,end,markAction}){
  if(typeof op.value!=='string'||!/^\d{4}-\d{2}$/.test(op.value))throw new Deferred('explicit_month_required');
  const [year,month]=op.value.split('-').map(Number);
  if(year<1||month<1||month>12)throw new Deferred('invalid_date');
  const target=fieldLocator(tab,packet,field);
  const read=()=>target.evaluate(el=>{
   const owner=el.closest('.throne-biz-date-range-picker-wrapper');
   const inputs=[...(owner?.querySelectorAll('input.ud__native-input')||[])];
   const label=owner?.closest('.ud-formily-item')?.querySelector('.ud-formily-item-label');
   return {valid:inputs.length===2&&inputs.includes(el)&&!el.readOnly&&!el.disabled,
    endpoint:inputs.indexOf(el)===0?'start':'end',actual:el.value,
    peer:inputs.find(e=>e!==el)?.value,label:!!label};
  });
  const before=await read();
  if(!before.valid||before.endpoint!==field.range_endpoint||!before.label)throw new Deferred('ud_range_structure_changed');
  if(before.actual!==field.value)throw new Conflict('control_user_value_changed');
  if(before.actual===op.value)return;
  markAction();await target.fill(op.value,{timeoutMs:milliseconds(end)});
  await target.press('Enter',{timeoutMs:milliseconds(end)});
  // Blur to the current row label; never focus or modify the other endpoint.
  const label=target.locator('xpath=ancestor::*[contains(concat(" ",normalize-space(@class)," ")," ud-formily-item ")][1]').locator('.ud-formily-item-label');
  if(await label.count()!==1)throw new Deferred('ud_range_label_not_unique');
  await label.click({timeoutMs:milliseconds(end)});
  const until=Math.min(end,Date.now()+1800);
  do{
   const state=await read();
   if(state.valid&&state.actual===op.value&&state.peer===before.peer)return;
   await new Promise(resolve=>setTimeout(resolve,60));
  }while(Date.now()<until);
  throw new Conflict('ud_range_endpoint_not_committed');
 }
};
