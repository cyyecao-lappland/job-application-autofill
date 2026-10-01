import test from 'node:test';
import assert from 'node:assert/strict';
import {readModuleDOM} from '../browser/control_detection.mjs';
import {runInNewContext} from 'node:vm';

function read({open=false,editable=false,focused=false,committed=null,value='搜索中的文字',placeholder=null,dialog=null,uploadNames=null,uploadBusy=false,uploadDelete=true,udPlain=false,mokaRequired=false}={}){
  const label={textContent:'政治面貌'};
  const item={querySelector:()=>label};
  let field;
  const owner={contains:e=>e===field,querySelectorAll:()=>[],querySelector:()=>null};
  const uploadItem={querySelector:()=>uploadDelete?{}:null};
  const uploadOwner={querySelectorAll:s=>s==='input[type=file]'?[field]:
    (uploadNames||[]).map(textContent=>({textContent,getClientRects:()=>[{}],parentElement:null,closest:()=>uploadItem})),
    querySelector:()=>uploadBusy?{}:null};
  field={id:'field',type:uploadNames===null?'text':'file',tagName:'INPUT',value,readOnly:!editable,files:[],
    className:'el-input__inner',disabled:false,required:false,multiple:false,
    parentElement:{className:'el-input',closest:()=>null},
    getClientRects:()=>[{}],getAttribute:key=>key==='type'?(uploadNames===null?'text':'file'):key==='aria-valuetext'?committed:key==='placeholder'?placeholder:null,
    hasAttribute:()=>false,matches:()=>false,closest:selector=>selector==='[class*="apply-field-"]'?{querySelectorAll:()=>[],querySelector:s=>mokaRequired&&s===':scope > [class*="title-"] [class*="required-asterisk-"]'?{}:null}:selector==='.el-form-item'?item:
      selector==='.ud__select'&&udPlain?owner:
      selector==='[class*="sd-Upload-upload-wrap-"]'&&uploadNames!==null?uploadOwner:
      !udPlain&&(selector==='.el-select'||selector.includes('.el-select,'))?owner:null};
  const root={matches:()=>false,querySelectorAll:selector=>selector==='#field'||selector.startsWith('input,textarea,select,[role="combobox"]')?[field]:[],
    contains:()=>true};
  const menu={getClientRects:()=>open?[{}]:[]};
  globalThis.document={activeElement:focused?field:null,
    querySelectorAll:selector=>selector==='#root'?[root]:selector==='.el-select-dropdown'?[menu]:
      selector==='[role="dialog"],.el-dialog,.el-message-box'&&dialog?
      [{getClientRects:()=>[{}],contains:e=>dialog==='owner'&&e===root}]:[]};
  globalThis.location={href:'https://example.test/form'};
  globalThis.CSS={escape:s=>s};
  globalThis.getComputedStyle=()=>({visibility:'visible'});
  // Browser evaluate serializes the function without its module imports.
  return runInNewContext('(' + readModuleDOM.toString() + ')({moduleSelector: "#root"})', {
    document:globalThis.document, location:globalThis.location,
    CSS:globalThis.CSS, getComputedStyle:globalThis.getComputedStyle
  }).fields[0];
}

test('Moka required marker is read from its own field title',()=>{
 assert.equal(read({mokaRequired:true}).required,true);
 assert.equal(read({mokaRequired:false}).required,false);
});

test('closed readonly Element selection is readable',()=>{
  const f=read();assert.equal(f.value_readable,true);assert.equal(f.expanded,'false');
});
test('focused teleported menu search text cannot establish selected value',()=>{
  const f=read({open:true,focused:true});assert.equal(f.value_readable,false);assert.equal(f.expanded,'true');
});
test('another Element menu does not make this field unreadable',()=>{
  const f=read({open:true,focused:false});assert.equal(f.value_readable,true);assert.equal(f.expanded,'false');
});
test('focused editable combo text is not a selection',()=>{
  assert.equal(read({editable:true,focused:true}).value_readable,false);
});
test('explicit collapsed aria value provides committed evidence',()=>{
  const f=read({editable:true,focused:true,committed:'已选项'});
  assert.equal(f.value_readable,true);assert.equal(f.value,'已选项');
});
test('closed Element selection can expose its committed label through placeholder',()=>{
  const f=read({value:'',placeholder:'中共党员'});
  assert.equal(f.value_readable,true);assert.equal(f.value,'中共党员');assert.equal(f.value_present,true);
});
test('generic Element placeholder is not treated as a selected value',()=>{
  const f=read({value:'',placeholder:'请选择'});
  assert.equal(f.value,'');assert.equal(f.value_present,false);
});

function elementSelector(placeholder){
  const label={textContent:'政治面貌'};
  const item={querySelector:()=>label};
  let field,wrapper,root;
  const owner={contains:e=>e===field,querySelectorAll:()=>[]};
  root={matches:()=>false,children:[],querySelectorAll:selector=>{
    if(selector.startsWith('input,textarea,select,[role="combobox"]'))return [field];
    if(selector.startsWith(':scope > '))return [field];
    return [];
  },contains:()=>true};
  wrapper={tagName:'DIV',children:[],parentElement:root,closest:()=>null};root.children=[wrapper];
  field={id:'',type:'text',tagName:'INPUT',value:'',readOnly:true,className:'el-input__inner',
    disabled:false,required:false,multiple:false,parentElement:wrapper,getClientRects:()=>[{}],
    getAttribute:key=>key==='type'?'text':key==='placeholder'?placeholder:null,hasAttribute:()=>false,
    matches:()=>false,closest:selector=>selector==='.el-form-item'?item:
      selector==='.el-select'||selector.includes('.el-select,')?owner:null};
  wrapper.children=[field];
  const document={activeElement:null,querySelectorAll:selector=>selector==='#root'?[root]:[]};
  const result=runInNewContext('('+readModuleDOM.toString()+')({moduleSelector:"#root"})',{
    document,location:{href:'https://example.test/form'},CSS:{escape:s=>s},
    getComputedStyle:()=>({visibility:'visible'})});
  return result.fields[0].selector;
}

test('Element select identity does not depend on its mutable placeholder',()=>{
  const closed=elementSelector('请选择'),focused=elementSelector('中共党员');
  assert.equal(closed,focused);
  assert.match(closed,/^:scope >/);
  assert.doesNotMatch(closed,/placeholder/);
});

test('closed blurred role combobox with a displayed value is readable',()=>{
  const label={textContent:'学校名称'},input={value:'华东师范大学'};
  const item={querySelector:()=>label};
  let field;
  field={id:'school',type:'',tagName:'DIV',className:'el-autocomplete',disabled:false,required:false,multiple:false,
    parentElement:{className:'',closest:()=>null},getClientRects:()=>[{}],contains:e=>e===input,
    querySelector:selector=>selector==='input'?input:null,getAttribute:key=>key==='role'?'combobox':key==='aria-expanded'?'false':null,
    hasAttribute:()=>false,matches:()=>false,closest:selector=>selector==='.el-form-item'?item:null};
  const root={matches:()=>false,querySelectorAll:selector=>selector==='#school'||selector.startsWith('input,textarea,select,[role="combobox"]')?[field]:[],contains:()=>true};
  globalThis.document={activeElement:null,querySelectorAll:selector=>selector==='#root'?[root]:[]};
  globalThis.location={href:'https://example.test/form'};globalThis.CSS={escape:s=>s};globalThis.getComputedStyle=()=>({visibility:'visible'});
  const result=runInNewContext('('+readModuleDOM.toString()+')({moduleSelector:"#root"})',{
    document:globalThis.document,location:globalThis.location,CSS:globalThis.CSS,getComputedStyle:globalThis.getComputedStyle});
  assert.equal(result.fields[0].value_readable,true);assert.equal(result.fields[0].value,'华东师范大学');
});
test('an application editor dialog containing the module allows readback',()=>{
  assert.equal(read({dialog:'owner'}).value_readable,true);
});
test('a separate dialog blocks readback of the application module',()=>{
  assert.equal(read({dialog:'other'}).value_readable,false);
});

function readAriaVariant(tagName, phone=false){
  const number={};
  const item={querySelector:selector=>selector==='.ant-form-item-label > label'?{textContent:phone?'手机号码':'性别'}:
    selector==='input[placeholder*="手机"]'&&phone?number:null};
  const field={id:'choice',tagName,type:'',value:tagName==='INPUT'?'男':undefined,readOnly:false,
    textContent:'男',className:'ant-select-selection',disabled:false,required:false,multiple:false,
    parentElement:{className:'',closest:()=>null},getClientRects:()=>[{}],contains:()=>false,
    querySelector:()=>null,querySelectorAll:()=>[],hasAttribute:()=>false,matches:()=>false,
    getAttribute:key=>({'role':'combobox','aria-controls':'menu','aria-autocomplete':'list','aria-expanded':'false'})[key]||null,
    closest:selector=>selector==='.ant-form-item'?item:null};
  const root={matches:()=>false,contains:()=>true,querySelectorAll:selector=>selector==='#choice'||selector.startsWith('input,textarea,select,[role="combobox"]')?[field]:[]};
  return runInNewContext('('+readModuleDOM.toString()+')({moduleSelector:"#root"})',{
    document:{activeElement:null,querySelectorAll:selector=>selector==='#root'?[root]:[]},
    location:{href:'https://example.test/form'},CSS:{escape:s=>s},getComputedStyle:()=>({visibility:'visible'})
  }).fields[0];
}
test('Moka multiple attachment requires unique completed server list item',()=>{
  const f=read({uploadNames:['resume.pdf']});
  assert.equal(f.value,'resume.pdf');assert.equal(f.upload_ready,true);
  assert.equal(read({uploadNames:['resume.pdf'],uploadBusy:true}).upload_ready,false);
  assert.equal(read({uploadNames:['resume.pdf'],uploadDelete:false}).upload_ready,false);
  assert.equal(read({uploadNames:['resume.pdf','other.pdf']}).upload_ready,false);
});
test('Feishu plain input inside an optional Select wrapper is a text field',()=>{
 const f=read({udPlain:true,editable:true,value:'华东师范大学'});
 assert.equal(f.kind,'text');assert.equal(f.value,'华东师范大学');assert.equal(f.value_readable,true);
});

test('ARIA metadata on an Ant container does not imply an editable search input',()=>{
  assert.equal(readAriaVariant('DIV').component,null);
  assert.equal(readAriaVariant('INPUT').component,'aria-search-select');
});

test('Ant phone prefix selector is distinct from the phone number body',()=>{
  assert.equal(readAriaVariant('DIV',true).label,'手机 国家/地区代码');
  assert.equal(readAriaVariant('DIV').label,'性别');
});

function readIdentityFields(){
  let root;
  const makeInput=({type='text',placeholder='',value='',checked=false,ownLabel='',select=false,item})=>{
    let input,label,wrapper;
    label={textContent:ownLabel,tagName:'LABEL',children:[],parentElement:null};
    wrapper={tagName:'SPAN',children:[],parentElement:label,closest:()=>null};label.children=[wrapper];
    input={id:'',type,tagName:'INPUT',value,checked,readOnly:select,className:'el-input__inner',disabled:false,
      required:false,multiple:false,parentElement:wrapper,getClientRects:()=>[{}],
      getAttribute:key=>key==='type'?type:key==='placeholder'?placeholder:null,hasAttribute:()=>false,matches:()=>false,
      closest:selector=>selector==='label'?label:selector==='.el-form-item'?item:
        selector==='.el-select'||selector.includes('.el-select,')?(select?{contains:()=>false,querySelectorAll:()=>[]}:null):null};
    wrapper.children=[input];
    return input;
  };
  const checkboxItem={classList:{contains:x=>x==='is-required'},querySelector:s=>s===':scope > label'?{textContent:'应用开发语言'}:null,controls:[],
    querySelectorAll:s=>s==='input,textarea,select,[role="combobox"]'||s==='input[type="checkbox"]'?checkboxItem.controls:[]};
  checkboxItem.controls=[
    makeInput({type:'checkbox',value:'1',checked:false,ownLabel:'JAVA',item:checkboxItem}),
    makeInput({type:'checkbox',value:'2',checked:true,ownLabel:'C++',item:checkboxItem})
  ];
  const phoneItem={querySelector:s=>s===':scope > label'?{textContent:'手机号码'}:null,controls:[],
    querySelectorAll:s=>s==='input,textarea,select,[role="combobox"]'?phoneItem.controls:[]};
  phoneItem.controls=[
    makeInput({placeholder:'请选择',value:'86',select:true,item:phoneItem}),
    makeInput({placeholder:'请准确填写您的手机号码',value:'13800000000',item:phoneItem})
  ];
  const fields=[...checkboxItem.controls,...phoneItem.controls];
  const labels=fields.map(field=>field.parentElement.parentElement);
  root={matches:()=>false,tagName:'DIV',children:labels,contains:()=>true,querySelectorAll:selector=>{
    if(selector.startsWith('input,textarea,select,[role="combobox"]'))return fields;
    if(selector.startsWith(':scope > '))return [];
    return [];
  }};
  for(const label of labels)label.parentElement=root;
  for(const field of fields){
    const item=checkboxItem.controls.includes(field)?checkboxItem:phoneItem;
    const originalClosest=field.closest;
    field.closest=selector=>selector==='.el-form-item'?item:originalClosest(selector);
  }
  const document={activeElement:null,querySelectorAll:selector=>selector==='#root'?[root]:[]};
  return runInNewContext('('+readModuleDOM.toString()+')({moduleSelector:"#root"})',{
    document,location:{href:'https://example.test/form'},CSS:{escape:s=>s},getComputedStyle:()=>({visibility:'visible'})
  }).fields;
}

test('checkbox options have distinct semantic identities and unchecked is not present',()=>{
  const fields=readIdentityFields();
  assert.equal(fields[0].label,'应用开发语言 JAVA');
  assert.equal(fields[1].label,'应用开发语言 C++');
  assert.equal(fields[0].value,false);assert.equal(fields[0].value_present,false);
  assert.equal(fields[1].value,true);assert.equal(fields[1].value_present,true);
  assert.equal(fields[0].required,false);assert.equal(fields[1].required,false);
});

test('phone country code and number input have distinct semantic identities',()=>{
  const fields=readIdentityFields();
  assert.equal(fields[2].label,'手机号码 国家/地区代码');
  assert.equal(fields[3].label,'手机号码 号码正文');
});
