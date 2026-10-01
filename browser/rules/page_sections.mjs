// Component-family section rules. This function is serialized into the browser.
export function readFrameworkSections(){
 const visible=e=>!!e.getClientRects().length&&getComputedStyle(e).visibility!=='hidden';
 const path=e=>{if(e.id&&document.querySelectorAll('#'+CSS.escape(e.id)).length===1)return '#'+CSS.escape(e.id);const a=[];while(e&&e!==document.body){const s=[...e.parentElement.children].filter(n=>n.tagName===e.tagName);a.unshift(e.tagName.toLowerCase()+':nth-of-type('+(s.indexOf(e)+1)+')');e=e.parentElement;}return 'body > '+a.join(' > ');};
 const text=e=>(e?.innerText||'').trim();
 const relative=(root,e)=>{const a=[];while(e&&e!==root){const peers=[...e.parentElement.children].filter(n=>n.tagName===e.tagName);a.unshift(e.tagName.toLowerCase()+':nth-of-type('+(peers.indexOf(e)+1)+')');e=e.parentElement;}return ':scope > '+a.join(' > ');};
 let family,roots=[];
 if(document.querySelector('[class*="apply-block-"]')){family='moka';roots=[...document.querySelectorAll('[class*="apply-block-"]')];}
 else if(document.querySelector('.applyFormModuleWrapper-windows')){family='feishu';roots=[...document.querySelectorAll('.applyFormModuleWrapper-windows')];}
 else if(document.querySelector('.form-item--phoenix')){family='phoenix';roots=[...document.querySelectorAll('.form')];}
 else if(document.querySelector('.resume-content .tab-block')){family='sf';roots=[...document.querySelectorAll('.resume-content .tab-block')];}
 else if(document.querySelector('.module-title__edit')){family='oppo';roots=[...document.querySelectorAll('.module-title')].map(e=>e.parentElement);}
 else if(document.querySelector('[class*="campusPersonalInfoCon___"]')){family='ant';roots=[...document.querySelectorAll('form')].map(f=>{let p=f.parentElement;while(p&&p!==document.body&&!p.querySelector(':scope > [class*="boxTitleCon"]'))p=p.parentElement;return p&&p!==document.body?p:f.parentElement;});}
 else return null;
 roots=roots.filter(visible).filter((r,i,a)=>a.indexOf(r)===i&&!a.some(x=>x!==r&&x.contains(r)));
 const buttons=[...document.querySelectorAll('button,[role=button]')].filter(visible);
 const saves=buttons.filter(b=>/^(保存简历|暂存)$/.test(text(b)));
 const sections=roots.map((r,index)=>{
  let heading=family==='moka'?r.querySelector(':scope > [class*="blockTitle-"]'):family==='feishu'?r.querySelector('.applyFormModuleWrapper-text'):
    family==='sf'?r.querySelector('.tag-title'):family==='oppo'?r.querySelector('.module-title__label > span'):family==='ant'?r.querySelector('[class*="boxTitleCon"]'):r.closest('.ux-standard-form')?.parentElement?.querySelector('[class*="title"],h3');
  let label=text(heading).split('\n')[0].replace(/\s*\*$/,'').trim();
  if(!label&&family==='phoenix'){
    // A form is one complete record. Its surrounding section supplies its title.
    for(let p=r.parentElement;p&&p!==document.body&&!label;p=p.parentElement){
      const first=p.firstElementChild;if(first&&!first.contains(r)&&first!==r){const t=text(first);if(t&&t.length<45)label=t.split('\n')[0];}
    }
  }
  const allAdds=[...r.querySelectorAll('button,[role=button],.new-one-btn')].filter(visible).filter(b=>/^(?:\+\s*)?添加/.test(text(b)));
  const adds=allAdds.length>1?allAdds.filter(b=>/教育/.test(label)&&/教育/.test(text(b))):allAdds;
  const edit=[...r.querySelectorAll('button,[role=button],.corner-btn,.module-title__edit')].filter(visible).find(b=>text(b)==='编辑'||b.matches('.module-title__edit'));
  const localSaves=[...r.querySelectorAll('button,[role=button],.normal-btn')].filter(visible).filter(b=>/^保存$/.test(text(b)));
  let recordSelector=null,records=[];
  if(family==='moka'&&r.querySelector(':scope > [class*="apply-fields-"][class*="multi-"]'))recordSelector=':scope > [class*="apply-fields-"][class*="multi-"]';
  if(family==='ant'&&r.querySelector('[class*="formDynamicWrap___"]'))recordSelector='form > [class*="formDynamicWrap___"] > [class*="formDynamicItem___"]';
  if(family==='feishu'&&/(教育|实习|工作|项目|语言|获奖)/.test(label)&&
     (r.querySelector('[class*="apply-form-array-card__"]')||
      adds.some(b=>String(b.className).includes('apply-form-array-card-add-float-right__')))){
    recordSelector=':scope > .applyFormModuleWrapper-right > .ud-formily-item > .ud-formily-item-control > .ud-formily-item-control-content > .ud-formily-item-control-content-component > [class*="apply-form-array-card__"]';
  }
  if(family==='sf'&&/(教育|学历)/.test(label)){
   const cards=[...r.querySelectorAll('.education-list__wrapper .info-list-item')].filter(visible);
   if(cards.length){
    recordSelector='.education-list__wrapper .info-list-item';
    records=cards.map(e=>{
     const values={};
     for(const pair of e.querySelectorAll('.flex-wrapper')){
      const label=text(pair.querySelector('.label')).replace(/[：:]$/,'').trim();
      const value=text(pair.querySelector('.content'));
      if(label&&value)values[label]=value;
     }
     const edits=[...e.querySelectorAll('.corner-btn')].filter(visible).filter(b=>text(b)==='编辑');
     return {selector:path(e),state:e.querySelector('input,textarea,select,[role=combobox]')?'editing':'collapsed',
       card_values:values,edit_selector:edits.length===1?path(edits[0]):null};
    });
   }
   const collections=[...r.querySelectorAll('.resume-first-education-edit-item__wrapper')];
   if(!records.length&&collections.length===1){
    const collection=collections[0],selector=':scope > .education-edit-item__wrapper';
    const rows=[...collection.querySelectorAll(selector)].filter(visible);
    if(rows.length){recordSelector=selector;records=rows.map((e,i)=>({selector:path(e),record_boundary:{container_selector:path(collection),record_selector:selector,record_count:rows.length,record_index:i}}));}
   }
  }
  if(recordSelector&&!records.length)records=[...r.querySelectorAll(recordSelector)].map(e=>({selector:path(e)}));
  const emptyFormCollection=family==='sf'&&adds.length===1&&
    !!r.querySelector('.education-list__wrapper,.resume-work-module__wrapper')&&
    !records.length&&!r.querySelector('form,input,textarea,select,[role=combobox]');
  return {id:'section-'+index,selector:path(r),label,page_order:index,record_selector:recordSelector,records,
    empty_form_collection:emptyFormCollection,
    add_selector:adds.length===1?relative(r,adds[0]):null,add_label:adds.length===1?text(adds[0]).replace(/\s/g,''):null,
    edit_selector:edit?path(edit):null,save_selector:localSaves.length===1?path(localSaves[0]):null,
    controls:r.querySelectorAll('input,textarea,select,[role=combobox]').length};
 });
 return {family,sections,save:saves.length===1?{selector:path(saves[0]),label:text(saves[0])}:null,
  observed_at:Date.now()/1000,url:location.href};
}
