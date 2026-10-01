export const mokaCalendar=f=>f.kind==='text'&&f.component==='moka-date'&&f.readonly&&!f.disabled;

// The local Dropdown is the control's structural owner; no geometry or global
// newly appeared menu is sufficient evidence of ownership.
export function readMokaCalendar({moduleSelector,selector,trackedPopupSelector}){
 const roots=[...document.querySelectorAll(moduleSelector)];if(roots.length!==1)return {error:'module_not_unique'};
 const fields=[...roots[0].querySelectorAll(selector)];if(fields.length!==1)return {error:'field_not_unique'};
 const input=fields[0],owner=input.closest('label.day_info'),container=owner?.closest('[class*="sd-Dropdown-container-"]');
 if(!owner||!container)return {error:'moka_calendar_owner_missing'};
 if(!roots[0].contains(owner)||!roots[0].contains(container))return {error:'moka_calendar_owner_outside_module'};
 if(container.querySelectorAll('label.day_info').length!==1||owner.querySelectorAll('input').length!==1)return {error:'moka_calendar_owner_ambiguous'};
 if(!input.readOnly||input.type!=='text')return {error:'moka_calendar_readonly_required'};
 const visible=e=>{if(!e.getClientRects().length)return false;for(let p=e;p;p=p.parentElement){const s=getComputedStyle(p);if(s.display==='none'||['hidden','collapse'].includes(s.visibility)||s.opacity==='0')return false;}return true;};
 const path=e=>{if(e.id&&roots[0].querySelectorAll('#'+CSS.escape(e.id)).length===1)return '#'+CSS.escape(e.id);const parts=[];while(e&&e!==roots[0]){const peers=[...e.parentElement.children].filter(x=>x.tagName===e.tagName);parts.unshift(e.tagName.toLowerCase()+':nth-of-type('+(peers.indexOf(e)+1)+')');e=e.parentElement;}return ':scope > '+parts.join(' > ');};
 const candidates=[...container.children].filter(e=>e!==owner&&!e.contains(owner)&&visible(e)&&(e.textContent||'').trim());
 const hasClass=(e,prefix)=>[...e.classList].some(c=>c.startsWith(prefix));
 const dropdowns=[...container.children].filter(e=>e.tagName==='SPAN'&&e.children.length===1)
  .map(e=>e.children[0]).filter(e=>hasClass(e,'sd-Dropdown-dropdown-')&&visible(e));
 const popups=dropdowns.filter(e=>e.children.length===1&&hasClass(e.children[0],'sd-panal-menu-wrapper-'));
 const recognizable=popups.length===1;
 const shape=e=>({selector:path(e),classes:e.className,html:e.outerHTML.slice(0,20000)});
 let calendar=null;
 if(popups.length===1){
  const panels=[...popups[0].querySelectorAll('[class*="sd-panal-menu-wrapper-"]')].filter(visible);
  if(panels.length===1){
   const panel=panels[0],years=[...panel.querySelectorAll('[class*="sd-basic-selector-year-"]')].filter(visible);
   const year=years.length===1&&/^\d{4}年$/.test(years[0].textContent.trim())?Number(years[0].textContent.trim().slice(0,4)):null;
   const monthNames=['一月','二月','三月','四月','五月','六月','七月','八月','九月','十月','十一月','十二月'];
   const months=[...panel.querySelectorAll('[class*="sd-basic-year-item-"]')].filter(visible);
   const controls=fragment=>[...panel.querySelectorAll('[class*="'+fragment+'"]')].filter(visible);
   const left=controls('sd-Icon-icondoubleLeft-'),right=controls('sd-Icon-icondoubleRight-');
   const enabled=e=>!!e&&!e.closest('[disabled],[aria-disabled="true"],[class*="disabled"]');
   if(year!==null&&months.length===12&&monthNames.every(n=>months.filter(e=>e.textContent.trim()===n).length===1)&&left.length===1&&right.length===1){
    calendar={mode:'month',year,panel:path(panel),popup:path(popups[0]),previous:enabled(left[0])?path(left[0].parentElement):null,
     next:enabled(right[0])?path(right[0].parentElement):null,
     months:monthNames.map((label,i)=>{const node=months.find(e=>e.textContent.trim()===label);return {month:i+1,selector:path(node),disabled:!enabled(node)};})};
   }
  }
 }
 const display=input.value,actual=/^\d{4}-\d{2}\s*\(\d{1,3}岁\)$/.test(display)?display.slice(0,7):display;
 return {input:path(input),owner:path(owner),container:path(container),actual,display,disabled:input.disabled||input.getAttribute('aria-disabled')==='true',
  popups:popups.map(shape),popup_candidates:candidates.map(shape),
  recognizable,calendar,committed:dropdowns.length===0&&candidates.length===0,
  tracked_popup_visible:trackedPopupSelector?[...roots[0].querySelectorAll(trackedPopupSelector)].some(visible):null};
}
