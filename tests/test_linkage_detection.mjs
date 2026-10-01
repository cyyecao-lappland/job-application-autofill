import test from 'node:test';
import assert from 'node:assert/strict';
import {detectLinkage} from '../browser/linkage_detection.mjs';
test('selection causing a derived disabled field is a linkage barrier',()=>{
  const before={fields:[{id:'paper',value:''},{id:'level',value:'',disabled:false}]};
  const after={fields:[{id:'paper',value:'IM'},{id:'level',value:'CCF-C',disabled:true}]};
  assert.deepEqual(detectLinkage(before,after,'paper'),{trigger_id:'paper',added:[],changed:['level'],removed:[]});
});
test('new hidden and changed option controls are detected, own value is not',()=>{
  const before={fields:[{id:'a',value:''},{id:'b',value:'',options:[]},{id:'gone',value:''}]};
  const after={fields:[{id:'a',value:'yes'},{id:'b',value:'',options:[{label:'new'}]},{id:'new',value:''}]};
  assert.deepEqual(detectLinkage(before,after,'a'),{trigger_id:'a',added:['new'],changed:['b'],removed:['gone']});
  assert.equal(detectLinkage({fields:[{id:'a',value:''}]},{fields:[{id:'a',value:'yes'}]},'a'),null);
});
