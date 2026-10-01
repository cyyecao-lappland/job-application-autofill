/** Standalone application loop for an existing Edge CDP page. */
import {access, mkdir, writeFile, readFile} from 'node:fs/promises';
import {spawn} from 'node:child_process';
import {join, resolve} from 'node:path';
import {connectEdgeCdp} from '../browser/cdp_connector.mjs';
import {runSessionRequest} from '../browser/playwright_executor.mjs';
import {inventoryPage,isolatePriorScopes} from '../browser/page_inventory.mjs';
import {identifyPlaywrightSession} from '../browser/playwright_backend.mjs';
import {registryReport} from '../browser/controls/service.mjs';
import {createApplicationWorker} from './application_worker.mjs';

async function exists(path) {
  try { await access(path); return true; } catch { return false; }
}

export async function defaultPython(projectDirectory) {
  const configured = process.env.JOB_APPLICATION_PYTHON;
  const candidates = [
    configured,
    join(projectDirectory, '.venv', 'Scripts', 'python.exe'),
    join(projectDirectory, '.venv', 'bin', 'python')
  ].filter(Boolean);
  for (const candidate of candidates) if (await exists(candidate)) return candidate;
  return 'python';
}

export function runProcess(executable, args, {cwd, timeoutMs = 600_000} = {}) {
  return new Promise((resolvePromise, reject) => {
    const child = spawn(executable, args, {cwd, windowsHide: true, stdio: ['ignore', 'pipe', 'pipe'],
      env: {...process.env, PYTHONUTF8: '1', PYTHONIOENCODING: 'utf-8'}});
    let stdout = '', stderr = '';
    child.stdout.setEncoding('utf8').on('data', chunk => { stdout += chunk; });
    child.stderr.setEncoding('utf8').on('data', chunk => { stderr += chunk; });
    const timer = setTimeout(() => {
      child.kill();
      reject(new Error('application_command_timeout'));
    }, timeoutMs);
    child.on('error', error => { clearTimeout(timer); reject(error); });
    child.on('close', code => {
      clearTimeout(timer);
      if (code !== 0) return reject(new Error(`application_command_failed:${stderr || stdout}`));
      try { resolvePromise(JSON.parse(stdout)); }
      catch { reject(new Error('application_command_invalid_json')); }
    });
  });
}

export async function runNativePlaywrightDriver(options, dependencies = {}) {
  if(options.controlAgent!==undefined&&!['on','off'].includes(options.controlAgent))throw new Error('control_agent_must_be_on_or_off');
  const controlAgent=options.controlAgent!=='off';
  if (options.agentTuning !== undefined) {
    if (!['on','off'].includes(options.agentTuning)) throw new Error('agent_tuning_must_be_on_or_off');
    if (!options.start) throw new Error('agent_tuning_is_persisted_at_start');
  }
  const projectDirectory = resolve(options.projectDirectory || '.');
  const connect = dependencies.connect || connectEdgeCdp;
  const executeRequest = dependencies.executeRequest || runSessionRequest;
  const python = options.python || await defaultPython(projectDirectory);
  let worker;
  const command = dependencies.command || (args => {
    worker ||= createApplicationWorker(python, {cwd: projectDirectory,
      timeoutMs: options.commandTimeoutMs || 600_000});
    return worker.command(args);
  });
  const connection = await connect({
    endpoint: options.endpoint,
    exactUrl: options.exactUrl,
    pageId: options.pageId,
    timeoutMs: options.connectionTimeoutMs || 10_000
  });
  try {
    await connection.activate?.();
    const registry = registryReport();
    const handshake = await command(['check-control-registry','--executor-report',JSON.stringify(registry)]);
    if (handshake?.status !== 'registry_matched') throw new Error('CONTROL_REGISTRY_MISMATCH: Python did not accept executor declarations');
    let runDirectory = resolve(options.runDirectory);
    let status;
    if (options.start) {
      if(!options.manifest){
        await mkdir(runDirectory,{recursive:true});
        const inventory=await inventoryPage(connection.session);
        const identity=await identifyPlaywrightSession(connection.session);
        const target={browser:'edge',browser_id:identity.browser_id,tab_id:identity.tab_id,url:identity.url};
        const inventoryPath=join(runDirectory,'page-inventory.json');
        const manifestPath=join(runDirectory,'generated-manifest.json');
        await writeFile(inventoryPath,JSON.stringify(inventory,null,2));
        const planArgs=['-m','edge_form_graph.page_planner','--inventory',inventoryPath,'--target',JSON.stringify(target),'--output',manifestPath];
        if(options.profile)planArgs.push('--profile',resolve(options.profile));
        await runProcess(python,planArgs,{cwd:projectDirectory});
        const manifest=JSON.parse(await readFile(manifestPath,'utf8'));
        const prior=await command(['preflight','--prior-root',resolve(options.priorRoot)]);
        if(prior.blockers?.length){
          await isolatePriorScopes(connection.session,manifest,prior.blockers);
          await writeFile(manifestPath,JSON.stringify(manifest,null,2));
        }
        options.manifest=manifestPath;
      }
      const action = options.freshStart === false ? 'start' : 'fresh-start';
      const args = [action, '--run-dir', runDirectory, '--manifest', resolve(options.manifest),
        '--prior-root', resolve(options.priorRoot)];
      if (options.profile) args.push('--profile', resolve(options.profile));
      if (options.allowSave) args.push('--allow-save');
      if (options.agentTuning !== undefined) {
        args.push('--agent-tuning', options.agentTuning);
      }
      status = await command(args);
      runDirectory = status.run_dir || runDirectory;
    } else {
      status = await command(['status', '--run-dir', runDirectory]);
    }
    options.onStatus?.(status);
    const startingModuleIndex=status.module_index;
    const attemptedControls=new Set();
    let controlRounds=0, controlModule=null;
    for (let batch = 0; batch < (options.maxBatches || 100); batch++) {
      // A targeted repair stops before dispatching the next module's request.
      // Its checkpoint and undispatched command remain available for continuation.
      if(options.stopAtModuleBoundary&&status.module_index!==startingModuleIndex)break;
      if (!status.pending_kind) {
        if(options.skipUnknownRequired!==false && status.unanswerable_required_fields?.length &&
           ['module_blocked','scope_review_blocked','incomplete_coverage'].includes(status.status)){
          status={...status,queue_disposition:'skipped_missing_required_answer'};
          options.onStatus?.(status);
          break;
        }
        if(status.pure_review_pending){
          status=await command(['resume-pure-review','--run-dir',runDirectory,
            '--basis','Continue only the interrupted pure review boundary after checking original writer settlement.']);
          options.onStatus?.(status);
          continue;
        }
        const active=status.active_module_id||status.module_index;
        if(controlModule!==active){controlRounds=0;controlModule=active;}
        if(controlAgent&&status.status==='control_committed'){
          status=await command(['continue-after-control','--run-dir',runDirectory]);
          options.onStatus?.(status);
          continue;
        }
        if(controlAgent&&status.control_exception_count!==0&&['module_blocked','control_blocked','recovery_blocked'].includes(status.status)&&controlRounds<3){
          controlRounds++;
          status=await command(['inspect-controls','--run-dir',runDirectory]);
          await executeRequest(connection.session,runDirectory);
          status=await command(['resume','--run-dir',runDirectory]);
          const resolution=await command(['resolve-controls','--run-dir',runDirectory]);
          const controlKey=item=>JSON.stringify([status.module_index,item.adapter==='next_range_date_v1'?'range':item.label,item.source,item.adapter]);
          const choice=resolution.ready?.find(item=>!attemptedControls.has(controlKey(item)));
          if(!choice){
            status={...status,control_resolution:resolution};
            if(status.program_inventory){status=await command(['defer-independent','--run-dir',runDirectory]);options.onStatus?.(status);continue;}
            break;
          }
          attemptedControls.add(controlKey(choice));
          // Model latency cannot make the pre-action DOM binding stale.
          status=await command(['inspect-controls','--run-dir',runDirectory]);
          await executeRequest(connection.session,runDirectory);
          status=await command(['resume','--run-dir',runDirectory]);
          status=await command(['apply-control','--run-dir',runDirectory,'--label',choice.label,'--source',choice.source,'--adapter',choice.adapter,
            ...(choice.field_id?['--field-id',choice.field_id]:[])]);
          options.onStatus?.(status);
          continue;
        }
        if(status.program_inventory&&status.control_exception_count===0&&['module_blocked','control_blocked','recovery_blocked'].includes(status.status)){
          status=await command(['defer-independent','--run-dir',runDirectory]);options.onStatus?.(status);continue;
        }
        if(status.program_inventory&&['scope_draft','save_control_unverified','scope_review_blocked','needs_reconciliation','module_add_blocked'].includes(status.status)){
          status=await command(['defer-independent','--run-dir',runDirectory]);options.onStatus?.(status);continue;
        }
        if (status.status !== 'module_edit_not_ready') break;
        status = await command(['open-module', '--run-dir', runDirectory]);
        options.onStatus?.(status);
        if (!status.pending_kind) break;
      }
      await executeRequest(connection.session, runDirectory);
      status = await command(['resume', '--run-dir', runDirectory]);
      options.onStatus?.(status);
    }
    return {...status, run_dir: runDirectory};
  } finally {
    worker?.close();
    await connection.closeConnection();
  }
}
