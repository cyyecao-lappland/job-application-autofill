#!/usr/bin/env node
import {runNativePlaywrightDriver} from '../host/native_playwright_driver.mjs';

function parse(argv) {
  const options = {start: false, allowSave: false};
  const values = new Set(['--endpoint','--page-id','--url','--project','--run-dir','--manifest','--prior-root','--profile','--python','--agent-tuning','--control-agent']);
  for (let i = 0; i < argv.length; i++) {
    const flag = argv[i];
    if (flag === '--start') { options.start = true; continue; }
    if (flag === '--allow-save') { options.allowSave = true; continue; }
    if (!values.has(flag)) throw new Error(`unknown_argument:${flag}`);
    const value = argv[++i];
    if (!value) throw new Error(`missing_value:${flag}`);
    const key = {'--endpoint':'endpoint','--page-id':'pageId','--url':'exactUrl','--project':'projectDirectory',
      '--run-dir':'runDirectory','--manifest':'manifest','--prior-root':'priorRoot','--profile':'profile','--python':'python','--agent-tuning':'agentTuning','--control-agent':'controlAgent'}[flag];
    options[key] = value;
  }
  for (const key of ['runDirectory']) if (!options[key]) throw new Error(`${key}_required`);
  if (options.agentTuning !== undefined && !['on','off'].includes(options.agentTuning)) throw new Error('agent_tuning_must_be_on_or_off');
  if (!options.start && options.agentTuning !== undefined) throw new Error('agent_tuning_is_persisted_at_start');
  if (options.start && !options.priorRoot) throw new Error('priorRoot_required');
  return options;
}

try {
  const result = await runNativePlaywrightDriver(parse(process.argv.slice(2)), {
    // Dependency object is intentionally empty in production. Tests inject fakes.
  });
  process.stdout.write(`${JSON.stringify(result, null, 2)}\n`);
} catch (error) {
  process.stderr.write(`${JSON.stringify({error:error.message}, null, 2)}\n`);
  process.exitCode = 1;
}
