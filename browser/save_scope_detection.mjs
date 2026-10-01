/** Read-only save ownership and action detection.
 *
 * This module never clicks, navigates, reads profile data, or declares that a
 * save succeeded.  DOM evidence creates candidates; persisted read-back is the
 * only path to a verified save rule.
 */

export function classifySaveAction(label) {
  const text=String(label||'').normalize('NFKC').trim().replace(/\s+/g,' ');
  const compact=text.replace(/\s+/g,'').toLowerCase();
  const finalSubmit=/(?:立即)?投递|确认投递|提交申请|最终提交|确认提交|submitapplication|applynow/.test(compact);
  if(finalSubmit)return {pattern:'FINAL_SUBMIT',transition:'terminal',candidate:false};
  // Record editors commonly persist a newly-created card with “添加”.  It is
  // a stay-on-page persistence action when it is scoped to the reviewed form.
  const save=/保存|save|添加|add/.test(compact);
  const advance=/下一步|继续|next|continue/.test(compact);
  if(save&&advance)return {pattern:'SAVE_AND_ADVANCE',transition:'step_change',candidate:true};
  if(save)return {pattern:'SAVE_STAY',transition:'stay',candidate:true};
  if(advance)return {pattern:'ADVANCE_WITH_PERSIST',transition:'step_change',candidate:true,requires_persistence_proof:true};
  return {pattern:'UNKNOWN',transition:'unknown',candidate:false};
}

export function classifySaveScopeEvidence(evidence) {
  if(!evidence||evidence.error)return {status:'unknown',scopes:[],reason:evidence?.error||'evidence_required'};
  const scopes=[];
  for(const control of evidence.controls||[]){
    const action=classifySaveAction(control.label);
    if(!action.candidate||!control.member_module_ids?.length)continue;
    scopes.push({
      candidate_id:'save-scope-'+scopes.length,
      member_module_ids:[...control.member_module_ids],
      action_pattern:action.pattern,
      transition:action.transition,
      control_selector:control.owner_control_selector||control.selector,
      ownership:control.ownership,
      root_selector:control.owner_selector,
      confidence:control.ownership==='native_form'&&control.persistent_owner&&action.pattern!=='ADVANCE_WITH_PERSIST'?'strong_candidate':'candidate',
      persistent_owner:control.persistent_owner===true,
      verified:false
    });
  }
  return {status:scopes.length?'candidates':'unknown',scopes,reason:scopes.length?null:'no_owned_save_action'};
}

// Serialized into a read-only browser evaluate call. Keep it self-contained.
export function readSaveScopeEvidence({moduleSelectors=[],rootSelector='body'}={}) {
  const visible=e=>!!(e&&e.getClientRects().length&&getComputedStyle(e).visibility!=='hidden');
  const label=e=>(e.tagName==='INPUT'?e.value:e.textContent||'').normalize('NFKC').trim().replace(/\s+/g,' ').slice(0,80);
  const path=e=>{
    if(!e)return null;
    if(e.id)return '#'+CSS.escape(e.id);
    const parts=[];
    while(e&&e!==document.documentElement){
      const peers=[...e.parentElement.children].filter(n=>n.tagName===e.tagName);
      parts.unshift(e.tagName.toLowerCase()+':nth-of-type('+(peers.indexOf(e)+1)+')');
      e=e.parentElement;
    }
    return 'html > '+parts.join(' > ');
  };
  const relativePath=(root,e)=>{
    if(!root||!e||!root.contains(e))return null;
    const parts=[];
    while(e&&e!==root){
      const peers=[...e.parentElement.children].filter(n=>n.tagName===e.tagName);
      parts.unshift(e.tagName.toLowerCase()+':nth-of-type('+(peers.indexOf(e)+1)+')');
      e=e.parentElement;
    }
    return e===root?':scope > '+parts.join(' > '):null;
  };
  const roots=[...document.querySelectorAll(rootSelector)];
  if(roots.length!==1)return {error:'save_root_not_unique',root_count:roots.length};
  const pageRoot=roots[0];
  const modules=moduleSelectors.map((item,index)=>{
    const spec=typeof item==='string'?{id:'module-'+index,selector:item}:item;
    const found=[...document.querySelectorAll(spec.selector)];
    return {id:spec.id,selector:spec.selector,count:found.length,element:found.length===1?found[0]:null};
  });
  const controls=[...pageRoot.querySelectorAll('button,[role="button"],input[type="submit"],input[type="button"]')].filter(visible).map(control=>{
    const text=label(control),compact=text.replace(/\s+/g,'').toLowerCase();
    const finalSubmit=/(?:立即)?投递|确认投递|提交申请|最终提交|确认提交|submitapplication|applynow/.test(compact);
    const save=/保存|save|添加|add/.test(compact),advance=/下一步|继续|next|continue/.test(compact);
    const pattern=finalSubmit?'FINAL_SUBMIT':save&&advance?'SAVE_AND_ADVANCE':save?'SAVE_STAY':advance?'ADVANCE_WITH_PERSIST':'UNKNOWN';
    let owner=null,persistentOwner=null,ownership='none',members=[];
    if(control.form){
      const owned=modules.filter(m=>m.element&&control.form.contains(m.element));
      if(owned.length){owner=control.form;ownership='native_form';members=owned;}
    }
    if(!owner){
      for(let node=control;node&&node!==document.documentElement;node=node.parentElement){
        const owned=modules.filter(m=>m.element&&(node===m.element||node.contains(m.element)));
        if(owned.length){owner=node;ownership='nearest_common_ancestor';members=owned;break;}
      }
    }
    if(owner){
      for(let node=owner;node&&node!==pageRoot.parentElement;node=node.parentElement){
        const tokens=String(node.className||'').split(/\s+/).filter(Boolean);
        const stableContainer=node.matches?.('section,article,[data-module],[data-section]')||
          tokens.some(token=>/(?:^|[-_])(module|card|panel|section)$/.test(token));
        if(stableContainer){persistentOwner=node;break;}
      }
    }
    const actionRoot=persistentOwner||owner;
    return {selector:path(control),owner_control_selector:relativePath(actionRoot,control),label:text,pattern,
      disabled:!!control.disabled||control.getAttribute('aria-disabled')==='true',ownership,
      owner_selector:path(actionRoot),immediate_owner_selector:path(owner),persistent_owner:!!persistentOwner,
      member_module_ids:members.map(m=>m.id)};
  });
  return {root_selector:rootSelector,modules:modules.map(({element,...m})=>m),controls};
}
