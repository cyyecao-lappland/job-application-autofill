/** Read-only page inventory. Component structure identifies ownership, never answers. */
export function readPageInventory() {
  const visible = el => {
    if(!el.getClientRects().length||getComputedStyle(el).visibility==='hidden')return false;
    for(let p=el;p;p=p.parentElement){
      const style=getComputedStyle(p),r=p.getBoundingClientRect();
      if(/hidden|clip/.test(style.overflow)&&(!r.width||!r.height))return false;
    }
    return true;
  };
  const controls = 'input:not([type="hidden"]),textarea,select,[contenteditable="true"]';
  const path = el => {
    if (el.id && document.querySelectorAll('#'+CSS.escape(el.id)).length === 1) return '#'+CSS.escape(el.id);
    const parts=[];
    while(el && el!==document.body) {
      const siblings=[...el.parentElement.children].filter(x=>x.tagName===el.tagName);
      parts.unshift(el.tagName.toLowerCase()+`:nth-of-type(${siblings.indexOf(el)+1})`);
      el=el.parentElement;
    }
    return 'body > '+parts.join(' > ');
  };
  const roots=[...document.querySelectorAll('[class*="FormCard--formCard--"],form')]
    .filter(visible).filter(el=>!el.parentElement.closest('[class*="FormCard--formCard--"],form'));
  const owned=new Set();
  const scopes=roots.map((root,index)=>{
    const selector=path(root), all=[...root.querySelectorAll(controls)].filter(el=>visible(el)||el.type==='file');
    all.forEach(el=>owned.add(el));
    const label=(root.querySelector('[class*="formCardHeader"],legend,h2,h3')?.textContent||root.getAttribute('aria-label')||`表单 ${index+1}`).trim();
    const buttons=[...root.querySelectorAll('button,[role="button"],[class*="PCButton--pcButton--"]')].filter(visible);
    const save=buttons.filter(el=>/^(保存|保存并下一步|下一步并保存|下一步)$/.test(el.textContent.trim()));
    const edit=buttons.find(el=>el.textContent.trim()==='编辑');
    const groups=[...root.querySelectorAll('.table-field-layout-tiled')].filter(visible).map(group=>{
      let container=group.parentElement;
      while(container!==root && !container.querySelector('button'))container=container.parentElement;
      const adds=[...container.querySelectorAll('button')].filter(el=>visible(el)&&/^添加/.test(el.textContent.trim()));
      const rows=[...group.querySelectorAll(':scope > .table-field-tiled-item')];
      return {selector:path(group), container_selector:path(container),
        add_selector:adds.length===1?path(adds[0]):null,add_label:adds.length===1?adds[0].textContent.trim():null,
        label:adds.length===1?adds[0].textContent.trim().replace(/^添加(更多)?/,''):'',
        row_selector:':scope > .table-field-tiled-item', rows:rows.map(row=>({selector:path(row)}))};
    });
    const units=[];
    for(const el of all) {
      const group=groups.find(g=>document.querySelector(g.selector).contains(el));
      if(group){if(!units.some(u=>u.group===group.selector))units.push({group:group.selector});continue;}
      const item=el.closest('.next-form-item,.ant-form-item,.el-form-item')||el.parentElement;
      if(!units.some(u=>u.selector===path(item)))units.push({selector:path(item),label:(item.querySelector('label')?.textContent||el.getAttribute('aria-label')||el.name||el.id).trim()});
    }
    return {id:`scope-${index}`,selector,label,page_order:index,groups,units,field_count:all.length,
      save_selector:save.length===1?path(save[0]):null,save_label:save.length===1?save[0].textContent.trim():null,
      edit_selector:edit?path(edit):null,editor_selector:root.matches('form')?selector:selector+' form',
      state:all.length?'editing':edit?'collapsed':'empty'};
  });
  return {version:1,url:location.href,observed_at:Date.now()/1000,scopes,
    unowned_controls:[...document.querySelectorAll(controls)].filter(visible).filter(el=>!owned.has(el)).map(el=>({selector:path(el),label:el.getAttribute('aria-label')||el.name||el.id||''})),
    unsupported_frames:[...document.querySelectorAll('iframe')].filter(visible).map(path)};
}

export async function inventoryPage(session) {
  const inventory=await session.tab.playwright.evaluate(readPageInventory);
  const {readFrameworkSections}=await import('./rules/page_sections.mjs');
  inventory.framework=await session.tab.playwright.evaluate(readFrameworkSections);
  // Reuse the same control reader as the writer so coverage counts agree.
  const {readModuleDOM}=await import('./control_detection.mjs');
  for(const scope of inventory.scopes) {
    for(const group of scope.groups) for(const row of group.rows)
      row.snapshot=await session.tab.playwright.evaluate(readModuleDOM,{moduleSelector:row.selector});
  }
  for(const section of inventory.framework?.sections||[]){
    section.snapshot=await session.tab.playwright.evaluate(readModuleDOM,{moduleSelector:section.selector});
    for(const row of section.records)row.snapshot=await session.tab.playwright.evaluate(readModuleDOM,{moduleSelector:row.selector});
  }
  return inventory;
}

export async function isolatePriorScopes(session, manifest, blockers) {
  const blocks=await session.tab.playwright.evaluate(({scopes,blockers})=>blockers.map(blocker=>{
    const roots=blocker.module_selector?[...document.querySelectorAll(blocker.module_selector)]:[];
    return {command_id:blocker.command_id,selector:blocker.module_selector,unique:roots.length===1,
      scope_relations:Object.fromEntries(scopes.map(scope=>{
        const nodes=[...document.querySelectorAll(scope.selector)];
        const node=nodes[0],old=roots[0];
        const relation=roots.length!==1||nodes.length!==1?'unknown':
          node.contains(old)?'contained':old.contains(node)?'overlap':'disjoint';
        return [scope.id,relation];
      }))};
  }),{scopes:manifest.save_scopes,blockers});
  for(const module of manifest.modules)if(blocks.some(b=>b.scope_relations[module.save_scope]==='contained'))module.hold_reason='prior_save_unknown';
  manifest.scope_isolation={target:manifest.target,observed_at:Date.now()/1000,blocks};
}
