/** Narrow variant: editable ARIA combobox, owned listbox, single selection. */
export function readSearchDOM({moduleSelector, fieldSelector}) {
  const roots=[...document.querySelectorAll(moduleSelector)];
  if(roots.length!==1)return {error:'scope_not_unique'};
  const fields=[...roots[0].querySelectorAll(fieldSelector)];
  if(fields.length!==1)return {error:'control_not_unique'};
  const input=fields[0], ids=(input.getAttribute('aria-controls')||'').trim().split(/\s+/).filter(Boolean);
  if(input.getAttribute('role')!=='combobox'||ids.length!==1||input.readOnly)return {error:'unsupported_search_structure'};
  const menu=document.getElementById(ids[0]);
  if(!menu||menu.getAttribute('role')!=='listbox'||menu.getAttribute('aria-multiselectable')==='true')return {error:'owned_menu_unavailable'};
  const selector='#'+CSS.escape(ids[0]);
  const options=[...menu.querySelectorAll('[role="option"]')].map((option,index)=>({
    label:option.textContent.trim(),index,disabled:option.getAttribute('aria-disabled')==='true',
    id:option.id||null
  }));
  return {menuSelector:selector,options,query:input.value,actual:input.getAttribute('aria-valuetext'),
    busy:menu.getAttribute('aria-busy')==='true',visible:!!menu.getClientRects().length,
    expanded:input.getAttribute('aria-expanded')==='true',disabled:input.disabled,
    committed:input.getAttribute('aria-expanded')==='false'};
}
