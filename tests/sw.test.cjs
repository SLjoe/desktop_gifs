const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { test } = require('node:test');

const source = fs.readFileSync(path.join(__dirname, '../extension/src/sw.js'), 'utf8');
const id = '7993045760456588950';
const post = (rating = 'G') => ({
  id, content_rating: rating, content_description: 'Selected GIF',
  flags: [], media_formats: { tinygif: { url: 'https://media.tenor.com/fixture/selected.gif', dims: [120, 80] } },
});

// opts: stored (storage.local contents), network (url -> 'down' to reject
// like fetch does), allowed (host permission), version (manifest).
function worker(results, apiStatus = 200, opts = {}) {
  const calls = [];
  const storage = { ...(opts.stored || {}) };
  const settings = Buffer.from(JSON.stringify({ API_V2_KEY: 'fixture-key', API_V2_CLIENT_KEY: 'fixture-client' })).toString('base64');
  const html = `<script id="store-cache" type="text/x-cache">{}</script><script id="data" type="text/x-cache">${settings}</script>`;
  const listeners = [];
  const context = vm.createContext({
    chrome: {
      runtime: {
        id: 'self',
        onInstalled: { addListener() {} },
        onMessage: { addListener(fn) { listeners.push(fn); } },
        getManifest: () => ({ version: opts.version || '0.1.5' }),
      },
      storage: {
        local: {
          get: async (key) => (key in storage ? { [key]: storage[key] } : {}),
          set: async (items) => { Object.assign(storage, items); },
          remove: async (key) => { delete storage[key]; },
        },
      },
      permissions: { contains: async () => opts.allowed !== false },
    },
    URL, URLSearchParams, atob, AbortSignal, setTimeout: (fn) => setTimeout(fn, 0),
    fetch: async (url, init) => {
      calls.push({ url, init });
      const host = new URL(url).hostname;
      if ((opts.network || {})[host] === 'down') throw new TypeError('Failed to fetch');
      if (host === 'api.github.com') return { ok: true, status: 200, json: async () => opts.release };
      if (url === 'https://tenor.com/') return { ok: true, url, text: async () => html };
      const key = new URL(url).searchParams.get('key');
      if (opts.goodKey && key !== opts.goodKey) return { ok: false, status: 400, json: async () => ({}) };
      return { ok: apiStatus === 200, status: apiStatus, json: async () => ({ results }) };
    },
  });
  vm.runInContext(source, context);
  const send = (msg) => new Promise((resolve) => listeners[0](msg, { id: 'self' }, resolve));
  return { check: () => context.handle({ type: 'check', id }), handle: (msg) => context.handle(msg), send, calls, storage };
}

test('selected G-rated IDs get a verified result even when a favorite has no rating metadata', async () => {
  const w = worker([post()]);
  const checked = await w.check();
  assert.equal(checked[0].id, id);
  assert.equal(checked[0].contentRating, 'G');
  const url = new URL(w.calls[1].url);
  assert.equal(url.pathname, '/v2/posts');
  assert.equal(url.searchParams.get('ids'), id);
  assert.equal(url.searchParams.get('contentfilter'), 'off');
  assert.equal(w.calls[1].init.credentials, 'omit');
});

// The worker reports the rating; content.js refuses a GIF that is not G, and
// preserves an unknown rating so transient refusals can still be retried.
for (const rating of ['PG', 'PG-13', 'R']) {
  test(`${rating}-rated selections are reported with their rating`, async () => {
    const checked = await worker([post(rating)]).check();
    assert.equal(checked[0].contentRating, rating);
  });
}

test('a missing rating is reported as unknown, never as G', async () => {
  const item = post();
  delete item.content_rating;
  assert.equal((await worker([item]).check())[0].contentRating, null);
});

test('a response for another ID does not count as the selected GIF', async () => {
  assert.equal((await worker([{ ...post(), id: '12345678' }]).check()).length, 0);
});

test('stickers and missing GIF formats are not returned', async () => {
  assert.equal((await worker([{ ...post(), flags: ['sticker'] }]).check()).length, 0);
  assert.equal((await worker([{ ...post(), media_formats: {} }]).check()).length, 0);
});

test('API failure is surfaced instead of assuming a suitable rating', async () => {
  await assert.rejects(worker([], 503).check(), /Tenor returned HTTP 503/);
});

test('the key read from tenor.com is kept, and later calls skip tenor.com', async () => {
  const w = worker([post()]);
  await w.check();
  assert.equal(JSON.stringify(w.storage.tenorKeys), JSON.stringify({ key: 'fixture-key', client_key: 'fixture-client' }));
  const again = worker([post()], 200, { stored: w.storage, network: { 'tenor.com': 'down' } });
  assert.equal((await again.check())[0].id, id);
  assert.ok(again.calls.every((c) => !c.url.startsWith('https://tenor.com/')));
});

test('a stored key Tenor no longer takes is replaced with the current one', async () => {
  const w = worker([post()], 200, { stored: { tenorKeys: { key: 'old-key', client_key: 'fixture-client' } }, goodKey: 'fixture-key' });
  assert.equal((await w.check())[0].id, id);
  assert.equal(w.storage.tenorKeys.key, 'fixture-key');
});

test('tenor.com that cannot be reached is explained, not reported as "Failed to fetch"', async () => {
  const w = worker([post()], 200, { network: { 'tenor.com': 'down' } });
  await assert.rejects(w.handle({ type: 'search', query: 'cat' }), /could not reach tenor\.com\. If tenor\.com does not open in a tab either/);
  // tried twice before giving up
  assert.equal(w.calls.filter((c) => c.url.startsWith('https://tenor.com/')).length, 4);
});

test('tenor.com that the extension may not reach points at its site access', async () => {
  const w = worker([post()], 200, { network: { 'tenor.com': 'down' }, allowed: false });
  await assert.rejects(w.handle({ type: 'featured' }), /not allowed to reach tenor\.com/);
});

const release = (tag) => ({ tag_name: tag, html_url: `https://github.com/SLjoe/desktop_gifs/releases/tag/${tag}` });

test('a newer GitHub release is reported once a day', async () => {
  const w = worker([], 200, { release: release('v0.1.10'), version: '0.1.9' });
  const reply = await w.send({ type: 'update' });
  assert.equal(reply.release.version, '0.1.10');
  assert.equal(reply.release.url, 'https://github.com/SLjoe/desktop_gifs/releases/tag/v0.1.10');
  await w.send({ type: 'update' });
  assert.equal(w.calls.filter((c) => c.url.includes('api.github.com')).length, 1);
  assert.equal(w.calls[0].init.credentials, 'omit');
});

test('the same or an older release is not reported', async () => {
  assert.equal((await worker([], 200, { release: release('v0.1.5'), version: '0.1.5' }).send({ type: 'update' })).release, null);
  assert.equal((await worker([], 200, { release: release('v0.1.4'), version: '0.1.5' }).send({ type: 'update' })).release, null);
});

test('a release link outside this repository is not reported', async () => {
  const w = worker([], 200, { release: { tag_name: 'v9.0.0', html_url: 'https://example.com/' }, version: '0.1.5' });
  assert.equal((await w.send({ type: 'update' })).release, null);
});

test('GitHub that cannot be reached means no notice', async () => {
  const w = worker([], 200, { network: { 'api.github.com': 'down' } });
  assert.equal(JSON.stringify(await w.send({ type: 'update' })), JSON.stringify({ ok: true, release: null }));
});
