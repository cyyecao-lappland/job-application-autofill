import test from 'node:test';
import assert from 'node:assert/strict';
import vm from 'node:vm';
import {classifySaveAction,classifySaveScopeEvidence,readSaveScopeEvidence} from '../browser/save_scope_detection.mjs';

test('save labels classify by operation and never confuse final submission',()=>{
  assert.equal(classifySaveAction('保存').pattern,'SAVE_STAY');
  assert.equal(classifySaveAction('保存并下一步').pattern,'SAVE_AND_ADVANCE');
  assert.equal(classifySaveAction('下一步并保存').pattern,'SAVE_AND_ADVANCE');
  assert.equal(classifySaveAction('下一步').pattern,'ADVANCE_WITH_PERSIST');
  assert.equal(classifySaveAction('立即投递').pattern,'FINAL_SUBMIT');
});

test('scope candidates retain ownership and require later verification',()=>{
  const result=classifySaveScopeEvidence({controls:[{label:'保存并下一步',selector:'#save',ownership:'native_form',owner_selector:'#form',persistent_owner:true,member_module_ids:['contact','family']}]});
  assert.deepEqual(result.scopes[0],{candidate_id:'save-scope-0',member_module_ids:['contact','family'],action_pattern:'SAVE_AND_ADVANCE',transition:'step_change',control_selector:'#save',ownership:'native_form',root_selector:'#form',confidence:'strong_candidate',persistent_owner:true,verified:false});
});

test('browser evidence reader has no module closure dependencies',()=>{
  const source='('+readSaveScopeEvidence.toString()+')';
  const fn=vm.runInNewContext(source,{});
  assert.equal(typeof fn,'function');
  assert.equal(source.includes('classifySaveAction'),false);
});
