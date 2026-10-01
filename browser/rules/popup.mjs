export function readPopupDOM({moduleSelector,selector,baseline=null,captureBaseline=false,resumeExisting=false}){
  const visible=el=>!!(el.getClientRects().length&&getComputedStyle(el).visibility!=='hidden');
  const roots=[...document.querySelectorAll(moduleSelector)];
  if(roots.length!==1)return {error:'module_not_unique'};
  const fields=[...roots[0].querySelectorAll(selector)];
  if(fields.length!==1)return {error:'field_not_unique'};
  const field=fields[0];
  const nextOwner=field.closest('.next-select');
  if(nextOwner?.classList?.contains('next-select')){
    const path=el=>{const parts=[];while(el&&el!==document.documentElement){const siblings=[...el.parentElement.children].filter(n=>n.tagName===el.tagName);parts.unshift(el.tagName.toLowerCase()+':nth-of-type('+(siblings.indexOf(el)+1)+')');el=el.parentElement;}return 'html > '+parts.join(' > ');};
    if(!nextOwner.contains(document.activeElement))return {error:'popup_focus_changed'};
    const ownerRect=nextOwner.getBoundingClientRect();
    const menus=[...document.querySelectorAll('.next-select-menu,[role="listbox"]')].filter(menu=>{
      if(!visible(menu)||menu.closest('.next-nav,nav,[role="navigation"]'))return false;
      const rect=menu.getBoundingClientRect();
      const overlap=Math.max(0,Math.min(ownerRect.right,rect.right)-Math.max(ownerRect.left,rect.left));
      return overlap/Math.max(1,Math.min(ownerRect.width,rect.width))>=0.8&&
        Math.min(Math.abs(rect.top-ownerRect.bottom),Math.abs(ownerRect.top-rect.bottom))<=40;
    });
    if(menus.length!==1)return {error:menus.length?'popup_ambiguous':'popup_not_unique_or_not_loaded'};
    const menu=menus[0];
    return {selector:path(menu),ownership:'focused_aligned_next_menu',search:field.readOnly?null:{location:'field'},
      busy:menu.getAttribute('aria-busy')==='true',multiple:nextOwner.classList.contains('next-select-multiple'),
      options:[...menu.querySelectorAll('[role="option"],.next-menu-item')].filter(visible).map(el=>({
        label:(el.getAttribute('title')||el.textContent||'').trim(),selector:path(el),
        disabled:el.getAttribute('aria-disabled')==='true'||el.classList.contains('next-disabled'),
        selected:el.getAttribute('aria-selected')==='true'||el.classList.contains('next-selected')}))};
  }
  // Element UI v2 keeps its option menu under the owning select before teleport.
  // Use structural DOM identity, not model-created selectors or global first-match.
  const owner=field.closest('.el-select');
  if(owner){
    const path=el=>{const parts=[];while(el&&el!==document.documentElement){const siblings=[...el.parentElement.children].filter(n=>n.tagName===el.tagName);parts.unshift(el.tagName.toLowerCase()+':nth-of-type('+(siblings.indexOf(el)+1)+')');el=el.parentElement;}return 'html > '+parts.join(' > ');};
    const labels=m=>[...m.querySelectorAll('.el-select-dropdown__item')].map(e=>(e.textContent||'').trim());
    const all=[...document.querySelectorAll('.el-select-dropdown')];
    const local=[...owner.querySelectorAll('.el-select-dropdown')];
    if(captureBaseline)return {visible:all.filter(visible).map(path),
      menus:all.map(m=>({selector:path(m),labels:labels(m),owned:owner.contains(m)}))};
    let menus=local.filter(visible),ownership='local';
    if(!menus.length&&baseline){
      // A pre-existing open menu is not evidence of this field's ownership.
      if(baseline.visible.length&&!resumeExisting)return {error:'preexisting_popup'};
      menus=all.filter(m=>visible(m)&&(resumeExisting||!baseline.visible.includes(path(m))));
      if(menus.length>1)return {error:'popup_ambiguous'};
      if(menus.length===1){
        if(!owner.contains(document.activeElement))return {error:'popup_focus_changed'};
        const signature=JSON.stringify(labels(menus[0]));
        // Element UI 2 teleports one dropdown per select into <body> and keeps
        // hidden dropdowns from sibling controls.  Repeated option sets (for
        // example two acceptance questions) therefore cannot be attributed by
        // label uniqueness.  Focus plus geometric popper alignment establishes
        // ownership without selecting from an unrelated global popup.
        const ownerRect=owner.getBoundingClientRect(),menuRect=menus[0].getBoundingClientRect();
        const overlap=Math.max(0,Math.min(ownerRect.right,menuRect.right)-Math.max(ownerRect.left,menuRect.left));
        const horizontalRatio=overlap/Math.max(1,Math.min(ownerRect.width,menuRect.width));
        const edgeGap=Math.min(Math.abs(menuRect.top-ownerRect.bottom),Math.abs(ownerRect.top-menuRect.bottom));
        // A remote-search dropdown can be empty before typing. Its focused,
        // editable owner and unique aligned menu permit a query, never an
        // option selection without the later exact candidate check.
        const searchable=field.tagName==='INPUT'&&!field.readOnly&&!field.disabled;
        if(!signature||signature==='[]'&&!searchable||horizontalRatio<0.8||edgeGap>40)
          return {error:'popup_ownership_unconfirmed'};
        ownership=resumeExisting?'focused_existing_matching_menu':'new_visible_matching_menu';
      }
    }
    if(menus.length!==1)return {error:menus.length>1?'popup_ambiguous':'popup_not_unique_or_not_loaded'};
    const menu=menus[0];
    return {selector:path(menu),ownership,search:!field.readOnly?{location:'field'}:null,busy:false,multiple:false,
      options:[...menu.querySelectorAll('.el-select-dropdown__item')].filter(visible).map(el=>({label:(el.textContent||'').trim(),selector:path(el),
        selected:el.classList.contains('selected'),disabled:el.classList.contains('is-disabled')}))};
  }
  const ids=(field.getAttribute('aria-controls')||field.getAttribute('aria-owns')||'').split(/\s+/).filter(Boolean);
  let menus=ids.map(id=>document.getElementById(id)).filter(el=>el&&visible(el));
  if(!menus.length&&!ids.length)menus=[...document.querySelectorAll('[role="listbox"]')].filter(visible);
  if(menus.length!==1)return {error:'popup_not_unique_or_not_loaded',expanded:field.getAttribute('aria-expanded')};
  const menu=menus[0];
  const selectorFor=el=>el.id?'#'+CSS.escape(el.id):null;
  const menuSelector=selectorFor(menu);
  if(!menuSelector || document.querySelectorAll(menuSelector).length!==1)return {error:'popup_needs_stable_identity'};
  const inputs=[...menu.querySelectorAll('input:not([type="hidden"])')].filter(el=>visible(el)&&!el.readOnly&&!el.disabled);
  let search=null;
  if(field.tagName==='INPUT'&&!field.readOnly&&!field.disabled)search={location:'field'};
  else if(inputs.length===1&&selectorFor(inputs[0]))search={location:'popup',selector:selectorFor(inputs[0])};
  else {
    const inner=[...field.querySelectorAll('input')].filter(el=>visible(el)&&!el.readOnly&&!el.disabled);
    if(inner.length===1)search={location:'inside',selector:'input'};
  }
  const options=[...menu.querySelectorAll('[role="option"]')].filter(visible).map(el=>({
    label:(el.getAttribute('aria-label')||el.textContent||'').trim(),selected:el.getAttribute('aria-selected')==='true',
    disabled:el.getAttribute('aria-disabled')==='true'||!!el.disabled}));
  return {selector:menuSelector,search,options,busy:menu.getAttribute('aria-busy')==='true',
    multiple:menu.getAttribute('aria-multiselectable')==='true'};
}

export function readTagSelectionDOM({moduleSelector,selector}){
  const roots=[...document.querySelectorAll(moduleSelector)];
  if(roots.length!==1)return {error:'module_not_unique'};
  const fields=[...roots[0].querySelectorAll(selector)];
  if(fields.length!==1)return {error:'field_not_unique'};
  const field=fields[0],owner=field.closest('.next-select-tag');
  if(!owner)return {error:'tag_structure_changed'};
  const path=el=>{const parts=[];while(el&&el!==document.documentElement){const peers=[...el.parentElement.children].filter(n=>n.tagName===el.tagName);parts.unshift(el.tagName.toLowerCase()+':nth-of-type('+(peers.indexOf(el)+1)+')');el=el.parentElement;}return 'html > '+parts.join(' > ');};
  return {expanded:field.getAttribute('aria-expanded'),tags:[...owner.querySelectorAll('.next-select-values .next-tag')].map(e=>({
    label:e.querySelector('.next-tag-body')?.textContent?.trim(),
    remove:e.querySelector('.next-tag-close-btn[aria-label="删除"]')?path(e.querySelector('.next-tag-close-btn[aria-label="删除"]')):null}))};
}

export function readComboCommitDOM({moduleSelector,selector,menuSelector,value}){
  const roots=[...document.querySelectorAll(moduleSelector)];
  if(roots.length!==1)return false;
  const fields=[...roots[0].querySelectorAll(selector)],menus=[...document.querySelectorAll(menuSelector)];
  if(fields.length!==1||menus.length!==1)return false;
  const menu=menus[0],visible=!!(menu.getClientRects().length&&getComputedStyle(menu).visibility!=='hidden');
  const selected=[...menu.querySelectorAll('.el-select-dropdown__item.selected')];
  const field=fields[0];
  const displayed=field.value||((field.readOnly||document.activeElement!==field)?field.getAttribute?.('placeholder'):'');
  return !visible&&displayed===value&&selected.length===1&&selected[0].textContent.trim()===value;
}
