import test,{before,after} from 'node:test';
import assert from 'node:assert/strict';
import {chromium} from 'playwright-core';
import {createNativePlaywrightSession} from '../browser/playwright_backend.mjs';
import {readModuleDOM} from '../browser/rules/dom.mjs';
import {readFrameworkChoice} from '../browser/rules/framework_choice.mjs';
import {readFrameworkSections} from '../browser/rules/page_sections.mjs';
import {resolveCollectionAdd} from '../browser/rules/collection_add.mjs';
import {readPopupDOM} from '../browser/rules/popup.mjs';
import {readElementPreviewCard,readProjectPreviewCard,readSfEducationPreviewCard} from '../browser/save_observer.mjs';
import {executeFieldControl} from '../browser/controls/service.mjs';
import {matchRegistered} from '../browser/rules/service.mjs';
import {entries} from '../browser/controls/registry.generated.mjs';
let browser,page,tab;
before(async()=>{browser=await chromium.launch({channel:'msedge',headless:true});page=await browser.newPage();({tab}=createNativePlaywrightSession(page,{browserId:'synthetic',tabId:'synthetic'}));});
after(async()=>{await browser?.close();});
async function fields(){return (await page.evaluate(readModuleDOM,{moduleSelector:'#record'})).fields;}
test('UD range binds endpoints and commits one month without changing its peer',async()=>{
 await page.setContent('<div id="record"><div class="ud-formily-item"><div class="ud-formily-item-label">起止时间</div><div class="throne-biz-date-range-picker-wrapper"><input class="ud__native-input"><input class="ud__native-input" value="2027-07"></div></div></div>');
 const fs=await fields();assert.deepEqual(fs.map(f=>f.range_endpoint),['start','end']);
 assert.ok(fs.every(f=>f.control_pattern==='ud_date_pair'));
 assert.deepEqual(fs.map(f=>f.label),['起止时间 开始时间','起止时间 结束时间']);
 const ctx={tab,packet:{module_selector:'#record'},field:fs[0],op:{value:'2024-09'},end:Date.now()+5000,markAction:()=>{}};
 await executeFieldControl(ctx);assert.deepEqual((await fields()).map(f=>f.value),['2024-09','2027-07']);
 await assert.rejects(executeFieldControl({...ctx,field:(await fields())[0],op:{value:'2024-09-01'}}),/explicit_month_required/);
});
test('UD range rejects a value which the site clears on blur',async()=>{
 await page.setContent('<div id="record"><div class="ud-formily-item"><div class="ud-formily-item-label">起止时间</div><div class="throne-biz-date-range-picker-wrapper"><input class="ud__native-input" onblur="this.value=\'\'"><input class="ud__native-input"></div></div></div>');
 await assert.rejects(executeFieldControl({tab,packet:{module_selector:'#record'},field:(await fields())[0],op:{value:'2024-09'},end:Date.now()+5000,markAction:()=>{}}),/ud_range_endpoint_not_committed/);
});
test('empty teleported Element menu permits search only for the focused editable aligned owner',async()=>{
 await page.setContent('<style>.el-select{position:absolute;left:10px;top:10px;width:200px;height:30px}.el-select-dropdown{position:absolute;left:10px;top:40px;width:200px;height:30px}</style><div id="record"><div class="el-select"><input id="school"></div></div><div class="el-select-dropdown"></div>');
 await page.locator('#school').focus();
 const args={moduleSelector:'#record',selector:'#school',baseline:{visible:[],menus:[]}};
 let state=await page.evaluate(readPopupDOM,args);assert.deepEqual(state.options,[]);assert.equal(state.search.location,'field');
 await page.locator('#school').evaluate(e=>e.readOnly=true);
 assert.equal((await page.evaluate(readPopupDOM,args)).error,'popup_ownership_unconfirmed');
 await page.locator('#school').evaluate(e=>e.readOnly=false);
 await page.locator('.el-select-dropdown').evaluate(e=>e.style.left='600px');
 assert.equal((await page.evaluate(readPopupDOM,args)).error,'popup_ownership_unconfirmed');
});
test('Element search rechecks editability after focus before typing a school query',async()=>{
 await page.setContent('<div id="record"><div class="el-form-item"><label>学校全称</label><div class="el-select"><input id="school" readonly placeholder="请输入关键词"><div class="el-select-dropdown" style="display:none"><ul><li class="el-select-dropdown__item">甲大学</li></ul></div></div></div></div><script>const input=document.querySelector("input"),menu=document.querySelector(".el-select-dropdown");input.onfocus=()=>input.readOnly=false;input.oninput=()=>menu.style.display="block";document.querySelector("li").onclick=()=>{input.value="甲大学";document.querySelector("li").classList.add("selected");input.readOnly=true;menu.style.display="none";input.blur();};</script>');
 const field=(await fields())[0];assert.equal(field.readonly,true);
 await executeFieldControl({tab,packet:{module_selector:'#record'},field,op:{value:'甲大学'},end:Date.now()+5000,markAction:()=>{},readField:async()=>({field:(await fields())[0]})});
 assert.equal((await fields())[0].value,'甲大学');assert.equal(await page.locator('.el-select-dropdown').isVisible(),false);
});
test('SF grade proof label and uploaded file receipt stay inside their own row',async()=>{
 const html='<div id="record"><div class="grade-prove__wrapper"><div class="el-upload"><span class="grade-prove__add-text">添加成绩证明</span><input type="file" name="file" style="display:none"></div><ul class="el-upload-list"><li class="el-upload-list__item is-success"><a class="el-upload-list__item-name">成绩单.pdf</a></li></ul></div></div>';
 await page.setContent(html);
 let f=(await fields())[0];assert.equal(f.label,'成绩证明');assert.equal(f.value,'成绩单.pdf');assert.equal(f.upload_ready,true);
 await page.locator('.el-upload-list__item').evaluate(e=>e.className='el-upload-list__item is-uploading');
 assert.equal((await fields())[0].upload_ready,false);
 await page.setContent(html.replace('</ul>','<li class="el-upload-list__item is-success"><a class="el-upload-list__item-name">另一份.pdf</a></li></ul>'));
 f=(await fields())[0];assert.equal(f.value,'');assert.equal(f.upload_ready,false);
});
test('editable Element date commits without focusing the following date picker',async()=>{
 await page.setContent('<div id="record"><div class="el-form-item"><label class="el-form-item__label">开始时间</label><div class="el-date-editor el-date-editor--date"><input id="start"></div></div><div class="el-form-item"><label class="el-form-item__label">结束时间</label><div class="el-date-editor el-date-editor--date"><input id="end"></div></div></div><div id="popup" style="display:none">calendar</div><script>document.querySelector("#end").onfocus=()=>document.querySelector("#popup").style.display="block";document.querySelector("#start").onkeydown=e=>{if(e.key==="Enter")document.querySelector("#start").dataset.committed="true";};</script>');
 const field=(await fields())[0];
 await executeFieldControl({tab,packet:{module_selector:'#record'},field,op:{value:'2025-09-17'},end:Date.now()+5000,markAction:()=>{},readField:async()=>({field:(await fields())[0]})});
 assert.equal(await page.locator('#start').inputValue(),'2025-09-17');
 assert.equal(await page.locator('#start').getAttribute('data-committed'),'true');
 assert.equal(await page.locator('#popup').isVisible(),false);
});
test('saved SF project card reads named fields with month precision and rejects ambiguity',async()=>{
 const html='<div id="projectList"><div class="resume-noedit-form"><p class="ft1">项目甲</p><p><span class="ft2">开发者</span><span class="ft3">2024.09-2025.07</span></p><div class="content-with-label"><div class="label">项目职责：</div><div class="content">职责甲</div></div><div class="content-with-label"><div class="label">项目描述：</div><div class="content">描述甲</div></div><a class="corner-btn">编辑</a></div></div>';
 await page.setContent(html);
 const fields=[['项目名称','项目甲'],['职务','开发者'],['开始时间','2024-09-01'],['结束时间','2025-07-01'],['项目职责','职责甲'],['项目简述','描述甲']].map(([label,value],id)=>({id:String(id),label,value,kind:'text'}));
 const read=()=>page.evaluate(readProjectPreviewCard,{selector:'#projectList',fields});
 assert.deepEqual((await read()).partial_field_ids,['2','3']);
 assert.equal((await read()).actual[2].value,'2024-09');
 fields[0].value='项目乙';assert.equal(await read(),null);fields[0].value='项目甲';
 fields[3].value='2025-08-01';assert.equal(await read(),null);fields[3].value='2025-07-01';
 await page.locator('#projectList').evaluate(e=>e.insertAdjacentHTML('beforeend','<input>'));assert.equal(await read(),null);
 await page.setContent(html.replace('</a>','</a><a class="corner-btn">编辑</a>'));assert.equal(await read(),null);
 await page.setContent(html.replace('</p><p>','</p><p class="ft1">项目甲</p><p>'));assert.equal(await read(),null);
});
test('SF empty collection discovery excludes open forms and ambiguous add controls',async()=>{
 const html='<div class="resume-content"><div class="tab-block" id="record"><h4 class="tag-title">教育信息</h4><div class="education-list__wrapper"><a class="new-one-btn">+\n 添加教育背景</a></div></div></div>';
 await page.setContent(html);
 const read=async()=>(await page.evaluate(readFrameworkSections)).sections[0];
 assert.equal((await read()).empty_form_collection,true);
 await page.locator('.education-list__wrapper').evaluate(e=>e.insertAdjacentHTML('beforeend','<form></form>'));
 assert.equal((await read()).empty_form_collection,false);
 await page.setContent(html.replace('</a>','</a><a class="new-one-btn">添加教育背景</a>'));
 assert.equal((await read()).empty_form_collection,false);
});
test('SF education section exposes sibling edit-item records and fresh boundary evidence',async()=>{
 await page.setContent(`<div class="resume-content"><section class="tab-block" id="educationList">
  <h4 class="tag-title">教育信息</h4><div class="resume-first-education-edit-item__wrapper">
   <div class="education-edit-item__wrapper"><form><div class="el-form-item"><label>学历</label><input disabled value="硕士研究生"></div><div class="el-form-item"><label>学校全称</label><input value="华东师范大学"></div></form></div>
   <div class="education-edit-item__wrapper"><form><div class="el-form-item"><label>学历</label><input disabled value="大学本科"></div><div class="el-form-item"><label>学校全称</label><input value=""></div></form></div>
  </div></section></div>`);
 const section=(await page.evaluate(readFrameworkSections)).sections[0];
 assert.equal(section.record_selector,':scope > .education-edit-item__wrapper');
 assert.equal(section.records.length,2);
 const first=section.records[0];
 const read=()=>page.evaluate(readModuleDOM,{moduleSelector:first.selector});
 const snapshot=await read();
 assert.equal(snapshot.record_boundary.record_count,2);
 assert.equal(snapshot.record_boundary.record_index,0);
 assert.ok(snapshot.fields.every(field=>field.record_container===first.selector));
 await page.locator('.resume-first-education-edit-item__wrapper').evaluate(e=>e.insertAdjacentHTML('beforeend',
  '<div class="education-edit-item__wrapper"><form><input value=""></form></div>'));
 assert.equal((await read()).record_boundary.record_count,3);
});
test('SF collapsed education cards retain identity and never count as an empty collection',async()=>{
 await page.setContent('<div class="resume-content"><section class="tab-block" id="educationList"><h4 class="tag-title">教育信息</h4><div class="education-list__wrapper"><div><div class="info-list-item"><div class="resume-noedit-form"><div class="flex-wrapper"><div class="label">学校全称：</div><div class="content">甲大学</div></div><a class="corner-btn">编辑</a></div></div></div><a class="new-one-btn">添加教育背景</a></div></section></div>');
 const section=(await page.evaluate(readFrameworkSections)).sections[0];
 assert.equal(section.empty_form_collection,false);
 assert.equal(section.records.length,1);
 assert.equal(section.records[0].state,'collapsed');
 assert.deepEqual(section.records[0].card_values,{'学校全称':'甲大学'});
 assert.ok(section.records[0].edit_selector);
 assert.equal((await page.evaluate(readModuleDOM,{moduleSelector:section.records[0].selector})).fields.length,0);
});

test('Ant existing inline record exposes its relocated Add control in the same collection',async()=>{
 await page.setContent('<div class="campusPersonalInfoCon___fixture"><section id="project"><div class="boxTitleCon___fixture">项目经历</div><form><div class="formDynamicWrap___fixture"><div class="formDynamicItem___fixture"><input value="既有项目"></div><div><button>添加项目经历</button></div></div></form></section></div>');
 const framework=await page.evaluate(readFrameworkSections);
 const section=framework.sections[0];assert.equal(framework.family,'ant');assert.equal(section.records.length,1);
 const selector=resolveCollectionAdd({inline_repeater:true,collection_selector:'#project',record_selector:section.record_selector,
   expected_record_count:1,add_label:'添加项目经历'},framework,1);
 assert.ok(selector);assert.equal(await page.locator('#project').locator(selector).innerText(),'添加项目经历');
 await page.locator('form').evaluate(e=>e.insertAdjacentHTML('beforeend','<button>添加项目经历</button>'));
 assert.equal(resolveCollectionAdd({inline_repeater:true,collection_selector:'#project',record_selector:section.record_selector,
   expected_record_count:1,add_label:'添加项目经历'},await page.evaluate(readFrameworkSections),1),null);
});
test('SF education saved-card proof checks dates and file names while marking hidden answers skipped',async()=>{
 const values={'学校全称':'甲大学','学历':'大学本科','专业名称':'计算机','入学时间':'2021-03-01','毕业时间':'2024-06-18'};
 await page.setContent('<div id="educationList"><div id="record" class="info-list-item"><div class="resume-noedit-form">'+Object.entries(values).map(([label,value])=>`<div class="flex-wrapper"><div class="label">${label}：</div><div class="content">${value}</div></div>`).join('')+'<span class="grade-prove-view__text">本科成绩单.pdf</span><a class="corner-btn">编辑</a></div></div></div>');
 const fields=Object.entries(values).map(([label,value],i)=>({id:String(i),label,value,kind:'text'}));
 fields.push({id:'topup',label:'是否专升本',value:'否',kind:'select'},{id:'file',label:'成绩证明',value:'本科成绩单.pdf',value_present:true,kind:'file'});
 const read=()=>page.evaluate(readSfEducationPreviewCard,{selector:'#record',fields});
 assert.deepEqual((await read()).partial_field_ids,['topup']);
 assert.equal((await read()).actual.find(f=>f.field_id==='file').value,'本科成绩单.pdf');
 fields[3].value='2019-09-01';assert.equal(await read(),null);fields[3].value='2021-03-01';
 fields.at(-1).value='另一成绩单.pdf';assert.equal(await read(),null);fields.at(-1).value='本科成绩单.pdf';
 fields.at(-1).value_present=false;fields.at(-1).required=true;assert.equal(await read(),null);
 fields.at(-1).value_present=true;fields.at(-1).upload_ready=false;assert.equal(await read(),null);
 fields.at(-1).upload_ready=true;
 await page.locator('#record').evaluate(e=>e.insertAdjacentHTML('beforeend','<input value="editing">'));
 assert.equal(await read(),null);
});
test('saved Element preview binds city-only display by label and reports partial verification',async()=>{
 await page.setContent('<div id="record"><form class="resume-noedit-form"><div class="el-form-item"><label class="el-form-item__label">籍贯：</label><div class="el-form-item__content">衡阳市</div></div></form><a class="corner-btn">编辑</a></div>');
 const f={id:'city',kind:'text',label:'籍贯',value:'湖南省-衡阳市',verified_control:{adapter:'province_city_dialog_v1',actual:'湖南省-衡阳市'}};
 const read=()=>page.evaluate(readElementPreviewCard,{selector:'#record',fields:[f]});
 assert.deepEqual((await read()).partial_field_ids,['city']);
 await page.locator('.el-form-item__content').evaluate(e=>e.textContent='长沙市');assert.equal(await read(),null);
});
async function apply(value){const field=(await fields())[0];let actions=0;await executeFieldControl({tab,packet:{module_selector:'#record'},field,op:{value},end:Date.now()+12000,markAction:()=>actions++,readField:async()=>({field:(await fields())[0]})});return actions;}
function fixture(component,{duplicate=false,foreign=false,search=false,multi=false,detached=false}={}){
 const family={
  'ant-select':['ant-select','ant-select-selector','ant-select-selection-item','ant-select-dropdown','ant-select-item-option'],
  'moka-select':['sd-Select-container-SUFFIX','sd-Input-common-input-SUFFIX','sd-Input-display-value-SUFFIX','sd-Select-menu-SUFFIX','sd-Select-common-item-SUFFIX'],
  'phoenix-select':['phoenix-select','','phoenix-select__tipEle','phoenix-selectList','phoenix-selectList__listItem'],
  'atsx-select':['atsx-select','atsx-select-selection--multiple','atsx-select-selection-selected-value','atsx-select-dropdown','atsx-select-dropdown-menu-item'],
  'ud-select':['ud__select','','ud__select__selector__selection','ud__select__dropdown','ud__select__list__item']
 }[component];const [owner,inner,value,menu,option]=family;
 const options=duplicate?['目标','目标']:search?['旧选项']:['目标','另一项'];
 return `<style>#owner{display:block;width:220px;height:30px}#menu{position:absolute;left:8px;top:38px;width:220px;background:white}#value:empty{display:none}</style>
 ${component==='moka-select'&&!detached?'<div class="sd-Dropdown-container-FIXTURE">':''}<div id="record"><div class="form-item"><label class="form-item__text" id="label">字段</label><div id="owner" class="${owner}">
 ${component==='atsx-select'?`<div id="field" role="combobox" class="${multi?inner:''}"><span id="value" class="${value}"></span><input id="search"></div>`:`<div class="${inner}"><span id="value" class="${value}"></span><input id="field" ${component==='ud-select'?'role="combobox"':''}></div>`}</div></div></div>
 <div id="menu" class="${menu}" style="display:${foreign?'block':'none'}">${options.map(v=>`<div class="${option}">${v}</div>`).join('')}</div>${component==='moka-select'&&!detached?'</div>':''}<input id="other" value="untouched">
 <script>(()=>{const owner=document.querySelector('#owner'),menu=document.querySelector('#menu'),value=document.querySelector('#value'),input=document.querySelector('#search')||document.querySelector('#field');
 const bind=()=>{for(const o of menu.children)o.onclick=()=>{${multi?`const t=document.createElement('span');t.className='atsx-select-selection__choice__content';t.textContent=o.textContent;owner.appendChild(t);`:`value.textContent=o.textContent;menu.style.display='none';input.value='';`} };};bind();
 owner.onclick=()=>{input.focus();menu.style.display='block';};input.onkeydown=e=>{if(e.key==='Escape')menu.style.display='none';};document.querySelector('#label').onclick=()=>menu.style.display='none';
 ${search?`input.oninput=()=>setTimeout(()=>{menu.innerHTML='<div class="${option}">目标</div>';bind();},100);`:''}
 })();</script>`;
}
for(const component of ['ant-select','moka-select','phoenix-select','atsx-select','ud-select'])test(component+' is uniquely routed and commits the selected display value',async()=>{
 await page.setContent(fixture(component));const f=(await fields())[0];assert.equal(f.component,component);assert.ok(matchRegistered(f,entries,'field'));
 await apply('目标');assert.equal((await fields())[0].value,'目标');assert.equal(await page.locator('#other').inputValue(),'untouched');assert.equal(await apply('目标'),0);
});
test('Moka rejects nested owners before acting but permits independent sibling fields in one module',async()=>{
 for(const nested of ['ancestor','descendant']){
  await page.setContent(fixture('moka-select'));
  await page.evaluate(nested=>{
   const owner=document.querySelector('#owner');
   if(nested==='ancestor'){
    const outer=document.createElement('div');outer.className='sd-Select-container-OUTER';owner.before(outer);outer.appendChild(owner);
   }else owner.insertAdjacentHTML('beforeend','<div class="sd-Select-container-INNER"><input></div>');
  },nested);
  const state=await page.evaluate(readFrameworkChoice,{moduleSelector:'#record',selector:'#field',component:'moka-select'});
  assert.equal(state.error,'framework_owner_ambiguous');
  await assert.rejects(apply('目标'),/framework_owner_ambiguous/);
  assert.equal(await page.locator('#menu').isVisible(),false);
 }
 await page.setContent(fixture('moka-select'));
 await page.evaluate(()=>{
  const owner=document.querySelector('#owner'),dropdown=document.createElement('div');
  dropdown.className='sd-Dropdown-container-TARGET';owner.before(dropdown);
  dropdown.append(owner,document.querySelector('#menu'));
  document.querySelector('#record').insertAdjacentHTML('beforeend','<div class="sd-Dropdown-container-OTHER"><div class="sd-Select-container-OTHER"><input></div></div>');
 });
 await apply('目标');assert.equal(await page.locator('#value').textContent(),'目标');
});
test('Moka single selection cannot concatenate two visible display nodes into a success',async()=>{
 await page.setContent(fixture('moka-select'));
 await page.locator('#value').evaluate(e=>{e.textContent='目';e.insertAdjacentHTML('afterend','<span class="sd-Input-display-value-SECOND">标</span>');});
 const state=await page.evaluate(readFrameworkChoice,{moduleSelector:'#record',selector:'#field',component:'moka-select'});
 assert.equal(state.error,'framework_single_value_ambiguous');
 await assert.rejects(apply('目标'),/framework_single_value_ambiguous/);
 assert.equal(await page.locator('#menu').isVisible(),false);
 await page.setContent(fixture('moka-select'));
 await page.locator('#value').evaluate(e=>{e.textContent='';e.insertAdjacentHTML('beforeend','<span class="sd-Input-display-value-INNER">目标</span>');});
 assert.equal(await apply('目标'),0);
});
test('search query never proves a selection and a populated remote menu can be searched',async()=>{
 await page.setContent(fixture('moka-select',{search:true}));await page.locator('#field').fill('目标');
 const r=await page.evaluate(readFrameworkChoice,{moduleSelector:'#record',selector:'#field',component:'moka-select'});assert.equal(r.actual,'');assert.equal(r.selection_present,false);
 await apply('目标');assert.equal((await fields())[0].value,'目标');
});
test('preexisting portal is not claimed even after focusing the current field',async()=>{
 await page.setContent(fixture('moka-select',{foreign:true}));await page.locator('#field').focus();await assert.rejects(apply('目标'),/preexisting_popup/);assert.equal(await page.locator('#value').textContent(),'');
});
test('duplicate exact options defer and close the popup',async()=>{
 await page.setContent(fixture('phoenix-select',{duplicate:true}));await assert.rejects(apply('目标'),/option_ambiguous/);assert.equal(await page.locator('#menu').isVisible(),false);
});
test('ATSX multi uses selected tokens and validates all requested options before changing the set',async()=>{
 await page.setContent(fixture('atsx-select',{multi:true}));await assert.rejects(apply(['目标','不存在']),/multiselect_option_missing/);assert.deepEqual((await fields())[0].value,[]);
 await apply(['目标','另一项']);assert.deepEqual((await fields())[0].value,['目标','另一项']);assert.equal(await apply(['另一项','目标']),0);
});
test('Phoenix custom radio groups expose and check one logical choice',async()=>{
 await page.setContent(`<div id="record"><div class="form-item--phoenix"><label class="form-item__text">性别</label><div class="phoenix-radio-group"><div class="phoenix-radio" onclick="this.classList.add('phoenix-radio--checked')"><span class="phoenix-radio__radio-text">男</span></div><div class="phoenix-radio"><span class="phoenix-radio__radio-text">女</span></div></div></div></div>`);
 const f=(await fields())[0];assert.equal(f.component,'phoenix-radio');assert.equal(f.options.length,2);await apply('男');assert.equal((await fields())[0].value,'男');
});
test('calendar and hierarchy inputs never fall through to plain text',async()=>{
 await page.setContent(`<div id="record"><div class="ant-picker"><input readonly></div><label class="day_info"><input readonly></label><span class="sd-Input-tag-container-X"><input readonly></span><div class="throne-biz-date-range-picker-wrapper"><input></div></div>`);
 const fs=await fields();assert.deepEqual(fs.map(f=>f.component),['ant-picker','moka-date','moka-hierarchy','ud-range-date']);
 assert.equal(matchRegistered(fs[0],entries,'field').config.name,'ant_picker_input_v1');
 assert.equal(matchRegistered(fs[1],entries,'field').config.name,'moka_calendar_v1');
 for(const f of fs.slice(2))assert.equal(matchRegistered(f,entries,'field'),null);
});
test('Element Plus date wrapper does not hide or reclassify its readonly input',async()=>{
 await page.setContent(`<div id="record"><div class="el-form-item"><label>出生日期</label><div role="combobox" aria-haspopup="dialog" class="el-date-editor"><input id="date" type="text" readonly></div></div></div>`);
 const fs=await fields();assert.equal(fs.length,1);assert.equal(fs[0].component,'element-date');assert.equal(fs[0].selector,'#date');assert.equal(matchRegistered(fs[0],entries,'field').config.name,'element_date_picker_v1');
});
for(const component of ['phoenix-date','ud-date'])test(component+' commits through its date editor and rejects impossible dates',async()=>{
 const phoenix=component==='phoenix-date';
 await page.setContent(`<style>#owner{width:200px;height:30px}#calendar{position:absolute;top:38px;left:8px;width:200px}</style><div id="record"><div class="${phoenix?'form-item form-item--phoenix':'ud-formily-item'}"><label id="label" class="${phoenix?'form-item__text':'ud-formily-item-label'}">日期</label><div id="owner" class="${phoenix?'phoenix-select':'ud__picker'}"><input id="field" placeholder="${phoenix?'':'YYYY-MM'}"><span class="phoenix-select__tipEle" id="value"></span>${phoenix?'<svg><path id="prefix_field_date_time_picker"></path></svg>':''}</div></div></div><div id="calendar" class="${phoenix?'phoenix-date-picker':'ud__picker-date-panel'}" style="display:none">${phoenix?'<input id="dateEditor" class="phoenix-calendar-input">':''}</div><script>(()=>{const field=document.querySelector('#field'),calendar=document.querySelector('#calendar'),editor=document.querySelector('#dateEditor')||field;document.querySelector('#owner').onclick=()=>{calendar.style.display='block';field.focus();};editor.onkeydown=e=>{if(e.key==='Enter'){document.querySelector('#value').textContent=editor.value;calendar.style.display='none';}};document.querySelector('#label').onclick=()=>calendar.style.display='none';})();</script>`);
 assert.equal((await fields())[0].component,component);await assert.rejects(apply('2025-02-29'),/invalid_date/);assert.equal(await page.locator('#calendar').isVisible(),false);
 const value=phoenix?'2002-02-23':'2027-07';await apply(value);assert.equal((await fields())[0].value,value);assert.equal(await page.locator('#calendar').isVisible(),false);
 if(!phoenix)await assert.rejects(apply('2027-07-01'),/explicit_month_transform/);
});
test('unrelated asynchronous portal without target focus is not owned',async()=>{
 await page.setContent(fixture('moka-select',{detached:true}));const baseline=await page.evaluate(readFrameworkChoice,{moduleSelector:'#record',selector:'#field',component:'moka-select'});
 await page.locator('#menu').evaluate(e=>e.style.display='block');await page.locator('#other').focus();
 const state=await page.evaluate(readFrameworkChoice,{moduleSelector:'#record',selector:'#field',component:'moka-select',baseline});assert.equal(state.menu,null);assert.equal(state.committed,false);
});
test('UD selectItem is committed display text in the current Feishu structure',async()=>{
 await page.setContent(fixture('ud-select').replaceAll('ud__select__selector__selection','ud__select__selector__selectItem'));
 await apply('目标');assert.equal((await fields())[0].value,'目标');assert.equal(await apply('目标'),0);
});
test('Moka upload receipt requires a filename and no active progress',async()=>{
 await page.setContent('<div id="record"><div class="file_upload"><button class="file_upload-btn">上传简历</button><input type="file" id="resumeKey"></div></div>');
 assert.equal((await fields())[0].upload_ready,false);
 await page.locator('button').evaluate(e=>e.textContent='resume.pdf');
 assert.equal((await fields())[0].value,'resume.pdf');assert.equal((await fields())[0].upload_ready,true);
 await page.locator('button').evaluate(e=>e.setAttribute('aria-busy','true'));
 assert.equal((await fields())[0].upload_ready,false);
});
test('ATSX city tokens read the city label separately from preference numbering',async()=>{
 await page.setContent(fixture('atsx-select',{multi:true}));
 await apply(['目标']);
 await page.locator('.atsx-select-selection__choice__content').evaluate(e=>e.innerHTML='<span class="select-item-tag">1</span><span class="select-item-label">目标</span>');
 const field=(await fields())[0];assert.deepEqual(field.value,['目标']);
 const state=await page.evaluate(readFrameworkChoice,{moduleSelector:'#record',selector:field.selector,component:'atsx-select'});
 assert.deepEqual(state.actual,['目标']);assert.equal(await apply(['目标']),0);
});

test('Moka search can mount a menu only after typing into its unique editable owner input',async()=>{
 await page.setContent(fixture('moka-select'));
 await page.evaluate(()=>{
  const input=document.querySelector('#field'),menu=document.querySelector('#menu');
  document.querySelector('#owner').onclick=()=>input.focus();
  input.oninput=()=>{menu.style.display='block';};
 });
 const before=await page.evaluate(readFrameworkChoice,{moduleSelector:'#record',selector:'#field',component:'moka-select'});
 assert.equal(before.pre_popup_search,true);
 await apply('目标');assert.equal(await page.locator('#value').textContent(),'目标');
 await page.setContent(fixture('moka-select'));
 await page.locator('#owner').evaluate(e=>e.insertAdjacentHTML('beforeend','<input id="extra">'));
 assert.equal((await page.evaluate(readFrameworkChoice,{moduleSelector:'#record',selector:'#field',component:'moka-select'})).pre_popup_search,false);
});

test('Moka enum deferral retains only enabled options actually observed before filtering',async()=>{
 await page.setContent(fixture('moka-select'));
 await page.locator('#menu').evaluate(e=>e.insertAdjacentHTML('beforeend','<div class="sd-Select-common-item-SUFFIX disabled">禁用项</div>'));
 await page.evaluate(()=>{
  document.querySelector('#field').oninput=()=>document.querySelector('#menu').replaceChildren();
 });
 await assert.rejects(apply('未对应的来源'),error=>{
  assert.equal(error.message,'option_missing_or_uncommitted');
  assert.deepEqual(error.enumCandidate.options,['目标','另一项']);return true;
 });
 assert.equal(await page.locator('#value').textContent(),'');
 assert.equal(await page.locator('#menu').isVisible(),false);
});

test('Moka pre-popup search stops if the editable input is replaced after the query',async()=>{
 await page.setContent(fixture('moka-select'));
 await page.evaluate(()=>{
  const input=document.querySelector('#field');
  document.querySelector('#owner').onclick=()=>input.focus();
  input.oninput=()=>{
   const replacement=document.createElement('input');replacement.id='field';replacement.readOnly=true;
   input.replaceWith(replacement);
  };
 });
 await assert.rejects(apply('目标'),/search_input_changed/);
 assert.equal(await page.locator('#value').textContent(),'');
});

test('Moka ownership rejects a nearby focused menu and a container with multiple owners',async()=>{
 await page.setContent(fixture('moka-select',{detached:true}));
 let state=await page.evaluate(readFrameworkChoice,{moduleSelector:'#record',selector:'#field',component:'moka-select'});
 const baseline={owner:state.owner,visible:state.visible};
 await page.locator('#field').focus();await page.locator('#menu').evaluate(e=>e.style.display='block');
 assert.equal((await page.evaluate(readFrameworkChoice,{moduleSelector:'#record',selector:'#field',component:'moka-select',baseline})).menu,null);
 await page.setContent(fixture('moka-select'));
 await page.locator('#menu').evaluate(e=>e.style.display='block');
 assert.equal((await page.evaluate(readFrameworkChoice,{moduleSelector:'#record',selector:'#field',component:'moka-select'})).menu,'#menu');
 await page.locator('.sd-Dropdown-container-FIXTURE').evaluate(e=>e.insertAdjacentHTML('beforeend','<div class="sd-Select-container-OTHER"><input></div>'));
 assert.equal((await page.evaluate(readFrameworkChoice,{moduleSelector:'#record',selector:'#field',component:'moka-select'})).menu,null);
});

function readonlyAntMonthFixture({duplicate=false,disabled=false,day=false,doublePanel=false}={}){
 return `<div id="record"><div class="ant-form-item"><label class="ant-form-item-label">结束时间</label>
  <div id="picker" class="ant-picker"><input id="month" readonly ${disabled?'disabled':''} placeholder="结束时间">
   <div id="monthMenu" class="ant-picker-dropdown" style="display:none"><div class="${day?'ant-picker-date-panel':'ant-picker-month-panel'}">
    <button class="ant-picker-header-super-prev-btn">prev</button><span class="ant-picker-year-btn">2025</span><button class="ant-picker-header-super-next-btn">next</button>
    <div id="monthCells"></div></div></div></div></div></div>
 <script>(function(){let year=2025;const menu=document.querySelector('#monthMenu'),input=document.querySelector('#month');
 const render=()=>{document.querySelector('.ant-picker-year-btn').textContent=year;document.querySelector('#monthCells').innerHTML='<div class="ant-picker-cell" title="'+year+'-09">Sep</div>'${duplicate?"+'<div class=\"ant-picker-cell\" title=\"'+year+'-09\">Sep</div>'":''};};render();
 document.querySelector('#picker').onclick=e=>{if(e.target===input||e.target.id==='picker'){if(!input.disabled)menu.style.display='block';}};
 document.querySelector('.ant-picker-header-super-prev-btn').onclick=()=>{year--;render();};
 document.querySelector('.ant-picker-header-super-next-btn').onclick=()=>{year++;render();};
 document.querySelector('#monthCells').onclick=e=>{const cell=e.target.closest('[title]');if(cell){input.value=cell.title;menu.style.display='none';}};
 input.onkeydown=e=>{if(e.key==='Escape')menu.style.display='none';};
 document.querySelector('label').onclick=()=>menu.style.display='none';
 ${doublePanel?"menu.appendChild(menu.querySelector('.ant-picker-month-panel').cloneNode(true));":''}
 })();</script>`;
}

test('readonly Ant month picker uses its owned calendar and confirms year movement before selection',async()=>{
 await page.setContent(readonlyAntMonthFixture());
 const f=(await fields())[0];assert.equal(f.readonly,true);assert.equal(f.disabled,false);assert.equal(f.control_status,'recognized');
 assert.equal(matchRegistered(f,entries,'field').config.name,'ant_picker_input_v1');
 await apply('2026-09');assert.equal((await fields())[0].value,'2026-09');assert.equal(await page.locator('#monthMenu').isVisible(),false);
 assert.equal(await apply('2026-09'),0);
});

test('readonly Ant picker rejects duplicate month cells and closes the owned popup',async()=>{
 await page.setContent(readonlyAntMonthFixture({duplicate:true}));
 await assert.rejects(apply('2025-09'),/date_month_missing_or_ambiguous/);
 assert.equal((await fields())[0].value,'');assert.equal(await page.locator('#monthMenu').isVisible(),false);
});

test('disabled Ant picker remains disabled and readonly day picker does not fall through to fill',async()=>{
 await page.setContent(readonlyAntMonthFixture({disabled:true}));
 assert.equal((await fields())[0].disabled,true);assert.equal(matchRegistered((await fields())[0],entries,'field'),null);
 await page.setContent(readonlyAntMonthFixture({day:true}));
 await assert.rejects(apply('2026-09-16'),/readonly_month_panel_required|owned_date_editor_missing/);
 assert.equal((await fields())[0].value,'');assert.equal(await page.locator('#monthMenu').isVisible(),false);
});

test('Ant calendar with two visible month panels rejects selection and closes without choosing a panel',async()=>{
 await page.setContent(readonlyAntMonthFixture({doublePanel:true}));
 await assert.rejects(apply('2025-09'),/date_panel_ambiguous/);
 assert.equal((await fields())[0].value,'');assert.equal(await page.locator('#monthMenu').isVisible(),false);
 assert.deepEqual(await page.locator('.ant-picker-year-btn').allTextContents(),['2025','2025']);
});
test('Moka Menu pointer rows commit once despite nested content and search highlights',async()=>{
 const html=fixture('moka-select').replaceAll('sd-Select-common-item-SUFFIX','sd-Menu-container-X sd-Select-pointer-X');
 await page.setContent(html);
 await page.locator('#menu > div').first().evaluate(e=>e.innerHTML='<div class="sd-Menu-content-X"><div class="sd-Menu-content-item-X"><span class="sd-Select-keyword-X">目标</span></div></div>');
 await apply('目标');assert.equal((await fields())[0].value,'目标');assert.equal(await apply('目标'),0);
});
test('Moka Menu disabled and decorative rows are not available options',async()=>{
 await page.setContent(fixture('moka-select').replaceAll('sd-Select-common-item-SUFFIX','sd-Menu-container-X sd-Select-pointer-X'));
 await page.locator('#menu > div').first().evaluate(e=>e.classList.add('sd-Menu-disabled-X'));
 await page.locator('#menu').evaluate(e=>e.insertAdjacentHTML('beforeend','<div class="sd-Menu-content-item-X">目标</div>'));
 await assert.rejects(apply('目标'),/option_missing_or_uncommitted/);
 assert.equal(await page.locator('#value').textContent(),'');assert.equal(await page.locator('#menu').isVisible(),false);
});
test('Moka Menu nested disabled markers exclude the clickable outer row',async()=>{
 await page.setContent(fixture('moka-select').replaceAll('sd-Select-common-item-SUFFIX','sd-Menu-container-X sd-Select-pointer-X'));
 await page.locator('#menu > div').first().evaluate(e=>e.innerHTML='<div class="sd-Menu-content-item-X sd-Menu-disabled-X">目标</div>');
 await page.locator('#owner').click();
 const r=await page.evaluate(readFrameworkChoice,{moduleSelector:'#record',selector:'#field',component:'moka-select',baseline:{owner:'#owner',visible:[]}});
 assert.equal(r.options.find(o=>o.label==='目标').disabled,true);
 assert.equal(r.options.find(o=>o.label==='另一项').disabled,false);
});
test('Element radio pairs without a group wrapper form one labeled choice',async()=>{
 await page.setContent(`<div id="record"><div class="el-form-item"><label class="el-form-item__label">接受调剂</label><div class="el-form-item__content">${['是','否'].map((v,i)=>`<label class="el-radio"><input type="radio" name="transfer" value="${i}"><span class="el-radio__label">${v}</span></label>`).join('')}</div></div></div>`);
 const fs=await fields();assert.equal(fs.length,1);assert.equal(fs[0].kind,'radio_group');assert.equal(fs[0].label,'接受调剂');
 assert.deepEqual(fs[0].options.map(o=>o.label),['是','否']);await apply('是');assert.equal((await fields())[0].value,'是');
});
test('ATSX uploaded resume is readable only with the matching done receipt',async()=>{
 await page.setContent('<div id="record"><div class="uploadResume"><input type="file"><p class="uploadFile-loadedFilename">resume.pdf</p><div class="atsx-upload-list-item-done"><span class="atsx-upload-list-item-name-text">resume.pdf</span></div></div></div>');
 assert.equal((await fields())[0].value,'resume.pdf');assert.equal((await fields())[0].upload_ready,true);
 await page.locator('.atsx-upload-list-item-done').evaluate(e=>e.className='atsx-upload-list-item-uploading');
 assert.equal((await fields())[0].upload_ready,false);
});
test('paired Element date inputs retain separate start and end identity',async()=>{
 await page.setContent(`<div id="record"><div class="el-form-item"><label>起止时间</label><div class="el-date-editor"><input type="text" readonly id="start"></div><div class="el-date-editor"><input type="text" readonly id="end"></div></div></div>`);
 const fs=await fields();assert.deepEqual(fs.map(f=>f.range_endpoint),['start','end']);
 assert.ok(fs.every(f=>f.control_pattern==='element_date_pair'&&matchRegistered(f,entries,'field').config.name==='element_date_picker_v1'));
});
test('ATSX historical uploads require a completed receipt matching the current filename',async()=>{
 await page.setContent('<div id="record"><div class="uploadResume"><input type="file"><p class="uploadFile-loadedFilename">resume.pdf</p>'+['old.pdf','resume.pdf','resume.pdf'].map(n=>`<div class="atsx-upload-list-item-done"><span class="atsx-upload-list-item-name-text">${n}</span></div>`).join('')+'</div></div>');
 assert.equal((await fields())[0].upload_ready,true);
 await page.locator('.uploadFile-loadedFilename').evaluate(e=>e.textContent='other.pdf');
 assert.equal((await fields())[0].upload_ready,false);
});
test('Element resume receipt outside the hidden uploader requires a visible unique downloadable file',async()=>{
 await page.setContent('<div id="record"><div class="resume-file"><div class="file-list"><span class="file__name">resume.pdf</span><a download href="https://example.test/resume.pdf">下载</a></div><div style="display:none"><div class="el-upload"><input type="file"></div></div></div></div>');
 assert.equal((await fields())[0].value,'resume.pdf');assert.equal((await fields())[0].upload_ready,true);
 await page.locator('a').evaluate(e=>e.removeAttribute('download'));
 assert.equal((await fields())[0].upload_ready,false);
 await page.locator('.file-list').evaluate(e=>e.style.display='none');
 assert.equal((await fields())[0].value,'');
});
test('four year/month selects expose endpoints and date parts without inferring dates',async()=>{
 await page.setContent(`<div id="record"><div class="apply-field-X"><div class="title-X">就读时间</div>${['年','月','年','月'].map(p=>`<label class="sd-Select-container-X"><input placeholder="${p}"></label>`).join('')}</div></div>`);
 const fs=await fields();assert.deepEqual(fs.map(f=>[f.range_endpoint,f.date_part]),[['start','year'],['start','month'],['end','year'],['end','month']]);
 assert.ok(fs.every(f=>f.control_pattern==='year_month_range_parts'));
});
test('single Moka year/month pair keeps date parts after year selection clears placeholders',async()=>{
 await page.setContent('<div id="record"><div class="apply-field-X"><div class="title-X">预计毕业时间：</div><div class="month-range-select">'+['2027','1'].map(v=>`<label class="sd-Select-container-X"><span class="sd-Input-display-value-X">${v}</span><input placeholder=""></label>`).join('')+'</div></div></div>');
 const fs=await fields();assert.deepEqual(fs.map(f=>f.date_part),['year','month']);
 assert.deepEqual(fs.map(f=>f.value),['2027','1']);
 assert.deepEqual(fs.map(f=>f.label),['预计毕业时间： 年','预计毕业时间： 月']);
 assert.ok(fs.every(f=>f.range_endpoint===undefined));
});
test('filled Moka range keeps endpoints after placeholders disappear',async()=>{
 await page.setContent(`<div id="record"><div class="apply-field-X"><div class="month-range-select">${['2024','9','2027','7'].map(v=>`<label class="sd-Select-container-X"><span class="sd-Input-display-value-X">${v}</span><input placeholder=""></label>`).join('')}</div></div></div>`);
 const fs=await fields();assert.deepEqual(fs.map(f=>[f.range_endpoint,f.date_part]),[['start','year'],['start','month'],['end','year'],['end','month']]);
});
for(const precision of ['month','date'])test('Ant picker commits with '+precision+' precision and rejects the other precision',async()=>{
 await page.setContent(`<style>#owner{width:200px;height:30px}#calendar{position:absolute;top:38px;left:8px;width:200px}</style><div id="record"><div class="ant-form-item"><label id="label" class="ant-form-item-label">时间</label><div id="owner" class="ant-picker"><input id="field"></div></div></div><div id="calendar" class="ant-picker-dropdown" style="display:none"><div class="ant-picker-${precision}-panel"></div></div><script>(()=>{const calendar=document.querySelector('#calendar'),field=document.querySelector('#field');document.querySelector('#owner').onclick=()=>{calendar.style.display='block';field.focus()};field.onkeydown=e=>{if(e.key==='Enter'||e.key==='Escape')calendar.style.display='none'};document.querySelector('#label').onclick=()=>calendar.style.display='none';})();</script>`);
 const value=precision==='month'?'2027-07':'2027-07-01';
 await assert.rejects(apply(precision==='month'?'2027-07-01':'2027-07'),/matching_panel/);
 await apply(value);assert.equal((await fields())[0].value,value);assert.equal(await page.locator('#calendar').isVisible(),false);
});
test('nested Element form items inherit the outer div label and keep certificate halves distinct',async()=>{
 await page.setContent(`<div id="record"><div class="el-form-item"><div class="el-form-item__label">证件类型</div><div class="certificate-validate"><div class="el-form-item"><div class="el-select"><input id="kind" readonly placeholder="请选择"></div></div><div class="el-form-item"><input id="number" placeholder="请填写"></div></div></div></div>`);
 const fs=await fields();assert.deepEqual(fs.map(f=>f.label),['证件类型','证件号码']);
});
test('a hierarchical popup behind an ordinary select trigger is deferred before choosing a parent',async()=>{
 await page.setContent(fixture('moka-select'));await page.locator('#menu').evaluate(e=>e.classList.add('sd-Cascader-menuContainer-X'));
 await assert.rejects(apply('目标'),/requires_cascader_driver/);assert.equal(await page.locator('#value').textContent(),'');assert.equal(await page.locator('#menu').isVisible(),false);
});
