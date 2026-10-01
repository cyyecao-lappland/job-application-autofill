import test from 'node:test';
import assert from 'node:assert/strict';
import {executeControl, registryReport, assertRegistry} from '../browser/controls/service.mjs';
import {isoDate} from '../browser/controls/driver.mjs';
import {runNativePlaywrightDriver} from '../host/native_playwright_driver.mjs';

function textHost({masked=false, failRead=false, mismatch=false, popup=false}={}) {
  let value='', filled=false;
  const actions=[];
  const field={label:'字段',selector:'#field',tag:'INPUT',type:'text',readonly:false,disabled:false};
  const target={count:async()=>1,click:async()=>actions.push('focus'),
    fill:async text=>{value=text;filled=true;actions.push('fill');},
    evaluate:async fn=>{
      if(filled&&failRead)throw new Error('transport_failure');
      return fn({value:filled?(masked?'***':mismatch?'wrong':value):value,blur:()=>actions.push('blur')});
    }};
  const tab={playwright:{locator:()=>({locator:()=>target}),
    evaluate:async()=>({root_count:1,fields:[field],menus:[],dialogs:popup&&actions.length?[{}]:[]})}};
  return {tab,actions};
}
const packet=()=>({adapter:'plain_text_probe_v1',command_id:'one',module_selector:'#module',
  field_selector:'#field',field_label:'字段',before_value:'',deadline:Date.now()/1000+20,
  controlTarget:{kind:'text',text:'target'},controlEvidence:{kind:'text'}});

test('loaded declarations compare independent of ordering and diagnose the mismatched field',()=>{
  const expected=registryReport(), actual=structuredClone(expected);
  actual.controls.reverse();assertRegistry(expected,actual);
  actual.controls[0].adapterVersion++;
  assert.throws(()=>assertRegistry(expected,actual),/adapterVersion: expected/);
  actual.controls.push(actual.controls[0]);
  assert.throws(()=>assertRegistry(expected,actual),/duplicate/);
});
test('registry mismatch prevents even the first focus action',async()=>{
  const host=textHost(),controlRegistry=registryReport();controlRegistry.registryVersion++;
  await assert.rejects(executeControl(host.tab,{...packet(),controlRegistry}),/CONTROL_REGISTRY_MISMATCH/);
  assert.deepEqual(host.actions,[]);
});
test('text service returns separate control and persistence results',async()=>{
  const host=textHost(),r=await executeControl(host.tab,packet());
  assert.equal(r.verification,'match');assert.equal(r.actual,'target');assert.equal(r.committed,true);
  assert.equal(r.persistence,'not_assessed');assert.deepEqual(host.actions,['focus','fill','blur']);
});
test('masked readback is skipped after finished actions, never promoted to match',async()=>{
  const host=textHost({masked:true}),r=await executeControl(host.tab,packet());
  assert.equal(r.verification,'skipped');assert.equal(r.committed,false);assert.equal(r.call,'finished');
  assert.equal(host.actions.filter(a=>a==='fill').length,1);
});
test('read transport failure and real mismatches do not become skipped verification',async()=>{
  await assert.rejects(executeControl(textHost({failRead:true}).tab,packet()),/transport_failure/);
  const mismatch=await executeControl(textHost({mismatch:true}).tab,packet());
  assert.equal(mismatch.verification,'mismatch');assert.equal(mismatch.actual,'wrong');
});
test('focus probing a composite control stops before filling',async()=>{
  const host=textHost({popup:true});
  await assert.rejects(executeControl(host.tab,packet()),/composite_control/);
  assert.deepEqual(host.actions,['focus']);
});
test('date precision is not invented by the compatibility bridge',()=>{
  assert.equal(isoDate({precision:'month',year:2024,month:9},'month'),'2024-09');
  assert.throws(()=>isoDate({precision:'month',year:2024,month:9}),/INSUFFICIENT_DATE_PRECISION/);
  assert.throws(()=>isoDate({precision:'day',year:2026,month:2,day:30}),/invalid_date_target/);
});
test('native host requires Python acceptance before dispatching anything',async()=>{
  const calls=[];
  await assert.rejects(runNativePlaywrightDriver({runDirectory:'C:/synthetic',python:'python'},{
    connect:async()=>({session:{},closeConnection:async()=>calls.push('closed')}),
    command:async args=>{calls.push(args[0]);return {status:'bad_registry'};},
    executeRequest:async()=>calls.push('must_not_execute')
  }),/CONTROL_REGISTRY_MISMATCH/);
  assert.deepEqual(calls,['check-control-registry','closed']);
});
