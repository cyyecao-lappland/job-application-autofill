import test from 'node:test';
import assert from 'node:assert/strict';
import {classifyDialog,classifyControl,exactLocationOption,exactAdministrativeOption,administrativeSearchQuery,parseISODate,runLocationAdapter,runTextAdapter,runElementDateNowAdapter,scopedControlTarget} from '../browser/control_adapters.mjs';
function fixture({ambiguous=false,partial=false,wrong=false,matched=false}={}){
  let opened=true,p=partial||matched?'湖南省':'省',c=matched?'衡阳市':'市';const calls=[];
  const evidence=()=>({root_count:1,fields:[{label:'籍贯',tag:'INPUT',type:'text',selector:'#field'}],dialogs:opened?[{title:'城市选择',selector:'#dialog',controls:[
    {tag:'SELECT',selector:'#p',class_name:'',options:[{label:'省',selected:p==='省'},{label:'湖南省',selected:p==='湖南省'}]},
    {tag:'SELECT',selector:'#c',class_name:'',options:[{label:'市',selected:c==='市'},...(p==='湖南省'?[{label:'衡阳市',selected:c==='衡阳市'}]:[])]},
    {tag:'BUTTON',selector:'#confirm',text:'确定',class_name:''},{tag:'BUTTON',selector:'#close',text:'',class_name:'el-dialog__headerbtn'}]}]:[]});
  const tab={playwright:{evaluate:async()=>{const e=evidence();if(ambiguous&&e.dialogs.length)e.dialogs.push(e.dialogs[0]);return e;},locator:s=>({
    locator:child=>tab.playwright.locator(child),
    click:async()=>{calls.push(s);if(s==='#field')opened=true;else opened=false;},
    selectOption:async({label})=>{calls.push(s);if(s==='#p')p=label;else c=label;},
    evaluate:async()=>wrong?'错误城市':(p==='湖南省'&&c==='衡阳市'?'湖南省衡阳市':'')})}};
  return {tab,calls,evidence};
}
const packet={module_selector:'#module',field_label:'籍贯',field_selector:'#field',location:{province:'湖南',city:'衡阳'},deadline:Date.now()/1000+120};
test('recognition requires title, two native selectors and exact confirm',()=>{
  const d=fixture().evidence().dialogs[0];assert.ok(classifyDialog(d));assert.equal(classifyDialog({...d,title:'删除确认'}),null);
  assert.throws(()=>exactLocationOption([{label:'湖南省'},{label:'湖南'}],'湖南'),/not_unique/);
});
test('administrative result requires one exact normalized full path',()=>{
  assert.equal(exactAdministrativeOption(['湖南-衡阳-雁峰区','湖南-衡阳-珠晖区'],'湖南省衡阳市雁峰区'),'湖南-衡阳-雁峰区');
  assert.throws(()=>exactAdministrativeOption(['湖南-衡阳-雁峰区','湖南省衡阳市雁峰区'],'湖南省衡阳市雁峰区'),/not_unique/);
  assert.equal(administrativeSearchQuery('湖南省衡阳市雁峰区'),'衡阳');
  assert.equal(administrativeSearchQuery('上海市普陀区'),'上海');
});
test('fixed adapter reopens empty dialog, selects in order, confirms and reads back',async()=>{
  const f=fixture(),r=await runLocationAdapter(f.tab,packet,async()=>{});
  assert.equal(r.committed,true);assert.deepEqual(f.calls,['#close','#field','#p','#c','#confirm']);
});
test('preexisting user selection and duplicate dialogs cause zero writes',async()=>{
  for(const opts of [{partial:true},{ambiguous:true}]){const f=fixture(opts);await assert.rejects(runLocationAdapter(f.tab,packet,async()=>{}));assert.deepEqual(f.calls,[]);}
});
test('an already committed matching region closes its unchanged dialog without confirming again',async()=>{
 const f=fixture({matched:true}),r=await runLocationAdapter(f.tab,{...packet,before_value:'湖南省衡阳市'},async()=>{});
 assert.equal(r.already_matched,true);assert.deepEqual(f.calls,['#close']);
});
test('wrong actual value never becomes a learned success',async()=>{
  const f=fixture({wrong:true});await assert.rejects(runLocationAdapter(f.tab,packet,async()=>{}),/readback_mismatch/);
});
test('unknown text never defaults to an executable text adapter',()=>{
  assert.equal(classifyControl({kind:'text'}),'agent_required');
  assert.equal(classifyControl({kind:'select'}),'native_select');
  assert.equal(classifyControl({kind:'text',component:'element-date-now'}),'element_date_now');
});

test('ISO date parser accepts real calendar dates and rejects invalid dates',()=>{
  assert.deepEqual(parseISODate('2027-07-01'),{year:2027,month:7,day:1});
  assert.throws(()=>parseISODate('2027-02-30'),/invalid_iso_date/);
});
test('date-now adapter exits current mode then reuses the verified date picker',async()=>{
  let open=false,current='至今',value='';const stages=[];
  const one=click=>({count:async()=>1,first(){return this;},click:async()=>click?.(),
    evaluate:async fn=>fn({value})});
  const dayCells={count:async()=>31,innerText:async()=>'',nth:index=>({
    innerText:async()=>String(index+1),click:async()=>{if(index!==15)throw Error('wrong_day');value='2026-09-16';current=value;open=false;}
  })};
  const panel={count:async()=>open?1:0,locator:selector=>{
    if(selector==='.el-date-picker__header-label')return one();
    if(selector==='.el-year-table td.available')return {count:async()=>1,filter:()=>one()};
    if(selector==='.el-month-table td')return {filter:()=>one()};
    if(selector.startsWith('.el-date-table'))return dayCells;
    throw Error('unexpected_panel_selector:'+selector);
  },getByRole:()=>one()};
  const currentInput={count:async()=>1,evaluate:async()=>current};
  const mask={count:async()=>1,click:async()=>{open=true;}};
  const wrapper={count:async()=>1,locator:selector=>selector.includes('__ipt')?currentInput:selector.includes('__mask')?mask:null};
  const target={count:async()=>1,evaluate:async()=>value,locator:()=>wrapper,click:async()=>{open=true;}};
  const module={locator:()=>target};
  const tab={playwright:{locator:selector=>selector==='#module'?module:selector.startsWith('.el-picker-panel')?panel:null}};
  const result=await runElementDateNowAdapter(tab,{module_selector:'#module',field_selector:'#end',
    before_value:'至今',location:'2026-09-16',deadline:Date.now()/1000+30},
    async detail=>stages.push(detail.stage));
  assert.equal(result.committed,true);assert.equal(result.adapter,'element_date_now_picker_v1');
  assert.equal(value,'2026-09-16');assert.ok(stages.includes('current_date_picker_issued'));
  assert.ok(stages.includes('verified'));
});
test('module-relative control selectors are resolved below the module root',()=>{
  const calls=[],field={kind:'field'};
  const module={locator:selector=>{calls.push(['field',selector]);return field;}};
  const tab={playwright:{locator:selector=>{calls.push(['module',selector]);return module;}}};
  const result=scopedControlTarget(tab,{module_selector:'#record-2',field_selector:':scope > form input'});
  assert.equal(result,field);
  assert.deepEqual(calls,[['module','#record-2'],['field',':scope > form input']]);
});
test('text probe discovers a dialog before transmitting a value',async()=>{
  let opened=false;const actions=[];
  const target={evaluate:async()=>'',click:async()=>{opened=true;actions.push('click');},fill:async()=>actions.push('fill')};
  target.locator=()=>target;
  const tab={playwright:{evaluate:async()=>({root_count:1,fields:[{label:'学校',selector:'#f'}],dialogs:opened?[{}]:[],menus:[]}),
    locator:()=>target}};
  await assert.rejects(runTextAdapter(tab,{field_label:'学校',field_selector:'#f',before_value:'',location:'A',deadline:Date.now()/1000+60},async()=>{}),/composite/);
  assert.deepEqual(actions,['click']);
});
test('text probe waits for a prior dropdown leave transition',async()=>{
  let reads=0,value='';const actions=[];
  const target={count:async()=>1,evaluate:async fn=>fn({value,blur(){}}),click:async()=>actions.push('click'),
    fill:async v=>{value=v;actions.push('fill');}};target.locator=()=>target;
  const tab={playwright:{evaluate:async()=>({root_count:1,fields:[{label:'研究方向',selector:'#f',readonly:false,disabled:false}],
      dialogs:[],menus:reads++<2?[{selector:'#old-menu'}]:[]}),
    locator:()=>target}};
  const result=await runTextAdapter(tab,{module_selector:'#module',field_label:'研究方向',field_selector:'#f',
    before_value:'',location:'检索增强生成',allow_logical_selector:true,deadline:Date.now()/1000+60},async()=>{});
  assert.equal(result.committed,true);assert.equal(value,'检索增强生成');assert.deepEqual(actions,['click','fill']);
});
test('user edits after discovery are not overwritten',async()=>{
  const f=fixture();await assert.rejects(runLocationAdapter(f.tab,{...packet,before_value:'old'},async()=>{}),/user_value_changed/);
  assert.deepEqual(f.calls,[]);
});

test('text execution binds the fresh DOM target without requiring a second business label parser',async()=>{
  let value='';const target={count:async()=>1,evaluate:async fn=>fn({value,blur(){}}),click:async()=>{},fill:async v=>{value=v;}};
  target.locator=()=>target;
  const tab={playwright:{locator:()=>target,evaluate:async(fn,args)=>{
    assert.equal(args.fieldSelector,'#name');
    return {root_count:1,target_count:1,menus:[],dialogs:[],fields:[{label:'',matches_target:true,readonly:false,disabled:false}]};
  }}};
  const result=await runTextAdapter(tab,{module_selector:'#module',field_selector:'#name',field_label:'姓名',
    allow_logical_selector:true,before_value:'',location:'Synthetic',deadline:Date.now()/1000+10},async()=>{});
  assert.equal(result.committed,true);assert.equal(value,'Synthetic');
});
