/** Compatibility wrappers preserve the existing interaction journal and readback. */
export function makeDriver(declaration, run, encode, detect = () => 'compatible') {
  return Object.freeze({
    declaration: Object.freeze(declaration),
    detect,
    async prepare(ctx, packet, target) {
      if (Date.now() >= packet.deadline * 1000) throw new Error('control_deadline');
      return {...packet, location: encode(target), return_verification: true};
    },
    async apply(ctx, packet) { return run(ctx.tab, packet, ctx.checkpoint); },
    async read(ctx, packet, applied) {
      // Migrated implementations supply actual + verification from their final read.
      // Legacy implementations retain their own verified readback contract.
      return {actual: applied.actual ?? applied.value ?? null,
        verification: applied.verification ?? (applied.committed === true ? 'match' : 'skipped'),
        reason: applied.reason ?? '', applied};
    },
    compare(observation) { return observation.verification; }
  });
}

export function textValue(target) {
  if (target?.kind !== 'text' || typeof target.text !== 'string') throw new Error('invalid_text_target');
  return target.text;
}

export function hierarchyValue(target, levels) {
  if (target?.kind !== 'hierarchy' || !Array.isArray(target.path) || target.path.length !== levels.length ||
      target.path.some((part, i) => part.level !== levels[i] || typeof part.label !== 'string' || !part.label.trim()))
    throw new Error('invalid_hierarchy_target');
  return Object.fromEntries(target.path.map(part => [part.level, part.label]));
}

export function isoDate(value, requiredPrecision = 'day') {
  const keys = {year:['year'], month:['year','month'], day:['year','month','day']}[value?.precision];
  if (!keys || keys.some(k => !Number.isInteger(value[k])) || value.year < 1 || value.year > 9999 ||
      (keys.includes('month') && (value.month < 1 || value.month > 12)) ||
      (keys.includes('day') && (value.day < 1 || value.day > new Date(Date.UTC(value.year, value.month, 0)).getUTCDate())))
    throw new Error('invalid_date_target');
  if (value.precision !== requiredPrecision) throw new Error('INSUFFICIENT_DATE_PRECISION');
  return keys.map((k, i) => String(value[k]).padStart(i ? 2 : 4, '0')).join('-');
}

export function incompleteRead(value, explicitlyUnreadable = false) {
  return explicitlyUnreadable || value === null || value === undefined ||
    (typeof value === 'string' && /[*•●]{3,}/u.test(value));
}
