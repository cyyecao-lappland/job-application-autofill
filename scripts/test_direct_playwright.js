'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const {createPlaywrightHost}=require('./direct_playwright');
function fixture() {
  const school={tagName:'INPUT',type:'text',value:'School A',id:'school',disabled:false,
    getClientRects:()=>[{}],getAttribute:()=>null};
  const field={...school,value:'',id:'major'};
  const record={getAttribute:key=>key==='data-record-id'?'r1':null,
    querySelectorAll:selector=>selector==='#school'?[school]:selector==='#major'?[field]:[]};
  const document={querySelectorAll:selector=>selector==='#record'?[record]:[]};
  let fills=0;
  const page={evaluate:async(fn,args)=>vm.runInNewContext('('+fn.toString()+')(args)',
      {document,args,getComputedStyle:()=>({visibility:'visible'})}),
    locator:()=>({locator:()=>({fill:async v=>{fills++;field.value=v;},selectOption:async()=>{},check:async()=>{}})})};
  const op={id:'major',label:'Major',kind:'text',before:'',value:'Computer Science',anchor:{label:'School',value:'School A'},
    locator:{selector:'#major',recordSelector:'#record',recordAttribute:'data-record-id',recordRef:'r1',anchorSelector:'#school'}};
  return {page,school,field,op,host:createPlaywrightHost(page,async()=>({}),{}),fills:()=>fills};
}
test('port reads both record ID and semantic anchor; writes via native locator',async()=>{
  const f=fixture();const row=await f.host.inspect(f.op);
  assert.equal(row.anchorMatched,true);assert.equal(row.recordRef,'r1');
  assert.equal((await f.host.write(f.op,{timeoutMs:100})).status,'written');
  assert.equal(f.field.value,'Computer Science');assert.equal(f.fills(),1);
});
test('same record ID with renamed school blocks write',async()=>{
  const f=fixture();f.school.value='School B';
  assert.equal((await f.host.inspect(f.op)).anchorMatched,false);
  assert.equal((await f.host.write(f.op,{timeoutMs:100})).status,'failed');assert.equal(f.fills(),0);
});
test('missing anchor and manual changed value cannot be overwritten',async()=>{
  const f=fixture();delete f.op.locator.anchorSelector;
  assert.equal((await f.host.write(f.op,{timeoutMs:100})).status,'failed');
  const g=fixture();g.field.value='User major';
  assert.equal((await g.host.write(g.op,{timeoutMs:100})).status,'failed');assert.equal(g.fills(),0);
});
test('expired absolute deadline prevents a late native write',async()=>{
  const f=fixture();const r=await f.host.write(f.op,{timeoutMs:100,deadline:Date.now()-1});
  assert.equal(r.status,'failed');assert.equal(f.fills(),0);
});

test('slow locator setup consumes the original deadline',async()=>{
  const f=fixture(),locate=f.page.locator;
  f.page.locator=(...args)=>{const end=Date.now()+15;while(Date.now()<end){};return locate(...args);};
  const r=await f.host.write(f.op,{timeoutMs:100,deadline:Date.now()+5});
  assert.equal(r.status,'failed');assert.equal(f.fills(),0);
});
