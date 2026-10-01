import test from 'node:test';
import {errors} from 'playwright-core';
import assert from 'node:assert/strict';
import {createHostedPlaywrightSession,createNativePlaywrightSession,identifyPlaywrightSession} from '../browser/playwright_backend.mjs';
import {executorName} from '../browser/edge_executor.mjs';
import {inspectSaveScopes} from '../browser/playwright_executor.mjs';

test('native locator timeout marks completion without treating transport errors as settled',async()=>{
 let error=new errors.TimeoutError('locator.fill: Timeout 5ms exceeded.');
 const page={evaluate:async()=>{},locator:()=>({fill:async()=>{throw error;}}),url:()=> 'https://example.test'};
 const session=createNativePlaywrightSession(page,{browserId:'edge-native',tabId:'p'});
 await assert.rejects(session.tab.playwright.locator('#x').fill('x'),e=>e===error&&e.settled===true);
 error=new Error('Timeout transport disconnected');
 await assert.rejects(session.tab.playwright.locator('#x').fill('x'),e=>e===error&&e.settled===undefined);
});

test('hosted session preserves the existing browser and tab identity',async()=>{
  const tab={id:'tab-1',url:async()=>'https://example.test/form',playwright:{evaluate:async()=>{},locator:()=>({})}};
  const session=createHostedPlaywrightSession({browserId:'edge-1'},tab);
  assert.equal(session.tab,tab);
  assert.deepEqual(await identifyPlaywrightSession(session),{
    backend:'playwright',transport:'hosted-existing-tab',browser_id:'edge-1',tab_id:'tab-1',url:'https://example.test/form'});
});

test('native session translates timeoutMs without retaining element handles',async()=>{
  const calls=[];
  const locator={
    fill:async(value,options)=>calls.push(['fill',value,options]),
    count:async()=>1,isVisible:async()=>true,isEnabled:async()=>true,
    locator:()=>locator,getByRole:()=>locator,getByText:()=>locator,getByLabel:()=>locator,getByPlaceholder:()=>locator,
    nth:()=>locator,first:()=>locator,last:()=>locator
  };
  const page={evaluate:async()=>({}),locator:()=>locator,getByRole:()=>locator,getByText:()=>locator,
    getByLabel:()=>locator,getByPlaceholder:()=>locator,url:()=> 'https://example.test/form',reload:async()=>{},on:()=>{}};
  const session=createNativePlaywrightSession(page,{browserId:'edge-native',tabId:'page-1'});
  await session.tab.playwright.locator('#field').fill('value',{timeoutMs:321});
  assert.deepEqual(calls,[['fill','value',{timeout:321}]]);
  assert.equal((await identifyPlaywrightSession(session)).transport,'native');
});

test('sessions fail closed when identity or Playwright surface is missing',()=>{
  assert.throws(()=>createHostedPlaywrightSession({browserId:'edge'},{}),/tab_identity_required/);
  assert.throws(()=>createNativePlaywrightSession({}, {browserId:'edge',tabId:'page'}),/native_playwright_page_required/);
});

test('receipts identify the selected execution backend',()=>{
  assert.equal(executorName({backend:'playwright'}),'playwright');
  assert.equal(executorName({browserId:'legacy-edge'}),'codex-edge');
});

test('save scope inspection is read-only and returns candidates',async()=>{
  const evidence={controls:[{label:'保存',selector:'#save',ownership:'nearest_common_ancestor',owner_selector:'#module',member_module_ids:['contact']}]};
  const session={backend:'playwright',tab:{playwright:{evaluate:async()=>evidence}}};
  const result=await inspectSaveScopes(session,{moduleSelectors:[{id:'contact',selector:'#contact'}]});
  assert.equal(result.classification.scopes[0].action_pattern,'SAVE_STAY');
  assert.equal(result.classification.scopes[0].verified,false);
});
