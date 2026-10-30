/* SUP_KPI - page web (aucune bibliothèque externe : marche sans internet). */
(function () {
  "use strict";

  var REFRESH_MS = 30000;
  var STATUS = {
    green:  { icon: "●", label: "OK" },
    orange: { icon: "■", label: "Quelques erreurs" },
    red:    { icon: "▲", label: "Beaucoup d'erreurs" },
    silent: { icon: "○", label: "Muet" },
    nodata: { icon: "○", label: "Pas de nouvelles" }
  };

  var state = { tab: "dashboard", period: "24h", server: "", app: "", pattern: "", q: "", dailyDate: "" };

  // ------------------------------------------------------------ utilitaires
  function $(id) { return document.getElementById(id); }
  function esc(s) {
    return String(s == null ? "" : s).replace(/[&<>"']/g, function (c) {
      return { "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c];
    });
  }
  function fmt(n) { return (n || 0).toLocaleString("fr-FR"); }
  function pad(n) { return n < 10 ? "0" + n : "" + n; }
  function fmtTime(ts) {
    if (!ts) return "–";
    var d = new Date(ts * 1000);
    return pad(d.getHours()) + ":" + pad(d.getMinutes()) + ":" + pad(d.getSeconds());
  }
  function fmtDateTime(ts) {
    if (!ts) return "–";
    var d = new Date(ts * 1000), today = new Date();
    var sameDay = d.toDateString() === today.toDateString();
    return (sameDay ? "" : pad(d.getDate()) + "/" + pad(d.getMonth() + 1) + " ") + pad(d.getHours()) + ":" + pad(d.getMinutes()) + (sameDay ? ":" + pad(d.getSeconds()) : "");
  }
  function ago(ts) {
    if (!ts) return "jamais";
    var s = Math.max(0, Math.round(Date.now() / 1000 - ts));
    if (s < 60) return "il y a " + s + " s";
    if (s < 3600) return "il y a " + Math.round(s / 60) + " min";
    if (s < 86400) return "il y a " + Math.round(s / 3600) + " h";
    return "il y a " + Math.round(s / 86400) + " j";
  }
  function isoDate(d) { return d.getFullYear() + "-" + pad(d.getMonth() + 1) + "-" + pad(d.getDate()); }
  function frDate(iso) { var p = iso.split("-"); return p[2] + "/" + p[1] + "/" + p[0]; }
  function badge(status) {
    var s = STATUS[status];
    return '<span class="badge"><span class="status-icon ' + status + '">' + s.icon + "</span>" + s.label + "</span>";
  }
  function delta(cur, prev, suffix) {
    var d = cur - prev;
    if (d === 0) return "= identique " + suffix;
    var cls = d > 0 ? "delta-up" : "delta-down";
    var arrow = d > 0 ? "↑" : "↓";
    var pct = prev > 0 ? " (" + (d > 0 ? "+" : "") + Math.round((d / prev) * 100) + " %)" : "";
    return '<span class="' + cls + '">' + arrow + " " + (d > 0 ? "+" : "") + fmt(d) + pct + "</span> " + suffix;
  }
  function api(path, opts) {
    return fetch(path, opts).then(function (r) {
      return r.json().then(function (data) {
        if (!r.ok) throw new Error(data.error || "Erreur " + r.status);
        return data;
      });
    });
  }
  function qs(obj) {
    return Object.keys(obj).filter(function (k) { return obj[k]; })
      .map(function (k) { return encodeURIComponent(k) + "=" + encodeURIComponent(obj[k]); }).join("&");
  }
  function setRefresh(ok, text) {
    $("refresh-dot").className = "dot" + (ok ? "" : " err");
    $("refresh-text").textContent = text;
  }

  // ------------------------------------------------------------ infobulle
  var tip = $("tooltip");
  function showTip(html, x, y) {
    tip.innerHTML = html;
    tip.hidden = false;
    var w = tip.offsetWidth, h = tip.offsetHeight;
    var left = Math.min(window.innerWidth - w - 8, x + 14);
    var top = y - h - 12 < 4 ? y + 16 : y - h - 12;
    tip.style.left = Math.max(4, left) + "px";
    tip.style.top = top + "px";
  }
  function hideTip() { tip.hidden = true; }

  // ------------------------------------------------------------ onglets
  document.querySelectorAll(".tab").forEach(function (btn) {
    btn.addEventListener("click", function () { switchTab(btn.dataset.tab); });
  });
  function switchTab(name) {
    state.tab = name;
    document.querySelectorAll(".tab").forEach(function (b) { b.classList.toggle("active", b.dataset.tab === name); });
    document.querySelectorAll(".tab-panel").forEach(function (p) { p.hidden = p.id !== "tab-" + name; });
    try { localStorage.setItem("supkpi-tab", name); } catch (e) { /* rien */ }
    refresh();
  }

  // ------------------------------------------------------------ tableau de bord
  document.querySelectorAll("#period button").forEach(function (btn) {
    btn.addEventListener("click", function () {
      state.period = btn.dataset.period;
      document.querySelectorAll("#period button").forEach(function (b) { b.classList.toggle("active", b === btn); });
      refresh();
    });
  });
  $("filter-server").addEventListener("change", function (e) { state.server = e.target.value; state.app = ""; refresh(); });
  $("filter-app").addEventListener("change", function (e) { state.app = e.target.value; refresh(); });
  $("filter-pattern").addEventListener("change", function (e) { state.pattern = e.target.value; loadErrors(); });
  var qTimer;
  $("filter-q").addEventListener("input", function (e) {
    clearTimeout(qTimer);
    qTimer = setTimeout(function () { state.q = e.target.value.trim(); loadErrors(); }, 300);
  });
  $("filter-reset").addEventListener("click", function () {
    state.server = ""; state.app = ""; state.pattern = ""; state.q = "";
    $("filter-q").value = "";
    refresh();
  });

  function fillSelect(sel, values, current, allLabel) {
    var html = '<option value="">' + allLabel + "</option>";
    values.forEach(function (v) {
      html += '<option value="' + esc(v) + '"' + (v === current ? " selected" : "") + ">" + esc(v) + "</option>";
    });
    sel.innerHTML = html;
  }

  function loadDashboard() {
    var params = qs({ period: state.period, server: state.server, app: state.app });
    return api("/api/kpi?" + params).then(function (k) {
      renderKpi(k);
      return loadErrors();
    });
  }

  function renderKpi(k) {
    var servers = k.servers.map(function (s) { return s.server; });
    fillSelect($("filter-server"), servers, state.server, "Tous");
    var apps = [];
    k.applications.forEach(function (a) {
      if ((!state.server || a.server === state.server) && apps.indexOf(a.app) < 0) apps.push(a.app);
    });
    apps.sort();
    fillSelect($("filter-app"), apps, state.app, "Toutes");
    fillSelect($("filter-pattern"), k.by_pattern.map(function (p) { return p.name; }), state.pattern, "Tous les patterns");

    var periodLabel = { "1h": "l'heure précédente", "24h": "les 24 h précédentes", "7d": "les 7 jours précédents", "30d": "les 30 jours précédents" }[k.period];
    $("kpi-total").textContent = fmt(k.total);
    $("kpi-delta").innerHTML = delta(k.total, k.previous_total, "vs " + periodLabel);
    $("kpi-ok").textContent = fmt(k.summary.ok);
    $("kpi-alert").textContent = fmt(k.summary.warn + k.summary.crit);
    $("kpi-alert-sub").textContent = k.summary.crit + " beaucoup · " + k.summary.warn + " quelques erreurs";
    $("kpi-silent").textContent = fmt(k.summary.silent);
    if (k.by_app.length) {
      $("kpi-worst").textContent = k.by_app[0].app;
      $("kpi-worst").title = k.by_app[0].app;
      $("kpi-worst-sub").textContent = fmt(k.by_app[0].count) + " erreurs · " + k.by_app[0].server;
    } else {
      $("kpi-worst").textContent = "Aucune";
      $("kpi-worst-sub").textContent = "pas d'erreur sur la période";
    }
    var t = k.thresholds;
    $("threshold-text").textContent = "(sur la dernière heure : orange dès " + t.hour_warn + ", rouge dès " + t.hour_crit + " erreurs)";

    renderServers(k.servers);
    renderSeries(k);
    renderBars($("bars-server"), k.by_server.map(function (r) {
      return { label: esc(r.name), value: r.count, onClick: function () { state.server = r.name; state.app = ""; refresh(); } };
    }));
    renderBars($("bars-app"), k.by_app.map(function (r) {
      return { label: esc(r.app) + " <small>· " + esc(r.server) + "</small>", value: r.count,
        onClick: function () { state.server = r.server; state.app = r.app; refresh(); } };
    }));
    renderTop(k.top_messages);
  }

  function renderServers(servers) {
    var grid = $("server-grid");
    if (!servers.length) {
      grid.innerHTML = '<div class="empty">Aucun serveur pour l\'instant. Installe un robot (agent) sur un serveur : il apparaîtra ici.</div>';
      return;
    }
    grid.innerHTML = "";
    servers.forEach(function (s) {
      var el = document.createElement("button");
      el.type = "button";
      el.className = "server " + s.status + (state.server === s.server ? " selected" : "");
      el.innerHTML =
        '<div class="server-top"><span class="server-name"><span class="status-icon ' + s.status + '">' + STATUS[s.status].icon + "</span> " + esc(s.server) + "</span>" +
        '<span class="server-count" title="erreurs sur la dernière heure">' + fmt(s.errors_last_hour) + "</span></div>" +
        '<div class="server-meta">' + STATUS[s.status].label + " · " + fmt(s.errors_last_24h) + " en 24 h · vu " + ago(s.last_seen) + "</div>" +
        '<div class="server-apps">' + s.applications.map(function (a) {
          return '<span class="app-chip" title="' + fmt(a.errors_last_hour) + ' erreur(s) sur la dernière heure"><span class="status-icon ' + a.status + '">' + STATUS[a.status].icon + "</span> " + esc(a.app) + "</span>";
        }).join("") + "</div>";
      el.addEventListener("click", function () {
        state.server = state.server === s.server ? "" : s.server;
        state.app = "";
        refresh();
      });
      grid.appendChild(el);
    });
  }

  function niceMax(v) {
    // 4 graduations aux valeurs rondes (1, 2, 5, 10, 20, 50...)
    var raw = Math.max(1, v / 4);
    var p = Math.pow(10, Math.floor(Math.log10(raw)));
    var steps = [1, 2, 5, 10];
    for (var i = 0; i < steps.length; i++) if (steps[i] * p >= raw) return steps[i] * p * 4;
    return 40 * p;
  }

  function bucketLabel(ts, bucket, long) {
    var d = new Date(ts * 1000);
    var hm = pad(d.getHours()) + ":" + pad(d.getMinutes());
    var dm = pad(d.getDate()) + "/" + pad(d.getMonth() + 1);
    if (bucket >= 86400) return dm;
    if (bucket >= 3600 * 6) return long ? dm + " " + hm : (d.getHours() === 0 ? dm : hm);
    return long && bucket >= 3600 ? dm + " " + hm : hm;
  }

  function renderSeries(k) {
    var box = $("chart-series");
    var data = k.series;
    var bucket = k.bucket_seconds;
    var unit = bucket >= 86400 ? "par jour" : bucket >= 3600 ? (bucket === 3600 ? "par heure" : "par tranche de " + bucket / 3600 + " h") : "par tranche de " + bucket / 60 + " min";
    $("chart-caption").textContent = "Nombre d'erreurs " + unit;
    if (!data.length) { box.innerHTML = '<div class="empty">Pas de données</div>'; return; }

    var W = box.clientWidth || 800, H = box.clientHeight || 240;
    var m = { l: 44, r: 12, t: 10, b: 26 };
    var iw = W - m.l - m.r, ih = H - m.t - m.b;
    var max = niceMax(Math.max.apply(null, data.map(function (d) { return d.count; })));
    var n = data.length;
    function x(i) { return m.l + (n === 1 ? iw / 2 : (i * iw) / (n - 1)); }
    function y(v) { return m.t + ih - (v / max) * ih; }

    var svg = '<svg viewBox="0 0 ' + W + " " + H + '" role="img" aria-label="Évolution du nombre d\'erreurs">';
    for (var g = 0; g <= 4; g++) {
      var gv = (max * g) / 4, gy = y(gv);
      svg += '<line class="' + (g === 0 ? "baseline" : "gridline") + '" x1="' + m.l + '" x2="' + (W - m.r) + '" y1="' + gy + '" y2="' + gy + '"/>';
      svg += '<text class="tick" x="' + (m.l - 8) + '" y="' + (gy + 4) + '" text-anchor="end">' + fmt(Math.round(gv)) + "</text>";
    }
    var every = Math.max(1, Math.ceil(n / Math.max(2, Math.floor(iw / 70))));
    for (var i = 0; i < n; i += every) {
      svg += '<text class="tick" x="' + x(i) + '" y="' + (H - 6) + '" text-anchor="middle">' + bucketLabel(data[i].ts, bucket) + "</text>";
    }
    var pts = data.map(function (d, i) { return x(i).toFixed(1) + "," + y(d.count).toFixed(1); });
    svg += '<path class="area" d="M' + x(0) + "," + y(0) + " L" + pts.join(" L") + " L" + x(n - 1) + "," + y(0) + ' Z"/>';
    svg += '<path class="line" d="M' + pts.join(" L") + '"/>';
    svg += '<line class="cross" id="cross" x1="0" x2="0" y1="' + m.t + '" y2="' + (m.t + ih) + '" visibility="hidden"/>';
    svg += '<circle class="marker" id="marker" r="4.5" cx="0" cy="0" visibility="hidden"/>';
    svg += '<rect id="hit" x="' + m.l + '" y="0" width="' + iw + '" height="' + H + '" fill="transparent"/>';
    svg += "</svg>";
    box.innerHTML = svg;

    var hit = box.querySelector("#hit"), cross = box.querySelector("#cross"), marker = box.querySelector("#marker");
    var svgEl = box.querySelector("svg");
    hit.addEventListener("mousemove", function (e) {
      var r = svgEl.getBoundingClientRect();
      var px = ((e.clientX - r.left) / r.width) * W;
      var i = n === 1 ? 0 : Math.round(((px - m.l) / iw) * (n - 1));
      i = Math.max(0, Math.min(n - 1, i));
      var d = data[i];
      cross.setAttribute("x1", x(i)); cross.setAttribute("x2", x(i)); cross.setAttribute("visibility", "visible");
      marker.setAttribute("cx", x(i)); marker.setAttribute("cy", y(d.count)); marker.setAttribute("visibility", "visible");
      showTip(bucketLabel(d.ts, bucket, true) + "<br><b>" + fmt(d.count) + "</b> erreur(s)", e.clientX, e.clientY);
    });
    hit.addEventListener("mouseleave", function () {
      cross.setAttribute("visibility", "hidden"); marker.setAttribute("visibility", "hidden"); hideTip();
    });
  }

  function renderBars(box, rows) {
    if (!rows.length) { box.innerHTML = '<div class="empty">Aucune erreur sur la période 🎉</div>'; return; }
    var max = Math.max.apply(null, rows.map(function (r) { return r.value; }));
    box.innerHTML = "";
    rows.forEach(function (r) {
      var row = document.createElement("div");
      row.className = "bar-row";
      row.title = "Cliquer pour filtrer";
      row.innerHTML = '<div class="bar-label">' + r.label + '</div><div class="bar-track"><div class="bar-fill" style="width:' +
        ((r.value / max) * 100).toFixed(1) + '%"></div></div><div class="bar-value">' + fmt(r.value) + "</div>";
      row.addEventListener("click", r.onClick);
      box.appendChild(row);
    });
  }

  function renderTop(rows) {
    var body = $("top-body");
    if (!rows.length) { body.innerHTML = '<tr><td colspan="5" class="empty">Aucune erreur sur la période 🎉</td></tr>'; return; }
    body.innerHTML = rows.map(function (r) {
      return "<tr><td class=\"num\"><b>" + fmt(r.count) + '</b></td><td class="mono" title="' + esc(r.signature) + '">' + esc(r.example) +
        "</td><td>" + esc((r.apps || "").split(",").join(", ")) + '</td><td class="num">' + r.servers + '</td><td class="nowrap">' + fmtDateTime(r.last_ts) + "</td></tr>";
    }).join("");
  }

  function loadErrors() {
    var params = qs({ period: state.period, server: state.server, app: state.app, pattern: state.pattern, q: state.q, limit: 300 });
    return api("/api/errors?" + params).then(function (d) {
      var body = $("errors-body");
      if (!d.errors.length) { body.innerHTML = '<tr><td colspan="5" class="empty">Aucune ligne trouvée</td></tr>'; return; }
      body.innerHTML = d.errors.map(function (e) {
        return '<tr><td class="nowrap">' + fmtDateTime(e.ts) + '</td><td class="nowrap">' + esc(e.server) + '</td><td class="nowrap">' + esc(e.app) +
          '</td><td class="nowrap">' + esc(e.pattern) + '</td><td class="mono" title="' + esc(e.file) + '">' + esc(e.line) + "</td></tr>";
      }).join("");
    });
  }

  // ------------------------------------------------------------ bilan de la veille
  function yesterday() { var d = new Date(); d.setDate(d.getDate() - 1); return isoDate(d); }
  $("daily-date").addEventListener("change", function (e) { state.dailyDate = e.target.value || yesterday(); loadDaily(); });
  $("daily-yesterday").addEventListener("click", function () { state.dailyDate = yesterday(); loadDaily(); });

  function loadDaily() {
    if (!state.dailyDate) state.dailyDate = yesterday();
    $("daily-date").value = state.dailyDate;
    $("daily-date").max = isoDate(new Date());
    $("daily-csv").href = "/api/daily-report.csv?date=" + state.dailyDate;
    return api("/api/daily-report?date=" + state.dailyDate).then(function (r) {
      var s = r.summary;
      var isYesterday = r.date === yesterday();
      $("daily-title").textContent = "Erreurs " + (isYesterday ? "d'hier" : "du " + frDate(r.date)) + " (" + frDate(r.date) + ")";
      $("daily-total").textContent = fmt(s.total);
      $("daily-delta").innerHTML = delta(s.total, s.previous_total, "vs le " + frDate(r.previous_date));
      $("daily-green").textContent = fmt(s.green);
      $("daily-orange").textContent = fmt(s.orange);
      $("daily-red").textContent = fmt(s.red);
      $("daily-nodata").textContent = fmt(s.nodata);
      $("daily-thresholds").textContent = "Orange dès " + r.thresholds.day_warn + " erreur(s), rouge dès " + r.thresholds.day_crit + " dans la journée";
      var body = $("daily-body");
      if (!r.rows.length) {
        body.innerHTML = '<tr><td colspan="9" class="empty">Aucune application connue pour cette journée.</td></tr>';
        return;
      }
      body.innerHTML = r.rows.map(function (row) {
        var d = row.delta;
        var evo = d === 0 ? "=" : '<span class="' + (d > 0 ? "delta-up" : "delta-down") + '">' + (d > 0 ? "↑ +" : "↓ ") + fmt(d) + "</span>";
        var top = row.top_message
          ? '<span class="mono">' + esc(row.top_message) + '</span> <span class="muted">(' + fmt(row.top_message_count) + "×)</span>"
          : '<span class="muted">' + (row.status === "nodata" ? "Le robot n'a rien envoyé ce jour-là" : "Rien à signaler") + "</span>";
        return "<tr><td>" + badge(row.status) + '</td><td class="nowrap">' + esc(row.server) + '</td><td class="nowrap"><b>' + esc(row.app) +
          '</b></td><td class="num"><b>' + fmt(row.count) + '</b></td><td class="num">' + fmt(row.previous_count) + '</td><td class="num">' + evo +
          '</td><td class="nowrap">' + fmtTime(row.first_ts) + '</td><td class="nowrap">' + fmtTime(row.last_ts) + "</td><td>" + top + "</td></tr>";
      }).join("");
    });
  }

  // ------------------------------------------------------------ patterns
  function loadPatterns() {
    return api("/api/patterns").then(function (d) {
      var body = $("patterns-body");
      if (!d.patterns.length) { body.innerHTML = '<tr><td colspan="4" class="empty">Aucun pattern : rien ne sera remonté !</td></tr>'; return; }
      body.innerHTML = d.patterns.map(function (p) {
        return '<tr><td class="mono"><b>' + esc(p.text) + "</b></td><td>" + (p.is_regex ? "Expression régulière" : "Texte") +
          '</td><td><label class="check switch"><input type="checkbox" data-toggle="' + p.id + '"' + (p.enabled ? " checked" : "") + "> " +
          (p.enabled ? "Oui" : "Non") + '</label></td><td><button class="btn-secondary btn-small" data-delete="' + p.id + '" data-text="' + esc(p.text) + '">Supprimer</button></td></tr>';
      }).join("");
    });
  }

  $("patterns-body").addEventListener("change", function (e) {
    var id = e.target.dataset.toggle;
    if (!id) return;
    api("/api/patterns/" + id + "/" + (e.target.checked ? "enable" : "disable"), { method: "POST" })
      .then(loadPatterns).catch(function (err) { message(err.message, false); });
  });
  $("patterns-body").addEventListener("click", function (e) {
    var id = e.target.dataset.delete;
    if (!id) return;
    if (!confirm("Supprimer le pattern « " + e.target.dataset.text + " » ?")) return;
    api("/api/patterns/" + id, { method: "DELETE" }).then(function () {
      message("Pattern supprimé.", true);
      return loadPatterns();
    }).catch(function (err) { message(err.message, false); });
  });
  $("pattern-form").addEventListener("submit", function (e) {
    e.preventDefault();
    var text = $("pattern-text").value.trim();
    if (!text) return;
    api("/api/patterns", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ text: text, is_regex: $("pattern-regex").checked })
    }).then(function () {
      $("pattern-text").value = "";
      $("pattern-regex").checked = false;
      message("« " + text + " » ajouté. Les robots le chercheront dès leur prochain passage (moins d'une minute).", true);
      return loadPatterns();
    }).catch(function (err) { message(err.message, false); });
  });
  function message(text, ok) {
    var el = $("pattern-msg");
    el.textContent = text;
    el.className = "msg " + (ok ? "ok" : "err");
  }

  // ------------------------------------------------------------ rafraîchissement
  function refresh() {
    var job = state.tab === "daily" ? loadDaily() : state.tab === "patterns" ? loadPatterns() : loadDashboard();
    return job.then(function () {
      var d = new Date();
      setRefresh(true, "Mis à jour à " + pad(d.getHours()) + ":" + pad(d.getMinutes()) + ":" + pad(d.getSeconds()));
    }).catch(function (err) {
      setRefresh(false, "Central injoignable : " + err.message);
    });
  }

  var resizeTimer;
  window.addEventListener("resize", function () {
    clearTimeout(resizeTimer);
    resizeTimer = setTimeout(function () { if (state.tab === "dashboard") refresh(); }, 200);
  });

  var saved = null;
  try { saved = localStorage.getItem("supkpi-tab"); } catch (e) { /* rien */ }
  switchTab(saved === "daily" || saved === "patterns" ? saved : "dashboard");
  setInterval(function () { if (state.tab !== "patterns") refresh(); }, REFRESH_MS);
})();
