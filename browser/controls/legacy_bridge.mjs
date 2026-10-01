// Compatibility exports and old request value decoding only.
export {driver as administrativeRegion} from './adapters/administrative_region.mjs';
export {driver as elementDate} from './adapters/element_date.mjs';
export {driver as elementDateNow} from './adapters/element_date_now.mjs';
export {driver as nextRangeDate} from './adapters/next_range_date.mjs';

export function decodeLegacyTarget(codec, value) {
  const date = value => {
    if (typeof value !== 'string') throw new Error('invalid_legacy_date');
    const parts = value.split('-').map(Number);
    return {precision:parts.length === 2 ? 'month':'day', year:parts[0], month:parts[1], ...(parts.length === 3 ? {day:parts[2]}:{})};
  };
  if (codec === 'text') return {kind:'text', text:value};
  if (codec === 'choice') return {kind:'choice', cardinality:'single', choice:{label:value}};
  if (codec === 'date') return {kind:'date', value:date(value)};
  if (codec === 'date_range') return {kind:'date_range', start:date(value?.start),
    end:value?.current === true && value.end === null ? {kind:'current'}:{kind:'date', value:date(value?.end)}};
  if (codec === 'administrative_region' && typeof value === 'string')
    return {kind:'hierarchy', path:[{level:'full_path', label:value}]};
  const levels = codec === 'province_city' ? ['province','city']:['province','city','district'];
  return {kind:'hierarchy', path:levels.map(level => ({level, label:value?.[level]}))};
}
