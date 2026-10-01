/** Primary Playwright executor entrypoint. */
import * as implementation from './edge_executor.mjs';
import {readSaveScopeEvidence,classifySaveScopeEvidence} from './save_scope_detection.mjs';
import {listModuleTabs,activateModuleTab,openModuleEditor} from './module_navigation.mjs';

export * from './edge_executor.mjs';

export async function observeSession(session, options) {
  if (session?.backend !== 'playwright') throw new TypeError('playwright_session_required');
  return implementation.observe(session, session.tab, options);
}

export async function inspectSaveScopes(session, options) {
  if(session?.backend!=='playwright')throw new TypeError('playwright_session_required');
  const evidence=await session.tab.playwright.evaluate(readSaveScopeEvidence,options,{timeoutMs:options?.timeoutMs??5000});
  return {evidence,classification:classifySaveScopeEvidence(evidence)};
}

export async function listSessionModules(session, options){
  if(session?.backend!=='playwright')throw new TypeError('playwright_session_required');
  return listModuleTabs(session.tab,options);
}

export async function activateSessionModule(session, options){
  if(session?.backend!=='playwright')throw new TypeError('playwright_session_required');
  return activateModuleTab(session.tab,options);
}

export async function openSessionModuleEditor(session,options){
  if(session?.backend!=='playwright')throw new TypeError('playwright_session_required');
  return openModuleEditor(session.tab,options);
}


export async function runSessionRequest(session, runDirectory) {
  if (session?.backend !== 'playwright') throw new TypeError('playwright_session_required');
  return implementation.runRequest(session, session.tab, runDirectory);
}

export async function verifySessionAutomaticSave(session, packet, journal = {}, checkpoint = async () => {}) {
  if (session?.backend !== 'playwright') throw new TypeError('playwright_session_required');
  return implementation.verifyAutomaticSave(session, session.tab, packet, journal, checkpoint);
}
