import test from 'node:test';
import assert from 'node:assert/strict';
import {searchOwnedOptions,optionMatches} from '../browser/search_select.mjs';
import {classifyComboInteraction,classifyControl} from '../browser/control_detection.mjs';

const ready=options=>({selector:'#menu',busy:false,options});
const option=label=>({label,disabled:false});
test('dial codes match a whole unique numeric token, never a prefix',async()=>{
  assert.equal(optionMatches('中国大陆 +86','+86'),true);
  for(const label of ['中国澳门 +853','示例 +860','示例 +86 / +1'])
    assert.equal(optionMatches(label,'+86'),false);
  assert.equal(optionMatches('上海市','上海'),false);
  await assert.rejects(scenario([ready([option('A +1'),option('B +1')])],'+1').run(),/ambiguous/);
});
function scenario(states,query='目标'){
  let clock=0,reads=0;const writes=[];
  return {writes,get reads(){return reads;},run:()=>searchOwnedOptions({query,deadline:300,now:()=>clock,
    pause:async ms=>{clock+=ms;},fill:async value=>writes.push(value),
    readMenu:async()=>states[Math.min(reads++,states.length-1)]})};
}

test('search selection requires dropdown structure, never a business label',()=>{
  for(const label of ['学校','城市','论文']){
    assert.equal(classifyComboInteraction({kind:'text',label}).method,null);
    assert.equal(classifyControl({kind:'combobox',label,search_evidence:{editable:true,selection_structure:true}}),'search_select');
    assert.equal(classifyComboInteraction({kind:'combobox',search_evidence:{editable:false,selection_structure:true}}).method,'select_option');
  }
  assert.equal(classifyComboInteraction({kind:'combobox',search_evidence:{editable:true,selection_structure:false}}).method,'select_option');
  const r=classifyComboInteraction({kind:'combobox'},{search:{location:'popup',selector:'#search'}});
  assert.equal(r.method,'search_select');assert.equal(r.confirmed,true);
});

test('types before menu exists and waits for asynchronous candidates',async()=>{
  const s=scenario([{error:'popup_not_unique_or_not_loaded'},
    {selector:'#old',busy:true,options:[option('目标')]},ready([]),ready([option('目标')])]);
  const result=await s.run();assert.equal(result.selector,'#menu');
  assert.deepEqual(s.writes,['目标']);assert.equal(s.reads,4);
});

test('uses the current menu and option selector after a render replaces them',async()=>{
  const s=scenario([ready([option('旧项')]),{selector:'#new',busy:false,options:[{...option('目标'),selector:'#new-option'}]}]);
  assert.equal((await s.run()).options[0].selector,'#new-option');
});

test('duplicate labels, disabled-only matches and missing queries do not become success',async()=>{
  await assert.rejects(scenario([ready([option('目标'),option('目标')])]).run(),/ambiguous/);
  await assert.rejects(scenario([ready([{label:'目标',disabled:true}])]).run(),/timeout/);
  for(const query of ['', '__FIRST_ENABLED_OPTION__']){
    const s=scenario([ready([])],query);await assert.rejects(s.run(),/query_required/);assert.deepEqual(s.writes,[]);
  }
});

test('losing popup ownership stops search instead of selecting from another menu',async()=>{
  await assert.rejects(scenario([{error:'popup_focus_changed'}]).run(),/popup_focus_changed/);
});

test('typing alone without actual options times out',async()=>{
  await assert.rejects(scenario([ready([])]).run(),/search_options_timeout/);
});

test('short search phrase must still select the complete path, not a same-name city',async()=>{
  const writes=[];
  const menu=await searchOwnedOptions({query:'中国-湖南-衡阳',searchText:'衡阳',deadline:Date.now()+1000,
    fill:async q=>writes.push(q),readMenu:async()=>ready([option('其他-衡阳'),option('中国-湖南-衡阳')])});
  assert.deepEqual(writes,['衡阳']);assert.equal(menu.options.length,2);
});
