/* Нутрибот: приложение внутри Telegram. Экран дня, добавление продуктов и наборов, правка записей. */
(function () {
  'use strict';

  /* Telegram puts the signed launch data into the page address. Reading it there means the page does not
     have to wait for telegram-web-app.js, which loads in the background (telegram.org can be slow). */
  function TG() { return window.Telegram && window.Telegram.WebApp; }
  var launch = (function () { try { return new URLSearchParams(location.hash.slice(1)); } catch (e) { return new URLSearchParams(''); } })();
  var launchData = launch.get('tgWebAppData') || '';
  try { if (launchData) sessionStorage.setItem('nutri:init', launchData); else launchData = sessionStorage.getItem('nutri:init') || ''; } catch (e) { /* storage may be off */ }
  function initData() { var tg = TG(); return (tg && tg.initData) || launchData; }
  var MULTS = [[0.5, '×½'], [1, '×1'], [1.5, '×1½'], [2, '×2']];
  var TABS = [['recent', 'Недавнее'], ['freq', 'Частое'], ['sets', 'Наборы'], ['vv', 'ВкусВилл']];
  var FORMS = {
    'штука': ['штука', 'штуки', 'штук'], 'банка': ['банка', 'банки', 'банок'], 'упаковка': ['упаковка', 'упаковки', 'упаковок'],
    'пачка': ['пачка', 'пачки', 'пачек'], 'бутылка': ['бутылка', 'бутылки', 'бутылок'], 'чашка': ['чашка', 'чашки', 'чашек'],
    'стакан': ['стакан', 'стакана', 'стаканов'], 'порция': ['порция', 'порции', 'порций'], 'ломтик': ['ломтик', 'ломтика', 'ломтиков'],
    'кусок': ['кусок', 'куска', 'кусков'], 'ягода': ['ягода', 'ягоды', 'ягод'], 'яйцо': ['яйцо', 'яйца', 'яиц'],
    'трубочка': ['трубочка', 'трубочки', 'трубочек'], 'лепёшка': ['лепёшка', 'лепёшки', 'лепёшек'], 'долька': ['долька', 'дольки', 'долек'],
    'набор': ['набор', 'набора', 'наборов'], 'бутерброд': ['бутерброд', 'бутерброда', 'бутербродов']
  };
  var WEEKDAYS = ['Вс', 'Пн', 'Вт', 'Ср', 'Чт', 'Пт', 'Сб'];
  var MONTHS = ['января', 'февраля', 'марта', 'апреля', 'мая', 'июня', 'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря'];

  var state = {
    view: 'day', date: todayMsk(), day: null, dayError: '', catalog: null, catalogError: '',
    addMeal: '', tab: 'recent', query: '', vv: { q: '', items: null, error: '', loading: false },
    sheet: null, busy: false, outbox: [], flushing: false, catalogDirty: false,
    stats: { mode: 'week', anchor: todayMsk(), data: null, stale: null, error: '', selected: -1 }
  };
  var toastTimer = 0, vvTimer = 0;

  /* ---------- helpers ---------- */
  function $(s) { return document.querySelector(s); }
  function esc(s) { return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]; }); }
  function fmt(n, d) {
    var parts = Number(n || 0).toFixed(d || 0).split('.');
    var int = parts[0].replace(/\B(?=(\d{3})+(?!\d))/g, ' ');
    var frac = (parts[1] || '').replace(/0+$/, '');
    return frac ? int + ',' + frac : int;
  }
  function num(s) { var v = parseFloat(String(s).replace(',', '.')); return isFinite(v) && v >= 0 ? v : 0; }
  function norm(s) { return String(s || '').toLowerCase().replace(/ё/g, 'е'); }
  function bju(o) { return 'Б ' + fmt(o.protein_g, 1) + ' · Ж ' + fmt(o.fat_g, 1) + ' · У ' + fmt(o.carbs_g, 1); }
  function plural(n, forms) {
    if (n !== Math.floor(n)) return forms[1];
    var a = n % 10, b = n % 100;
    if (a === 1 && b !== 11) return forms[0];
    if (a >= 2 && a <= 4 && (b < 10 || b >= 20)) return forms[1];
    return forms[2];
  }
  function unitWord(name, qty) { var f = FORMS[name] || FORMS[String(name).toLowerCase()]; return f ? plural(qty, f) : name; }
  function newRecordId() {
    var bytes = new Uint8Array(4); (window.crypto || window.msCrypto).getRandomValues(bytes);
    return 'r' + Array.prototype.map.call(bytes, function (b) { return ('0' + b.toString(16)).slice(-2); }).join('');
  }
  function mskNow() { return new Date(Date.now() + (new Date().getTimezoneOffset() + 180) * 60000); }
  function pad(n) { return (n < 10 ? '0' : '') + n; }
  function dateStr(d) { return pad(d.getDate()) + '.' + pad(d.getMonth() + 1) + '.' + d.getFullYear(); }
  function todayMsk() { return dateStr(mskNow()); }
  function parseDate(s) { var p = s.split('.'); return new Date(Number(p[2]), Number(p[1]) - 1, Number(p[0])); }
  function shiftDate(s, days) { var d = parseDate(s); d.setDate(d.getDate() + days); return dateStr(d); }
  function dateTitle(s) { var d = parseDate(s); return WEEKDAYS[d.getDay()] + ', ' + d.getDate() + ' ' + MONTHS[d.getMonth()]; }
  function dateKey(s) { var p = (s || '').split('.'); return p.length === 3 ? p[2] + p[1] + p[0] : ''; }
  function haptic(kind) { try { var tg = TG(); tg && tg.HapticFeedback && tg.HapticFeedback.notificationOccurred(kind); } catch (e) { /* not in Telegram */ } }

  function toast(text, bad) {
    var el = $('#toast'); el.textContent = text; el.className = 'toast' + (bad ? ' bad' : ''); el.hidden = false;
    clearTimeout(toastTimer); toastTimer = setTimeout(function () { el.hidden = true; }, bad ? 5000 : 2800);
  }

  function api(method, path, body, keepalive) {
    return fetch('api/' + path, {
      method: method,
      headers: { 'Content-Type': 'application/json', 'X-Telegram-Init-Data': initData() },
      body: body ? JSON.stringify(body) : undefined,
      keepalive: !!keepalive                       // lets a write finish even if the app is closed right after the tap
    }).then(function (res) {
      return res.json().catch(function () { return {}; }).then(function (data) {
        if (res.ok) return data;
        var text = res.status === 401 ? 'Открой приложение кнопкой «Дневник» в чате Нутрибота: вход идёт через Telegram.'
          : res.status === 403 ? 'Этот дневник закрыт для других пользователей.' : (data.message || 'Сервер ответил ошибкой ' + res.status);
        var err = new Error(text); err.status = res.status; throw err;
      });
    }, function () { var err = new Error('Нет связи с сервером. Проверь интернет и попробуй ещё раз.'); err.status = 0; throw err; });
  }

  /* ---------- nutrition math (preview only; the server recalculates) ---------- */
  function factor(base, unit, qty) { var amount = qty * unit.amount; return base === '1 шт' ? amount : amount / 100; }
  function calc(p, unit, qty) {
    var k = factor(p.base, unit, qty);
    return { kcal: p.kcal * k, protein_g: p.protein_g * k, fat_g: p.fat_g * k, carbs_g: p.carbs_g * k };
  }
  function isBaseUnit(unit) { return unit.name === 'г' || unit.name === 'мл'; }
  function stepOf(unit) { return unit.name === 'г' ? 5 : unit.name === 'мл' ? 50 : 0; }
  function nextQty(unit, qty, up) {
    var st = stepOf(unit), q;
    if (st) q = Math.max(st, Math.round((qty + (up ? st : -st)) / st) * st);
    else q = up ? (qty < 1 ? 1 : qty + 1) : (qty <= 1 ? 0.5 : qty - 1);
    return Number(q.toFixed(2));
  }
  function portionText(p, unit, qty) {
    if (isBaseUnit(unit)) return fmt(qty, 2) + ' ' + unit.name;
    var head = fmt(qty, 2) + ' ' + unitWord(unit.name, qty);
    if (p.base === '1 шт') return head;
    return head + ' (' + fmt(qty * unit.amount, 1) + ' ' + (p.base === '100 мл' ? 'мл' : 'г') + ')';
  }
  function productById(id) {
    var list = (state.catalog && state.catalog.products) || [];
    for (var i = 0; i < list.length; i++) if (list[i].product_id === id) return list[i];
    return null;
  }
  function unitByName(p, name) {
    for (var i = 0; i < p.unit_list.length; i++) if (p.unit_list[i].name === name) return i;
    return 0;
  }
  function defaultChoice(p) {
    if (p.last_unit && p.last_quantity) return { ui: unitByName(p, p.last_unit), qty: p.last_quantity };
    var ui = p.default_unit ? unitByName(p, p.default_unit) : 0;
    return { ui: ui, qty: isBaseUnit(p.unit_list[ui]) ? 100 : 1 };
  }
  function vvProduct(item) {
    var np = item.new_product, base = np.base === '100 мл' ? 'мл' : 'г', units = [];
    if (item.package) units.push({ name: 'упаковка', amount: item.package, label: 'упаковка · ' + fmt(item.package, 1) + ' ' + base });
    units.push({ name: base, amount: 1, label: base === 'г' ? 'граммы' : 'миллилитры' });
    return { product_id: '', name: item.name, base: np.base, kcal: np.kcal, protein_g: np.protein_g, fat_g: np.fat_g, carbs_g: np.carbs_g,
             unit_list: units, precision: 'точно', new_product: np, vv: true };
  }

  /* ---------- phone-side cache: last known screens are shown at once, fresh data replaces them ---------- */
  var CACHE_VERSION = 1, DAYS_KEPT = 14;
  function cacheGet(key) {
    try { var v = JSON.parse(localStorage.getItem('nutri:' + key) || 'null'); return v && v.v === CACHE_VERSION ? v : null; } catch (e) { return null; }
  }
  function cacheSet(key, data) {
    try {
      localStorage.setItem('nutri:' + key, JSON.stringify({ v: CACHE_VERSION, at: Date.now(), data: data }));
      if (key.indexOf('day:') !== 0) return;
      var index = (cacheGet('days') || { data: [] }).data.filter(function (d) { return d !== key; }); index.push(key);
      while (index.length > DAYS_KEPT) localStorage.removeItem('nutri:' + index.shift());
      localStorage.setItem('nutri:days', JSON.stringify({ v: CACHE_VERSION, at: Date.now(), data: index }));
    } catch (e) { /* private mode or full storage: the app simply works without the cache */ }
  }
  function clock(ms) { var d = new Date(ms + (new Date().getTimezoneOffset() + 180) * 60000); return pad(d.getHours()) + ':' + pad(d.getMinutes()); }
  function staleLabel(at) { var days = Math.floor((Date.now() - at) / 86400000); return days >= 1 ? 'данные ' + (days === 1 ? 'вчерашние' : days + ' дн. назад') : 'данные на ' + clock(at); }

  /* ---------- outbox: a new entry is shown at once and written in the background ----------
     Each item keeps the request and the rows to show. The record ids are made on the phone, so sending the
     same item again (after a lost answer or a restart) can never create a duplicate. */
  function loadOutbox() { var saved = cacheGet('outbox'); return saved && Array.isArray(saved.data) ? saved.data : []; }
  function saveOutbox() { cacheSet('outbox', state.outbox); }
  function provisional(p, unit, qty, meal, recordId, setName, groupId) {
    var v = calc(p, unit, qty);
    return { record_id: recordId, editable: false, pending: true, date: state.date, meal: meal, product: p.vv ? p.new_product.name : p.name,
             portion: portionText(p, unit, qty), kcal: Math.round(v.kcal * 10) / 10, protein_g: Math.round(v.protein_g * 10) / 10,
             fat_g: Math.round(v.fat_g * 10) / 10, carbs_g: Math.round(v.carbs_g * 10) / 10, estimated: p.precision === 'оценка',
             product_id: p.product_id || '', quantity: qty, unit: unit.name, unit_list: p.unit_list, set_name: setName || '', group_id: groupId || '' };
  }
  function enqueue(payload, rows, label) {
    state.outbox.push({ key: rows[0].record_id, payload: payload, rows: rows, label: label, failed: '' });
    saveOutbox(); closeSheet(); state.view = 'day'; state.query = ''; render(); haptic('success'); flushOutbox();
  }
  /* The day as the user should see it: what the table has, plus entries that are still on their way. */
  function shownDay() {
    var day = state.day; if (!day) return null;
    var known = {}; day.entries.forEach(function (e) { known[e.record_id] = true; });
    var extra = [];
    state.outbox.forEach(function (item) {
      if (item.payload.date !== day.date) return;
      item.rows.forEach(function (row) { if (!known[row.record_id]) { var copy = JSON.parse(JSON.stringify(row)); copy.failed = item.failed; copy.outboxKey = item.key; extra.push(copy); } });
    });
    if (!extra.length) return day;
    var entries = day.entries.concat(extra), totals = { kcal: 0, protein_g: 0, fat_g: 0, carbs_g: 0 };
    entries.forEach(function (e) { Object.keys(totals).forEach(function (k) { totals[k] += e[k]; }); });
    Object.keys(totals).forEach(function (k) { totals[k] = Math.round(totals[k] * 10) / 10; });
    return { date: day.date, meals: day.meals, entries: entries, totals: totals, targets: day.targets };
  }
  function flushOutbox(retryFailed) {
    if (state.flushing) return;
    if (retryFailed) state.outbox.forEach(function (item) { item.failed = ''; });
    var item = state.outbox.filter(function (x) { return !x.failed; })[0];
    if (!item) { if (state.catalogDirty) { state.catalogDirty = false; loadCatalog(true); } return; }
    state.flushing = true;
    api('POST', 'entries', item.payload, true).then(function (res) {
      state.outbox = state.outbox.filter(function (x) { return x.key !== item.key; }); saveOutbox();
      state.catalogDirty = true;
      if (res.day) { cacheSet('day:' + res.day.date, res.day); if (res.day.date === state.date) showDay(res.day); }
    }, function (err) {
      if (err.status >= 400 && err.status < 500 && err.status !== 401 && err.status !== 429) {     // the server will never accept it: drop it
        state.outbox = state.outbox.filter(function (x) { return x.key !== item.key; });
        toast('Не записано: ' + item.label + '. ' + err.message, true);
      } else {
        item.failed = err.message;
        toast('Пока не отправлено: ' + item.label + '. Повторю сам при следующем открытии, или нажми на запись.', true);
      }
      saveOutbox(); haptic('error');
    }).then(function () { state.flushing = false; if (state.view === 'day' && !state.sheet) render(); flushOutbox(); });
  }

  /* ---------- loading ---------- */
  function showDay(day) { state.day = day; state.stale = null; state.dayError = ''; cacheSet('day:' + day.date, day); }
  function loadDay(withCatalog) {
    var wanted = state.date, cached = cacheGet('day:' + wanted);
    state.day = cached ? cached.data : null; state.stale = cached ? { at: cached.at, error: '' } : null; state.dayError = ''; render();
    var request = withCatalog ? api('GET', 'bootstrap?date=' + encodeURIComponent(wanted)) : api('GET', 'day?date=' + encodeURIComponent(wanted)).then(function (day) { return { day: day }; });
    return request.then(function (res) {
      if (res.catalog) { state.catalog = res.catalog; state.catalogError = ''; cacheSet('catalog', res.catalog); }
      if (wanted !== state.date) return;
      showDay(res.day); render();
    }, function (err) {
      if (wanted !== state.date) return;
      if (state.stale) state.stale.error = err.message; else state.dayError = err.message;
      render();
    });
  }
  function loadCatalog(force) {
    if (!state.catalog) { var cached = cacheGet('catalog'); if (cached) state.catalog = cached.data; }
    if (state.catalog && !force) return Promise.resolve();
    state.catalogError = '';
    return api('GET', 'catalog').then(function (c) { state.catalog = c; cacheSet('catalog', c); if (state.view === 'add' && !state.sheet) render(); },
      function (err) { if (!state.catalog) { state.catalogError = err.message; if (state.view === 'add') render(); } });
  }
  function searchVv() {
    var q = state.query.trim();
    if (q.length < 2) { state.vv = { q: q, items: null, error: '', loading: false }; renderList(); return; }
    state.vv = { q: q, items: null, error: '', loading: true }; renderList();
    api('GET', 'vkusvill?q=' + encodeURIComponent(q)).then(function (res) {
      if (state.vv.q !== q) return;
      state.vv = { q: q, items: res.items, error: '', loading: false }; renderList();
    }, function (err) { if (state.vv.q === q) { state.vv = { q: q, items: null, error: err.message, loading: false }; renderList(); } });
  }

  /* ---------- day screen ---------- */
  function ringSvg(kcal, goal) {
    var r = 54, C = 2 * Math.PI * r, R = 62, CO = 2 * Math.PI * R;
    var max = goal ? goal[1] * 1.16 : Math.max(kcal, 1) * 1.2;
    var prog = Math.min(kcal / max, 1) * C, zone = '';
    if (goal) {
      var zoneLen = Math.max((goal[1] - goal[0]) / max * CO, 4), zoneOff = -(goal[0] / max) * CO;
      zone = '<circle cx="66" cy="66" r="' + R + '" fill="none" stroke="var(--accent)" stroke-width="3" stroke-linecap="round" stroke-dasharray="' +
        zoneLen.toFixed(1) + ' ' + (CO - zoneLen).toFixed(1) + '" stroke-dashoffset="' + zoneOff.toFixed(1) + '"/>';
    }
    return '<svg viewBox="0 0 132 132" aria-hidden="true"><circle cx="66" cy="66" r="' + r + '" fill="none" stroke="var(--track)" stroke-width="10"/>' + zone +
      '<circle cx="66" cy="66" r="' + r + '" fill="none" stroke="' + (goal && kcal > goal[1] ? 'var(--bad)' : 'var(--accent)') +
      '" stroke-width="10" stroke-linecap="round" stroke-dasharray="' + prog.toFixed(1) + ' ' + (C - prog).toFixed(1) + '"/></svg>';
  }
  function macroRow(label, value, range, color, minOnly) {
    var hint = 'без цели', cls = '', width = 0;
    if (range) {
      width = Math.min(value / range[1], 1) * 100;
      if (value < range[0]) { hint = 'не хватает ' + fmt(range[0] - value, 1) + ' г'; cls = ' warn'; }
      else if (value > range[1] && !minOnly) { hint = 'выше цели на ' + fmt(value - range[1], 1) + ' г'; cls = ' warn'; }
      else hint = 'в норме';
    }
    return '<div><div class="macro-head"><span>' + label + '</span><b class="num">' + fmt(value, 1) + ' г' +
      (range ? ' <i>/ ' + fmt(range[0]) + '–' + fmt(range[1]) + '</i>' : '') + '</b></div>' +
      (range ? '<div class="bar"><i style="width:' + width.toFixed(0) + '%;background:' + color + '"></i></div>' : '') +
      '<div class="hint' + cls + '">' + hint + '</div></div>';
  }
  function entryRow(e, child, last) {
    var pending = e.pending, act = pending ? 'pending' : 'edit';
    var tail = !pending ? bju(e) : e.failed ? '<b class="unsent">не отправлено · нажми, чтобы повторить</b>' : '<span class="sending"><span class="spin"></span>записываю…</span>';
    return '<button class="row' + (child ? ' child' : '') + (last ? ' last' : '') + (pending ? ' pending' : '') + '" data-act="' + act + '" data-id="' + esc(e.record_id) + '"><span class="name">' +
      esc(e.product) + (e.estimated ? '<span class="badge">оценка</span>' : '') + '</span><span class="kcal num">' + fmt(e.kcal) +
      ' <small>ккал</small></span><span class="sub num">' + esc(e.portion) + ' · ' + tail + '</span></button>';
  }
  function renderDay() {
    var isToday = state.date === todayMsk();
    $('#top').innerHTML = '<button class="icon-btn" data-act="prev" aria-label="Предыдущий день">‹</button><h2>' + dateTitle(state.date) +
      (isToday ? '<span class="today">сегодня</span>' : '<button class="today" data-act="today">к сегодня</button>') +
      '</h2><button class="icon-btn" data-act="next" aria-label="Следующий день"' + (isToday ? ' disabled' : '') + '>›</button>';
    if (state.dayError) { $('#screen').innerHTML = '<div class="state">' + esc(state.dayError) + '<button class="btn" data-act="reload">Повторить</button></div>'; return; }
    if (!state.day) { $('#screen').innerHTML = '<div class="state">Загружаю дневник…</div>'; return; }

    var banner = '';
    if (state.stale) banner = state.stale.error
      ? '<div class="stale bad">Не обновилось: ' + esc(state.stale.error) + ' Показаны ' + staleLabel(state.stale.at) + '. <button data-act="reload">Повторить</button></div>'
      : '<div class="stale"><span class="spin"></span>' + staleLabel(state.stale.at) + ', обновляю…</div>';
    var day = shownDay(), t = day.totals, g = day.targets.kcal, goalText = '', bad = false;
    if (g) {
      if (t.kcal < g[0]) goalText = 'До цели осталось <b class="num">' + fmt(g[0] - t.kcal) + '–' + fmt(g[1] - t.kcal) + ' ккал</b>';
      else if (t.kcal <= g[1]) goalText = '<b>В коридоре цели</b> ' + fmt(g[0]) + '–' + fmt(g[1]) + ' ккал';
      else { goalText = '<b class="num">Выше цели на ' + fmt(t.kcal - g[1]) + ' ккал</b>'; bad = true; }
    }
    var order = day.meals.slice();
    day.entries.forEach(function (e) { if (order.indexOf(e.meal) < 0) order.push(e.meal); });
    var meals = order.filter(function (m) { return day.entries.some(function (e) { return e.meal === m; }); }).map(function (m) {
      var list = day.entries.filter(function (e) { return e.meal === m; }), done = {}, rows = '';
      var sum = list.reduce(function (s, e) { return s + e.kcal; }, 0);
      var linked = list.filter(function (e) { return e.product_id && e.record_id && !e.pending; });
      list.forEach(function (e) {
        if (!e.group_id) { rows += entryRow(e); return; }
        if (done[e.group_id]) return;
        done[e.group_id] = true;
        var grp = list.filter(function (x) { return x.group_id === e.group_id; });
        var gsum = grp.reduce(function (s, x) { return s + x.kcal; }, 0);
        rows += '<div class="row group-head"><span class="name">' + esc(e.set_name || 'Набор') + '</span><span class="kcal num">' + fmt(gsum) +
          ' <small>ккал</small></span><span class="sub">набор · ' + grp.length + ' ' + plural(grp.length, ['продукт', 'продукта', 'продуктов']) + '</span></div>' +
          grp.map(function (x, i) { return entryRow(x, true, i === grp.length - 1); }).join('');
      });
      return '<section class="meal"><div class="meal-head"><h3>' + esc(m) + '</h3>' +
        (linked.length >= 2 ? '<button class="to-set" data-act="toset" data-meal="' + esc(m) + '">в набор</button>' : '') +
        '<span class="num">' + fmt(sum) + ' ккал</span><button class="plus" data-act="add" data-meal="' + esc(m) + '" aria-label="Добавить в ' + esc(m) + '">+</button></div>' +
        '<div class="rows">' + rows + '</div></section>';
    }).join('');

    $('#screen').innerHTML = banner + '<section class="summary"><div class="ring">' + ringSvg(t.kcal, g) +
      '<div class="ring-text"><strong class="num">' + fmt(t.kcal) + '</strong><span>ккал съедено</span></div></div><div class="macros">' +
      macroRow('Белки', t.protein_g, day.targets.protein_g, 'var(--protein)') + macroRow('Жиры', t.fat_g, day.targets.fat_g, 'var(--fat)', day.targets.fat_min_only) +
      macroRow('Углеводы', t.carbs_g, null) + '</div>' + (goalText ? '<div class="goal-line' + (bad ? ' bad' : '') + '">' + goalText + '</div>' : '') + '</section>' +
      (meals || '<div class="empty-day">За этот день записей нет. Нажми «+ Добавить».</div>');
  }

  /* ---------- statistics: how the days did against the targets ---------- */
  var MONTH_NAMES = ['Январь', 'Февраль', 'Март', 'Апрель', 'Май', 'Июнь', 'Июль', 'Август', 'Сентябрь', 'Октябрь', 'Ноябрь', 'Декабрь'];
  var MONTHS_SHORT = ['янв.', 'февр.', 'марта', 'апр.', 'мая', 'июня', 'июля', 'авг.', 'сент.', 'окт.', 'нояб.', 'дек.'];
  var STATE_WORDS = { below: 'ниже цели', 'in': 'в цели', above: 'выше цели' };
  function statsPeriod() {
    var anchor = parseDate(state.stats.anchor), from, to, title;
    if (state.stats.mode === 'week') {
      from = new Date(anchor); from.setDate(anchor.getDate() - (anchor.getDay() + 6) % 7);
      to = new Date(from); to.setDate(from.getDate() + 6);
      title = from.getMonth() === to.getMonth() ? from.getDate() + '–' + to.getDate() + ' ' + MONTHS[to.getMonth()]
        : from.getDate() + ' ' + MONTHS_SHORT[from.getMonth()] + ' – ' + to.getDate() + ' ' + MONTHS_SHORT[to.getMonth()];
    } else {
      from = new Date(anchor.getFullYear(), anchor.getMonth(), 1); to = new Date(anchor.getFullYear(), anchor.getMonth() + 1, 0);
      title = MONTH_NAMES[from.getMonth()] + ' ' + from.getFullYear();
    }
    return { from: dateStr(from), to: dateStr(to), title: title, isCurrent: dateKey(dateStr(to)) >= dateKey(todayMsk()) };
  }
  function shiftStats(direction) {
    var d = parseDate(state.stats.anchor);
    if (state.stats.mode === 'week') d.setDate(d.getDate() + 7 * direction); else d = new Date(d.getFullYear(), d.getMonth() + direction, 1);
    state.stats.anchor = dateStr(d); loadStats();
  }
  function loadStats() {
    var period = statsPeriod(), key = 'stats:' + period.from + ':' + period.to, cached = cacheGet(key), st = state.stats;
    st.data = cached ? cached.data : null; st.stale = cached ? { at: cached.at, error: '' } : null; st.error = ''; st.selected = -1; render();
    api('GET', 'stats?from=' + encodeURIComponent(period.from) + '&to=' + encodeURIComponent(period.to)).then(function (data) {
      if (statsPeriod().from !== data.from || statsPeriod().to !== data.to) return;
      st.data = data; st.stale = null; cacheSet(key, data); if (state.view === 'stats') render();
    }, function (err) {
      if (statsPeriod().from !== period.from) return;
      if (st.stale) st.stale.error = err.message; else st.error = err.message;
      if (state.view === 'stats') render();
    });
  }
  function dayWord(n) { return plural(n, ['день', 'дня', 'дней']); }
  function statsChart(days, goal, width) {
    var H = 190, padL = 34, padR = 6, padT = 18, padB = 34, plotW = width - padL - padR, plotH = H - padT - padB, n = days.length;
    var peak = Math.max.apply(null, days.map(function (d) { return d.kcal; }).concat(goal ? [goal[1]] : [0], [500])) * 1.12;
    var y = function (v) { return padT + plotH - v / peak * plotH; };
    var slot = plotW / n, barW = Math.max(4, Math.min(24, slot - 2)), svg = '';
    var step = peak > 2400 ? 1000 : 500;
    for (var tick = 0; tick <= peak; tick += step) {
      svg += '<line x1="' + padL + '" x2="' + (width - padR) + '" y1="' + y(tick).toFixed(1) + '" y2="' + y(tick).toFixed(1) + '" stroke="var(--line)" stroke-width="1"/>' +
        '<text x="' + (padL - 6) + '" y="' + (y(tick) + 4).toFixed(1) + '" text-anchor="end" class="axis">' + fmt(tick) + '</text>';
    }
    if (goal) svg += '<rect x="' + padL + '" width="' + plotW + '" y="' + y(goal[1]).toFixed(1) + '" height="' + Math.max(2, y(goal[0]) - y(goal[1])).toFixed(1) + '" fill="var(--st-in)" opacity=".16"/>' +
      '<text x="' + (width - padR) + '" y="' + (y(goal[1]) - 5).toFixed(1) + '" text-anchor="end" class="axis">цель ' + fmt(goal[0]) + '–' + fmt(goal[1]) + '</text>';
    days.forEach(function (d, i) {
      var cx = padL + slot * i + slot / 2, x = cx - barW / 2, date = parseDate(d.date), selected = i === state.stats.selected;
      if (d.logged) {
        var top = y(d.kcal), h = Math.max(2, y(0) - top), r = Math.min(4, barW / 2, h);
        var path = 'M' + x.toFixed(1) + ' ' + y(0).toFixed(1) + 'V' + (top + r).toFixed(1) + 'q0 -' + r + ' ' + r + ' -' + r + 'h' + (barW - 2 * r).toFixed(1) + 'q' + r + ' 0 ' + r + ' ' + r + 'V' + y(0).toFixed(1) + 'Z';
        var color = d.kcal_state ? 'var(--st-' + d.kcal_state + ')' : 'var(--muted)';
        svg += '<path d="' + path + '" fill="' + (d.today ? 'url(#unfinished)' : color) + '"' + (d.today ? ' stroke="' + color + '" stroke-width="1.5" stroke-dasharray="3 2" style="color:' + color + '"' : '') + '/>';
        if (selected) svg += '<text x="' + cx.toFixed(1) + '" y="' + (top - 5).toFixed(1) + '" text-anchor="middle" class="value">' + fmt(d.kcal) + '</text>';
      } else if (!d.future) svg += '<circle cx="' + cx.toFixed(1) + '" cy="' + (y(0) - 3).toFixed(1) + '" r="2" fill="var(--line)"/>';
      if (selected) svg += '<rect x="' + (cx - slot / 2).toFixed(1) + '" y="' + padT + '" width="' + slot.toFixed(1) + '" height="' + plotH + '" fill="var(--ink)" opacity=".06"/>';
      var showLabel = n <= 7 || date.getDate() === 1 || date.getDate() % 5 === 0;
      if (showLabel) svg += '<text x="' + cx.toFixed(1) + '" y="' + (H - 18) + '" text-anchor="middle" class="axis' + (d.today ? ' strong' : '') + '">' + (n <= 7 ? WEEKDAYS[date.getDay()] : date.getDate()) + '</text>' +
        (n <= 7 ? '<text x="' + cx.toFixed(1) + '" y="' + (H - 5) + '" text-anchor="middle" class="axis">' + date.getDate() + '</text>' : '');
      svg += '<rect data-act="sday" data-i="' + i + '" x="' + (cx - slot / 2).toFixed(1) + '" y="0" width="' + slot.toFixed(1) + '" height="' + H + '" fill="transparent" style="cursor:pointer"/>';
    });
    return '<svg class="chart" width="' + width + '" height="' + H + '" viewBox="0 0 ' + width + ' ' + H + '" role="img" aria-label="Калории по дням относительно цели">' +
      '<defs><pattern id="unfinished" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><rect width="6" height="6" fill="var(--card)"/><rect width="3" height="6" fill="currentColor" opacity=".55"/></pattern></defs>' + svg + '</svg>';
  }
  function stateMark(d) {
    if (!d.logged) return '<span class="dim">нет записей</span>';
    if (!d.kcal_state) return '';
    var delta = d.kcal_state === 'in' ? '' : ' на ' + fmt(Math.abs(d.kcal_delta));
    return '<span class="state-dot" style="background:var(--st-' + d.kcal_state + ')"></span>' + STATE_WORDS[d.kcal_state] + delta;
  }
  function okMark(label, ok) { return ok === null || ok === undefined ? '' : '<span class="ok' + (ok ? '' : ' no') + '">' + label + ' ' + (ok ? '✓' : '—') + '</span>'; }
  function renderStats() {
    var st = state.stats, period = statsPeriod();
    $('#top').innerHTML = '<button class="icon-btn" data-act="sprev" aria-label="Раньше">‹</button><h2>' + esc(period.title) +
      '<span class="today" style="color:var(--muted)">' + (st.mode === 'week' ? 'неделя' : 'месяц') + '</span></h2>' +
      '<button class="icon-btn" data-act="snext" aria-label="Позже"' + (period.isCurrent ? ' disabled' : '') + '>›</button>';
    var seg = '<div class="seg two-way"><button data-act="smode" data-mode="week" class="' + (st.mode === 'week' ? 'on' : '') + '">Неделя</button>' +
      '<button data-act="smode" data-mode="month" class="' + (st.mode === 'month' ? 'on' : '') + '">Месяц</button></div>';
    if (st.error) { $('#screen').innerHTML = seg + '<div class="state">' + esc(st.error) + '<button class="btn" data-act="sreload">Повторить</button></div>'; return; }
    if (!st.data) { $('#screen').innerHTML = seg + '<div class="state">Считаю статистику…</div>'; return; }
    var data = st.data, sum = data.summary, goal = data.targets.kcal, n = sum.days_counted, banner = '';
    if (st.stale) banner = st.stale.error
      ? '<div class="stale bad">Не обновилось: ' + esc(st.stale.error) + ' Показаны ' + staleLabel(st.stale.at) + '. <button data-act="sreload">Повторить</button></div>'
      : '<div class="stale"><span class="spin"></span>' + staleLabel(st.stale.at) + ', обновляю…</div>';
    var tiles = n ? '<section class="tiles">' +
      '<div class="tile wide"><span class="cap">В цели по калориям</span><strong class="num">' + sum.kcal_in + ' из ' + n + ' ' + dayWord(n) + '</strong>' +
      '<span class="cap">ниже цели: ' + sum.kcal_below + ' · выше цели: ' + sum.kcal_above + '</span></div>' +
      '<div class="tile"><span class="cap">В среднем за день</span><strong class="num">' + fmt(sum.avg.kcal) + ' <small>ккал</small></strong><span class="cap">' + (goal ? 'цель ' + fmt(goal[0]) + '–' + fmt(goal[1]) : 'без цели') + '</span></div>' +
      '<div class="tile"><span class="cap">Белок в норме</span><strong class="num">' + sum.protein_ok + ' из ' + n + '</strong><span class="cap">в среднем ' + fmt(sum.avg.protein_g) + ' г' +
      (data.targets.protein_g ? ' при цели от ' + fmt(data.targets.protein_g[0]) : '') + '</span></div>' +
      '<div class="tile"><span class="cap">Жиры в норме</span><strong class="num">' + sum.fat_ok + ' из ' + n + '</strong><span class="cap">в среднем ' + fmt(sum.avg.fat_g) + ' г</span></div>' +
      '<div class="tile"><span class="cap">Углеводы</span><strong class="num">' + fmt(sum.avg.carbs_g) + ' <small>г</small></strong><span class="cap">в среднем за день</span></div></section>'
      : '<div class="empty-day">За этот период нет законченных дней с записями.</div>';
    var hasToday = data.days.some(function (d) { return d.today && d.logged; });
    var width = Math.max(280, Math.min(488, $('#screen').clientWidth - 32) - 28);
    var picked = st.selected >= 0 ? data.days[st.selected] : null, pickedLine = '';
    if (picked) pickedLine = '<div class="picked"><b>' + dateTitle(picked.date) + (picked.today ? ', день не закончен' : '') + '</b><span class="num">' +
      (picked.logged ? fmt(picked.kcal) + ' ккал · ' + stateMark(picked) + ' · ' + bju(picked) : 'нет записей') + '</span>' +
      (picked.future ? '' : '<button class="btn ghost" data-act="openday" data-date="' + picked.date + '">Открыть день</button>') + '</div>';
    var chart = '<section class="card"><p class="label">Калории по дням</p><div class="chart-box">' + statsChart(data.days, goal, width) + '</div>' +
      '<div class="legend">' + ['below', 'in', 'above'].map(function (k) { return '<span><i style="background:var(--st-' + k + ')"></i>' + STATE_WORDS[k] + '</span>'; }).join('') +
      (hasToday ? '<span><i class="hatch"></i>сегодня, день не закончен</span>' : '') + '</div>' +
      (pickedLine || '<p class="note" style="padding:0">Нажми на столбик, чтобы увидеть день.</p>') + '</section>';
    var rows = data.days.filter(function (d) { return !d.future; }).map(function (d) {
      var date = parseDate(d.date);
      return '<button class="row stat-row" data-act="openday" data-date="' + d.date + '"><span class="name">' + WEEKDAYS[date.getDay()] + ', ' + date.getDate() + ' ' + MONTHS_SHORT[date.getMonth()] +
        (d.today ? '<span class="src">сегодня</span>' : '') + '</span><span class="kcal num">' + (d.logged ? fmt(d.kcal) + ' <small>ккал</small>' : '') + '</span>' +
        '<span class="sub">' + stateMark(d) + (d.logged ? ' ' + okMark('белок', d.protein_ok) + ' ' + okMark('жиры', d.fat_ok) : '') + '</span></button>';
    }).join('');
    var list = st.mode === 'week' ? '<section><p class="label" style="padding:0 4px">По дням</p><div class="rows">' + rows + '</div></section>'
      : '<details class="by-day"><summary>По дням</summary><div class="rows">' + rows + '</div></details>';
    $('#screen').innerHTML = seg + banner + tiles + chart + list +
      (hasToday || data.days.some(function (d) { return !d.logged && !d.future; }) ? '<p class="note">В подсчёт входят только законченные дни с записями. Сегодняшний день и дни без записей в средние не попадают.</p>' : '');
  }

  /* ---------- add screen ---------- */
  function visibleItems() {
    var c = state.catalog, q = norm(state.query.trim());
    if (!c) return [];
    var products = c.products, sets = c.sets;
    if (q) {
      var words = q.split(/\s+/);
      var hit = function (name) { var n = norm(name); return words.every(function (w) { return n.indexOf(w) >= 0 || (w.length >= 4 && n.indexOf(w.slice(0, -1)) >= 0); }); };
      return sets.filter(function (s) { return hit(s.set_name); }).map(function (s) { return ['s', s]; })
        .concat(products.filter(function (p) { return hit(p.name); }).map(function (p) { return ['p', p]; }));
    }
    if (state.tab === 'sets') return sets.map(function (s) { return ['s', s]; });
    if (state.tab === 'recent') return products.filter(function (p) { return p.last_date; })
      .sort(function (a, b) { return dateKey(b.last_date).localeCompare(dateKey(a.last_date)) || b.times_logged - a.times_logged; }).slice(0, 30).map(function (p) { return ['p', p]; });
    return products.filter(function (p) { return p.times_logged > 0; }).slice(0, 30).map(function (p) { return ['p', p]; });
  }
  function productRow(p, key, searching) {
    var lastUnit = p.last_unit ? p.unit_list[unitByName(p, p.last_unit)] : null;
    var meta = p.vv ? 'на ' + p.base + ': ' + bju(p)
      : lastUnit ? (state.tab === 'freq' && !searching ? 'записано ' + p.times_logged + ' ' + plural(p.times_logged, ['раз', 'раза', 'раз']) + ' · обычно ' : 'в прошлый раз: ') +
        portionText(p, lastUnit, p.last_quantity) + (p.last_date && (state.tab !== 'freq' || searching) ? ' · ' + p.last_date.slice(0, 5) : '') : 'ещё не записывал';
    var first = p.unit_list[0], price = p.vv ? [p.kcal, p.base] : [p.unit_kcal, p.unit_label];
    return '<button class="row" data-act="pick" data-key="' + esc(key) + '"><span class="name">' + esc(p.name) +
      (p.precision === 'оценка' ? '<span class="badge">оценка</span>' : '') + (searching && !p.vv ? '<span class="src">мой продукт</span>' : '') +
      '</span><span class="kcal num">' + fmt(price[0]) + ' <small>ккал / ' + esc(price[1]) + '</small></span><span class="sub num">' + esc(meta) + '</span></button>';
  }
  function renderList() {
    var list = $('#list'), note = $('#listNote'); if (!list) return;
    var searching = state.query.trim() !== '';
    if (state.tab === 'vv') {
      var vv = state.vv;
      if (vv.loading) list.innerHTML = '<div class="row"><span class="sub">Ищу в каталоге ВкусВилла…</span></div>';
      else if (vv.error) list.innerHTML = '<div class="row"><span class="sub">' + esc(vv.error) + '</span></div>';
      else if (!vv.items) list.innerHTML = '<div class="row"><span class="sub">Набери название товара, от двух букв.</span></div>';
      else if (!vv.items.length) list.innerHTML = '<div class="row"><span class="sub">В каталоге не нашлось товаров с указанной пищевой ценностью.</span></div>';
      else list.innerHTML = vv.items.map(function (item, i) { return productRow(vvProduct(item), 'vv:' + i, false); }).join('');
      note.textContent = 'Живой каталог ВкусВилла. Выбранный товар сохранится в твоих продуктах.';
      return;
    }
    if (state.catalogError) { list.innerHTML = '<div class="row"><span class="sub">' + esc(state.catalogError) + '</span></div>'; note.textContent = ''; return; }
    if (!state.catalog) { list.innerHTML = '<div class="row"><span class="sub">Загружаю продукты…</span></div>'; note.textContent = ''; return; }
    var items = visibleItems();
    list.innerHTML = items.map(function (it) {
      if (it[0] === 's') {
        var s = it[1];
        return '<button class="row" data-act="pickset" data-key="' + esc(s.set_name) + '"><span class="name">' + esc(s.set_name) + (searching ? '<span class="src">набор</span>' : '') +
          '</span><span class="kcal num">' + fmt(s.kcal) + ' <small>ккал</small></span><span class="sub">' +
          esc(s.items.map(function (i) { return i.product + ' ' + fmt(i.quantity, 2) + ' ' + i.unit; }).join(' + ')) + '</span></button>';
      }
      return productRow(it[1], 'p:' + it[1].product_id, searching);
    }).join('') || '<div class="row"><span class="sub">' + (searching ? 'В твоих продуктах не нашлось. Загляни во вкладку «ВкусВилл» или опиши еду боту.' :
      state.tab === 'sets' ? 'Наборов пока нет. На экране дня у приёма пищи есть кнопка «в набор».' : 'Пока пусто.') + '</div>';
    note.textContent = searching ? 'Поиск по твоим продуктам и наборам.' : state.tab === 'sets' ? 'Набор можно менять при каждом добавлении.' :
      'Справа цена одной мерки. Количество выбираешь на следующем шаге.';
  }
  function renderAdd() {
    var meals = (state.catalog && state.catalog.meals) || (state.day && state.day.meals) || [];
    if (!state.addMeal) state.addMeal = guessMeal(meals);
    $('#top').innerHTML = '<button class="back" data-act="day">‹ День</button><h2>Добавить<span class="today" style="color:var(--muted)">' + dateTitle(state.date) + '</span></h2><span style="width:58px"></span>';
    $('#screen').innerHTML = '<div><p class="label">Куда</p><div class="chips scroll">' + meals.map(function (m) {
      return '<button class="chip' + (m === state.addMeal ? ' on' : '') + '" data-act="meal" data-meal="' + esc(m) + '">' + esc(m) + '</button>'; }).join('') + '</div></div>' +
      '<input class="search" id="q" type="search" placeholder="' + (state.tab === 'vv' ? 'Найти в каталоге ВкусВилла' : 'Найти продукт или набор') + '" value="' + esc(state.query) + '" autocomplete="off">' +
      '<div class="seg">' + TABS.map(function (t) { return '<button data-act="tab" data-tab="' + t[0] + '" class="' + (t[0] === state.tab ? 'on' : '') + '">' + t[1] + '</button>'; }).join('') + '</div>' +
      '<div><div class="rows" id="list"></div></div><p class="note" id="listNote"></p>' +
      '<div class="ask"><p>Новая еда, фото этикетки или блюдо из ресторана. Бот посчитает, запишет и заведёт продукт с мерками.</p>' +
      '<button class="btn ghost" data-act="bot">Описать боту в чате</button></div>';
    renderList();
  }
  function guessMeal(meals) {
    var h = mskNow().getHours(), name = h < 8 ? 'До завтрака' : h < 11 ? 'Завтрак' : h < 13 ? 'Перекус после завтрака' : h < 16 ? 'Обед' : h < 19 ? 'Перекус после обеда' : 'Ужин';
    return meals.indexOf(name) >= 0 ? name : (meals[0] || name);
  }
  function renderTabbar() {
    $('#tabbar').innerHTML = '<button data-act="day" class="' + (state.view === 'day' ? 'on' : '') + '">День</button>' +
      '<button data-act="stats" class="' + (state.view === 'stats' ? 'on' : '') + '">Статистика</button>' +
      '<button class="fab" data-act="add">+ Добавить</button><button data-act="bot">Чат</button>';
  }
  function render() { if (state.view === 'day') renderDay(); else if (state.view === 'stats') renderStats(); else renderAdd(); renderTabbar(); }

  /* ---------- sheets ---------- */
  function mealChips(sel) {
    var meals = (state.day && state.day.meals) || (state.catalog && state.catalog.meals) || [];
    if (sel && meals.indexOf(sel) < 0) meals = meals.concat([sel]);
    return '<div><p class="label">Приём пищи</p><div class="chips">' + meals.map(function (m) {
      return '<button class="chip' + (m === sel ? ' on' : '') + '" data-act="smeal" data-meal="' + esc(m) + '">' + esc(m) + '</button>'; }).join('') + '</div></div>';
  }
  function stepper(id, qty, i, sm) {
    var attr = i != null ? ' data-i="' + i + '"' : '';
    return '<div class="stepper' + (sm ? ' sm' : '') + '"><button data-act="dec"' + attr + ' aria-label="Меньше">−</button><input id="' + id + '"' + attr +
      ' type="text" inputmode="decimal" value="' + fmt(qty, 2) + '"><button data-act="inc"' + attr + ' aria-label="Больше">+</button></div>';
  }
  function delBlock(s) {
    return s.confirm ? '<div class="two"><button class="btn danger" data-act="del2">Да, удалить</button><button class="btn ghost wide" data-act="delno">Оставить</button></div>'
      : '<button class="btn danger" data-act="del">Удалить запись</button>';
  }
  function paintCalc() {
    var s = state.sheet, v = calc(s.p, s.p.unit_list[s.ui], s.qty);
    $('#calc').innerHTML = '<strong class="num">' + fmt(v.kcal) + ' ккал</strong><span class="num">' + bju(v) + '</span>';
    var word = $('#unitWord'); if (word) word.textContent = isBaseUnit(s.p.unit_list[s.ui]) ? s.p.unit_list[s.ui].name : unitWord(s.p.unit_list[s.ui].name, s.qty);
  }
  function paintSet() {
    var s = state.sheet, total = { kcal: 0, protein_g: 0, fat_g: 0, carbs_g: 0 };
    s.rows.forEach(function (r, i) {
      var unit = r.p.unit_list[r.ui], v = calc(r.p, unit, r.qty);
      $('#sk_' + i).textContent = r.on ? portionText(r.p, unit, r.qty) + ' · ' + fmt(v.kcal) + ' ккал' : 'в этот раз без него';
      if (r.on) Object.keys(total).forEach(function (k) { total[k] += v[k]; });
    });
    $('#calc').innerHTML = '<strong class="num">' + fmt(total.kcal) + ' ккал</strong><span class="num">' + bju(total) + '</span>';
  }
  function renderSheet() {
    var s = state.sheet, html = '<div class="grab"></div>', busy = state.busy ? ' disabled' : '';
    if (s.kind === 'add' || s.kind === 'editp') {
      var p = s.p, unit = p.unit_list[s.ui];
      html += '<div><h3>' + esc(p.name) + '</h3><div class="from">' + (s.kind === 'editp' ? 'Запись за ' + esc(s.entry.date) + ' · ' : p.vv ? 'Каталог ВкусВилла · ' : 'Твой продукт · ') +
        fmt(p.kcal, 1) + ' ккал на ' + esc(p.base) + (p.precision === 'оценка' ? ' · оценка' : '') + '</div></div>' +
        '<div><p class="label">Сколько</p><div class="qty">' + stepper('qty', s.qty) + '<span class="unit-word" id="unitWord"></span></div></div>' +
        (p.unit_list.length > 1 ? '<div><p class="label">Мерка</p><div class="chips">' + p.unit_list.map(function (u, i) {
          return '<button class="chip' + (i === s.ui ? ' on' : '') + '" data-act="unit" data-u="' + i + '">' + esc(u.label) + '</button>'; }).join('') + '</div></div>' : '') +
        '<div class="calc" id="calc"></div>' + mealChips(s.meal) +
        (s.kind === 'add' ? '<button class="btn" data-act="save"' + busy + '>' + (state.busy ? 'Записываю…' : 'Записать в дневник') + '</button>'
          : '<button class="btn" data-act="updatep"' + busy + '>' + (state.busy ? 'Сохраняю…' : 'Сохранить') + '</button>' + delBlock(s));
    } else if (s.kind === 'set') {
      html += '<div><h3>' + esc(s.set.set_name) + '</h3><div class="from">Набор. Количество меняется только для этой записи.</div></div><div class="rows">' +
        s.rows.map(function (r, i) {
          return '<div class="set-row' + (r.on ? '' : ' off') + '"><button class="check' + (r.on ? ' on' : '') + '" data-act="toggle" data-i="' + i + '" aria-label="Учитывать ' + esc(r.p.name) +
            '">✓</button><span class="name">' + esc(r.p.name) + (r.extra ? '<span class="src">на этот раз</span>' : '') + '</span>' + stepper('sq_' + i, r.qty, i, true) +
            '<span class="sub num" id="sk_' + i + '"></span></div>'; }).join('') + '</div>';
      if (s.extraOpen) {
        var q = norm(s.extraQuery), found = state.catalog.products.filter(function (p) {
          return (!q || norm(p.name).indexOf(q) >= 0) && !s.rows.some(function (r) { return r.p.product_id === p.product_id; }); }).slice(0, 8);
        html += '<div><p class="label">Добавить на этот раз</p><input class="search" id="extraQ" type="search" placeholder="Название продукта" value="' + esc(s.extraQuery) +
          '" autocomplete="off"><div class="chips" id="extraChips" style="margin-top:8px">' + found.map(function (p) {
            return '<button class="chip" data-act="extra" data-key="' + esc(p.product_id) + '">' + esc(p.name) + '</button>'; }).join('') + '</div></div>';
      } else html += '<button class="btn ghost" data-act="extraopen">+ Добавить продукт на этот раз</button>';
      html += '<div class="calc" id="calc"></div><button class="keep" data-act="keep"><span class="check' + (s.keep ? ' on' : '') + '">✓</span><span>Запомнить этот состав как новый стандарт</span></button>' +
        mealChips(s.meal) + '<button class="btn" data-act="saveset"' + busy + '>' + (state.busy ? 'Записываю…' : 'Записать в дневник') + '</button>';
    } else if (s.kind === 'toset') {
      html += '<div><h3>Сохранить как набор</h3><div class="from">' + esc(s.meal) + ': ' + esc(s.entries.map(function (e) { return e.product; }).join(' + ')) + '</div></div>' +
        '<div class="field full"><label for="setName">Название набора</label><input id="setName" type="text" value="' + esc(s.name) + '" placeholder="Например, Творожный завтрак"></div>' +
        '<button class="btn" data-act="savetoset"' + busy + '>' + (state.busy ? 'Сохраняю…' : 'Сохранить набор') + '</button>';
    } else {
      var e = s.vals;
      html += '<div><h3>' + esc(s.entry.product) + '</h3><div class="from">Запись за ' + esc(s.entry.date) + ' · свободная запись, правится числами</div></div>' +
        '<div><p class="label">Быстро изменить порцию</p><div class="chips">' + MULTS.map(function (m) {
          return '<button class="chip' + (m[0] === s.mult ? ' on' : '') + '" data-act="mult" data-m="' + m[0] + '">' + m[1] + '</button>'; }).join('') + '</div></div>' +
        '<div class="fields"><div class="field full"><label for="ePortion">Порция</label><input id="ePortion" type="text" value="' + esc(e.portion) + '"></div>' +
        [['kcal', 'ккал'], ['protein_g', 'Белки'], ['fat_g', 'Жиры'], ['carbs_g', 'Углеводы']].map(function (x) {
          return '<div class="field"><label for="e_' + x[0] + '">' + x[1] + '</label><input id="e_' + x[0] + '" data-field="' + x[0] + '" type="text" inputmode="decimal" value="' + fmt(e[x[0]], 1) + '"></div>'; }).join('') +
        '</div>' + mealChips(s.meal) + '<button class="btn" data-act="update"' + busy + '>' + (state.busy ? 'Сохраняю…' : 'Сохранить') + '</button>' + delBlock(s);
    }
    $('#sheet').innerHTML = html; $('#sheetWrap').hidden = false;
    if (s.kind === 'set') paintSet(); else if (s.kind === 'add' || s.kind === 'editp') paintCalc();
  }
  function closeSheet() { state.sheet = null; state.busy = false; $('#sheetWrap').hidden = true; }
  function syncEditInputs() {
    var s = state.sheet; if (!s || s.kind !== 'edit') return;
    var p = $('#ePortion'); if (p) s.vals.portion = p.value;
    Array.prototype.forEach.call(document.querySelectorAll('#sheet [data-field]'), function (inp) { s.vals[inp.dataset.field] = num(inp.value); });
  }

  /* ---------- writes ---------- */
  function run(promise, okText) {
    state.busy = true; renderSheet();
    return promise.then(function (res) {
      if (res.day) { cacheSet('day:' + res.day.date, res.day); if (res.day.date === state.date) showDay(res.day); }
      closeSheet(); state.view = 'day'; state.query = ''; render();
      toast(okText(res)); haptic('success'); loadCatalog(true);
    }, function (err) {
      state.busy = false; if (state.sheet) renderSheet();
      toast(err.message, true); haptic('error');
      if (err.status === 404 || err.status === 409) { closeSheet(); loadDay(); }
    });
  }
  function dayLine(res) { return 'День: ' + fmt(res.totals.kcal) + ' ккал'; }

  /* ---------- events ---------- */
  document.addEventListener('click', function (ev) {
    var b = ev.target.closest('[data-act]'); if (!b || b.disabled) return;
    var act = b.dataset.act, s = state.sheet;
    if (act === 'prev') { state.date = shiftDate(state.date, -1); loadDay(); }
    else if (act === 'next') { state.date = shiftDate(state.date, 1); loadDay(); }
    else if (act === 'today') { state.date = todayMsk(); loadDay(); }
    else if (act === 'reload') loadDay();
    else if (act === 'bot') { var tgc = TG(); if (tgc && tgc.close) tgc.close(); else toast('Эта кнопка закрывает приложение и возвращает в чат с ботом.'); }
    else if (act === 'day') { state.view = 'day'; state.query = ''; render(); }
    else if (act === 'stats') { state.view = 'stats'; loadStats(); $('#screen').scrollTop = 0; }
    else if (act === 'smode') { state.stats.mode = b.dataset.mode; if (dateKey(state.stats.anchor) > dateKey(todayMsk())) state.stats.anchor = todayMsk(); loadStats(); }
    else if (act === 'sprev') shiftStats(-1);
    else if (act === 'snext') shiftStats(1);
    else if (act === 'sreload') loadStats();
    else if (act === 'sday') { state.stats.selected = state.stats.selected === Number(b.dataset.i) ? -1 : Number(b.dataset.i); renderStats(); }
    else if (act === 'openday') { state.date = b.dataset.date; state.view = 'day'; loadDay(); $('#screen').scrollTop = 0; }
    else if (act === 'add') { state.view = 'add'; state.addMeal = b.dataset.meal || ''; state.query = ''; if (state.tab === 'vv') state.tab = 'recent'; render(); loadCatalog(); $('#screen').scrollTop = 0; }
    else if (act === 'meal') { state.addMeal = b.dataset.meal; renderAdd(); }
    else if (act === 'tab') { state.tab = b.dataset.tab; state.query = ''; state.vv = { q: '', items: null, error: '', loading: false }; renderAdd(); }
    else if (act === 'pick') {
      var key = b.dataset.key, p = key.indexOf('vv:') === 0 ? vvProduct(state.vv.items[Number(key.slice(3))]) : productById(key.slice(2));
      var choice = p.vv ? { ui: p.unit_list.length - 1, qty: 100 } : defaultChoice(p);
      state.sheet = { kind: 'add', p: p, qty: choice.qty, ui: choice.ui, meal: state.addMeal, recordId: newRecordId() }; renderSheet();
    }
    else if (act === 'pickset') {
      var set = state.catalog.sets.filter(function (x) { return x.set_name === b.dataset.key; })[0];
      var rows = set.items.filter(function (i) { return !i.missing && productById(i.product_id); }).map(function (i) {
        var pr = productById(i.product_id); return { p: pr, qty: i.quantity, ui: unitByName(pr, i.unit), on: true, recordId: newRecordId() }; });
      state.sheet = { kind: 'set', set: set, rows: rows, meal: state.addMeal, extraOpen: false, extraQuery: '', keep: false }; renderSheet();
    }
    else if (act === 'edit') {
      var entry = state.day.entries.filter(function (e) { return e.record_id === b.dataset.id; })[0];
      if (!entry || !entry.record_id) { toast('У этой старой записи нет кода, её можно поправить через бота.'); return; }
      if (entry.product_id && entry.unit_list.length) {
        var ep = { name: entry.product, unit_list: entry.unit_list, precision: entry.estimated ? 'оценка' : 'точно' }, cat = productById(entry.product_id);
        if (cat) ep = cat;
        else { var u0 = entry.unit_list[unitByName(ep, entry.unit)], k = factor('100 г', u0, entry.quantity) || 1; ep.base = '100 г'; ['kcal', 'protein_g', 'fat_g', 'carbs_g'].forEach(function (f) { ep[f] = entry[f] / k; }); }
        state.sheet = { kind: 'editp', entry: entry, p: ep, qty: entry.quantity, ui: unitByName(ep, entry.unit), meal: entry.meal, confirm: false };
      } else state.sheet = { kind: 'edit', entry: entry, base: JSON.parse(JSON.stringify(entry)), vals: JSON.parse(JSON.stringify(entry)), mult: 1, meal: entry.meal, confirm: false };
      renderSheet(); loadCatalog();
    }
    else if (act === 'toset') {
      var list = state.day.entries.filter(function (e) { return e.meal === b.dataset.meal && e.product_id && e.record_id; });
      state.sheet = { kind: 'toset', meal: b.dataset.meal, entries: list, name: '' }; renderSheet();
    }
    else if (act === 'close') { if (!state.busy) closeSheet(); }
    else if (act === 'smeal') { syncEditInputs(); s.meal = b.dataset.meal; renderSheet(); }
    else if (act === 'inc' || act === 'dec') {
      var t = b.dataset.i != null ? s.rows[Number(b.dataset.i)] : s;
      t.qty = nextQty(t.p.unit_list[t.ui], t.qty, act === 'inc'); renderSheet();
    }
    else if (act === 'unit') {
      var nu = s.p.unit_list[Number(b.dataset.u)], st = stepOf(nu) || 0.5;
      s.qty = Number(Math.max(st, Math.round(s.qty * s.p.unit_list[s.ui].amount / nu.amount / st) * st).toFixed(2)); s.ui = Number(b.dataset.u); renderSheet();
    }
    else if (act === 'toggle') { s.rows[Number(b.dataset.i)].on = !s.rows[Number(b.dataset.i)].on; renderSheet(); }
    else if (act === 'extraopen') { s.extraOpen = true; renderSheet(); var inp = $('#extraQ'); if (inp) inp.focus(); }
    else if (act === 'extra') { var xp = productById(b.dataset.key), c = defaultChoice(xp); s.rows.push({ p: xp, qty: c.qty, ui: c.ui, on: true, extra: true, recordId: newRecordId() }); s.extraOpen = false; s.extraQuery = ''; renderSheet(); }
    else if (act === 'keep') { s.keep = !s.keep; renderSheet(); }
    else if (act === 'mult') {
      var m = Number(b.dataset.m), o = s.base; syncEditInputs(); s.mult = m;
      ['kcal', 'protein_g', 'fat_g', 'carbs_g'].forEach(function (f) { s.vals[f] = o[f] * m; });
      s.vals.portion = m === 1 ? o.portion : o.portion + ' (' + MULTS.filter(function (x) { return x[0] === m; })[0][1] + ')'; renderSheet();
    }
    else if (act === 'pending') {
      var waiting = state.outbox.filter(function (x) { return x.failed; });
      if (waiting.length) { toast('Отправляю ещё раз…'); flushOutbox(true); } else toast('Эта запись ещё отправляется в таблицу.');
    }
    else if (act === 'save') {
      if (!s.qty) { toast('Укажи количество больше нуля.', true); return; }
      var unit = s.p.unit_list[s.ui], item = { record_id: s.recordId, quantity: s.qty, unit: unit.name };
      if (s.p.vv) item.new_product = s.p.new_product; else item.product_id = s.p.product_id;
      enqueue({ date: state.date, meal: s.meal, items: [item] }, [provisional(s.p, unit, s.qty, s.meal, s.recordId)], s.p.name);
    }
    else if (act === 'saveset') {
      var on = s.rows.filter(function (r) { return r.on && r.qty; });
      if (!on.length) { toast('Отметь хотя бы один продукт.', true); return; }
      var groupId = 'sending-' + on[0].recordId, setName = s.set.set_name, mealName = s.meal;
      enqueue({ date: state.date, meal: mealName, set_name: setName, remember_set: s.keep,
                items: on.map(function (r) { return { record_id: r.recordId, product_id: r.p.product_id, quantity: r.qty, unit: r.p.unit_list[r.ui].name }; }) },
              on.map(function (r) { return provisional(r.p, r.p.unit_list[r.ui], r.qty, mealName, r.recordId, setName, groupId); }), setName);
    }
    else if (act === 'updatep') {
      if (!s.qty) { toast('Укажи количество больше нуля.', true); return; }
      run(api('PATCH', 'entries/' + s.entry.record_id, { quantity: s.qty, unit: s.p.unit_list[s.ui].name, meal: s.meal }), function (res) { return 'Запись исправлена. ' + dayLine(res); });
    }
    else if (act === 'update') {
      syncEditInputs();
      run(api('PATCH', 'entries/' + s.entry.record_id, { meal: s.meal, portion: s.vals.portion, kcal: s.vals.kcal, protein_g: s.vals.protein_g, fat_g: s.vals.fat_g, carbs_g: s.vals.carbs_g }),
        function (res) { return 'Запись исправлена. ' + dayLine(res); });
    }
    else if (act === 'del') { syncEditInputs(); s.confirm = true; renderSheet(); }
    else if (act === 'delno') { s.confirm = false; renderSheet(); }
    else if (act === 'del2') run(api('DELETE', 'entries/' + s.entry.record_id), function (res) { return 'Запись удалена. ' + dayLine(res); });
    else if (act === 'savetoset') {
      var name = ($('#setName').value || '').trim();
      if (!name) { toast('Дай набору название.', true); return; }
      s.name = name;
      run(api('POST', 'sets', { set_name: name, record_ids: s.entries.map(function (e) { return e.record_id; }) }).then(function (res) { res.totals = state.day.totals; return res; }),
        function () { return 'Набор «' + name + '» сохранён. Он появится во вкладке «Наборы».'; });
    }
  });

  document.addEventListener('input', function (ev) {
    var t = ev.target, s = state.sheet;
    if (t.id === 'q') { state.query = t.value; if (state.tab === 'vv') { clearTimeout(vvTimer); vvTimer = setTimeout(searchVv, 450); } else renderList(); }
    else if (t.id === 'qty' && s) { s.qty = num(t.value); paintCalc(); }
    else if (t.id.indexOf('sq_') === 0 && s) { s.rows[Number(t.dataset.i)].qty = num(t.value); paintSet(); }
    else if (t.id === 'setName' && s) s.name = t.value;
    else if (t.id === 'extraQ' && s) {
      s.extraQuery = t.value; var q = norm(t.value);
      $('#extraChips').innerHTML = state.catalog.products.filter(function (p) {
        return (!q || norm(p.name).indexOf(q) >= 0) && !s.rows.some(function (r) { return r.p.product_id === p.product_id; }); }).slice(0, 8)
        .map(function (p) { return '<button class="chip" data-act="extra" data-key="' + esc(p.product_id) + '">' + esc(p.name) + '</button>'; }).join('');
    }
  });

  /* ---------- start ---------- */
  function isDark(hex) { var m = /^#?([0-9a-f]{6})$/i.exec(hex || ''); if (!m) return null; var n = parseInt(m[1], 16); return ((n >> 16) * 299 + ((n >> 8) & 255) * 587 + (n & 255) * 114) / 1000 < 128; }
  function applyTheme() {
    var tg = TG(), scheme = tg && tg.colorScheme;
    if (!scheme) { try { var dark = isDark(JSON.parse(launch.get('tgWebAppThemeParams') || '{}').bg_color); if (dark !== null) scheme = dark ? 'dark' : 'light'; } catch (e) { /* no theme in the address */ } }
    if (!scheme) scheme = window.matchMedia && window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
    document.documentElement.setAttribute('data-theme', scheme);
    try { if (tg && tg.setHeaderColor) { var bg = getComputedStyle(document.documentElement).getPropertyValue('--ground').trim(); tg.setHeaderColor(bg); tg.setBackgroundColor(bg); } } catch (e) { /* older clients */ }
  }
  window.__nutriTelegramReady = function () {
    var tg = TG(); if (!tg) return;
    try { tg.ready(); tg.expand(); tg.onEvent && tg.onEvent('themeChanged', applyTheme); } catch (e) { /* ignore */ }
    applyTheme();
  };
  window.__nutriTelegramReady();
  applyTheme();
  var params = new URLSearchParams(location.search);                      // deep links used for previews
  if (/^\d{2}\.\d{2}\.\d{4}$/.test(params.get('date') || '')) state.date = params.get('date');
  if (params.get('screen') === 'add') { state.view = 'add'; if (params.get('tab')) state.tab = params.get('tab'); }
  if (params.get('screen') === 'stats') { state.view = 'stats'; if (params.get('mode')) state.stats.mode = params.get('mode'); state.stats.anchor = state.date; }
  state.outbox = loadOutbox();
  render();
  loadCatalog();                                                        // instant, from the phone-side cache
  window.addEventListener('online', function () { flushOutbox(true); });
  if (state.view === 'stats') loadStats();
  loadDay(true).then(function () {
    flushOutbox(true);
    if (!state.catalog) return;
    var open = params.get('open'), openSet = params.get('openset');
    if (open) {
      var found = state.catalog.products.filter(function (p) { return norm(p.name).indexOf(norm(open)) === 0; })[0];
      if (found) { var ch = defaultChoice(found); state.sheet = { kind: 'add', p: found, qty: ch.qty, ui: ch.ui, meal: state.addMeal || guessMeal(state.catalog.meals), recordId: newRecordId() }; renderSheet();
        if (params.get('autosave') && /^(127\.0\.0\.1|localhost)$/.test(location.hostname)) document.querySelector('[data-act="save"]').click(); }   // local previews only
    } else if (openSet) {
      var btn = document.querySelector('[data-act="pickset"]'); if (btn) btn.click();
    }
  });
})();
