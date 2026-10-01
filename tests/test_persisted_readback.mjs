import test from 'node:test';
import assert from 'node:assert/strict';
import {confirmsAbsentEducation} from '../browser/persisted_readback.mjs';
test('absence oracle is limited to empty persisted Alibaba education',()=>{
  const packet={target:{url:'https://campus-talent.alibaba.com/personal/resume'},expected_modules:[{fields:[{label:'学校全称',value:'大学'},{label:'学历',value:'硕士'}]}]};
  const evidence={oracle:'alibaba_resume_detail_v1',education_count:0,observed_at:100};
  assert.equal(confirmsAbsentEducation(packet,evidence),true);
  assert.equal(confirmsAbsentEducation(packet,{...evidence,education_count:1}),false);
  assert.equal(confirmsAbsentEducation({...packet,expected_modules:[]},evidence),false);
  assert.equal(confirmsAbsentEducation({...packet,target:{url:'https://other.test'}},evidence),false);
  assert.equal(confirmsAbsentEducation(packet,null),false);
});
