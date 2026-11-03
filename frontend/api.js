/*
 * Browser data layer of the Invent Market Tracker frontend (no build step).
 *
 * "Invent Market Tracker.dc.html" loads this plain script before the dc runtime, so every
 * design component reads it as window.InventApi. Node tests load the same file through
 * module.exports and pass a fake fetch (frontend/tests/api.test.js).
 *
 * Every request resolves, it never rejects: the result is either
 *   { ok: true, status, data, etag, asOf }
 * or
 *   { ok: false, status, kind, code, detail, message, retryAfter }
 * so a view can show an offline, 401, 403, 429 or 503 state instead of crashing.
 * The API is same-origin (/api/v1 behind the frontend proxy). No token or secret lives
 * here: the session is an HttpOnly cookie and write requests copy the readable CSRF
 * cookie into the X-CSRF-Token header (double submit, services/gateway/app/main.py).
 * User-facing text is Spanish, written with \u escapes so this file stays ASCII.
 */
(function (root, factory) {
  'use strict';
  var api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.InventApi = api;
})(typeof window !== 'undefined' ? window : null, function () {
  'use strict';

  var BASE = '/api/v1';
  var CSRF_COOKIES = ['__Host-csrf', 'csrf'];
  var DEFAULT_TIMEOUT_MS = 15000;
  var CACHE_TTL_MS = 15000;

  var MESSAGES = {
    offline: 'Sin conexi\u00f3n con la API. Revisa la red o que el stack est\u00e9 arriba.',
    timeout: 'La API tard\u00f3 demasiado en responder.',
    unauthorized: 'Tu sesi\u00f3n no es v\u00e1lida. Vuelve a entrar.',
    forbidden: 'Tu rol no tiene permiso para esto.',
    csrf: 'La sesi\u00f3n perdi\u00f3 su token CSRF. Recarga la p\u00e1gina y vuelve a entrar.',
    not_found: 'No existe o no est\u00e1 en tu alcance.',
    route_missing: 'La API no expone esta ruta todav\u00eda.',
    not_in_layer: 'Esta funci\u00f3n pertenece a una capa que a\u00fan no est\u00e1 activa.',
    conflict: 'Otro usuario cambi\u00f3 este registro. Recarga y vuelve a intentar.',
    precondition: 'La versi\u00f3n que editabas ya no es la actual. Recarga y vuelve a intentar.',
    invalid: 'La API rechaz\u00f3 la solicitud.',
    tenant: 'Esta vista necesita una sesi\u00f3n con rol: en modo local la pasarela no env\u00eda el tenant a las rutas restringidas por rol.',
    rate_limited: 'Demasiadas solicitudes. Espera unos segundos y vuelve a intentar.',
    unavailable: 'El servicio no est\u00e1 disponible en este momento.',
    not_implemented: 'Todav\u00eda no est\u00e1 construido en la API.',
    server: 'Error interno del servidor.',
    bad_response: 'La API devolvi\u00f3 una respuesta inesperada.'
  };

  function kindFor(status, code) {
    if (status === 401) return 'unauthorized';
    if (status === 403) return code === 'csrf_token_invalid' ? 'csrf' : 'forbidden';
    if (status === 404) {
      if (code === 'not_in_layer') return 'not_in_layer';
      if (code === 'route_not_found') return 'route_missing';
      return 'not_found';
    }
    if (status === 409) return 'conflict';
    if (status === 412 || status === 428) return 'precondition';
    if (status === 400 && code === 'tenant_required') return 'tenant';
    if (status === 400 || status === 413 || status === 415 || status === 422) return 'invalid';
    if (status === 429) return 'rate_limited';
    if (status === 501) return 'not_implemented';
    if (status === 502 || status === 503 || status === 504) return 'unavailable';
    return 'server';
  }

  function codeOf(detail) {
    if (typeof detail === 'string') return detail;
    if (detail && typeof detail === 'object') {
      if (typeof detail.error === 'string') return detail.error;
      if (typeof detail.code === 'string') return detail.code;
      if (Array.isArray(detail) && detail[0] && typeof detail[0].msg === 'string') return 'validation_error';
    }
    return '';
  }

  function failure(status, kind, detail, retryAfter) {
    var code = codeOf(detail);
    var message = MESSAGES[kind] || MESSAGES.server;
    if (kind === 'rate_limited' && retryAfter) {
      message = 'Demasiadas solicitudes. Espera ' + retryAfter + ' s y vuelve a intentar.';
    }
    if (kind === 'invalid' && code) message = MESSAGES.invalid + ' (' + code + ')';
    if (kind === 'server' && status) message = MESSAGES.server + ' (' + status + ')';
    return { ok: false, status: status, kind: kind, code: code, detail: detail, message: message, retryAfter: retryAfter || null };
  }

  function readCookie(cookieText, name) {
    var parts = String(cookieText || '').split(';');
    for (var i = 0; i < parts.length; i++) {
      var part = parts[i].replace(/^\s+/, '');
      if (part.indexOf(name + '=') === 0) {
        try { return decodeURIComponent(part.slice(name.length + 1)); } catch (e) { return part.slice(name.length + 1); }
      }
    }
    return '';
  }

  function csrfToken(cookieText) {
    for (var i = 0; i < CSRF_COOKIES.length; i++) {
      var value = readCookie(cookieText, CSRF_COOKIES[i]);
      if (value) return value;
    }
    return '';
  }

  function buildQuery(query) {
    if (!query) return '';
    var pairs = [];
    Object.keys(query).forEach(function (key) {
      var value = query[key];
      if (value === undefined || value === null || value === '') return;
      pairs.push(encodeURIComponent(key) + '=' + encodeURIComponent(String(value)));
    });
    return pairs.length ? '?' + pairs.join('&') : '';
  }

  function parseBody(text) {
    if (!text) return null;
    try { return JSON.parse(text); } catch (e) { return text; }
  }

  function header(response, name) {
    try { return response.headers && response.headers.get ? response.headers.get(name) : null; } catch (e) { return null; }
  }

  function newKey() {
    var c = typeof crypto !== 'undefined' ? crypto : null;
    if (c && typeof c.randomUUID === 'function') return c.randomUUID();
    return 'k-' + Date.now().toString(36) + '-' + Math.random().toString(36).slice(2, 12);
  }

  function createClient(options) {
    var opts = options || {};
    var base = opts.base || BASE;
    var fetchImpl = opts.fetch || (typeof fetch === 'function' ? fetch.bind(typeof window !== 'undefined' ? window : undefined) : null);
    var getCookie = opts.getCookie || function () { return typeof document !== 'undefined' ? document.cookie : ''; };
    var timeoutMs = opts.timeoutMs || DEFAULT_TIMEOUT_MS;
    var now = opts.now || function () { return Date.now(); };
    var cache = {};
    var listeners = { unauthorized: [], offline: [], online: [] };

    function emit(name, payload) {
      (listeners[name] || []).forEach(function (fn) { try { fn(payload); } catch (e) { /* listener errors never break a request */ } });
    }

    function request(method, path, req) {
      var r = req || {};
      var verb = String(method || 'GET').toUpperCase();
      var url = base + path + buildQuery(r.query);
      if (!fetchImpl) return Promise.resolve(failure(0, 'offline', 'fetch_unavailable'));
      if (verb === 'GET' && !r.fresh) {
        var hit = cache[url];
        if (hit && now() - hit.at < CACHE_TTL_MS) return Promise.resolve(hit.result);
      }
      var headers = { Accept: 'application/json' };
      var body;
      if (r.raw !== undefined) {
        body = r.raw;
        headers['Content-Type'] = r.contentType || 'text/csv';
      } else if (r.body !== undefined) {
        body = JSON.stringify(r.body);
        headers['Content-Type'] = 'application/json';
      }
      if (verb !== 'GET' && verb !== 'HEAD') {
        var token = csrfToken(getCookie());
        if (token) headers['X-CSRF-Token'] = token;
      }
      Object.keys(r.headers || {}).forEach(function (k) { if (r.headers[k] !== undefined && r.headers[k] !== null) headers[k] = String(r.headers[k]); });
      var controller = typeof AbortController === 'function' ? new AbortController() : null;
      var timedOut = false;
      var timer = controller ? setTimeout(function () { timedOut = true; controller.abort(); }, r.timeoutMs || timeoutMs) : null;
      var init = { method: verb, headers: headers, credentials: 'same-origin', body: body };
      if (controller) init.signal = controller.signal;
      return Promise.resolve()
        .then(function () { return fetchImpl(url, init); })
        .then(function (response) {
          return response.text().then(function (text) { return { response: response, text: text }; });
        })
        .then(function (got) {
          if (timer) clearTimeout(timer);
          var response = got.response;
          var status = response.status;
          var data = status === 204 ? null : parseBody(got.text);
          if (status >= 200 && status < 300) {
            var asOf = header(response, 'X-As-Of');
            var result = { ok: true, status: status, data: data, etag: header(response, 'ETag'), asOf: asOf ? Number(asOf) : null };
            if (verb === 'GET') cache[url] = { at: now(), result: result };
            else cache = {};
            emit('online', null);
            return result;
          }
          var detail = data && typeof data === 'object' && 'detail' in data ? data.detail : data;
          var retry = header(response, 'Retry-After');
          var fail = failure(status, kindFor(status, codeOf(detail)), detail, retry ? Number(retry) : null);
          if (fail.kind === 'unauthorized' && !r.quietAuth) emit('unauthorized', fail);
          return fail;
        })
        .catch(function (error) {
          if (timer) clearTimeout(timer);
          var fail = failure(0, timedOut ? 'timeout' : 'offline', error && error.message ? error.message : 'network_error');
          emit('offline', fail);
          return fail;
        });
    }

    var client = {
      request: request,
      get: function (path, query, extra) { return request('GET', path, Object.assign({}, extra || {}, { query: query })); },
      post: function (path, body, extra) { return request('POST', path, Object.assign({}, extra || {}, { body: body })); },
      put: function (path, body, extra) { return request('PUT', path, Object.assign({}, extra || {}, { body: body })); },
      patch: function (path, body, extra) { return request('PATCH', path, Object.assign({}, extra || {}, { body: body })); },
      on: function (name, fn) { (listeners[name] = listeners[name] || []).push(fn); return function () { listeners[name] = listeners[name].filter(function (f) { return f !== fn; }); }; },
      invalidate: function () { cache = {}; },
      newKey: newKey,

      // A page of a cursor-paginated list ({data, next_cursor, total?}).
      listPage: function (path, query, extra) {
        return client.get(path, query, extra).then(function (res) {
          if (!res.ok) return res;
          var payload = res.data || {};
          var rows = Array.isArray(payload.data) ? payload.data : [];
          return Object.assign({}, res, {
            rows: rows,
            nextCursor: payload.next_cursor || null,
            total: typeof payload.total === 'number' ? payload.total : null
          });
        });
      },

      // Session detection: a user from /me, else the login screen when the gateway enforces
      // identity (a data probe answers 401), else the local mode without identity validation.
      detectSession: function () {
        return client.get('/me', null, { fresh: true, quietAuth: true }).then(function (me) {
          if (me.ok && me.data && me.data.role) return { mode: 'session', user: me.data };
          if (!me.ok && (me.kind === 'offline' || me.kind === 'timeout')) return { mode: 'offline', error: me };
          return client.get('/leads', { limit: 1 }, { fresh: true, quietAuth: true }).then(function (probe) {
            if (probe.ok) return { mode: 'local', warning: null };
            if (probe.kind === 'unauthorized') return { mode: 'login' };
            if (probe.kind === 'offline' || probe.kind === 'timeout') return { mode: 'offline', error: probe };
            return { mode: 'local', warning: probe };
          });
        });
      },
      login: function (email, password) {
        return client.post('/auth/session', { email: email, password: password }, { quietAuth: true });
      },
      logout: function () { return client.post('/logout', undefined, { quietAuth: true }); }
    };
    return client;
  }

  // Merge a listPage result into the accumulated rows of a paginated view.
  function mergePage(prev, res, append) {
    var base = append && prev ? prev : { rows: [], nextCursor: null, total: null };
    if (!res || !res.ok) {
      return { rows: base.rows, nextCursor: base.nextCursor, total: base.total, hasMore: !!base.nextCursor, error: res || failure(0, 'bad_response', null) };
    }
    var rows = (append ? base.rows : []).concat(res.rows || []);
    var total = res.total !== null && res.total !== undefined ? res.total : null;
    return { rows: rows, nextCursor: res.nextCursor || null, total: total, hasMore: !!res.nextCursor, error: null };
  }

  // CSV export. Objects become JSON; cells that a spreadsheet would run as a formula get a
  // leading apostrophe; CRLF line ends and a UTF-8 BOM keep Excel happy.
  function csvCell(value) {
    if (value === null || value === undefined) return '';
    var text = typeof value === 'object' ? JSON.stringify(value) : String(value);
    if (/^[=+\-@\t\r]/.test(text)) text = "'" + text;
    if (/[",\r\n]/.test(text) || /^\s|\s$/.test(text)) text = '"' + text.replace(/"/g, '""') + '"';
    return text;
  }

  function toCsv(columns, rows) {
    var names = (columns || []).map(function (c) { return typeof c === 'string' ? c : c.name; });
    var lines = [names.map(csvCell).join(',')];
    (rows || []).forEach(function (row) {
      lines.push(names.map(function (n) { return csvCell(row ? row[n] : null); }).join(','));
    });
    return '\ufeff' + lines.join('\r\n') + '\r\n';
  }

  function downloadCsv(filename, csv) {
    if (typeof document === 'undefined' || typeof Blob === 'undefined') return false;
    var link = document.createElement('a');
    link.href = URL.createObjectURL(new Blob([csv], { type: 'text/csv;charset=utf-8' }));
    link.download = filename;
    document.body.appendChild(link);
    link.click();
    setTimeout(function () { URL.revokeObjectURL(link.href); link.remove(); }, 0);
    return true;
  }

  // Pipeline stages: API value, design key (StatusPill), Spanish label.
  // Mirrors libs/domain/pipeline.py ALLOWED_TRANSITIONS; the API stays the authority (422).
  var STAGES = [
    { api: 'discovered', key: 'descubierto', label: 'Descubierto' },
    { api: 'qualified', key: 'calificado', label: 'Calificado' },
    { api: 'hot', key: 'hot', label: 'Hot' },
    { api: 'new', key: 'hot', label: 'Nuevo' },
    { api: 'contacted', key: 'contactado', label: 'Contactado' },
    { api: 'meeting', key: 'reunion', label: 'Reuni\u00f3n' },
    { api: 'proposal', key: 'propuesta', label: 'Propuesta' },
    { api: 'won', key: 'ganado', label: 'Ganado' },
    { api: 'lost', key: 'perdido', label: 'Perdido' }
  ];
  var TRANSITIONS = {
    discovered: ['qualified'],
    qualified: ['hot'],
    hot: ['contacted', 'lost'],
    'new': ['contacted', 'lost'],
    contacted: ['meeting', 'lost'],
    meeting: ['proposal', 'lost'],
    proposal: ['won', 'lost'],
    won: [],
    lost: []
  };
  function stage(api) {
    for (var i = 0; i < STAGES.length; i++) if (STAGES[i].api === api) return STAGES[i];
    return { api: api || '', key: 'descubierto', label: api || '\u2014' };
  }
  var pipeline = {
    STAGES: STAGES,
    ORDER: ['discovered', 'qualified', 'hot', 'contacted', 'meeting', 'proposal', 'won'],
    stage: stage,
    allowedTargets: function (api) { return (TRANSITIONS[api] || []).slice(); },
    canMove: function (from, to) { return (TRANSITIONS[from] || []).indexOf(to) !== -1; }
  };

  // RUES status of a company or lead row -> RuesStatus design key.
  function ruesKey(status) {
    return { matched: 'match', pending: 'pendiente', unmatched: 'sinmatch', quarantined: 'cuarentena' }[status] || 'pendiente';
  }

  // Formatting for es-CO. Accepts ISO strings or epoch milliseconds.
  function toDate(value) {
    if (value === null || value === undefined || value === '') return null;
    var d = typeof value === 'number' ? new Date(value) : new Date(String(value));
    return isNaN(d.getTime()) ? null : d;
  }
  var fmt = {
    number: function (n, digits) {
      if (n === null || n === undefined || n === '' || isNaN(Number(n))) return '\u2014';
      var d = digits === undefined ? 0 : digits;
      return Number(n).toLocaleString('es-CO', { minimumFractionDigits: d, maximumFractionDigits: d });
    },
    percent: function (ratio, digits) {
      if (ratio === null || ratio === undefined || isNaN(Number(ratio))) return '\u2014';
      return fmt.number(Number(ratio) * 100, digits === undefined ? 1 : digits) + ' %';
    },
    date: function (value) {
      var d = toDate(value);
      if (!d) return '\u2014';
      return d.toLocaleString('es-CO', { day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit' });
    },
    ago: function (value, nowMs) {
      var d = toDate(value);
      if (!d) return '\u2014';
      var seconds = Math.round(((nowMs || Date.now()) - d.getTime()) / 1000);
      if (seconds < 60) return 'ahora';
      if (seconds < 3600) return 'hace ' + Math.floor(seconds / 60) + ' min';
      if (seconds < 86400) return 'hace ' + Math.floor(seconds / 3600) + ' h';
      return 'hace ' + Math.floor(seconds / 86400) + ' d';
    },
    cell: function (value) {
      if (value === null || value === undefined) return '';
      if (typeof value === 'object') return JSON.stringify(value);
      return String(value);
    }
  };

  var api = {
    BASE: BASE,
    MESSAGES: MESSAGES,
    createClient: createClient,
    mergePage: mergePage,
    toCsv: toCsv,
    downloadCsv: downloadCsv,
    csrfToken: csrfToken,
    pipeline: pipeline,
    ruesKey: ruesKey,
    fmt: fmt,
    kindFor: kindFor
  };
  // The shared browser client; the Node tests build their own with a fake fetch.
  if (typeof window !== 'undefined' && typeof fetch === 'function') api.client = createClient();
  return api;
});
