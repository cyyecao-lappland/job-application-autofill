/** Pure local snapshot comparison; no values leave the trusted executor. */
const comparable=f=>JSON.stringify([f.signature,f.kind,!!f.disabled,!!f.readonly,
  f.required,f.value,(f.options||[]).map(o=>[o.label,!!o.disabled])]);
export function detectLinkage(before,after,triggerId){
  const old=new Map(before.fields.map(f=>[f.id,f]));
  const now=new Map(after.fields.map(f=>[f.id,f]));
  const added=[],changed=[],removed=[];
  for(const [id,f] of now){
    if(id===triggerId)continue;
    if(!old.has(id))added.push(id);
    else if(comparable(old.get(id))!==comparable(f))changed.push(id);
  }
  for(const id of old.keys())if(id!==triggerId&&!now.has(id))removed.push(id);
  return added.length||changed.length||removed.length?{trigger_id:triggerId,added,changed,removed}:null;
}
