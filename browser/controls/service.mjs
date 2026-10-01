import {matchRegistered} from '../rules/service.mjs';
import {Deferred,Conflict} from './runtime.mjs';
import {entries, protocolVersion, registryVersion} from './registry.generated.mjs';
import {decodeLegacyTarget} from './legacy_bridge.mjs';

const keys = ['adapterVersion','name','protocolVersion','targetTypes'];
export function canonicalReport(report) {
  const fail = detail => {throw new Error('CONTROL_REGISTRY_MISMATCH: ' + detail);};
  if (!report || JSON.stringify(Object.keys(report).sort()) !== JSON.stringify(['controls','protocolVersion','registryVersion']) ||
      !Number.isInteger(report.protocolVersion) || report.protocolVersion < 1 ||
      !Number.isInteger(report.registryVersion) || report.registryVersion < 1 || !Array.isArray(report.controls)) fail('invalid report');
  const names = new Set();
  const controls = report.controls.map(c => {
    if (!c || JSON.stringify(Object.keys(c).sort()) !== JSON.stringify(keys) || typeof c.name !== 'string' || !c.name || names.has(c.name) ||
        !Number.isInteger(c.adapterVersion) || c.adapterVersion < 1 || !Number.isInteger(c.protocolVersion) || c.protocolVersion < 1 ||
        !Array.isArray(c.targetTypes) || !c.targetTypes.length || c.targetTypes.some(t => typeof t !== 'string' || !t) ||
        new Set(c.targetTypes).size !== c.targetTypes.length) fail('invalid or duplicate declaration');
    names.add(c.name);
    return {name:c.name, adapterVersion:c.adapterVersion, targetTypes:[...c.targetTypes].sort(), protocolVersion:c.protocolVersion};
  }).sort((a,b) => a.name < b.name ? -1 : a.name > b.name ? 1:0);
  return {protocolVersion:report.protocolVersion, registryVersion:report.registryVersion, controls};
}

export function assertRegistry(expected, actual = registryReport()) {
  expected = canonicalReport(expected); actual = canonicalReport(actual);
  const diff = [];
  for (const k of ['protocolVersion','registryVersion']) if (expected[k] !== actual[k]) diff.push(`${k}: expected ${expected[k]}, executor provides ${actual[k]}`);
  const left = new Map(expected.controls.map(c => [c.name,c])), right = new Map(actual.controls.map(c => [c.name,c]));
  for (const name of new Set([...left.keys(),...right.keys()])) {
    if (!left.has(name) || !right.has(name)) diff.push(`${name}: ${left.has(name) ? 'missing':'unexpected'}`);
    else for (const key of keys.filter(k => k !== 'name'))
      if (JSON.stringify(left.get(name)[key]) !== JSON.stringify(right.get(name)[key]))
        diff.push(`${name}.${key}: expected ${JSON.stringify(left.get(name)[key])}, executor provides ${JSON.stringify(right.get(name)[key])}`);
  }
  if (diff.length) throw new Error('CONTROL_REGISTRY_MISMATCH: ' + diff.join('; '));
  return actual;
}

export function registryReport() {
  // Report imported implementations, not a second read of the source catalog.
  const controls = [...entries.values()].map(({driver,config,rules}) => {
    if(!rules?.length||rules.some(rule=>typeof rule.match!=='function'))throw new Error('CONTROL_REGISTRY_MISMATCH: missing recognition rule');
    for (const method of config.execution==='field'?['applyField']:['detect','prepare','apply','read','compare'])
      if (typeof driver[method] !== 'function') throw new Error('CONTROL_REGISTRY_MISMATCH: missing ' + method);
    const declared = Object.fromEntries(keys.map(k => [k,config[k]]));
    assertRegistry({protocolVersion,registryVersion,controls:[declared]},
      {protocolVersion,registryVersion,controls:[driver.declaration]});
    return driver.declaration;
  });
  return canonicalReport({protocolVersion,registryVersion,controls});
}

export const adapterNames = () => [...entries.values()].filter(e=>e.config.execution!=='field').map(e=>e.config.name);
export const hasFieldControl = field => !!matchRegistered(field,entries,'field');
export const matchControl = field => matchRegistered(field,entries,'control')?.config.name??null;

// Field drivers share executor identity checks, readback and journal semantics.
export async function executeFieldControl(ctx) {
  const entry=matchRegistered(ctx.field,entries,'field');
  if(!entry)throw new Deferred('unknown_control_method');
  if(entry.config.execution==='field')return entry.driver.applyField(ctx);
  const {tab,packet,field,op,end,checkpoint,recordOutcome}=ctx;
  const outcome=await executeControl(tab,{...packet,adapter:entry.config.name,
    field_label:field.label,field_selector:field.selector,before_value:field.value,
    location:op.value,deadline:Math.min(packet.deadline,end/1000),controlEvidence:field},checkpoint);
  await recordOutcome(outcome);
  if(outcome.verification==='mismatch')throw new Conflict('value_did_not_match_after_action');
  return outcome;
}
export const targetFromLegacy = (adapter, value) => {
  const entry = entries.get(adapter);
  if (!entry || entry.config.execution==='field') throw new Error('unsupported_control_adapter');
  return decodeLegacyTarget(entry.config.legacyCodec, value);
};

export async function executeControl(tab, packet, checkpoint = async () => {}) {
  if (packet.controlRegistry) assertRegistry(packet.controlRegistry);
  const entry = entries.get(packet.adapter);
  if (!entry || entry.config.execution==='field') throw new Error('unsupported_control_adapter');
  const {driver} = entry;
  const target = packet.controlTarget ?? targetFromLegacy(packet.adapter, packet.location);
  if (!driver.declaration.targetTypes.includes(target?.kind)) throw new Error('unsupported_control_target');
  if (driver.detect(packet.controlEvidence) === 'incompatible') throw new Error('UNSUPPORTED_VARIANT');
  const ctx = {tab, checkpoint};
  const prepared = await driver.prepare(ctx, packet, target);
  let applied;
  try { applied = await driver.apply(ctx, prepared, target); }
  catch(error) {
    if(error.controlVerification !== 'mismatch') throw error;
    return {schema:'control-receipt/v1',operationId:packet.command_id,adapter:packet.adapter,
      adapterVersion:driver.declaration.adapterVersion,call:'finished',verification:'mismatch',
      actual:error.actual,committed:false,persistence:'not_assessed',reason:error.message};
  }
  const observation = await driver.read(ctx, prepared, applied);
  const verification = driver.compare(observation, target);
  if (!['match','skipped','mismatch'].includes(verification)) throw new Error('invalid_control_verification');
  return {...applied, schema:'control-receipt/v1', operationId:packet.command_id,
    adapter:packet.adapter, adapterVersion:driver.declaration.adapterVersion,
    call:'finished', verification, actual:observation.actual,
    committed:verification === 'match', persistence:'not_assessed',
    reason:verification === 'skipped' ? (observation.reason || 'readback_incomplete'):observation.reason};
}
