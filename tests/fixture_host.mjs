// Synthetic browser for Python -> packet -> JS executor -> journal -> graph integration.
// This is NOT a live Edge connection and must never be reported as a website test.
import {readFile} from 'node:fs/promises';
import {join} from 'node:path';
import {runRequest} from '../browser/edge_executor.mjs';
const directory=process.argv[2];
const {command}=JSON.parse(await readFile(join(directory,'request.json'),'utf8'));
const snapshot=JSON.parse(await readFile(join(directory,'fixture-snapshot.json'),'utf8'));
const fields=structuredClone(snapshot.fields);
let signal='';
const locator=selector=>({
  locator:child=>locator(child),
  count:async()=>selector==='#saved'&&!signal?0:1,
  isVisible:async()=>true,
  innerText:async()=>selector==='#save'?'保存':signal,
  waitFor:async()=>{},
  fill:async value=>{fields.find(f=>f.selector===selector).value=value;},
  click:async()=>{signal='保存成功';},
});
const tab={id:command.target.tab_id,url:async()=>command.target.url,playwright:{
  locator,
  evaluate:async()=>({url:command.target.url,fields,save:snapshot.save})
}};
const result=await runRequest({browserId:command.target.browser_id},tab,directory);
console.log(JSON.stringify({...result,synthetic_browser:true}));
