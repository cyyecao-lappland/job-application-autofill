/** Local synthetic forms only. Never connects to the user's logged-in browser. */
import assert from 'node:assert/strict';
import {chromium} from 'playwright-core';
import {writeFile} from 'node:fs/promises';
import {createNativePlaywrightSession} from '../browser/playwright_backend.mjs';
import {executeControl, registryReport, matchControl} from '../browser/controls/service.mjs';
import {readModuleDOM} from '../browser/control_detection.mjs';

const browser=await chromium.launch({channel:'msedge',headless:true});
const rows=[];
try {
  const page=await browser.newPage();
  const {tab}=createNativePlaywrightSession(page,{browserId:'local-test',tabId:'local-test'});
  const layouts=['div','section','article','fieldset'];
  const states=['empty','existing','already_correct','enabled','rerender','duplicate_label'];
  const input='<label for="field">同名字段</label><input id="field" type="text" name="value">';
  for(const kind of ['text','region'])for(const [layoutIndex,tag] of layouts.entries())for(const state of states){
    let html=`<div id="other"><label>同名字段<input name="value" value="other record"></label></div><${tag} id="record"><div><span>${input}</span></div></${tag}>`;
    if(kind==='region')html+=`<div id="dialog" role="dialog" style="display:none"><h2>${layoutIndex%2?'地区选择':'城市选择'}</h2>
      <select id="province" aria-label="省份"><option>省</option><option>浙江省</option></select>
      <select id="city" aria-label="城市"><option>市</option></select><button id="confirm">确定</button></div>
      <script>
      document.querySelector('#field').setAttribute('onclick',"document.querySelector('#dialog').style.display='block'");
      document.querySelector('#province').onchange=()=>{const city=document.querySelector('#city');city.disabled=true;city.innerHTML='<option>市</option>';setTimeout(()=>{city.innerHTML='<option>市</option><option>杭州市</option>';city.disabled=false;},75);};
      document.querySelector('#confirm').onclick=()=>{document.querySelector('#field').value='浙江省杭州市';document.querySelector('#dialog').style.display='none';};
      </script>`;
    await page.setContent(html);
    const wanted=kind==='text'?'测试字段值':'浙江省杭州市';
    if(state==='existing')await page.locator('#field').fill('old');
    if(state==='already_correct')await page.locator('#field').fill(wanted);
    if(state==='enabled'){
      await page.locator('#field').evaluate(el=>{el.disabled=true;});
      await page.locator('#field').evaluate(el=>{el.disabled=false;});
    }
    const before=await page.locator('#field').inputValue();
    if(state==='rerender')await page.locator('#field').evaluate(el=>el.replaceWith(el.cloneNode(true)));
    const events=[];
    const result=await executeControl(tab,{command_id:`${kind}-${layoutIndex}-${state}`,adapter:kind==='text'?'plain_text_probe_v1':'province_city_dialog_v1',
      module_selector:'#record',field_selector:'input[name="value"]',field_label:'同名字段',allow_logical_selector:true,before_value:before,
      deadline:Date.now()/1000+8,controlRegistry:registryReport(),controlTarget:kind==='text'?{kind:'text',text:wanted}:
        {kind:'hierarchy',path:[{level:'province',label:'浙江省'},{level:'city',label:'杭州市'}]}},event=>events.push(event));
    assert.equal(result.verification,'match');
    assert.equal(await page.locator('#other input').inputValue(),'other record');
    if(state==='already_correct')assert.equal(events.length,0);
    rows.push({kind,layout:tag,state,verification:result.verification,actions:events.length});
  }

  // A new capability joins the catalog; no adapter-name branch is added to the graph.
  for(const state of ['async','rerender','duplicate_options','unreadable','already_correct','two_records']){
    await page.setContent(`<div id="record"><input type="text" id="search" role="combobox" aria-autocomplete="list"
      aria-controls="menu" aria-expanded="false" aria-valuetext=""></div><ul id="menu" role="listbox" style="display:none"></ul>
      <input id="unrelated" value="other record">
      <script>(()=>{
      const input=document.querySelector('#search');let menu=document.querySelector('#menu');
      input.onclick=()=>{input.setAttribute('aria-expanded','true');menu.style.display='block';};
      input.oninput=()=>{menu.setAttribute('aria-busy','true');setTimeout(()=>{
        ${state==='rerender'?"const replacement=menu.cloneNode(true);menu.replaceWith(replacement);menu=replacement;":''}
        menu.innerHTML='<li role="option">示例大学</li>${state==='duplicate_options'?'<li role="option">示例大学</li>':''}';
        menu.setAttribute('aria-busy','false');
        for(const li of menu.children)li.onclick=()=>{input.value=li.textContent;${state==='unreadable'?'input.removeAttribute("aria-valuetext");':'input.setAttribute("aria-valuetext",li.textContent);'}input.setAttribute('aria-expanded','false');menu.style.display='none';};
      },70);};
      })();</script>`);
    if(state==='already_correct')await page.locator('#search').evaluate(el=>{el.value='示例大学';el.setAttribute('aria-valuetext','示例大学');});
    const dom=await page.evaluate(readModuleDOM,{moduleSelector:'#record'});
    const field=dom.fields[0];
    assert.equal(matchControl(field),'aria_search_select_v1');
    const promise=executeControl(tab,{command_id:`search-${state}`,adapter:matchControl(field),controlEvidence:field,
      module_selector:'#record',field_selector:'#search',before_value:field.value,deadline:Date.now()/1000+4,
      controlTarget:{kind:'choice',cardinality:'single',choice:{label:'示例大学'}}});
    if(state==='duplicate_options'){
      await assert.rejects(promise,/OPTION_AMBIGUOUS/);rows.push({kind:'search',state,result:'correct_rejection'});
    }else{
      const result=await promise;
      assert.equal(result.verification,state==='unreadable'?'skipped':'match');
      assert.equal(await page.locator('#unrelated').inputValue(),'other record');
      rows.push({kind:'search',state,verification:result.verification});
    }
  }

  for(const state of ['user_modified','ambiguous_record','composite','masked']){
    await page.setContent(`<div class="record" id="record">${input}</div>${state==='ambiguous_record'?`<div class="record">${input.replaceAll('id="field"','id="second"')}</div>`:''}
      <div id="dialog" role="dialog" style="display:none">选择</div>`);
    if(state==='user_modified')await page.locator('#field').fill('user edit');
    if(state==='composite')await page.locator('#field').evaluate(el=>el.onfocus=()=>{document.querySelector('#dialog').style.display='block';});
    if(state==='masked')await page.locator('#field').evaluate(el=>el.onblur=()=>{el.value='***';});
    const promise=executeControl(tab,{command_id:state,adapter:'plain_text_probe_v1',module_selector:state==='ambiguous_record'?'.record':'#record',
      field_selector:'#field',field_label:'同名字段',allow_logical_selector:true,before_value:'',deadline:Date.now()/1000+4,
      controlTarget:{kind:'text',text:'target'}});
    if(state==='masked'){
      const result=await promise;assert.equal(result.verification,'skipped');assert.equal(result.committed,false);
      rows.push({kind:'text',state,verification:'skipped'});
    }else{
      await assert.rejects(promise,/control_user_value_changed|text_probe_discovered_composite_control/);
      assert.equal(await page.locator('#field').inputValue(),state==='user_modified'?'user edit':'');
      rows.push({kind:'text',state,result:'correct_rejection'});
    }
  }
  const summary={environment:'local headless Edge; synthetic HTML, no production site',
    textRegionScenarios:48,searchScenarios:6,additionalScenarios:4,total:rows.length,
    match:rows.filter(r=>r.verification==='match').length,skipped:rows.filter(r=>r.verification==='skipped').length,
    correctRejection:rows.filter(r=>r.result==='correct_rejection').length,rows};
  const output=process.argv.find(arg=>arg.startsWith('--output='))?.slice(9);
  if(output)await writeFile(output,JSON.stringify(summary,null,2)+'\n');
  console.log(JSON.stringify({...summary,rows:undefined},null,2));
} finally {await browser.close();}
