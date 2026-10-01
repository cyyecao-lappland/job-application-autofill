import test from 'node:test';
import assert from 'node:assert/strict';
import {awaitSaveOutcome,confirmCard,classifySaveDialog,readStructuredScopePreview} from '../browser/save_observer.mjs';
import {runInNewContext} from 'node:vm';
const packet={module_selector:'#m',deadline:Date.now()/1000+60,save:{signal:{selector:'#ok',text:'保存成功'}}};
test('save dialogs are classified without treating every OK as confirmation',()=>{
  assert.equal(classifySaveDialog('是否确认保存？',['取消','确认保存']),'SAVE_CONFIRM');
  assert.equal(classifySaveDialog('保存成功',['确定']),'SAVE_SUCCESS');
  assert.equal(classifySaveDialog('数据已被其他用户修改，是否覆盖？',['覆盖']),'CONFLICT');
  assert.equal(classifySaveDialog('提示',['确定']),'UNKNOWN_DIALOG');
});
test('native dialog is detected without accepting or inventing message API',async()=>{
  const r=await awaitSaveOutcome({getJsDialog:async()=>({type:'confirm'})},packet,()=>{});
  assert.equal(r.reason,'native_dialog_requires_review');
});
test('unconfigured DOM dialog is not clicked',async()=>{
  const r=await awaitSaveOutcome({playwright:{evaluate:async()=>({dialog_count:1})}},packet,()=>{});
  assert.equal(r.reason,'dom_dialog_requires_review');
});
test('validation failure does not wait or retry save',async()=>{
  const r=await awaitSaveOutcome({playwright:{evaluate:async()=>({validation_error_count:1})}},packet,()=>{});
  assert.equal(r.reason,'validation_failed');
});
test('hidden card cannot be saved evidence',async()=>{
  const tab={playwright:{locator:()=>({count:async()=>1,isVisible:async()=>false})}};
  assert.equal(await confirmCard(tab,{selector:'#m',editSelector:'form'},['目标']),false);
});

test('Fusion preview needs preview class, no editing controls and all reviewed values',async()=>{
  let controls=0;
  const editor={count:async()=>1,isVisible:async()=>true,getAttribute:async()=> 'next-form next-form-preview',
    locator:()=>({count:async()=>controls})};
  const root={count:async()=>1,isVisible:async()=>true,innerText:async()=> '目标 编辑',locator:()=>editor};
  const tab={playwright:{locator:()=>root,evaluate:async()=>0}};
  const signal={selector:'#m',editSelector:'form'};
  assert.equal(await confirmCard(tab,signal,['目标']),true);
  assert.equal(await confirmCard(tab,signal,['其他']),false);
  controls=1;assert.equal(await confirmCard(tab,signal,['目标']),false);
});
test('empty optional strings do not prevent nonempty card value confirmation',async()=>{
  const editor={count:async()=>0,isVisible:async()=>false};
  const root={count:async()=>1,isVisible:async()=>true,innerText:async()=> '目标 --',locator:()=>editor};
  const tab={playwright:{locator:()=>root}};
  assert.equal(await confirmCard(tab,{selector:'#m',editSelector:'form'},['目标','']),true);
  assert.equal(await confirmCard(tab,{selector:'#m',editSelector:'form'},['目标',false]),false);
});
test('repeatable editor can confirm one matching sibling card after insertion',async()=>{
  const editor={count:async()=>0,isVisible:async()=>false};
  const root={count:async()=>1,isVisible:async()=>true,innerText:async()=> '父亲',locator:()=>editor};
  const tab={playwright:{locator:()=>root,evaluate:async(fn,args)=>{
    assert.equal(fn.name,'readSiblingCardMatch');assert.deepEqual(args.values,['母亲','中共党员']);return 1;
  }}};
  assert.equal(await confirmCard(tab,{selector:'#first',editSelector:'form'},['母亲','中共党员']),true);
});

test('structured saved scope verifies each record and explicit present checkbox',()=>{
  let body='Record A',checked='至今',editing=false;
  const name={getAttribute:()=> 'READONLY',get textContent(){return body;}};
  const present={get textContent(){return checked;}};
  const row={matches:()=>false,querySelector:()=>({}),querySelectorAll:s=>
    s==='#name'?[name]:s.includes('to-present-checkbox')?[present]:[]};
  const edit={textContent:'编辑',getClientRects:()=>[{}]};
  const root={getClientRects:()=>[{}],contains:x=>x===row,querySelectorAll:s=>s.startsWith('input')?(editing?[edit]:[]):[edit]};
  const document={querySelectorAll:s=>s==='#scope'?[root]:s==='#row'?[row]:[]};
  const modules=[{module_selector:'#row',fields:[{kind:'text',selector:'#name',value:'Record A'},
     {kind:'checkbox',label:'至今',value:true}]}];
  const run=()=>runInNewContext('('+readStructuredScopePreview.toString()+')(args)',{
    document,args:{selector:'#scope',modules},getComputedStyle:()=>({visibility:'visible'})});
  assert.equal(run(),true);
  body='Record B';assert.equal(run(),false);
  body='Record A';checked='';assert.equal(run(),false);
  checked='至今';editing=true;assert.equal(run(),false);
});
