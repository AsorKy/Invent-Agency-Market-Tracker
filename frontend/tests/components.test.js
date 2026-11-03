/*
 * Offline checks of the dc design components (no browser, no network).
 *
 * For every frontend/*.dc.html the test compiles the logic script exactly as the dc runtime
 * does (new Function with DCLogic), mounts it in a sandbox whose window.fetch serves fixtures
 * (an empty database and a small populated one), runs componentDidMount, and checks that
 * renderVals() does not throw and that every {{ name }} of the template resolves to a value
 * key, a prop or a loop variable. It also fails when mock data of the original design export
 * comes back (seed functions, demo literals, invented KPIs). Run: make test-frontend.
 */
'use strict';

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const path = require('node:path');
const vm = require('node:vm');
const { execFileSync } = require('node:child_process');

const DIR = path.join(__dirname, '..');
const API_SOURCE = fs.readFileSync(path.join(DIR, 'api.js'), 'utf8');
const APP = 'Invent Market Tracker';
// The style guide board documents components with sample text; it is not part of the app.
const REFERENCE_ONLY = new Set(['Design System']);

// Files are enumerated the way the repository rules ask: tracked or untracked, never ignored.
function components() {
  const out = execFileSync('git', ['ls-files', '--cached', '--others', '--exclude-standard', '-z', '--', '.'], { cwd: DIR, encoding: 'utf8' });
  return out.split('\0').filter((f) => f && !f.includes('/') && f.endsWith('.dc.html')).map((f) => f.slice(0, -'.dc.html'.length)).sort();
}

function parse(name) {
  const src = fs.readFileSync(path.join(DIR, name + '.dc.html'), 'utf8');
  const open = src.indexOf('<x-dc>');
  const close = src.lastIndexOf('</x-dc>');
  assert.ok(open >= 0 && close > open, name + ': no <x-dc> block');
  const template = src.slice(open + 6, close);
  const m = /<script type="text\/x-dc" data-dc-script[^>]*>([\s\S]*?)<\/script>/.exec(src);
  return { src, template, js: m ? m[1] : '' };
}

// ---- fixtures ---------------------------------------------------------------------------

const NOW = Date.parse('2026-11-02T12:00:00Z');
const LEAD = {
  id: '11111111-1111-4111-8111-111111111111', company_id: '22222222-2222-4222-8222-222222222222',
  legal_name: 'Acme Logistica SAS', service_line: 'SaaS', stage: 'discovered', city: 'Medellin',
  segment: 'H49', score: 72, version: 1, created_at: '2026-10-30T10:00:00Z', updated_at: '2026-11-01T10:00:00Z',
  rues_status: 'pending', decision_makers: [{ name: 'Ana Perez', title: 'CTO', profile_url: 'https://www.linkedin.com/in/x' }],
  decision_makers_available: true
};
const COLUMNS = { discovered: [], qualified: [], hot: [], new: [], contacted: [], meeting: [], proposal: [], won: [], lost: [] };
const CATALOG = {
  datasets: [
    { id: 'raw_snapshots', title: 'Snapshots crudos', description: 'Respuestas de Bright Data', service: 'ingest', origin: 'bright_data_raw', count: 2, columns: [{ name: 'id', type: 'uuid' }, { name: 'payload', type: 'jsonb' }] },
    { id: 'companies', title: 'Empresas', description: 'core.company', service: 'core', origin: 'derived', count: 1, columns: [{ name: 'id', type: 'uuid' }, { name: 'legal_name', type: 'text' }] },
    { id: 'lead_scores', title: 'Scores', description: 'ml.lead_score', service: 'ml', origin: 'derived', count: null, columns: [], unavailable: true }
  ]
};

function fixtures(populated) {
  const page = (rows) => ({ data: rows, next_cursor: null });
  const insights = populated
    ? { as_of: '2026-11-02T11:00:00Z', stale: false, companies: 3, by_city_category: [{ city: 'Medellin', category: 'Restaurante', companies: 3, listings: 3, avg_rating: 4.4, avg_reviews: 120 }], maps_available: true, maps_by_city: [{ city: 'Medellin', listings: 3, avg_rating: 4.4, avg_reviews: 120 }], hiring: [{ city: 'Medellin', keyword: 'python', postings: 4, companies: 2 }], mid_size: { min_employees: 11, max_employees: 500, companies: 3, companies_with_size: 2, mid_size: 1, share: 0.5, by_city: [{ city: 'Medellin', companies: 3, companies_with_size: 2, mid_size: 1, share: 0.5 }] } }
    : { as_of: '2026-11-02T11:00:00Z', stale: false, companies: 0, by_city_category: [], maps_available: false, maps_by_city: [], hiring: [], mid_size: { min_employees: 11, max_employees: 500, companies: 0, companies_with_size: 0, mid_size: 0, share: null, by_city: [] } };
  const leads = populated ? [LEAD, Object.assign({}, LEAD, { id: '3', stage: 'hot', rues_status: 'quarantined', score: null, decision_makers: [] })] : [];
  const project = { id: '44444444-4444-4444-8444-444444444444', lead_id: LEAD.id, name: 'Proyecto Acme', service_line: 'SaaS', hypothesis: 'h', status: 'active', estimated_roi: 1.5, metadata: {}, created_at: '2026-11-01T10:00:00Z', updated_at: '2026-11-01T10:00:00Z' };
  return {
    '/api/v1/me': [401, { detail: 'authentication_required' }],
    '/api/v1/leads': [200, page(leads)],
    ['/api/v1/leads/' + LEAD.id]: [200, Object.assign({}, LEAD, { source: 'bright_data', qualification_reason: { rule: 'v0' }, last_transition_at: null })],
    ['/api/v1/leads/' + LEAD.id + '/brief']: populated
      ? [200, { company_id: LEAD.company_id, source_version: 1, content: { headline: 'H49 - Medellin - Score 72', alerts: [{ code: 'RUES_PENDING', severity: 'warning', message: 'No RUES match yet.' }], next_steps: ['Collect verified company signals.'], coverage: { company: true, rues: false, activity: false }, activity: [{ type: 'call', at: '2026-11-01' }], priority: 0.4 }, generated_at: NOW, summarizer_version: 'rules-v1', degraded: false, stale: false, drivers: [{ feature: 'signals', contribution: 0.3 }] }]
      : [404, { detail: 'company_not_found' }],
    '/api/v1/pipeline/kanban': [200, { as_of: '2026-11-02T11:00:00Z', columns: populated ? Object.assign({}, COLUMNS, { hot: [leads[1]] }) : COLUMNS }],
    '/api/v1/companies': [200, page(populated ? [{ id: LEAD.company_id, legal_name: 'Acme Logistica SAS', city: 'Medellin', rues_status: 'pending' }] : [])],
    '/api/v1/companies/insights': [200, insights],
    '/api/v1/pulse/market': [200, { signals: populated ? 10 : 0, leads: populated ? 2 : 0, hot_leads: populated ? 1 : 0, as_of: null }],
    '/api/v1/pulse/pipeline-backlog': [200, { active: null, hot: null, capacity_estimate: null, as_of: NOW, stale: false }],
    '/api/v1/pulse/source-efficiency': [200, { sources: populated ? [{ name: 'maps', cost_usd: '12.00', leads: 3, meetings: 1, cost_per_meeting: '12.00', roas: null }] : [], as_of: NOW, stale: false, leads_concentration: null }],
    '/api/v1/pulse/time-in-pipeline': [200, { groups: populated ? [{ key: 'SaaS', p50_days: 3, p90_days: 9, n: 4, low_sample: true }] : [], as_of: NOW, stale: false, p50_days_stats: null }],
    '/api/v1/pulse/top-sources': [200, { ranking: [], as_of: NOW, stale: false }],
    '/api/v1/pulse/unprocessed-signals': [200, { points: populated ? [{ t: '2026-10-26', total: 8, failed: 1, rate: 0.125 }] : [], as_of: NOW, stale: false }],
    '/api/v1/ops/health': [200, { sources: populated ? [{ source: 'maps', as_of: NOW - 3600000, stale: false }] : [], outbox_backlog: populated ? 2 : null, as_of: NOW, stale: false }],
    '/api/v1/territory/opportunity': [200, { territories: populated ? [{ rank: 1, city: 'Medellin', territory_code: '05001-14', territory_name: 'El Poblado', ipet: 0.8, imd: 0.6, iop: 0.7, n_in_city: 3, low_sample: true }] : [], as_of: NOW, stale: false, method_version: 'iop-v1' }],
    '/api/v1/projects': [200, page(populated ? [project] : [])],
    ['/api/v1/funnel/projects/' + project.id]: [400, { detail: 'tenant_required' }],
    '/api/v1/admin/tenant-config': populated ? [200, { version: 2, scoring_rules: {}, brief_rules: {}, bright_data_budget_usd: 50, funnel_rules: null }] : [400, { detail: 'tenant_required' }],
    '/api/v1/validation/meta-ml': [400, { detail: 'tenant_required' }],
    '/api/v1/segments': [501, { detail: 'segment_read_model_pending' }],
    '/api/v1/data/catalog': [200, CATALOG],
    '/api/v1/data/raw_snapshots': [200, { dataset: 'raw_snapshots', columns: CATALOG.datasets[0].columns, data: populated ? [{ id: 'a', payload: { name: 'Acme', nested: { k: [1, 2] } } }] : [], next_cursor: populated ? 'c2' : null, total: populated ? 2 : 0, truncated_cells: !!populated }],
    '/api/v1/data/companies': [200, { dataset: 'companies', columns: CATALOG.datasets[1].columns, data: [], next_cursor: null, total: 0, truncated_cells: false }]
  };
}

function fakeFetch(table, calls) {
  return async (url) => {
    calls.push(url);
    const bare = url.split('?')[0];
    const hit = table[bare] || [404, { detail: 'route_not_found' }];
    const body = JSON.stringify(hit[1]);
    return { status: hit[0], headers: { get: () => null }, text: async () => body };
  };
}

// ---- sandbox ----------------------------------------------------------------------------

class FakeLogic {
  constructor(props) { this.props = props || {}; this.state = {}; }
  setState(update, cb) { const patch = typeof update === 'function' ? update(this.state) : update; this.state = Object.assign({}, this.state, patch); if (cb) cb(); }
  forceUpdate() {}
  componentDidMount() {}
  componentDidUpdate() {}
  componentWillUnmount() {}
  renderVals() { return {}; }
}

function sandbox(populated) {
  const calls = [];
  const noop = () => {};
  const element = () => ({ click: noop, remove: noop, focus: noop, setAttribute: noop, style: {}, appendChild: noop });
  const ctx = {
    console, Promise, JSON, Math, Date, Intl, URL, URLSearchParams, Object, Array, String, Number, Boolean, RegExp, Error, TypeError, Map, Set,
    setTimeout, clearTimeout, setInterval: () => 0, clearInterval: noop, queueMicrotask, AbortController, encodeURIComponent, decodeURIComponent, isNaN, parseInt, parseFloat,
    Blob: class { constructor(parts) { this.parts = parts; } },
    crypto: { randomUUID: () => '00000000-0000-4000-8000-000000000000' },
    localStorage: { getItem: () => null, setItem: noop, removeItem: noop },
    navigator: { onLine: true, clipboard: { writeText: async () => {} } },
    location: { hash: '', href: 'http://localhost:3000/' + encodeURIComponent(APP) + '.dc.html', pathname: '/' + APP + '.dc.html', reload: noop },
    history: { replaceState: noop, pushState: noop },
    document: { cookie: '', activeElement: null, body: element(), querySelector: () => null, querySelectorAll: () => [], getElementById: () => null, createElement: element, addEventListener: noop, removeEventListener: noop },
    addEventListener: noop, removeEventListener: noop, innerWidth: 1440,
    fetch: fakeFetch(fixtures(populated), calls)
  };
  ctx.window = ctx;
  ctx.self = ctx;
  vm.createContext(ctx);
  vm.runInContext(API_SOURCE, ctx, { filename: 'api.js' });
  assert.ok(ctx.InventApi && ctx.InventApi.client, 'api.js must expose window.InventApi.client');
  ctx.__calls = calls;
  return ctx;
}

function compile(name, js, ctx) {
  const wrapped = '(function (DCLogic, StreamableLogic, React) {\n' + js + '\n;return (typeof Component !== "undefined" && Component) || undefined;\n})';
  const factory = vm.runInContext(wrapped, ctx, { filename: name + '.dc.html' });
  const React = { createElement: (type, props, ...children) => ({ type, props, children }), Fragment: 'Fragment' };
  return factory(FakeLogic, FakeLogic, React);
}

async function settle() {
  for (let i = 0; i < 12; i++) await new Promise((resolve) => setImmediate(resolve));
}

// ---- template references ------------------------------------------------------------------

const LITERAL = /^(?:true|false|null|undefined|-?\d+(?:\.\d+)?|'[^']*'|"[^"]*")$/;
function heads(expr) {
  const out = [];
  String(expr).split(/===|!==|==|!=/).forEach((part) => {
    const t = part.trim().replace(/^[!(\s]+/, '').replace(/[)\s]+$/, '');
    if (!t || LITERAL.test(t)) return;
    const m = /^[A-Za-z_$][A-Za-z0-9_$]*/.exec(t);
    if (m) out.push(m[0]);
  });
  return out;
}

function templateRefs(template) {
  const loopVars = new Set(['$index']);
  for (const m of template.matchAll(/<sc-for\b[^>]*\bas="([^"]+)"/g)) loopVars.add(m[1]);
  const refs = new Set();
  for (const m of template.matchAll(/\{\{([\s\S]+?)\}\}/g)) heads(m[1]).forEach((h) => { if (!loopVars.has(h)) refs.add(h); });
  return refs;
}

function checkRefs(name, template, vals) {
  const missing = [...templateRefs(template)].filter((ref) => !(ref in vals));
  assert.deepEqual(missing, [], name + ': template names without a value');
}

function checkBalance(name, template) {
  for (const tag of ['sc-if', 'sc-for', 'dc-import']) {
    const opened = (template.match(new RegExp('<' + tag + '\\b', 'g')) || []).length;
    const closed = (template.match(new RegExp('</' + tag + '>', 'g')) || []).length;
    assert.equal(opened, closed, name + ': unbalanced <' + tag + '>');
  }
}

// Props each component receives from the main app (callbacks are no-ops here).
const noop = () => {};
const PROPS = {
  AdminView: [{ mode: 'imports', role: 'admin' }, { mode: 'config', role: 'admin' }, { mode: 'metrics', role: 'analyst' }],
  AnalyticsView: [{ layer: 0, role: 'admin', onGo: noop }],
  DataView: [{ role: 'admin', onNotify: noop }, { role: 'admin', dataset: 'raw_snapshots', onNotify: noop }],
  InboxView: [{ role: 'admin', city: '', onOpen: noop, onNotify: noop, onGo: noop }],
  LeadPanel: [{ brief: null, briefError: null, rues: 'pending', onModels: noop }],
  LoginCard: [{ state: 'default', onSubmit: noop }, { state: 'error' }, { state: 'expired' }],
  OpsView: [{}],
  PipelineView: [{ role: 'admin', city: '', onOpen: noop, onNotify: noop }],
  PlotSvg: [{ spec: {} }],
  PortfolioView: [{ part: 0, onModels: noop }],
  PrivacyView: [{ onBack: noop }],
  ProjectsView: [{ role: 'admin', onNotify: noop, onOpenLead: noop }],
  ResearchView: [{ role: 'admin' }],
  RuesStatus: [{ state: 'pending' }, { state: 'matched' }, { state: 'unmatched' }],
  Sidebar: [{ active: 'market', items: [], collapsed: false, env: 'local' }],
  StatusPill: [{ status: 'discovered' }, { status: 'won' }],
  StreamlitView: [{ page: 'analitica', tab: 0 }, { page: 'segmentos' }, { page: 'modelos', comp: 'Scoring' }],
  TerritoryView: [{ city: '' }],
  TopBar: [{ crumb: 'Mercado', role: 'admin', local: true, who: 'Modo local', initials: 'ML', city: '', scope: '', theme: 'light', compact: false, onMenu: noop, onTheme: noop, onCity: noop, onLogout: noop }]
};

for (const populated of [false, true]) {
  const label = populated ? 'populated' : 'empty';
  for (const name of components()) {
    if (name === APP || REFERENCE_ONLY.has(name)) continue;
    test(name + ' mounts and renders against the ' + label + ' API', async () => {
      const { template, js } = parse(name);
      checkBalance(name, template);
      const ctx = sandbox(populated);
      const Cls = compile(name, js, ctx);
      assert.equal(typeof Cls, 'function', name + ' must define class Component');
      for (const props of PROPS[name] || [{}]) {
        const logic = new Cls(Object.assign({}, props));
        logic.componentDidMount();
        await settle();
        const vals = Object.assign({}, props, logic.renderVals());
        checkRefs(name, template, vals);
      }
    });
  }
}

const ROUTES = ['market', 'inbox', 'leads', 'pipeline', 'projects', 'analytics', 'st-analitica', 'st-modelos', 'st-redes', 'st-segmentos', 'territory', 'validation', 'ops', 'imports', 'config', 'metrics', 'research', 'data', 'rep', 'privacy'];

for (const populated of [false, true]) {
  test('the app renders every route in local mode against the ' + (populated ? 'populated' : 'empty') + ' API', async () => {
    const { template, js } = parse(APP);
    checkBalance(APP, template);
    const ctx = sandbox(populated);
    const Cls = compile(APP, js, ctx);
    const logic = new Cls({});
    logic.componentDidMount();
    await settle();
    assert.equal(logic.state.mode, 'local', 'no /me user and data on the probe means local mode');
    assert.equal(logic.state.role, 'admin');
    for (const route of ROUTES) {
      logic.go(route);
      await settle();
      const vals = logic.renderVals();
      checkRefs(APP + ' #' + route, template, vals);
      assert.equal(vals.isForbidden, false, route + ' must be visible to the local admin');
    }
    logic.openLead(LEAD.id, LEAD);
    await settle();
    const vals = logic.renderVals();
    checkRefs(APP + ' #lead', template, vals);
    if (populated) assert.equal(vals.isLead, true);
    logic.componentWillUnmount();
  });
}

test('the app shows the login screen when the gateway enforces identity', async () => {
  const { js } = parse(APP);
  const ctx = sandbox(false);
  ctx.fetch = async (url) => ({ status: 401, headers: { get: () => null }, text: async () => '{"detail":"authentication_required"}' });
  ctx.InventApi.client = ctx.InventApi.createClient({ fetch: ctx.fetch, getCookie: () => '' });
  const Cls = compile(APP, js, ctx);
  const logic = new Cls({});
  logic.componentDidMount();
  await settle();
  assert.equal(logic.state.mode, 'login');
  assert.equal(logic.renderVals().isLogin, true);
  logic.componentWillUnmount();
});

// ---- no mock data -----------------------------------------------------------------------

const MOCK_PATTERNS = [
  /\bseed[A-Z]\w*\s*\(/, /Empresa Demo/, /Cliente Demo/, /\bDemo:/, />DEMO</, /usuario@invent\.example/,
  /'BD rep [12]'/, /hace 3 min/, /1\.284/, /US\$ 450/, /AUC 0,81/, /Brier 0,14/, /Comuna 14/, /br_0087/,
  /Simular conflicto/, /evento_feria_2026/, /rues_lote_03/, /Chapinero/, /HORECA Medell/, /a Contactado 62 %/
];

test('no mock data or demo literal remains in the app files', () => {
  const files = ['api.js', ...components().filter((n) => !REFERENCE_ONLY.has(n)).map((n) => n + '.dc.html')];
  const found = [];
  for (const file of files) {
    const text = fs.readFileSync(path.join(DIR, file), 'utf8');
    for (const pattern of MOCK_PATTERNS) if (pattern.test(text)) found.push(file + ': ' + pattern);
  }
  assert.deepEqual(found, []);
});

test('every component the app imports exists, and api.js loads before the runtime', () => {
  const { src, template } = parse(APP);
  const names = new Set();
  for (const name of components()) {
    for (const m of parse(name).template.matchAll(/<dc-import name="([^"]+)"/g)) names.add(m[1]);
  }
  for (const m of template.matchAll(/<dc-import name="([^"]+)"/g)) names.add(m[1]);
  const missing = [...names].filter((n) => !fs.existsSync(path.join(DIR, n + '.dc.html')));
  assert.deepEqual(missing, []);
  assert.ok(src.indexOf('src="./api.js"') > -1 && src.indexOf('src="./api.js"') < src.indexOf('src="./support.js"'));
});
