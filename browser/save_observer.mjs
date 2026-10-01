export function classifySaveDialog(text,buttons=[]){
  const body=String(text||'').normalize('NFKC').trim().replace(/\s+/g,' ');
  const actions=buttons.map(v=>String(v).normalize('NFKC').trim());
  if(/验证码|人脸|滑块|captcha/i.test(body))return 'CAPTCHA';
  if(/登录失效|重新登录|登录已过期|session.*expired/i.test(body))return 'LOGIN_EXPIRED';
  if(/未保存|放弃修改|离开.*页面/.test(body))return 'UNSAVED_WARNING';
  if(/冲突|已被修改|版本.*变化|覆盖/.test(body))return 'CONFLICT';
  if(/必填|不能为空|请填写|格式.*错误/.test(body))return 'VALIDATION_ERROR';
  if(/保存成功|修改成功|success/i.test(body))return 'SAVE_SUCCESS';
  if(/(?:确认|是否).*(?:保存|修改)|(?:保存|修改).*(?:确认|是否)/.test(body)&&actions.some(v=>/保存|确认/.test(v)))return 'SAVE_CONFIRM';
  return 'UNKNOWN_DIALOG';
}

// Read-only visible UI sampling. No listeners, page state or network inspection.
export function readSaveUI({moduleSelector}) {
  const visible=e=>!!(e.getClientRects().length&&getComputedStyle(e).visibility!=='hidden');
  const roots=[...document.querySelectorAll(moduleSelector)];
  if(roots.length!==1)return {error:'module_not_unique'};
  const root=roots[0];
  // Classify inside the page and return no dialog body: it may contain identity values.
  const dialogs=[...document.querySelectorAll('[role="dialog"],.el-message-box,.el-dialog')].filter(visible);
  const errors=[...root.querySelectorAll('.el-form-item__error,[role="alert"],.error-message,.next-form-item.has-error .next-form-item-help')].filter(visible);
  // Fusion renders cross-record validation in a portal toast, outside the
  // owning form. Return a category only; toast bodies can contain user data.
  const portalValidation=[...document.querySelectorAll('.next-message-error,.next-message-warning')]
    .filter(visible).filter(e=>/必填|不能为空|请填写|请补充|格式.*错误/.test(e.innerText||''));
  const dialog_signatures=dialogs.map(d=>{
    const body=(d.innerText||'').normalize('NFKC').trim().replace(/\s+/g,' ');
    const buttons=[...d.querySelectorAll('button,[role="button"]')].filter(visible).map(b=>(b.innerText||'').normalize('NFKC').trim()).filter(Boolean);
    const kind=/验证码|人脸|滑块|captcha/i.test(body)?'CAPTCHA':
      /登录失效|重新登录|登录已过期|session.*expired/i.test(body)?'LOGIN_EXPIRED':
      /未保存|放弃修改|离开.*页面/.test(body)?'UNSAVED_WARNING':
      /冲突|已被修改|版本.*变化|覆盖/.test(body)?'CONFLICT':
      /必填|不能为空|请填写|格式.*错误/.test(body)?'VALIDATION_ERROR':
      /保存成功|修改成功|success/i.test(body)?'SAVE_SUCCESS':
      /(?:确认|是否).*(?:保存|修改)|(?:保存|修改).*(?:确认|是否)/.test(body)&&buttons.some(v=>/保存|确认/.test(v))?'SAVE_CONFIRM':'UNKNOWN_DIALOG';
    return {kind,buttons};
  });
  return {dialog_count:dialogs.length,validation_error_count:errors.length+portalValidation.length,
    dialogs:dialog_signatures,
    loading:!![...root.querySelectorAll('[aria-busy="true"],.el-loading-mask')].find(visible)};
}

export async function inspectSaveUI(tab,moduleSelector) {
  const dialog=typeof tab.getJsDialog==='function'?await tab.getJsDialog():null;
  if(dialog)return {native_dialog:dialog.type}; // No documented message text: never auto-accept.
  return await tab.playwright.evaluate(readSaveUI,{moduleSelector},{timeoutMs:2000});
}

// Read-only fallback for repeatable editors that collapse into sibling cards.
// The original editor selector can point at a different record after insertion,
// so require one sibling with the same element/class shape and every reviewed
// nonempty string.  This never clicks, navigates or accepts a dialog.
export function readSiblingCardMatch({selector,editSelector,values}){
  const roots=[...document.querySelectorAll(selector)];
  if(roots.length!==1)return 0;
  const anchor=roots[0],parent=anchor.parentElement;
  if(!parent)return 0;
  const visible=e=>!!(e.getClientRects().length&&getComputedStyle(e).visibility!=='hidden');
  const norm=s=>String(s||'').normalize('NFKC').trim().replace(/\s+/g,' ');
  const wanted=values.filter(v=>typeof v==='string'&&v.trim()).map(norm);
  if(!wanted.length)return 0;
  return [...parent.children].filter(e=>e.tagName===anchor.tagName&&String(e.className)===String(anchor.className)&&visible(e))
    .filter(e=>{
      const editor=e.querySelector(editSelector);
      if(editor&&visible(editor))return false;
      const body=norm(e.innerText);
      return wanted.every(v=>body.includes(v));
    }).length;
}

export async function confirmCard(tab,signal,values) {
  if(!signal?.selector||!signal.editSelector)return false;
  const fields=values.every(v=>v&&typeof v==='object')?values:null;
  const expectedFiles=fields?fields.filter(f=>f.kind==='file'&&f.value_present&&f.upload_ready!==false).length:0;
  values=fields?fields.filter(f=>f.kind!=='file').map(f=>f.value):values;
  if(values.some(v=>typeof v!=='string'))return false;
  const norm=s=>s.normalize('NFKC').trim().replace(/\s+/g,' ');
  const visibleValues=values.filter(v=>v.trim());
  if(!visibleValues.length&&!expectedFiles)return false;
  const root=tab.playwright.locator(signal.selector);
  if(await root.count()!==1||!await root.isVisible())return false;
  const editor=root.locator(signal.editSelector);
  const editors=await editor.count();
  // Fusion keeps the form node after saving, switching it to a preview card.
  // Require the actual preview class and absence of editing controls as well as values.
  const preview=editors===1&&typeof editor.getAttribute==='function'&&
    (await editor.getAttribute('class')||'').split(/\s+/).includes('next-form-preview')&&
    await editor.locator('input:not([type="hidden"]),textarea,select,[contenteditable="true"]').count()===0;
  if(editors<=1&&(editors===0||!await editor.isVisible()||preview)){
    const text=norm(await root.innerText({timeoutMs:1000}));
    const readyFiles=expectedFiles?await root.locator('.uploader-img img[src],.el-upload-list__item.is-success').count():0;
    if(visibleValues.every(v=>text.includes(norm(v)))&&readyFiles>=expectedFiles)return true;
  }
  // Empty optional strings are represented by site-specific placeholders such
  // as "--" and have no canonical value to find in the card. Verify every
  // nonempty string; booleans/hidden values still cannot be proven by text.
  if(expectedFiles)return false;
  return await tab.playwright.evaluate(readSiblingCardMatch,{selector:signal.selector,
    editSelector:signal.editSelector,values})===1;
}

// A shared card may render each record as a preview while the outer form keeps
// its original class. Bind every reviewed record independently; a value found
// in a different record must not satisfy this record's persistence check.
export function readStructuredScopePreview({selector,modules}) {
  const visible=e=>!!(e.getClientRects().length&&getComputedStyle(e).visibility!=='hidden');
  const norm=v=>String(v??'').normalize('NFKC').replace(/\s+/g,' ').trim();
  const roots=[...document.querySelectorAll(selector)];
  if(roots.length!==1||!visible(roots[0]))return false;
  const root=roots[0];
  if([...root.querySelectorAll('input:not([type="hidden"]),textarea,select,[contenteditable="true"]')].some(visible))return false;
  const buttons=[...root.querySelectorAll('button,[role="button"],[class*="PCButton--pcButton--"]')].filter(visible);
  if(!buttons.some(b=>norm(b.textContent)==='编辑')||buttons.some(b=>norm(b.textContent)==='保存'))return false;
  return modules.length>0&&modules.every(module=>{
    const nodes=[...document.querySelectorAll(module.module_selector)];
    if(nodes.length!==1||!root.contains(nodes[0]))return false;
    const node=nodes[0];
    if(!node.matches('.next-form-preview')&&!node.querySelector('.next-form-preview'))return false;
    return module.fields.filter(f=>!f.protected).every(f=>{
      if(f.kind==='file')return false;
      if(f.kind==='checkbox'){
        if(f.label!=='至今'||typeof f.value!=='boolean')return false;
        const displays=[...node.querySelectorAll('.to-present-checkbox [behavior="READONLY"]')];
        const ranges=[...node.querySelectorAll('.deep-range-picker.next-form-preview')];
        if(displays.length===1&&ranges.length===1){
          const end=norm(ranges[0].textContent).split(' - ')[1];
          return f.value?end==='至今':/^\d{4}-\d{2}$/.test(end||'');
        }
        return displays.length===1&&(norm(displays[0].textContent)==='至今')===f.value;
      }
      if(typeof f.value!=='string')return false;
      if(f.value==='')return true;
      const exact=f.selector?[...node.querySelectorAll(f.selector)]:[];
      if(exact.length===1&&exact[0].getAttribute('behavior')==='READONLY')return norm(exact[0].innerText??exact[0].textContent)===norm(f.value);
      if(f.control_pattern==='next_range_date'){
        const ranges=[...node.querySelectorAll('.deep-range-picker.next-form-preview')];
        if(ranges.length!==1)return false;
        const values=norm(ranges[0].textContent).split(' - ');
        return values[f.range_endpoint==='start'?0:1]===norm(f.value);
      }
      return false;
    });
  });
}

export async function confirmStructuredScope(tab,signal,modules){
  if(!signal?.selector||!modules?.length)return false;
  return await tab.playwright.evaluate(readStructuredScopePreview,{selector:signal.selector,modules})===true;
}

// Element preview cards can project a province/city control to its city label.
// Report those fields as partial readback; never manufacture the hidden province.
export function readElementPreviewCard({selector,fields}){
  const visible=e=>!!e.getClientRects().length&&getComputedStyle(e).visibility!=='hidden';
  const norm=s=>String(s??'').normalize('NFKC').trim().replace(/\s+/g,' ');
  const roots=[...document.querySelectorAll(selector)];if(roots.length!==1)return null;
  const root=roots[0],forms=[...root.querySelectorAll('form.resume-noedit-form')].filter(visible);
  if(forms.length!==1||[...root.querySelectorAll('input:not([type="hidden"]),textarea,select')].some(visible))return null;
  const edits=[...root.querySelectorAll('a.corner-btn')].filter(e=>visible(e)&&norm(e.innerText)==='编辑');
  if(edits.length!==1)return null;
  const rows=[...forms[0].querySelectorAll('.el-form-item')].map(e=>({
    label:norm(e.querySelector('.el-form-item__label')?.textContent).replace(/[：:]$/,''),
    value:norm(e.querySelector('.el-form-item__content')?.textContent)}));
  const actual=[],partial=[];
  for(const f of fields){
    if(f.kind==='file'){if(f.value_present||f.required)return null;continue;}
    if(typeof f.value!=='string')return null;
    const matches=rows.filter(r=>r.label===norm(f.label).replace(/[：:]$/,''));
    if(!f.value&&!f.required&&matches.length===0)continue;
    if(matches.length!==1)return null;
    const value=matches[0].value;
    if(value!==norm(f.value)){
      const parts=String(f.value).split('-');
      if(f.verified_control?.adapter!=='province_city_dialog_v1'||f.verified_control.actual!==f.value||
         parts.length!==2||value!==norm(parts[1]))return null;
      partial.push(f.id);
    }
    actual.push({field_id:f.id,value});
  }
  return actual.length?{actual,partial_field_ids:partial}:null;
}

// SF project editors collapse to a DIV card whose date range exposes months only.
// Bind a unique card and named values; retain the actual display precision.
export function readProjectPreviewCard({selector,fields}){
  const visible=e=>!!e.getClientRects().length&&getComputedStyle(e).visibility!=='hidden';
  const norm=s=>String(s??'').normalize('NFKC').trim().replace(/\s+/g,' ');
  const roots=[...document.querySelectorAll(selector)];
  if(roots.length!==1||!visible(roots[0]))return null;
  const root=roots[0];
  if(root.id!=='projectList'||[...root.querySelectorAll('input:not([type="hidden"]),textarea,select,[contenteditable="true"],[role="combobox"]')].some(visible))return null;
  const cards=[...root.querySelectorAll('div.resume-noedit-form')].filter(visible);
  if(cards.length!==1)return null;
  const card=cards[0],edits=[...card.querySelectorAll('a.corner-btn')].filter(e=>visible(e)&&norm(e.textContent)==='编辑');
  if(edits.length!==1)return null;
  const one=s=>{const nodes=[...card.querySelectorAll(s)].filter(visible);return nodes.length===1?norm(nodes[0].textContent):null;};
  const range=one('.ft3')?.match(/^(\d{4})\.(\d{2})\s*-\s*(\d{4})\.(\d{2})$/);
  if(!range)return null;
  const rows=[...card.querySelectorAll('.content-with-label')].filter(visible).map(e=>({
    label:norm(e.querySelector('.label')?.textContent).replace(/[：:]$/,''),
    value:norm(e.querySelector('.content')?.textContent)}));
  const named=label=>{const matches=rows.filter(r=>r.label===label);return matches.length===1?matches[0].value:null;};
  const shown={'项目名称':one('.ft1'),'职务':one('.ft2'),'开始时间':range[1]+'-'+range[2],
    '结束时间':range[3]+'-'+range[4],'项目职责':named('项目职责'),'项目简述':named('项目描述')};
  const actual=[],partial=[];
  if(!fields.some(f=>f.label==='项目名称'&&f.value)||!fields.length)return null;
  for(const f of fields){
    const value=shown[norm(f.label).replace(/[：:]$/,'')];
    if(typeof f.value!=='string'||value==null)return null;
    if(value!==norm(f.value)){
      if(!['开始时间','结束时间'].includes(f.label)||!/^\d{4}-\d{2}-\d{2}$/.test(f.value)||f.value.slice(0,7)!==value)return null;
      partial.push(f.id);
    }
    actual.push({field_id:f.id,value});
  }
  return {actual,partial_field_ids:partial};
}

// Existing SF education records keep the info-list-item ancestor after saving.
// Only named visible values prove persistence. Hidden boolean answers stay skipped.
export function readSfEducationPreviewCard({selector,fields}){
  const visible=e=>!!e.getClientRects().length&&getComputedStyle(e).visibility!=='hidden';
  const norm=s=>String(s??'').normalize('NFKC').trim().replace(/\s+/g,' ');
  const roots=[...document.querySelectorAll(selector)];
  if(roots.length!==1||!visible(roots[0]))return null;
  const root=roots[0];
  if(!root.matches('.info-list-item')||!root.closest('#educationList')||
    [...root.querySelectorAll('input:not([type="hidden"]):not([type="file"]),textarea,select,[role="combobox"]')].some(visible))return null;
  const cards=[...root.querySelectorAll('.resume-noedit-form')].filter(visible);
  if(cards.length!==1)return null;
  const card=cards[0],edits=[...card.querySelectorAll('.corner-btn')].filter(e=>visible(e)&&norm(e.textContent)==='编辑');
  if(edits.length!==1)return null;
  const rows=[...card.querySelectorAll('.flex-wrapper')].filter(visible).map(e=>({
    label:norm(e.querySelector('.label')?.textContent).replace(/[：:]$/,''),
    value:norm(e.querySelector('.content')?.textContent)}));
  const actual=[],partial=[];
  for(const label of ['学校全称','学历','专业名称']){
    if(!fields.some(f=>f.label===label&&f.value)||rows.filter(r=>r.label===label).length!==1)return null;
  }
  for(const f of fields){
    if(f.kind==='file'){
      const names=[...card.querySelectorAll('.grade-prove-view__text')].filter(visible).map(e=>norm(e.textContent));
      if(f.required&&!f.value_present||f.value_present&&f.upload_ready===false||!f.value_present&&names.length)return null;
      if(f.value_present&&(names.length!==1||names[0]!==norm(f.value)))return null;
      if(f.value_present)actual.push({field_id:f.id,value:names[0]});
      continue;
    }
    const matches=rows.filter(r=>r.label===norm(f.label).replace(/[：:]$/,''));
    if(!matches.length&&['是否专升本','是否第二学位'].includes(f.label)){
      partial.push(f.id);continue;
    }
    if(matches.length!==1||typeof f.value!=='string'||matches[0].value!==norm(f.value))return null;
    actual.push({field_id:f.id,value:matches[0].value});
  }
  return actual.length?{actual,partial_field_ids:partial}:null;
}

export async function awaitSaveOutcome(tab,packet,stage,{timeoutMs=5000}={}) {
  const end=Math.min(packet.deadline*1000,Date.now()+timeoutMs);
  let confirmationClicked=false,last={};
  do {
    last=await inspectSaveUI(tab,packet.module_selector);
    if(last.native_dialog)return {confirmed:false,reason:'native_dialog_requires_review',ui:last};
    if(last.error)return {confirmed:false,reason:last.error};
    if(last.validation_error_count)return {confirmed:false,reason:'validation_failed',ui:last};
    if(last.dialog_count){
      const dialogKind=last.dialogs?.[0]?.kind;
      if(['CAPTCHA','LOGIN_EXPIRED','UNSAVED_WARNING','CONFLICT','VALIDATION_ERROR'].includes(dialogKind))
        return {confirmed:false,reason:'save_dialog_'+dialogKind.toLowerCase(),ui:last};
      const c=packet.capture?.saveConfirmation;
      // Only a site-observed exact ordinary save confirmation, never generic OK.
      if(!c||confirmationClicked||!/^确认保存(?:修改)?[？?]?$/.test(c.text)||!['保存','确认保存'].includes(c.buttonText))
        return {confirmed:false,reason:'dom_dialog_requires_review',ui:last};
      const d=tab.playwright.locator(c.selector);
      const body=c.bodySelector?d.locator(c.bodySelector):d;
      if(await d.count()!==1||!await d.isVisible()||await body.count()!==1||await body.innerText({timeoutMs:1000})!==c.text)
        return {confirmed:false,reason:'confirmation_changed',ui:last};
      const b=d.locator(c.buttonSelector);
      if(await b.count()!==1||await b.innerText({timeoutMs:1000})!==c.buttonText)
        return {confirmed:false,reason:'confirmation_button_changed',ui:last};
      await stage('confirmation_issued');
      await b.click({timeoutMs:Math.max(1,Math.min(2000,end-Date.now()))});
      await stage('confirmation_returned');confirmationClicked=true;
    }else if(!last.loading){
      const s=packet.save.signal;
      if(s.kind==='module_readback'){
        if(await confirmCard(tab,s,packet.expected_fields)||await confirmStructuredScope(tab,s,packet.expected_modules))return {confirmed:true,reason:'editor_closed_and_all_values_visible'};
      }else{
        const l=tab.playwright.locator(s.selector);
        if(await l.count()===1&&await l.isVisible()&&await l.innerText({timeoutMs:1000})===s.text)
          return {confirmed:true,reason:'fresh_save_indicator'};
      }
    }
    if(Date.now()<end)await new Promise(r=>setTimeout(r,100));
  }while(Date.now()<end);
  return {confirmed:false,reason:'save_observation_timeout',ui:last};
}
