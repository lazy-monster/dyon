/* Dyon 3D scene panel.
 *
 * Capability-gated: renders a GLB via <model-viewer> only where there is WebGL2
 * and enough compute; otherwise falls back to SceneSpec.fallback_svg (a 2D
 * schematic with the same field overlays). Either way the live visual cues are
 * the same: a hotspot per bound sensor field shows its current value and turns
 * warn/crit colour as thresholds are crossed.
 *
 * Loaded after dyon-dash.js; registers the "scene" renderer (panel, D), where D
 * is the owning dashboard instance — so a member's 3D scene reads that member's
 * own live feed and resolves model URLs against its own api_base.
 */
(function () {
  "use strict";
  if (!window.DyonDash) return;
  const { register, el } = window.DyonDash;

  // Testing override: "?scene=2d" forces the 2D fallback even on capable
  // hardware (and "?scene=3d" attempts 3D regardless of the perf heuristic),
  // so both branches can be compared on one machine without toggling WebGL.
  function sceneOverride() {
    try { return new URLSearchParams(window.location.search).get("scene"); }
    catch (_) { return null; }
  }

  function canRender3D() {
    const forced = sceneOverride();
    if (forced === "2d") return false;
    try {
      const canvas = document.createElement("canvas");
      const gl = canvas.getContext("webgl2");
      if (!gl) return false;
      if (forced === "3d") return true;
      const cores = navigator.hardwareConcurrency || 2;
      const lowMem = navigator.deviceMemory && navigator.deviceMemory < 2;
      return cores >= 2 && !lowMem;
    } catch (_) { return false; }
  }

  let mvLoaded = false;
  function loadModelViewer() {
    if (mvLoaded) return;
    mvLoaded = true;
    const s = document.createElement("script");
    s.type = "module";
    s.src = "https://cdn.jsdelivr.net/npm/@google/model-viewer@3.5.0/dist/model-viewer.min.js";
    document.head.appendChild(s);
  }

  function hotspotLevel(h, value) {
    if (value == null) return "";
    if (h.crit != null && (h.direction === "below" ? value < h.crit : value > h.crit)) return "crit";
    if (h.warn != null && (h.direction === "below" ? value < h.warn : value > h.warn)) return "warn";
    return "";
  }

  function resolveUrl(D, url) {
    if (/^https?:\/\//.test(url)) return url;        // absolute (CDN/object store)
    return D.api(url.startsWith("/") ? url : "/" + url);
  }

  register("scene", function (panel, D) {
    const cfg = panel.config || {};
    const root = el("div", "panel scene span-hero");
    root.appendChild(el("div", "panel-title", panel.title || "Asset (3D)"));
    const host = el("div", "scene-host");
    root.appendChild(host);

    const allHotspots = cfg.hotspots || [];
    // A 3D hotspot must be anchored in model space; a field with no position
    // would otherwise pile up at the origin on top of every other unplaced one.
    // So the 3D branch shows only positioned hotspots (the curated few), while
    // the 2D fallback shows whichever fields its schematic actually binds.
    const hotspots = allHotspots.filter((h) => h.position);
    const use3D = cfg.model_url && canRender3D();
    const overlays = {};   // field -> overlay element
    let modelViewer = null;   // set on the 3D path; drives tint + stage swap
    let currentStage = null;

    if (use3D) {
      loadModelViewer();
      const mv = modelViewer = document.createElement("model-viewer");
      mv.setAttribute("src", resolveUrl(D, cfg.model_url));
      if (cfg.poster) mv.setAttribute("poster", cfg.poster);
      mv.setAttribute("camera-controls", "");
      mv.setAttribute("auto-rotate", "");
      mv.setAttribute("shadow-intensity", "1");
      mv.setAttribute("exposure", "1");
      mv.setAttribute("ar", "");
      hotspots.forEach((h, i) => {
        const slot = el("button", "hotspot");
        slot.setAttribute("slot", "hotspot-" + i);
        if (h.position) slot.setAttribute("data-position", h.position);
        slot.setAttribute("data-normal", "0m 1m 0m");
        slot.textContent = h.label;
        mv.appendChild(slot);
        overlays[h.field] = slot;
      });
      host.appendChild(mv);
    } else if (cfg.fallback_svg) {
      const fb = el("div", "fallback");
      fb.innerHTML = cfg.fallback_svg;
      host.appendChild(fb);
      // The schematic self-selects: a field is bound only if the SVG has a node
      // for it, so the full hotspot list is safe here (no origin pile-up).
      allHotspots.forEach((h) => {
        const node = fb.querySelector('[data-field="' + h.field + '"]');
        if (node) overlays[h.field] = node;
      });
    } else {
      host.appendChild(el("div", "muted-note",
        "3D unavailable on this device and no 2D fallback was provided."));
    }

    // Live condition cue: a 0..1 stress ramp from cfg.stress_field's bounds. For
    // an "above" field, 0 at/below warn → 1 at/above crit (inverted for "below").
    function stressRamp() {
      const f = cfg.stress_field;
      if (!f) return null;
      const v = D.latest[f];
      if (typeof v !== "number") return null;
      const warn = cfg.stress_warn, crit = cfg.stress_crit;
      if (warn == null || crit == null || warn === crit) return null;
      const below = cfg.stress_direction === "below";
      const t = below ? (warn - v) / (warn - crit) : (v - warn) / (crit - warn);
      return Math.max(0, Math.min(1, t));
    }

    function applyCondition() {
      if (!modelViewer) return;
      const t = stressRamp();
      if (t == null) return;
      // Healthy → amber → brown: sepia warms the model toward brown and the
      // brightness/hue drop reads as degradation. A subtle floor so a healthy
      // asset still looks live.
      modelViewer.style.filter =
        `sepia(${(0.75 * t).toFixed(2)}) saturate(${(1 + 0.4 * t).toFixed(2)}) ` +
        `brightness(${(1 - 0.28 * t).toFixed(2)}) hue-rotate(${Math.round(-12 * t)}deg)`;
      // Optional discrete stage swap when distinct GLBs are provided.
      const stages = cfg.stage_models || {};
      const level = t >= 0.999 ? "crit" : t > 0 ? "warn" : "ok";
      if (stages[level] && stages[level] !== currentStage) {
        currentStage = stages[level];
        modelViewer.setAttribute("src", resolveUrl(D, currentStage));
      }
    }

    function update() {
      for (const h of allHotspots) {
        const node = overlays[h.field];
        if (!node) continue;
        const v = D.latest[h.field];
        const txt = v == null ? "—" : Number(v).toFixed(1);
        node.textContent = h.label + ": " + txt + (h.unit ? " " + h.unit : "");
        node.classList.remove("warn", "crit");
        const lvl = hotspotLevel(h, v);
        if (lvl) node.classList.add(lvl);
      }
      applyCondition();
    }

    return { el: root, fields: hotspots.map((h) => h.field), update };
  });
})();
