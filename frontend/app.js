/* =============================================================
   GeoEnv Platform v3.2 — Frontend logic
   Multi-point · Chart-first time control · ET / SM / Market
   ============================================================= */

const API_BASE   = window.location.origin;
const FIXED_SCALE = "1m";   // time-range lives IN the chart, not the panel

// ── State ──────────────────────────────────────────────────────
const state = {
  points:     [],   // [{ id, lat, lon, color, marker, result }]
  loading:    false,
  nextId:     1,
  activeId:   null,
  marketData: null,
};

const POINT_COLORS = ["#3b9eff", "#23d18b", "#e5b93c", "#f07b3a", "#e8425a"];

// ── Map setup ──────────────────────────────────────────────────
const map = L.map("map", {
  center: [-38.5, -65], zoom: 5,
  zoomControl: true, preferCanvas: true,
});

L.tileLayer(
  "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
  { attribution: "Tiles &copy; Esri", maxZoom: 19, crossOrigin: true }
).addTo(map);

L.tileLayer(
  "https://{s}.basemaps.cartocdn.com/rastertiles/voyager_only_labels/{z}/{x}/{y}{r}.png",
  { attribution: "&copy; CartoDB", subdomains: "abcd", maxZoom: 19, opacity: 0.9, pane: "shadowPane" }
).addTo(map);

setTimeout(() => map.invalidateSize(), 200);

// Argentina bounding-box guide — interactive:false so it NEVER absorbs clicks
L.rectangle([[-55.1, -73.6], [-21.8, -53.4]], {
  color: "#3b9eff", weight: 1, fill: false,
  dashArray: "5 5", opacity: 0.4,
  interactive: false,           // ← fix: never steal map-click events
}).addTo(map);

// ── Marker helpers ─────────────────────────────────────────────
function makeMarkerIcon(id, color) {
  return L.divIcon({
    className: "",
    html: `<div style="width:22px;height:22px;border-radius:50%;`
        + `background:${color};border:3px solid #fff;`
        + `box-shadow:0 0 14px ${color}bb;`
        + `display:flex;align-items:center;justify-content:center;`
        + `font-size:11px;font-weight:700;color:#000;`
        + `font-family:'Inter',sans-serif;line-height:1;">${id}</div>`,
    iconAnchor: [11, 11],
  });
}

// ── Point management ───────────────────────────────────────────
function addPoint(lat, lon) {
  // Single-point mode: remove existing point before adding new one
  if (state.points.length >= 1) {
    state.points[0].marker.remove();
    state.points.splice(0, 1);
  }
  const id     = state.nextId++;
  const color  = POINT_COLORS[0];
  const marker = L.marker([lat, lon], { icon: makeMarkerIcon(id, color) }).addTo(map);
  marker.on("click", (e) => { L.DomEvent.stop(e); removePoint(id); });
  state.points.push({ id, lat, lon, color, marker, result: null });
  state.activeId = id;
  updateControlPanel();
}

function removePoint(id) {
  const idx = state.points.findIndex(p => p.id === id);
  if (idx === -1) return;
  state.points[idx].marker.remove();
  state.points.splice(idx, 1);
  state.activeId = null;
  updateControlPanel();
  if (state.points.length === 0) {
    document.getElementById("dashboard").classList.remove("visible");
    document.getElementById("dashboard").style.display = "none";
  }
}

function setActivePoint(id) {
  state.activeId = id;
  updateControlPanel();
  renderPointTabs();
  const point = state.points.find(p => p.id === id);
  if (!point) return;
  if (point.result?._error) { showDashboardError(point.result._error); return; }
  if (point.result) renderIndexSections(point.result);
  // charts stay as-is — they already show all points
}

// ── Control panel ──────────────────────────────────────────────
function updateControlPanel() {
  const n       = state.points.length;
  const listEl  = document.getElementById("points-list");
  const countEl = document.getElementById("points-count");
  const btn     = document.getElementById("btn-analyze");

  if (!listEl || !btn) return;   // guard: DOM not ready yet

  countEl.textContent = "";

  if (n === 0) {
    listEl.innerHTML = `<div class="points-hint">Haga clic en el mapa para seleccionar un punto</div>`;
  } else {
    listEl.innerHTML = state.points.map(p => `
      <div class="point-row${p.result && !p.result._error ? " has-result" : ""}"
           role="listitem">
        <span class="point-badge" style="background:${p.color}">●</span>
        <span class="point-coord-pair">
          <span class="point-coord">${p.lat.toFixed(4)}</span>
          <span class="point-coord-sep">,</span>
          <span class="point-coord">${p.lon.toFixed(4)}</span>
        </span>
        <span class="point-state">
          ${state.loading && !p.result ? `<span class="point-spin"></span>` : ""}
          ${p.result && !p.result._error ? `<span class="point-done" title="Análisis listo">✓</span>` : ""}
          ${p.result && p.result._error  ? `<span class="point-err"  title="${esc(p.result._error)}">!</span>` : ""}
        </span>
        <button class="point-remove" onclick="removePoint(${p.id})"
                aria-label="Eliminar punto">×</button>
      </div>`).join("");
  }

  btn.disabled = (n === 0) || state.loading;
  btn.textContent = state.loading ? "Analizando…"
    : n === 0 ? "Seleccione un punto"
    : "Analizar punto";
}

// ── Map click ──────────────────────────────────────────────────
map.on("click", ({ latlng }) => {
  const { lat, lng } = latlng;
  // Argentina bounding box guard
  if (lat < -55.1 || lat > -21.8 || lng < -73.6 || lng > -53.4) return;
  if (state.loading) return;
  addPoint(parseFloat(lat.toFixed(5)), parseFloat(lng.toFixed(5)));
});

// ── Analyze button ─────────────────────────────────────────────
document.getElementById("btn-analyze").addEventListener("click", () => {
  if (state.points.length === 0 || state.loading) return;
  runAnalysis();
});

async function runAnalysis() {
  state.loading = true;
  updateControlPanel();
  showDashboard();
  showGlobalSpinner();

  for (const point of state.points) {
    if (point.result && !point.result._error) continue;   // skip cached
    updateControlPanel();   // show spinner on this point row
    try {
      const res = await fetch(`${API_BASE}/analyze`, {
        method:  "POST",
        headers: { "Content-Type": "application/json" },
        body:    JSON.stringify({ lat: point.lat, lon: point.lon, scale: FIXED_SCALE }),
      });
      if (!res.ok) {
        const err  = await res.json().catch(() => ({}));
        point.result = { _error: err.detail || `HTTP ${res.status}` };
      } else {
        point.result = await res.json();
      }
    } catch (e) {
      point.result = { _error: e.message };
    }
    updateControlPanel();
  }

  state.loading = false;

  const first = state.points.find(p => p.result && !p.result._error);
  if (first) {
    state.activeId = first.id;
    renderDashboardFull();
    fetchMarketData();
  } else {
    const errMsg = state.points.map(p => p.result?._error).filter(Boolean).join("; ");
    showDashboardError(errMsg || "Error desconocido");
  }

  updateControlPanel();
}

// ── Dashboard orchestration ────────────────────────────────────
function showDashboard() {
  const d = document.getElementById("dashboard");
  d.style.display = "";
  d.classList.add("visible");
  d.scrollIntoView({ behavior: "smooth", block: "start" });
}

function renderDashboardFull() {
  clearInterval(document._spinnerTicker);

  const point = state.points.find(p => p.id === state.activeId);
  if (!point?.result || point.result._error) return;

  renderIndexSections(point.result);
  if (state.marketData) renderMarket(state.marketData);
}

// ── Spinner & section clearing ─────────────────────────────────
function showGlobalSpinner() {
  clearAllSections();
  document.getElementById("sit-banner").innerHTML = `
    <div class="spinner-wrap">
      <div class="spin-ring" role="status" aria-label="Cargando análisis"></div>
      <div>
        <div class="spinner-text">Consultando Google Earth Engine…</div>
        <div class="spinner-sub">MODIS + CHIRPS + ET + SMAP · paralelo · 15–30 seg</div>
        <div class="spinner-steps" id="spinner-steps">
          <div class="spinner-step" data-step="veg">⏳ MOD13Q1 — NDVI / EVI</div>
          <div class="spinner-step" data-step="opt">⏳ MOD09A1 — NDWI / SAVI / NBR</div>
          <div class="spinner-step" data-step="lst">⏳ MOD11A2 — LST</div>
          <div class="spinner-step" data-step="pcp">⏳ CHIRPS — Precipitación</div>
          <div class="spinner-step" data-step="et"> ⏳ MOD16A2 — ET</div>
          <div class="spinner-step" data-step="sm"> ⏳ SMAP — Humedad suelo</div>
        </div>
      </div>
    </div>`;
  let i = 0;
  const steps = ["veg","opt","lst","pcp","et","sm"];
  clearInterval(document._spinnerTicker);
  document._spinnerTicker = setInterval(() => {
    if (i >= steps.length) { clearInterval(document._spinnerTicker); return; }
    const el = document.querySelector(`[data-step="${steps[i]}"]`);
    if (el) { el.textContent = el.textContent.replace("⏳","✅"); el.classList.add("done"); }
    i++;
  }, 2800);
}

/** Clear everything — called before a new analysis run */
function clearAllSections() {
  clearIndexSections();
}

/** Clear only per-point content — called on tab switch */
function clearIndexSections() {
  ["sit-banner","grid-veg","grid-water","grid-thermal","grid-hydro",
   "precip-card","static-ctx-bar","socio-section"].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.innerHTML = "";
  });
  const s = document.getElementById("section-hydro");
  const b = document.getElementById("static-ctx-bar");
  if (s) s.style.display = "none";
  if (b) b.style.display = "none";
  const btnR = document.getElementById("btn-report");
  const body = document.getElementById("report-body");
  if (btnR) btnR.disabled = true;
  if (body) body.innerHTML = `<span style="color:var(--text-dim);font-size:.77rem">Haga clic en "Generar Informe" para el análisis narrativo completo.</span>`;
}

function showDashboardError(msg) {
  clearIndexSections();
  document.getElementById("sit-banner").innerHTML = `
    <div class="error-wrap" role="alert">
      <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="#e8425a" stroke-width="1.5">
        <circle cx="12" cy="12" r="10"/>
        <line x1="12" y1="8" x2="12" y2="12"/>
        <circle cx="12" cy="16" r=".5" fill="#e8425a"/>
      </svg>
      <p>${esc(msg)}</p>
    </div>`;
}

// ── Index sections — per active point ─────────────────────────
function renderIndexSections(data) {
  clearIndexSections();
  clearInterval(document._spinnerTicker);
  const { meta, indices: idx, situation_indicator, socioeconomic: socio, static_context } = data;

  // Header chip
  const chip = document.getElementById("header-chip");
  if (chip) {
    chip.textContent = `${meta.region} · ${meta.current_month_name} · ${situation_indicator}`;
    chip.classList.add("visible");
  }

  // Situation banner
  document.getElementById("sit-banner").innerHTML = `
    <div class="sit-badge sit-${situation_indicator}" role="status">
      ${INDIC_ICON[situation_indicator] || ""} ${situation_indicator}
    </div>
    <div class="sit-meta">
      <div><strong>${meta.region}</strong> · ${meta.season}</div>
      <div>${meta.period_start} → ${meta.period_end}</div>
    </div>
    <div class="sit-elapsed" aria-label="Tiempo de respuesta">⏱ ${meta.elapsed_seconds ?? "—"}s</div>`;

  // Vegetation index cards
  document.getElementById("grid-veg").innerHTML =
    idxCard("NDVI", idx.ndvi, "Salud vegetal / biomasa") +
    idxCard("EVI",  idx.evi,  "Señal vegetal corregida") +
    idxCard("SAVI", idx.savi, "Veg. suelo expuesto");

  // Water & Drought index cards
  document.getElementById("grid-water").innerHTML =
    idxCard("NDWI",  idx.ndwi,  "Humedad canopeo") +
    idxCard("MNDWI", idx.mndwi, "Agua superficial") +
    idxCard("VCI",   idx.vci,   "Condición vs sequía") +
    idxCard("VHI",   idx.vhi,   "Salud ecosistema (VCI+TCI)/2");

  // Thermal & Fire index cards
  document.getElementById("grid-thermal").innerHTML =
    idxCard("LST", idx.lst, "Temperatura superficial", "°C") +
    idxCard("TCI", idx.tci, "Condición térmica") +
    idxCard("NBR", idx.nbr, "Degradación / fuego");

  // ET + Soil Moisture (optional)
  const hasET = idx.et && idx.et.current !== null;
  const hasSM = idx.sm && idx.sm.current !== null;
  if (hasET || hasSM) {
    document.getElementById("section-hydro").style.display = "";
    document.getElementById("grid-hydro").innerHTML =
      (hasET ? idxCard("ET", idx.et, "Evapotranspiración real", " mm/8d") : "") +
      (hasSM ? idxCard("SM", idx.sm, "Humedad superficial suelo", " mm")   : "");
  }

  // Precipitation
  renderPrecip(idx.precipitation);

  // Static context
  if (static_context) renderStaticContext(static_context);

  // Socioeconomic
  renderSocio(socio);

  // Enable report button
  const btnR = document.getElementById("btn-report");
  if (btnR) btnR.disabled = false;

  // Show market if already loaded
  if (state.marketData) renderMarket(state.marketData);
}

// ── Index card ────────────────────────────────────────────────
const ANOMALY_MAP = {
  "muy_alto":  { cls: "extreme",  color: "#e8425a", label: "Muy alto"   },
  "alto":      { cls: "moderate", color: "#e5b93c", label: "Alto"       },
  "normal":    { cls: "normal",   color: "#23d18b", label: "Normal"     },
  "bajo":      { cls: "moderate", color: "#e5b93c", label: "Bajo"       },
  "muy_bajo":  { cls: "extreme",  color: "#e8425a", label: "Muy bajo"   },
  "sin_datos": { cls: "nodata",   color: "#1c2d42", label: "Sin datos"  },
};

function idxCard(name, d, desc, unit = "") {
  if (!d) return "";
  const val = d.current;
  const z   = d.z_score;
  const pct = d.pct_deviation;
  const ac  = ANOMALY_MAP[d.anomaly_class] || ANOMALY_MAP["sin_datos"];

  // Get current-month percentiles from climatology
  const curMonth = new Date().getMonth() + 1;
  const mClim    = d.climatology?.[curMonth] || {};
  const p25 = mClim.p25, p50 = mClim.p50, p75 = mClim.p75;

  const zW         = z !== null ? Math.min(Math.abs(z) / 3 * 100, 100) : 0;
  const valDisplay = val !== null && val !== undefined
    ? `${val.toFixed(Math.abs(val) > 10 ? 1 : 3)}${unit}` : "N/D";
  const zDisplay   = z !== null && z !== undefined
    ? `${z > 0 ? "+" : ""}${z.toFixed(2)}σ` : "N/D";
  const pctDisplay = pct !== null && pct !== undefined
    ? ` · ${pct > 0 ? "+" : ""}${pct.toFixed(1)}%` : "";

  const p50Disp = p50 !== null && p50 !== undefined
    ? `p50 ${p50.toFixed(Math.abs(p50) > 10 ? 1 : 3)}${unit}` : "p50 N/D";
  const iqrDisp = (p25 !== null && p75 !== null)
    ? ` [${p25.toFixed(Math.abs(p25) > 10 ? 1 : 2)}–${p75.toFixed(Math.abs(p75) > 10 ? 1 : 2)}]` : "";

  return `
  <div class="idx-card z-${ac.cls}" role="article" aria-label="${name}: ${valDisplay}">
    <div class="idx-name">${name}</div>
    <div class="idx-desc" title="${desc}">${desc}</div>
    <div class="idx-value" style="color:${val !== null ? ac.color : "var(--text-dim)"}">${valDisplay}</div>
    <div class="idx-badge badge-${ac.cls}">${ac.label}</div>
    <div class="idx-stats">
      ${p50Disp}<span class="iqr-range">${iqrDisp}</span><br>
      <span class="z-val">${zDisplay}</span>${pctDisplay}
    </div>
    <div class="z-track" aria-hidden="true">
      <div class="z-fill" style="width:${zW}%;background:${ac.color}"></div>
    </div>
  </div>`;
}

// ── Precipitation card ────────────────────────────────────────
function renderPrecip(p) {
  const container = document.getElementById("precip-card");
  if (!p || p.current_mm === null) {
    container.innerHTML = `<p style="color:var(--text-dim);font-size:.77rem;padding:0 0 16px">Datos de precipitación no disponibles.</p>`;
    return;
  }
  const ac     = ANOMALY_MAP[p.anomaly_class] || ANOMALY_MAP["Sin datos"];
  const zFmt   = p.z_score !== null ? `${p.z_score > 0 ? "+" : ""}${p.z_score.toFixed(2)}σ` : "N/D";
  const pctFmt = p.pct_deviation !== null ? ` (${p.pct_deviation > 0 ? "+" : ""}${p.pct_deviation.toFixed(1)}%)` : "";

  container.innerHTML = `
    <div class="precip-card">
      <div class="precip-main">
        <div class="precip-label">Precipitación acumulada (${p.analysis_days} días)</div>
        <div class="precip-val" style="color:${ac.color}">${p.current_mm} mm</div>
      </div>
      <div class="precip-compare">
        Media histórica: <strong>${p.hist_mean_mm} mm</strong>${pctFmt}<br>
        Z-score: <strong>${zFmt}</strong>
      </div>
      <div class="precip-badge">
        <span class="idx-badge badge-${ac.cls}">${p.anomaly_class}</span>
      </div>
    </div>`;
}

// ── Static context (topography) ───────────────────────────────
function slopeLabel(deg) {
  if (deg === null) return "";
  if (deg < 1)   return "plano";
  if (deg < 5)   return "suave";
  if (deg < 15)  return "moderado";
  return "fuerte";
}
function twiLabel(twi) {
  if (twi === null) return "";
  if (twi < 5)  return "bien drenado";
  if (twi < 8)  return "moderado";
  return "susceptible a anegamiento";
}
function curvLabel(c) {
  if (c === null) return "";
  if (c < -0.5) return "cóncavo";
  if (c > 0.5)  return "convexo";
  return "plano";
}

function renderStaticContext(ctx) {
  const bar = document.getElementById("static-ctx-bar");
  if (!ctx) return;

  const chips = [
    ctx.elevation_m !== null
      ? `<div class="terrain-chip"><div class="terrain-chip-icon">🏔</div>
         <div class="terrain-chip-body"><div class="terrain-chip-val">${ctx.elevation_m} m</div>
         <div class="terrain-chip-label">Altitud</div></div></div>` : null,

    ctx.slope_deg !== null
      ? `<div class="terrain-chip"><div class="terrain-chip-icon">📐</div>
         <div class="terrain-chip-body"><div class="terrain-chip-val">${ctx.slope_deg}°</div>
         <div class="terrain-chip-label">Pendiente · ${slopeLabel(ctx.slope_deg)}</div></div></div>` : null,

    ctx.hand_m !== null
      ? `<div class="terrain-chip"><div class="terrain-chip-icon">🌊</div>
         <div class="terrain-chip-body"><div class="terrain-chip-val">${ctx.hand_m} m</div>
         <div class="terrain-chip-label">HAND (drenaje)</div></div></div>` : null,

    ctx.twi !== null && ctx.twi !== undefined
      ? `<div class="terrain-chip"><div class="terrain-chip-icon">💧</div>
         <div class="terrain-chip-body"><div class="terrain-chip-val">${ctx.twi}</div>
         <div class="terrain-chip-label">TWI · ${twiLabel(ctx.twi)}</div></div></div>` : null,

    ctx.curvature !== null && ctx.curvature !== undefined
      ? `<div class="terrain-chip"><div class="terrain-chip-icon">↩</div>
         <div class="terrain-chip-body"><div class="terrain-chip-val">${ctx.curvature > 0 ? "+" : ""}${ctx.curvature}</div>
         <div class="terrain-chip-label">Curvatura · ${curvLabel(ctx.curvature)}</div></div></div>` : null,
  ].filter(Boolean);

  if (!chips.length) return;
  bar.style.display = "flex";
  bar.innerHTML = `
    <div class="terrain-header">
      <span class="idx-section-icon" aria-hidden="true">🗺</span>
      <span class="idx-section-title" style="font-size:.82rem">Topografía estática</span>
      <span class="idx-section-sub">MERIT/SRTM · 90 m</span>
    </div>
    <div class="terrain-chips">${chips.join("")}</div>`;
}

// ── Socioeconomic ─────────────────────────────────────────────
function renderSocio(socio) {
  const el    = document.getElementById("socio-section");
  const crops = socio.agriculture.crops_at_risk;
  const cropTags = crops.length
    ? crops.map(c => `<span class="crop-tag">${esc(c)}</span>`).join("")
    : `<span style="color:var(--text-dim);font-size:.72rem">Ninguno identificado</span>`;

  el.innerHTML = `
    <div class="idx-section-header" style="margin-bottom:14px">
      <span class="idx-section-icon" aria-hidden="true">💼</span>
      <span class="idx-section-title">Contexto Socioeconómico</span>
    </div>
    <div class="socio-grid">
      <div class="socio-cell">
        <div class="socio-cell-label">Producción agropecuaria</div>
        <div class="socio-cell-body">${esc(socio.agriculture.assessment)}</div>
        ${crops.length ? `<div class="crops-list">${cropTags}</div>` : ""}
      </div>
      <div class="socio-cell">
        <div class="socio-cell-label">Recurso hídrico &amp; Precipitación</div>
        <div class="socio-cell-body">${esc(socio.water)}<br><br>${esc(socio.precipitation)}</div>
      </div>
      <div class="socio-cell">
        <div class="socio-cell-label">Contexto térmico</div>
        <div class="socio-cell-body">${esc(socio.thermal)}</div>
      </div>
    </div>
    <div class="causality-box" role="note" aria-label="Cadena causal de impacto">
      ${esc(socio.causality_chain)}
    </div>
    <details>
      <summary style="font-size:.68rem;color:var(--text-dim);cursor:pointer;margin-bottom:6px">
        Supuestos y fuentes
      </summary>
      <ul class="assumptions-box">
        ${socio.assumptions.map(a => `<li>${esc(a)}</li>`).join("")}
      </ul>
    </details>`;
}

// ── Market data ───────────────────────────────────────────────
async function fetchMarketData() {
  try {
    const res = await fetch(`${API_BASE}/market`);
    if (!res.ok) return;
    state.marketData = await res.json();
    renderMarket(state.marketData);
  } catch (_) { /* optional — fail silently */ }
}

function renderMarket(data) {
  const section = document.getElementById("market-section");
  const grid    = document.getElementById("market-grid");
  const ts      = document.getElementById("market-ts");
  if (!data) return;

  ts.textContent = data._timestamp ? `actualizado ${data._timestamp.slice(0, 10)}` : "";

  const fx     = data.fx     || {};
  const granos = data.granos || {};
  const macro  = data.macro  || {};
  const siem   = data.siembra || {};

  let html = "";

  // ── FX row ──
  const fxItems = [
    { key:"oficial", icon:"💵", label:"Oficial"  },
    { key:"ccl",     icon:"💹", label:"CCL"      },
    { key:"mep",     icon:"📊", label:"MEP"      },
    { key:"blue",    icon:"💙", label:"Blue"     },
  ].filter(f => fx[f.key]?.venta);

  if (fxItems.length) {
    const oficialVenta = fx.oficial?.venta;
    html += `<div class="market-row-label">💱 Tipos de cambio</div>
    <div class="market-ticker-row">` +
      fxItems.map(f => {
        const venta = fx[f.key].venta;
        const spread = (oficialVenta && f.key !== "oficial")
          ? ` <span class="spread-chip">${((venta/oficialVenta - 1)*100).toFixed(0)}%</span>` : "";
        return `<div class="market-chip">
          <div class="market-chip-title">${f.icon} ${f.label}</div>
          <div class="market-chip-val">$${venta.toLocaleString("es-AR")}</div>
          <div class="market-chip-sub">venta${spread}</div>
        </div>`;
      }).join("") + `</div>`;
  }

  // ── Granos FAS row ──
  const granosItems = [
    { key:"soja_fas",    icon:"🌱", label:"Soja"    },
    { key:"maiz_fas",    icon:"🌽", label:"Maíz"    },
    { key:"trigo_fas",   icon:"🌾", label:"Trigo"   },
    { key:"girasol_fas", icon:"🌻", label:"Girasol" },
  ].filter(g => granos[g.key]?.valor);

  if (granosItems.length) {
    html += `<div class="market-row-label">🌾 Precios FAS (MAGyP)</div>
    <div class="market-ticker-row">` +
      granosItems.map(g => {
        const v = granos[g.key];
        return `<div class="market-chip">
          <div class="market-chip-title">${g.icon} ${g.label}</div>
          <div class="market-chip-val" style="color:var(--green)">$${Math.round(v.valor).toLocaleString("es-AR")}</div>
          <div class="market-chip-sub">$/tn · ${v.fecha?.slice(0,7) || ""}</div>
        </div>`;
      }).join("") + `</div>`;
  }

  // ── Macro row ──
  const macroChips = [];
  if (macro.badlar?.valor) macroChips.push(
    `<div class="market-chip"><div class="market-chip-title">📈 BADLAR</div>
     <div class="market-chip-val" style="color:var(--yellow)">${macro.badlar.valor.toFixed(1)}%</div>
     <div class="market-chip-sub">n.a. bancos priv.</div></div>`
  );
  if (macro.ipc_ng?.valor) macroChips.push(
    `<div class="market-chip"><div class="market-chip-title">🧾 IPC m/m</div>
     <div class="market-chip-val" style="color:var(--yellow)">${macro.ipc_ng.valor.toFixed(1)}%</div>
     <div class="market-chip-sub">nivel gral INDEC · ${macro.ipc_ng.fecha?.slice(0,7) || ""}</div></div>`
  );
  if (macro.cer?.valor) macroChips.push(
    `<div class="market-chip"><div class="market-chip-title">📉 CER</div>
     <div class="market-chip-val">${macro.cer.valor.toLocaleString("es-AR")}</div>
     <div class="market-chip-sub">índice BCRA · ${macro.cer.fecha?.slice(0,7) || ""}</div></div>`
  );
  if (macroChips.length) {
    html += `<div class="market-row-label">📊 Macro</div>
    <div class="market-ticker-row">${macroChips.join("")}</div>`;
  }

  // ── Avance siembra ──
  const siembraItems = [
    { key:"soja_siembra_pct",    icon:"🌱", label:"Soja"    },
    { key:"maiz_siembra_pct",    icon:"🌽", label:"Maíz"    },
    { key:"trigo_siembra_pct",   icon:"🌾", label:"Trigo"   },
    { key:"girasol_siembra_pct", icon:"🌻", label:"Girasol" },
  ].filter(s => siem[s.key]?.valor !== undefined && siem[s.key]?.valor !== null);

  if (siembraItems.length) {
    html += `<div class="market-row-label">🌱 Avance de siembra</div>
    <div class="campaign-grid">` +
      siembraItems.map(s => {
        const pct = Math.min(100, Math.round(siem[s.key].valor));
        return `<div class="campaign-row">
          <span class="campaign-label">${s.icon} ${s.label}</span>
          <progress class="campaign-bar" value="${pct}" max="100"></progress>
          <span class="campaign-pct">${pct}%</span>
        </div>`;
      }).join("") + `</div>`;
  }

  if (!html) { section.style.display = "none"; return; }
  grid.innerHTML = html;
  section.style.display = "";
}

// ── AI Report ─────────────────────────────────────────────────
document.getElementById("btn-report").addEventListener("click", generateReport);

async function generateReport() {
  const point = state.points.find(p => p.id === state.activeId);
  if (!point?.result || point.result._error) return;

  const btn  = document.getElementById("btn-report");
  const body = document.getElementById("report-body");
  btn.disabled    = true;
  btn.textContent = "Generando…";
  body.innerHTML  = `<span class="cursor"></span>`;

  let fullText   = "";
  let reportDone = false;
  let reportError = null;

  try {
    const res = await fetch(`${API_BASE}/report`, {
      method:  "POST",
      headers: { "Content-Type": "application/json" },
      body:    JSON.stringify(point.result),
    });
    if (!res.ok) throw new Error(`HTTP ${res.status}`);

    const reader  = res.body.getReader();
    const decoder = new TextDecoder();
    let currentEvent = null;

    outer: while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      const lines = decoder.decode(value, { stream: true }).split("\n");
      for (const line of lines) {
        if (line.startsWith("event: ")) { currentEvent = line.slice(7).trim(); continue; }
        if (line === "")               { currentEvent = null; continue; }
        if (!line.startsWith("data: ")) continue;
        const payload = line.slice(6).trim();
        if (payload === "[DONE]") { reportDone = true; break outer; }
        try {
          const parsed = JSON.parse(payload);
          if (currentEvent === "error") {
            reportError = parsed.message || "Error desconocido.";
            break outer;
          }
          fullText += parsed.chunk || "";
          body.innerHTML = marked.parse(fullText) + `<span class="cursor" aria-hidden="true"></span>`;
          body.scrollIntoView({ behavior: "smooth", block: "end" });
        } catch { /* partial chunk — ignore */ }
        currentEvent = null;
      }
    }

    if (fullText) body.innerHTML = marked.parse(fullText);
    if (reportError) {
      body.innerHTML =
        `<div class="report-error-banner" role="alert"><strong>⚠ Error al generar el informe</strong><br>${esc(reportError)}</div>` +
        (fullText ? marked.parse(fullText) : "");
    }
    if (!reportDone && !reportError && fullText) {
      body.innerHTML = marked.parse(fullText) +
        `<div class="report-incomplete-banner" role="status">⚠ <em>El informe fue interrumpido antes de completarse.</em></div>`;
    }
  } catch (e) {
    body.innerHTML = `<p style="color:var(--red)">Error: ${esc(e.message)}</p>`;
  } finally {
    btn.disabled    = false;
    btn.textContent = "Regenerar Informe";
  }
}

// ── Helpers ───────────────────────────────────────────────────
const INDIC_ICON = {
  CRÍTICO: "🔴", ALERTA: "🟠", NORMAL: "🔵", FAVORABLE: "🟢", INDETERMINADO: "⚫",
};

function esc(str) {
  if (!str) return "";
  return str.replace(/&/g,"&amp;").replace(/</g,"&lt;")
            .replace(/>/g,"&gt;").replace(/"/g,"&quot;");
}
