import test from 'node:test';
import assert from 'node:assert/strict';
import {runInNewContext} from 'node:vm';
import {readControlEvidence} from '../browser/rules/dom.mjs';
test('diagnostics preserve distinct semantic IDs for equal range labels',()=>{
 const html={children:[],tagName:'HTML'};
 const root={parentElement:html,children:[],tagName:'DIV'};html.children=[root];
 const make=(id)=>({id,parentElement:root,tagName:'INPUT',type:'text',className:'date',readOnly:true,disabled:false,
   getAttribute:()=>null,getClientRects:()=>[{}],matches:()=>false,closest:()=>null});
 const start=make('start'),end=make('end');root.children=[start,end];
 root.querySelectorAll=s=>s==='#start'?[start]:s==='#end'?[end]:s==='input,textarea,select'?[start,end]:[];
 const result=runInNewContext('('+readControlEvidence.toString()+')(input)',{
   input:{moduleSelector:'#root',semanticFields:[{id:'begin',selector:'#start',label:'起止时间'},{id:'finish',selector:'#end',label:'起止时间'}]},
   document:{documentElement:html,querySelectorAll:s=>s==='#root'?[root]:[]},getComputedStyle:()=>({visibility:'visible'})});
 assert.deepEqual(Array.from(result.fields,f=>f.semantic_id),['begin','finish']);
 assert.deepEqual(Array.from(result.fields,f=>f.label),['起止时间','起止时间']);
});
