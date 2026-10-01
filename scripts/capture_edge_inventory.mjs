import {connectEdgeCdp} from '../browser/cdp_connector.mjs';
import {inventoryPage} from '../browser/page_inventory.mjs';
import {writeFile} from 'node:fs/promises';
const [pageId,output]=process.argv.slice(2);
const c=await connectEdgeCdp({pageId});
try{
 const inventory=await inventoryPage(c.session);
 inventory.target={browser:'edge',browser_id:c.browserId,tab_id:c.session.tab.id,url:await c.session.tab.url()};
 await writeFile(output,JSON.stringify(inventory,null,2));
 console.log(JSON.stringify({status:'captured',sections:inventory.framework?.sections.map(s=>({label:s.label,records:s.records.length,record_selector:s.record_selector}))}));
}finally{await c.closeConnection()}
