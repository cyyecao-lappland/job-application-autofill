/** Search/select operation. No business labels, profile access, or LLM calls. */
export class SearchSelectError extends Error {}

const normal=value=>String(value).normalize('NFKC').trim().replace(/\s+/g,' ');

export function optionMatches(label,value){
  if(normal(label)===normal(value))return true;
  // Country dial-code labels may include a country name; never use substring matching.
  if(!/^\+\d{1,4}$/.test(normal(value)))return false;
  const codes=normal(label).match(/\+\d+/g)||[];
  return codes.length===1&&codes[0]===normal(value);
}

export async function searchOwnedOptions({query, searchText=query, fill, readMenu, deadline,
  pause=ms=>new Promise(resolve=>setTimeout(resolve,ms)), now=()=>Date.now()}){
  if(typeof query!=='string'||!query.trim()||query==='__FIRST_ENABLED_OPTION__')
    throw new SearchSelectError('search_query_required');
  if(now()>=deadline)throw new SearchSelectError('search_options_timeout');
  await fill(searchText);
  let lastMenu=null;
  // Re-read menu ownership and selectors after every asynchronous update.
  // A menu may be created only after typing, or replaced while results load.
  while(now()<deadline){
    const menu=await readMenu();
    lastMenu=menu;
    if(menu.error&&menu.error!=='popup_not_unique_or_not_loaded')
      throw new SearchSelectError(menu.error);
    if(!menu.error&&!menu.busy){
      const matches=(menu.options||[]).filter(o=>!o.disabled&&optionMatches(o.label,query));
      if(matches.length>1)throw new SearchSelectError('search_option_ambiguous');
      if(matches.length===1)return menu;
    }
    await pause(Math.min(60,Math.max(1,deadline-now())));
  }
  const error=new SearchSelectError('search_options_timeout');
  error.menu=lastMenu;
  throw error;
}
