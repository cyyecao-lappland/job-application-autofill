import test from 'node:test';
import assert from 'node:assert/strict';
import {retryAtomicRename} from '../browser/edge_executor.mjs';
test('temporary file sharing lock retries only the identical local rename',async()=>{
  let calls=0;const waits=[];
  await retryAtomicRename('temp','journal',async(a,b)=>{
    assert.equal(a,'temp');assert.equal(b,'journal');
    if(++calls<3)throw Object.assign(new Error('sharing lock'),{code:'EPERM'});
  },async ms=>waits.push(ms));
  assert.equal(calls,3);assert.deepEqual(waits,[20,40]);
});
test('persistent sharing locks and unrelated errors are bounded',async()=>{
  for(const [code,expected] of [['EPERM',6],['ENOENT',1]]){
    let calls=0;
    await assert.rejects(retryAtomicRename('temp','journal',async()=>{
      calls++;throw Object.assign(new Error(code),{code});
    },async()=>{}),{code});
    assert.equal(calls,expected);
  }
});
