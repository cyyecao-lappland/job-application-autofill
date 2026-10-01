import test from 'node:test';
import assert from 'node:assert/strict';
import {readFileSync} from 'node:fs';
import {runInNewContext} from 'node:vm';
import * as detection from '../browser/control_detection.mjs';
import * as adapters from '../browser/control_adapters.mjs';
import {readModuleDOM} from '../browser/edge_executor.mjs';

test('legacy imports point to the same detection implementation',()=>{
  assert.equal(readModuleDOM,detection.readModuleDOM);
  for(const key of ['classifyControl','classifyDialog','readControlEvidence'])
    assert.equal(adapters[key],detection[key]);
});

test('field business labels and profile sources do not select a handler',()=>{
  for(const label of ['竞赛介绍','实习工作内容','论文介绍']){
    assert.equal(detection.classifyControl({label,kind:'text',source:'/anything'}),'agent_required');
    assert.equal(detection.classifyControl({label,kind:'combobox',component:'element-select'}),'element_select');
  }
});

test('diagnostic DOM reader survives browser serialization without module scope',()=>{
  const result=runInNewContext('('+detection.readControlEvidence.toString()+')({moduleSelector:"#missing"})',{
    document:{querySelectorAll:()=>[]}
  });
  assert.equal(result.root_count,0);
  assert.equal(result.fields.length,0);
  assert.equal(result.dialogs.length,0);
});

test('detection has no imports from execution or data systems',()=>{
  const source=readFileSync(new URL('../browser/control_detection.mjs',import.meta.url),'utf8');
  assert.doesNotMatch(source,/^import\s/m);
  for(const file of ['edge_executor.mjs','control_adapters.mjs']){
    const consumer=readFileSync(new URL('../browser/'+file,import.meta.url),'utf8');
    assert.doesNotMatch(consumer,/export function (readModuleDOM|readControlEvidence|classifyControl|classifyDialog)\(/);
  }
});
