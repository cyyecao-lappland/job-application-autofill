import test from 'node:test';
import assert from 'node:assert/strict';
import {resolveCollectionAdd} from '../browser/rules/collection_add.mjs';
const packet={inline_repeater:true,collection_selector:'#project',record_selector:'.row',
              expected_record_count:1,add_label:'添加项目经历'};
const section={selector:'#project',record_selector:'.row',records:[{}],
               add_label:'添加项目经历',add_selector:':scope > div:nth-of-type(2) > button'};
test('reacquires a moved Ant Add only within the same collection and unchanged count',()=>{
 assert.equal(resolveCollectionAdd(packet,{family:'ant',sections:[section]},1),section.add_selector);
 for(const changed of [{selector:'#other'},{record_selector:'.different'},{records:[]},
                       {add_label:'删除项目经历'},{add_selector:'#global'}]){
  assert.equal(resolveCollectionAdd(packet,{family:'ant',sections:[{...section,...changed}]},1),null);
 }
 assert.equal(resolveCollectionAdd(packet,{family:'ant',sections:[section,section]},1),null);
 assert.equal(resolveCollectionAdd(packet,{family:'ant',sections:[section]},2),null);
 assert.equal(resolveCollectionAdd({...packet,inline_repeater:false},{family:'ant',sections:[section]},1),null);
 assert.equal(resolveCollectionAdd(packet,{family:'moka',sections:[section]},1),null);
});
test('Feishu moved Add uses the same strict collection and count checks',()=>{
 assert.equal(resolveCollectionAdd(packet,{family:'feishu',sections:[section]},1),section.add_selector);
 assert.equal(resolveCollectionAdd(packet,{family:'feishu',sections:[section]},2),null);
 assert.equal(resolveCollectionAdd(packet,{family:'feishu',sections:[{...section,record_selector:'.other'}]},1),null);
});
