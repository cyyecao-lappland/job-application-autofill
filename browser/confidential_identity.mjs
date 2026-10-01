import {readFile} from 'node:fs/promises';

export const identityField = field => field?.protected===true && field.kind==='text' &&
  /^(?:身份证(?:件)?号(?:码)?|证件号(?:码)?)$/.test(String(field.label).replace(/[\s*：:]/g,''));

export async function identityValue(){
  let config;
  try {config=JSON.parse(await readFile(new URL('../private/local-config.json',import.meta.url),'utf8'));}
  catch {throw new Error('identity_config_unavailable');}
  if(config.identity_document_fill!==true||!config.profile)throw new Error('identity_fill_not_authorized');
  let value;
  try {value=JSON.parse(await readFile(config.profile,'utf8')).identity?.identity_document_number;}
  catch {throw new Error('identity_source_unavailable');}
  if(typeof value!=='string'||!/^\d{17}[\dXx]$/.test(value))throw new Error('identity_source_invalid');
  return value;
}

// Only booleans cross back from the page. No identity value in snapshots/receipts.
export function readIdentityMatch({moduleSelector,selector,expected}){
  const roots=[...document.querySelectorAll(moduleSelector)];
  if(roots.length!==1)return {unique:false};
  const fields=[...roots[0].querySelectorAll(selector)];
  if(fields.length!==1)return {unique:false};
  return {unique:true,present:!!fields[0].value,match:fields[0].value===expected};
}
