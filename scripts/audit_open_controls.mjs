/** Read-only inventory of the existing Edge tabs. Never navigates or fills. */
import {chromium} from 'playwright-core';
import {mkdir,writeFile} from 'node:fs/promises';
import {resolve} from 'node:path';
import {describeCdpPages,DEFAULT_CDP_ENDPOINT} from '../browser/cdp_connector.mjs';
import {readModuleDOM} from '../browser/rules/dom.mjs';
import {matchRegistered} from '../browser/rules/service.mjs';
import {entries} from '../browser/controls/registry.generated.mjs';
import {registryReport} from '../browser/controls/service.mjs';

const output=resolve(process.argv[2]||'private/open-controls-20260929');
await mkdir(output,{recursive:true});
const browser=await chromium.connectOverCDP(DEFAULT_CDP_ENDPOINT,{timeout:10000});
const summary={observed_at:new Date().toISOString(),endpoint:DEFAULT_CDP_ENDPOINT,registry:registryReport(),pages:[]};
try{
 for(const {page,...identity} of await describeCdpPages(browser)){
  try{
   const snapshot=await page.evaluate(readModuleDOM,{moduleSelector:'body'});
   const structure=await page.evaluate(()=>{
    const visible=e=>!!e.getClientRects().length&&getComputedStyle(e).visibility!=='hidden';
    return {title:document.title,text:document.body?.innerText.slice(0,30000),
      controls:[...document.querySelectorAll('input,textarea,select,[role=combobox],[role=radio],[role=checkbox],[contenteditable=true],.phoenix-radio,.ud__radio-group')].filter(e=>visible(e)||e.type==='file').map(e=>({
       tag:e.tagName,type:e.type,role:e.getAttribute('role'),html:e.outerHTML.slice(0,2500),ancestors:(()=>{const a=[];for(let p=e.parentElement;p&&p!==document.body&&a.length<5;p=p.parentElement)a.push({tag:p.tagName,cls:p.className,text:p.innerText?.slice(0,180)});return a;})()
      })),frames:[...document.querySelectorAll('iframe')].map(e=>({src:e.src,title:e.title,visible:visible(e)}))};
   });
   const fields=snapshot.fields||[];
   const audited=fields.map(f=>{try{return {...f,adapter:matchRegistered(f,entries,'field')?.config.name||null};}catch(error){return {...f,adapter:null,recognition_error:error.message};}});
   const families={};for(const f of audited){const key=f.component||f.kind;families[key]=(families[key]||0)+1;}
   const row={...identity,fields:fields.length,recognized:audited.filter(f=>f.adapter).length,unnamed:audited.filter(f=>!f.label).length,families,
      unsupported:audited.filter(f=>!f.adapter).map(({label,kind,component,readonly,selector})=>({label,kind,component,readonly,selector})),frames:structure.frames};
   await writeFile(resolve(output,identity.pageId+'.json'),JSON.stringify({...identity,observed_at:new Date().toISOString(),snapshot:{...snapshot,fields:audited},structure},null,2));
   summary.pages.push(row);console.log(JSON.stringify({title:row.title,pageId:row.pageId,fields:row.fields,recognized:row.recognized,families}));
  }catch(error){summary.pages.push({...identity,error:error.message});console.log(JSON.stringify({pageId:identity.pageId,error:error.message}));}
  await writeFile(resolve(output,'inventory.json'),JSON.stringify(summary,null,2));
 }
}finally{await browser.close();}
