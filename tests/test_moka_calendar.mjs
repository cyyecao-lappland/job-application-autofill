import test,{before,after} from 'node:test';
import assert from 'node:assert/strict';
import {chromium} from 'playwright-core';
import {createNativePlaywrightSession} from '../browser/playwright_backend.mjs';
import {readModuleDOM} from '../browser/rules/dom.mjs';
import {readMokaCalendar} from '../browser/rules/moka_calendar.mjs';
import {executeFieldControl} from '../browser/controls/service.mjs';
let browser,page,tab;
before(async()=>{browser=await chromium.launch({channel:'msedge',headless:true});page=await browser.newPage();({tab}=createNativePlaywrightSession(page,{browserId:'synthetic',tabId:'synthetic'}));});
after(async()=>{await browser?.close();});
const html=`<div id="record"><div class="apply-field-FIXTURE"><div class="title-FIXTURE" id="title">出生日期</div><div class="sd-Dropdown-container-FIXTURE" id="dropdown"><label class="day_info" id="owner"><input id="field" readonly></label><span id="menu" style="display:none">2026年9月<button>下一年</button></span></div></div></div><script>document.querySelector('#owner').onclick=()=>document.querySelector('#menu').style.display='block';document.querySelector('#title').onclick=()=>document.querySelector('#menu').style.display='none';</script>`;
async function apply(value){const field=(await page.evaluate(readModuleDOM,{moduleId:'m',moduleSelector:'#record',capture:{}})).fields[0];let actions=0;
 await executeFieldControl({tab,packet:{module_selector:'#record'},field,op:{value},end:Date.now()+5000,markAction:()=>actions++,readField:async()=>({field})});return actions;}
test('Moka calendar probe records its local popup and explicitly leaves it open without changing value',async()=>{
 await page.setContent(html);
 await assert.rejects(apply('2002-02-23'),e=>{assert.equal(e.message,'moka_calendar_variant_requires_driver');assert.equal(e.controlDiagnostic.popup_candidates[0].selector,'#menu');assert.equal(e.controlDiagnostic.closure_confirmed,false);assert.equal(e.controlDiagnostic.cleanup_attempted,false);return true;});
 assert.equal(await page.locator('#field').inputValue(),'');assert.equal(await page.locator('#menu').isVisible(),true);
});
test('Moka calendar matching committed date causes zero actions',async()=>{
 await page.setContent(html);await page.locator('#field').evaluate(e=>e.value='2002-02-23');await assert.rejects(apply('2002-02-23'),/moka_calendar_readback_only/);assert.equal(await page.locator('#menu').isVisible(),false);
});
test('Moka calendar rejects disabled input, invalid day and ambiguous sibling date owners before opening',async()=>{
 await page.setContent(html);await page.locator('#field').evaluate(e=>e.disabled=true);await assert.rejects(apply('2002-02-23'),/date_input_disabled|unknown_control_method/);
 await page.setContent(html);await assert.rejects(apply('2002-02-31'),/confirmed_day_date_required/);assert.equal(await page.locator('#menu').isVisible(),false);
 await page.locator('#dropdown').evaluate(e=>e.insertAdjacentHTML('beforeend','<label class="day_info"><input readonly></label>'));
 assert.equal((await page.evaluate(readMokaCalendar,{moduleSelector:'#record',selector:'#field'})).error,'moka_calendar_owner_ambiguous');
 await assert.rejects(apply('2002-02-23'),/moka_calendar_owner_ambiguous/);assert.equal(await page.locator('#menu').isVisible(),false);
});
test('A foreign calendar outside the structural dropdown is not owned',async()=>{
 await page.setContent(html);await page.evaluate(()=>{document.body.append(document.querySelector('#menu'));document.querySelector('#menu').style.display='block';});
 const state=await page.evaluate(readMokaCalendar,{moduleSelector:'#record',selector:'#field'});
 assert.deepEqual(state.popups,[]);
});
test('An owner outside the module and unrelated helper text cannot establish an owned calendar',async()=>{
 await page.setContent(html);
 assert.equal((await page.evaluate(readMokaCalendar,{moduleSelector:'#owner',selector:'#field'})).error,'moka_calendar_owner_outside_module');
 await page.locator('#dropdown').evaluate(e=>e.insertAdjacentHTML('beforeend','<div id="helper">请选择出生日期</div>'));
 let state=await page.evaluate(readMokaCalendar,{moduleSelector:'#record',selector:'#field'});
 assert.deepEqual(state.popups,[]);assert.equal(state.popup_candidates[0].selector,'#helper');assert.equal(state.committed,false);
 await page.locator('#helper').evaluate(e=>e.style.visibility='collapse');
 state=await page.evaluate(readMokaCalendar,{moduleSelector:'#record',selector:'#field'});assert.deepEqual(state.popup_candidates,[]);
});
test('Moka calendar owner changing after opening is deferred without cleanup or date selection',async()=>{
 await page.setContent(html);await page.evaluate(()=>{document.querySelector('#owner').onclick=()=>{document.querySelector('#menu').style.display='block';document.querySelector('#owner').id='changed';};});
 await assert.rejects(apply('2002-02-23'),/moka_calendar_owner_changed/);
 assert.equal(await page.locator('#field').inputValue(),'');
});

const monthGrid=`<div id="record"><div class="apply-field-FIXTURE"><div class="sd-Dropdown-container-FIXTURE" id="dropdown"><label class="day_info" id="owner"><input id="field" readonly></label><span id="menu" style="display:none"><div class="sd-Dropdown-dropdown-FIXTURE"><div class="sd-panal-menu-wrapper-FIXTURE"><div class="sd-basic-selector-FIXTURE"><div id="prev"><span class="sd-Icon-icondoubleLeft-FIXTURE">‹</span></div><div><span class="sd-basic-selector-year-FIXTURE" id="year">1990年</span></div><div id="next"><span class="sd-Icon-icondoubleRight-FIXTURE">›</span></div></div><div id="months">${['一月','二月','三月','四月','五月','六月','七月','八月','九月','十月','十一月','十二月'].map((m,i)=>`<div class="sd-basic-year-item-FIXTURE" data-month="${i+1}">${m}</div>`).join('')}</div></div></div></span></div></div></div><script>
document.querySelector('#owner').onclick=()=>document.querySelector('#menu').style.display='block';
for(const [id,delta] of [['prev',-1],['next',1]])document.querySelector('#'+id).onclick=()=>{const e=document.querySelector('#year');e.textContent=(Number(e.textContent.slice(0,4))+delta)+'年';};
document.querySelector('#months').onclick=e=>{if(!e.target.dataset.month)return;document.querySelector('#field').value=document.querySelector('#year').textContent.slice(0,4)+'-'+e.target.dataset.month.padStart(2,'0');document.querySelector('#menu').style.display='none';};
</script>`;
test('The observed Moka month grid confirms each year navigation and reports month precision without a full-date success',async()=>{
 await page.setContent(monthGrid);
 await assert.rejects(apply('2002-02-23'),e=>{assert.equal(e.message,'moka_calendar_month_precision_observed');assert.equal(e.controlDiagnostic.actual,'2002-02');assert.equal(e.controlDiagnostic.closure_confirmed,true);return true;});
 assert.equal(await page.locator('#year').textContent(),'2002年');assert.equal(await page.locator('#field').inputValue(),'2002-02');assert.equal(await page.locator('#menu').isVisible(),false);
});
test('A year navigation jump or unavailable month stops before selecting a month',async()=>{
 await page.setContent(monthGrid);await page.evaluate(()=>{document.querySelector('#next').onclick=()=>document.querySelector('#year').textContent='1992年';});
 await assert.rejects(apply('2002-02-23'),/moka_calendar_year_navigation_unconfirmed/);assert.equal(await page.locator('#field').inputValue(),'');
 await page.setContent(monthGrid);await page.evaluate(()=>{document.querySelector('#year').textContent='2002年';document.querySelector('[data-month="2"]').setAttribute('aria-disabled','true');});
 await assert.rejects(apply('2002-02-23'),/moka_calendar_month_unavailable/);assert.equal(await page.locator('#field').inputValue(),'');
});
test('A month-precision field fills and rereads only the requested month, including its computed-age display',async()=>{
 await page.setContent(monthGrid);
 await page.locator('#record').evaluate(e=>e.insertAdjacentHTML('afterbegin','<div class="title-FIXTURE">出生日期 (年龄)</div>'));
 // The live variant derives its label from the apply-field title.
 await page.locator('.apply-field-FIXTURE').evaluate(e=>e.insertAdjacentHTML('afterbegin','<div class="title-FIXTURE">出生日期 (年龄)</div>'));
 await page.evaluate(()=>{document.querySelector('#months').onclick=e=>{if(!e.target.dataset.month)return;const input=document.querySelector('#field');input.value=document.querySelector('#year').textContent.slice(0,4)+'-'+e.target.dataset.month.padStart(2,'0')+' (24岁)';input.removeAttribute('placeholder');document.querySelector('#menu').style.display='none';};});
 await apply('2002-02');
 const field=(await page.evaluate(readModuleDOM,{moduleId:'m',moduleSelector:'#record',capture:{}})).fields[0];
 assert.equal(field.value,'2002-02');assert.equal(field.date_precision,'month');
 assert.equal(await page.locator('#menu').isVisible(),false);
 assert.equal(await apply('2002-02'),0);
});
test('The birth-month field keeps a structural selector when its placeholder disappears',async()=>{
 await page.setContent(monthGrid);
 await page.locator('.apply-field-FIXTURE').evaluate(e=>e.insertAdjacentHTML('afterbegin','<div class="title-FIXTURE">出生日期 (年龄)</div>'));
 await page.locator('#field').evaluate(e=>{e.id='';e.placeholder='出生日期 (年龄)';});
 const before=(await page.evaluate(readModuleDOM,{moduleId:'m',moduleSelector:'#record',capture:{}})).fields[0];
 await page.locator('#owner input').evaluate(e=>{e.placeholder='';e.value='2002-02 (24岁)';});
 const after=(await page.evaluate(readModuleDOM,{moduleId:'m',moduleSelector:'#record',capture:{}})).fields[0];
 assert.equal(before.id,after.id);assert.equal(before.selector,after.selector);assert.equal(after.value,'2002-02');
});
