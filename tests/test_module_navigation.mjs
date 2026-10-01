import test from 'node:test';
import assert from 'node:assert/strict';
import {activateModuleTab,openModuleEditor} from '../browser/module_navigation.mjs';

test('module navigation clicks once and verifies the new active tab',async()=>{
  let active='联系方式',clicks=0;
  const tab={playwright:{
    evaluate:async()=>['个人信息','联系方式'].map((label,index)=>({index,label,canonical_label:label,active:label===active,disabled:false})),
    waitForTimeout:async()=>{},
    locator:()=>({getByText:label=>({count:async()=>1,isVisible:async()=>true,click:async()=>{clicks++;active=label;}})})
  }};
  assert.deepEqual(await activateModuleTab(tab,{menuSelector:'.item',label:'个人信息'}),{status:'activated',label:'个人信息'});
  assert.equal(clicks,1);
  assert.deepEqual(await activateModuleTab(tab,{menuSelector:'.item',label:'个人信息'}),{status:'already_active',label:'个人信息'});
  assert.equal(clicks,1);
});

test('module navigation refuses ambiguous labels',async()=>{
  const tab={playwright:{evaluate:async()=>[{label:'教育经历',canonical_label:'教育经历',active:false,disabled:false},{label:'教育经历',canonical_label:'教育经历',active:false,disabled:false}],waitForTimeout:async()=>{}}};
  await assert.rejects(activateModuleTab(tab,{menuSelector:'.item',label:'教育经历'}),/not_unique/);
});

test('module navigation waits for a client-rendered menu after reload',async()=>{
  let reads=0,clicks=0,active=false;
  const tab={playwright:{
    evaluate:async()=>++reads<3?[]:[{label:'教育经历',canonical_label:'教育经历',active,disabled:false}],
    waitForTimeout:async()=>{},
    locator:()=>({getByText:()=>({count:async()=>1,isVisible:async()=>true,click:async()=>{clicks++;active=true;}})})
  }};
  assert.deepEqual(await activateModuleTab(tab,{menuSelector:'.item',label:'教育经历',timeoutMs:1000}),
    {status:'activated',label:'教育经历'});
  assert.equal(clicks,1);
  assert.ok(reads>=3);
});

test('module editor opens once and verifies fields appear',async()=>{
  let opened=false,clicks=0;
  const fields={count:async()=>opened?2:0};
  const edit={count:async()=>1,isVisible:async()=>true,innerText:async()=>'编辑',click:async()=>{clicks++;opened=true;}};
  const root={count:async()=>1,isVisible:async()=>true,locator:selector=>selector.startsWith('input')?fields:edit};
  const tab={playwright:{locator:()=>root}};
  assert.deepEqual(await openModuleEditor(tab,{moduleSelector:'#module',editSelector:'.edit'}),{status:'opened'});
  assert.deepEqual(await openModuleEditor(tab,{moduleSelector:'#module',editSelector:'.edit'}),{status:'already_open'});
  assert.equal(clicks,1);
});
