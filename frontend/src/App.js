// Gridlock Parking Intelligence — App.js with Ward side panel
// Replaces ALL of src/App.js. Delete src/HotspotMap.jsx.
// NO map-drawn wards (those crash Mappls). Wards are a safe HTML side panel.

import { mappls } from "mappls-web-maps";
import { useEffect, useRef, useState, useMemo } from "react";

const mapplsClassObject = new mappls();

// ─── Paste a FRESH token (regenerate in console — old ones expire in 24h) ───
const MAPPLS_TOKEN = "YOUR_MAPPLS_TOKEN_HERE";
// ─────────────────────────────────────────────────────────────────────────────

function App() {
  const mapRef = useRef(null);
  const layers = useRef({ heatmap: null, circles: [] });
  const [heat, setHeat] = useState([]);
  const [hotspots, setHotspots] = useState([]);
  const [mapReady, setMapReady] = useState(false);
  const [selected, setSelected] = useState(null);
  const [wardFilter, setWardFilter] = useState("ALL");
  const [show, setShow] = useState({ traffic: true, heatmap: true, circles: true });

  // load data
  useEffect(() => {
    fetch("/heatmap_points.json").then((r) => (r.ok ? r.json() : [])).then(setHeat).catch(() => {});
    fetch("/hotspots_full.json").then((r) => (r.ok ? r.json() : [])).then(setHotspots).catch(() => {});
  }, []);

  // group hotspots by ward for the side panel
  const wards = useMemo(() => {
    const m = {};
    hotspots.forEach((h) => {
      const w = h.ward_name || "Unknown";
      if (!m[w]) m[w] = [];
      m[w].push(h);
    });
    // sort wards by their top hotspot's impact
    return Object.entries(m)
      .map(([name, list]) => ({ name, list: list.sort((a, b) => b.impact_score - a.impact_score), top: Math.max(...list.map((x) => x.impact_score)) }))
      .sort((a, b) => b.top - a.top);
  }, [hotspots]);

  // visible hotspots = all, or filtered to a ward
  const visible = useMemo(
    () => (wardFilter === "ALL" ? hotspots : hotspots.filter((h) => (h.ward_name || "Unknown") === wardFilter)),
    [hotspots, wardFilter]
  );

  // init map — guarded so it runs EXACTLY once (React StrictMode double-mounts effects in dev,
  // which was creating two maps in one div and aborting tiles).
  const initedRef = useRef(false);
  useEffect(() => {
    if (initedRef.current) return;          // already initialized — skip the StrictMode second run
    initedRef.current = true;
    mapplsClassObject.initialize(MAPPLS_TOKEN, { map: true, layer: "vector", version: "3.0" }, () => {
      const map = mapplsClassObject.Map({
        id: "map",
        properties: { center: [12.9716, 77.5946], zoom: 12, traffic: true, zoomControl: true, scaleControl: true },
      });
      mapRef.current = map;
      map.on("load", () => { console.log("[map] load fired"); setMapReady(true); });
      setTimeout(() => { console.log("[map] fallback ready"); setMapReady(true); }, 2500);
    });
    // NOTE: intentionally NO cleanup that removes the map — removing it under StrictMode
    // double-mount is what aborted the tiles. The map lives for the page's lifetime.
  }, []);

  // draw heatmap + circles (circles respect the ward filter)
  useEffect(() => {
    const map = mapRef.current;
    if (!map || !mapReady) return;
    console.log("[draw] running — heat:", heat.length, "visible:", visible.length, "show:", show);
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
      const maxImpact = Math.max(...hotspots.map((h) => h.impact_score));
      const color = (i) => (i / maxImpact > 0.65 ? "#C62828" : i / maxImpact > 0.35 ? "#EF6C00" : "#F9A825");
      const radius = (i) => 250 + (i / maxImpact) * 500;
      visible.forEach((h) => {
        try {
          const c = mapplsClassObject.Circle({
            map, center: { lat: h.lat, lng: h.lng }, radius: radius(h.impact_score),
            fillColor: color(h.impact_score), fillOpacity: 0.45,
            strokeColor: color(h.impact_score), strokeOpacity: 0.9, strokeWeight: 2,
          });
          if (c && c.addListener) c.addListener("click", () => setSelected(h));
          layers.current.circles.push(c);
        } catch (e) { console.log("circle error", e); }
      });
    }

    // FIX: layers draw but stay invisible until the map repaints.
    // Force the map to re-measure + repaint so they show immediately.
    const repaint = () => {
      try {
        if (map.resize) map.resize();
        if (map.triggerRepaint) map.triggerRepaint();
        // nudge center by nothing to force a redraw on builds without triggerRepaint
        if (map.getCenter && map.setCenter) map.setCenter(map.getCenter());
      } catch (e) {}
    };
    repaint();
    setTimeout(repaint, 200);
    setTimeout(repaint, 600);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [heat, hotspots, visible, mapReady, show]);

  function clearLayers(map) {
    if (layers.current.heatmap) { try { mapplsClassObject.removeLayer({ map, layer: layers.current.heatmap }); } catch (e) {} layers.current.heatmap = null; }
    layers.current.circles.forEach((c) => { try { mapplsClassObject.removeLayer({ map, layer: c }); } catch (e) {} });
    layers.current.circles = [];
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

  return (
    <div style={{ position: "relative", width: "100%", height: "100vh", fontFamily: "system-ui, sans-serif" }}>
      {/* WARD SIDE PANEL (left) */}
      <div style={{ ...card, position: "absolute", top: 12, left: 12, zIndex: 999, padding: "14px 16px", width: 270, maxHeight: "90vh", overflowY: "auto" }}>
        <div style={{ fontWeight: 700, fontSize: 15, marginBottom: 4 }}>Enforcement Priorities</div>
        <div style={{ fontSize: 12, color: "#666", marginBottom: 10 }}>
          {heat.length} violations · {hotspots.length} hotspots · {wards.length} wards
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
              <span>{w.name}</span>
              <span style={{ color: "#888", fontWeight: 400 }}>{w.list.length}</span>
            </div>
            {wardFilter === w.name && w.list.map((h) => (
              <div key={h.priority_rank} onClick={() => setSelected(h)}
                style={{ padding: "6px 10px", fontSize: 12, borderTop: "1px solid #eee", cursor: "pointer", color: "#333" }}>
                #{h.priority_rank} {h.locality} — <b>{h.congestion_ratio}×</b>
              </div>
            ))}
          </div>
        ))}
      </div>

      {/* LAYER TOGGLES (right) */}
      <div style={{ ...card, position: "absolute", top: 12, right: 12, zIndex: 999, padding: "12px 16px", fontSize: 13, width: 190 }}>
        <strong style={{ display: "block", marginBottom: 8 }}>Layers</strong>
        {[["traffic", "Live traffic flow"], ["heatmap", "Violation heatmap"], ["circles", "Impact hotspots"]].map(([k, l]) => (
          <label key={k} style={{ display: "block", marginBottom: 6 }}>
            <input type="checkbox" checked={show[k]} onChange={() => setShow((s) => ({ ...s, [k]: !s[k] }))} /> {l}
          </label>
        ))}
        <div style={{ marginTop: 10, paddingTop: 8, borderTop: "1px solid #eee", fontSize: 12, color: "#555" }}>
          <span style={{ color: "#C62828" }}>●</span> High &nbsp; <span style={{ color: "#EF6C00" }}>●</span> Med &nbsp; <span style={{ color: "#F9A825" }}>●</span> Low
        </div>
        {!mapReady && <div style={{ marginTop: 8, color: "#E65100", fontSize: 12 }}>⏳ map loading… (if stuck, token may be expired)</div>}
      </div>

      {/* DETAIL PANEL */}
      {selected && (
        <div style={{ ...card, position: "absolute", bottom: 30, right: 12, zIndex: 999, padding: "16px 20px", fontSize: 14, width: 300 }}>
          <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 8 }}>
            <strong>#{selected.priority_rank} {selected.locality}</strong>
            <span onClick={() => setSelected(null)} style={{ cursor: "pointer", color: "#999" }}>✕</span>
          </div>
          <div style={{ fontSize: 13, color: "#444", lineHeight: 1.7 }}>
            <div>Ward: <b>{selected.ward_name || "—"}</b></div>
            <div>Violations: <b>{selected.violation_count}</b></div>
            <div>Live congestion: <b>{selected.congestion_ratio}×</b> normal</div>
            <div>Impact score: <b>{selected.impact_score}</b></div>
          </div>
        </div>
      )}

      <div id="map" style={{ width: "100%", height: "100%" }} />
    </div>
  );
}

export default App;