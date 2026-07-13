/* Dyon combined-twin overviews.
 *
 * Registers the overview renderer for a combined dashboard (DashboardSpec.
 * combination !== "single"), giving each collection type its own purpose-built
 * summary while reusing the single-twin renderer for member drill-ins:
 *
 *   aggregate  → fleet roll-up (merged mean/min/max KPIs + avg health)
 *   collection → peer comparison (health ranking + statistical outliers)
 *   composite  → hierarchy tree + boundary-condition flow diagram
 *   network    → typed relationship graph (health-coloured node-link)
 *
 * Every type also shows a member grid; clicking a member (here or in the
 * sidebar) opens that twin's own full dashboard (its KPIs, charts and 3D scene)
 * federated from its api_base. No-op for a single twin.
 */
(function () {
  "use strict";
  if (!window.DyonDash) return;
  const { el, lightStream, createAgentsView } = window.DyonDash;
  const SVGNS = "http://www.w3.org/2000/svg";

  function svg(tag, attrs) {
    const e = document.createElementNS(SVGNS, tag);
    for (const k in (attrs || {})) e.setAttribute(k, attrs[k]);
    return e;
  }

  function levelFromAlarms(alarms, latest) {
    let lvl = null;
    for (const f in latest) {
      const v = latest[f];
      if (typeof v !== "number") continue;
      for (const r of alarms || []) {
        if (r.field !== f) continue;
        const hit = r.direction === "below" ? v < r.threshold : v > r.threshold;
        if (hit) lvl = r.level === "crit" ? "crit" : (lvl || "warn");
      }
    }
    return lvl;
  }

  // A live, panel-less monitor for one member: pulls its spec (labels, KPI
  // fields, alarm rules) once, then streams its telemetry to derive a status and
  // a small KPI snapshot. Subscribers (cards, topology nodes, sidebar dots) are
  // notified on every change.
  function createMonitor(member) {
    const mon = {
      member, latest: {}, alarms: [], kpis: [], status: "", name: member.name,
      subs: [],
    };
    mon.on = (fn) => { mon.subs.push(fn); fn(mon); };
    const emit = () => mon.subs.forEach((fn) => { try { fn(mon); } catch (_) {} });

    fetch((member.api_base || "") + "/api/viz/spec")
      .then((r) => r.json())
      .then((s) => {
        mon.alarms = s.alarms || [];
        mon.name = s.asset_name || member.name;
        mon.kpis = (s.panels || [])
          .filter((p) => p.kind === "kpi" && p.config && p.config.binding)
          .slice(0, 3)
          .map((p) => ({
            field: p.config.binding.field,
            label: p.config.binding.label || p.config.binding.field,
            unit: p.config.binding.unit || "",
            format: p.config.format || "{:.1f}",
          }));
        emit();
      })
      .catch(() => {});

    const feed = lightStream(member.api_base, (frame) => {
      const payload = (frame && frame.payload) || {};
      for (const k in payload) mon.latest[k] = payload[k];
      const lvl = levelFromAlarms(mon.alarms, mon.latest);
      mon.status = lvl || "live";
      emit();
    }, (conn) => {
      if (conn === "down") { mon.status = "down"; emit(); }
    });

    mon.destroy = () => feed.close();
    return mon;
  }

  const fmtVal = (v, format) => {
    if (v == null) return "—";
    if (typeof v !== "number") return String(v);
    const m = (format || "{:.1f}").match(/\{:\.(\d+)f\}/);
    return m ? v.toFixed(+m[1]) : String(v);
  };
  const dotClass = (status) => (status === "crit" ? "down" : status || "");
  const healthOf = (status) =>
    status === "crit" ? 32 : status === "warn" ? 70 : status === "down" ? 0 : 100;
  const barClass = (status) =>
    status === "crit" || status === "down" ? "crit" : status === "warn" ? "warn" : "";

  // ── member grid (shown for every combination) ────────────────────────────
  function memberGrid(spec, monitors, openMember) {
    const sec = el("div", "");
    sec.appendChild(el("div", "nav-section", "Members"));
    const grid = el("div", "member-grid");
    sec.appendChild(grid);

    spec.members.forEach((m) => {
      const mon = monitors[m.id];
      const card = el("div", "member-card");
      const head = el("div", "mc-head");
      const dot = el("span", "mc-dot");
      head.appendChild(dot);
      const who = el("div", "");
      who.appendChild(el("div", "mc-name", m.name));
      who.appendChild(el("div", "mc-type", m.asset_type || "twin"));
      head.appendChild(who);
      const badge = el("span", "");
      head.appendChild(badge);
      card.appendChild(head);

      const bar = el("div", "mc-bar");
      const fill = el("i"); bar.appendChild(fill);
      card.appendChild(bar);

      const kpis = el("div", "mc-kpis");
      card.appendChild(kpis);
      card.appendChild(el("div", "mc-open", "Open dashboard →"));
      card.onclick = () => openMember(m.id);
      grid.appendChild(card);

      mon.on(() => {
        dot.className = "mc-dot " + dotClass(mon.status);
        fill.className = barClass(mon.status);
        fill.style.width = healthOf(mon.status) + "%";
        badge.className = mon.status === "crit" ? "badge crit"
          : mon.status === "down" ? "badge crit" : "";
        badge.textContent = mon.status === "crit" ? "CRIT"
          : mon.status === "down" ? "OFFLINE" : "";
        kpis.innerHTML = "";
        mon.kpis.forEach((k) => {
          const cell = el("div", "mc-kpi");
          cell.appendChild(el("div", "k", fmtVal(mon.latest[k.field], k.format) +
            (k.unit ? " " + k.unit : "")));
          cell.appendChild(el("div", "l", k.label));
          kpis.appendChild(cell);
        });
      });
    });
    return sec;
  }

  // ── aggregate: merged roll-up KPIs across all members ────────────────────
  function rollup(spec, monitors) {
    const sec = el("div", "");
    sec.appendChild(el("div", "nav-section", "Fleet roll-up"));
    const stats = el("div", "ov-stats");
    sec.appendChild(stats);
    const mons = Object.values(monitors);

    function recompute() {
      // fields common to every member that has reported, numeric only
      const perField = {};
      mons.forEach((mon) => mon.kpis.forEach((k) => {
        const v = mon.latest[k.field];
        if (typeof v !== "number") return;
        (perField[k.field] = perField[k.field] || { label: k.label, unit: k.unit, vals: [] }).vals.push(v);
      }));
      stats.innerHTML = "";
      const active = mons.filter((m) => m.status && m.status !== "down").length;
      stats.appendChild(statTile("Active members", active + " / " + mons.length, ""));
      const avgH = mons.length
        ? Math.round(mons.reduce((s, m) => s + healthOf(m.status), 0) / mons.length) : 0;
      stats.appendChild(statTile("Avg health", avgH + "%", avgH < 50 ? "crit" : avgH < 80 ? "warn" : ""));
      Object.entries(perField).slice(0, 6).forEach(([, info]) => {
        if (!info.vals.length) return;
        const mean = info.vals.reduce((a, b) => a + b, 0) / info.vals.length;
        const lo = Math.min(...info.vals), hi = Math.max(...info.vals);
        stats.appendChild(statTile(info.label + (info.unit ? " (" + info.unit + ")" : ""),
          mean.toFixed(1), "", lo.toFixed(1) + " – " + hi.toFixed(1)));
      });
    }
    mons.forEach((m) => m.on(recompute));
    return sec;
  }

  function statTile(label, value, level, foot) {
    const t = el("div", "panel kpi" + (level ? " " + level : ""));
    t.appendChild(el("div", "panel-title", label));
    t.appendChild(el("div", "kpi-value", value));
    if (foot) t.appendChild(el("div", "kpi-foot", "range " + foot));
    return t;
  }

  // ── collection: health ranking + statistical outliers ────────────────────
  function ranking(spec, monitors, openMember) {
    const sec = el("div", "");
    sec.appendChild(el("div", "nav-section", "Peer comparison"));
    const panel = el("div", "panel");
    panel.appendChild(el("div", "panel-title", "Members ranked by health (worst first)"));
    const list = el("div", "events");
    const log = el("div", "log");
    panel.appendChild(log);
    sec.appendChild(panel);
    const mons = Object.values(monitors);

    function recompute() {
      // outliers: z-score on the first KPI field shared widely
      const fieldCounts = {};
      mons.forEach((m) => m.kpis.forEach((k) => { fieldCounts[k.field] = (fieldCounts[k.field] || 0) + 1; }));
      const pick = Object.entries(fieldCounts).sort((a, b) => b[1] - a[1])[0];
      const outliers = new Set();
      if (pick) {
        const vals = mons.map((m) => m.latest[pick[0]]).filter((v) => typeof v === "number");
        if (vals.length >= 3) {
          const mean = vals.reduce((a, b) => a + b, 0) / vals.length;
          const sd = Math.sqrt(vals.reduce((s, v) => s + (v - mean) ** 2, 0) / vals.length);
          if (sd > 0) mons.forEach((m) => {
            const v = m.latest[pick[0]];
            if (typeof v === "number" && Math.abs(v - mean) / sd > 2) outliers.add(m.member.id);
          });
        }
      }
      const ranked = mons.slice().sort((a, b) => healthOf(a.status) - healthOf(b.status));
      log.innerHTML = "";
      ranked.forEach((m) => {
        const row = el("div", "row");
        row.style.cursor = "pointer";
        row.onclick = () => openMember(m.member.id);
        const dot = el("span", "st " + dotClass(m.status));
        dot.style.cssText = "width:9px;height:9px;border-radius:50%;display:inline-block;";
        dot.style.background = m.status === "crit" || m.status === "down" ? "var(--crit)"
          : m.status === "warn" ? "var(--warn)" : "var(--ok)";
        row.appendChild(dot);
        row.appendChild(el("span", "type", m.name));
        const right = el("span", "ts");
        right.textContent = healthOf(m.status) + "%" +
          (outliers.has(m.member.id) ? "  · outlier" : "");
        right.style.marginLeft = "auto";
        if (outliers.has(m.member.id)) right.style.color = "var(--warn)";
        row.appendChild(right);
        log.appendChild(row);
      });
    }
    mons.forEach((m) => m.on(recompute));
    return sec;
  }

  // ── composite / network: topology diagram ────────────────────────────────
  function topology(spec, monitors, openMember) {
    const sec = el("div", "");
    const isNet = spec.combination === "network";
    sec.appendChild(el("div", "nav-section", isNet ? "Network topology" : "Composition & flow"));
    const panel = el("div", "panel topology");
    sec.appendChild(panel);

    const members = spec.members;
    const W = 860, NODE_W = 132, NODE_H = 46;
    const pos = isNet ? circleLayout(members, W) : layeredLayout(members, spec.hierarchy, W);
    const H = Math.max(...Object.values(pos).map((p) => p.y)) + NODE_H + 30;

    const root = svg("svg", { viewBox: "0 0 " + W + " " + H, preserveAspectRatio: "xMidYMin meet" });
    const defs = svg("defs");
    const marker = svg("marker", {
      id: "arrow", viewBox: "0 0 10 10", refX: "9", refY: "5",
      markerWidth: "7", markerHeight: "7", orient: "auto-start-reverse",
    });
    const mpath = svg("path", { d: "M0,0 L10,5 L0,10 z", fill: "currentColor" });
    marker.appendChild(mpath); defs.appendChild(marker); root.appendChild(defs);

    // structural edges (composite hierarchy) drawn first, beneath nodes
    if (!isNet) {
      Object.entries(spec.hierarchy || {}).forEach(([parent, kids]) =>
        (kids || []).forEach((c) => {
          if (pos[parent] && pos[c]) root.appendChild(edgePath(pos[parent], pos[c], NODE_H, "structural"));
        }));
    }
    // declared edges (flows + relationships)
    (spec.edges || []).forEach((e) => {
      if (pos[e.source] && pos[e.target]) {
        root.appendChild(edgePath(pos[e.source], pos[e.target], NODE_H, e.kind));
        if (e.label) root.appendChild(edgeLabel(pos[e.source], pos[e.target], e.label));
      }
    });

    members.forEach((m) => {
      const p = pos[m.id];
      const g = svg("g", { class: "topo-node", transform: "translate(" + (p.x - NODE_W / 2) + "," + p.y + ")" });
      g.appendChild(svg("rect", { width: NODE_W, height: NODE_H, rx: "9" }));
      const name = svg("text", { x: NODE_W / 2, y: 20, "text-anchor": "middle" });
      name.textContent = m.name.length > 16 ? m.name.slice(0, 15) + "…" : m.name;
      g.appendChild(name);
      const sub = svg("text", { x: NODE_W / 2, y: 36, "text-anchor": "middle", class: "sub" });
      sub.textContent = m.asset_type || "twin";
      g.appendChild(sub);
      g.style.cursor = "pointer";
      g.onclick = () => openMember(m.id);
      root.appendChild(g);
      monitors[m.id].on((mon) => {
        g.setAttribute("class", "topo-node " +
          (mon.status === "crit" || mon.status === "down" ? "crit"
            : mon.status === "warn" ? "warn" : mon.status === "live" ? "ok" : ""));
      });
    });

    panel.appendChild(root);
    const legend = el("div", "topo-legend");
    legend.innerHTML = isNet
      ? '<span><i class="rel"></i> relationship</span><span><i></i> flow</span>'
      : '<span><i></i> boundary flow</span><span>border = live health</span>';
    panel.appendChild(legend);
    return sec;
  }

  function layeredLayout(members, hierarchy, W) {
    const childSet = new Set();
    Object.values(hierarchy || {}).forEach((cs) => (cs || []).forEach((c) => childSet.add(c)));
    const ids = members.map((m) => m.id);
    const roots = ids.filter((id) => !childSet.has(id));
    const layerOf = {};
    let frontier = roots.length ? roots : ids.slice();
    let depth = 0; const seen = new Set();
    while (frontier.length) {
      const next = [];
      frontier.forEach((id) => {
        if (seen.has(id)) return;
        seen.add(id); layerOf[id] = depth;
        ((hierarchy || {})[id] || []).forEach((c) => { if (!seen.has(c)) next.push(c); });
      });
      depth++; frontier = next;
    }
    ids.forEach((id) => { if (!(id in layerOf)) layerOf[id] = depth; });
    const byLayer = {};
    ids.forEach((id) => (byLayer[layerOf[id]] = byLayer[layerOf[id]] || []).push(id));
    const pos = {};
    Object.entries(byLayer).forEach(([layer, list]) => {
      list.forEach((id, i) => {
        pos[id] = { x: ((i + 1) / (list.length + 1)) * W, y: 24 + +layer * 96 };
      });
    });
    return pos;
  }

  function circleLayout(members, W) {
    const ids = members.map((m) => m.id);
    const n = ids.length;
    const cx = W / 2, cy = 200, r = Math.min(W, 460) / 2 - 80;
    const pos = {};
    ids.forEach((id, i) => {
      const a = (i / Math.max(1, n)) * Math.PI * 2 - Math.PI / 2;
      pos[id] = { x: cx + r * Math.cos(a), y: cy + r * Math.sin(a) };
    });
    return pos;
  }

  function edgePath(a, b, nodeH, kind) {
    const cls = kind === "relationship" ? "relationship" : kind === "flow" ? "flow" : "";
    const p = svg("path", {
      class: "topo-edge " + cls,
      d: "M" + a.x + "," + (a.y + nodeH / 2) + " L" + b.x + "," + (b.y + nodeH / 2),
    });
    return p;
  }
  function edgeLabel(a, b, text) {
    const t = svg("text", {
      class: "topo-edge-label", x: (a.x + b.x) / 2, y: (a.y + b.y) / 2 + 20, "text-anchor": "middle",
    });
    t.textContent = text;
    return t;
  }

  // ── overview entry point ─────────────────────────────────────────────────
  // The combined view is itself tabbed: an **Overview** (composition/roll-up +
  // members) and — when the combined twin is a real overseer with its own agents
  // — an **Agents** tab showing that composite's multi-agent system in action,
  // exactly as a single twin's dashboard does. The Agents tab is added only once
  // the composite's own /api/viz/agents reports a system, so a pure federation
  // (no overseer process) shows just the Overview.
  function renderOverview(content, opts) {
    const { spec, openMember, rootApi } = opts;
    const view = el("div", "twin-view");
    const tabbar = el("div", "tabbar");
    view.appendChild(tabbar);
    content.appendChild(view);

    const tabs = {}, panes = {};
    function addTab(id, label) {
      const b = el("button", "tab", label);
      b.onclick = () => showTab(id);
      tabbar.appendChild(b); tabs[id] = b;
      const pane = el("div", "tabpane"); pane.hidden = true;
      view.appendChild(pane); panes[id] = pane;
      return pane;
    }
    function showTab(id) {
      Object.entries(tabs).forEach(([k, b]) => b.classList.toggle("active", k === id));
      Object.entries(panes).forEach(([k, p]) => { p.hidden = k !== id; });
    }

    const ovPane = addTab("overview", "Overview");
    const wrap = el("div", "overview");
    ovPane.appendChild(wrap);

    const monitors = {};
    (spec.members || []).forEach((m) => { monitors[m.id] = createMonitor(m); });
    // bridge member status to the sidebar dots
    Object.values(monitors).forEach((mon) =>
      mon.on(() => opts.setMemberStatus(mon.member.id, mon.status === "crit" ? "crit"
        : mon.status === "warn" ? "warn" : mon.status === "down" ? "down" : "live")));

    const header = el("div", "panel");
    header.appendChild(el("div", "panel-title", spec.combination + " twin"));
    header.appendChild(el("div", "muted-note",
      (spec.members || []).length + " constituent twins · select one to drill into its full dashboard."));
    wrap.appendChild(header);

    if (spec.combination === "aggregate") wrap.appendChild(rollup(spec, monitors));
    else if (spec.combination === "collection") wrap.appendChild(ranking(spec, monitors, openMember));
    else if (spec.combination === "composite" || spec.combination === "network")
      wrap.appendChild(topology(spec, monitors, openMember));

    wrap.appendChild(memberGrid(spec, monitors, openMember));
    showTab("overview");

    // Probe the combined twin's own agents; add the Agents tab only if present.
    let agentsView = null;
    const base = (rootApi || "").replace(/\/$/, "");
    fetch(base + "/api/viz/agents")
      .then((r) => (r.ok ? r.json() : null))
      .then((d) => {
        if (d && d.available && (d.agents || []).length && createAgentsView) {
          const ap = addTab("agents", "Agents");
          agentsView = createAgentsView(ap, rootApi);
        }
      })
      .catch(() => {});

    return {
      destroy() {
        Object.values(monitors).forEach((m) => m.destroy());
        if (agentsView && agentsView.destroy) try { agentsView.destroy(); } catch (_) {}
        if (view.parentNode) view.parentNode.removeChild(view);
      },
    };
  }

  window.DyonDash.setOverviewRenderer(renderOverview);
})();
