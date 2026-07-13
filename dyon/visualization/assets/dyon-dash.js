/* Dyon dashboard renderer.
 *
 * Builds a modern app shell — sidebar · stage · chat dock — from a DashboardSpec
 * fetched at /api/viz/spec, and lays each twin's panels out into deliberate
 * regions (alarm banner, KPI strip, a 3D-scene + primary-trend hero, a secondary
 * chart grid, and an aside for events/state) instead of a flat grid. The very
 * same renderer drives a lone twin and each member of a combined twin: a member
 * view just points the renderer at that member's own api_base.
 *
 * Customise without forking:
 *   DyonDash.register("kpi", fn)   // override or add a panel renderer: (panel, D)
 *   DyonDash.setOverviewRenderer(fn)  // combined-twin overview (see dyon-combined.js)
 *
 * No build step: this file and its CDN libraries load directly, so opening the
 * page from a file:// URL (with ?api=<twin-url>) works.
 */
(function () {
  "use strict";

  // api_base resolution: ?api= query param, then spec.api_base, then same-origin.
  // CORS is open on twin APIs, so a separately served shell can point anywhere —
  // and a combined twin federates each member from that member's own api_base.
  const params = new URLSearchParams(location.search);
  const ROOT_API = (params.get("api") || "").replace(/\/$/, "");
  const THEME_KEY = "dyon-theme";

  // API key for a hardened twin (production mode). Read once from the URL
  // (?api_key=) or localStorage; empty in dev mode, where every helper below is
  // a no-op so behaviour is unchanged. EventSource/WebSocket cannot set headers,
  // so the key rides as a query param there; fetch() sends it as x-api-key.
  const API_KEY =
    params.get("api_key") || localStorage.getItem("dyon_api_key") || "";
  function apiKeyed(url) {
    if (!API_KEY) return url;
    return url + (url.includes("?") ? "&" : "?") + "api_key=" + encodeURIComponent(API_KEY);
  }
  function keyHeaders(h) {
    h = h || {};
    if (API_KEY) h["x-api-key"] = API_KEY;
    return h;
  }

  // --- helpers -------------------------------------------------------------
  function el(tag, cls, html) {
    const e = document.createElement(tag);
    if (cls) e.className = cls;
    if (html != null) e.innerHTML = html;
    return e;
  }

  function fmt(value, spec) {
    if (value == null || Number.isNaN(value)) return "—";
    if (typeof value !== "number") return String(value);
    const f = (spec && spec.format) || "{:.1f}";
    const m = f.match(/\{:\.(\d+)f\}/);
    return m ? Number(value).toFixed(Number(m[1])) : String(value);
  }

  function cssVar(name, fallback) {
    return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback;
  }

  // --- theme ---------------------------------------------------------------
  function applyTheme(theme) {
    if (!theme) return;
    const root = document.documentElement;
    for (const [k, v] of Object.entries(theme)) root.style.setProperty(k, v);
  }
  function currentMode() {
    return localStorage.getItem(THEME_KEY) || "dark";
  }
  function applyMode(mode) {
    document.documentElement.setAttribute("data-theme", mode);
  }
  function toggleMode() {
    const next = currentMode() === "dark" ? "light" : "dark";
    localStorage.setItem(THEME_KEY, next);
    applyMode(next);
    return next;
  }

  // --- panel registry ------------------------------------------------------
  // Every renderer is (panel, D) -> { el, fields?, update(frame?) }, where D is
  // the dashboard instance owning the panel (its live data + api base + alarms).
  const Registry = {};
  function register(kind, fn) { Registry[kind] = fn; }

  register("kpi", function (panel, D) {
    const cfg = panel.config || {};
    const binding = cfg.binding || {};
    const field = binding.field;
    const root = el("div", "panel kpi");
    root.appendChild(el("div", "panel-title", panel.title || binding.label || field));
    const valEl = el("div", "kpi-value", "—");
    if (binding.unit) valEl.appendChild(el("span", "kpi-unit", binding.unit));
    root.appendChild(valEl);
    let spark = null;
    if (cfg.sparkline) { spark = el("canvas", "spark"); root.appendChild(spark); }

    function update() {
      const v = D.latest[field];
      valEl.firstChild.nodeValue = fmt(v, cfg);
      const lvl = D.alarmLevel(field, v);
      root.classList.toggle("warn", lvl === "warn");
      root.classList.toggle("crit", lvl === "crit");
      if (spark) drawSpark(spark, (D.series[field] || []).map((p) => p.v));
    }
    return { el: root, fields: field ? [field] : [], update };
  });

  function drawSpark(canvas, values) {
    const ctx = canvas.getContext("2d");
    const w = canvas.width = canvas.clientWidth || 200;
    const h = canvas.height = canvas.clientHeight || 36;
    ctx.clearRect(0, 0, w, h);
    if (values.length < 2) return;
    const min = Math.min(...values), max = Math.max(...values);
    const span = max - min || 1;
    ctx.strokeStyle = cssVar("--accent", "#5b8cff");
    ctx.lineWidth = 1.6;
    ctx.beginPath();
    values.forEach((v, i) => {
      const x = (i / (values.length - 1)) * w;
      const y = h - ((v - min) / span) * (h - 4) - 2;
      i ? ctx.lineTo(x, y) : ctx.moveTo(x, y);
    });
    ctx.stroke();
  }

  register("alarms", function (panel, D) {
    const root = el("div", "panel alarms");
    root.appendChild(el("div", "panel-title", panel.title || "Alarms"));
    const list = el("div", "alarm-list");
    root.appendChild(list);
    function update() {
      list.innerHTML = "";
      const { warn, crit } = D.alarms;
      if (!warn.length && !crit.length) {
        list.appendChild(el("span", "all-clear", "All systems nominal"));
        return;
      }
      crit.forEach((f) => list.appendChild(el("span", "alarm-pill crit", "CRIT · " + f)));
      warn.forEach((f) => list.appendChild(el("span", "alarm-pill warn", "WARN · " + f)));
    }
    return { el: root, update };
  });

  register("events", function (panel, D) {
    const root = el("div", "panel events");
    root.appendChild(el("div", "panel-title", panel.title || "Event Log"));
    const log = el("div", "log");
    root.appendChild(log);
    async function refresh() {
      try {
        const r = await fetch(D.api("/api/twin/events?n=20"), { headers: keyHeaders() });
        if (!r.ok) return;
        const data = await r.json();
        log.innerHTML = "";
        const evs = (data.events || []).slice().reverse();
        if (!evs.length) { log.appendChild(el("div", "muted-note", "No recent events.")); return; }
        evs.forEach((ev) => {
          const sev = ev.severity || "info";
          const row = el("div", "row " + sev);
          const t = ev.timestamp ? new Date(ev.timestamp).toLocaleTimeString() : "";
          row.appendChild(el("span", "ts", t));
          row.appendChild(el("span", "type", ev.event_type || ev.type || "event"));
          log.appendChild(row);
        });
      } catch (_) { /* events endpoint optional */ }
    }
    refresh();
    const timer = setInterval(refresh, 10000);
    return { el: root, update() {}, destroy() { clearInterval(timer); } };
  });

  register("fsm", function (panel, D) {
    const cfg = panel.config || {};
    const root = el("div", "panel fsm");
    root.appendChild(el("div", "panel-title", panel.title || cfg.title || "State"));
    const wrap = el("div", "states");
    const nodes = {};
    (cfg.states || []).forEach((s) => {
      const n = el("div", "state", s);
      nodes[s] = n;
      wrap.appendChild(n);
    });
    root.appendChild(wrap);
    function update() {
      const cur = D.latest[cfg.state_field];
      Object.entries(nodes).forEach(([s, n]) =>
        n.classList.toggle("active", String(s) === String(cur)));
    }
    return { el: root, fields: cfg.state_field ? [cfg.state_field] : [], update };
  });

  register("chart", function (panel, D) {
    const cfg = panel.config || {};
    const hero = !!panel._hero;
    const root = el("div", "panel chart" + (hero ? " span-hero" : ""));
    root.appendChild(el("div", "panel-title", panel.title || cfg.title || "Trend"));
    const host = el("div", "vega-host");
    root.appendChild(host);
    let view = null;
    const fields = cfg.fields || [];

    async function render() {
      const spec = cfg.vega_lite || buildTimeseriesSpec(fields, hero ? 300 : 220);
      try {
        const res = await vegaEmbed(host, spec, { actions: false, theme: vegaTheme() });
        view = res.view;
        update();
      } catch (e) { host.innerHTML = '<div class="error">chart error: ' + e + "</div>"; }
    }
    render();

    function update() {
      if (!view) return;
      const rows = [];
      fields.forEach((f) => (D.series[f] || []).forEach((p) =>
        rows.push({ t: new Date(p.t * 1000).toISOString(), v: p.v, field: f })));
      try { view.data("table", rows).resize().runAsync(); } catch (_) {}
    }
    return { el: root, fields, update };
  });

  function vegaTheme() {
    return document.documentElement.getAttribute("data-theme") === "light" ? "default" : "dark";
  }

  function buildTimeseriesSpec(fields, height) {
    return {
      $schema: "https://vega.github.io/schema/vega-lite/v5.json",
      data: { name: "table", values: [] },
      mark: { type: "line", interpolate: "monotone" },
      width: "container",
      height: height || 220,
      background: "transparent",
      encoding: {
        x: { field: "t", type: "temporal", title: null },
        y: { field: "v", type: "quantitative", title: null },
        color: { field: "field", type: "nominal", legend: fields.length > 1 ? {} : null },
      },
    };
  }

  register("html", function (panel) {
    const root = el("div", "panel");
    if (panel.title) root.appendChild(el("div", "panel-title", panel.title));
    root.appendChild(el("div", "", (panel.config && panel.config.html) || ""));
    return { el: root, update() {} };
  });

  function placeholder(panel, D, label) {
    const root = el("div", "panel");
    root.appendChild(el("div", "panel-title", panel.title || label));
    root.appendChild(el("div", "muted-note", label + " unavailable"));
    return { el: root, update() {} };
  }
  // scene3d.js replaces this with the real 3D renderer on load.
  if (!Registry.scene) register("scene", (p, D) => placeholder(p, D, "3D view"));

  // --- alarms --------------------------------------------------------------
  function alarmLevelFor(spec, field, value) {
    if (value == null || !spec) return null;
    let level = null;
    for (const rule of spec.alarms || []) {
      if (rule.field !== field) continue;
      const hit = rule.direction === "below" ? value < rule.threshold : value > rule.threshold;
      if (hit) level = rule.level === "crit" ? "crit" : (level || "warn");
    }
    return level;
  }

  // --- dashboard instance --------------------------------------------------
  // One twin's live dashboard, mounted into `container`. Owns its own data feed
  // (history seed + SSE stream) and panel set, so several can coexist (a member
  // drill-in replaces the previous one via destroy()).
  function createDashboard(container, opts) {
    const D = {
      apiBase: opts.apiBase || "",
      spec: opts.spec,
      caps: opts.caps || {},
      latest: {}, series: {}, alarms: { warn: [], crit: [] },
      panels: {}, maxPoints: 600, es: null,
      onConn: opts.onConn || function () {},
    };
    D.api = (p) => D.apiBase + p;
    D.fmt = fmt;
    D.alarmLevel = (field, value) => alarmLevelFor(D.spec, field, value);

    // The twin view is tabbed. Home foregrounds what matters at a glance — the
    // asset (3D), the live agent-chart canvas, and chat. Telemetry holds the KPIs,
    // charts, alarms, and events. Agents shows the multi-agent system in action.
    const root = el("div", "twin-view");
    const tabbar = el("div", "tabbar");
    root.appendChild(tabbar);
    const tabs = {}, panes = {};
    function addTab(id, label) {
      const b = el("button", "tab", label);
      b.onclick = () => showTab(id);
      tabbar.appendChild(b); tabs[id] = b;
      const pane = el("div", "tabpane"); pane.hidden = true;
      root.appendChild(pane); panes[id] = pane;
      return pane;
    }
    const homePane = addTab("home", "Home");
    const telePane = addTab("telemetry", "Telemetry");
    const agentsPane = addTab("agents", "Agents");
    container.appendChild(root);

    function showTab(id) {
      D.activeTab = id;
      Object.entries(tabs).forEach(([k, b]) => b.classList.toggle("active", k === id));
      Object.entries(panes).forEach(([k, p]) => { p.hidden = k !== id; });
      // Charts laid out while their pane was hidden measured 0 width; refresh so
      // they fill the now-visible pane.
      for (const p of Object.values(D.panels)) { try { p.update(); } catch (_) {} }
    }

    const allPanels = D.spec.panels || [];
    const scenePanel = allPanels.find((p) => p.kind === "scene");
    const telePanels = allPanels.filter((p) => p.kind !== "chat" && p.kind !== "scene");

    buildHomeTab(homePane, scenePanel, D);
    buildTelemetryTab(telePane, telePanels, D);
    D._agentsView = createAgentsView(agentsPane, D.apiBase);
    showTab("home");

    function recomputeAlarms() {
      const warn = [], crit = [];
      for (const f of Object.keys(D.latest)) {
        const lvl = D.alarmLevel(f, D.latest[f]);
        if (lvl === "crit") crit.push(f);
        else if (lvl === "warn") warn.push(f);
      }
      D.alarms = { warn, crit };
    }

    function ingestFrame(frame) {
      const payload = (frame && frame.payload) || {};
      const now = Date.now() / 1000;
      for (const [k, v] of Object.entries(payload)) {
        D.latest[k] = v;
        if (typeof v !== "number") continue;
        const buf = (D.series[k] = D.series[k] || []);
        buf.push({ t: now, v });
        if (buf.length > D.maxPoints) buf.shift();
      }
      recomputeAlarms();
      for (const p of Object.values(D.panels)) { try { p.update(frame); } catch (_) {} }
    }
    D.ingestFrame = ingestFrame;

    async function seedHistory() {
      try {
        const r = await fetch(D.api("/api/viz/history?minutes=120"), { headers: keyHeaders() });
        if (!r.ok) return;
        const data = await r.json();
        for (const [f, pts] of Object.entries(data)) {
          D.series[f] = pts.slice(-D.maxPoints);
          if (pts.length) D.latest[f] = pts[pts.length - 1].v;
        }
      } catch (_) {}
    }

    function connectStream() {
      function open() {
        const es = D.es = new EventSource(apiKeyed(D.api("/api/viz/stream")));
        es.onopen = () => D.onConn("live");
        es.onmessage = (m) => {
          if (!m.data || m.data.startsWith(":")) return;
          try { ingestFrame(JSON.parse(m.data)); } catch (_) {}
        };
        es.onerror = () => {
          D.onConn("down");
          es.close();
          if (!D._destroyed) D._reconnect = setTimeout(open, 3000);
        };
      }
      open();
    }

    D.destroy = function () {
      D._destroyed = true;
      if (D._reconnect) clearTimeout(D._reconnect);
      if (D.es) try { D.es.close(); } catch (_) {}
      for (const p of Object.values(D.panels)) { if (p.destroy) try { p.destroy(); } catch (_) {} }
      [D._canvas, D._agentsView].forEach((c) => { if (c && c.destroy) try { c.destroy(); } catch (_) {} });
      if (root.parentNode) root.parentNode.removeChild(root);
    };

    (async () => {
      recomputeAlarms();
      for (const p of Object.values(D.panels)) { try { p.update(); } catch (_) {} }
      await seedHistory();
      for (const p of Object.values(D.panels)) { try { p.update(); } catch (_) {} }
      connectStream();
    })();

    return D;
  }

  // --- twin-view tabs ------------------------------------------------------
  function buildHomeTab(pane, scenePanel, D) {
    const home = el("div", "home");
    const top = el("div", "home-top");
    home.appendChild(top);

    if (scenePanel) {
      const make = Registry[scenePanel.kind];
      let comp;
      try { comp = make ? make(scenePanel, D) : placeholder(scenePanel, D, "scene"); }
      catch (e) { comp = { el: el("div", "panel error", "scene error: " + e), update() {} }; }
      comp.el.classList.add("home-scene");
      top.appendChild(comp.el);
      D.panels[scenePanel.id] = comp;
    } else {
      top.classList.add("no-scene");
    }

    const canvasHost = el("div", "home-canvas");
    top.appendChild(canvasHost);
    D._canvas = createAgentCanvas(canvasHost, D);

    const chatHost = el("div", "home-chat");
    home.appendChild(chatHost);
    D._homeChat = createChat(
      chatHost, () => D.apiBase, () => D.spec.asset_name,
      () => !!D.spec.voice_enabled, true,
    );
    pane.appendChild(home);
  }

  function buildTelemetryTab(pane, panels, D) {
    const dash = el("div", "dash no-scene");
    const regions = {
      alarms: el("div", "region-alarms"),
      hero: el("div", "region-hero"),
      kpis: el("div", "region-kpis"),
      charts: el("div", "region-charts"),
      aside: el("div", "region-aside"),
    };
    [regions.alarms, regions.hero, regions.kpis, regions.charts, regions.aside]
      .forEach((r) => dash.appendChild(r));

    const firstChart = panels.find((p) => p.kind === "chart");
    if (firstChart) firstChart._hero = true;

    for (const panel of panels) {
      const make = Registry[panel.kind];
      let comp;
      try { comp = make ? make(panel, D) : placeholder(panel, D, panel.kind); }
      catch (e) { comp = { el: el("div", "panel error", "panel error: " + e), update() {} }; }
      let region = "charts";
      if (panel.kind === "alarms") region = "alarms";
      else if (panel.kind === "kpi") region = "kpis";
      else if (panel.kind === "chart") region = panel._hero ? "hero" : "charts";
      else if (panel.kind === "events" || panel.kind === "fsm") region = "aside";
      regions[region].appendChild(comp.el);
      D.panels[panel.id] = comp;
    }
    if (!regions.aside.children.length) dash.classList.add("no-aside");
    if (!panels.length) dash.appendChild(el("div", "muted-note", "No telemetry panels."));
    pane.appendChild(dash);
  }

  // The dedicated agent-chart canvas: every chart the agent draws for this twin
  // lands here, newest first, and live-renders via Vega — a persistent home for
  // the agent's visuals separate from the chat transcript.
  function createAgentCanvas(host, D) {
    const panel = el("div", "panel canvas-panel");
    panel.appendChild(el("div", "panel-title", "Agent charts · live"));
    const body = el("div", "canvas-body");
    const empty = el("div", "empty", "Charts the agent draws appear here, newest first. Ask it to plot or forecast something.");
    body.appendChild(empty);
    panel.appendChild(body);
    host.appendChild(panel);

    function add(spec) {
      if (empty.parentNode) empty.remove();
      const card = el("div", "canvas-chart");
      const cap = el("div", "canvas-cap", new Date().toLocaleTimeString());
      card.appendChild(cap);
      const chartHost = el("div", "canvas-chart-host");
      card.appendChild(chartHost);
      body.insertBefore(card, body.firstChild);
      const vl = spec.vega_lite || spec;
      if (vl && typeof vl === "object" && !vl.background) vl.background = "transparent";
      if (typeof vegaEmbed === "function") {
        vegaEmbed(chartHost, vl, { actions: false, theme: vegaTheme() })
          .catch((e) => { chartHost.innerHTML = '<div class="error">chart error: ' + e + "</div>"; });
      } else {
        chartHost.innerHTML = '<div class="error">charts unavailable — Vega failed to load</div>';
      }
      while (body.children.length > 6) body.removeChild(body.lastChild);
    }
    const unsub = AgentCharts.subscribe(D.apiBase, add);
    return { destroy() { unsub(); } };
  }

  // --- agent-chart pub/sub -------------------------------------------------
  // Chat emits each chart the agent draws (keyed by the twin's api base); the
  // matching twin's canvas receives it. Decouples the chat panel from the canvas.
  const AgentCharts = {
    subs: [],
    subscribe(apiBase, fn) {
      const s = { apiBase, fn };
      this.subs.push(s);
      return () => { this.subs = this.subs.filter((x) => x !== s); };
    },
    emit(apiBase, spec) {
      this.subs.forEach((s) => { if (s.apiBase === apiBase) { try { s.fn(spec); } catch (_) {} } });
    },
  };

  // --- agents (MAS) activity view ------------------------------------------
  // Polls <apiBase>/api/viz/agents and renders a live card per agent. Drives the
  // Agents tab of a single twin and, reused from the combined overview, the
  // composite overseer's own agents.
  function createAgentsView(pane, apiBase) {
    const base = (apiBase || "").replace(/\/$/, "");
    const wrap = el("div", "agents-view");
    const note = el("div", "muted-note", "Multi-agent system — live activity");
    wrap.appendChild(note);
    const grid = el("div", "agent-grid");
    wrap.appendChild(grid);
    pane.appendChild(wrap);

    async function refresh() {
      try {
        const r = await fetch(base + "/api/viz/agents", { headers: keyHeaders() });
        if (!r.ok) { grid.innerHTML = ""; grid.appendChild(el("div", "muted-note", "Agent activity unavailable for this twin.")); return; }
        const data = await r.json();
        if (!data.available || !(data.agents || []).length) {
          grid.innerHTML = "";
          grid.appendChild(el("div", "muted-note", "No multi-agent system is attached to this twin."));
          return;
        }
        note.textContent = "Multi-agent system — " + data.agents.length +
          " agents" + (data.monitor_interval ? " · " + data.monitor_interval + "s cycle" : "");
        renderAgentCards(grid, data.agents);
      } catch (_) { /* transient */ }
    }
    refresh();
    const timer = setInterval(refresh, 5000);
    return { destroy() { clearInterval(timer); } };
  }

  function renderAgentCards(grid, agents) {
    grid.innerHTML = "";
    agents.forEach((a) => {
      const sev = a.severity === "critical" ? "crit" : a.severity === "warning" ? "warn" : "";
      const card = el("div", "agent-card" + (a.anomaly ? " anomaly" : ""));
      const head = el("div", "ac-head");
      head.appendChild(el("span", "ac-name", a.agent_name));
      if (a.domain) head.appendChild(el("span", "ac-domain", a.domain));
      const badge = el("span", "badge " + (sev || "ok"), (a.severity || "info").toUpperCase());
      head.appendChild(badge);
      card.appendChild(head);

      card.appendChild(el("div", "ac-action", (a.anomaly ? "⚠ " : "") + "Action: " + (a.action || "monitoring")));
      if (a.summary) {
        const sum = el("div", "ac-summary");
        sum.innerHTML = renderMarkdown(a.summary);
        card.appendChild(sum);
      }
      const calls = a.tool_calls || [];
      if (calls.length) {
        const tc = el("div", "ac-tools");
        tc.appendChild(el("div", "ac-tools-h", calls.length + " tool call" + (calls.length > 1 ? "s" : "")));
        calls.forEach((c) => {
          const row = el("div", "ac-tool");
          row.appendChild(el("span", "tt", c.tool || "tool"));
          const io = el("span", "ti");
          const inp = c.input && Object.keys(c.input).length ? JSON.stringify(c.input) : "";
          io.textContent = (inp ? inp + " → " : "") + String(c.output || "").slice(0, 140);
          row.appendChild(io);
          tc.appendChild(row);
        });
        card.appendChild(tc);
      }
      if (a.error) card.appendChild(el("div", "ac-err", "error: " + a.error));
      if (a.ts_s) card.appendChild(el("div", "ac-ts", "updated " + new Date(a.ts_s * 1000).toLocaleTimeString()));
      grid.appendChild(card);
    });
  }

  // A lightweight live feed (no panels): used by the combined overview to show
  // each member's status/KPIs without mounting that member's whole dashboard.
  function lightStream(apiBase, onFrame, onConn) {
    let es, retry, dead = false;
    function open() {
      es = new EventSource(apiKeyed((apiBase || "") + "/api/viz/stream"));
      es.onopen = () => onConn && onConn("live");
      es.onmessage = (m) => {
        if (!m.data || m.data.startsWith(":")) return;
        try { onFrame(JSON.parse(m.data)); } catch (_) {}
      };
      es.onerror = () => {
        onConn && onConn("down");
        es.close();
        if (!dead) retry = setTimeout(open, 4000);
      };
    }
    open();
    return { close() { dead = true; if (retry) clearTimeout(retry); if (es) try { es.close(); } catch (_) {} } };
  }

  // --- chat dock -----------------------------------------------------------
  // One dock for the whole shell; its scope follows the active view (the top
  // twin in an overview, or the focused member). Posts to <activeApi>/api/chat.
  function createChat(dockEl, getApi, getScope, getVoice, embedded) {
    dockEl.innerHTML = "";
    if (embedded) dockEl.classList.add("chat-embed");
    const head = el("div", "chat-head");
    head.appendChild(el("div", "t", "Ask the Twin"));
    const scope = el("div", "scope");
    head.appendChild(scope);
    let closeBtn = null;
    if (!embedded) {
      closeBtn = el("button", "x", "✕");
      head.appendChild(closeBtn);
    }
    dockEl.appendChild(head);

    const chat = el("div", "chat");
    const log = el("div", "log");
    const empty = el("div", "empty", "Ask about status, history, or request a chart — e.g. “plot the last hour”.");
    log.appendChild(empty);
    chat.appendChild(log);
    const composer = el("div", "composer");
    const input = el("input");
    input.placeholder = "Ask about status, history, predictions…";
    const send = el("button", null, "Send");
    composer.appendChild(input);
    if (getVoice() && SpeechRec) voiceAttach(composer, input, () => submit());
    composer.appendChild(send);
    chat.appendChild(composer);
    dockEl.appendChild(chat);

    if (closeBtn) closeBtn.onclick = () => { dockEl.hidden = true; };

    function addMsg(role, text) {
      if (empty.parentNode) empty.remove();
      const m = el("div", "msg " + role);
      const span = el("span", "text");
      span.textContent = text || "";
      m.appendChild(span);
      m._text = span;
      m._buf = "";
      log.appendChild(m);
      log.scrollTop = log.scrollHeight;
      return m;
    }

    async function submit() {
      const q = input.value.trim();
      if (!q) return;
      input.value = "";
      addMsg("user", q);
      const bubble = addMsg("agent", "");
      try {
        const resp = await fetch(getApi() + "/api/chat", {
          method: "POST",
          headers: keyHeaders({ "Content-Type": "application/json" }),
          body: JSON.stringify({ message: q, stream: true }),
        });
        if (!resp.ok) {
          bubble._text.textContent = resp.status === 404
            ? "Chat is not available for this twin." : "Chat error (" + resp.status + ").";
          return;
        }
        const ctype = resp.headers.get("content-type") || "";
        if (!resp.body || !ctype.includes("text/event-stream")) {
          const data = await resp.json();
          handleChunk(bubble, data.response || "(no response)");
          return;
        }
        const reader = resp.body.getReader();
        const dec = new TextDecoder();
        let buf = "";
        while (true) {
          const { value, done } = await reader.read();
          if (done) break;
          buf += dec.decode(value, { stream: true });
          const lines = buf.split("\n");
          buf = lines.pop();
          for (const line of lines) {
            if (!line.startsWith("data:")) continue;
            const data = line.slice(5).trim();
            if (data === "[DONE]") continue;
            try {
              const obj = JSON.parse(data);
              if (obj.error) bubble._text.textContent += "\n[error] " + obj.error;
              else if (obj.chunk != null) handleChunk(bubble, obj.chunk);
            } catch (_) { handleChunk(bubble, data); }
            log.scrollTop = log.scrollHeight;
          }
        }
        if (getVoice()) voiceSpeak(bubble._text.textContent);
      } catch (e) {
        bubble._text.textContent = "Chat unavailable: " + e;
      }
    }

    // Pull complete inline chart blocks (from the agent's chart tool) out of the
    // stream and render each via Vega-Lite inside the message. Markers match
    // agent_tools.CHART_OPEN / CHART_CLOSE.
    const CHART_OPEN = "<<<DYON_CHART>>>";
    const CHART_CLOSE = "<<<END_CHART>>>";
    function handleChunk(bubble, chunk) {
      bubble._buf += chunk;
      let open;
      while ((open = bubble._buf.indexOf(CHART_OPEN)) !== -1) {
        const close = bubble._buf.indexOf(CHART_CLOSE, open);
        if (close === -1) break;
        const jsonStr = bubble._buf.slice(open + CHART_OPEN.length, close);
        bubble._buf = bubble._buf.slice(0, open) + bubble._buf.slice(close + CHART_CLOSE.length);
        try { renderInlineChart(bubble, JSON.parse(jsonStr)); } catch (_) {}
      }
      const partial = bubble._buf.indexOf(CHART_OPEN);
      bubble._text.innerHTML = renderMarkdown(partial === -1 ? bubble._buf : bubble._buf.slice(0, partial));
    }

    function renderInlineChart(bubble, spec) {
      // A chart needs real width: agent bubbles shrink to their text, which would
      // collapse a ``width:"container"`` chart to nothing. Flag the bubble so CSS
      // gives it (and the chart) full width.
      bubble.classList.add("has-chart");
      const host = el("div", "chart-inline");
      bubble.appendChild(host);
      const vl = spec.vega_lite || spec;
      if (vl && typeof vl === "object" && !vl.background) vl.background = "transparent";
      if (typeof vegaEmbed === "function") {
        vegaEmbed(host, vl, { actions: false, theme: vegaTheme() })
          .catch((e) => { host.innerHTML = '<div class="error">chart error: ' + e + "</div>"; });
      } else {
        host.innerHTML = '<div class="error">charts unavailable — Vega failed to load</div>';
      }
      // Mirror the chart onto this twin's dedicated agent canvas (Home tab).
      AgentCharts.emit(getApi(), spec);
      log.scrollTop = log.scrollHeight;
    }

    send.onclick = submit;
    input.addEventListener("keydown", (e) => { if (e.key === "Enter") submit(); });

    return { updateScope() { scope.textContent = getScope(); } };
  }

  // Minimal, dependency-free Markdown → HTML (escapes first, so no raw HTML can
  // be injected). Supports bold, italics, lists, headings, links, inline/fenced
  // code.
  function renderMarkdown(src) {
    if (src == null) return "";
    const esc = (s) => s.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;");
    const blocks = [];
    let text = String(src).replace(/```[ \t]*\w*\n?([\s\S]*?)```/g, (_, body) => {
      blocks.push("<pre><code>" + esc(body.replace(/\n+$/, "")) + "</code></pre>");
      return "  " + (blocks.length - 1) + " ";
    });
    const inline = (line) => {
      let s = esc(line);
      s = s.replace(/`([^`]+)`/g, "<code>$1</code>");
      s = s.replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>");
      s = s.replace(/(^|[^*])\*([^*\n]+)\*/g, "$1<em>$2</em>");
      s = s.replace(/\[([^\]]+)\]\((https?:[^)\s]+)\)/g,
        '<a href="$2" target="_blank" rel="noopener">$1</a>');
      return s;
    };
    const out = [];
    let list = null, para = [];
    const flushPara = () => { if (para.length) { out.push("<p>" + para.join("<br>") + "</p>"); para = []; } };
    const flushList = () => { if (list) { out.push("</" + list + ">"); list = null; } };
    // GitHub-flavoured table: split a `| a | b |` row into trimmed cells, and
    // recognise the `|---|:--:|` separator that turns the line above it into a
    // header — together they let the chat render any Markdown table.
    const splitRow = (s) =>
      s.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map((c) => c.trim());
    const isTableSep = (s) =>
      s.includes("|") && s.includes("-") && splitRow(s).every((c) => /^:?-+:?$/.test(c));
    const renderTable = (header, aligns, rows) => {
      const cell = (tag, txt, al) =>
        "<" + tag + (al ? ' style="text-align:' + al + '"' : "") + ">" + inline(txt) + "</" + tag + ">";
      const head = "<tr>" + header.map((h, i) => cell("th", h, aligns[i])).join("") + "</tr>";
      const body = rows.map((r) =>
        "<tr>" + header.map((_, i) => cell("td", r[i] || "", aligns[i])).join("") + "</tr>").join("");
      return "<table><thead>" + head + "</thead><tbody>" + body + "</tbody></table>";
    };
    const lines = text.split("\n");
    for (let i = 0; i < lines.length; i++) {
      const line = lines[i].replace(/\s+$/, "");
      const ph = line.match(/^  (\d+) $/);
      if (ph) { flushPara(); flushList(); out.push(blocks[+ph[1]]); continue; }
      if (!line.trim()) { flushPara(); flushList(); continue; }
      if (line.includes("|") && i + 1 < lines.length && isTableSep(lines[i + 1])) {
        flushPara(); flushList();
        const header = splitRow(line);
        const aligns = splitRow(lines[i + 1]).map((c) => {
          const l = c.startsWith(":"), r = c.endsWith(":");
          return l && r ? "center" : r ? "right" : l ? "left" : "";
        });
        i += 1;
        const rows = [];
        while (i + 1 < lines.length && lines[i + 1].includes("|") && lines[i + 1].trim()) {
          rows.push(splitRow(lines[++i]));
        }
        out.push(renderTable(header, aligns, rows));
        continue;
      }
      let m;
      if ((m = line.match(/^(#{1,6})\s+(.*)$/))) {
        flushPara(); flushList();
        const n = m[1].length;
        out.push("<h" + n + ">" + inline(m[2]) + "</h" + n + ">");
      } else if ((m = line.match(/^\s*[-*]\s+(.*)$/))) {
        flushPara();
        if (list !== "ul") { flushList(); out.push("<ul>"); list = "ul"; }
        out.push("<li>" + inline(m[1]) + "</li>");
      } else if ((m = line.match(/^\s*\d+\.\s+(.*)$/))) {
        flushPara();
        if (list !== "ol") { flushList(); out.push("<ol>"); list = "ol"; }
        out.push("<li>" + inline(m[1]) + "</li>");
      } else { flushList(); para.push(inline(line)); }
    }
    flushPara(); flushList();
    return out.join("");
  }

  // --- voice (browser Web Speech API; zero dependency) ---------------------
  const SpeechRec = window.SpeechRecognition || window.webkitSpeechRecognition;
  function voiceSpeak(text) {
    if (!text || !window.speechSynthesis) return;
    try {
      window.speechSynthesis.cancel();
      window.speechSynthesis.speak(new SpeechSynthesisUtterance(text));
    } catch (_) {}
  }
  function voiceAttach(composer, input, onSubmit) {
    const mic = el("button", "mic", "🎤");
    mic.title = "Dictate";
    composer.appendChild(mic);
    const rec = new SpeechRec();
    rec.continuous = false; rec.interimResults = false;
    let recording = false;
    rec.onresult = (e) => { input.value = e.results[0][0].transcript; onSubmit(); };
    rec.onend = () => { recording = false; mic.classList.remove("recording"); };
    mic.onclick = () => {
      if (recording) { rec.stop(); return; }
      try { rec.start(); recording = true; mic.classList.add("recording"); } catch (_) {}
    };
  }

  // --- shell + routing -----------------------------------------------------
  const Shell = {
    spec: null, caps: {}, current: null, chat: null,
    activeApi: ROOT_API, activeLabel: "",
    navItems: {},   // viewId -> nav element
  };

  function setConn(status) {
    const c = document.querySelector(".topbar .conn");
    if (!c) return;
    c.classList.remove("live", "down");
    if (status) c.classList.add(status);
    c.querySelector(".label").textContent =
      status === "live" ? "Live" : status === "down" ? "Disconnected" : "Connecting…";
  }

  function buildShell(app, spec, caps) {
    Shell.spec = spec; Shell.caps = caps;
    const combined = spec.combination && spec.combination !== "single";
    const members = spec.members || [];

    // Sidebar
    const sidebar = app.querySelector(".sidebar");
    sidebar.innerHTML = "";
    const brand = el("div", "brand");
    brand.appendChild(el("div", "mark", "D"));
    const who = el("div", "who");
    who.appendChild(el("div", "name", spec.asset_name));
    who.appendChild(el("div", "kind", combined ? spec.combination + " twin" : spec.asset_type));
    brand.appendChild(who);
    sidebar.appendChild(brand);

    if (combined && members.length) {
      sidebar.appendChild(el("div", "nav-section", "Twins"));
      const list = el("div", "nav-list");
      const ov = navItem("overview", "Overview", "◎");
      list.appendChild(ov);
      members.forEach((mem) => {
        const it = navItem("member:" + mem.id, mem.name, "");
        const st = el("span", "st"); it.appendChild(st); it._st = st;
        list.appendChild(it);
      });
      sidebar.appendChild(list);
    } else {
      sidebar.appendChild(el("div", "nav-section", "View"));
      const list = el("div", "nav-list");
      list.appendChild(navItem("single", "Dashboard", "◎"));
      sidebar.appendChild(list);
      setActiveNav("single");
    }

    const foot = el("div", "sidebar-foot");
    const tt = el("button", "theme-toggle");
    const setTtLabel = () => { tt.innerHTML = currentMode() === "dark" ? "☀ Light" : "🌙 Dark"; };
    setTtLabel();
    tt.onclick = () => { toggleMode(); setTtLabel(); rerenderForTheme(); };
    foot.appendChild(tt);
    sidebar.appendChild(foot);

    // Topbar
    const topbar = app.querySelector(".topbar");
    topbar.innerHTML = "";
    topbar.appendChild(el("h1", null, spec.asset_name));
    topbar.appendChild(el("span", "sub", combined ? spec.combination + " twin" : spec.asset_type));
    topbar.appendChild(el("div", "spacer"));
    const conn = el("div", "conn");
    conn.appendChild(el("span", "dot"));
    conn.appendChild(el("span", "label", "Connecting…"));
    topbar.appendChild(conn);

    // Chat dock + fab
    const dock = app.querySelector(".chat-dock");
    const fab = el("button", "chat-fab", "💬");
    fab.title = "Ask the Twin";
    document.body.appendChild(fab);
    fab.onclick = () => { dock.hidden = !dock.hidden; if (!dock.hidden && Shell.chat) Shell.chat.updateScope(); };
    if (!(spec.chat_enabled === false)) {
      Shell.chat = createChat(dock, () => Shell.activeApi, () => Shell.activeLabel,
        () => !!Shell.spec.voice_enabled);
    } else {
      fab.style.display = "none";
    }

    document.querySelector(".app").removeAttribute("data-booting");
    document.title = "Dyon · " + spec.asset_name;

    // Initial view
    if (combined && members.length) selectView("overview");
    else mountSingle();
  }

  function navItem(id, label, icon) {
    const it = el("div", "nav-item");
    it.appendChild(el("span", "ic", icon || "●"));
    it.appendChild(el("span", "lbl", label));
    it.onclick = () => selectView(id);
    Shell.navItems[id] = it;
    return it;
  }

  function setActiveNav(id) {
    Object.entries(Shell.navItems).forEach(([k, n]) => n.classList.toggle("active", k === id));
  }

  function clearContent() {
    if (Shell.current && Shell.current.destroy) { try { Shell.current.destroy(); } catch (_) {} }
    Shell.current = null;
    const content = document.querySelector(".content");
    content.innerHTML = "";
    return content;
  }

  function mountSingle() {
    const content = clearContent();
    Shell.activeApi = ROOT_API;
    Shell.activeLabel = Shell.spec.asset_name;
    Shell.current = createDashboard(content, {
      apiBase: ROOT_API, spec: Shell.spec, caps: Shell.caps, onConn: setConn,
    });
    if (Shell.chat) Shell.chat.updateScope();
  }

  function selectView(id) {
    setActiveNav(id);
    if (id === "single") return mountSingle();
    if (id === "overview") return mountOverview();
    if (id.startsWith("member:")) {
      const mem = (Shell.spec.members || []).find((m) => "member:" + m.id === id);
      if (mem) return mountMember(mem);
    }
  }

  function mountOverview() {
    const content = clearContent();
    Shell.activeApi = ROOT_API;
    Shell.activeLabel = Shell.spec.asset_name + " (overview)";
    if (Shell.chat) Shell.chat.updateScope();
    const renderer = DyonDash._overviewRenderer;
    if (!renderer) {
      content.appendChild(el("div", "muted-note", "Combined overview renderer not loaded."));
      Shell.current = null;
      return;
    }
    Shell.current = renderer(content, {
      spec: Shell.spec, caps: Shell.caps, rootApi: ROOT_API,
      openMember: (memId) => selectView("member:" + memId),
      setMemberStatus: (memId, status) => {
        const it = Shell.navItems["member:" + memId];
        if (it && it._st) { it._st.className = "st " + (status || ""); }
      },
    });
  }

  async function mountMember(mem) {
    const content = clearContent();
    Shell.activeApi = (mem.api_base || "").replace(/\/$/, "");
    Shell.activeLabel = mem.name;
    if (Shell.chat) Shell.chat.updateScope();
    content.appendChild(el("div", "muted-note", "Loading " + mem.name + "…"));
    let memberSpec, caps = {};
    try {
      const [s, c] = await Promise.all([
        fetch(Shell.activeApi + "/api/viz/spec", { headers: keyHeaders() }),
        fetch(Shell.activeApi + "/api/viz/capabilities", { headers: keyHeaders() }).catch(() => null),
      ]);
      memberSpec = await s.json();
      caps = c && c.ok ? await c.json() : {};
    } catch (e) {
      content.innerHTML = "";
      content.appendChild(el("div", "panel error",
        "Could not reach " + mem.name + " at " + Shell.activeApi + ": " + e));
      return;
    }
    content.innerHTML = "";
    Shell.current = createDashboard(content, {
      apiBase: Shell.activeApi, spec: memberSpec, caps,
      onConn: (st) => { const it = Shell.navItems["member:" + mem.id]; if (it && it._st) it._st.className = "st " + (st || ""); },
    });
  }

  function rerenderForTheme() {
    // Re-mount the current view so Vega charts pick up the new theme palette.
    const id = Object.entries(Shell.navItems).find(([, n]) => n.classList.contains("active"));
    if (id) selectView(id[0]);
    else mountSingle();
  }

  // --- boot ----------------------------------------------------------------
  async function boot() {
    applyMode(currentMode());
    const app = document.querySelector(".app");
    let spec, caps;
    try {
      const [specRes, capRes] = await Promise.all([
        fetch(ROOT_API + "/api/viz/spec", { headers: keyHeaders() }),
        fetch(ROOT_API + "/api/viz/capabilities", { headers: keyHeaders() }).catch(() => null),
      ]);
      spec = await specRes.json();
      caps = capRes && capRes.ok ? await capRes.json() : {};
    } catch (e) {
      const content = document.querySelector(".content");
      content.innerHTML = "";
      content.appendChild(el("div", "panel error",
        "Could not load dashboard spec from " + (ROOT_API || "this origin") + ": " + e));
      return;
    }
    applyTheme(spec.theme);
    buildShell(app, spec, caps);
  }

  window.DyonDash = {
    register, createDashboard, createAgentsView, lightStream, boot,
    el, fmt, applyTheme, buildTimeseriesSpec, renderMarkdown, vegaTheme,
    setOverviewRenderer(fn) { this._overviewRenderer = fn; },
    _overviewRenderer: null,
  };

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", boot);
  } else {
    boot();
  }
})();
