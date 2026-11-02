/*
 * Offline tests of frontend/api.js with a fake fetch (no network, no browser).
 * Run with `node --test frontend/tests` (tests/unit/test_frontend.py runs it under pytest
 * when node is installed). Covers result shapes, error kinds, CSRF, cache, pagination,
 * session detection and CSV export.
 */
'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');

const InventApi = require(path.join(__dirname, '..', 'api.js'));

function response(status, body, headers) {
  const text = body === undefined ? '' : typeof body === 'string' ? body : JSON.stringify(body);
  const map = Object.fromEntries(Object.entries(headers || {}).map(([k, v]) => [k.toLowerCase(), String(v)]));
  return { status, headers: { get: (name) => map[name.toLowerCase()] ?? null }, text: async () => text };
}

function fakeFetch(routes) {
  const calls = [];
  const fn = async (url, init) => {
    calls.push({ url, init });
    const key = (init.method || 'GET') + ' ' + url;
    const handler = routes[key] || routes[url];
    if (!handler) return response(404, { detail: 'route_not_found' });
    if (handler instanceof Error) throw handler;
    return typeof handler === 'function' ? handler(url, init, calls) : handler;
  };
  fn.calls = calls;
  return fn;
}

test('a 200 resolves ok with data, ETag and X-As-Of', async () => {
  const fetch = fakeFetch({ '/api/v1/leads/abc': response(200, { id: 'abc', version: 3 }, { ETag: '"3"', 'X-As-Of': '1700000000000' }) });
  const client = InventApi.createClient({ fetch, getCookie: () => '' });
  const res = await client.get('/leads/abc');
  assert.equal(res.ok, true);
  assert.equal(res.data.version, 3);
  assert.equal(res.etag, '"3"');
  assert.equal(res.asOf, 1700000000000);
  assert.equal(fetch.calls[0].init.credentials, 'same-origin');
});

test('query parameters skip empty values and are encoded', async () => {
  const fetch = fakeFetch({});
  const client = InventApi.createClient({ fetch });
  await client.get('/leads', { city: 'Medell\u00edn', state: '', segment: null, limit: 50 });
  assert.equal(fetch.calls[0].url, '/api/v1/leads?city=Medell%C3%ADn&limit=50');
});

test('a network failure resolves as offline and never rejects', async () => {
  const fetch = fakeFetch({ '/api/v1/me': new TypeError('Failed to fetch') });
  const client = InventApi.createClient({ fetch });
  let offline = 0;
  client.on('offline', () => { offline += 1; });
  const res = await client.get('/me');
  assert.equal(res.ok, false);
  assert.equal(res.kind, 'offline');
  assert.equal(res.status, 0);
  assert.match(res.message, /Sin conexi/);
  assert.equal(offline, 1);
});

test('error statuses map to kinds with Spanish messages', async () => {
  const cases = [
    [401, { detail: 'authentication_required' }, 'unauthorized'],
    [403, { detail: 'forbidden' }, 'forbidden'],
    [403, { detail: 'csrf_token_invalid' }, 'csrf'],
    [404, { detail: 'not_in_layer' }, 'not_in_layer'],
    [404, { detail: 'route_not_found' }, 'route_missing'],
    [404, { detail: 'lead_not_found' }, 'not_found'],
    [409, { detail: 'version_conflict', current: { version: 4 } }, 'conflict'],
    [412, { detail: { error: 'stale_etag', current_version: 2 } }, 'precondition'],
    [400, { detail: 'tenant_required' }, 'tenant'],
    [422, { detail: { error: 'invalid_transition', allowed: ['hot'] } }, 'invalid'],
    [501, { detail: 'segment_read_model_pending' }, 'not_implemented'],
    [503, { detail: 'upstream_unavailable' }, 'unavailable'],
    [500, 'Internal Server Error', 'server']
  ];
  for (const [status, body, kind] of cases) {
    const client = InventApi.createClient({ fetch: fakeFetch({ '/api/v1/x': response(status, body) }) });
    const res = await client.get('/x');
    assert.equal(res.ok, false, String(status));
    assert.equal(res.kind, kind, status + ' ' + JSON.stringify(body));
    assert.ok(res.message.length > 10, kind);
  }
  const client = InventApi.createClient({ fetch: fakeFetch({ '/api/v1/x': response(422, { detail: { error: 'invalid_transition', allowed: ['hot'] } }) }) });
  const res = await client.get('/x');
  assert.equal(res.code, 'invalid_transition');
  assert.deepEqual(res.detail.allowed, ['hot']);
});

test('429 carries Retry-After in the result and the message', async () => {
  const client = InventApi.createClient({ fetch: fakeFetch({ '/api/v1/x': response(429, { detail: 'rate_limit_exceeded' }, { 'Retry-After': '17' }) }) });
  const res = await client.get('/x');
  assert.equal(res.kind, 'rate_limited');
  assert.equal(res.retryAfter, 17);
  assert.match(res.message, /17 s/);
});

test('401 notifies listeners unless the call is quiet', async () => {
  const client = InventApi.createClient({ fetch: fakeFetch({ '/api/v1/leads': response(401, { detail: 'authentication_required' }) }) });
  let seen = 0;
  client.on('unauthorized', () => { seen += 1; });
  await client.get('/leads', null, { fresh: true });
  await client.get('/leads', null, { fresh: true, quietAuth: true });
  assert.equal(seen, 1);
});

test('writes send JSON and the CSRF cookie as X-CSRF-Token; GET does not', async () => {
  const fetch = fakeFetch({ 'PATCH /api/v1/leads/1/status': response(200, { lead_id: '1', stage: 'qualified', version: 2 }) });
  const client = InventApi.createClient({ fetch, getCookie: () => 'session=abc; csrf=tok%2Den' });
  const res = await client.patch('/leads/1/status', { stage: 'qualified', expected_version: 1 });
  assert.equal(res.ok, true);
  const init = fetch.calls[0].init;
  assert.equal(init.headers['X-CSRF-Token'], 'tok-en');
  assert.equal(init.headers['Content-Type'], 'application/json');
  assert.deepEqual(JSON.parse(init.body), { stage: 'qualified', expected_version: 1 });
  await client.get('/leads/1');
  assert.equal(fetch.calls[1].init.headers['X-CSRF-Token'], undefined);
});

test('the production __Host-csrf cookie wins over the local one', () => {
  assert.equal(InventApi.csrfToken('__Host-csrf=prod; csrf=local'), 'prod');
  assert.equal(InventApi.csrfToken('other=1'), '');
});

test('raw CSV bodies keep their content type and extra headers', async () => {
  const fetch = fakeFetch({ 'POST /api/v1/imports/companies': response(202, { job_id: 'j1', status: 'queued', status_url: '/api/v1/jobs/j1' }) });
  const client = InventApi.createClient({ fetch, getCookie: () => '' });
  const res = await client.request('POST', '/imports/companies', { raw: 'a,b\n1,2\n', headers: { 'Idempotency-Key': 'k1', 'X-Filename': 'f.csv' } });
  assert.equal(res.status, 202);
  const init = fetch.calls[0].init;
  assert.equal(init.body, 'a,b\n1,2\n');
  assert.equal(init.headers['Content-Type'], 'text/csv');
  assert.equal(init.headers['Idempotency-Key'], 'k1');
});

test('GET responses are cached briefly and a successful write clears the cache', async () => {
  let clock = 1000;
  let served = 0;
  const fetch = fakeFetch({
    '/api/v1/projects': () => { served += 1; return response(200, { data: [], next_cursor: null }); },
    'POST /api/v1/projects': response(201, { project_id: 'p' })
  });
  const client = InventApi.createClient({ fetch, now: () => clock });
  await client.get('/projects');
  await client.get('/projects');
  assert.equal(served, 1);
  await client.get('/projects', null, { fresh: true });
  assert.equal(served, 2);
  await client.post('/projects', { lead_id: 'x', name: 'n', hypothesis: 'h' });
  await client.get('/projects');
  assert.equal(served, 3);
  clock += 60000;
  await client.get('/projects');
  assert.equal(served, 4);
});

test('a hung request times out as kind timeout', async () => {
  const fetch = (url, init) => new Promise((resolve, reject) => {
    init.signal.addEventListener('abort', () => reject(new Error('aborted')));
  });
  const client = InventApi.createClient({ fetch, timeoutMs: 20 });
  const res = await client.get('/slow');
  assert.equal(res.kind, 'timeout');
});

test('listPage and mergePage follow next_cursor and keep rows on error', async () => {
  const pages = {
    '/api/v1/data/companies?limit=2': response(200, { dataset: 'companies', columns: [], data: [{ id: 1 }, { id: 2 }], next_cursor: 'c2', total: 3, truncated_cells: false }),
    '/api/v1/data/companies?limit=2&cursor=c2': response(200, { dataset: 'companies', columns: [], data: [{ id: 3 }], next_cursor: null, total: 3, truncated_cells: false }),
    '/api/v1/data/companies?limit=2&cursor=bad': response(400, { detail: 'invalid_cursor' })
  };
  const client = InventApi.createClient({ fetch: fakeFetch(pages) });
  const first = await client.listPage('/data/companies', { limit: 2 });
  let state = InventApi.mergePage(null, first, false);
  assert.deepEqual(state.rows.map((r) => r.id), [1, 2]);
  assert.equal(state.hasMore, true);
  assert.equal(state.total, 3);
  const second = await client.listPage('/data/companies', { limit: 2, cursor: state.nextCursor });
  state = InventApi.mergePage(state, second, true);
  assert.deepEqual(state.rows.map((r) => r.id), [1, 2, 3]);
  assert.equal(state.hasMore, false);
  const broken = await client.listPage('/data/companies', { limit: 2, cursor: 'bad' });
  const kept = InventApi.mergePage(state, broken, true);
  assert.equal(kept.rows.length, 3);
  assert.equal(kept.error.kind, 'invalid');
});

test('detectSession: a /me user means session mode', async () => {
  const client = InventApi.createClient({ fetch: fakeFetch({ '/api/v1/me': response(200, { email: 'a@b', role: 'analyst', expires_at: '2030-01-01T00:00:00Z' }) }) });
  const s = await client.detectSession();
  assert.equal(s.mode, 'session');
  assert.equal(s.user.role, 'analyst');
});

test('detectSession: 401 on /me and on the probe means the login screen', async () => {
  const client = InventApi.createClient({ fetch: fakeFetch({
    '/api/v1/me': response(401, { detail: 'authentication_required' }),
    '/api/v1/leads?limit=1': response(401, { detail: 'authentication_required' })
  }) });
  assert.equal((await client.detectSession()).mode, 'login');
});

test('detectSession: 401 on /me but data on the probe means local mode', async () => {
  const client = InventApi.createClient({ fetch: fakeFetch({
    '/api/v1/me': response(401, { detail: 'authentication_required' }),
    '/api/v1/leads?limit=1': response(200, { data: [], next_cursor: null })
  }) });
  const s = await client.detectSession();
  assert.equal(s.mode, 'local');
  assert.equal(s.warning, null);
});

test('detectSession: an unreachable gateway is offline; a 404 probe is local with a warning', async () => {
  const down = InventApi.createClient({ fetch: fakeFetch({ '/api/v1/me': new TypeError('down') }) });
  assert.equal((await down.detectSession()).mode, 'offline');
  const edge = InventApi.createClient({ fetch: fakeFetch({}) });
  const s = await edge.detectSession();
  assert.equal(s.mode, 'local');
  assert.equal(s.warning.kind, 'route_missing');
});

test('toCsv escapes quotes, commas, newlines, objects and formula prefixes', () => {
  const csv = InventApi.toCsv(['a', 'b', 'c'], [
    { a: 'plain', b: 'with,comma', c: 'say "hi"' },
    { a: '=SUM(A1)', b: { k: 1 }, c: null },
    { a: 'two\nlines', b: 7, c: undefined }
  ]);
  assert.equal(csv.charCodeAt(0), 0xfeff);
  const lines = csv.slice(1).split('\r\n');
  assert.equal(lines[0], 'a,b,c');
  assert.equal(lines[1], 'plain,"with,comma","say ""hi"""');
  assert.equal(lines[2], '\'=SUM(A1),"{""k"":1}",');
  assert.equal(lines[3], '"two\nlines",7,');
});

test('pipeline mirrors the backend transitions and labels', () => {
  assert.deepEqual(InventApi.pipeline.allowedTargets('discovered'), ['qualified']);
  assert.deepEqual(InventApi.pipeline.allowedTargets('hot'), ['contacted', 'lost']);
  assert.deepEqual(InventApi.pipeline.allowedTargets('won'), []);
  assert.equal(InventApi.pipeline.canMove('discovered', 'lost'), false);
  assert.equal(InventApi.pipeline.stage('meeting').key, 'reunion');
  assert.equal(InventApi.pipeline.stage('unknown').label, 'unknown');
  assert.equal(InventApi.ruesKey('pending'), 'pendiente');
  assert.equal(InventApi.ruesKey('quarantined'), 'cuarentena');
});

test('fmt handles missing values and relative time', () => {
  assert.equal(InventApi.fmt.number(null), '\u2014');
  assert.equal(InventApi.fmt.percent(null), '\u2014');
  assert.equal(InventApi.fmt.ago(null), '\u2014');
  const now = Date.parse('2026-11-02T12:00:00Z');
  assert.equal(InventApi.fmt.ago('2026-11-02T11:59:30Z', now), 'ahora');
  assert.equal(InventApi.fmt.ago('2026-11-02T11:00:00Z', now), 'hace 1 h');
  assert.equal(InventApi.fmt.ago(now - 3 * 86400000, now), 'hace 3 d');
  assert.equal(InventApi.fmt.cell({ a: 1 }), '{"a":1}');
});
