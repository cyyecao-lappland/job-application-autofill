import test from 'node:test';
import assert from 'node:assert/strict';
import {mkdtemp,writeFile,readFile,mkdir} from 'node:fs/promises';
import {tmpdir} from 'node:os';
import {join} from 'node:path';
import {randomUUID} from 'node:crypto';
import {runRequest,observe,sanitizedError,instrumentTab} from '../browser/edge_executor.mjs';
import {FAILURE_TEXT,FIRST_OPTION,firstEnabledOption} from '../browser/fallback_policy.mjs';
import {registryReport} from '../browser/controls/service.mjs';

test('incomplete readback finishes once as skipped and journal replay never refills',async()=>{
  const host=fake();
  host.f.control_status='unverified_text_candidate';
  const original=host.tab.playwright.locator;
  host.tab.playwright.locator=selector=>{
    const target=original(selector);
    return {...target,locator:child=>host.tab.playwright.locator(child),fill:async(...args)=>{
      await target.fill(...args);host.f.value='***';host.f.value_readable=false;
    }};
  };
  const {dir}=await setup(host);
  await runRequest(host.edge,host.tab,dir);
  const receipt=JSON.parse(await readFile(join(dir,'receipt.json'),'utf8'));
  assert.equal(receipt.settled,true);
  assert.equal(receipt.results[0].status,'verification_skipped');
  assert.equal(receipt.evidence.controlRegistry.registryVersion,registryReport().registryVersion);
  await runRequest(host.edge,host.tab,dir);
  assert.equal(host.actions.filter(a=>a[0]==='fill').length,1);
});

test('per-request registry mismatch is rejected before any browser mutation',async()=>{
  const host=fake();
  const {dir}=await setup(host);
  const path=join(dir,'request.json'),request=JSON.parse(await readFile(path,'utf8'));
  request.controlRegistry=registryReport();request.controlRegistry.controls[0].adapterVersion++;
  await writeFile(path,JSON.stringify(request));
  await assert.rejects(runRequest(host.edge,host.tab,dir),/CONTROL_REGISTRY_MISMATCH/);
  assert.deepEqual(host.actions,[]);
});

function fake({kind='text',multiple=false,value='',fail=false,card=false,cardText='目标'}={}){
  const f={id:'#answer',selector:'#answer',label:'答案',kind,value,signature:{tag:'INPUT',label:'答案'},
    disabled:false,required:true,multiple,expanded:'false',options:[{label:'目标',disabled:false}]};
  const actions=[];
  let menuOpen=false,search='',signal=null;
  const save={selector:'#save',label:'保存',enabled:true,signal:{selector:'#saved',text:'保存成功'}};
  const api={
    async evaluate(fn){
      if(fn.name==='readScopeBindings')return [{root_id:'#answer',module_id:'m',field_id:'#answer'}];
      if(fn.name==='readSaveUI')return {dialog_count:0,validation_error_count:0,loading:false};
      if(fn.name==='readModuleDOM')return {url:'https://example.test/form',fields:[structuredClone(f)],save};
      if(fn.name==='readPopupDOM')return menuOpen?{selector:'#menu',search:{location:'field'},busy:false,
        options:search==='目标'?[{label:'目标',disabled:false}]:[]}: {error:'popup_not_unique_or_not_loaded'};
      if(fn.name==='readControlEvidence')return {root_count:1,dialogs:[],menus:[],fields:[{
        label:f.label,selector:f.selector,readonly:false,disabled:f.disabled
      }]};
      throw Error('unexpected_evaluate');
    },
    locator(selector){return loc(selector);}
  };
  function loc(selector){return {
    locator: child=>loc(child),getByRole:(role,{name})=>loc('option:'+name),
    count:async()=>selector==='#saved'&&signal===null?0:1,
    isVisible:async()=>!(selector==='form'&&signal),isEnabled:async()=>true,waitFor:async()=>{},
    evaluate:async fn=>fn.toString().includes('blur')?undefined:f.value,
    innerText:async()=>card&&selector==='#module'?cardText:selector==='#saved'?signal:'保存',
    async fill(value,options){assert.ok(options.timeoutMs>0);actions.push(['fill',value]);if(fail)throw Error('transport_failure');if(kind==='combobox')search=value;else f.value=value;},
    async setInputFiles(value,options){assert.ok(options.timeoutMs>0);actions.push(['upload',value]);if(fail)throw Error('transport_failure');f.value=value.split(/[\\/]/).pop();f.value_present=true;f.upload_ready=true;},
    async setChecked(value,options){assert.ok(options.timeoutMs>0);actions.push(['check',value]);f.value=value;},
    async press(value,options){assert.ok(options.timeoutMs>0);actions.push(['press',value]);},
    async check(options){assert.ok(options.timeoutMs>0);actions.push(['radio']);f.value=true;},
    async selectOption(values,options){assert.ok(options.timeoutMs>0);actions.push(['select',values]);f.value=multiple?values.map(v=>v.label):values[0].label;},
    async click(options){assert.ok(options.timeoutMs>0);actions.push(['click',selector]);
      if(selector==='#save'){signal='保存成功';return;}
      if(selector.startsWith('option:')){f.value=selector.slice(7);f.expanded='false';menuOpen=false;}
      else{menuOpen=true;f.expanded='true';}
    }
  };}
  const edge={browserId:'edge-1'},tab={id:'tab-1',url:async()=>'https://example.test/form',playwright:api};
  return {edge,tab,f,actions,save};
}

async function setup(host,modify=()=>{}){
  const dir=await mkdtemp(join(tmpdir(),'edge-executor-test-'));
  const snapshot=await observe(host.edge,host.tab,{moduleId:'m',moduleSelector:'#module'});
  const packet={version:1,command_id:randomUUID(),kind:'fill',browser:'edge',target:snapshot.target,
    module_id:'m',module_selector:'#module',snapshot_id:snapshot.snapshot_id,revision:0,
    operations:[{id:'#answer',field:snapshot.fields[0],value:'目标',depends_on:[]}],
    completed_ids:[],capture:{},deadline:Date.now()/1000+30,max_batch_ms:8000};
  modify(packet);
  const normalized=v=>JSON.stringify(Array.isArray(v)?[...v].sort():v);
  if(packet.kind==='fill')packet.min_fill_count=Math.min(10,packet.operations.filter(op=>normalized(op.field.value)!==normalized(op.value)).length);
  const policy={target:snapshot.target,module_id:'m',module_selector:'#module',fill:true,save:true};
  await writeFile(join(dir,'policy.json'),JSON.stringify(policy));
  await writeFile(join(dir,'request.json'),JSON.stringify({phase:packet.kind==='save'?'awaiting_save':'awaiting_edge',command:packet}));
  return {dir,packet};
}

test('text uses supported timeoutMs and replay returns receipt without action',async()=>{
  const h=fake(),{dir}=await setup(h);
  const first=await runRequest(h.edge,h.tab,dir);
  assert.equal(first.status,'completed');assert.equal(h.f.value,'目标');assert.equal(h.actions.length,1);
  await runRequest(h.edge,h.tab,dir);assert.equal(h.actions.length,1);
});

test('changed education record boundary stops before the first stale write',async()=>{
  const h=fake();let reads=0;const original=h.tab.playwright.evaluate;
  h.tab.playwright.evaluate=async(fn,args)=>{
    const result=await original(fn,args);
    if(fn.name==='readModuleDOM'){
      reads++;
      result.record_boundary={container_selector:'#education-list',record_selector:':scope > .record',
        record_count:3,record_index:0,scope_record_count:1};
    }
    return result;
  };
  const expected={container_selector:'#education-list',record_selector:':scope > .record',
    record_count:2,record_index:0,scope_record_count:1};
  const {dir}=await setup(h,p=>p.record_boundary=expected);
  await runRequest(h.edge,h.tab,dir);
  const receipt=JSON.parse(await readFile(join(dir,'receipt.json'),'utf8'));
  assert.ok(reads>=1);
  assert.equal(receipt.results[0].status,'conflict');
  assert.equal(receipt.results[0].reason,'record_boundary_changed');
  assert.deepEqual(h.actions,[]);
});

test('recovery resolves a rebound DOM id by reviewed selector and signature',async()=>{
  const h=fake(),{dir}=await setup(h);
  h.f.id='#fresh-structural-id';
  const result=await runRequest(h.edge,h.tab,dir);
  assert.equal(result.status,'completed');
  assert.equal(h.f.value,'目标');
  assert.deepEqual(h.actions,[['fill','目标']]);
});

test('native select does not expand or search',async()=>{
  const h=fake({kind:'select'}),{dir}=await setup(h);
  await runRequest(h.edge,h.tab,dir);assert.deepEqual(h.actions.map(a=>a[0]),['select']);
});

test('dependent level update stops the batch before its stale write',async()=>{
  const h=fake({kind:'select'});
  const child={...structuredClone(h.f),id:'#level',selector:'#level',label:'level',value:''};
  const originalEvaluate=h.tab.playwright.evaluate;
  h.tab.playwright.evaluate=async(fn,args)=>{
    const value=await originalEvaluate(fn,args);
    if(fn.name==='readModuleDOM'){
      if(h.f.value==='目标'){child.value='CCF-C';child.disabled=true;}
      value.fields.push(structuredClone(child));
    }
    return value;
  };
  const {dir}=await setup(h,p=>p.operations.push({id:child.id,field:structuredClone(child),value:'A',depends_on:[]}));
  await runRequest(h.edge,h.tab,dir);
  const r=JSON.parse(await readFile(join(dir,'receipt.json'),'utf8'));
  assert.deepEqual(r.evidence.linkage.changed,['#level']);
  assert.equal(r.results[1].reason,'linkage_barrier');
  assert.equal(r.results[1].status,'unattempted');
  assert.deepEqual(h.actions.map(a=>a[0]),['select']);
});

test('fallback first selected tag is retained instead of toggled off',async()=>{
  const h=fake({kind:'combobox',value:'目标'});
  h.f.selection_mode='tag';h.f.component='next-select';
  const original=h.tab.playwright.evaluate;
  h.tab.playwright.evaluate=async(fn,args)=>fn.name==='readTagSelectionDOM'?
    {tags:[{label:'目标',remove:'#remove'}],expanded:'true'}:
    fn.name==='readPopupDOM'?{selector:'#menu',options:[{label:'目标',disabled:false}]}:original(fn,args);
  const {dir}=await setup(h,p=>{p.agent_tuning=false;Object.assign(p.operations[0],{fallback:'first_option',value:FIRST_OPTION,source:null,transform:'draft_fallback'});});
  // Fake Tab/Escape close the menu as the real browser does.
  const loc=h.tab.playwright.locator;
  h.tab.playwright.locator=s=>{const l=loc(s);l.locator=h.tab.playwright.locator;
    l.press=async()=>{h.f.expanded='false';};return l;};
  const r=await runRequest(h.edge,h.tab,dir);
  assert.equal(r.status,'completed');assert.equal(h.f.value,'目标');
  assert.ok(!h.actions.some(a=>a[1]==='option:目标'));
});

test('tag select removes an obsolete chip, selects exact replacement and closes the persistent menu',async()=>{
  const h=fake({kind:'combobox',value:'旧专业'});
  Object.assign(h.f,{component:'next-select',selection_mode:'tag'});
  let tags=['旧专业'];const removed=[];
  const evaluate=h.tab.playwright.evaluate;
  h.tab.playwright.evaluate=async(fn,args)=>fn.name==='readTagSelectionDOM'?
    {expanded:'false',tags:tags.map(label=>({label,remove:'#remove'}))}:evaluate(fn,args);
  const locator=h.tab.playwright.locator;
  h.tab.playwright.locator=selector=>{
    const l=locator(selector);
    if(selector==='#remove')l.click=async()=>{removed.push(tags[0]);tags=[];h.f.value='';};
    return l;
  };
  const {dir}=await setup(h);
  const result=await runRequest(h.edge,h.tab,dir);
  assert.equal(result.status,'completed');assert.equal(h.f.value,'目标');
  assert.deepEqual(removed,['旧专业']);
  assert.ok(h.actions.some(a=>a[0]==='press'&&a[1]==='Escape'));
});

function fallbackPacket(packet, mode){
  packet.agent_tuning=false;
  Object.assign(packet.operations[0],{source:null,transform:'draft_fallback',fallback:mode,
    value:mode==='text_marker'?FAILURE_TEXT:FIRST_OPTION});
}

test('disabled tuning writes the exact manual marker and replay does not repeat',async()=>{
  const h=fake(),{dir}=await setup(h,p=>fallbackPacket(p,'text_marker'));
  const receipt=await runRequest(h.edge,h.tab,dir);
  assert.equal(receipt.status,'completed');assert.equal(h.f.value,FAILURE_TEXT);
  await runRequest(h.edge,h.tab,dir);assert.equal(h.actions.length,1);
});

test('fallback native dropdown skips placeholder and disabled options in DOM order',async()=>{
  const h=fake({kind:'select'});
  h.f.options=[{label:'请选择',value:''},{label:'不可选',disabled:true},
    {label:'第一个可用项',value:'one'},{label:'第二项',value:'two'}];
  const {dir}=await setup(h,p=>fallbackPacket(p,'first_option'));
  const receipt=await runRequest(h.edge,h.tab,dir);
  assert.equal(h.f.value,'第一个可用项');
  assert.deepEqual(h.actions.map(a=>a[0]),['select']);
  const full=JSON.parse(await readFile(join(dir,'receipt.json'),'utf8'));
  assert.equal(full.evidence.fallback_values['#answer'],'第一个可用项');
});

test('fallback ARIA dropdown chooses a real option without searching with sentinel',async()=>{
  const h=fake({kind:'combobox'}),evaluate=h.tab.playwright.evaluate;
  h.tab.playwright.evaluate=async(fn,...args)=>{
    if(fn.name==='readPopupDOM')return {selector:'#menu',busy:false,search:{location:'field'},
      options:[{label:'请选择'},{label:'目标',disabled:false},{label:'其它',disabled:false}]};
    return evaluate(fn,...args);
  };
  const {dir}=await setup(h,p=>fallbackPacket(p,'first_option'));
  const receipt=await runRequest(h.edge,h.tab,dir);
  assert.equal(receipt.status,'completed');assert.equal(h.f.value,'目标');
  const full=JSON.parse(await readFile(join(dir,'receipt.json'),'utf8'));
  assert.equal(full.evidence.fallback_values['#answer'],'目标');
  assert.ok(!h.actions.some(a=>a[0]==='fill'));
});

test('no available fallback option is deferred without writing a fabricated value',async()=>{
  assert.equal(firstEnabledOption([{label:'请选择'},{label:'禁用',disabled:true}]),undefined);
  const h=fake({kind:'select'});h.f.options=[{label:'请选择',value:''}];
  const {dir}=await setup(h,p=>fallbackPacket(p,'first_option'));
  await runRequest(h.edge,h.tab,dir);
  const full=JSON.parse(await readFile(join(dir,'receipt.json'),'utf8'));
  assert.equal(full.results[0].reason,'fallback_no_available_option');
  assert.deepEqual(h.actions,[]);assert.deepEqual(full.evidence.fallback_values,{});
});

test('fallback is rejected before any browser write unless tuning explicitly off',async()=>{
  const h=fake(),{dir}=await setup(h,p=>{fallbackPacket(p,'text_marker');p.agent_tuning=true;});
  await assert.rejects(runRequest(h.edge,h.tab,dir));assert.deepEqual(h.actions,[]);
});

test('editable date uses Enter then same-field blur and actual value readback',async()=>{
  const h=fake();h.f.component='element-date';
  const {dir}=await setup(h,p=>p.operations[0].value='2000-01-02');
  const result=await runRequest(h.edge,h.tab,dir);
  assert.equal(result.status,'completed');
  assert.deepEqual(h.actions,[['fill','2000-01-02'],['press','Enter']]);
});

test('native file input uploads once and verifies rendered presence',async()=>{
  const h=fake({kind:'file'});h.f.component='native-file';h.f.control_status='recognized';h.f.value_present=false;h.f.upload_ready=false;
  const file=join(await mkdtemp(join(tmpdir(),'edge-file-test-')),'life-photo.jpg');await writeFile(file,'photo');
  const {dir}=await setup(h,p=>p.operations[0].value=file);
  const result=await runRequest(h.edge,h.tab,dir);
  assert.equal(result.status,'completed');assert.equal(h.f.value_present,true);
  assert.deepEqual(h.actions,[['upload',file]]);
});

test('missing upload file is deferred before a browser action',async()=>{
  const h=fake({kind:'file'});h.f.component='native-file';h.f.control_status='recognized';h.f.value_present=false;h.f.upload_ready=false;
  const {dir,packet}=await setup(h,p=>p.operations[0].value=join(tmpdir(),'definitely-missing-photo.jpg'));
  const result=await runRequest(h.edge,h.tab,dir);
  assert.equal(result.status,'partial');assert.deepEqual(h.actions,[]);
  assert.equal(result.counts.deferred,1);
  const journal=JSON.parse(await readFile(join(dir,'writer',packet.command_id+'.json'),'utf8'));
  assert.equal(journal.results[0].reason,'file_path_missing');
});

test('local exception before any action is not an unsettled browser write',async()=>{
  const h=fake({kind:'combobox'});h.f.component='element-select';
  const evaluate=h.tab.playwright.evaluate;
  h.tab.playwright.evaluate=async(fn,args)=>fn.name==='readPopupDOM'?{visible:null,menus:[]}:evaluate(fn,args);
  const {dir}=await setup(h);
  const result=await runRequest(h.edge,h.tab,dir);
  assert.equal(result.settled,true);assert.equal(result.counts.deferred,1);
  assert.equal(h.actions.length,0);
  const receipt=JSON.parse(await readFile(join(dir,'receipt.json'),'utf8'));
  assert.equal(receipt.results[0].reason,'local_executor_error');
});

test('matching search text in an open combo is not already_matched',async()=>{
  const h=fake({kind:'combobox',value:'目标'});h.f.expanded='true';h.f.value_readable=false;
  const {dir}=await setup(h);
  const r=await runRequest(h.edge,h.tab,dir);
  assert.equal(r.counts.already_matched,undefined);assert.equal(r.counts.deferred,1);
  assert.equal(h.actions.length,0);
});
test('native select mismatch is deferred without a write or polling',async()=>{
  const h=fake({kind:'select'}),{dir}=await setup(h,p=>p.operations[0].value='原始名称');
  const result=await runRequest(h.edge,h.tab,dir);
  assert.equal(result.counts.deferred,1);assert.deepEqual(h.actions,[]);
  const receipt=JSON.parse(await readFile(join(dir,'receipt.json'),'utf8'));
  assert.deepEqual(receipt.evidence.enum_candidates,[{field_id:'#answer',source_value:'原始名称',options:['目标']}]);
});

for(const failClose of [false,true])test('nonexact owned menu closes before enum evidence '+failClose,async()=>{
  const h=fake({kind:'combobox'});h.f.component='element-select';
  let opened=false;
  const evaluate=h.tab.playwright.evaluate;
  h.tab.playwright.evaluate=async(fn,args)=>{
    if(fn.name==='readPopupDOM')return args.captureBaseline?{visible:[],menus:[]}:
      {selector:'#menu',ownership:'local',search:{location:'field'},options:[{label:'汉族',disabled:false}],busy:false};
    return evaluate(fn,args);
  };
  const locator=h.tab.playwright.locator;
  h.tab.playwright.locator=s=>{
    const l=locator(s);l.locator=child=>h.tab.playwright.locator(child);
    l.click=async()=>{opened=!opened;h.actions.push(['toggle',opened]);};
    l.waitFor=async()=>{if(failClose)throw Error('transport_failure');assert.equal(opened,false);};
    return l;
  };
  const {dir,packet}=await setup(h,p=>p.operations[0].value='汉');
  const result=await runRequest(h.edge,h.tab,dir);
  const receipt=JSON.parse(await readFile(join(dir,'receipt.json'),'utf8'));
  assert.equal(result.status,failClose?'unknown':'partial');
  assert.equal(result.settled,!failClose);
  assert.deepEqual(receipt.evidence.enum_candidates,failClose?[]:[{field_id:'#answer',source_value:'汉',options:['汉族']}]);
  assert.equal(h.f.value,'');
  const j=JSON.parse(await readFile(join(dir,'writer',packet.command_id+'.json'),'utf8'));
  const close=j.instrumentation.filter(e=>e.stage==='popup_close');
  assert.ok(close.length>=2);
  assert.equal(close.at(-1).status,failClose?'failed':'returned');
  assert.ok(close.every(e=>e.duration_ms>=0));
});
test('orphan popup can be reclaimed by target click before enum deferral',async()=>{
  const h=fake({kind:'combobox'});h.f.component='element-select';
  let focused=false,opened=true;
  const evaluate=h.tab.playwright.evaluate;
  h.tab.playwright.evaluate=async(fn,args)=>{
    if(fn.name==='readPopupDOM')return args.captureBaseline?{visible:opened?['#menu']:[],menus:[]}:
      !focused?{error:'popup_focus_changed'}: {selector:'#menu',ownership:'focused_existing_matching_menu',search:null,options:[{label:'汉族',disabled:false}],busy:false};
    return evaluate(fn,args);
  };
  const locator=h.tab.playwright.locator;
  h.tab.playwright.locator=s=>{
    const l=locator(s);l.locator=child=>h.tab.playwright.locator(child);
    l.click=async()=>{if(!focused)focused=true;else opened=false;h.actions.push(['click',s]);};
    l.waitFor=async()=>assert.equal(opened,false);return l;
  };
  const {dir}=await setup(h,p=>p.operations[0].value='汉');
  const r=await runRequest(h.edge,h.tab,dir);
  assert.equal(r.counts.deferred,1);assert.equal(r.settled,true);assert.equal(h.actions.length,2);
  assert.equal(h.f.value,'');
});

test('native multiselect compares selected sets rather than order',async()=>{
  const h=fake({kind:'select',multiple:true,value:['乙','甲']}),{dir}=await setup(h,p=>p.operations[0].value=['甲','乙']);
  const result=await runRequest(h.edge,h.tab,dir);assert.equal(result.counts.already_matched,1);assert.equal(h.actions.length,0);
});

test('search dropdown opens, searches and selects in one executor call',async()=>{
  const h=fake({kind:'combobox'}),{dir,packet}=await setup(h);
  const result=await runRequest(h.edge,h.tab,dir);
  assert.equal(result.status,'completed');assert.deepEqual(h.actions,[['click','#answer'],['fill','目标'],['click','option:目标']]);
  const journal=JSON.parse(await readFile(join(dir,'writer',packet.command_id+'.json'),'utf8'));
  const stages=journal.instrumentation.map(e=>e.stage);
  for(const stage of ['module_readback','popup_open','popup_search','popup_select'])assert.ok(stages.includes(stage),stage);
  assert.ok(journal.instrumentation.every(e=>e.duration_ms>=0&&e.status==='returned'));
  assert.ok(!JSON.stringify(journal.instrumentation).includes('目标'));
});

test('search-select can type before a popup is mounted then select its current result',async()=>{
  const h=fake({kind:'combobox'}),original=h.tab.playwright.evaluate;
  h.f.search_evidence={editable:true,selection_structure:true};
  h.tab.playwright.evaluate=async(fn,...args)=>{
    if(fn.name==='readPopupDOM'&&!h.actions.some(a=>a[0]==='fill'))return {error:'popup_not_unique_or_not_loaded'};
    return original(fn,...args);
  };
  const {dir}=await setup(h),r=await runRequest(h.edge,h.tab,dir);
  assert.equal(r.status,'completed');
  assert.deepEqual(h.actions,[['click','#answer'],['fill','目标'],['click','option:目标']]);
});

test('unmapped search-select fallback reports missing query without typing its sentinel',async()=>{
  const h=fake({kind:'combobox'}),original=h.tab.playwright.evaluate;
  h.f.search_evidence={editable:true,selection_structure:true};
  h.tab.playwright.evaluate=async(fn,...args)=>fn.name==='readPopupDOM'?{error:'popup_not_unique_or_not_loaded'}:original(fn,...args);
  const {dir}=await setup(h,p=>fallbackPacket(p,'first_option'));
  await runRequest(h.edge,h.tab,dir);
  const r=JSON.parse(await readFile(join(dir,'receipt.json'),'utf8'));
  assert.equal(r.results[0].reason,'search_query_required');
  assert.ok(!h.actions.some(a=>a[0]==='fill'));
});

test('instrumentation never echoes private errors or changes settlement',async()=>{
  const secret='SyntheticName secret-id-123456789012345678';
  for(const settled of [undefined,true,false]){
    const error=Object.assign(Error('Timeout '+secret),settled===undefined?{}:{settled});
    const journal={},snapshots=[];
    const tab=instrumentTab({playwright:{locator:()=>({fill:async()=>{throw error;}})}},journal,
      async j=>snapshots.push(structuredClone(j)));
    await assert.rejects(tab.playwright.locator('private selector').fill(secret),e=>e===error);
    assert.equal(error.settled,settled);
    assert.equal(snapshots[0].instrumentation[0].status,'pending');
    assert.equal(journal.instrumentation[0].status,'failed');
    assert.ok(journal.instrumentation[0].duration_ms>=0);
    assert.equal(journal.instrumentation[0].error.code,'timeout');
    assert.ok(!JSON.stringify(journal).includes(secret));
    assert.ok(!JSON.stringify(journal).includes('private selector'));
  }
  assert.ok(sanitizedError(Error('x'.repeat(10000))).message.length<=500);
  assert.equal(sanitizedError(Error('Timeout waiting for popup; id 123456789012')).message,
    'Timeout waiting for popup; id [redacted-number]');
});

test('already matching values are skipped',async()=>{
  const h=fake({value:'目标'}),{dir}=await setup(h);
  const result=await runRequest(h.edge,h.tab,dir);assert.equal(result.counts.already_matched,1);assert.equal(h.actions.length,0);
});

test('startup comparison fills blank and incorrect values but skips matches',async()=>{
  const h=fake();
  const fields=[
    {...h.f,id:'#blank',selector:'#blank',value:''},
    {...h.f,id:'#incorrect',selector:'#incorrect',value:'旧值'},
    {...h.f,id:'#matched',selector:'#matched',value:'目标'},
  ];
  h.tab.playwright.evaluate=async()=>({url:'https://example.test/form',fields:structuredClone(fields),save:h.save});
  h.tab.playwright.locator=selector=>({
    locator:child=>h.tab.playwright.locator(child),
    async fill(value){fields.find(field=>field.selector===selector).value=value;h.actions.push(['fill',selector,value]);}
  });
  const {dir}=await setup(h,packet=>{
    packet.operations=fields.map(field=>({id:field.id,field:structuredClone(field),value:'目标',depends_on:[]}));
  });
  const result=await runRequest(h.edge,h.tab,dir);
  assert.equal(result.status,'completed');
  assert.equal(result.counts.written,2);
  assert.equal(result.counts.already_matched,1);
  assert.deepEqual(fields.map(field=>field.value),['目标','目标','目标']);
  assert.deepEqual(h.actions,[['fill','#blank','目标'],['fill','#incorrect','目标']]);
});

test('restart skips an already matching unverified text control before handler routing',async()=>{
  const h=fake({value:'目标'});h.f.control_status='unverified_text_candidate';
  const {dir}=await setup(h);
  const result=await runRequest(h.edge,h.tab,dir);
  assert.equal(result.counts.already_matched,1);assert.equal(h.actions.length,0);
});

test('plain text candidate is probed, filled and verified programmatically',async()=>{
  const h=fake();h.f.control_status='unverified_text_candidate';
  const {dir}=await setup(h);
  const result=await runRequest(h.edge,h.tab,dir);
  assert.equal(result.status,'completed');assert.equal(h.f.value,'目标');
  assert.deepEqual(h.actions,[['click','#answer'],['fill','目标']]);
});

test('user edits are not overwritten',async()=>{
  const h=fake(),{dir}=await setup(h);h.f.value='用户改了';
  const result=await runRequest(h.edge,h.tab,dir);assert.equal(result.counts.conflict,1);assert.equal(h.actions.length,0);
});

test('wrong Edge instance is rejected before a write',async()=>{
  const h=fake(),{dir}=await setup(h);h.edge.browserId='chrome-1';
  await assert.rejects(runRequest(h.edge,h.tab,dir),/page_identity_changed/);assert.equal(h.actions.length,0);
});

test('submit cannot be expressed as an executor operation',async()=>{
  const h=fake(),{dir}=await setup(h,p=>p.kind='submit');
  await assert.rejects(runRequest(h.edge,h.tab,dir),/no_such_action/);assert.equal(h.actions.length,0);
});

test('unknown browser failure is durable and is not replayed',async()=>{
  const h=fake({fail:true}),{dir}=await setup(h);
  const result=await runRequest(h.edge,h.tab,dir);assert.equal(result.status,'unknown');assert.equal(result.settled,false);
  await runRequest(h.edge,h.tab,dir);assert.equal(h.actions.length,1);
});

test('explicitly ended fill failure allows graph read-only reconciliation',async()=>{
  const h=fake(),{dir}=await setup(h);
  const target={locator:()=>target,fill:async()=>{throw Object.assign(Error('ended failure'),{settled:true});}};
  h.tab.playwright.locator=()=>target;
  const result=await runRequest(h.edge,h.tab,dir);
  assert.equal(result.status,'unknown');assert.equal(result.settled,true);
});

test('a crashed pending command cannot execute twice',async()=>{
  const h=fake(),{dir,packet}=await setup(h);
  await mkdir(join(dir,'writer'));await writeFile(join(dir,'writer',packet.command_id+'.json'),JSON.stringify({status:'pending'}));
  await assert.rejects(runRequest(h.edge,h.tab,dir),/requires_reconciliation/);assert.equal(h.actions.length,0);
});

test('save requires a current reviewed revision',async()=>{
  const h=fake({value:'目标'}),{dir}=await setup(h,p=>{
    p.kind='save';p.reviewed_revision=99;p.save=h.save;p.expected_fields=[{id:'#answer',value:'目标'}];
  });
  await assert.rejects(runRequest(h.edge,h.tab,dir),/save_not_authorized/);assert.equal(h.actions.length,0);
});

test('save checks reviewed values again and reads fresh evidence',async()=>{
  const h=fake({value:'目标'}),{dir}=await setup(h,p=>{
    p.kind='save';p.reviewed_revision=0;p.save=h.save;p.expected_fields=[{id:'#answer',value:'目标'}];
  });
  const result=await runRequest(h.edge,h.tab,dir);assert.equal(result.status,'saved');assert.equal(result.save_confirmed,true);
});

test('save refuses a changed field even with an old review',async()=>{
  const h=fake({value:'目标'}),{dir}=await setup(h,p=>{
    p.kind='save';p.reviewed_revision=0;p.save=h.save;p.expected_fields=[{id:'#answer',value:'目标'}];
  });
  h.f.value='用户修改';const result=await runRequest(h.edge,h.tab,dir);
  assert.equal(result.status,'conflict');assert.equal(h.actions.length,0);
});

for(const scenario of [
  {count:40,step:0,expected:40},
  {count:12,step:1000,expected:10},
  {count:3,step:4000,expected:3},
  {count:12,step:1000,expected:0,fail:true},
  {count:12,step:1000,expected:1,budget:2},
  {count:12,step:1000,expected:10,matched:2},
])test('batch minimum and safety '+JSON.stringify(scenario),async()=>{
  const h=fake(),originalNow=Date.now;
  let now=originalNow();
  Date.now=()=>now;
  try{
    const fields=Array.from({length:scenario.count},(_,i)=>({...h.f,id:'#f'+i,selector:'#f'+i,
      value:i<(scenario.matched||0)?'目标':''}));
    h.tab.playwright.evaluate=async()=>({url:'https://example.test/form',fields:structuredClone(fields),save:h.save});
    const locator=selector=>({locator,async fill(value){
      if(scenario.fail)throw Error('transport_failure');
      fields.find(f=>f.selector===selector).value=value;now+=scenario.step;
    }});
    h.tab.playwright.locator=locator;
    const {dir,packet}=await setup(h,p=>{
      p.operations=fields.map(f=>({id:f.id,field:structuredClone(f),value:'目标',depends_on:[]}));
      p.deadline=now/1000+(scenario.budget||1000);
    });
    assert.equal(packet.min_fill_count,Math.min(10,scenario.count-(scenario.matched||0)));
    const result=await runRequest(h.edge,h.tab,dir);
    assert.equal(result.counts.written||0,scenario.expected);
    if(scenario.fail)assert.equal(result.status,'unknown');
  }finally{Date.now=originalNow;}
});

test('executor rejects a weakened minimum before writing',async()=>{
  const h=fake(),{dir,packet}=await setup(h);
  packet.min_fill_count=0;
  await writeFile(join(dir,'request.json'),JSON.stringify({phase:'awaiting_edge',command:packet}));
  await assert.rejects(runRequest(h.edge,h.tab,dir),/invalid_batch_policy/);
  assert.equal(h.actions.length,0);
});

for(const cardText of ['目标','丢失内容'])test('save card readback '+cardText,async()=>{
  const h=fake({value:'目标',card:true,cardText});
  h.save.signal={kind:'module_readback',selector:'#module',editSelector:'form'};
  const {dir}=await setup(h,p=>{p.kind='save';p.reviewed_revision=0;p.save=h.save;p.expected_fields=[{id:'#answer',value:'目标'}];});
  const result=await runRequest(h.edge,h.tab,dir);
  assert.equal(result.save_confirmed,cardText==='目标');
  assert.equal(h.actions.length,1);
});

test('application gate rejects a stale module command before browser actions',async()=>{
  const h=fake(),{dir,packet}=await setup(h);
  const policy=JSON.parse(await readFile(join(dir,'policy.json'),'utf8'));
  await writeFile(join(dir,'policy.json'),JSON.stringify({...policy,application_gate:true,active_command_id:randomUUID(),active_kind:'fill'}));
  await assert.rejects(runRequest(h.edge,h.tab,dir),/not_current_application_command/);
  assert.equal(h.actions.length,0);
});

for(const phase of ['click','readback','read_transport'])test('save failure records exact phase '+phase,async()=>{
  const h=fake({value:'目标'});
  const original=h.tab.playwright.locator;
  h.tab.playwright.locator=s=>{
    const l=original(s),click=l.click;
    l.locator=child=>h.tab.playwright.locator(child);
    l.click=async o=>{if(s==='#save'&&phase==='click')throw Error('transport');return click(o);};
    return l;
  };
  const evaluate=h.tab.playwright.evaluate;
  h.tab.playwright.evaluate=async fn=>{
    if(fn.name==='readSaveUI'&&h.actions.length&&phase==='readback')throw Object.assign(Error('read failed'),{settled:true});
    if(fn.name==='readSaveUI'&&h.actions.length&&phase==='read_transport')throw Error('transport failure');
    return evaluate(fn);
  };
  const {dir,packet}=await setup(h,p=>{p.kind='save';p.reviewed_revision=0;p.save=h.save;p.expected_fields=[{id:'#answer',value:'目标'}];});
  const r=await runRequest(h.edge,h.tab,dir);
  assert.equal(r.settled,phase==='readback');
  assert.equal(r.status,phase==='readback'?'unconfirmed':'unknown');
  const j=JSON.parse(await readFile(join(dir,'writer',packet.command_id+'.json')));
  assert.equal(j.save_stage,phase==='click'?'save_click_issued':'save_click_returned');
  const count=h.actions.length;
  await runRequest(h.edge,h.tab,dir);assert.equal(h.actions.length,count);
});

test('root value race cannot replace reviewed expectations',async()=>{
  const h=fake({value:'目标'});
  const before=await observe(h.edge,h.tab,{moduleId:'m',moduleSelector:'#module'});
  const {dir,packet}=await setup(h,p=>{p.kind='save_scope';p.expected_modules=[before];});
  const policy=JSON.parse(await readFile(join(dir,'policy.json'),'utf8'));
  await writeFile(join(dir,'policy.json'),JSON.stringify({...policy,application_gate:true,active_command_id:packet.command_id,active_kind:'save_scope'}));
  await writeFile(join(dir,'request.json'),JSON.stringify({phase:'awaiting_scope_save',command:packet}));
  const original=h.tab.playwright.evaluate;let reads=0;
  h.tab.playwright.evaluate=async fn=>{if(fn.name==='readModuleDOM'&&++reads===2)h.f.value='未经核验的新值';return original(fn);};
  await assert.rejects(runRequest(h.edge,h.tab,dir),/reviewed_root_value_changed/);
  assert.equal(h.actions.length,0);
});

test('skipped field verification permits scope saving but remains explicit in its receipt',async()=>{
  const h=fake({value:null});h.f.value_readable=false;
  const before=await observe(h.edge,h.tab,{moduleId:'m',moduleSelector:'#module'});
  before.fields[0].verification_skipped=true;
  const {dir,packet}=await setup(h,p=>{p.kind='save_scope';p.expected_modules=[before];});
  const policy=JSON.parse(await readFile(join(dir,'policy.json'),'utf8'));
  await writeFile(join(dir,'policy.json'),JSON.stringify({...policy,application_gate:true,active_command_id:packet.command_id,active_kind:'save_scope'}));
  await writeFile(join(dir,'request.json'),JSON.stringify({phase:'awaiting_scope_save',command:packet}));
  assert.equal((await runRequest(h.edge,h.tab,dir)).status,'saved');
  const receipt=JSON.parse(await readFile(join(dir,'receipt.json'),'utf8'));
  assert.deepEqual(receipt.evidence.verification_skipped_fields,[{module_id:'m',field_id:'#answer'}]);
  assert.equal(h.actions.filter(a=>a[0]==='click').length,1);
});

for(const changed of [false,true])test('shared scope rechecks every reviewed module '+changed,async()=>{
  const h=fake({value:'目标'});
  const before=await observe(h.edge,h.tab,{moduleId:'m',moduleSelector:'#module'});
  before.fields[0].verified_control={command_id:'prior',actual:'目标',source:'/city'};
  const {dir,packet}=await setup(h,p=>{p.kind='save_scope';p.expected_modules=[before];});
  const policy=JSON.parse(await readFile(join(dir,'policy.json'),'utf8'));
  await writeFile(join(dir,'policy.json'),JSON.stringify({...policy,application_gate:true,active_command_id:packet.command_id,active_kind:'save_scope'}));
  await writeFile(join(dir,'request.json'),JSON.stringify({phase:'awaiting_scope_save',command:packet}));
  if(changed){h.f.value='用户修改';await assert.rejects(runRequest(h.edge,h.tab,dir),/reviewed_module_changed/);assert.equal(h.actions.length,0);}
  else {assert.equal((await runRequest(h.edge,h.tab,dir)).status,'saved');assert.equal(h.actions.length,1);}
});

for(const preserved of [false,true])test('scope save permits only graph-authorized preserved blank '+preserved,async()=>{
  const h=fake({value:''});
  const before=await observe(h.edge,h.tab,{moduleId:'m',moduleSelector:'#module'});
  const {dir,packet}=await setup(h,p=>{
    p.kind='save_scope';p.expected_modules=[before];
    p.preserved_blank_field_ids=preserved?['#answer']:[];
  });
  const policy=JSON.parse(await readFile(join(dir,'policy.json'),'utf8'));
  await writeFile(join(dir,'policy.json'),JSON.stringify({...policy,application_gate:true,
    active_command_id:packet.command_id,active_kind:'save_scope'}));
  await writeFile(join(dir,'request.json'),JSON.stringify({phase:'awaiting_scope_save',command:packet}));
  const result=await runRequest(h.edge,h.tab,dir);
  assert.equal(result.status,preserved?'saved':'conflict');
  assert.equal(h.actions.length,preserved?1:0);
});

function cancelEditor({validationErrors=2,saveUIError=false,changed=false}={}){
  const fields=[
    {id:'#degree',selector:'#degree',label:'学位',kind:'text',value:'',signature:{tag:'INPUT',label:'学位'},disabled:false,required:true},
    {id:'#length',selector:'#length',label:'学制',kind:'text',value:'',signature:{tag:'INPUT',label:'学制'},disabled:false,required:true},
  ];
  const actions=[];let open=true;
  const root={
    locator:selector=>selector==='.edit'?{
      count:async()=>1,isVisible:async()=>!open,innerText:async()=>'编 辑'
    }:selector==='#editor'?{count:async()=>open?1:0,isVisible:async()=>open}:root,
    getByRole:(role,{name})=>({
      count:async()=>String(name)==='/^取\\s*消$/'&&open?1:0,isVisible:async()=>String(name)==='/^取\\s*消$/'&&open,
      async click(options){assert.ok(options.timeoutMs>0);actions.push(['click','取消']);open=false;}
    })
  };
  const playwright={
    locator:selector=>selector==='#module'?root:root,
    async evaluate(fn){
      if(fn.name==='readScopeBindings')return fields.map(f=>({root_id:f.id,module_id:'m',field_id:f.id}));
      if(fn.name==='readModuleDOM')return {url:'https://example.test/form',fields:open?structuredClone(fields):[],save:{}};
      if(fn.name==='readSaveUI')return saveUIError?{error:'module_not_unique'}:
        {dialog_count:0,validation_error_count:validationErrors,loading:false};
      if(fn.name==='readControlEvidence')return {root_count:1,dialogs:[],menus:[],fields:open?fields.map(f=>({label:f.label,selector:f.selector})):[]};
      throw Error('unexpected_evaluate:'+fn.name);
    }
  };
  const edge={browserId:'edge-1'},tab={id:'tab-1',url:async()=>'https://example.test/form',playwright};
  return {edge,tab,fields,actions,mutate(){fields[0].value=changed?'用户修改':'值已改变';},close(){open=false;}};
}

async function setupCancel(host){
  const dir=await mkdtemp(join(tmpdir(),'edge-cancel-test-'));
  const snapshot=await observe(host.edge,host.tab,{moduleId:'m',moduleSelector:'#module'});
  const packet={version:1,command_id:randomUUID(),kind:'cancel_module_edit',browser:'edge',target:snapshot.target,
    module_id:'m',module_selector:'#module',cancel_label:'取消',expected_snapshot:snapshot,
    preserved_blank_field_ids:['#degree','#length'],capture:{editControl:'.edit',savedSignal:{editSelector:'#editor'}},deadline:Date.now()/1000+30};
  const policy={target:snapshot.target,module_id:'m',module_selector:'#module',fill:true,save:true,
    application_gate:true,active_command_id:packet.command_id,active_kind:packet.kind};
  await writeFile(join(dir,'policy.json'),JSON.stringify(policy));
  await writeFile(join(dir,'request.json'),JSON.stringify({phase:'awaiting_module_cancel',command:packet}));
  return {dir,packet};
}

test('cancel module edit closes only the reviewed invalid editor',async()=>{
  const h=cancelEditor(),{dir,packet}=await setupCancel(h);
  const result=await runRequest(h.edge,h.tab,dir);
  const receipt=JSON.parse(await readFile(join(dir,'writer',packet.command_id+'.json'),'utf8'));
  assert.equal(result.status,'completed');
  assert.equal(receipt.receipt.evidence.editor_closed,true);
  assert.deepEqual(h.actions,[['click','取消']]);
});

for(const scenario of [
  {name:'validation count changed',options:{validationErrors:1},reason:'validation_failure_changed_before_cancel'},
  {name:'save ui unreadable',options:{saveUIError:true},reason:'validation_failure_changed_before_cancel'},
])test('cancel module edit refuses when '+scenario.name,async()=>{
  const h=cancelEditor(scenario.options),{dir,packet}=await setupCancel(h);
  const result=await runRequest(h.edge,h.tab,dir);
  const receipt=JSON.parse(await readFile(join(dir,'writer',packet.command_id+'.json'),'utf8'));
  assert.equal(result.status,'unconfirmed');
  assert.equal(receipt.receipt.evidence.reason,scenario.reason);
  assert.deepEqual(h.actions,[]);
});

test('cancel module edit refuses a field changed after review',async()=>{
  const h=cancelEditor(),{dir,packet}=await setupCancel(h);h.mutate();
  const result=await runRequest(h.edge,h.tab,dir);
  const receipt=JSON.parse(await readFile(join(dir,'writer',packet.command_id+'.json'),'utf8'));
  assert.equal(result.status,'unconfirmed');
  assert.equal(receipt.receipt.evidence.reason,'module_changed_before_cancel');
  assert.deepEqual(h.actions,[]);
});

test('cancel reconciliation is read-only and confirms a closed reviewed editor',async()=>{
  const h=cancelEditor(),{dir,packet}=await setupCancel(h);h.close();
  packet.kind='reconcile_module_cancel';packet.original_command_id=randomUUID();
  const policy=JSON.parse(await readFile(join(dir,'policy.json'),'utf8'));
  await writeFile(join(dir,'policy.json'),JSON.stringify({...policy,
    active_kind:packet.kind,active_command_id:packet.command_id}));
  await writeFile(join(dir,'request.json'),JSON.stringify({phase:'awaiting_module_cancel_reconciliation',command:packet}));
  const result=await runRequest(h.edge,h.tab,dir);
  assert.equal(result.status,'completed');
  assert.deepEqual(h.actions,[]);
});

function addEditor({alreadyOpen=false,count=1,inline=false,emptyForm=false,moved=false,credentialIcon=false,mountDelay=0,
                    freshMatches=1,driftOnReacquire=false}={}){
  let open=alreadyOpen,reacquired=false,queued=false,mountTicks=0;const actions=[];
  const field={id:'#name',selector:'#name',label:'名称',kind:'text',value:'',signature:{tag:'INPUT',label:'名称'},disabled:false,required:true};
  const records={count:async()=>count+(open?1:0)+(reacquired&&driftOnReacquire?1:0)};
  const parent={
    count:async()=>1,locator:selector=>['.record','form',':scope > .into-content > .el-col:has(label[for^="personalCertificateDTOS["][for$=".certificateType"])'].includes(selector)?records:
    credentialIcon&&selector==='.credentalsBtnGroup > i.el-icon-circle-plus-outline'?{
      count:async()=>1,isVisible:async()=>true,innerText:async()=>'',
      async click(options){assert.ok(options.timeoutMs>0);actions.push(['click','添加证件']);open=true;}
    }:['.add',':scope > .footer > button'].includes(selector)?{
      filter:()=>({count:async()=>selector==='.add'?(moved?0:1):freshMatches,isVisible:async()=>true,innerText:async()=>emptyForm?'+\n 添加教育背景':inline?'添加教育情况':'添 加',
        async click(options){assert.ok(options.timeoutMs>0);actions.push(['click','添加']);if(mountDelay)queued=true;else open=true;}})
    }:parent,
    getByRole:(role,{name})=>({count:async()=>String(name)==='/^添\\s*加$/'?1:0,isVisible:async()=>true,
      async click(options){assert.ok(options.timeoutMs>0);actions.push(['click','添加']);open=true;}})
  };
  const playwright={
    async waitForTimeout(){if(queued&&++mountTicks>=mountDelay)open=true;},
    locator:selector=>selector==='#new'&&inline?{count:async()=>open?1:0}:parent,
    async evaluate(fn,args){
      if(fn.name==='readFrameworkSections'){
        reacquired=true;
        return {family:'ant',sections:[{selector:'#collection',record_selector:'.record',
          records:Array.from({length:count},()=>({})),add_label:'添加教育情况',
          add_selector:':scope > .footer > button'}]};
      }
      if(fn.name==='readScopeBindings')return open?[{root_id:'#name',module_id:'m',field_id:'#name'}]:[];
      if(fn.name==='readModuleDOM')return {url:'https://example.test/form',fields:open?[structuredClone(field)]:[],save:{}};
      if(fn.name==='readControlEvidence')return {root_count:1,dialogs:[],menus:[],fields:inline?[field]:[]};
      throw Error('unexpected_evaluate:'+fn.name);
    }
  };
  return {edge:{browserId:'edge-1'},tab:{id:'tab-1',url:async()=>'https://example.test/form',playwright},actions};
}

async function setupAdd(host,{expected=1,inline=false}={}){
  const dir=await mkdtemp(join(tmpdir(),'edge-add-test-'));
  const packet={version:1,command_id:randomUUID(),kind:'add_module_record',browser:'edge',
    target:{browser:'edge',browser_id:'edge-1',tab_id:'tab-1',url:'https://example.test/form'},
    module_id:'m',module_selector:'#new',collection_selector:'#collection',record_selector:'.record',
    add_control_selector:'.add',expected_record_count:expected,add_label:'添加',capture:{},deadline:Date.now()/1000+30};
  if(inline)Object.assign(packet,{inline_repeater:true,add_label:'添加教育情况'});
  const policy={target:packet.target,module_id:'m',module_selector:'#new',fill:true,save:true,
    application_gate:true,active_command_id:packet.command_id,active_kind:packet.kind};
  await writeFile(join(dir,'policy.json'),JSON.stringify(policy));
  await writeFile(join(dir,'request.json'),JSON.stringify({phase:'awaiting_module_add',command:packet}));
  return {dir,packet};
}

test('missing collection record opens one new editor before mapping',async()=>{
  const h=addEditor(),{dir}=await setupAdd(h);
  const result=await runRequest(h.edge,h.tab,dir);
  assert.equal(result.status,'completed');assert.deepEqual(h.actions,[['click','添加']]);
});

test('readonly search-like combobox without a loaded menu never attempts fill',async()=>{
  const h=fake({kind:'combobox'});h.f.readonly=true;h.f.placeholder='请输入关键词';
  h.f.search_evidence={editable:true,selection_structure:true};
  const base=h.tab.playwright.evaluate;
  h.tab.playwright.evaluate=async(fn,...args)=>fn.name==='readPopupDOM'?{error:'popup_not_unique_or_not_loaded'}:base(fn,...args);
  const {dir}=await setup(h);
  await runRequest(h.edge,h.tab,dir);
  const receipt=JSON.parse(await readFile(join(dir,'receipt.json'),'utf8'));
  assert.equal(receipt.settled,true);
  assert.equal(receipt.results[0].reason,'search_input_not_editable');
  assert.equal(h.actions.filter(a=>a[0]==='fill').length,0);
});
test('empty named form collection adds one editor and rejects nonzero baseline',async()=>{
 for(const expected of [0,1]){
  const h=addEditor({count:0,emptyForm:true}),{dir,packet}=await setupAdd(h,{expected});
  Object.assign(packet,{collection_selector:packet.module_selector,record_selector:'form',add_label:'+添加教育背景'});
  await writeFile(join(dir,'request.json'),JSON.stringify({phase:'awaiting_module_add',command:packet}));
  if(expected===0){assert.equal((await runRequest(h.edge,h.tab,dir)).status,'completed');assert.equal(h.actions.length,1);}
  else{await assert.rejects(runRequest(h.edge,h.tab,dir),/module_add_not_authorized/);assert.equal(h.actions.length,0);}
 }
});

test('restart reuses the already open new-record editor without another add',async()=>{
  const h=addEditor({alreadyOpen:true}),{dir}=await setupAdd(h);
  const result=await runRequest(h.edge,h.tab,dir);
  assert.equal(result.status,'completed');assert.deepEqual(h.actions,[]);
});

test('inline collection permits editable siblings and observes the new row after add',async()=>{
  const h=addEditor({inline:true}),{dir}=await setupAdd(h,{inline:true});
  assert.equal((await runRequest(h.edge,h.tab,dir)).status,'completed');
  assert.equal(h.actions.length,1);
});

test('inline collection count drift does not click add',async()=>{
  const h=addEditor({inline:true,count:2}),{dir}=await setupAdd(h,{inline:true});
  assert.equal((await runRequest(h.edge,h.tab,dir)).status,'unconfirmed');
  assert.deepEqual(h.actions,[]);
});

test('inline Add waits for reactive mount without clicking a second time',async()=>{
  const h=addEditor({inline:true,mountDelay:2}),{dir}=await setupAdd(h,{inline:true});
  assert.equal((await runRequest(h.edge,h.tab,dir)).status,'completed');
  await runRequest(h.edge,h.tab,dir);
  assert.deepEqual(h.actions,[['click','添加']]);
});

for(const count of [1,2])test('credential plus icon adds one row once and rejects count drift '+count,async()=>{
  const h=addEditor({inline:true,credentialIcon:true,count}),{dir,packet}=await setupAdd(h,{inline:true});
  Object.assign(packet,{add_label:'添加证件',add_control_kind:'credential_plus_icon',
    record_selector:':scope > .into-content > .el-col:has(label[for^="personalCertificateDTOS["][for$=".certificateType"])',
    add_control_selector:'.credentalsBtnGroup > i.el-icon-circle-plus-outline'});
  await writeFile(join(dir,'request.json'),JSON.stringify({phase:'awaiting_module_add',command:packet}));
  const result=await runRequest(h.edge,h.tab,dir);
  assert.equal(result.status,count===1?'completed':'unconfirmed');
  await runRequest(h.edge,h.tab,dir);
  assert.deepEqual(h.actions,count===1?[['click','添加证件']]:[]);
});

test('moved inline Add is reacquired once and journal replay does not click again',async()=>{
  const h=addEditor({inline:true,moved:true}),{dir,packet}=await setupAdd(h,{inline:true});
  assert.equal((await runRequest(h.edge,h.tab,dir)).status,'completed');
  const receipt=JSON.parse(await readFile(join(dir,'receipt.json'),'utf8'));
  const journal=JSON.parse(await readFile(join(dir,'writer',packet.command_id+'.json'),'utf8'));
  assert.equal(receipt.evidence.add_reacquisition.selector,':scope > .footer > button');
  assert.equal(receipt.evidence.add_reacquisition.expected_record_count,1);
  assert.deepEqual(journal.add_reacquisition,receipt.evidence.add_reacquisition);
  assert.equal((await runRequest(h.edge,h.tab,dir)).status,'completed');
  assert.deepEqual(h.actions,[['click','添加']]);
});

for(const drift of [false,true])test('moved Add rejects '+(drift?'count drift':'ambiguous controls')+' without clicking',async()=>{
  const h=addEditor({inline:true,moved:true,freshMatches:drift?1:2,driftOnReacquire:drift});
  const {dir}=await setupAdd(h,{inline:true});
  assert.equal((await runRequest(h.edge,h.tab,dir)).status,'unconfirmed');
  const receipt=JSON.parse(await readFile(join(dir,'receipt.json'),'utf8'));
  assert.equal(receipt.settled,true);
  assert.equal(receipt.evidence.reason,drift?'collection_changed_during_add_reacquisition':'add_control_changed');
  assert.deepEqual(h.actions,[]);
});
for(const alreadyOpen of [true,false])test('add reconciliation never clicks whether the old row exists '+alreadyOpen,async()=>{
  const h=addEditor({inline:true,alreadyOpen}),{dir,packet}=await setupAdd(h,{inline:true});
  Object.assign(packet,{reconcile_only:true,original_command_id:randomUUID()});
  await writeFile(join(dir,'request.json'),JSON.stringify({phase:'awaiting_module_add',command:packet}));
  const result=await runRequest(h.edge,h.tab,dir);
  assert.equal(result.status,alreadyOpen?'completed':'unconfirmed');assert.deepEqual(h.actions,[]);
  if(alreadyOpen)assert.equal(JSON.parse(await readFile(join(dir,'receipt.json'),'utf8')).evidence.reconciliation_only,true);
});
