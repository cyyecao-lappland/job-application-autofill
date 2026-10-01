/** Observe the official job's application entry, with a persisted one-time navigation journal. */
import {chromium} from 'playwright-core';
import {mkdir,writeFile,readFile} from 'node:fs/promises';
import {describeCdpPages,DEFAULT_CDP_ENDPOINT} from '../browser/cdp_connector.mjs';
const folder='private/mass-apply-20260930/recovery/ctrip';
await mkdir(folder,{recursive:true});
const match=JSON.parse(await readFile('private/mass-apply-20260930/discovery/ctrip-llm-algorithm-detail-live-20260930.json','utf8'));
if(match.qualification?.overall?.suitable_to_submit_existing_resume!==true)throw new Error('qualification_blocked');
const browser=await chromium.connectOverCDP(DEFAULT_CDP_ENDPOINT);
try{
 const pages=await describeCdpPages(browser);
 const detail=pages.filter(p=>p.url.includes('/campus/job-detail/MJ036605'));
 if(detail.length!==1)throw new Error('job_detail_not_unique');
 const page=detail[0].page;
 const button=page.getByText('立即申请',{exact:true});
 if(await button.count()!==1||!await button.isVisible()||!await button.isEnabled())throw new Error('observed_entry_not_available');
 const before={pageId:detail[0].pageId,url:page.url(),text:await page.locator('body').innerText()};
 if(!before.text.includes('LLM算法工程师（2027届秋招）'))throw new Error('wrong_job');
 const journal={kind:'open_application_entry',job_id:'MJ036605',status:'pending',before,started_at:new Date().toISOString()};
 const path=`${folder}/entry-journal.json`;
 await writeFile(path,JSON.stringify(journal,null,2),{flag:'wx'});
 try{await button.click({timeout:15000});journal.call_returned=true;}catch(error){journal.call_returned=false;journal.error=error.name;}
 await page.waitForTimeout(1500);
 journal.after={url:page.url(),text:await page.locator('body').innerText(),pages:(await describeCdpPages(browser)).map(({page,...p})=>p)};
 journal.status=journal.call_returned?'entry_observed':'entry_unknown';
 await writeFile(path,JSON.stringify(journal,null,2));
 console.log(JSON.stringify({status:journal.status,url:journal.after.url,text:journal.after.text.slice(0,5000)}));
}finally{await browser.close();}
