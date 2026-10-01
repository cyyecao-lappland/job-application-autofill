import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
const AsyncFunction=Object.getPrototypeOf(async function(){}).constructor;
const source=await readFile(new URL('../host/playwright_driver.js',import.meta.url),'utf8');

test('Playwright host uses only the session executor entrypoint',async()=>{
  const pending=['observe','fill',null],calls=[];
  const tools={
    exec_command:async()=>({exit_code:0,output:JSON.stringify({status:'observed',pending_kind:pending.shift()})}),
    mcp__node_repl__js:async args=>{calls.push(args.code);return {isError:false};}
  };
  await new AsyncFunction('tools','notify','options',source)(tools,()=>{},
    {application:true,projectDirectory:'C:/example',runDirectory:'C:/example/run'});
  assert.equal(calls.length,2);
  assert.ok(calls.every(code=>code.includes('.runSessionRequest(playwrightSession,')));
  assert.ok(calls.every(code=>!code.includes('runRequest(edge,')));
});

test('Playwright host preserves pending state when the browser call fails',async()=>{
  let cliCalls=0;
  const tools={
    exec_command:async()=>{cliCalls++;return {exit_code:0,output:JSON.stringify({status:'observed',pending_kind:'fill'})};},
    mcp__node_repl__js:async()=>({isError:true})
  };
  await assert.rejects(new AsyncFunction('tools','notify','options',source)(tools,()=>{},
    {application:true,projectDirectory:'C:/example',runDirectory:'C:/example/run'}),/pending_preserved/);
  assert.equal(cliCalls,1);
});
