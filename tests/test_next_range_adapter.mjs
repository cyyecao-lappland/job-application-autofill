import test from 'node:test';
import assert from 'node:assert/strict';
import {runNextRangeDateAdapter} from '../browser/control_adapters.mjs';

function fixture({actual=['',''],owner=true,wrong=false,expanded='true'}={}){
  const calls=[],queued=[{range:true,readonly:true,expanded,values:actual},actual[0],owner,
    wrong?['2024-09','2027-06']:['2024-09','2027-07']];
  const target={count:async()=>1,evaluate:async()=>queued.shift()};
  const input={count:async()=>1,getAttribute:async()=>'YYYY-MM',fill:async v=>calls.push(v),press:async()=>{}};
  const panel={count:async()=>1,locator:()=>input,getByRole:()=>({count:async()=>1,isEnabled:async()=>true,click:async()=>calls.push('confirm')}),waitFor:async()=>{}};
  return {calls,tab:{playwright:{locator:s=>s==='.next-range-picker-body:visible'?panel:{locator:()=>target}}}};
}
const packet={module_selector:'#module',field_selector:'input',before_value:'',
  location:{start:'2024-09-06',end:'2027-07-01'}};
test('month range fills both endpoints and verifies committed pair',async()=>{
  const f=fixture();const result=await runNextRangeDateAdapter(f.tab,packet,async()=>{});
  assert.equal(result.endpoints_verified,2);assert.deepEqual(f.calls,['2024-09','2027-07','confirm']);
});
test('correct range is checked without any browser writes',async()=>{
  const f=fixture({actual:['2024-09','2027-07'],expanded:'false'});
  assert.equal((await runNextRangeDateAdapter(f.tab,packet,async()=>{})).already_matched,true);
  assert.deepEqual(f.calls,[]);
});
test('unowned panel and concurrent user edits produce zero writes',async()=>{
  for(const options of [{owner:false},{actual:['2025-01','']}]){
    const f=fixture(options);await assert.rejects(runNextRangeDateAdapter(f.tab,packet,async()=>{}));assert.deepEqual(f.calls,[]);
  }
});
test('wrong endpoint readback cannot become committed success',async()=>{
  const f=fixture({wrong:true});await assert.rejects(runNextRangeDateAdapter(f.tab,packet,async()=>{}),/range_readback_mismatch/);
});

test('ongoing range requires an explicit present checkbox before any date write',async()=>{
  const calls=[];
  const target={count:async()=>1,evaluate:async()=>({range:true,readonly:true,values:['','']})};
  const root={locator:s=>s==='input'?target:{filter:()=>({count:async()=>0})}};
  const tab={playwright:{locator:()=>root}};
  await assert.rejects(runNextRangeDateAdapter(tab,{...packet,location:{start:'2024-09-06',end:null,current:true}},async x=>calls.push(x)),/current_range_requires_present_checkbox/);
  assert.deepEqual(calls,[]);
});
