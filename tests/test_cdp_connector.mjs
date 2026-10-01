import test from 'node:test';
import assert from 'node:assert/strict';
import {connectEdgeCdp, describeCdpPages, selectCdpPage} from '../browser/cdp_connector.mjs';
import {identifyPlaywrightSession} from '../browser/playwright_backend.mjs';

function fixture(urls = ['https://example.test/form']) {
  const calls = [];
  const pages = urls.map((url, index) => {
    const cdp = {
      send: async method => {
        calls.push(['send', method]);
        return {targetInfo: {targetId: `page-${index + 1}`}};
      },
      detach: async () => calls.push(['detach', index])
    };
    const context = {newCDPSession: async page => {
      calls.push(['newCDPSession', page.url()]);
      return cdp;
    }};
    return {
      context: () => context,
      title: async () => `Page ${index + 1}`,
      url: () => url,
      evaluate: async () => ({}),
      locator: () => ({}),
      on: () => {}
    };
  });
  const browser = {
    contexts: () => [{pages: () => pages}],
    close: async () => calls.push(['close'])
  };
  const chromium = {
    connectOverCDP: async (...args) => {
      calls.push(['connectOverCDP', ...args]);
      return browser;
    }
  };
  return {browser, chromium, calls};
}

test('describes pages using CDP target identity without navigation', async () => {
  const {browser, calls} = fixture();
  const pages = await describeCdpPages(browser);
  assert.deepEqual(pages.map(({page, ...item}) => item), [
    {pageId: 'page-1', title: 'Page 1', url: 'https://example.test/form'}
  ]);
  assert.ok(calls.every(call => call[0] !== 'goto'));
});

test('refuses to guess when multiple pages are present', () => {
  const pages = [
    {pageId: 'one', url: 'https://one.test'},
    {pageId: 'two', url: 'https://two.test'}
  ];
  assert.throws(() => selectCdpPage(pages), error => {
    assert.match(error.message, /cdp_page_selection_ambiguous/);
    assert.deepEqual(error.pages, pages);
    return true;
  });
});

test('selects one page by exact URL or explicit page id', () => {
  const pages = [
    {pageId: 'one', url: 'https://one.test'},
    {pageId: 'two', url: 'https://two.test'}
  ];
  assert.equal(selectCdpPage(pages, {exactUrl: 'https://two.test'}).pageId, 'two');
  assert.equal(selectCdpPage(pages, {pageId: 'one'}).url, 'https://one.test');
  assert.throws(() => selectCdpPage(pages, {exactUrl: 'https://one.test', pageId: 'one'}),
    /choose_exact_url_or_page_id/);
});

test('connects over CDP and preserves native browser and tab identity', async () => {
  const {chromium, calls} = fixture();
  const connection = await connectEdgeCdp({chromium, endpoint: 'http://127.0.0.1:9333'});
  assert.deepEqual(await identifyPlaywrightSession(connection.session), {
    backend: 'playwright',
    transport: 'native',
    browser_id: 'edge-cdp:http://127.0.0.1:9333',
    tab_id: 'page-1',
    url: 'https://example.test/form'
  });
  await connection.closeConnection();
  await connection.closeConnection();
  assert.equal(calls.filter(call => call[0] === 'close').length, 1);
  assert.ok(calls.every(call => call[0] !== 'goto'));
});

test('disconnects after ambiguous selection', async () => {
  const {chromium, calls} = fixture(['https://one.test', 'https://two.test']);
  await assert.rejects(connectEdgeCdp({chromium}), /cdp_page_selection_ambiguous/);
  assert.equal(calls.filter(call => call[0] === 'close').length, 1);
});
