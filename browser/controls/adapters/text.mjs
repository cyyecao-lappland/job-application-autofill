import {Deferred} from '../runtime.mjs';
import {textCompatibility} from '../../rules/recognition.mjs';
import {scopedControlTarget} from '../target.mjs';
import {readControlEvidence} from '../../rules/dom.mjs';
import {makeDriver, textValue, incompleteRead} from '../driver.mjs';

export const driver = makeDriver(
  {name:'plain_text_probe_v1', adapterVersion:1, targetTypes:['text'], protocolVersion:1},
  runTextAdapter, textValue,
  textCompatibility
);

export async function runTextAdapter(tab,packet,checkpoint){
  const read=()=>tab.playwright.evaluate(readControlEvidence,{moduleSelector:packet.module_selector,
    fieldSelector:packet.allow_logical_selector?packet.field_selector:null},{timeoutMs:2000});
  const guard=e=>{if(e.root_count!==1||e.dialogs.length||e.menus?.length)throw new Deferred('text_probe_discovered_composite_control');};
  // Element UI keeps a dropdown in its leave transition briefly after the
  // preceding combobox has been closed.  That is read-only stale UI, but it
  // used to make every following plain text field fail before an action. Wait
  // for a bounded clean observation; a persistent/open popup still blocks the
  // text adapter and a popup created by focusing this field is still rejected
  // immediately below.
  let before=await read();
  const cleanEnd=Math.min(packet.deadline*1000,Date.now()+3500);
  while(before.root_count===1&&!before.dialogs.length&&before.menus?.length&&Date.now()<cleanEnd){
    await new Promise(resolve=>setTimeout(resolve,60));
    before=await read();
  }
  if(before.root_count!==1||before.dialogs.length||before.menus?.length)throw new Deferred('preexisting_popup');
  const fields=before.fields.filter(f=>(packet.allow_logical_selector&&before.target_count!=null?
    before.target_count===1&&f.matches_target:f.label===packet.field_label&&
    (packet.allow_logical_selector||f.selector===packet.field_selector))&&!f.readonly&&!f.disabled);
  if(fields.length!==1)throw new Deferred('text_control_identity_changed');
  const target=scopedControlTarget(tab,packet);
  if(packet.allow_logical_selector&&await target.count()!==1)throw new Deferred('text_control_identity_changed');
  const actualBefore=await target.evaluate(el=>el.value);
  if(actualBefore===packet.location)return {adapter:'plain_text_probe_v1',committed:true,actual:actualBefore,verification:'match',already_matched:true};
  if(actualBefore!==packet.before_value)throw new Deferred('control_user_value_changed');
  const timeout=()=>Math.max(1,Math.min(5000,packet.deadline*1000-Date.now()));
  await checkpoint({stage:'text_probe_issued'});await target.click({timeoutMs:timeout()});guard(await read());
  await checkpoint({stage:'text_fill_issued'});await target.fill(packet.location,{timeoutMs:timeout()});
  // Blur the same input without transferring focus into the next custom
  // control. On Element forms, Tab can focus and open the following select,
  // which is unrelated to the text input just verified.
  await target.evaluate(el=>el.blur());guard(await read());
  const actual=await target.evaluate(el=>el.value??null);
  if(actual!==packet.location){
    if(packet.return_verification&&incompleteRead(actual)){
      await checkpoint({stage:'verification_skipped'});
      return {adapter:'plain_text_probe_v1',committed:false,actual,verification:'skipped',reason:'readback_incomplete'};
    }
    throw Object.assign(new Error('text_readback_mismatch'),{controlVerification:'mismatch',actual});
  }
  await checkpoint({stage:'verified'});return {adapter:'plain_text_probe_v1',committed:true,actual,verification:'match'};
}
