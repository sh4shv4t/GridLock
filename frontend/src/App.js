// Gridlock 2.0 — Parking Intelligence map
// Surfaces the debiased model outputs: priority hotspots, enforcement blind
// spots, and recidivist vehicles. Wards remain a safe HTML side panel
// (map-drawn ward polygons crash the Mappls SDK).

import { mappls } from "mappls-web-maps";
import { useEffect, useRef, useState, useMemo } from "react";

const mapplsClassObject = new mappls();

// ─── Paste a FRESH token (regenerate in console — old ones expire in 24h) ───
const MAPPLS_TOKEN = "YOUR_MAPPLS_TOKEN_HERE";
// ─────────────────────────────────────────────────────────────────────────────

// priority_score is the debiased model output; fall back to legacy impact_score
const scoreOf = (h) => (h.priority_score ?? h.impact_score ?? 0);
const fmt = (n) => (n == null ? "—" : Number(n).toLocaleString());
const CIS_COLOR = { Critical: "#B71C1C", High: "#E64A19", Medium: "#F9A825", Low: "#43A047" };

// ── Role-based views ────────────────────────────────────────────────────────
// "local" = personalised, area-scoped, sparse (default — what a resident/officer
// in one area needs). "admin" = the full citywide console. Switched by ?view=,
// shareable by URL. Local view never shows vehicle plates (privacy by default).
const VIEWS = ["local", "admin"];
const DEFAULT_AREA = "Jakkasandra"; // central ward near BTM/Koramangala; ?area= overrides
// colloquial names → official ward names, so typing "BTM"/"Koramangala" resolves
// (residents don't think in BBMP ward names). Editable; extras harmless.
const AREA_ALIASES = {
  "Jakkasandra": ["btm", "koramangala", "krmngla"],
  "Shantala Nagar": ["mg road", "brigade road", "ashok nagar"],
  "Sampangiram Nagar": ["shivajinagar", "shivaji nagar"],
  "HSR - Singasandra": ["hsr", "hsr layout"],
  "J P Nagar": ["jp nagar"],
  "Agara": ["sarjapur"],
  "Marathahalli": ["marathalli"],
  "Hulimavu": ["bannerghatta"],
  "Begur": ["bommanahalli"],
};
const LOCAL_LAYERS = { traffic: false, heatmap: false, circles: true, blindspots: true, recidivists: false };
const readParams = () => {
  const p = new URLSearchParams(window.location.search);
  const v = (p.get("view") || "local").toLowerCase();
  return { view: VIEWS.includes(v) ? v : "local", area: p.get("area") || DEFAULT_AREA };
};
const writeParam = (k, val) => {
  const p = new URLSearchParams(window.location.search);
  p.set(k, val);
  window.history.replaceState(null, "", `?${p.toString()}`);
};

// responsive breakpoint — phones get a top bar + bottom sheet instead of floating cards
const MOBILE_BP = 640;
function useIsMobile(bp = MOBILE_BP) {
  const query = `(max-width: ${bp}px)`;
  const [m, setM] = useState(() => typeof window !== "undefined" && window.matchMedia(query).matches);
  useEffect(() => {
    const mq = window.matchMedia(query);
    const on = () => setM(mq.matches);
    on();
    mq.addEventListener ? mq.addEventListener("change", on) : mq.addListener(on);
    return () => { mq.removeEventListener ? mq.removeEventListener("change", on) : mq.removeListener(on); };
  }, [query]);
  return m;
}

// ── design tokens (sharp "telemetry" look — ink + signal-orange, mono metrics) ──
const T = {
  ink: "#0B1020", muted: "#6B7280", hair: "#E6E8EC", accent: "#FF5A1F",
  panel: "rgba(255,255,255,0.96)",
  display: '"Space Grotesk", system-ui, -apple-system, sans-serif',
  mono: '"JetBrains Mono", ui-monospace, "SF Mono", monospace',
};
const eyebrow = { textTransform: "uppercase", fontSize: 9.5, letterSpacing: 1.4, fontWeight: 600, color: T.muted };

function App() {
  const mapRef = useRef(null);
  const layers = useRef({ heatmap: null, circles: [], recids: [] });
  const [heat, setHeat] = useState([]);
  const [debiased, setDebiased] = useState([]);
  const [rawHotspots, setRawHotspots] = useState([]);
  const [recids, setRecids] = useState([]);
  const [summary, setSummary] = useState(null);
  const [insights, setInsights] = useState(null);
  const [showWhy, setShowWhy] = useState(false);
  const [mode, setMode] = useState("debiased"); // "debiased" | "raw" (admin only)
  const [mapReady, setMapReady] = useState(false);
  const [selected, setSelected] = useState(null);
  const [wardFilter, setWardFilter] = useState("ALL");
  const initParams = readParams();
  const [view, setView] = useState(initParams.view);   // "local" | "admin"
  const [area, setArea] = useState(initParams.area);    // personalised scope for local view
  const [areaQuery, setAreaQuery] = useState(initParams.area); // typeahead text
  const [areaOpen, setAreaOpen] = useState(false);      // area dropdown open?
  const [areaHi, setAreaHi] = useState(0);              // highlighted match (keyboard)
  const isMobile = useIsMobile();
  const SHEET_COLLAPSED = 56;                           // peek height (just the handle row)
  const [sheetH, setSheetH] = useState(() => Math.round((typeof window !== "undefined" ? window.innerHeight : 800) * 0.5));
  const [dragging, setDragging] = useState(false);
  const dragRef = useRef(null);                         // bottom-sheet drag gesture state
  const [show, setShow] = useState({
    traffic: false, heatmap: false, circles: true, blindspots: true, recidivists: false,
  });

  // load data
  useEffect(() => {
    fetch("/heatmap_points.json").then((r) => (r.ok ? r.json() : [])).then(setHeat).catch(() => {});
    fetch("/hotspots_full.json").then((r) => (r.ok ? r.json() : [])).then(setDebiased).catch(() => {});
    fetch("/hotspots_raw.json").then((r) => (r.ok ? r.json() : [])).then(setRawHotspots).catch(() => {});
    fetch("/recidivists.json").then((r) => (r.ok ? r.json() : [])).then(setRecids).catch(() => {});
    fetch("/summary.json").then((r) => (r.ok ? r.json() : null)).then(setSummary).catch(() => {});
    fetch("/insights.json").then((r) => (r.ok ? r.json() : null)).then(setInsights).catch(() => {});
  }, []);

  // local view is intentionally sparse: fixed minimal layers, debiased-only,
  // scoped to one area. We DERIVE these (never clobber admin's own state).
  const isLocal = view === "local";
  const effShow = isLocal ? LOCAL_LAYERS : show;
  const effMode = isLocal ? "debiased" : mode;          // no raw toggle in local
  const scope = isLocal ? area : wardFilter;            // local locks scope to the area

  // the active hotspot set depends on the debiased/raw toggle (raw falls back to debiased if absent)
  const hotspots = useMemo(
    () => (effMode === "raw" && rawHotspots.length ? rawHotspots : debiased),
    [effMode, rawHotspots, debiased]
  );

  // group hotspots by ward for the side panel, ranked by priority
  const wards = useMemo(() => {
    const m = {};
    hotspots.forEach((h) => {
      const w = h.ward_name || "Unknown";
      (m[w] = m[w] || []).push(h);
    });
    return Object.entries(m)
      .map(([name, list]) => ({
        name,
        list: list.sort((a, b) => scoreOf(b) - scoreOf(a)),
        top: Math.max(...list.map(scoreOf)),
        blind: list.filter((x) => x.blindspot).length,
      }))
      .sort((a, b) => b.top - a.top);
  }, [hotspots]);

  const visible = useMemo(
    () => (scope === "ALL" ? hotspots : hotspots.filter((h) => (h.ward_name || "Unknown") === scope)),
    [hotspots, scope]
  );

  // wards present in the data — powers the local-view area picker
  const areaOptions = useMemo(
    () => Array.from(new Set(hotspots.map((h) => h.ward_name || "Unknown"))).filter((w) => w && w !== "Unknown").sort(),
    [hotspots]
  );

  // typeahead matches: ward name OR a colloquial alias contains the query (empty = full list)
  const areaMatches = useMemo(() => {
    const ql = areaQuery.trim().toLowerCase();
    if (!ql) return areaOptions;
    return areaOptions.filter((w) =>
      w.toLowerCase().includes(ql) || (AREA_ALIASES[w] || []).some((a) => a.includes(ql) || ql.includes(a))
    );
  }, [areaQuery, areaOptions]);

  // the local "in your area" list — top hotspots in the chosen area, by priority
  const localList = useMemo(
    () => [...visible].sort((a, b) => scoreOf(b) - scoreOf(a)),
    [visible]
  );
  const localBlind = useMemo(() => visible.filter((h) => h.blindspot).length, [visible]);

  // wards ranked by mean CIS — lets the local view say "your area ranks #N"
  const wardRanking = useMemo(() => {
    const m = {};
    hotspots.forEach((h) => { const w = h.ward_name || "Unknown"; (m[w] = m[w] || []).push(h.cis || 0); });
    return Object.entries(m)
      .map(([w, arr]) => ({ ward: w, mean: arr.reduce((a, b) => a + b, 0) / arr.length }))
      .sort((a, b) => b.mean - a.mean);
  }, [hotspots]);

  // up to 3 short, personal headlines for "News in your area" — all from shipped JSON
  const localNews = useMemo(() => {
    const items = [];
    if (localBlind > 0) items.push({ icon: "◆", color: "#8E24AA",
      text: `${localBlind} enforcement blind spot${localBlind > 1 ? "s" : ""} nearby — high impact, rarely patrolled.` });
    const idx = wardRanking.findIndex((r) => r.ward === area);
    if (idx >= 0) items.push({ icon: "▲", color: T.accent,
      text: `Ranks #${idx + 1} of ${wardRanking.length} areas by congestion impact.` });
    const worst = localList[0];
    if (worst && worst.cis_explanation && worst.cis_explanation.length) {
      const driver = worst.cis_explanation[0].split(" +")[0].toLowerCase();
      items.push({ icon: "●", color: CIS_COLOR[worst.cis_class] || T.ink,
        text: `Worst spot: ${worst.cis_class} impact — driven by ${driver}.` });
    }
    return items.slice(0, 3);
  }, [localBlind, wardRanking, area, localList]);

  const blindCount = useMemo(() => hotspots.filter((h) => h.blindspot).length, [hotspots]);

  // init map — guarded to run EXACTLY once (StrictMode double-mount safe)
  const initedRef = useRef(false);
  useEffect(() => {
    if (initedRef.current) return;
    initedRef.current = true;
    mapplsClassObject.initialize(MAPPLS_TOKEN, { map: true, layer: "vector", version: "3.0" }, () => {
      const map = mapplsClassObject.Map({
        id: "map",
        properties: { center: [12.9716, 77.5946], zoom: 12, traffic: true, zoomControl: true, scaleControl: true },
      });
      mapRef.current = map;
      map.on("load", () => setMapReady(true));
      setTimeout(() => setMapReady(true), 2500);
    });
    // NOTE: no cleanup — removing the map under StrictMode double-mount aborts tiles.
  }, []);

  // draw layers
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !mapReady) return;
    clearLayers(map);

    // Heatmap = the RAW patrol footprint. Auto-shown in Raw mode (tells the story:
    // "counting → red everywhere"), hidden in Debiased unless explicitly toggled on.
    if ((effShow.heatmap || effMode === "raw") && heat.length) {
      try {
        layers.current.heatmap = mapplsClassObject.HeatmapLayer({
          map, data: heat, opacity: 0.45, radius: 22, maxIntensity: 18, fitbounds: false,
          gradient: ["rgba(0,0,255,0)", "rgba(0,150,255,0.45)", "rgba(0,220,120,0.55)", "rgba(255,210,0,0.7)", "rgba(255,70,0,0.8)"],
        });
      } catch (e) { console.log("heatmap error", e); }
    }

    if (effShow.circles && visible.length) {
      const metric = (h) => (effMode === "raw" ? (h.violation_count || 0) : scoreOf(h));
      const vals = hotspots.map(metric).sort((a, b) => a - b);
      const maxScore = vals[vals.length - 1] || 1;
      const q = (p) => vals[Math.floor(p * (vals.length - 1))] || 0;
      const p80 = q(0.80), p55 = q(0.55);   // quantile bands -> red/amber/yellow spread (raw mode)
      const byQuantile = (s) => (s >= p80 ? "#C62828" : s >= p55 ? "#EF6C00" : "#F9A825");
      // Debiased mode: colour by CIS class (most green/amber, few red) — ties map to the score.
      const color = (h) => (effMode === "debiased" && h.cis_class ? CIS_COLOR[h.cis_class] : byQuantile(metric(h)));
      const radius = (s) => 130 + (s / maxScore) * 300;
      visible.forEach((h) => {
        const isBlind = h.blindspot && effShow.blindspots && effMode === "debiased";
        try {
          const c = mapplsClassObject.Circle({
            map, center: { lat: h.lat, lng: h.lng }, radius: radius(metric(h)),
            fillColor: color(h), fillOpacity: 0.3,
            strokeColor: isBlind ? "#8E24AA" : color(h),
            strokeOpacity: 0.9, strokeWeight: isBlind ? 4 : 1.5,
          });
          if (c && c.addListener) c.addListener("click", () => setSelected(h));
          layers.current.circles.push(c);
        } catch (e) { console.log("circle error", e); }
      });
    }

    if (effShow.recidivists && recids.length) {
      recids.slice(0, 120).forEach((v) => {
        try {
          const c = mapplsClassObject.Circle({
            map, center: { lat: v.top_lat, lng: v.top_lng }, radius: 120,
            fillColor: "#1565C0", fillOpacity: 0.7, strokeColor: "#0D47A1", strokeOpacity: 1, strokeWeight: 1,
          });
          if (c && c.addListener) c.addListener("click", () => setSelected({ recid: true, ...v }));
          layers.current.recids.push(c);
        } catch (e) {}
      });
    }

    const repaint = () => {
      try {
        if (map.resize) map.resize();
        if (map.triggerRepaint) map.triggerRepaint();
        if (map.getCenter && map.setCenter) map.setCenter(map.getCenter());
      } catch (e) {}
    };
    repaint(); setTimeout(repaint, 200); setTimeout(repaint, 600);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [heat, hotspots, recids, visible, mapReady, effShow, effMode, view, area]);

  // the map fills the screen behind the overlays; nudge a repaint when the layout
  // mode flips (mobile sheet <-> desktop cards) so tiles re-settle cleanly.
  useEffect(() => {
    const map = mapRef.current;
    if (!map) return;
    const t = setTimeout(() => { try { map.resize && map.resize(); map.triggerRepaint && map.triggerRepaint(); } catch (e) {} }, 250);
    return () => clearTimeout(t);
  }, [isMobile]);

  function clearLayers(map) {
    if (layers.current.heatmap) { try { mapplsClassObject.removeLayer({ map, layer: layers.current.heatmap }); } catch (e) {} layers.current.heatmap = null; }
    [...layers.current.circles, ...layers.current.recids].forEach((c) => { try { mapplsClassObject.removeLayer({ map, layer: c }); } catch (e) {} });
    layers.current.circles = []; layers.current.recids = [];
  }

  function centerOnArea(name) {
    const map = mapRef.current;
    if (!map || !map.setCenter) return;
    const list = hotspots.filter((h) => (h.ward_name || "Unknown") === name);
    if (list.length) { map.setCenter([list[0].lng, list[0].lat]); if (map.setZoom) map.setZoom(14); }
  }
  function cityView() {
    const map = mapRef.current;
    if (map && map.setZoom) { map.setZoom(12); map.setCenter([77.5946, 12.9716]); }
  }

  function selectWard(name) {
    setWardFilter(name);
    if (name !== "ALL") centerOnArea(name); else cityView();
  }

  // local view: pick the area you're in (e.g. "I'm in BTM") — recenters + rescopes
  function focusArea(name) {
    setArea(name); writeParam("area", name); centerOnArea(name);
  }

  // switch lens; keep the URL shareable so a link drops someone straight into a view
  function switchView(v) {
    setView(v); writeParam("view", v);
    if (v === "local") centerOnArea(area); else cityView();
  }

  // when the map first becomes ready in local view, frame the user's area
  useEffect(() => {
    if (mapReady && view === "local" && hotspots.length) centerOnArea(area);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [mapReady, view, hotspots.length]);

  // ── area typeahead handlers ──
  const pickArea = (name) => { focusArea(name); setAreaQuery(name); setAreaOpen(false); };
  const onAreaFocus = (e) => { setAreaOpen(true); setAreaQuery(area); setAreaHi(0); e.target.select(); };
  const onAreaChange = (e) => { setAreaQuery(e.target.value); setAreaHi(0); setAreaOpen(true); };
  const onAreaBlur = () => { setTimeout(() => setAreaOpen(false), 200); }; // delay so option click registers
  const onAreaKey = (e) => {
    if (e.key === "ArrowDown") { e.preventDefault(); setAreaOpen(true); setAreaHi((i) => Math.min(i + 1, areaMatches.length - 1)); }
    else if (e.key === "ArrowUp") { e.preventDefault(); setAreaHi((i) => Math.max(i - 1, 0)); }
    else if (e.key === "Enter") { e.preventDefault(); if (areaMatches[areaHi]) pickArea(areaMatches[areaHi]); }
    else if (e.key === "Escape") { setAreaOpen(false); e.target.blur(); }
  };

  const card = { background: T.panel, borderRadius: 6, border: `1px solid ${T.hair}`,
    boxShadow: "0 6px 24px rgba(11,16,32,0.12)", fontFamily: T.display,
    backdropFilter: "blur(6px)", WebkitBackdropFilter: "blur(6px)" };

  const kpi = (val, label, color) => (
    <div style={{ textAlign: "center", padding: "0 11px" }}>
      <div style={{ fontFamily: T.mono, fontSize: 18, fontWeight: 700, color: color || T.ink, lineHeight: 1.05 }}>{val}</div>
      <div style={{ ...eyebrow, marginTop: 2 }}>{label}</div>
    </div>
  );

  // ── responsive containers — phone uses a DRAGGABLE bottom sheet ──
  // drag the handle to resize between three snaps (peek / half / full); the body
  // scrolls within whatever height you set. A plain tap = collapse <-> half.
  const snapPoints = () => {
    const vh = window.innerHeight;
    return [SHEET_COLLAPSED, Math.round(vh * 0.5), Math.round(vh * 0.9)];
  };
  const nearestSnap = (h) => snapPoints().reduce((a, b) => (Math.abs(b - h) < Math.abs(a - h) ? b : a));
  const onSheetDown = (e) => {
    dragRef.current = { startY: e.clientY, startH: sheetH, moved: false };
    setDragging(true);
    try { e.currentTarget.setPointerCapture(e.pointerId); } catch (_) {}
  };
  const onSheetMove = (e) => {
    if (!dragRef.current) return;
    const dy = dragRef.current.startY - e.clientY;          // up = taller
    if (Math.abs(dy) > 3) dragRef.current.moved = true;
    const h = Math.min(Math.max(dragRef.current.startH + dy, SHEET_COLLAPSED), Math.round(window.innerHeight * 0.92));
    setSheetH(h);
  };
  const onSheetUp = () => {
    const d = dragRef.current; dragRef.current = null; setDragging(false);
    if (!d) return;
    if (!d.moved) {                                          // a tap, not a drag
      const [collapsed, half] = snapPoints();
      setSheetH(d.startH <= collapsed + 20 ? half : collapsed);
    } else {
      setSheetH((h) => nearestSnap(h));                      // snap to nearest on release
    }
  };
  const sheetStyle = (h) => ({
    position: "absolute", left: 0, right: 0, bottom: 0, zIndex: 1000,
    background: T.panel, borderRadius: "14px 14px 0 0", borderTop: `3px solid ${T.accent}`,
    boxShadow: "0 -6px 24px rgba(11,16,32,0.18)", fontFamily: T.display,
    backdropFilter: "blur(6px)", WebkitBackdropFilter: "blur(6px)",
    height: h, overflow: "hidden", display: "flex", flexDirection: "column",
    transition: dragging ? "none" : "height 0.25s ease",
  });
  const sheetScroll = { flex: 1, minHeight: 0, padding: "0 16px max(16px, env(safe-area-inset-bottom))",
    overflowY: "auto", WebkitOverflowScrolling: "touch" };

  // ── reusable content — assembled into floating cards (desktop) or the sheet (mobile) ──
  const ViewSwitch = () => (
    <div style={{ display: "flex", border: `1px solid ${T.ink}`, borderRadius: 5, overflow: "hidden" }}>
      {[["local", "My area"], ["admin", "Admin"]].map(([v, l]) => (
        <button key={v} onClick={() => switchView(v)}
          style={{ flex: 1, padding: "5px 10px", fontSize: 11, border: "none", cursor: "pointer", fontFamily: T.display,
                   background: view === v ? T.ink : "transparent", color: view === v ? "#fff" : T.ink,
                   fontWeight: 600, letterSpacing: 0.3, whiteSpace: "nowrap" }}>{l}</button>
      ))}
    </div>
  );

  const RawToggle = () => (
    <div style={{ display: "flex", border: `1px solid ${T.ink}`, borderRadius: 5, overflow: "hidden", width: "fit-content" }}>
      {[["debiased", "Debiased"], ["raw", "Raw counts"]].map(([m, l]) => (
        <button key={m} onClick={() => setMode(m)}
          style={{ padding: "5px 10px", fontSize: 11, border: "none", cursor: "pointer", fontFamily: T.display,
                   background: mode === m ? T.ink : "transparent", color: mode === m ? "#fff" : T.ink,
                   fontWeight: 600, letterSpacing: 0.3, whiteSpace: "nowrap" }}>{l}</button>
      ))}
    </div>
  );

  const AdminKpis = () => (summary ? (
    <>
      {kpi(fmt(summary.total_violations), "violations")}
      {kpi(summary.n_blindspots_total, "blind spots", "#8E24AA")}
      {kpi(fmt(summary.n_recidivists), "recidivists", "#1565C0")}
      {kpi(summary.evening_gap_pct != null ? `${summary.evening_gap_pct}%` : "—", "tickets 3–10pm", "#C62828")}
    </>
  ) : null);

  const LocalInner = () => (
    <>
      <div style={{ ...eyebrow, marginBottom: 3 }}>Your area</div>
      <div style={{ position: "relative", marginBottom: 8 }}>
        <input value={areaOpen ? areaQuery : area} onChange={onAreaChange} onFocus={onAreaFocus}
          onBlur={onAreaBlur} onKeyDown={onAreaKey} placeholder="Type or pick your area"
          style={{ width: "100%", boxSizing: "border-box", fontFamily: T.display, fontSize: 15, fontWeight: 700,
                   color: T.ink, border: "none", borderBottom: `2px solid ${T.accent}`, padding: "2px 18px 5px 0",
                   background: "transparent", outline: "none" }} />
        <span style={{ position: "absolute", right: 0, top: 3, color: T.muted, pointerEvents: "none", fontSize: 11 }}>▾</span>
        {areaOpen && (
          <div style={{ position: "absolute", top: "100%", left: 0, right: 0, zIndex: 1100, marginTop: 4,
                        background: "#fff", border: `1px solid ${T.hair}`, borderRadius: 6,
                        boxShadow: "0 8px 24px rgba(11,16,32,0.16)", maxHeight: 240, overflowY: "auto" }}>
            {areaMatches.length === 0 && (
              <div style={{ padding: "9px 10px", fontSize: 12, color: "#999" }}>No area matches “{areaQuery}”.</div>
            )}
            {areaMatches.map((w, i) => (
              <div key={w} onMouseDown={(e) => e.preventDefault()} onClick={() => pickArea(w)}
                   onMouseEnter={() => setAreaHi(i)}
                   style={{ padding: "8px 10px", fontSize: 13, cursor: "pointer", color: T.ink,
                            background: i === areaHi ? "#F0F1F4" : (w === area ? "#FFF3EC" : "#fff"),
                            fontWeight: w === area ? 700 : 500 }}>
                {w}
              </div>
            ))}
          </div>
        )}
      </div>
      <div style={{ fontSize: 12, color: "#666", marginBottom: 10 }}>
        {localList.length} hotspot{localList.length === 1 ? "" : "s"} near you
        {localBlind > 0 && <> · <b style={{ color: "#8E24AA" }}>{localBlind} blind spot{localBlind === 1 ? "" : "s"}</b></>}
      </div>
      {/* News in your area — compact, max 3 lines, all area-scoped from shipped data */}
      {localNews.length > 0 && (
        <div style={{ marginBottom: 10, padding: "8px 10px", background: "#F8F9FB", border: `1px solid ${T.hair}`, borderRadius: 6 }}>
          <div style={{ ...eyebrow, marginBottom: 5 }}>News in your area</div>
          {localNews.map((n, i) => (
            <div key={i} style={{ display: "flex", gap: 7, fontSize: 11, lineHeight: 1.4,
                                  marginBottom: i < localNews.length - 1 ? 5 : 0, color: "#444" }}>
              <span style={{ color: n.color, flexShrink: 0 }}>{n.icon}</span>
              <span>{n.text}</span>
            </div>
          ))}
        </div>
      )}
      {localList.length === 0 && (
        <div style={{ fontSize: 12, color: "#999", padding: "6px 0 10px" }}>
          No mapped hotspots here yet — pick another area above.
        </div>
      )}
      {localList.slice(0, 8).map((h) => (
        <div key={h.priority_rank} onClick={() => setSelected(h)}
          style={{ padding: "8px 10px", marginBottom: 6, fontSize: 12.5, cursor: "pointer",
                   border: "1px solid #eee", borderRadius: 6, display: "flex", alignItems: "center", gap: 9,
                   borderLeft: `3px solid ${CIS_COLOR[h.cis_class] || "#ccc"}` }}>
          <span style={{ fontFamily: T.mono, fontWeight: 700, fontSize: 14, color: CIS_COLOR[h.cis_class] || T.ink }}>{h.cis ?? "—"}</span>
          <span style={{ flex: 1, color: "#333" }}>
            {h.cis_class || "—"} impact
            {h.blindspot && <span style={{ color: "#8E24AA" }}> · blind spot</span>}
          </span>
        </div>
      ))}
      <div style={{ fontSize: 10.5, color: T.muted, marginTop: 4, lineHeight: 1.45 }}>
        Tap a hotspot for its congestion breakdown. Showing only what's relevant to your area.
      </div>
    </>
  );

  const AdminInner = () => (
    <>
      <div style={{ ...eyebrow, marginBottom: 3 }}>Bengaluru · Enforcement</div>
      <div style={{ fontWeight: 700, fontSize: 16, marginBottom: 6, letterSpacing: -0.2 }}>Priority Hotspots</div>
      <div style={{ fontSize: 11, marginBottom: 6, padding: "5px 8px", borderRadius: 5,
                    background: mode === "raw" ? "#FFF3E0" : "#E8F5E9",
                    color: mode === "raw" ? "#E65100" : "#2E7D32" }}>
        {mode === "raw"
          ? "Raw counts — ranks where police already ticket most (the patrol-route view)."
          : "Debiased — predicted latent rate, corrected for patrol coverage × congestion impact."}
      </div>
      <div style={{ fontSize: 12, color: "#666", marginBottom: 10 }}>
        {hotspots.length} hotspots · {wards.length} wards
        {mode === "debiased" && blindCount > 0 && <> · <b style={{ color: "#8E24AA" }}>{blindCount} blind spots</b></>}
      </div>
      <button onClick={() => selectWard("ALL")}
        style={{ width: "100%", marginBottom: 8, padding: "7px", border: `1px solid ${T.ink}`, fontFamily: T.display,
                 background: wardFilter === "ALL" ? T.ink : "transparent", color: wardFilter === "ALL" ? "white" : T.ink,
                 borderRadius: 5, cursor: "pointer", fontSize: 12, fontWeight: 600, letterSpacing: 0.3 }}>
        Show all wards
      </button>
      {wards.map((w) => (
        <div key={w.name} style={{ marginBottom: 8, border: "1px solid #eee", borderRadius: 6, overflow: "hidden" }}>
          <div onClick={() => selectWard(w.name)}
            style={{ padding: "8px 10px", cursor: "pointer", background: wardFilter === w.name ? "#E8EAF6" : "#fafafa",
                     fontWeight: 600, fontSize: 13, display: "flex", justifyContent: "space-between" }}>
            <span>{w.name}{w.blind > 0 && <span style={{ color: "#8E24AA" }}> ◆{w.blind}</span>}</span>
            <span style={{ color: "#888", fontWeight: 400 }}>{w.list.length}</span>
          </div>
          {wardFilter === w.name && w.list.map((h) => (
            <div key={h.priority_rank} onClick={() => setSelected(h)}
              style={{ padding: "6px 10px", fontSize: 12, borderTop: "1px solid #eee", cursor: "pointer", color: "#333" }}>
              #{h.priority_rank} {h.locality || h.ward_name} — <b>{mode === "raw" ? `${h.violation_count} tickets` : scoreOf(h).toFixed(2)}</b>
              {mode === "debiased" && h.blindspot && <span style={{ color: "#8E24AA" }}> ◆</span>}
            </div>
          ))}
        </div>
      ))}
    </>
  );

  const LayersWhyLegend = () => (
    <>
      {view === "admin" && (
        <>
          <div style={{ ...eyebrow, marginBottom: 8 }}>Layers</div>
          {[["traffic", "Live traffic flow"], ["heatmap", "Violation heatmap (raw)"], ["circles", "Priority hotspots"],
            ["blindspots", "Highlight blind spots"], ["recidivists", "Recidivist vehicles"]].map(([k, l]) => (
            <label key={k} style={{ display: "block", marginBottom: 6 }}>
              <input type="checkbox" checked={show[k]} onChange={() => setShow((s) => ({ ...s, [k]: !s[k] }))} /> {l}
            </label>
          ))}
        </>
      )}
      <div style={{ marginTop: 10, paddingTop: 8, borderTop: `1px solid ${T.hair}`, fontSize: 12, color: T.muted }}>
        <div><span style={{ color: "#C62828" }}>●</span> High &nbsp; <span style={{ color: "#EF6C00" }}>●</span> Med &nbsp; <span style={{ color: "#F9A825" }}>●</span> Low priority</div>
        <div style={{ marginTop: 4 }}><span style={{ color: "#8E24AA" }}>◆</span> Blind spot (high impact, low patrol) &nbsp; <span style={{ color: "#1565C0" }}>●</span> Recidivist</div>
      </div>

      {/* WHY — socio-economic drivers (expandable) */}
      {insights && insights.factor_contrasts && (
        <div style={{ marginTop: 10, paddingTop: 8, borderTop: `1px solid ${T.hair}` }}>
          <div onClick={() => setShowWhy((s) => !s)}
               style={{ ...eyebrow, cursor: "pointer", display: "flex", justifyContent: "space-between", color: T.accent }}>
            <span>Why these hotspots?</span><span>{showWhy ? "−" : "+"}</span>
          </div>
          {showWhy && (
            <div style={{ marginTop: 8 }}>
              <div style={{ fontSize: 11, color: T.muted, marginBottom: 6 }}>CIS multiplier when a hotspot is near…</div>
              {insights.factor_contrasts.slice(0, 4).map((f) => {
                const pct = Math.min((f.ratio / 3) * 100, 100);
                return (
                  <div key={f.factor} style={{ marginBottom: 6 }}>
                    <div style={{ display: "flex", justifyContent: "space-between", fontSize: 11, marginBottom: 2 }}>
                      <span style={{ color: T.ink }}>{f.factor}</span>
                      <span style={{ fontFamily: T.mono, fontWeight: 700, color: T.accent }}>{f.ratio}×</span>
                    </div>
                    <div style={{ height: 5, background: T.hair, borderRadius: 3 }}>
                      <div style={{ width: `${pct}%`, height: 5, background: T.accent, borderRadius: 3 }} />
                    </div>
                  </div>
                );
              })}
              <div style={{ fontSize: 10.5, color: T.muted, marginTop: 6, lineHeight: 1.4 }}>
                Strongest correlate: road capacity × dense activity. Income/equity axis adds with a VIIRS layer.
              </div>
            </div>
          )}
        </div>
      )}
      {!mapReady && <div style={{ marginTop: 8, color: "#E65100", fontSize: 12 }}>⏳ map loading… (if stuck, token may be expired)</div>}
    </>
  );

  const DetailInner = () => (
    <>
      <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 8 }}>
        <strong>#{selected.priority_rank} {selected.locality || selected.ward_name}</strong>
        <span onClick={() => setSelected(null)} style={{ cursor: "pointer", color: "#999", fontSize: 18, lineHeight: 1 }}>✕</span>
      </div>
      {selected.blindspot && (
        <div style={{ background: "#F3E5F5", color: "#6A1B9A", fontSize: 12, fontWeight: 600,
                      padding: "4px 8px", borderRadius: 4, marginBottom: 8 }}>
          ◆ Enforcement blind spot — high predicted impact, low observed patrol
        </div>
      )}
      <div style={{ fontSize: 13, color: "#444", lineHeight: 1.7 }}>
        <div>Ward: <b>{selected.ward_name || "—"}</b></div>
        <div>Observed violations: <b>{selected.violation_count}</b></div>
        {selected.latent_rate != null && <div>Predicted latent rate: <b>{selected.latent_rate}</b> <span style={{ color: "#888" }}>(debiased)</span></div>}
        <div>Congestion impact: <b>{selected.congestion_ratio}×</b> <span style={{ color: "#888" }}>(road capacity)</span></div>
        <div>Priority score: <b>{scoreOf(selected).toFixed(3)}</b></div>
      </div>

      {/* CONGESTION IMPACT SCORE (CIS) */}
      {selected.cis != null && (
        <div style={{ marginTop: 12, paddingTop: 10, borderTop: "1px solid #eee" }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 8 }}>
            <span style={{ background: CIS_COLOR[selected.cis_class] || "#999", color: "white",
                           fontWeight: 700, fontSize: 12, padding: "3px 9px", borderRadius: 12 }}>
              {selected.cis_class}
            </span>
            <span style={{ fontSize: 20, fontWeight: 800, color: "#1A237E" }}>{selected.cis}</span>
            <span style={{ fontSize: 11, color: "#888" }}>/100 CIS</span>
            <span style={{ fontSize: 11, color: "#888", marginLeft: "auto" }}>
              conf {Math.round((selected.cis_confidence ?? 0) * 100)}%
            </span>
          </div>
          {selected.cis_components && (
            <div style={{ display: "flex", height: 8, borderRadius: 4, overflow: "hidden", marginBottom: 8 }}>
              {[["violation_load", "#1A237E"], ["excess_congestion", "#C62828"],
                ["carriageway_obstruction", "#EF6C00"], ["recurrence", "#00897B"]].map(([k, c]) => (
                <div key={k} title={`${k}: ${selected.cis_components[k]} pts`}
                     style={{ width: `${selected.cis_components[k]}%`, background: c }} />
              ))}
            </div>
          )}
          {selected.cis_explanation && (
            <ul style={{ margin: 0, paddingLeft: 16, fontSize: 11.5, color: "#444", lineHeight: 1.5 }}>
              {selected.cis_explanation.map((e, i) => <li key={i}>{e}</li>)}
            </ul>
          )}
          {selected.low_confidence && (
            <div style={{ fontSize: 10.5, color: "#E65100", marginTop: 6 }}>
              ⚠ ECS from proxy (no live traffic feed) — low confidence
            </div>
          )}
        </div>
      )}
    </>
  );

  const RecidInner = () => (
    <>
      <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 8 }}>
        <strong style={{ color: "#0D47A1" }}>Recidivist vehicle</strong>
        <span onClick={() => setSelected(null)} style={{ cursor: "pointer", color: "#999", fontSize: 18, lineHeight: 1 }}>✕</span>
      </div>
      <div style={{ fontSize: 13, color: "#444", lineHeight: 1.7 }}>
        <div>Vehicle: <b>{selected.vehicle}</b></div>
        <div>Type: <b>{selected.vehicle_type}</b></div>
        <div>Total violations: <b>{selected.hits}</b></div>
        <div>Location concentration: <b>{Math.round(selected.concentration * 100)}%</b></div>
        {selected.is_fleet_pattern && <div style={{ color: "#0D47A1", fontWeight: 600 }}>Fixed-location pattern (likely fleet / stand)</div>}
      </div>
    </>
  );

  return (
    <div style={{ position: "relative", width: "100%", height: "100vh", fontFamily: T.display, color: T.ink, overflow: "hidden" }}>
      {isMobile ? (
        // ── MOBILE: slim top bar + a single collapsible bottom sheet ──
        <>
          <div style={{ ...card, position: "absolute", top: 8, left: 8, zIndex: 1001,
                        display: "flex", alignItems: "center", gap: 10, padding: "6px 10px", borderLeft: `3px solid ${T.accent}` }}>
            <span style={{ width: 8, height: 8, background: T.accent, borderRadius: 2, display: "inline-block" }} />
            <span style={{ fontWeight: 700, fontSize: 13, letterSpacing: 0.5 }}>GRIDLOCK</span>
            {ViewSwitch()}
          </div>

          {!selected && (
            <div style={sheetStyle(sheetH)}>
              <div onPointerDown={onSheetDown} onPointerMove={onSheetMove} onPointerUp={onSheetUp} onPointerCancel={onSheetUp}
                   style={{ flexShrink: 0, cursor: "grab", touchAction: "none", userSelect: "none" }}>
                <div style={{ width: 36, height: 5, borderRadius: 3, background: T.hair, margin: "8px auto 0" }} />
                <div style={{ display: "flex", alignItems: "center", gap: 10, padding: "8px 16px 10px" }}>
                  <span style={{ fontWeight: 700, fontSize: 14, flex: 1 }}>{isLocal ? area : "Priority Hotspots"}</span>
                  <span style={{ ...eyebrow, color: T.accent }}>{sheetH <= SHEET_COLLAPSED + 20 ? "Drag up ↕" : "Drag ↕"}</span>
                </div>
              </div>
              <div style={sheetScroll}>
                {isLocal ? LocalInner() : (
                  <>
                    {summary && (
                      <div style={{ display: "flex", flexWrap: "wrap", rowGap: 10, marginBottom: 10, justifyContent: "space-between" }}>
                        {AdminKpis()}
                      </div>
                    )}
                    <div style={{ marginBottom: 12 }}>{RawToggle()}</div>
                    {AdminInner()}
                  </>
                )}
                <div style={{ marginTop: 10, paddingTop: 8, borderTop: `1px solid ${T.hair}` }}>
                  {LayersWhyLegend()}
                </div>
              </div>
            </div>
          )}

          {selected && (
            <div style={sheetStyle(Math.round((typeof window !== "undefined" ? window.innerHeight : 800) * 0.74))}>
              <div style={{ ...sheetScroll, paddingTop: 14 }}>
                {selected.recid ? RecidInner() : DetailInner()}
              </div>
            </div>
          )}
        </>
      ) : (
        // ── DESKTOP / LAPTOP / BIG SCREEN: corner-pinned floating cards ──
        <>
          {/* HEADLINE BANNER (top center) — admin only (decluttered out of local view) */}
          {view === "admin" && (
            <div style={{ ...card, position: "absolute", top: 12, left: "50%", transform: "translateX(-50%)",
                          zIndex: 1000, padding: "9px 8px 9px 0", display: "flex", alignItems: "center", gap: 4,
                          borderLeft: `3px solid ${T.accent}`,
                          maxWidth: "min(680px, calc(100vw - 620px))", flexWrap: "wrap", justifyContent: "center" }}>
              <div style={{ fontWeight: 700, fontSize: 14, color: T.ink, padding: "0 12px", letterSpacing: 0.5,
                            display: "flex", alignItems: "center", gap: 6 }}>
                <span style={{ width: 8, height: 8, background: T.accent, borderRadius: 2, display: "inline-block" }} />
                GRIDLOCK
              </div>
              {AdminKpis()}
              <div style={{ marginLeft: 6, marginRight: 4 }}>{RawToggle()}</div>
            </div>
          )}

          {/* LEFT PANEL — local: compact "in your area" */}
          {view === "local" && (
            <div style={{ ...card, position: "absolute", top: 12, left: 12, zIndex: 999, padding: "14px 16px", width: 264, maxHeight: "90vh", overflowY: "auto", borderTop: `3px solid ${T.accent}` }}>
              {LocalInner()}
            </div>
          )}

          {/* LEFT PANEL — admin: full ward list */}
          {view === "admin" && (
            <div style={{ ...card, position: "absolute", top: 12, left: 12, zIndex: 999, padding: "14px 16px", width: 280, maxHeight: "90vh", overflowY: "auto", borderTop: `3px solid ${T.accent}` }}>
              {AdminInner()}
            </div>
          )}

          {/* RIGHT PANEL — view switcher + (admin) layers + legend + socioeconomic toggle */}
          <div style={{ ...card, position: "absolute", top: 12, right: 12, zIndex: 999, padding: "12px 16px", fontSize: 13, width: 210 }}>
            <div style={{ ...eyebrow, marginBottom: 6 }}>View</div>
            <div style={{ marginBottom: 12 }}>{ViewSwitch()}</div>
            {LayersWhyLegend()}
          </div>

          {/* DETAIL PANEL */}
          {selected && !selected.recid && (
            <div style={{ ...card, position: "absolute", bottom: 30, right: 12, zIndex: 999, padding: "16px 20px", fontSize: 14, width: 310 }}>
              {DetailInner()}
            </div>
          )}

          {/* RECIDIVIST DETAIL */}
          {selected && selected.recid && (
            <div style={{ ...card, position: "absolute", bottom: 30, right: 12, zIndex: 999, padding: "16px 20px", fontSize: 14, width: 300 }}>
              {RecidInner()}
            </div>
          )}
        </>
      )}

      {/* map renders ONCE, outside the responsive branch — never remounts on resize */}
      <div id="map" style={{ width: "100%", height: "100%" }} />
    </div>
  );
}

export default App;
