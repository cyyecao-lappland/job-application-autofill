export const phoenixRadio=f=>f.kind==='radio_group'&&f.component==='phoenix-radio';
export const udDate=f=>f.kind==='text'&&f.component==='ud-date'&&!f.readonly;
export const phoenixDate=f=>f.kind==='text'&&f.component==='phoenix-date';
export const antPicker=f=>f.kind==='text'&&f.component==='ant-picker'&&!f.disabled;

export function readFrameworkDate({moduleSelector,selector,component,baseline=null}){
 const roots=[...document.querySelectorAll(moduleSelector)];if(roots.length!==1)return {error:'module_not_unique'};
 const fields=[...roots[0].querySelectorAll(selector)];if(fields.length!==1)return {error:'field_not_unique'};
 const input=fields[0],visible=e=>!!e.getClientRects().length&&getComputedStyle(e).visibility!=='hidden';
 const path=e=>{if(e.id&&document.querySelectorAll('#'+CSS.escape(e.id)).length===1)return '#'+CSS.escape(e.id);const a=[];while(e&&e!==document.body){const peers=[...e.parentElement.children].filter(p=>p.tagName===e.tagName);a.unshift(e.tagName.toLowerCase()+':nth-of-type('+(peers.indexOf(e)+1)+')');e=e.parentElement;}return 'body > '+a.join(' > ');};
 const owner=input.closest(component==='phoenix-date'?'.phoenix-select':component==='ant-picker'?'.ant-picker':'.ud__picker');if(!owner)return {error:'date_owner_missing'};
 const menus=[...document.querySelectorAll(component==='phoenix-date'?'.phoenix-date-picker':component==='ant-picker'?'.ant-picker-dropdown':'.ud__picker-date-panel')].filter(visible);
 const rect=owner.getBoundingClientRect();
 const owned=menus.filter(m=>{if(owner.contains(m))return true;if(!baseline||baseline.owner!==path(owner)||baseline.visible.length||menus.length!==1||!(owner.contains(document.activeElement)||m.contains(document.activeElement)))return false;const r=m.getBoundingClientRect();return Math.min(rect.right,r.right)>Math.max(rect.left,r.left)&&Math.min(Math.abs(rect.bottom-r.top),Math.abs(rect.top-r.bottom))<80;});
 const menu=owned.length===1?owned[0]:null;
 const monthPanels=menu?[...menu.querySelectorAll('.ant-picker-month-panel')].filter(visible):[];
 const dayPanels=menu?[...menu.querySelectorAll('.ant-picker-date-panel')].filter(visible):[];
 const panelAmbiguous=monthPanels.length>1||dayPanels.length>1||monthPanels.length>0&&dayPanels.length>0;
 const monthPanel=monthPanels.length===1&&!panelAmbiguous?monthPanels[0]:null;
 const yearLabels=monthPanel?[...monthPanel.querySelectorAll('.ant-picker-year-btn')].filter(visible):[];
 const nav=direction=>{const buttons=monthPanel?[...monthPanel.querySelectorAll('.ant-picker-header-super-'+direction+'-btn')].filter(e=>visible(e)&&!e.disabled&&e.getAttribute('aria-disabled')!=='true'):[];return buttons.length===1?path(buttons[0]):null;};
 const edit=component==='phoenix-date'?menu?.querySelector('input.phoenix-calendar-input'):input;
 const label=owner.closest('.form-item,.ud-formily-item,.ant-form-item')?.querySelector('.form-item__text,.ud-formily-item-label,.ant-form-item-label');
 return {owner:path(owner),visible:menus.map(path),menu:menu?path(menu):null,editor:edit&&!edit.readOnly?path(edit):null,
  actual:component==='phoenix-date'?(owner.querySelector('.phoenix-select__tipEle')?.textContent||'').trim():input.value,
  panel_ambiguous:panelAmbiguous,
  precision:monthPanel?'month':dayPanels.length===1&&!panelAmbiguous?'day':null,
  readonly:input.readOnly,disabled:input.disabled,
  month_year:yearLabels.length===1?Number(yearLabels[0].textContent.match(/\d{4}/)?.[0]):null,
  month_previous:nav('prev'),month_next:nav('next'),
  month_cells:monthPanel?[...monthPanel.querySelectorAll('.ant-picker-cell[title]')].filter(visible).map(cell=>({
    value:cell.getAttribute('title'),selector:path(cell),disabled:cell.classList.contains('ant-picker-cell-disabled')||cell.getAttribute('aria-disabled')==='true'})):[],
  placeholder:input.getAttribute('placeholder'),dismiss:label?path(label):null,committed:menus.length===0};
}
