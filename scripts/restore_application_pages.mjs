/** Restore observed application URLs after the user reopened the original browser. */
import {chromium} from 'playwright-core';
import {readFile, writeFile, mkdir} from 'node:fs/promises';
import {describeCdpPages, DEFAULT_CDP_ENDPOINT} from '../browser/cdp_connector.mjs';
const root='private/mass-apply-20260930';
const inventory=JSON.parse(await readFile(`${root}/open-pages/inventory.json`,'utf8'));
const browser=await chromium.connectOverCDP(DEFAULT_CDP_ENDPOINT);
try {
 const context=browser.contexts()[0];
 const existing=await describeCdpPages(browser);
 const restored=[];
 for(const old of inventory.pages){
  if(!old.url.includes('/apply') && !old.url.includes('resume'))continue;
  let page=existing.find(p=>p.url===old.url)?.page;
  if(!page){page=await context.newPage();try{await page.goto(old.url,{waitUntil:'domcontentloaded',timeout:20000});}catch(error){console.log(JSON.stringify({url:old.url,navigation:error.name}));}}
  const current=(await describeCdpPages(browser)).find(p=>p.page===page);
  restored.push({previous_page_id:old.pageId,pageId:current.pageId,url:page.url(),original_url:old.url,title:await page.title()});
  await mkdir(`${root}/recovery`,{recursive:true});
  await writeFile(`${root}/recovery/restored-pages.json`,JSON.stringify(restored,null,2));
  console.log(JSON.stringify(restored.at(-1)));
 }
}finally{await browser.close();}
