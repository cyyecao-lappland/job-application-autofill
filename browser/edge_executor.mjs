import {readPopupDOM,readComboCommitDOM} from './rules/popup.mjs';
import {Deferred,Conflict,milliseconds,fieldLocator,normalize,same,sameValue,actionStage,fileMatches} from './controls/runtime.mjs';
import {chooseCombo} from './controls/adapters/combobox.mjs';
export {readPopupDOM,readTagSelectionDOM,readComboCommitDOM} from './rules/popup.mjs';
import {incompleteRead} from './controls/driver.mjs';
import {executeControl, executeFieldControl, assertRegistry, registryReport, adapterNames, matchControl, hasFieldControl, targetFromLegacy} from './controls/service.mjs';
import {readAlibabaEducationPersistence,confirmsAbsentEducation} from './persisted_readback.mjs';
import {detectLinkage} from './linkage_detection.mjs';
import {readModuleDOM,readControlEvidence} from './control_detection.mjs';
import {readFrameworkSections} from './rules/page_sections.mjs';
import {resolveCollectionAdd} from './rules/collection_add.mjs';
export {readModuleDOM} from './control_detection.mjs'; // Compatibility for existing callers.
/* Execute through the registered backend using the existing Edge session.
 * No browser launch or arbitrary page-state writes. Native Playwright may use
 * a registered read-only persistence oracle during save reconciliation.
 */
import {randomUUID} from 'node:crypto';
import {readFile, writeFile, rename, mkdir, open, unlink} from 'node:fs/promises';
import {join,dirname} from 'node:path';
import {inspectSaveUI, awaitSaveOutcome, confirmCard, confirmStructuredScope,readElementPreviewCard,readProjectPreviewCard,readSfEducationPreviewCard} from './save_observer.mjs';
import {classifySaveAction} from './save_scope_detection.mjs';
import {identityField,identityValue,readIdentityMatch} from './confidential_identity.mjs';
import {activateModuleTab,openModuleEditor} from './module_navigation.mjs';
import {FAILURE_TEXT,FIRST_OPTION} from './fallback_policy.mjs';

const protectedLabel = /(?!)/; // No privacy-based field exclusion.
const reviewedDOMField = field => Object.fromEntries(Object.entries(field).filter(([key])=>
  !['verified_control','verification_skipped','verification_skip_command'].includes(key)));
const stableSavedFields = fields => fields.map(f=>Object.fromEntries(Object.entries(reviewedDOMField(f)).filter(([k])=>k!=='controls')));
export const executorName = backend => backend?.backend === 'playwright' ? 'playwright' : 'codex-edge';

function diagnosticText(value,privateValues=[]){
  let text=String(value??'');
  for(const secret of [...privateValues].filter(v=>typeof v==='string'&&v).sort((a,b)=>b.length-a.length))
    text=text.split(secret).join('[redacted-value]');
  return text.replace(/\d{8,}/g,'[redacted-number]')
    .replace(/[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}/g,'[redacted-email]')
    .replace(/((?:password|token|secret|authorization)\s*[:=]\s*)[^\s,;]+/gi,'$1[redacted-secret]').slice(0,500);
}

// Retain the actionable runtime text, never values supplied to/read from controls.
export function sanitizedError(error,privateValues=[]){
  const message=diagnosticText(error?.message,privateValues);
  const code=/timeout|timed out/i.test(message)?'timeout':
    /transport|connection|disconnected/i.test(message)?'transport_failure':
    /strict mode|ambiguous|not.unique/i.test(message)?'ambiguous_target':'browser_call_failed';
  return {code,message};
}

// Wrap only calls the executor already makes. No retries, probes, or settlement guesses.
export function instrumentTab(tab,journal,checkpoint,packet={}){
  const context={stage:null,operation:null,field_id:null,field_label:null};
  const privateValues=new Set();
  const remember=(value,personal=false)=>{
    if(Array.isArray(value)){for(const item of value)remember(item,personal);}
    else if(value&&typeof value==='object'){
      for(const [key,item] of Object.entries(value))remember(item,personal||['value','expected','source_value'].includes(key));
    }else if(personal&&typeof value==='string'&&value)privateValues.add(value);
  };
  remember(packet);
  const invoke=async(stage,method,fn)=>{
    const entry={stage,method,operation:context.operation,
      field_id:context.field_id===null?null:diagnosticText(context.field_id,privateValues),
      field_label:context.field_label===null?null:diagnosticText(context.field_label,privateValues),
      at:Date.now()/1000,status:'pending'};
    (journal.instrumentation??=[]).push(entry);
    await checkpoint(journal);
    const started=performance.now();
    try{const result=await fn();remember(result);entry.status='returned';
      if(stage==='popup_baseline_read')entry.result_shape={type:typeof result,keys:result&&typeof result==='object'?Object.keys(result):[],
        visible_array:Array.isArray(result?.visible),menus_array:Array.isArray(result?.menus)};
      return result;}
    catch(error){entry.status='failed';entry.error=sanitizedError(error,privateValues);throw error;}
    finally{entry.duration_ms=Math.max(0,performance.now()-started);await checkpoint(journal);}
  };
  const wrapLocator=locator=>new Proxy(locator,{get(target,key){
    const value=target[key];
    if(typeof value!=='function')return value;
    if(['locator','getByRole','getByText','getByLabel','filter','nth','first','last'].includes(key))
      return (...args)=>wrapLocator(value.apply(target,args));
    return (...args)=>{
      if(['fill','selectOption','setInputFiles'].includes(key))remember(args[0],true);
      return invoke(context.stage||(['fill','check','setChecked','selectOption','setInputFiles','click'].includes(key)?'field_action':'control_read'),
        String(key),()=>value.apply(target,args));
    };
  }});
  const playwright=new Proxy(tab.playwright,{get(target,key){
    if(key==='locator')return (...args)=>wrapLocator(target.locator(...args));
    if(key==='evaluate')return (fn,...args)=>{
      remember(args);
      const stage=fn===readPopupDOM?(args[0]?.captureBaseline?'popup_baseline_read':'popup_read'):
        fn===readComboCommitDOM?'popup_readback':fn===readModuleDOM?'module_readback':
        fn===readIdentityMatch?'identity_readback':context.stage||'ui_read';
      return invoke(stage,'evaluate',()=>target.evaluate(fn,...args));
    };
    const value=target[key];return typeof value==='function'?value.bind(target):value;
  }});
  return new Proxy(tab,{get(target,key){
    if(key==='playwright')return playwright;
    if(key==='instrumentationContext')return context;
    if(key==='getJsDialog'&&typeof target[key]==='function')
      return (...args)=>invoke('dialog_read','getJsDialog',()=>target[key](...args));
    if(key==='reload'&&typeof target[key]==='function')
      return (...args)=>invoke('saved_readback_reload','reload',()=>target[key](...args));
    const value=target[key];return typeof value==='function'?value.bind(target):value;
  }});
}

export async function observe(edge,tab,{moduleId,moduleSelector,capture={},timeoutMs=10000}){
  if(!moduleId || !moduleSelector)throw new Error('module_scope_required');
  const dom=await tab.playwright.evaluate(readModuleDOM,{moduleSelector,...capture},{timeoutMs});
  if(dom.error)throw new Error(dom.error);
  for(const field of dom.fields.filter(identityField)){
    let expected;
    try{expected=await identityValue();}catch{continue;}
    const check=await tab.playwright.evaluate(readIdentityMatch,{moduleSelector,selector:field.selector,expected},{timeoutMs});
    if(check.unique)field.secret_match=check.match;
  }
  let recoveryDiagnostics;
  if(capture.recoveryFieldSelector){
    const baseline=await tab.playwright.evaluate(readPopupDOM,{moduleSelector,selector:capture.recoveryFieldSelector,captureBaseline:true},{timeoutMs});
    recoveryDiagnostics={type:typeof baseline,keys:baseline&&typeof baseline==='object'?Object.keys(baseline):[],
      visible_array:Array.isArray(baseline?.visible),menus_array:Array.isArray(baseline?.menus),error:baseline?.error||null};
  }
  return {version:1,snapshot_id:randomUUID(),observed_at:Date.now()/1000,
    ...(recoveryDiagnostics?{recovery_diagnostics:recoveryDiagnostics}:{}),
    target:{browser:'edge',browser_id:edge.browserId,tab_id:tab.id,url:dom.url},
    module_id:moduleId,module_selector:moduleSelector,fields:dom.fields,save:dom.save,capture,
    ...(dom.record_boundary?{record_boundary:dom.record_boundary}:{})};
}

export async function retryAtomicRename(from,to,replace=rename,wait=ms=>new Promise(r=>setTimeout(r,ms))){
  for(let attempt=0;;attempt++){
    try{return await replace(from,to);}catch(error){
      if(!['EPERM','EACCES','EBUSY'].includes(error.code)||attempt>=5)throw error;
      await wait(20*2**attempt);
    }
  }
}

async function atomicJSON(path,value){
  const tmp=path+'.'+randomUUID()+'.tmp';
  const handle=await open(tmp,'wx');
  try{await handle.writeFile(JSON.stringify(value,null,2),'utf8');await handle.sync();}finally{await handle.close();}
  await retryAtomicRename(tmp,path);
}

export async function saveSnapshot(path,snapshot){await atomicJSON(path,snapshot);}

export function credentialIconAdd(packet){
  return packet.add_control_kind==='credential_plus_icon'&&packet.inline_repeater===true&&
    packet.add_label==='添加证件'&&Number.isInteger(packet.expected_record_count)&&
    packet.expected_record_count>=1&&packet.expected_record_count<=2&&
    packet.record_selector===':scope > .into-content > .el-col:has(label[for^="personalCertificateDTOS["][for$=".certificateType"])'&&
    packet.add_control_selector==='.credentalsBtnGroup > i.el-icon-circle-plus-outline';
}

function validatePacket(packet,policy,phase){
  if(policy.application_gate&&(packet?.command_id!==policy.active_command_id||packet?.kind!==policy.active_kind))
    throw new Error('not_current_application_command');
  if(packet.version!==1 || packet.browser!=='edge' || packet.target?.browser!=='edge')throw new Error('edge_only');
  if(!same(packet.target,policy.target) || packet.module_id!==policy.module_id || packet.module_selector!==policy.module_selector)
    throw new Error('wrong_target_or_scope');
  if(!/^[a-f0-9-]{36}$/.test(packet.command_id))throw new Error('invalid_command_id');
  if(Date.now()/1000>=packet.deadline)throw new Error('run_deadline');
  if(packet.kind==='fill'){
    if(phase!=='awaiting_edge'||!policy.fill||!Array.isArray(packet.operations)||!packet.operations.length)
      throw new Error('fill_not_authorized');
    const minimum=Math.min(10,packet.operations.filter(op=>op.secret_ref?op.field?.secret_match!==true:!sameValue(op.field?.value,op.value)).length);
    // A stale overestimate can occur when enum repair turns a pending source
    // value into the label already displayed by an expanded control. It may
    // only extend the soft batch boundary; it cannot authorize another write.
    // Understating the live mutation count remains forbidden.
    if(!Number.isInteger(packet.min_fill_count)||packet.min_fill_count<minimum||
        packet.min_fill_count>Math.min(10,packet.operations.length)||
        !Number.isFinite(packet.max_batch_ms)||packet.max_batch_ms<=0)
      throw new Error('invalid_batch_policy');
    const seen=new Set(packet.completed_ids || []);
    for(const op of packet.operations){
      if(op.fallback && (packet.agent_tuning !== false || op.source !== null || op.transform !== 'draft_fallback' ||
          !(op.fallback==='text_marker' && op.field?.kind==='text' && op.value===FAILURE_TEXT ||
            op.fallback==='first_option' && ['select','combobox'].includes(op.field?.kind) && op.value===FIRST_OPTION)))
        throw new Error('fallback_not_authorized');
      const confidential=op.secret_ref?.kind==='identity_document_number'&&op.secret_ref.source==='/identity/identity_document_number'&&op.source===op.secret_ref.source&&op.value===null&&identityField(op.field);
      if(op.secret_ref&&!confidential)throw new Error('invalid_confidential_reference');
      if(!['text','checkbox','radio','radio_group','select','combobox','file'].includes(op.field?.kind)||!op.field.selector)
        throw new Error('unsupported_or_protected_field');
      if(op.id!==op.field.id||seen.has(op.id)||!op.depends_on.every(id=>seen.has(id)))throw new Error('invalid_operation_order');
      seen.add(op.id);
    }
  }else if(packet.kind==='reconcile_save'){
    if(!policy.application_gate||phase!=='awaiting_reconciliation')throw new Error('reconciliation_not_authorized');
  }else if(packet.kind==='observe'){
    if(!policy.application_gate||phase!=='awaiting_observation')throw new Error('observe_not_authorized');
  }else if(packet.kind==='activate_module'){
    if(!policy.application_gate||phase!=='awaiting_module_navigation'||
      !packet.navigation?.menu_selector||!packet.navigation?.label)throw new Error('module_navigation_not_authorized');
  }else if(packet.kind==='add_module_record'){
    if(!policy.application_gate||!policy.fill||phase!=='awaiting_module_add'||
       !(credentialIconAdd(packet)||packet.add_label==='添加'||(packet.inline_repeater===true&&!packet.add_control_kind&&/^添加[^\r\n]{1,30}$/.test(packet.add_label))||
         (packet.expected_record_count===0&&packet.record_selector==='form'&&packet.module_selector===packet.collection_selector&&
          /^\+?添加(?:教育背景|实习经历|项目经验)$/.test(packet.add_label)))||
       !packet.collection_selector||!packet.record_selector||!Number.isInteger(packet.expected_record_count)||
       !packet.add_control_selector||packet.expected_record_count<0||
       (packet.max_records!==undefined&&(!Number.isInteger(packet.max_records)||packet.max_records<packet.expected_record_count)))
      throw new Error('module_add_not_authorized');
  }else if(packet.kind==='delete_module_record'){
    if(!policy.application_gate||!policy.fill||phase!=='awaiting_module_delete'||
       packet.delete_label!=='删除本条记录'||packet.confirm_label!=='确定'||
       !packet.collection_selector||!packet.record_selector||!packet.edit_control_selector||
       !packet.delete_control_selector||!Number.isInteger(packet.expected_record_count)||
       packet.expected_record_count<=0||!Array.isArray(packet.record_identity_fields)||
       !packet.record_identity_fields.length||packet.record_identity_fields.some(item=>
         !item||Object.keys(item).sort().join(',')!=='label,value'||
         typeof item.label!=='string'||!item.label||typeof item.value!=='string'||!item.value))
      throw new Error('module_delete_not_authorized');
  }else if(packet.kind==='verify_module_record_absent'){
    if(!policy.application_gate||phase!=='awaiting_module_absence_verification'||
       !packet.collection_selector||!packet.record_selector||!Number.isInteger(packet.expected_record_count)||
       packet.expected_record_count<0||!Array.isArray(packet.record_identity_fields)||
       !packet.record_identity_fields.length||packet.record_identity_fields.some(item=>
         !item||Object.keys(item).sort().join(',')!=='label,value'||
         typeof item.label!=='string'||!item.label||typeof item.value!=='string'||!item.value))
      throw new Error('module_absence_verification_not_authorized');
  }else if(packet.kind==='edit_module'){
    if(!policy.application_gate||!policy.fill||phase!=='awaiting_module_edit'||!packet.edit_selector)throw new Error('module_edit_not_authorized');
  }else if(packet.kind==='cancel_module_edit'){
    const reviewedIds=packet.expected_snapshot?.fields?.map(field=>field.id)||[];
    const preserved=packet.preserved_blank_field_ids||[];
    if(!policy.application_gate||!policy.fill||phase!=='awaiting_module_cancel'||packet.cancel_label!=='取消'||
       packet.expected_snapshot?.target?.tab_id!==packet.target.tab_id||!reviewedIds.length||
       !Array.isArray(preserved)||!preserved.length||new Set(preserved).size!==preserved.length||
       preserved.some(id=>!reviewedIds.includes(id)))throw new Error('module_cancel_not_authorized');
  }else if(packet.kind==='reconcile_module_cancel'){
    if(!policy.application_gate||phase!=='awaiting_module_cancel_reconciliation'||
       !packet.original_command_id||!packet.capture?.editControl||!packet.capture?.savedSignal?.editSelector)
      throw new Error('module_cancel_reconciliation_not_authorized');
  }else if(packet.kind==='save_scope'){
    if(!policy.application_gate||phase!=='awaiting_scope_save'||!policy.save||!packet.expected_modules?.length)
      throw new Error('scope_save_not_authorized');
    const reviewedIds=packet.expected_modules.flatMap(module=>module.fields.map(field=>field.id));
    const preserved=packet.preserved_blank_field_ids||[];
    if(!Array.isArray(preserved)||new Set(preserved).size!==preserved.length||
        preserved.some(id=>typeof id!=='string'||!reviewedIds.includes(id)))
      throw new Error('invalid_preserved_blank_fields');
  }else if(packet.kind==='verify_autosave'){
    if(!policy.application_gate||phase!=='awaiting_scope_save'||!policy.save||!packet.expected_modules?.length||!packet.capture?.savedSignal)
      throw new Error('automatic_save_verification_not_authorized');
  }else if(packet.kind==='save'){
    if(phase!=='awaiting_save'||!policy.save||packet.revision!==packet.reviewed_revision)throw new Error('save_not_authorized');
    if(classifySaveAction(packet.save?.label).pattern!=='SAVE_STAY'||!packet.save.signal)
      throw new Error('save_semantics_not_verified');
  }else if(packet.kind==='control_fill'){
    if(!policy.application_gate||!policy.fill||phase!=='awaiting_control'||!adapterNames().includes(packet.adapter)||!packet.field_selector||!packet.source)throw new Error('control_not_authorized');
  }else throw new Error('no_such_action'); // No submit, navigation, file, arbitrary click, or JS action.
}

async function assertTarget(edge,tab,target){
  if(edge.browserId!==target.browser_id||tab.id!==target.tab_id||await tab.url()!==target.url)
    throw new Conflict('page_identity_changed');
}

async function currentField(edge,tab,packet,id,deadline){
  const snapshot=await observe(edge,tab,{moduleId:packet.module_id,moduleSelector:packet.module_selector,capture:packet.capture,
    timeoutMs:deadline?milliseconds(deadline):10000});
  if(!same(snapshot.target,packet.target))throw new Conflict('page_identity_changed');
  if(packet.record_boundary&&JSON.stringify(snapshot.record_boundary)!==JSON.stringify(packet.record_boundary))
    throw new Conflict('record_boundary_changed');
  let matches=snapshot.fields.filter(f=>f.id===id);
  // Recovery preserves the logical operation id while rebinding a mutable DOM
  // selector (for example an Element UI placeholder) to a fresh structural
  // selector.  Resolve that one field by the freshly reviewed selector and
  // semantic signature; never fall back to a label-only or first match.
  if(matches.length===0){
    const expected=packet.operations?.find(op=>op.id===id)?.field;
    if(expected?.selector)matches=snapshot.fields.filter(f=>f.selector===expected.selector&&
      same(f.signature,expected.signature)&&f.kind===expected.kind&&f.protected===expected.protected);
  }
  if(matches.length!==1)throw new Conflict('field_missing_or_ambiguous');
  return {field:matches[0],snapshot};
}

// Explicit single-control regression only; never resumes or clears a workflow.
// The caller supplies a fresh observed field and a value resolved from the profile.
export async function testDropdown(edge,tab,{snapshot,fieldId,value,evidencePath}){
  const f=snapshot.fields.find(f=>f.id===fieldId);
  if(!f||f.kind!=='combobox'||f.component!=='element-select'||f.protected||typeof value!=='string')throw new Error('invalid_dropdown_probe');
  if(Date.now()/1000-snapshot.observed_at>60)throw new Error('stale_probe');
  await assertTarget(edge,tab,snapshot.target);
  const lock=await open(evidencePath,'wx');
  const record={kind:'single_dropdown_regression',target:snapshot.target,field_label:f.label,before:f.value,
    expected:value,status:'pending',started_at:Date.now()/1000,save:false,workflow_resumed:false};
  const persist=async()=>{await lock.truncate(0);await lock.write(JSON.stringify(record,null,2),0,'utf8');await lock.sync();};
  try{
    await persist();
    const packet={target:snapshot.target,module_id:snapshot.module_id,module_selector:snapshot.module_selector,capture:snapshot.capture};
    const {field}=await currentField(edge,tab,packet,fieldId,Date.now()+5000);
    if(!same(field.signature,f.signature)||!sameValue(field.value,f.value))throw new Conflict('probe_field_changed');
    if(field.value===value){record.status='already_matched';}
    else{
      await chooseCombo(tab,packet,field,value,Date.now()+8000,()=>{record.action_issued=true;},async(stage,detail)=>{
        record.stage=stage;(record.trace??=[]).push({stage,at:Date.now()/1000,...detail});await persist();});
      const after=await currentField(edge,tab,packet,fieldId,Date.now()+3000);
      record.after=after.field.value;
      record.status=after.field.value===value?'verified':'mismatch';
    }
  }catch(error){record.status='failed';record.error_kind=error.name;record.reason=error instanceof Deferred||error instanceof Conflict?error.message:'browser_call_failed';
    record.error_detail=sanitizedError(error,[value,f.value]).message;}
  finally{record.completed_at=Date.now()/1000;await persist();await lock.close();}
  return record;
}

async function fillBatch(edge,tab,packet,journal,checkpoint){
  const results=[],completed=new Set(packet.completed_ids||[]);
  const enumCandidates=[];
  const controlDiagnostics=[];
  const fallbackValues={};
  const baselines=new Map(packet.operations.map(op=>[op.id,op.field.value]));
  let unknown=false;
  let remoteUnsettled=false;
  const softEnd=Date.now()+packet.max_batch_ms;
  let written=0;
  let linkage=null;
  const recordLinkage=async(before,after,op,end)=>{
    // Allow asynchronous dependent fields to settle. The window is local and
    // bounded, never an assumption that arbitrary network work has finished.
    if(['select','combobox','radio','radio_group','checkbox'].includes(op.field.kind)){
      let previous=JSON.stringify(after.fields),stable=0;
      const until=Math.min(end,Date.now()+1200);
      while(Date.now()+100<until&&stable<2){
        await new Promise(resolve=>setTimeout(resolve,100));
        const next=await observe(edge,tab,{moduleId:packet.module_id,moduleSelector:packet.module_selector,capture:packet.capture});
        const signature=JSON.stringify(next.fields);
        stable=signature===previous?stable+1:0;previous=signature;after=next;
      }
    }
    linkage=detectLinkage(before,after,op.id);
  };
  for(const original of packet.operations){
    const op={...original};
    if(tab.instrumentationContext){Object.assign(tab.instrumentationContext,{operation:packet.operations.indexOf(original)+1,
      field_id:op.id,field_label:op.field.label});actionStage(tab,null);}
    const reason=unknown?'unknown_write':linkage?'linkage_barrier':Date.now()>=packet.deadline*1000?'run_deadline':
      written>=packet.min_fill_count&&Date.now()>=softEnd?'batch_boundary':null;
    if(reason){results.push({id:op.id,status:'unattempted',reason});continue;}
    // Batch time is a soft yield boundary, not an in-flight action timeout.
    // max_batch_ms is a soft boundary checked before an operation starts.  Do
    // not also turn the remainder of that batch window into the in-flight
    // timeout: a control near the boundary (notably a multi-step date picker)
    // would otherwise start successfully and then receive only a few hundred
    // milliseconds for its final click.  Once admitted, give the operation a
    // fresh bounded window while still respecting the application deadline.
    const end=Math.min(packet.deadline*1000,Date.now()+Math.max(packet.max_batch_ms,20_000));
    if(!op.depends_on.every(id=>completed.has(id))){results.push({id:op.id,status:'deferred',reason:'dependency_not_ready'});continue;}
    let actionIssued=false;
    const markAction=()=>{actionIssued=true;};
    try{
      const before=await currentField(edge,tab,packet,op.id,end);
      let {field}=before;
      if(!same(field.signature,op.field.signature))throw new Conflict('field_identity_changed');
      // Fresh page state is authoritative on every restart. A readable exact
      // match needs no write handler and must be skipped before method routing.
      if(!op.secret_ref&&field.kind==='file'&&fileMatches(field,op.value)){
        results.push({id:op.id,status:'already_matched',reason:''});completed.add(op.id);continue;
      }
      if(!op.secret_ref&&sameValue(field.value,op.value)){
        if(field.value_readable!==false&&field.expanded!=='true'){
          results.push({id:op.id,status:'already_matched',reason:''});completed.add(op.id);continue;
        }
        // An open Element menu still needs its owned option and closure checks.
        // Route it through the existing adapter instead of trusting input text.
        if(field.component!=='element-select'||field.expanded!=='true')throw new Deferred('selection_not_committed');
      }
      const registeredControl=matchControl(field);
      if(!registeredControl&&!hasFieldControl(field)&&!op.secret_ref)throw new Deferred('unknown_control_method');
      if(registeredControl&&!op.secret_ref&&!op.fallback){
        // Probe plain text candidates in the executor. The adapter checks the
        // module/field identity, refuses any popup or dialog discovered by the
        // focus action, fills, blurs, and reads the DOM value back. This keeps
        // control classification separate from semantic field mapping while
        // allowing an ordinary input to graduate programmatically.
        const probe={...packet,field_label:field.label,field_selector:field.selector,
          before_value:field.value,location:op.value,allow_logical_selector:true,
          deadline:Math.min(packet.deadline,end/1000)};
        const outcome=await executeControl(tab,{...probe,adapter:registeredControl,controlTarget:targetFromLegacy(registeredControl,op.value),controlEvidence:field},async detail=>{
          if(detail.stage?.endsWith('_issued'))markAction();
          journal.control={operation:packet.operations.indexOf(original)+1,...detail};await checkpoint(journal);
        });
        journal.control_evidence=outcome;await checkpoint(journal);
        const after=await currentField(edge,tab,packet,op.id,Math.min(packet.deadline*1000,Date.now()+3000));
        if(outcome.verification==='mismatch')throw new Conflict('value_did_not_match_after_action');
        const skip=outcome.verification==='skipped'||incompleteRead(after.field.value,after.field.value_readable===false);
        if(!skip&&!sameValue(after.field.value,op.value))throw new Conflict('value_did_not_match_after_action');
        results.push({id:op.id,status:skip?'verification_skipped':'written',reason:skip?'readback_incomplete':''});completed.add(op.id);written++;
        await recordLinkage(before.snapshot,after.snapshot,op,end);
        journal.pending_field=null;journal.results=results;await checkpoint(journal);continue;
      }
      if(op.secret_ref){
        if(!identityField(field)||field.disabled)throw new Conflict('identity_field_changed');
        if(field.secret_match===true){results.push({id:op.id,status:'already_matched',reason:''});completed.add(op.id);continue;}
        if(field.value_present)throw new Conflict('existing_identity_differs');
        const secret=await identityValue();
        journal.pending_field=op.id;await checkpoint(journal);
        markAction();await fieldLocator(tab,packet,field).fill(secret,{timeoutMs:milliseconds(end)});
        const check=await currentField(edge,tab,packet,op.id,end);
        if(check.field.secret_match!==true)throw new Conflict('identity_readback_mismatch');
        results.push({id:op.id,status:'written',reason:''});completed.add(op.id);written++;
        journal.pending_field=null;journal.results=results;await checkpoint(journal);continue;
      }
      if(!sameValue(field.value,baselines.get(op.id)))throw new Conflict('old_value_changed');
      // The fresh DOM snapshot already checked visibility and locator uniqueness.
      // Cascades can briefly disable children. Read only until the child becomes usable.
      while(field.disabled){
        milliseconds(end);await new Promise(resolve=>setTimeout(resolve,60));
        ({field}=await currentField(edge,tab,packet,op.id,end));
      }
      journal.pending_field=op.id;await checkpoint(journal);
      await executeFieldControl({tab,packet,field,op,end,markAction,
        readField:deadline=>currentField(edge,tab,packet,op.id,deadline),
        checkpoint:async detail=>{
          if(detail.stage?.endsWith('_issued'))markAction();
          journal.control={operation:packet.operations.indexOf(original)+1,...detail};await checkpoint(journal);
        },
        recordOutcome:async outcome=>{journal.control_evidence=outcome;await checkpoint(journal);}});
      const after=await currentField(edge,tab,packet,op.id,Math.min(packet.deadline*1000,Date.now()+3000));field=after.field;
      const skip=field.kind!=='file'&&incompleteRead(field.value,field.value_readable===false);
      if(!skip&&(field.kind==='file'?!fileMatches(field,op.value):!sameValue(field.value,op.value)))throw new Conflict('value_did_not_match_after_action');
      if(field.kind==='combobox'&&field.expanded==='true')throw new Conflict('selection_not_committed');
      if(op.fallback==='first_option')fallbackValues[op.id]=op.value;
      results.push({id:op.id,status:skip?'verification_skipped':'written',reason:skip?'readback_incomplete':''});completed.add(op.id);
      written++;
      await recordLinkage(before.snapshot,after.snapshot,op,end);
      // Capture a cascade's reset values immediately after its parent action.
      // Later user changes are still conflicts; do not simply waive the old-value check.
      for(const child of packet.operations.filter(child=>child.depends_on.includes(op.id))){
        const actual=after.snapshot.fields.find(f=>f.id===child.id);
        if(actual)baselines.set(child.id,actual.value);
      }
      journal.pending_field=null;journal.results=results;await checkpoint(journal);
    }catch(error){
      if(error.enumCandidate&&!op.fallback)enumCandidates.push(error.enumCandidate);
      if(error.controlDiagnostic)controlDiagnostics.push(error.controlDiagnostic);
      const operationEvents=(journal.instrumentation||[]).filter(e=>e.operation===tab.instrumentationContext?.operation);
      const localBeforeAction=!actionIssued&&operationEvents.length>0&&operationEvents.every(e=>e.status==='returned');
      const localFailure=!(error instanceof Deferred||error instanceof Conflict)&&localBeforeAction;
      const known=error instanceof Deferred||error instanceof Conflict||localFailure;
      const status=known?(error instanceof Conflict?'conflict':'deferred'):'unknown';
      results.push({id:op.id,status,reason:localFailure?'local_executor_error':known?error.message:'browser_call_failed'});
      if(!known){unknown=true;remoteUnsettled=error.settled!==true;journal.remote_unsettled=remoteUnsettled;}
      else journal.pending_field=null;
      journal.action_issued=actionIssued;journal.results=results;await checkpoint(journal);
      journal.last_error={kind:error.name||'Error',phase:actionIssued?'action_or_readback':'before_action',
        code:localFailure?'local_executor_error':known?error.message:'browser_call_failed',sanitized:journal.instrumentation?.findLast(e=>e.status==='failed')?.error||
          sanitizedError(error,packet.operations.flatMap(o=>[o.value,o.field?.value]).flat())};await checkpoint(journal);
    }
  }
  // An unsettled browser call prohibits even another read of the tab.
  const snapshot=unknown?null:await observe(edge,tab,{moduleId:packet.module_id,moduleSelector:packet.module_selector,capture:packet.capture});
  return {command_id:packet.command_id,kind:'fill',target:packet.target,settled:!remoteUnsettled,
    status:unknown?'unknown':results.every(r=>['written','already_matched','verification_skipped'].includes(r.status))?'completed':'partial',
    results,snapshot,evidence:{executor:executorName(edge),action:'semantic_controls',enum_candidates:enumCandidates,control_diagnostics:controlDiagnostics,fallback_values:fallbackValues,
      ...(linkage?{linkage}:{}),completed_at:Date.now()/1000}};
}

async function readSignal(tab,signal){
  const l=tab.playwright.locator(signal.selector);
  return await l.count()===1&&await l.isVisible()?await l.innerText({timeoutMs:1000}):null;
}

async function saveModule(edge,tab,packet,journal,checkpoint){
  actionStage(tab,null);
  const before=await observe(edge,tab,{moduleId:packet.module_id,moduleSelector:packet.module_selector,capture:packet.capture});
  const receipt={command_id:packet.command_id,kind:'save',target:packet.target,settled:true,status:'conflict',results:[],snapshot:before,evidence:{save_confirmed:false}};
  if(!same(before.target,packet.target))return receipt;
  if(!packet.expected_fields.every(e=>before.fields.some(f=>f.id===e.id&&
    (sameValue(f.value,e.value)||(e.verification_skipped&&incompleteRead(f.value,f.value_readable===false))))))return receipt;
  const preservedBlankIds=new Set([...(packet.preserved_blank_field_ids||[]),
    ...packet.expected_fields.filter(f=>f.verification_skipped).map(f=>f.id)]);
  if(before.fields.some(f=>f.required&&!preservedBlankIds.has(f.id)&&
      (f.protected?!f.value_present:(f.value===null||f.value===''||(Array.isArray(f.value)&&!f.value.length)||(f.kind==='checkbox'&&f.value!==true)))))return receipt;
  if(!same(before.save,packet.save)||!before.save.enabled)return receipt;
  const saveAction=classifySaveAction(packet.save.label);
  if(saveAction.pattern!=='SAVE_STAY')return {...receipt,status:'unconfirmed',evidence:{...receipt.evidence,
    action_pattern:saveAction.pattern,reason:'save_action_handler_not_verified'}};
  const signal=packet.save.signal;
  const cardSignal=signal.kind==='module_readback';
  if(cardSignal){
    if(signal.selector!==packet.module_selector||!signal.editSelector)return {...receipt,status:'unconfirmed'};
    const edit=tab.playwright.locator(packet.module_selector).locator(signal.editSelector);
    if(await edit.count()!==1||!await edit.isVisible())return {...receipt,status:'unconfirmed'};
  }else if(await readSignal(tab,signal)===signal.text)return {...receipt,status:'unconfirmed'}; // Reject a stale success notice.
  const button=tab.playwright.locator(packet.module_selector).locator(packet.save.selector);
  if(await button.count()!==1||normalize(await button.innerText({timeoutMs:1000}))!==normalize(packet.save.label))return receipt;
  const stage=async name=>{journal.save_stage=name;actionStage(tab,name==='confirmation_issued'?'save_confirmation':name==='confirmation_returned'?'save_readback':null);
    (journal.stages??=[]).push({name,at:Date.now()/1000});await checkpoint(journal);};
  const ui=await inspectSaveUI(tab,packet.module_selector);
  if(ui.native_dialog||ui.dialog_count||ui.error)return {...receipt,status:'unconfirmed',evidence:{save_confirmed:false,signal:'preexisting_dialog_or_invalid_scope',ui}};
  try{
    await stage('save_click_issued');
    actionStage(tab,'save_click');
    await button.click({timeoutMs:Math.min(5000,milliseconds(packet.deadline*1000))});
    await stage('save_click_returned');
    actionStage(tab,'save_readback');
    const result=await awaitSaveOutcome(tab,packet,stage);
    await stage('observation_returned');
    const saveObservedAt=Date.now()/1000;
    let post=null;
    if(result.confirmed)post=await observe(edge,tab,{moduleId:packet.module_id,moduleSelector:packet.module_selector,capture:packet.capture});
    return {...receipt,status:result.confirmed?'saved':'unconfirmed',snapshot:post,
      evidence:{save_confirmed:result.confirmed,signal:result.reason,ui:result.ui,action_pattern:saveAction.pattern,completed_at:Date.now()/1000,
        save_observed_at:saveObservedAt,saved_modules:post?[post]:[]}};
  }catch(error){
    const pending=['save_click_issued','confirmation_issued'].includes(journal.save_stage)||
      !(error instanceof Deferred||error instanceof Conflict||error.settled===true);
    return {...receipt,status:pending?'unknown':'unconfirmed',settled:!pending,snapshot:null,
      evidence:{save_confirmed:false,stage:journal.save_stage,error_kind:error.name||'Error',
        error:journal.instrumentation?.findLast(e=>e.status==='failed')?.error||{code:'browser_call_failed',message:'No failed runtime call recorded'},
        signal:pending?'write_call_outcome_unknown':'readback_failed_after_click_returned'}};
  }
}

export function readScopeBindings({moduleSelector,rootFields,expectedModules}){
  const roots=[...document.querySelectorAll(moduleSelector)];
  if(roots.length!==1)return null;
  const root=roots[0],bindings=[];
  for(const m of expectedModules){
    // querySelectorAll() only searches descendants.  A single-module save
    // scope may legitimately be the reviewed module itself, so include the
    // root when it matches the module selector.
    const scopes=[...(root.matches(m.module_selector)?[root]:[]),...root.querySelectorAll(m.module_selector)];
    if(scopes.length!==1||!root.contains(scopes[0]))return null;
    for(const f of m.fields){
      const targets=[...scopes[0].querySelectorAll(f.selector)];
      if(targets.length!==1)return null;
      const ids=rootFields.filter(r=>{const matches=[...root.querySelectorAll(r.selector)];return matches.length===1&&matches[0]===targets[0];}).map(r=>r.id);
      if(ids.length!==1)return null;
      bindings.push({root_id:ids[0],module_id:m.module_id,field_id:f.id});
    }
  }
  return bindings;
}

async function saveScope(edge,tab,packet,journal,checkpoint){
  for(const expected of packet.expected_modules){
    const actual=await observe(edge,tab,{moduleId:expected.module_id,moduleSelector:expected.module_selector,capture:expected.capture});
    if(!same(actual.target,packet.target)||!same(actual.fields.map(reviewedDOMField),expected.fields.map(reviewedDOMField)))throw new Conflict('reviewed_module_changed');
  }
  const root=await observe(edge,tab,{moduleId:packet.module_id,moduleSelector:packet.module_selector,capture:packet.capture});
  if(root.fields.length!==packet.expected_modules.reduce((n,m)=>n+m.fields.length,0))throw new Conflict('unreviewed_scope_fields');
  const bindings=await tab.playwright.evaluate(readScopeBindings,{moduleSelector:packet.module_selector,
    rootFields:root.fields,expectedModules:packet.expected_modules},{timeoutMs:2000});
  if(!bindings||new Set(bindings.map(b=>b.root_id)).size!==root.fields.length)throw new Conflict('ambiguous_scope_field_binding');
  const content=f=>Object.fromEntries(Object.entries(reviewedDOMField(f)).filter(([k])=>!['id','selector'].includes(k)));
  for(const b of bindings){
    const original=packet.expected_modules.find(m=>m.module_id===b.module_id)?.fields.find(f=>f.id===b.field_id);
    const actual=root.fields.find(f=>f.id===b.root_id);
    if(!original||!actual||!same(content(original),content(actual)))throw new Conflict('reviewed_root_value_changed');
    if(original.verification_skipped)actual.verification_skipped=true;
  }
  const saveAction=classifySaveAction(root.save?.label);
  if(saveAction.pattern!=='SAVE_STAY'||!root.save?.signal)
    throw new Conflict('scope_save_semantics_unknown');
  const r=await saveModule(edge,tab,{...packet,kind:'save',save:root.save,
    expected_fields:root.fields.filter(f=>!f.protected).map(f=>({id:f.id,value:f.value,kind:f.kind,
      value_present:f.value_present,upload_ready:f.upload_ready,verification_skipped:f.verification_skipped})),
    preserved_blank_field_ids:[...(packet.preserved_blank_field_ids||[]),...bindings.filter(b=>packet.expected_modules.find(m=>m.module_id===b.module_id)?.fields.find(f=>f.id===b.field_id)?.verification_skipped).map(b=>b.root_id)]},journal,checkpoint);
  if(r.status==='saved'){
    r.evidence.saved_modules=[];
    for(const expected of packet.expected_modules){
      const post=await observe(edge,tab,{moduleId:expected.module_id,moduleSelector:expected.module_selector,capture:expected.capture});
      const signal=expected.capture?.savedSignal;
      if(!post.fields.length&&expected.fields.length&&signal?.kind==='module_readback'&&
          await confirmCard(tab,signal,expected.fields.filter(f=>!f.protected&&!f.verification_skipped))){
        post.persisted_fields=expected.fields;
        post.persistence_evidence={kind:'module_card_all_values_visible',selector:signal.selector};
      }
      r.evidence.saved_modules.push(post);
    }
  }
  return {...r,kind:'save_scope'};
}

export async function verifyAutomaticSave(edge,tab,packet,journal={},checkpoint=async()=>{}){
  const receipt={command_id:packet.command_id,kind:packet.kind,target:packet.target,settled:true,status:'unconfirmed',
    results:[],snapshot:null,evidence:{save_confirmed:false,saved_modules:[]}};
  const signal=packet.capture?.savedSignal;
  if(!signal?.selector||!signal.text||!packet.expected_modules?.length)return receipt;
  await assertTarget(edge,tab,packet.target);
  const unchanged=async()=>{
    const observed=[];
    for(const expected of packet.expected_modules){
      const current=await observe(edge,tab,{moduleId:expected.module_id,moduleSelector:expected.module_selector,capture:expected.capture});
      if(!same(current.target,expected.target)||!same(stableSavedFields(current.fields),stableSavedFields(expected.fields)))throw new Conflict('autosave_fields_changed');
      observed.push(current);
    }
    return observed;
  };
  await unchanged();
  const baseline=await readSignal(tab,signal);
  journal.autosave={baseline,stage:'waiting_fresh_signal'};await checkpoint(journal);
  const deadline=Math.min(packet.deadline*1000,Date.now()+75000);
  let latest=baseline;
  while(Date.now()<deadline){
    latest=await readSignal(tab,signal);
    if(latest&&latest!==baseline&&latest.includes(signal.text))break;
    await new Promise(resolve=>setTimeout(resolve,1000));
  }
  if(!latest||latest===baseline||!latest.includes(signal.text))return {...receipt,evidence:{...receipt.evidence,reason:'fresh_autosave_signal_missing'}};
  const savedAt=Date.now()/1000;
  await unchanged();
  journal.autosave={baseline,signal:latest,save_observed_at:savedAt,stage:'reload_issued'};await checkpoint(journal);
  await tab.reload();
  journal.autosave.stage='reload_returned';await checkpoint(journal);
  // Hydration can briefly expose an empty form. Read only, never replay a write.
  let saved, lastError;
  for(let attempt=0;attempt<12&&Date.now()<packet.deadline*1000;attempt++){
    try{saved=await unchanged();break;}catch(error){
      if(!(error instanceof Conflict)&&error.message!=='module_not_unique')throw error;
      lastError=error;await new Promise(resolve=>setTimeout(resolve,500));
    }
  }
  if(!saved)return {...receipt,evidence:{...receipt.evidence,reason:lastError?.message||'reload_readback_failed',reloaded:true,save_observed_at:savedAt,signal:latest,baseline}};
  journal.autosave.stage='reload_verified';await checkpoint(journal);
  return {...receipt,status:'saved',snapshot:saved[0],evidence:{save_confirmed:true,save_observed_at:savedAt,
    saved_modules:saved,signal:latest,baseline,reloaded:true,executor:executorName(edge),completed_at:Date.now()/1000}};
}

async function confirmModuleEditorClosed(tab,packet){
  const nativeDialog=await tab.getJsDialog?.();
  if(nativeDialog)return false;
  const controls=await tab.playwright.evaluate(readControlEvidence,
    {moduleSelector:packet.module_selector},{timeoutMs:2000});
  if(controls.dialogs.length||controls.fields.length)return false;
  const root=tab.playwright.locator(packet.module_selector);
  const editor=root.locator(packet.capture.savedSignal.editSelector);
  const edit=root.locator(packet.capture.editControl);
  if(await editor.count()!==0||await edit.count()!==1||!await edit.isVisible())return false;
  return (await edit.innerText({timeoutMs:2000})).replace(/\s/g,'')==='编辑';
}

export async function runRequest(edge,tab,runDirectory){
  const request=JSON.parse(await readFile(join(runDirectory,'request.json'),'utf8'));
  const policy=JSON.parse(await readFile(join(runDirectory,'policy.json'),'utf8'));
  const packet=request.command;
  if(request.controlRegistry)assertRegistry(request.controlRegistry);
  validatePacket(packet,policy,request.phase);
  const directory=join(runDirectory,'writer');await mkdir(directory,{recursive:true});
  const lockPath=join(directory,'active.lock'),lock=await open(lockPath,'wx');
  try{
    const lockedRequest=JSON.parse(await readFile(join(runDirectory,'request.json'),'utf8'));
    const lockedPolicy=JSON.parse(await readFile(join(runDirectory,'policy.json'),'utf8'));
    if(!same(request,lockedRequest)||!same(policy,lockedPolicy))throw new Error('request_changed_before_writer_lock');
    const journalPath=join(directory,packet.command_id+'.json');
    let old=null;
    try{old=JSON.parse(await readFile(journalPath,'utf8'));}catch(e){if(e.code!=='ENOENT')throw e;}
    if(old){
      if(old.receipt){await atomicJSON(join(runDirectory,'receipt.json'),old.receipt);return compact(old.receipt);}
      throw new Error('pending_command_requires_reconciliation');
    }
    await assertTarget(edge,tab,packet.target);
    const journal={command_id:packet.command_id,kind:packet.kind,status:'pending',started_at:Date.now()/1000};
    await atomicJSON(journalPath,journal); // Durable before ANY page action.
    tab=instrumentTab(tab,journal,j=>atomicJSON(journalPath,j),packet);
    let receipt;
    if(packet.kind==='activate_module'){
      const result=await activateModuleTab(tab,{menuSelector:packet.navigation.menu_selector,
        label:packet.navigation.label,timeoutMs:5000});
      receipt={command_id:packet.command_id,kind:packet.kind,target:packet.target,status:'completed',settled:true,
        results:[],snapshot:null,evidence:{executor:executorName(edge),activated:true,result:result.status}};
    }else if(packet.kind==='reconcile_save'){
      receipt=await reconcileSave(edge,tab,packet);
    }else if(packet.kind==='control_fill'){
      try{
        const outcome=await executeControl(tab,packet,async detail=>{journal.control=detail;await atomicJSON(journalPath,journal);});
        receipt={command_id:packet.command_id,kind:packet.kind,target:packet.target,status:outcome.verification==='mismatch'?'conflict':'completed',settled:true,results:[],snapshot:null,evidence:outcome};
      }catch(error){
        const pending=journal.instrumentation?.some(e=>e.status==='pending'||e.status==='failed');
        receipt={command_id:packet.command_id,kind:packet.kind,target:packet.target,status:pending?'unknown':'unconfirmed',settled:!pending,results:[],snapshot:null,evidence:{reason:error.message,stage:journal.control?.stage,committed:false}};
      }
    }else if(packet.kind==='add_module_record'){
      try{
        const parent=tab.playwright.locator(packet.collection_selector);
        const records=parent.locator(packet.record_selector);
        if(await parent.count()!==1)throw new Conflict('collection_changed_before_add');
        const targetCount=await tab.playwright.locator(packet.module_selector).count();
        const current=packet.inline_repeater&&targetCount===0?{fields:[]}:
          await observe(edge,tab,{moduleId:packet.module_id,moduleSelector:packet.module_selector,capture:packet.capture});
        const count=await records.count();
        if(current.fields.length){
          if(count!==packet.expected_record_count+1)throw new Conflict('open_record_count_changed');
          receipt={command_id:packet.command_id,kind:packet.kind,target:packet.target,status:'completed',settled:true,
            results:[],snapshot:current,evidence:{executor:executorName(edge),record_editor_open:true,already_open:true,
              ...(packet.reconcile_only?{original_command_id:packet.original_command_id,reconciliation_only:true}:{})}};
        }else{
          if(packet.reconcile_only)throw new Conflict('prior_added_record_not_found');
          if(count!==packet.expected_record_count)throw new Conflict('collection_changed_before_add');
          const state=await tab.playwright.evaluate(readControlEvidence,
            {moduleSelector:packet.collection_selector},{timeoutMs:2000});
          if(state.dialogs.length||(!packet.inline_repeater&&state.fields.length))throw new Conflict('editor_or_dialog_already_open');
          const addPattern=new RegExp('^\\s*'+[...packet.add_label]
            .map(char=>char.replace(/[.*+?^${}()|[\]\\]/g,'\\$&')).join('\\s*')+'\\s*$');
          const iconAdd=credentialIconAdd(packet);
          let add=parent.locator(packet.add_control_selector);
          if(!iconAdd)add=add.filter({hasText:addPattern});
          if(packet.max_records===count&&await add.count()===0){
            receipt={command_id:packet.command_id,kind:packet.kind,target:packet.target,status:'completed',settled:true,
              results:[],snapshot:null,evidence:{executor:executorName(edge),record_editor_open:false,
                capacity_reached:true,record_count:count}};
          }else{
          let reacquisition=null;
          if(packet.inline_repeater&&!iconAdd&&await add.count()===0){
            const framework=await tab.playwright.evaluate(readFrameworkSections,{}, {timeoutMs:2000});
            const selector=resolveCollectionAdd(packet,framework,count);
            if(selector){
              add=parent.locator(selector).filter({hasText:addPattern});
              reacquisition={collection_selector:packet.collection_selector,record_selector:packet.record_selector,
                expected_record_count:count,original_selector:packet.add_control_selector,selector,label:packet.add_label};
              journal.add_reacquisition=reacquisition;await atomicJSON(journalPath,journal);
            }
          }
          if(await add.count()!==1||!await add.isVisible()||
              (await add.innerText({timeoutMs:2000})).replace(/\s/g,'')!==(iconAdd?'':packet.add_label))
            throw new Conflict('add_control_changed');
          if(await records.count()!==count||await tab.playwright.locator(packet.module_selector).count()!==targetCount)
            throw new Conflict('collection_changed_during_add_reacquisition');
          actionStage(tab,'module_add');
          await add.click({timeoutMs:3000});
          // Reactive repeaters may mount the new editor after the click returns.
          // Wait for its bounded read-only count barrier; never repeat Add.
          for(let attempt=0;attempt<20;attempt++){
            const afterCount=await records.count(),afterTarget=await tab.playwright.locator(packet.module_selector).count();
            if(afterCount===packet.expected_record_count+1&&afterTarget===1)break;
            if(afterCount>packet.expected_record_count+1||afterTarget>1)throw new Conflict('collection_changed_after_add');
            if(Date.now()/1000>=packet.deadline)throw new Conflict('add_readback_deadline');
            await tab.playwright.waitForTimeout(100);
          }
          const snapshot=await observe(edge,tab,{moduleId:packet.module_id,moduleSelector:packet.module_selector,capture:packet.capture});
          if((await records.count())!==packet.expected_record_count+1||!snapshot.fields.length)
            throw new Conflict('record_editor_not_confirmed');
          receipt={command_id:packet.command_id,kind:packet.kind,target:packet.target,status:'completed',settled:true,
            results:[],snapshot,evidence:{executor:executorName(edge),record_editor_open:true,already_open:false,
              ...(reacquisition?{add_reacquisition:reacquisition}:{})}};
          }
        }
      }catch(error){
        const pending=journal.instrumentation?.some(e=>e.status==='pending'||e.status==='failed');
        receipt={command_id:packet.command_id,kind:packet.kind,target:packet.target,
          status:pending?'unknown':'unconfirmed',settled:!pending,results:[],snapshot:null,
          evidence:{record_editor_open:false,reason:error.message}};
      }
    }else if(packet.kind==='delete_module_record'){
      let destructiveIssued=false;
      try{
        const parent=tab.playwright.locator(packet.collection_selector);
        const records=parent.locator(packet.record_selector);
        const target=tab.playwright.locator(packet.module_selector);
        if(await parent.count()!==1||await records.count()!==packet.expected_record_count||await target.count()!==1)
          throw new Conflict('collection_changed_before_delete');
        const matchesIdentity=snapshot=>packet.record_identity_fields.every(expected=>
          snapshot.fields.some(field=>normalize(field.label)===normalize(expected.label)&&sameValue(field.value,expected.value)));
        let snapshot=await observe(edge,tab,{moduleId:packet.module_id,moduleSelector:packet.module_selector,capture:packet.capture});
        if(!snapshot.fields.length){
          const cardText=await target.innerText({timeoutMs:2000});
          if(!packet.record_identity_fields.every(expected=>cardText.includes(expected.value)))
            throw new Conflict('record_identity_changed_before_delete');
          const edit=target.locator(packet.edit_control_selector);
          if(await edit.count()!==1||!await edit.isVisible()||normalize(await edit.innerText({timeoutMs:2000}))!=='编辑')
            throw new Conflict('edit_control_changed_before_delete');
          actionStage(tab,'module_delete_open');
          await edit.click({timeoutMs:3000});
          snapshot=await observe(edge,tab,{moduleId:packet.module_id,moduleSelector:packet.module_selector,capture:packet.capture});
        }
        if(!snapshot.fields.length||!matchesIdentity(snapshot))
          throw new Conflict('record_identity_changed_before_delete');
        const remove=target.locator(packet.delete_control_selector).filter({hasText:packet.delete_label});
        if(await remove.count()!==1||!await remove.isVisible()||
            normalize(await remove.innerText({timeoutMs:2000}))!==packet.delete_label)
          throw new Conflict('delete_control_changed');
        actionStage(tab,'module_delete');destructiveIssued=true;
        await remove.click({timeoutMs:3000});
        const confirmPattern=new RegExp('^'+[...packet.confirm_label]
          .map(char=>char.replace(/[.*+?^${}()|[\]\\]/g,'\\$&')).join('\\s*')+'$');
        const confirm=tab.playwright.getByRole('button',{name:confirmPattern});
        if(await confirm.count()!==1||!await confirm.isVisible())throw new Conflict('delete_confirmation_changed');
        actionStage(tab,'module_delete_confirmation');
        await confirm.click({timeoutMs:3000});
        let count=await records.count();
        for(let attempt=0;attempt<20&&count!==packet.expected_record_count-1;attempt++){
          await tab.playwright.waitForTimeout(100);count=await records.count();
        }
        if(count!==packet.expected_record_count-1||await target.count()!==0)
          throw new Conflict('delete_readback_not_confirmed');
        actionStage(tab,'module_delete_reload');
        await tab.reload();
        if(packet.navigation)await activateModuleTab(tab,{menuSelector:packet.navigation.menu_selector,
          label:packet.navigation.label,timeoutMs:5000});
        const reloadedParent=tab.playwright.locator(packet.collection_selector);
        const reloadedRecords=reloadedParent.locator(packet.record_selector);
        const texts=await reloadedRecords.evaluateAll(items=>items.map(item=>item.innerText));
        if(await reloadedParent.count()!==1||await reloadedRecords.count()!==packet.expected_record_count-1||
            texts.some(text=>packet.record_identity_fields.every(expected=>text.includes(expected.value))))
          throw new Conflict('delete_reload_readback_not_confirmed');
        receipt={command_id:packet.command_id,kind:packet.kind,target:packet.target,status:'completed',settled:true,
          results:[],snapshot:null,evidence:{executor:executorName(edge),deletion_confirmed:true,reloaded:true,
            record_count_after:packet.expected_record_count-1}};
      }catch(error){
        const pending=journal.instrumentation?.some(e=>e.status==='pending'||e.status==='failed');
        receipt={command_id:packet.command_id,kind:packet.kind,target:packet.target,
          status:pending?'unknown':'unconfirmed',settled:!pending,results:[],snapshot:null,
          evidence:{deletion_confirmed:false,destructive_issued:destructiveIssued,reason:error.message}};
      }
    }else if(packet.kind==='verify_module_record_absent'){
      try{
        const verify=async()=>{
          const parent=tab.playwright.locator(packet.collection_selector);
          const records=parent.locator(packet.record_selector);
          const texts=await records.evaluateAll(items=>items.map(item=>item.innerText));
          return await parent.count()===1&&await records.count()===packet.expected_record_count&&
            !texts.some(text=>packet.record_identity_fields.every(expected=>text.includes(expected.value)));
        };
        if(!await verify())throw new Conflict('record_absence_not_confirmed');
        actionStage(tab,'module_absence_reload');await tab.reload();
        if(packet.navigation)await activateModuleTab(tab,{menuSelector:packet.navigation.menu_selector,
          label:packet.navigation.label,timeoutMs:5000});
        if(!await verify())throw new Conflict('record_absence_reload_not_confirmed');
        receipt={command_id:packet.command_id,kind:packet.kind,target:packet.target,status:'completed',settled:true,
          results:[],snapshot:null,evidence:{executor:executorName(edge),absence_confirmed:true,reloaded:true,
            record_count:packet.expected_record_count}};
      }catch(error){
        const pending=journal.instrumentation?.some(e=>e.status==='pending'||e.status==='failed');
        receipt={command_id:packet.command_id,kind:packet.kind,target:packet.target,
          status:pending?'unknown':'unconfirmed',settled:!pending,results:[],snapshot:null,
          evidence:{absence_confirmed:false,reason:error.message}};
      }
    }else if(packet.kind==='edit_module'){
      try{
        const state=await tab.playwright.evaluate(readControlEvidence,{moduleSelector:packet.module_selector},{timeoutMs:2000});
        if(state.dialogs.length)throw new Error('editor_or_dialog_already_open');
        await openModuleEditor(tab,{moduleSelector:packet.module_selector,editSelector:packet.edit_selector,timeoutMs:3000});
        const snapshot=await observe(edge,tab,{moduleId:packet.module_id,moduleSelector:packet.module_selector,capture:packet.capture});
        receipt={command_id:packet.command_id,kind:packet.kind,target:packet.target,status:snapshot.fields.length?'completed':'unconfirmed',settled:true,results:[],snapshot,evidence:{executor:executorName(edge)}};
      }catch(error){
        const pending=journal.instrumentation?.some(e=>e.status==='pending'||e.status==='failed');
        receipt={command_id:packet.command_id,kind:packet.kind,target:packet.target,status:pending?'unknown':'unconfirmed',settled:!pending,results:[],snapshot:null,evidence:{reason:error.message}};
      }
    }else if(packet.kind==='cancel_module_edit'){
      try{
        const before=await observe(edge,tab,{moduleId:packet.module_id,moduleSelector:packet.module_selector,capture:packet.capture});
        if(!same(before.target,packet.target)||!same(before.fields,packet.expected_snapshot.fields))throw new Conflict('module_changed_before_cancel');
        const preserved=new Set(packet.preserved_blank_field_ids);
        if(packet.expected_snapshot.fields.some(field=>preserved.has(field.id)&&field.value!==''&&field.value!==null))
          throw new Conflict('preserved_blank_changed_before_cancel');
        const ui=await inspectSaveUI(tab,packet.module_selector);
        if(ui.error||ui.native_dialog||ui.dialog_count||ui.loading||ui.validation_error_count!==preserved.size)
          throw new Conflict('validation_failure_changed_before_cancel');
        const root=tab.playwright.locator(packet.module_selector);
        // Element UI visually separates Chinese button characters (for example
        // `取 消`).  Match the reviewed label while allowing presentation-only
        // whitespace, then still require one visible button in this module.
        const cancelPattern=new RegExp('^'+[...packet.cancel_label]
          .map(char=>char.replace(/[.*+?^${}()|[\]\\]/g,'\\$&')).join('\\s*')+'$');
        const cancel=root.getByRole('button',{name:cancelPattern});
        if(await cancel.count()!==1||!await cancel.isVisible())throw new Conflict('cancel_control_changed');
        actionStage(tab,'module_cancel');
        await cancel.click({timeoutMs:3000});
        const closed=await confirmModuleEditorClosed(tab,packet);
        receipt={command_id:packet.command_id,kind:packet.kind,target:packet.target,
          status:closed?'completed':'unconfirmed',settled:true,results:[],snapshot:null,
          evidence:{executor:executorName(edge),editor_closed:!!closed,
                    reason:closed?'cancelled_unsaved_invalid_record':'cancel_not_confirmed'}};
      }catch(error){
        const pending=journal.instrumentation?.some(e=>e.status==='pending'||e.status==='failed');
        receipt={command_id:packet.command_id,kind:packet.kind,target:packet.target,
          status:pending?'unknown':'unconfirmed',settled:!pending,results:[],snapshot:null,
          evidence:{editor_closed:false,reason:error.message}};
      }
    }else if(packet.kind==='reconcile_module_cancel'){
      try{
        const closed=await confirmModuleEditorClosed(tab,packet);
        receipt={command_id:packet.command_id,kind:packet.kind,target:packet.target,
          status:closed?'completed':'unconfirmed',settled:true,results:[],snapshot:null,
          evidence:{executor:executorName(edge),editor_closed:!!closed,
                    signal:closed?'current_saved_card':'current_editor_or_unknown'}};
      }catch(error){
        receipt={command_id:packet.command_id,kind:packet.kind,target:packet.target,
          status:'unconfirmed',settled:true,results:[],snapshot:null,
          evidence:{editor_closed:false,reason:error.message}};
      }
    }else if(packet.kind==='observe'){
      try {
      const snapshot=await observe(edge,tab,{moduleId:packet.module_id,moduleSelector:packet.module_selector,capture:packet.capture});
      receipt={command_id:packet.command_id,kind:'observe',target:packet.target,settled:true,status:'completed',results:[],snapshot,evidence:{executor:executorName(edge)}};
      if(packet.capture?.controlDiagnostics){
        receipt.evidence.controls=await tab.playwright.evaluate(readControlEvidence,{moduleSelector:packet.module_selector,
          semanticFields:snapshot.fields.map(({id,selector,label})=>({id,selector,label}))},{timeoutMs:2000});
        receipt.evidence.module_view=await tab.playwright.evaluate(({selector})=>{
          const root=document.querySelector(selector);
          const redact=s=>String(s||'').replace(/\b\d{17}[\dXx]\b/g,'[证件已隐藏]');
          const relative=e=>{const p=[];while(e&&e!==root){const peers=[...e.parentElement.children].filter(n=>n.tagName===e.tagName);p.unshift(e.tagName.toLowerCase()+':nth-of-type('+(peers.indexOf(e)+1)+')');e=e.parentElement;}return ':scope > '+p.join(' > ');};
          return {text:redact(root?.innerText).slice(0,12000),
            edit_controls:root?[...root.querySelectorAll('*')].filter(e=>e.getClientRects().length&&(e.matches('.module-title__edit')||e.innerText?.trim()==='编辑'&&![...e.children].some(c=>c.innerText?.trim()==='编辑'))).map(e=>({label:'编辑',selector:relative(e)})):[],
            buttons:root?[...root.querySelectorAll('button,[role="button"]')].filter(e=>e.getClientRects().length).map(e=>({label:redact(e.innerText),class_name:e.className})):[]};
        },{selector:packet.module_selector},{timeoutMs:2000});
      }
      } catch(error) {
        const pending=(journal.instrumentation||[]).some(e=>e.status!=='returned');
        receipt={command_id:packet.command_id,kind:'observe',target:packet.target,
          settled:!pending,status:pending?'unknown':'unconfirmed',results:[],snapshot:null,
          evidence:{executor:executorName(edge),reason:error.message}};
      }
    }else if(packet.kind==='verify_autosave')receipt=await verifyAutomaticSave(edge,tab,packet,journal,j=>atomicJSON(journalPath,j));
    else if(packet.kind==='save_scope')receipt=await saveScope(edge,tab,packet,journal,j=>atomicJSON(journalPath,j));
    else receipt=packet.kind==='fill'?await fillBatch(edge,tab,packet,journal,j=>atomicJSON(journalPath,j)):
      await saveModule(edge,tab,packet,journal,j=>atomicJSON(journalPath,j));
    const skippedFields=packet.expected_modules?.flatMap(m=>m.fields.filter(f=>f.verification_skipped).map(f=>({module_id:m.module_id,field_id:f.id})))||
      packet.expected_fields?.filter(f=>f.verification_skipped).map(f=>({module_id:packet.module_id,field_id:f.id}))||
      receipt.results.filter(r=>r.status==='verification_skipped').map(r=>({module_id:packet.module_id,field_id:r.id}));
    if(skippedFields.length)receipt.evidence.verification_skipped_fields=skippedFields;
    receipt.evidence.controlRegistry=registryReport();
    journal.receipt=receipt;journal.status=receipt.status;await atomicJSON(journalPath,journal);
    await atomicJSON(join(runDirectory,'receipt.json'),receipt);
    return compact(receipt);
  }finally{await lock.close();await unlink(lockPath);}
}

async function reconcileSave(edge,tab,packet){
  const snapshot=await observe(edge,tab,{moduleId:packet.module_id,moduleSelector:packet.module_selector,capture:packet.capture});
  if(!same(snapshot.target,packet.target))throw new Conflict('page_identity_changed');
  let values=packet.expected_fields?.filter(f=>!f.verification_skipped)||packet.expected_modules?.flatMap(m=>m.fields.filter(f=>!f.protected))||[];
  const signal=packet.save?.signal||packet.capture?.savedSignal;
  const card=signal?.kind==='module_readback'&&(await confirmCard(tab,signal,values)||
    await confirmStructuredScope(tab,signal,packet.expected_modules));
  const matching=values.length>0&&values.every(e=>snapshot.fields.some(f=>f.id===e.id&&sameValue(f.value,e.value)));
  const receipt={command_id:packet.command_id,kind:packet.kind,target:packet.target,settled:true,
    status:card?'saved':'unconfirmed',snapshot,results:[],evidence:{save_confirmed:!!card,signal:card?'current_saved_card':'current_draft_only'}};
  if(!card&&signal?.kind==='module_readback'&&packet.expected_modules?.length===1){
    const expected=packet.expected_modules[0];
    let preview=await tab.playwright.evaluate(readElementPreviewCard,{selector:expected.module_selector,fields:expected.fields});
    let previewKind='element_preview_card_visible';
    if(!preview){preview=await tab.playwright.evaluate(readProjectPreviewCard,{selector:expected.module_selector,fields:expected.fields});previewKind='sf_project_preview_card_visible';}
    if(!preview){preview=await tab.playwright.evaluate(readSfEducationPreviewCard,{selector:expected.module_selector,fields:expected.fields});previewKind='sf_education_preview_card_visible';}
    if(preview){
      receipt.status='saved';
      receipt.evidence={save_confirmed:true,signal:previewKind,preview,
        save_observed_at:Date.now()/1000,learning_blocked:'partial_card_projection_readback',
        verification_skipped_fields:preview.partial_field_ids.map(field_id=>({module_id:expected.module_id,field_id}))};
      return receipt;
    }
  }
  if(!card && matching && edge.backend==='playwright' &&
      packet.target.url==='https://campus-talent.alibaba.com/personal/resume' &&
      values.some(f=>f.label==='学校全称'&&f.value)) {
    let persisted=null;
    try { persisted=await tab.playwright.evaluate(readAlibabaEducationPersistence); }
    catch { receipt.evidence.persistence_readback_unavailable=true; }
    if(confirmsAbsentEducation(packet,persisted))receipt.evidence={save_confirmed:false,
      signal:'persisted_scope_absent',persisted,original_command_id:packet.original_command_id,
      current_draft_matches:true};
  }
  if(card){
    receipt.evidence.save_observed_at=Date.now()/1000;
    receipt.evidence.saved_modules=[];
    for(const expected of packet.expected_modules||[snapshot]){
      receipt.evidence.saved_modules.push(await observe(edge,tab,{moduleId:expected.module_id,moduleSelector:expected.module_selector,capture:expected.capture}));
    }
  }
  if(packet.original_journal&&packet.user_basis&&(matching||card)){
    const old=JSON.parse(await readFile(packet.original_journal,'utf8'));
    if(old.command_id!==packet.original_command_id||!same(old.receipt,packet.original_receipt)||old.kind!=='save')
      throw new Conflict('legacy_journal_changed');
    const resolution={original_command_id:old.command_id,original_receipt:old.receipt,target:packet.target,
      status:'superseded_by_user',user_basis:packet.user_basis,current_values_match:true,
      current_state:card?'saved_card_observed':'matching_editor_values_only',
      executor:executorName(edge),observed_at:snapshot.observed_at,snapshot_id:snapshot.snapshot_id,
      note:'Original automated save and remote termination are not asserted; user intervention supersedes its workflow.'};
    const dir=join(dirname(dirname(packet.original_journal)),'resolutions');await mkdir(dir,{recursive:true});
    const h=await open(join(dir,old.command_id+'.json'),'wx');
    try{await h.writeFile(JSON.stringify(resolution,null,2));await h.sync();}finally{await h.close();}
    receipt.status='superseded_by_user';
  }
  return receipt;
}

export function compact(receipt){return {command_id:receipt.command_id,kind:receipt.kind,status:receipt.status,
  settled:receipt.settled,counts:receipt.results.reduce((a,r)=>(a[r.status]=(a[r.status]||0)+1,a),{}),
  save_confirmed:receipt.evidence?.save_confirmed===true};}
