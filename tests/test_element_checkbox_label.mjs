import test from 'node:test';
import assert from 'node:assert/strict';
import {checkbox} from '../browser/controls/adapters/native.mjs';

test('a clipped Element checkbox uses its uniquely owned visible label',async()=>{
  let clicked=0;
  const label={count:async()=>1,click:async()=>{clicked++;}};
  const target={getAttribute:async()=>'el-checkbox__original',locator:selector=>{assert.match(selector,/ancestor::label/);return label;},setChecked:async()=>{throw Error('clipped input must not be clicked');}};
  await checkbox.applyField({tab:{playwright:{locator:()=>({locator:()=>target})}},packet:{module_selector:'#module'},field:{selector:'#choice'},op:{value:true},end:Date.now()+1000,markAction:()=>{}});
  assert.equal(clicked,1);
});

test('an ambiguous Element checkbox label causes no click',async()=>{
  let clicked=0;
  const target={getAttribute:async()=>'el-checkbox__original',locator:()=>({count:async()=>2,click:async()=>{clicked++;}})};
  await assert.rejects(checkbox.applyField({tab:{playwright:{locator:()=>({locator:()=>target})}},packet:{module_selector:'#module'},field:{selector:'#choice'},op:{value:true},end:Date.now()+1000,markAction:()=>{}}),/checkbox_label_not_unique/);
  assert.equal(clicked,0);
});
