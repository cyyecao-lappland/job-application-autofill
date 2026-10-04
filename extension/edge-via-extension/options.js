const defaults = {bridgeUrl: 'http://127.0.0.1:43127', token: ''};
const form = document.getElementById('form');
const bridgeUrl = document.getElementById('bridgeUrl');
const token = document.getElementById('token');
const status = document.getElementById('status');

chrome.storage.local.get(defaults).then(value => {
  bridgeUrl.value = value.bridgeUrl || defaults.bridgeUrl;
  token.value = value.token || '';
});
form.addEventListener('submit', async event => {
  event.preventDefault();
  try {
    const url = new URL(bridgeUrl.value);
    if (url.protocol !== 'http:' || !['127.0.0.1', 'localhost', '[::1]'].includes(url.hostname)) {
      throw new Error('只允许 127.0.0.1 / localhost / [::1]');
    }
    if (!token.value.trim()) throw new Error('Token 不能为空');
    await chrome.storage.local.set({bridgeUrl: url.toString().replace(/\/$/, ''), token: token.value.trim()});
    chrome.runtime.sendMessage({type: 'config-updated'}).catch(() => {});
    status.textContent = '已保存';
  } catch (error) {
    status.textContent = error.message;
  }
});
