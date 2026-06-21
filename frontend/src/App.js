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

function App() {
  const mapRef = useRef(null);
  const layers = useRef({ heatmap: null, circles: [], recids: [] });
  const [heat, setHeat] = useState([]);
  const [debiased, setDebiased] = useState([]);
  const [rawHotspots, setRawHotspots] = useState([]);
  const [recids, setRecids] = useState([]);
  const [summary, setSummary] = useState(null);
  const [mode, setMode] = useState("debiased"); // "debiased" | "raw"
  const [mapReady, setMapReady] = useState(false);
  const [selected, setSelected] = useState(null);
  const [wardFilter, setWardFilter] = useState("ALL");
  const [show, setShow] = useState({
    traffic: true, heatmap: true, circles: true, blindspots: true, recidivists: false,
  });

  // load data
  useEffect(() => {
    fetch("/heatmap_points.json").then((r) => (r.ok ? r.json() : [])).then(setHeat).catch(() => {});
    fetch("/hotspots_full.json").then((r) => (r.ok ? r.json() : [])).then(setDebiased).catch(() => {});
    fetch("/hotspots_raw.json").then((r) => (r.ok ? r.json() : [])).then(setRawHotspots).catch(() => {});
    fetch("/recidivists.json").then((r) => (r.ok ? r.json() : [])).then(setRecids).catch(() => {});
    fetch("/summary.json").then((r) => (r.ok ? r.json() : null)).then(setSummary).catch(() => {});
  }, []);

  // the active hotspot set depends on the debiased/raw toggle (raw falls back to debiased if absent)
  const hotspots = useMemo(
    () => (mode === "raw" && rawHotspots.length ? rawHotspots : debiased),
    [mode, rawHotspots, debiased]
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
    () => (wardFilter === "ALL" ? hotspots : hotspots.filter((h) => (h.ward_name || "Unknown") === wardFilter)),
    [hotspots, wardFilter]
  );

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

    if (show.heatmap && heat.length) {
      try {
        layers.current.heatmap = mapplsClassObject.HeatmapLayer({
          map, data: heat, opacity: 0.7, radius: 35, maxIntensity: 8, fitbounds: false,
          gradient: ["rgba(0,0,255,0)", "rgba(0,170,255,0.6)", "rgba(0,255,120,0.7)", "rgba(255,200,0,0.85)", "rgba(255,50,0,0.9)"],
        });
      } catch (e) { console.log("heatmap error", e); }
    }

    if (show.circles && visible.length) {
      // raw mode colours by violation count (the patrol-route view); debiased by priority score
      const metric = (h) => (mode === "raw" ? (h.violation_count || 0) : scoreOf(h));
      const maxScore = Math.max(...hotspots.map(metric)) || 1;
      const color = (s) => (s / maxScore > 0.65 ? "#C62828" : s / maxScore > 0.35 ? "#EF6C00" : "#F9A825");
      const radius = (s) => 250 + (s / maxScore) * 500;
      visible.forEach((h) => {
        const isBlind = h.blindspot && show.blindspots && mode === "debiased";
        try {
          const c = mapplsClassObject.Circle({
            map, center: { lat: h.lat, lng: h.lng }, radius: radius(metric(h)),
            fillColor: color(metric(h)), fillOpacity: 0.45,
            // blind spots get a bold magenta ring so they pop against the impact colours
            strokeColor: isBlind ? "#8E24AA" : color(metric(h)),
            strokeOpacity: 0.95, strokeWeight: isBlind ? 4 : 2,
          });
          if (c && c.addListener) c.addListener("click", () => setSelected(h));
          layers.current.circles.push(c);
        } catch (e) { console.log("circle error", e); }
      });
    }

    if (show.recidivists && recids.length) {
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
  }, [heat, hotspots, recids, visible, mapReady, show, mode]);

  function clearLayers(map) {
    if (layers.current.heatmap) { try { mapplsClassObject.removeLayer({ map, layer: layers.current.heatmap }); } catch (e) {} layers.current.heatmap = null; }
    [...layers.current.circles, ...layers.current.recids].forEach((c) => { try { mapplsClassObject.removeLayer({ map, layer: c }); } catch (e) {} });
    layers.current.circles = []; layers.current.recids = [];
  }

  function selectWard(name) {
    setWardFilter(name);
    const map = mapRef.current;
    if (map && name !== "ALL") {
      const list = hotspots.filter((h) => (h.ward_name || "Unknown") === name);
      if (list.length && map.setCenter) { map.setCenter([list[0].lng, list[0].lat]); if (map.setZoom) map.setZoom(14); }
    } else if (map && map.setZoom) { map.setZoom(12); map.setCenter([77.5946, 12.9716]); }
  }

  const card = { background: "white", borderRadius: 8, boxShadow: "0 2px 10px rgba(0,0,0,0.15)", fontFamily: "system-ui, sans-serif" };

  const kpi = (val, label, color) => (
    <div style={{ textAlign: "center", padding: "0 12px" }}>
      <div style={{ fontSize: 18, fontWeight: 800, color: color || "#1A237E", lineHeight: 1.1 }}>{val}</div>
      <div style={{ fontSize: 10, color: "#666", textTransform: "uppercase", letterSpacing: 0.4 }}>{label}</div>
    </div>
  );

  return (
    <div style={{ position: "relative", width: "100%", height: "100vh", fontFamily: "system-ui, sans-serif" }}>
      {/* HEADLINE BANNER (top center) — quantified takeaway + before/after toggle */}
      <div style={{ ...card, position: "absolute", top: 12, left: "50%", transform: "translateX(-50%)",
                    zIndex: 1000, padding: "8px 14px", display: "flex", alignItems: "center", gap: 6,
                    maxWidth: "min(640px, calc(100vw - 620px))", flexWrap: "wrap", justifyContent: "center" }}>
        <div style={{ fontWeight: 800, fontSize: 14, color: "#1A237E", paddingRight: 6 }}>GRIDLOCK</div>
        {summary && <>
          {kpi(fmt(summary.total_violations), "violations")}
          {kpi(summary.n_blindspots_total, "blind spots", "#8E24AA")}
          {kpi(fmt(summary.n_recidivists), "recidivists", "#1565C0")}
          {kpi(summary.evening_gap_pct != null ? `${summary.evening_gap_pct}%` : "—", "tickets 3–10pm", "#C62828")}
        </>}
        <div style={{ display: "flex", border: "1px solid #1A237E", borderRadius: 6, overflow: "hidden", marginLeft: 4 }}>
          {[["debiased", "Debiased"], ["raw", "Raw counts"]].map(([m, l]) => (
            <button key={m} onClick={() => setMode(m)}
              style={{ padding: "5px 9px", fontSize: 11, border: "none", cursor: "pointer",
                       background: mode === m ? "#1A237E" : "white", color: mode === m ? "white" : "#1A237E",
                       fontWeight: 600 }}>{l}</button>
          ))}
        </div>
      </div>

      {/* WARD SIDE PANEL (left) */}
      <div style={{ ...card, position: "absolute", top: 12, left: 12, zIndex: 999, padding: "14px 16px", width: 280, maxHeight: "90vh", overflowY: "auto" }}>
        <div style={{ fontWeight: 700, fontSize: 15, marginBottom: 4 }}>Enforcement Priorities</div>
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
          style={{ width: "100%", marginBottom: 8, padding: "6px", border: "1px solid #1A237E",
                   background: wardFilter === "ALL" ? "#1A237E" : "white", color: wardFilter === "ALL" ? "white" : "#1A237E",
                   borderRadius: 6, cursor: "pointer", fontSize: 13 }}>
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
      </div>

      {/* LAYER TOGGLES (right) */}
      <div style={{ ...card, position: "absolute", top: 12, right: 12, zIndex: 999, padding: "12px 16px", fontSize: 13, width: 210 }}>
        <strong style={{ display: "block", marginBottom: 8 }}>Layers</strong>
        {[["traffic", "Live traffic flow"], ["heatmap", "Violation heatmap (raw)"], ["circles", "Priority hotspots"],
          ["blindspots", "Highlight blind spots"], ["recidivists", "Recidivist vehicles"]].map(([k, l]) => (
          <label key={k} style={{ display: "block", marginBottom: 6 }}>
            <input type="checkbox" checked={show[k]} onChange={() => setShow((s) => ({ ...s, [k]: !s[k] }))} /> {l}
          </label>
        ))}
        <div style={{ marginTop: 10, paddingTop: 8, borderTop: "1px solid #eee", fontSize: 12, color: "#555" }}>
          <div><span style={{ color: "#C62828" }}>●</span> High &nbsp; <span style={{ color: "#EF6C00" }}>●</span> Med &nbsp; <span style={{ color: "#F9A825" }}>●</span> Low priority</div>
          <div style={{ marginTop: 4 }}><span style={{ color: "#8E24AA" }}>◆</span> Blind spot (high impact, low patrol) &nbsp; <span style={{ color: "#1565C0" }}>●</span> Recidivist</div>
        </div>
        {!mapReady && <div style={{ marginTop: 8, color: "#E65100", fontSize: 12 }}>⏳ map loading… (if stuck, token may be expired)</div>}
      </div>

      {/* DETAIL PANEL */}
      {selected && !selected.recid && (
        <div style={{ ...card, position: "absolute", bottom: 30, right: 12, zIndex: 999, padding: "16px 20px", fontSize: 14, width: 310 }}>
          <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 8 }}>
            <strong>#{selected.priority_rank} {selected.locality || selected.ward_name}</strong>
            <span onClick={() => setSelected(null)} style={{ cursor: "pointer", color: "#999" }}>✕</span>
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
        </div>
      )}

      {/* RECIDIVIST DETAIL */}
      {selected && selected.recid && (
        <div style={{ ...card, position: "absolute", bottom: 30, right: 12, zIndex: 999, padding: "16px 20px", fontSize: 14, width: 300 }}>
          <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 8 }}>
            <strong style={{ color: "#0D47A1" }}>Recidivist vehicle</strong>
            <span onClick={() => setSelected(null)} style={{ cursor: "pointer", color: "#999" }}>✕</span>
          </div>
          <div style={{ fontSize: 13, color: "#444", lineHeight: 1.7 }}>
            <div>Vehicle: <b>{selected.vehicle}</b></div>
            <div>Type: <b>{selected.vehicle_type}</b></div>
            <div>Total violations: <b>{selected.hits}</b></div>
            <div>Location concentration: <b>{Math.round(selected.concentration * 100)}%</b></div>
            {selected.is_fleet_pattern && <div style={{ color: "#0D47A1", fontWeight: 600 }}>Fixed-location pattern (likely fleet / stand)</div>}
          </div>
        </div>
      )}

      <div id="map" style={{ width: "100%", height: "100%" }} />
    </div>
  );
}

export default App;
