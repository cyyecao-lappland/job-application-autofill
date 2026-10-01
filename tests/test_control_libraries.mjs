import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {entries} from '../browser/controls/registry.generated.mjs';
import {matchRegistered} from '../browser/rules/service.mjs';
import {executeFieldControl,matchControl,registryReport} from '../browser/controls/service.mjs';

test('all known field operations resolve through registered recognition rules',()=>{
  const cases=[
    [{kind:'text'},'plain_text_input_v1'],
    [{kind:'text',component:'ant-date'},'ant_date_input_v1'],
    [{kind:'text',component:'element-date',readonly:true},'element_date_picker_v1'],
    [{kind:'text',component:'element-date-now',readonly:true},'element_date_now_picker_v1'],
    [{kind:'checkbox',component:'next-checkbox'},'checkbox_v1'],
    [{kind:'checkbox'},'checkbox_v1'],[{kind:'radio'},'radio_v1'],
    [{kind:'radio_group'},'radio_group_v1'],[{kind:'file'},'file_upload_v1'],
    [{kind:'select',multiple:true},'native_select_v1'],
    ...['element-select','next-select',null].map(component=>[{kind:'combobox',component},'combobox_v1'])
  ];
  for(const [field,name] of cases){
    assert.equal(matchRegistered(field,entries,'field').config.name,name);
    assert.equal(matchRegistered({...field,label:'another page',selector:'#changed'},entries,'field').config.name,name);
  }
  assert.equal(matchControl({kind:'text',control_status:'unverified_text_candidate'}),'plain_text_probe_v1');
  assert.equal(matchControl({kind:'combobox',component:'aria-search-select'}),'aria_search_select_v1');
});

test('aliases can share a control while conflicting controls are rejected',()=>{
  const first={config:{name:'one'},rules:[{route:'field',match:()=>true},{route:'field',match:()=>true}]};
  assert.equal(matchRegistered({},new Map([['one',first]]),'field'),first);
  assert.throws(()=>matchRegistered({},new Map([['one',first],['two',{...first,config:{name:'two'}}]]),'field'),/AMBIGUOUS_ADAPTER/);
});

test('unsupported and record-level range targets never fall through to plain text',async()=>{
  for(const field of [{kind:'unsupported'},{kind:'text',control_pattern:'next_range_date'},{kind:'combobox',control_pattern:'next_range_date'}]){
    assert.equal(matchControl(field),null);
    await assert.rejects(executeFieldControl({field}),/unknown_control_method/);
  }
});

test('registry links rule and control modules without containing recognition predicates',()=>{
  const catalog=JSON.parse(readFileSync(new URL('../edge_form_graph/control_catalog.json',import.meta.url),'utf8'));
  assert.equal(registryReport().controls.length,catalog.controls.length);
  for(const control of catalog.controls){
    assert.equal(Object.hasOwn(control,'match'),false);
    assert.ok(control.implementation.startsWith('./adapters/'));
    assert.ok(control.rules.length);
    for(const rule of control.rules)assert.ok(rule.implementation.startsWith('../rules/'));
  }
  for(const file of ['recognition.mjs','dom.mjs','popup.mjs','aria_search.mjs','service.mjs']){
    const source=readFileSync(new URL('../browser/rules/'+file,import.meta.url),'utf8');
    assert.doesNotMatch(source,/from\s+['"].*(?:controls|edge_executor|profile|knowledge)/);
  }
});
