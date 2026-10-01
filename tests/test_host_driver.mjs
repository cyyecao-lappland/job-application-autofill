import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
const AsyncFunction=Object.getPrototypeOf(async function(){}).constructor;
const source=await readFile(new URL('../host/codex_driver.js',import.meta.url),'utf8');

test('host continues batches without additional model-authored actions',async()=>{
  const statuses=['awaiting_edge','awaiting_edge','awaiting_save','saved'];
  const calls=[],notices=[];
  const tools={
    exec_command:async args=>{calls.push(['cli',args.cmd]);return {exit_code:0,output:JSON.stringify({status:statuses.shift(),metrics:{}})};},
    mcp__node_repl__js:async args=>{calls.push(['edge',args.code]);return {content:[],isError:false};}
  };
  const result=await new AsyncFunction('tools','notify','options',source)(tools,n=>notices.push(n),{
    projectDirectory:'C:/example',runDirectory:'C:/example/private/run',start:true,profile:'C:/profile.json',snapshot:'C:/snapshot.json'});
  assert.equal(result.status,'saved');assert.equal(calls.filter(x=>x[0]==='edge').length,3);
  assert.ok(calls.filter(x=>x[0]==='edge').every(x=>x[1].includes('.runRequest(edge,resumeTab,')));
});

test('host stops on browser errors and never generates its own receipt',async()=>{
  let cliCalls=0;
  const tools={
    exec_command:async()=>{cliCalls++;return {exit_code:0,output:JSON.stringify({status:'awaiting_edge',metrics:{}})};},
    mcp__node_repl__js:async()=>({isError:true})
  };
  await assert.rejects(new AsyncFunction('tools','notify','options',source)(tools,()=>{},{
    projectDirectory:'C:/example',runDirectory:'C:/example/private/run'}),/pending_preserved/);
  assert.equal(cliCalls,1);
});

test('host does not run a pending unknown command',async()=>{
  const tools={exec_command:async()=>({exit_code:0,output:JSON.stringify({status:'needs_reconciliation',metrics:{}})}),
    mcp__node_repl__js:async()=>{throw Error('must_not_execute');}};
  const result=await new AsyncFunction('tools','notify','options',source)(tools,()=>{},{
    projectDirectory:'C:/example',runDirectory:'C:/example/private/run'});
  assert.equal(result.status,'needs_reconciliation');
});

test('application host drives observe fill and shared save through the same gate',async()=>{
  const pending=['observe','fill','observe','fill','save_scope',null],calls=[];
  const tools={exec_command:async args=>{assert.ok(args.cmd.includes("'application'"));return {exit_code:0,output:JSON.stringify({status:'observed',pending_kind:pending.shift()})};},
    mcp__node_repl__js:async args=>{calls.push(args);return {isError:false};}};
  await new AsyncFunction('tools','notify','options',source)(tools,()=>{},{application:true,projectDirectory:'C:/example',runDirectory:'C:/example/run'});
  assert.equal(calls.length,5);
});
