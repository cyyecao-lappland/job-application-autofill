'use strict';

// The page runtime lives in this isolated content-script world. Keeping one
// listener in the document avoids waiting for a large executeScript call to
// settle while a click is changing the SPA route.
const pageCommandRuntime = globalThis.__jobApplicationAutofillRunPageCommand;

const serialiseError = error => ({
  message: error?.message || String(error || 'Page command failed'),
  code: error?.code || undefined
});

chrome.runtime.onMessage.addListener((message, sender, sendResponse) => {
  if (sender?.id !== chrome.runtime.id || message?.type !== 'page-command') return false;
  Promise.resolve().then(() => {
    if (typeof pageCommandRuntime !== 'function') throw new Error('Page command runtime is unavailable');
    return pageCommandRuntime(message.command);
  }).then(result => sendResponse({result}), error => sendResponse({error: serialiseError(error)}));
  return true;
});

// This content script is also a readiness signal. The worker may be asleep
// when a page is opened, so the message wakes it and schedules an immediate
// bridge poll.
try {
  chrome.runtime.sendMessage({type: 'page-ready', url: location.href});
} catch {
  // The page can outlive an extension reload; readiness is best effort.
}
