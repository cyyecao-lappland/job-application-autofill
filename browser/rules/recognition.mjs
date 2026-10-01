/** Control detection boundary: read-only DOM evidence and existing recognition rules.
 * No profile lookup, model calls, browser connection, writes, or execution adapters.
 * readModuleDOM/readControlEvidence are serialized by evaluate: keep their bodies
 * self-contained (no imported helpers or module-scope closures).
 * Detection does not authorize writing: executor preconditions and verification remain required.
 */
export function classifyComboInteraction(field, menu=null){
  if(field.kind!=='combobox')return {method:null,confirmed:false};
  // An editable input alone is not a text-accepting combobox. Only a verified
  // component contract may declare free-input support; never infer it here.
  const input=field.search_evidence;
  if(menu?.search)return {method:'search_select',confirmed:true,search:menu.search};
  if(input?.editable&&input?.selection_structure)
    return {method:'search_select',confirmed:false,search:{location:'field'}};
  return {method:'select_option',confirmed:!!menu&&!menu.error,search:null};
}

export function classifyControl(field){
  const native={select:'native_select',checkbox:'native_checkbox',radio:'native_radio',file:'native_file'};
  if(native[field.kind])return native[field.kind];
  if(classifyComboInteraction(field).method==='search_select')return 'search_select';
  if(field.kind==='combobox'&&field.component==='element-select')return 'element_select';
  if(field.kind==='combobox'&&field.component==='next-select')return 'next_select';
  if(field.component==='element-date-now')return 'element_date_now';
  if(field.component==='element-date')return 'element_date';
  return 'agent_required';
}

export function classifyDialog(d) {
  const s=d.controls.filter(c=>c.tag==='SELECT'),buttons=d.controls.filter(c=>c.tag==='BUTTON'&&c.text==='确定');
  if(s.length!==2||buttons.length!==1)return null;
  const labeled=s.some(c=>/^(省|省份)$/.test(c.label||''))&&s.some(c=>/^(市|城市)$/.test(c.label||''));
  if(d.title!=='城市选择'&&!labeled)return null;
  const province=s.find(c=>c.options.some(o=>o.label==='省')),city=s.find(c=>c.options.some(o=>o.label==='市'));
  if(!province||!city||province===city)return null;
  return {adapter:'province_city_dialog_v1',province,city,confirm:buttons[0],close:d.controls.find(c=>c.class_name.split(' ').includes('el-dialog__headerbtn'))};
}

// Matching is read-only. These rules consume freshly observed structure, never applicant data.
export function textProbe(field) {
  return field.kind==='text' && field.control_status==='unverified_text_candidate' &&
    !field.control_pattern && !/date|picker|hierarchy/.test(field.component||'') && !['element-date','element-date-now','ant-date','ant-month'].includes(field.component);
}
export function textCompatibility(evidence) {
  if(!evidence)return 'needs_probe';
  if(evidence.kind!=='text'||evidence.control_pattern||/date|picker|hierarchy/.test(evidence.component||'')||['element-date','element-date-now','ant-date','ant-month'].includes(evidence.component))return 'incompatible';
  return evidence.control_status==='unverified_text_candidate'?'needs_probe':'compatible';
}
export const provinceCity = field => field.dialogPattern==='two_native_selects';
export const administrativeRegion = field => field.dialogPattern==='administrative_region_search';
export const elementDate = field => field.kind==='text'&&field.component==='element-date'&&field.readonly===true;
export const elementDateNow = field => field.kind==='text'&&field.component==='element-date-now'&&field.readonly===true;
export const nextRangeDate = field => field.control_pattern==='next_range_date';
export const ariaSearch = field => field.kind==='combobox'&&field.component==='aria-search-select';
export const antDate = field => field.kind==='text'&&field.component==='ant-date'&&!field.control_pattern;
export const plainText = field => field.kind==='text'&&!field.control_pattern&&!['ant-picker','moka-date','moka-hierarchy','phoenix-date','ud-date','ud-range-date'].includes(field.component)&&!antDate(field)&&!elementDate(field)&&!elementDateNow(field);
export const checkbox = field => field.kind==='checkbox';
export const radio = field => field.kind==='radio';
export const radioGroup = field => field.kind==='radio_group'&&field.component!=='phoenix-radio';
export const fileUpload = field => field.kind==='file';
export const nativeSelect = field => field.kind==='select';
export const combobox = field => field.kind==='combobox'&&!field.control_pattern&&!['ant-select','moka-select','phoenix-select','atsx-select','ud-select'].includes(field.component);
