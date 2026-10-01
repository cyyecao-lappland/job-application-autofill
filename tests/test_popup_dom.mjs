import test from 'node:test';
import assert from 'node:assert/strict';
import {readPopupDOM,readComboCommitDOM} from '../browser/edge_executor.mjs';

// Exercise the actual DOM reader rather than mocking its returned menu result.
function fixture(){
  class El {
    constructor(tag,parent=null,text=''){this.tagName=tag;this.parentElement=parent;this.children=[];this.textContent=text;this.show=false;this.selected=false;this.rect={left:0,right:100,top:0,bottom:32,width:100,height:32};if(parent)parent.children.push(this);}
    getClientRects(){return this.show?[{}]:[];}
    getBoundingClientRect(){return this.rect;}
    contains(e){return e===this||this.children.some(c=>c.contains(e));}
    querySelectorAll(s){if(s==='.el-select-dropdown')return menus.filter(m=>this.contains(m));
      if(s==='.el-select-dropdown__item')return this.children;
      if(s==='.el-select-dropdown__item.selected')return this.children.filter(c=>c.selected);
      if(s==='#field')return [field];return [];}
    closest(){return owner;}
    get classList(){return {contains:n=>n==='selected'&&this.selected};}
  }
  const html=new El('HTML'),body=new El('BODY',html),root=new El('DIV',body),owner=new El('DIV',root),field=new El('INPUT',owner);
  field.readOnly=true;field.value='女';
  const menus=[];
  const add=parent=>{const m=new El('DIV',parent);m.rect={left:0,right:100,top:40,bottom:120,width:100,height:80};menus.push(m);for(const name of ['男','女'])new El('LI',m,name);return m;};
  const menu=add(owner);
  globalThis.document={documentElement:html,activeElement:field,querySelectorAll:s=>s==='#root'?[root]:s==='.el-select-dropdown'?menus:s==='#menu'?[menu]:[]};
  globalThis.getComputedStyle=()=>({visibility:'visible'});
  const args={moduleSelector:'#root',selector:'#field'};
  return {args,field,menu,add,body,owner,baseline:()=>readPopupDOM({...args,captureBaseline:true}),
    move(){owner.children=owner.children.filter(n=>n!==menu);menu.parentElement=body;body.children.push(menu);menu.show=true;menu.children.forEach(c=>c.show=true);}};
}
test('menu moved to body is found by the real reader',()=>{
  const f=fixture(),baseline=f.baseline();f.move();
  const r=readPopupDOM({...f.args,baseline});
  assert.equal(r.ownership,'new_visible_matching_menu');assert.deepEqual(r.options.map(o=>o.label),['男','女']);
});

function nextFixture(){
  const f=fixture();f.move();
  Object.defineProperty(f.owner,'classList',{value:{contains:name=>name==='next-select'}});
  f.field.closest=s=>s==='.next-select'?f.owner:null;
  f.menu.closest=()=>null;f.menu.getAttribute=()=>null;
  f.menu.querySelectorAll=()=>f.menu.children;
  for(const option of f.menu.children)option.getAttribute=()=>null;
  const nav={...f.menu,closest:()=>({}),getClientRects:()=>[{}]};
  const original=document.querySelectorAll;
  document.querySelectorAll=s=>s==='.next-select-menu,[role="listbox"]'?[nav,f.menu]:original(s);
  return f;
}

test('Next dropdown ownership excludes navigation and reads real options',()=>{
  const f=nextFixture(),r=readPopupDOM(f.args);
  assert.equal(r.ownership,'focused_aligned_next_menu');
  assert.deepEqual(r.options.map(o=>o.label),['男','女']);
  assert.ok(r.options.every(o=>o.selector));
});

test('Next dropdown rejects focus or alignment belonging to another control',()=>{
  const f=nextFixture();document.activeElement=f.body;
  assert.equal(readPopupDOM(f.args).error,'popup_focus_changed');
  document.activeElement=f.field;f.menu.rect.left=500;f.menu.rect.right=600;
  assert.equal(readPopupDOM(f.args).error,'popup_not_unique_or_not_loaded');
});
test('preexisting visible menu is rejected',()=>{
  const f=fixture();f.add(f.body).show=true;const baseline=f.baseline();f.move();
  assert.equal(readPopupDOM({...f.args,baseline}).error,'preexisting_popup');
});
test('existing menu recovery requires focus and geometric alignment',()=>{
  const f=fixture();f.move();const baseline=f.baseline();
  assert.equal(readPopupDOM({...f.args,baseline,resumeExisting:true}).ownership,'focused_existing_matching_menu');
  document.activeElement=f.body;
  assert.equal(readPopupDOM({...f.args,baseline,resumeExisting:true}).error,'popup_focus_changed');
});
test('focused unrelated popup is rejected by geometric ownership',()=>{
  const f=fixture();f.move();const baseline=f.baseline();f.menu.rect={left:400,right:500,top:40,bottom:120,width:100,height:80};
  assert.equal(readPopupDOM({...f.args,baseline,resumeExisting:true}).error,'popup_ownership_unconfirmed');
});
test('two new visible menus are ambiguous',()=>{
  const f=fixture(),baseline=f.baseline();f.move();f.add(f.body).show=true;
  assert.equal(readPopupDOM({...f.args,baseline}).error,'popup_ambiguous');
});
test('changed focus is not attributed to the original field',()=>{
  const f=fixture(),baseline=f.baseline();f.move();document.activeElement=f.body;
  assert.equal(readPopupDOM({...f.args,baseline}).error,'popup_focus_changed');
});
test('commit requires hidden menu and selected option, not only text',()=>{
  const f=fixture();f.move();f.field.value='男';f.menu.children[0].selected=true;
  const args={...f.args,menuSelector:'#menu',value:'男'};
  assert.equal(readComboCommitDOM(args),false);f.menu.show=false;assert.equal(readComboCommitDOM(args),true);
});
