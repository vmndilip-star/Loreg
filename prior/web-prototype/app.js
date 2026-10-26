(function () {
  'use strict';

  // Every number on screen comes from snapshot.json (python snapshot.py, computed from the product database).
  var VIEWS = ['home', 'stock', 'watch', 'ask', 'upload'];
  var TITLES = { home: 'Home', stock: 'Stock', watch: 'Loreg Watch', ask: 'Ask Loreg', upload: 'Upload' };
  var S = null;           // the snapshot
  var PLANS = [];         // option cards for the launch-exposed items
  var BASE = 0;           // money at risk with no plan, before any accepted plan
  var state = { help: false, decisions: {} };

  function money(n) { return '$' + Math.round(n).toLocaleString('en-US'); }
  function money2(n) { return '$' + n.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 }); }
  function num(n) { return Number(n).toLocaleString('en-US'); }
  function esc(s) { return String(s).replace(/[&<>"']/g, function (c) { return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]; }); }
  function $(sel, root) { return (root || document).querySelector(sel); }
  function $all(sel, root) { return Array.prototype.slice.call((root || document).querySelectorAll(sel)); }

  function atRisk() {
    var total = BASE;
    PLANS.forEach(function (p) { if (state.decisions[p.id] === 'accepted') { total -= p.tied; } });
    return total;
  }

  function longDate(iso) {
    var d = new Date(iso + 'T12:00:00Z');
    return d.toLocaleDateString('en-US', { weekday: 'long', month: 'long', day: 'numeric', timeZone: 'UTC' });
  }
  function shortDate(iso) {
    var d = new Date(iso + 'T12:00:00Z');
    return d.toLocaleDateString('en-US', { month: 'short', day: 'numeric', year: 'numeric', timeZone: 'UTC' });
  }

  function binds() {
    var t = S.totals, L = S.launch, u = S.upload, b = S.bill;
    var m = {
      asOfLong: longDate(S.as_of), asOfShort: shortDate(S.as_of), shop: S.shop,
      skus: num(t.skus), stockCap: money(t.stock_at_cost),
      deadN: num(t.dead.n), deadCap: money(t.dead.cap), slowN: num(t.slow.n), slowCap: money(t.slow.cap),
      atRiskN: num(t.at_risk.n), atRiskCap: money(t.at_risk.cap),
      upFile: u.file, upRows: num(u.rows), upOk: num(u.ok), upLook: num(u.look), upBlocked: num(u.blocked),
      upContinue: num(u.rows - u.blocked), billN: String(b.lines.length),
      billOkN: String(b.lines.filter(function (l) { return !l.unclear; }).length), billSum: money2(b.sum)
    };
    if (L) {
      m.launchLabel = L.label; m.launchBrand = L.brand; m.launchCat = L.cat; m.launchN = num(L.n);
      m.launchCap = money(L.cap); m.top3Cap = money(L.top3_cap);
    }
    return m;
  }

  function renderStatic() {
    var m = binds();
    $all('[data-bind]').forEach(function (el) {
      var k = el.getAttribute('data-bind');
      if (k in m) { el.textContent = m[k]; }
    });
    $all('[data-needs="launch"]').forEach(function (el) { el.hidden = !S.launch; });

    // pill bars: the five biggest categories, the rest merged into "Other"
    var cats = S.by_category.slice(0, 5);
    var rest = S.by_category.slice(5);
    if (rest.length) {
      var cap = rest.reduce(function (a, c) { return a + c.cap; }, 0);
      cats.push({ label: 'Other', cap: cap, pct: Math.round(100 * cap / S.by_category[0].cap) });
    }
    $('#pills').innerHTML = cats.map(function (c) {
      return '<div class="col"><div class="pillbar glass" style="--fill:' + Math.max(c.pct, 2) + '%" role="img" aria-label="' + esc(c.label) + ': ' + money(c.cap) + ' at risk"><div class="fill"></div><div class="cap"><div class="fw-bolder">' + esc(c.label) + '</div><div class="fw-semibold">' + money(c.cap) + '</div></div></div></div>';
    }).join('');

    // biggest amounts table
    $('#topRows').innerHTML = S.top_items.map(function (i) {
      var chip = i.status === 'dead' ? '<span class="chip chip-red">Dead</span>' : '<span class="chip chip-amber">Slow</span>';
      return '<tr><td class="fw-bold">' + esc(i.name) + '</td><td class="text-end">' + num(i.qty) + '</td><td class="text-end d-none d-md-table-cell">' + (i.dsls === null ? 'never' : num(i.dsls)) + '</td><td>' + chip + '</td><td class="d-none d-lg-table-cell soft">' + esc(i.why) + '</td><td class="text-end fw-bold">' + money(i.cap) + '</td></tr>';
    }).join('');

    // affected items on the Watch alert
    if (S.launch) {
      var rows = S.launch.top.map(function (i) {
        return '<li class="d-flex justify-content-between py-1"><span>' + esc(i.name) + ', ' + num(i.shelf) + ' on shelf</span><span>' + money(i.cap) + '</span></li>';
      });
      if (S.launch.rest_n > 0) {
        rows.push('<li class="d-flex justify-content-between py-1 soft"><span>and ' + num(S.launch.rest_n) + ' more items</span><span>' + money(S.launch.rest_cap) + '</span></li>');
      }
      $('#affected').innerHTML = rows.join('');
    }

    // data check on the Upload screen
    var u = S.upload;
    $('#blockedTile').className = 'tile h-100 d-flex flex-column align-items-center justify-content-center text-center px-2 ' + (u.blocked > 0 ? 'tile-red' : 'glass');
    if (u.issues.length === 0) {
      $('#issuesTitle').textContent = 'No problems found';
      $('#issues').innerHTML = '<li class="soft">Every row has a cost, a valid quantity and valid dates.</li>';
    } else {
      $('#issuesTitle').textContent = (u.blocked > 0 ? num(u.blocked) + ' to fix' : 'Worth a look');
      $('#issues').innerHTML = u.issues.map(function (x) {
        var cls = x.level === 'error' ? 'chip-red' : 'chip-amber';
        return '<li class="d-flex gap-2 align-items-start"><span class="chip ' + cls + '">' + (x.level === 'error' ? 'Blocked' : 'Look') + '</span><span>' + esc(x.text) + '</span></li>';
      }).join('');
    }

    // sample purchase bill built from the largest at-risk items
    $('#billRows').innerHTML = S.bill.lines.map(function (l) {
      var match = l.unclear ? '<span class="chip chip-amber">Needs a look: ' + esc(l.matched) + '?</span>' : '<span class="chip chip-green">' + esc(l.matched) + '</span>';
      var name = esc(l.on_bill) + (l.unclear ? ' <span class="soft small">(part of the text is unclear)</span>' : '');
      return '<tr><td>' + name + '</td><td class="text-end">' + num(l.qty) + '</td><td class="text-end">' + money2(l.each) + '</td><td class="text-end">' + money2(l.line) + '</td><td>' + match + '</td></tr>';
    }).join('');
  }

  function renderKpi() {
    var now = atRisk();
    var down = BASE - now;
    $all('[data-kpi]').forEach(function (el) { el.textContent = money(now); });
    $all('[data-kpi-note]').forEach(function (el) {
      el.hidden = down === 0;
      el.textContent = down === 0 ? '' : 'Down ' + money(down) + ' from ' + money(BASE);
    });
    $('#live').textContent = 'Money at risk with no plan is ' + money(now) + '.';
  }

  function cardHtml(p) {
    var d = state.decisions[p.id];
    var cls = d === 'accepted' ? ' style="border-color:#5FD0A3;background:rgba(18,128,63,.45)"' : (d === 'declined' ? ' style="opacity:.7"' : '');
    var id = esc(p.id);
    var actions;
    if (d === 'accepted') {
      actions = '<div class="d-flex flex-wrap align-items-center gap-2"><span class="chip chip-green d-inline-flex align-items-center gap-1"><svg class="ico" style="width:1.1em;height:1.1em" aria-hidden="true"><use href="#i-check"/></svg>Accepted</span><button type="button" class="btn-pill btn-soft" data-action="undo" data-id="' + id + '">Undo</button></div>';
    } else if (d === 'declined') {
      actions = '<div class="d-flex flex-wrap align-items-center gap-2"><span class="soft">Declined.</span><button type="button" class="btn-pill btn-soft" data-action="undo" data-id="' + id + '">Undo</button></div>';
    } else {
      actions = '<div class="d-flex flex-wrap gap-2"><button type="button" class="btn-pill btn-go" data-action="accept" data-id="' + id + '">Accept</button><button type="button" class="btn-pill btn-soft" data-action="decline" data-id="' + id + '">Decline</button></div>';
    }
    return '<div class="col-12"><article class="tile glass d-flex flex-column gap-3" aria-label="' + esc(p.name) + '"' + cls + '>' +
      '<div class="d-flex flex-wrap justify-content-between align-items-center gap-2"><div><div class="fs-5 fw-bolder">' + esc(p.name) + '</div><div class="soft small">' + num(p.shelf) + ' on shelf, ' + money(p.tied) + ' tied up</div></div><span class="chip fs-6">Option: ' + esc(p.option) + '</span></div>' +
      '<ul class="m-0 ps-3 small">' + p.reasons.map(function (r) { return '<li class="mb-1">' + esc(r) + '</li>'; }).join('') + '</ul>' +
      '<div class="d-flex flex-wrap gap-2">' + p.chips.map(function (c) { return '<span class="chip">' + esc(c) + '</span>'; }).join('') + '</div>' +
      actions + '</article></div>';
  }

  function renderAsk() {
    $('#askPlan').hidden = !state.help;
    $('#askButtons').hidden = state.help;
    if (state.help) { $('#askLater').hidden = true; }
    $('#cards').innerHTML = PLANS.map(cardHtml).join('');
  }

  function renderAll() { renderKpi(); renderAsk(); }

  function show(name) {
    if (VIEWS.indexOf(name) < 0) { name = 'home'; }
    $all('.view').forEach(function (v) { v.hidden = v.getAttribute('data-view') !== name; });
    $all('.dock a').forEach(function (a) {
      if (a.getAttribute('data-nav') === name) { a.setAttribute('aria-current', 'page'); } else { a.removeAttribute('aria-current'); }
    });
    $('#viewName').textContent = TITLES[name];
    document.title = 'Loreg: ' + TITLES[name];
    var h = $('.view[data-view="' + name + '"] .vh');
    if (h) { h.focus({ preventScroll: true }); }
    if (typeof window.scrollTo === 'function') { window.scrollTo(0, 0); }
  }

  function addMessage(text, who) {
    var el = document.createElement('div');
    el.className = who === 'user' ? 'bubble-user' : 'bubble-bot glass';
    el.textContent = text;
    $('#chat').appendChild(el);
    if (typeof el.scrollIntoView === 'function') { el.scrollIntoView({ block: 'nearest' }); }
  }

  function wire() {
    document.addEventListener('click', function (e) {
      var b = e.target.closest('[data-action]');
      if (!b) { return; }
      var a = b.getAttribute('data-action');
      var id = b.getAttribute('data-id');
      if (a === 'help') {
        state.help = true; renderAll();
        var go = b.getAttribute('data-go');
        if (go) { location.hash = '#' + go; }
      } else if (a === 'accept') { state.decisions[id] = 'accepted'; renderAll(); }
      else if (a === 'decline') { state.decisions[id] = 'declined'; renderAll(); }
      else if (a === 'undo') { delete state.decisions[id]; renderAll(); }
      else if (a === 'home-later') { $('#qAsk').hidden = true; $('#qLater').hidden = false; }
      else if (a === 'home-undo') { $('#qAsk').hidden = false; $('#qLater').hidden = true; }
      else if (a === 'watch-dismiss') { $('#watchActions').hidden = true; $('#watchDismissed').hidden = false; }
      else if (a === 'watch-undo') { $('#watchActions').hidden = false; $('#watchDismissed').hidden = true; }
      else if (a === 'ask-later') { $('#askLater').hidden = false; }
      else if (a === 'ask-undo') { state.help = true; renderAll(); }
    });
    $('#composer').addEventListener('submit', function (e) {
      e.preventDefault();
      var input = $('#q');
      var text = input.value.trim();
      if (!text) { return; }
      addMessage(text, 'user');
      addMessage('This prototype answers one question: “What should I clear before the launch?” Use the buttons above to see the plan.', 'bot');
      input.value = '';
    });
    window.addEventListener('hashchange', function () { show(location.hash.slice(1)); });
  }

  function start(snapshot) {
    S = snapshot;
    BASE = S.totals.at_risk.cap;
    PLANS = S.launch ? S.launch.plans : [];
    renderStatic();
    renderAll();
    $('#main').hidden = false;
    wire();
    show(location.hash.slice(1));
  }

  var embedded = $('#snapshot');
  if (embedded && embedded.textContent.trim()) {
    start(JSON.parse(embedded.textContent));
  } else {
    fetch('snapshot.json').then(function (r) { return r.json(); }).then(start).catch(function () { $('#loadError').hidden = false; });
  }
})();
