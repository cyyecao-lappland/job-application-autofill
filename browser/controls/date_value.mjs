export function parseISODate(value){
  const match=/^(\d{4})-(\d{2})-(\d{2})$/.exec(String(value));
  if(!match)throw new Error('invalid_iso_date');
  const year=Number(match[1]),month=Number(match[2]),day=Number(match[3]);
  const date=new Date(Date.UTC(year,month-1,day));
  if(date.getUTCFullYear()!==year||date.getUTCMonth()!==month-1||date.getUTCDate()!==day)throw new Error('invalid_iso_date');
  return {year,month,day};
}
