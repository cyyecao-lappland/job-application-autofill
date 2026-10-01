/** Deterministic same-page module navigation for tab/menu based applications. */

export function readModuleTabs({menuSelector}){
  const visible=e=>!!(e.getClientRects().length&&getComputedStyle(e).visibility!=='hidden');
  return [...document.querySelectorAll(menuSelector)].filter(visible).map((e,index)=>{
    const label=(e.textContent||'').normalize('NFKC').trim().replace(/\s+/g,' ');
    const canonical_label=label.replace(/^\*\s*/,'').replace(/\s+(?:未完成|已完成)$/,'').trim();
    return {
    index,label,canonical_label,
    active:e.classList.contains('resume-menu-item--active')||e.getAttribute('aria-current')==='page'||e.getAttribute('aria-selected')==='true',
    disabled:!!e.disabled||e.getAttribute('aria-disabled')==='true'
  };});
}

export async function listModuleTabs(tab,{menuSelector,timeoutMs=3000}){
  if(!menuSelector)throw new TypeError('menu_selector_required');
  return tab.playwright.evaluate(readModuleTabs,{menuSelector},{timeoutMs});
}

export async function activateModuleTab(tab,{menuSelector,label,timeoutMs=5000}){
  const end=Date.now()+timeoutMs;
  let before,matches;
  // A full-page reload can finish before a client-rendered menu is mounted.
  // Treat zero matches as transient within the bounded navigation window, but
  // still fail closed immediately for duplicate or disabled labels.
  do{
    before=await listModuleTabs(tab,{menuSelector,timeoutMs:Math.max(1,end-Date.now())});
    matches=before.filter(item=>item.canonical_label===label||item.label===label);
    if(matches.length>1||(matches.length===1&&matches[0].disabled))
      throw new Error('module_tab_not_unique_or_disabled');
    if(matches.length===1)break;
    if(Date.now()>=end)throw new Error('module_tab_not_unique_or_disabled');
    await tab.playwright.waitForTimeout(100);
  }while(true);
  if(matches[0].active)return {status:'already_active',label};
  const control=tab.playwright.locator(menuSelector).getByText(label,{exact:true});
  if(await control.count()!==1||!await control.isVisible())throw new Error('module_tab_control_changed');
  await control.click({timeoutMs});
  while(Date.now()<end){
    const after=await listModuleTabs(tab,{menuSelector,timeoutMs:Math.max(1,end-Date.now())});
    const active=after.filter(item=>item.active);
    if(active.length===1&&(active[0].canonical_label===label||active[0].label===label))return {status:'activated',label};
    await new Promise(resolve=>setTimeout(resolve,100));
  }
  throw new Error('module_tab_activation_unconfirmed');
}

export async function openModuleEditor(tab,{moduleSelector,editSelector,timeoutMs=5000}){
  if(!moduleSelector||!editSelector)throw new TypeError('module_edit_selectors_required');
  const root=tab.playwright.locator(moduleSelector);
  if(await root.count()!==1||!await root.isVisible())throw new Error('module_not_unique_or_visible');
  const existing=root.locator('input:not([type="file"]):not([type="hidden"]),textarea,select,[role="combobox"]');
  if(await existing.count()>0)return {status:'already_open'};
  const edit=root.locator(editSelector);
  if(await edit.count()!==1||!await edit.isVisible()||((await edit.innerText({timeoutMs:1000})).trim()!=='编辑'&&!(await edit.getAttribute('class')||'').split(/\s+/).includes('module-title__edit')))
    throw new Error('module_edit_control_changed');
  await edit.click({timeoutMs});
  const end=Date.now()+timeoutMs;
  while(Date.now()<end){
    if(await root.locator('input,textarea,select,[role="combobox"]').count()>0)return {status:'opened'};
    await new Promise(resolve=>setTimeout(resolve,100));
  }
  throw new Error('module_editor_open_unconfirmed');
}
