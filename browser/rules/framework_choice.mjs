// Read-only ownership and committed selection evidence; serialized in the page.
export function readFrameworkChoice({moduleSelector,selector,component,baseline=null,diagnostic=false}) {
 const roots=[...document.querySelectorAll(moduleSelector)];if(roots.length!==1)return {error:'module_not_unique'};
 const nodes=[...roots[0].querySelectorAll(selector)];if(nodes.length!==1)return {error:'field_not_unique'};
 const input=nodes[0],visible=e=>!!e.getClientRects().length&&getComputedStyle(e).visibility!=='hidden'&&getComputedStyle(e).display!=='none';
 const family={
  'ant-select':{owner:'.ant-select',menu:'.ant-select-dropdown',option:'.ant-select-item-option',value:'.ant-select-selection-item',multi:'.ant-select-multiple'},
  'moka-select':{owner:'[class*="sd-Select-container"]',menu:'[class*="sd-Select-menu-"]',option:'[class*="sd-Select-common-item-"],[class*="sd-Menu-container-"][class*="sd-Select-pointer-"]',value:'[class*="sd-Input-display-value"]',multi:'[class*="sd-Input-tag-container"]',tags:'[class*="sd-Tag-text"],[class*="sd-Tag-label"],[class*="sd-Tag-content"]'},
  'phoenix-select':{owner:'.phoenix-select',menu:'.phoenix-selectList,.area-selector-container',option:'.phoenix-selectList__listItem',value:'.phoenix-select__tipEle',multi:'.phoenix-select__multiValue',tags:'.phoenix-select__multiValue'},
  'atsx-select':{owner:'.atsx-select',menu:'.atsx-select-dropdown',option:'.atsx-select-dropdown-menu-item',value:'.atsx-select-selection-selected-value',multi:'.atsx-select-selection--multiple',tags:'.atsx-select-selection__choice__content'},
  'ud-select':{owner:'.ud__select',menu:'.ud__select__dropdown',option:'.ud__select__list__item',value:'.ud__select__selector__selection,.ud__select__selector__selectItem',multi:'.ud__select__selector-multiple',tags:'.ud__select__selector__tag'}
 }[component];if(!family)return {error:'unsupported_component'};
 const owner=input.closest(family.owner);if(!owner)return {error:'owner_missing'};
 if(component==='moka-select'&&(owner.parentElement?.closest(family.owner)||owner.querySelector(family.owner)))return {error:'framework_owner_ambiguous'};
 const path=e=>{if(e.id&&document.querySelectorAll('#'+CSS.escape(e.id)).length===1)return '#'+CSS.escape(e.id);const a=[];while(e&&e!==document.body){const s=[...e.parentElement.children].filter(n=>n.tagName===e.tagName);a.unshift(e.tagName.toLowerCase()+':nth-of-type('+(s.indexOf(e)+1)+')');e=e.parentElement;}return 'body > '+a.join(' > ');};
 const top=arr=>arr.filter((e,_,a)=>!a.some(x=>x!==e&&x.contains(e)));
 const all=top([...document.querySelectorAll(family.menu)].filter(visible));
 const ids=(input.getAttribute('aria-controls')||input.getAttribute('aria-owns')||owner.getAttribute('aria-controls')||'').split(/\s+/).filter(Boolean);
 const rect=owner.getBoundingClientRect();
 const dropdown=component==='moka-select'?owner.closest('[class*="sd-Dropdown-container-"]'):null;
 const dropdownOwners=dropdown?[...dropdown.querySelectorAll(family.owner)]:[];
 const menus=all.filter(m=>{
  if(owner.contains(m)||ids.some(id=>{const e=document.getElementById(id);return e&&(e===m||m.contains(e)||e.contains(m));}))return true;
  if(component==='moka-select')return !!(dropdown&&dropdownOwners.length===1&&dropdownOwners[0]===owner
      &&dropdown.contains(m)&&m.closest('[class*="sd-Dropdown-container-"]')===dropdown);
  if(!baseline||baseline.owner!==path(owner)||baseline.visible.length!==0||all.length!==1||
     !(owner.contains(document.activeElement)||m.contains(document.activeElement)||baseline.bound_menu===path(m)))return false;
  const r=m.getBoundingClientRect(),overlap=Math.max(0,Math.min(rect.right,r.right)-Math.max(rect.left,r.left));
  return overlap/Math.max(1,Math.min(rect.width,r.width))>=0.5&&Math.min(Math.abs(rect.bottom-r.top),Math.abs(rect.top-r.bottom))<80;
 });
 const menu=menus.length===1?menus[0]:null;
 const multiple=!!(owner.matches(family.multi)||owner.querySelector(family.multi)||menu?.querySelector('.phoenix-selectList__multipleLabel'));
 const valueNodes=top([...owner.querySelectorAll(multiple?(family.tags||family.value):family.value)].filter(visible));
 if(component==='moka-select'&&!multiple&&valueNodes.length>1)return {error:'framework_single_value_ambiguous'};
 if(component==='atsx-select'&&valueNodes.some(e=>e.querySelectorAll('.select-item-label').length>1))return {error:'framework_choice_label_ambiguous'};
 const values=valueNodes.map(e=>{
  const city=component==='atsx-select'?e.querySelector('.select-item-label'):null;
  return (city?.textContent||e.getAttribute('title')||e.textContent||'').trim();
 }).filter(Boolean);
 const variant=menu?.matches('[class*="sd-Cascader-menuContainer"]')?'cascader':menu?.matches('.area-selector-container')?'region_dialog':'choice';
 const options=menu&&variant==='choice'?top([...menu.querySelectorAll(family.option)].filter(visible)).map(o=>({label:(o.getAttribute('title')||o.getAttribute('aria-label')||o.textContent||'').trim(),selector:path(o),disabled:o.getAttribute('aria-disabled')==='true'||/disabled/i.test(o.className)||!!o.querySelector('[aria-disabled=true],.phoenix-checkbox--disabled')||(component==='moka-select'&&!!o.querySelector('[class*="sd-Menu-"][class*="disabled"]')),selected:o.getAttribute('aria-selected')==='true'||/selected|--checked/.test(o.className)})):[];
 const search=menu?.querySelector('input:not([readonly]):not([disabled])')||owner.querySelector('input:not([readonly]):not([disabled])');
 const editable=[...owner.querySelectorAll('input:not([readonly]):not([disabled])')].filter(visible);
 const prePopupSearch=component==='moka-select'&&all.length===0&&editable.length===1&&editable[0]===search;
 const item=owner.closest('.form-item,.ud-formily-item,.ant-form-item,[class*="apply-field-"]');
 const label=item?.querySelector('.form-item__text,.ud-formily-item-label,.ant-form-item-label,[class*="title-"]');
 return {owner:path(owner),menu:menu?path(menu):null,visible:all.map(path),menu_count:menus.length,variant,options,
  ...(diagnostic?{menu_structure:all.map(m=>({selector:path(m),classes:m.className,children:[...m.querySelectorAll('*')].filter(visible).slice(0,60).map(e=>({tag:e.tagName,classes:e.className,label:(e.textContent||'').trim().slice(0,150)}))}))}:{}),
  actual:multiple?values:component==='moka-select'?(values[0]||''):values.join(''),multiple,selection_present:values.length>0,search_text:input.value||'',
  search:search?path(search):null,dismiss:label?path(label):null,
  pre_popup_search:prePopupSearch,
  disabled:!!input.disabled||input.getAttribute('aria-disabled')==='true'||/disabled/i.test(owner.className),
  committed:all.length===0&&input.getAttribute('aria-expanded')!=='true'};
}
export const antChoice=f=>f.kind==='combobox'&&f.component==='ant-select';
export const mokaChoice=f=>f.kind==='combobox'&&f.component==='moka-select';
export const phoenixChoice=f=>f.kind==='combobox'&&f.component==='phoenix-select';
export const atsxChoice=f=>f.kind==='combobox'&&f.component==='atsx-select';
export const udChoice=f=>f.kind==='combobox'&&f.component==='ud-select';
