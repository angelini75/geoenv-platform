/* =============================================================
   GeoEnv Platform v3 — Frontend logic
   Multi-point analysis · Market context · ET / Soil Moisture
   ============================================================= */

const API_BASE = window.location.origin;

// ── State ──────────────────────────────────────────────────────
const state = {
  points:   [],     // [{ id, lat, lon, color, marker, result }]
  scale:    "1m",
  loading:  false,
  nextId:   1,
  activeId: null,   // ID of point whose results are currently shown
  marketData: null,
};

// Colors cycling through 5 positions (accent, green, yellow, orange, red)
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

// Argentina bounding box guide
L.rectangle([[-55.1, -73.6], [-21.8, -53.4]], {
  color: "#3b9eff", weight: 1, fill: false, dashArray: "5 5", opacity: 0.4,
}).addTo(map);

// ── Marker helpers ─────────────────────────────────────────────
function makeMarkerIcon(id, color) {
  return L.divIcon({
    className: "",
    html: `<div style="
      width:22px;height:22px;border-radius:50%;
      background:${color};border:3px solid #fff;
      box-shadow:0 0 14px ${color}bb;
      display:flex;align-items:center;justify-content:center;
      font-size:11px;font-weight:700;color:#000;
      font-family:'Inter',sans-serif;line-height:1;
    ">${id}</div>`,
    iconAnchor: [11, 11],
  });
}

// ── Point management ───────────────────────────────────────────
function addPoint(lat, lon) {
  if (state.points.length >= 5) return;
  const id    = state.nextId++;
  const color = POINT_COLORS[(id - 1) % 5];
  const marker = L.marker([lat, lon], { icon: makeMarkerIcon(id, color) }).addTo(map);
  // Click on marker = remove it
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
  // Switch active to last point with results, or last point, or null
  if (state.activeId === id) {
    const withResult = [...state.points].reverse().find(p => p.result && !p.result._error);
    state.activeId = withResult
      ? withResult.id
      : state.points.length > 0 ? state.points[state.points.length - 1].id : null;
  }
  updateControlPanel();
  const hasResults = state.points.some(p => p.result && !p.result._error);
  if (hasResults) {
    renderActivePoint();
  } else if (state.points.length === 0) {
    document.getElementById("dashboard").classList.remove("visible");
    document.getElementById("dashboard").style.display = "none";
    document.getElementById("point-tabs-bar").style.display = "none";
  }
}

function setActivePoint(id) {
  state.activeId = id;
  updateControlPanel();
  renderActivePoint();
}

// ── Control panel ──────────────────────────────────────────────
function updateControlPanel() {
  const n       = state.points.length;
  const listEl  = document.getElementById("points-list");
  const countEl = document.getElementById("points-count");
  const btn     = document.getElementById("btn-analyze");

  countEl.textContent = n > 0 ? `(${n}/5)` : "";

  if (n === 0) {
    listEl.innerHTML = `<div class="points-hint">Haga clic en el mapa · hasta 5 puntos</div>`;
  } else {
    listEl.innerHTML = state.points.map(p => `
      <div class="point-row${p.id === state.activeId ? " is-active" : ""}${p.result && !p.result._error ? " has-result" : ""}"
           role="listitem"
           onclick="setActivePoint(${p.id})">
        <span class="point-badge" style="background:${p.color}">${p.id}</span>
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
        <button class="point-remove" onclick="event.stopPropagation();removePoint(${p.id})"
                aria-label="Eliminar punto ${p.id}">×</button>
      </div>`).join("");
  }

  btn.disabled = n === 0 || state.loading;
  btn.textContent = state.loading ? "Analizando…"
    : n === 0 ? "Seleccione un punto"
    : n === 1 ? "Analizar punto"
    : `Analizar ${n} puntos`;
}

// ── Map click ─────────────────────────────────────────────────
map.on("click", ({ latlng }) => {
  const { lat, lng } = latlng;
  if (lat < -55.1 || lat > -21.8 || lng < -73.6 || lng > -53.4) return;
  if (state.loading) return;
  if (state.points.length >= 5) return;
  const la = parseFloat(lat.toFixed(5));
  const lo = parseFloat(lng.toFixed(5));
  addPoint(la, lo);
  document.getElementById("btn-analyze").disabled = false;
});

// ── Scale buttons ──────────────────────────────────────────────
document.querySelectorAll(".scale-btn").forEach(btn => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".scale-btn").forEach(b => {
      b.classList.remove("active"); b.setAttribute("aria-pressed", "false");
    });
    btn.classList.add("active"); btn.setAttribute("aria-pressed", "true");
    state.scale = btn.dataset.scale;
  });
});

// ── Analyze ───────────────────────────────────────────────────
document.getElementById("btn-analyze").addEventListener("click", () => {
  if (state.points.length === 0 || state.loading) return;
  runAnalysis();
});

async function runAnalysis() {
  state.loading = true;
  updateControlPanel();
  showDashboard();
  showGlobalSpinner();

  // Analyze points sequentially (GEE quota protection)
  for (const point of state.points) {
    if (point.result && !point.result._error) continue; // skip already done
    updateControlPanel(); // show spinner on this point
    try {
      const res = await fetch(`${API_BASE}/analyze`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ lat: point.lat, lon: point.lon, scale: state.scale }),
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
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

  // Activate first successful result
  const first = state.points.find(p => p.result && !p.result._error);
  if (first) {
    state.activeId = first.id;
    renderActivePoint();
    fetchMarketData();
  } else {
    const errMsg = state.points.map(p => p.result?._error).filter(Boolean).join("; ");
    showDashboardError(errMsg || "Error desconocido");
  }

  updateControlPanel();
}

// ── Dashboard ─────────────────────────────────────────────────
function showDashboard() {
  const d = document.getElementById("dashboard");
  d.style.display = "";
  d.classList.add("visible");
  d.scrollIntoView({ behavior: "smooth", block: "start" });
}

function showGlobalSpinner() {
  clearSections();
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

function clearSections() {
  ["sit-banner","grid-veg","charts-veg","grid-water","charts-water",
   "grid-thermal","charts-thermal","grid-hydro","precip-card",
   "static-ctx-bar","socio-section"].forEach(id => {
    const el = document.getElementById(id);
    if (el) el.innerHTML = "";
  });
  document.getElementById("section-hydro").style.display = "none";
  document.getElementById("static-ctx-bar").style.display = "none";
}

function showDashboardError(msg) {
  clearSections();
  document.getElementById("sit-banner").innerHTML = `
    <div class="error-wrap" role="alert">
      <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="#e8425a" stroke-width="1.5">
        <circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/>
        <circle cx="12" cy="16" r=".5" fill="#e8425a"/>
      </svg>
      <p>${esc(msg)}</p>
    </div>`;
}

function renderActivePoint() {
  clearInterval(document._spinnerTicker);
  renderPointTabs();

  const point = state.points.find(p => p.id === state.activeId);
  if (!point) return;
  if (point.result?._error) { showDashboardError(point.result._error); return; }
  if (point.result) renderDashboard(point.result);
}

// ── Point tabs ────────────────────────────────────────────────
function renderPointTabs() {
  const bar = document.getElementById("point-tabs-bar");
  const withResults = state.points.filter(p => p.result);
  if (withResults.length <= 1) { bar.style.display = "none"; return; }
  bar.style.display = "flex";
  bar.innerHTML = withResults.map(p => `
    <button class="point-tab${p.id === state.activeId ? " active" : ""}"
            role="tab" aria-selected="${p.id === state.activeId}"
            style="--tab-color:${p.color}"
            onclick="setActivePoint(${p.id})">
      <span class="tab-dot" style="background:${p.color}"></span>
      Punto ${p.id}
      ${p.result._error ? `<span class="tab-err">!</span>` : ""}
    </button>`).join("");
}

// ── Main render ───────────────────────────────────────────────
function renderDashboard(data) {
  clearInterval(document._spinnerTicker);
  const { meta, indices: idx, situation_indicator, socioeconomic: socio, static_context } = data;

  // Header chip — last active point
  const chip = document.getElementById("header-chip");
  chip.textContent = `${meta.region} · ${scaleFmt(meta.scale)} · ${situation_indicator}`;
  chip.classList.add("visible");

  // Situation banner
  document.getElementById("sit-banner").innerHTML = `
    <div class="sit-badge sit-${situation_indicator}" role="status">
      ${INDIC_ICON[situation_indicator] || ""} ${situation_indicator}
    </div>
    <div class="sit-meta">
      <div><strong>${meta.region}</strong> · ${meta.season}</div>
      <div>${meta.period_start} → ${meta.period_end}</div>
      <div>Escala: ${scaleFmt(meta.scale)}</div>
    </div>
    <div class="sit-elapsed" aria-label="Tiempo de respuesta">⏱ ${meta.elapsed_seconds}s</div>`;

  // Vegetation
  document.getElementById("grid-veg").innerHTML =
    idxCard("NDVI", idx.ndvi, "Salud vegetal / biomasa") +
    idxCard("EVI",  idx.evi,  "Señal vegetal corregida") +
    idxCard("SAVI", idx.savi, "Veg. suelo expuesto");
  renderCharts("charts-veg", [
    { key:"ndvi", label:"NDVI", candles:idx.ndvi.candlesticks, color:"#23d18b" },
    { key:"evi",  label:"EVI",  candles:idx.evi.candlesticks,  color:"#18c2b4" },
    { key:"savi", label:"SAVI", candles:idx.savi.candlesticks, color:"#9b72f5" },
  ]);

  // Water & Drought
  document.getElementById("grid-water").innerHTML =
    idxCard("NDWI",  idx.ndwi,  "Humedad canopeo") +
    idxCard("MNDWI", idx.mndwi, "Agua superficial") +
    idxCard("VCI",   idx.vci,   "Condición vs sequía") +
    idxCard("VHI",   idx.vhi,   "Salud ecosistema (VCI+TCI)/2");
  renderCharts("charts-water", [
    { key:"ndwi",  label:"NDWI",  candles:idx.ndwi.candlesticks,  color:"#3b9eff" },
    { key:"mndwi", label:"MNDWI", candles:idx.mndwi.candlesticks, color:"#9b72f5" },
    { key:"vci",   label:"VCI",   candles:idx.vci.candlesticks,   color:"#23d18b" },
  ]);

  // Thermal & Fire
  document.getElementById("grid-thermal").innerHTML =
    idxCard("LST", idx.lst, "Temperatura superficial", "°C") +
    idxCard("TCI", idx.tci, "Condición térmica") +
    idxCard("NBR", idx.nbr, "Degradación / fuego");
  renderCharts("charts-thermal", [
    { key:"lst", label:"LST (°C)", candles:idx.lst.candlesticks, color:"#f07b3a" },
    { key:"tci", label:"TCI",      candles:idx.tci.candlesticks, color:"#e5b93c" },
    { key:"nbr", label:"NBR",      candles:idx.nbr.candlesticks, color:"#e8425a" },
  ]);

  // ET + Soil Moisture (optional)
  const hasET = idx.et  && idx.et.current  !== null;
  const hasSM = idx.sm  && idx.sm.current  !== null;
  if (hasET || hasSM) {
    const hydroSection = document.getElementById("section-hydro");
    const hydroGrid    = document.getElementById("grid-hydro");
    hydroSection.style.display = "";
    hydroGrid.innerHTML =
      (hasET ? idxCard("ET", idx.et, "Evapotranspiración real", " mm/8d") : "") +
      (hasSM ? idxCard("SM", idx.sm, "Humedad superficial suelo", " mm")   : "");
  }

  // Precipitation
  renderPrecip(idx.precipitation);

  // Static context (HAND, elevation, slope)
  if (static_context) renderStaticContext(static_context);

  // Socioeconomic
  renderSocio(socio);

  // Enable report
  document.getElementById("btn-report").disabled = false;
  document.getElementById("report-body").innerHTML =
    `<span style="color:var(--text-dim);font-size:.77rem">Haga clic en "Generar Informe" para el análisis narrativo completo.</span>`;

  // Show market if already loaded
  if (state.marketData) renderMarket(state.marketData);
}

// ── Index card ────────────────────────────────────────────────
const ANOMALY_MAP = {
  "Normal":            { cls: "normal",   color: "#23d18b" },
  "Anomalía moderada": { cls: "moderate", color: "#e5b93c" },
  "Anomalía extrema":  { cls: "extreme",  color: "#e8425a" },
  "Sin datos":         { cls: "nodata",   color: "#1c2d42" },
};

function idxCard(name, d, desc, unit = "") {
  if (!d) return "";
  const val = d.current;
  const z   = d.z_score;
  const pct = d.pct_deviation;
  const ac  = ANOMALY_MAP[d.anomaly_class] || ANOMALY_MAP["Sin datos"];

  const zW         = z !== null ? Math.min(Math.abs(z) / 3 * 100, 100) : 0;
  const valDisplay = val !== null && val !== undefined
    ? `${val.toFixed(Math.abs(val) > 10 ? 1 : 3)}${unit}` : "N/D";
  const zDisplay   = z !== null && z !== undefined
    ? `${z > 0 ? "+" : ""}${z.toFixed(2)}σ` : "N/D";
  const pctDisplay = pct !== null && pct !== undefined
    ? ` · ${pct > 0 ? "+" : ""}${pct.toFixed(1)}%` : "";
  const meanDisplay = d.hist_mean !== null && d.hist_mean !== undefined
    ? `μ ${d.hist_mean.toFixed(Math.abs(d.hist_mean) > 10 ? 1 : 3)}${unit}` : "μ N/D";
  const stdDisplay  = d.hist_std !== null && d.hist_std !== undefined
    ? ` · σ ${d.hist_std.toFixed(Math.abs(d.hist_std) > 10 ? 1 : 3)}` : "";

  return `
  <div class="idx-card z-${ac.cls}" role="article" aria-label="${name}: ${valDisplay}">
    <div class="idx-name">${name}</div>
    <div class="idx-desc" title="${desc}">${desc}</div>
    <div class="idx-value" style="color:${val !== null ? ac.color : "var(--text-dim)"}">${valDisplay}</div>
    <div class="idx-badge badge-${ac.cls}">${d.anomaly_class}</div>
    <div class="idx-stats">
      ${meanDisplay}${stdDisplay}<br>
      <span class="z-val">${zDisplay}</span>${pctDisplay}
    </div>
    <div class="z-track" aria-hidden="true">
      <div class="z-fill" style="width:${zW}%;background:${ac.color}"></div>
    </div>
  </div>`;
}

// ── Charts ────────────────────────────────────────────────────
const _lwCharts = {};

function renderCharts(containerId, series) {
  const active = series.filter(s => s.candles && s.candles.length > 0);
  const container = document.getElementById(containerId);
  container.innerHTML = active.map(s => {
    const first = s.candles[0]?.period?.slice(0, 7) ?? "";
    const last  = s.candles[s.candles.length - 1]?.period?.slice(0, 7) ?? "";
    return `
      <div class="chart-card">
        <div class="chart-header">
          <span>${s.label} — Velas mensuales</span>
          <span>${first} → ${last} · ${s.candles.length} meses</span>
        </div>
        <div class="chart-wrap" id="chart-${s.key}"></div>
      </div>`;
  }).join("");

  requestAnimationFrame(() => active.forEach(s => plotCandleLW(s.key, s.candles, s.color)));
}

function plotCandleLW(key, candles, accentColor) {
  const el = document.getElementById(`chart-${key}`);
  if (!el || !candles.length) return;
  if (_lwCharts[key]) { try { _lwCharts[key].remove(); } catch (_) {} }
  el.innerHTML = "";

  const chart = LightweightCharts.createChart(el, {
    layout: { background: { type: "solid", color: "#0e1520" }, textColor: "#7a90aa", fontSize: 10 },
    grid: { vertLines: { color: "#1c2d42", style: 1 }, horzLines: { color: "#1c2d42", style: 1 } },
    crosshair: { mode: LightweightCharts.CrosshairMode.Normal },
    rightPriceScale: { borderColor: "#243650", scaleMargins: { top: 0.08, bottom: 0.08 } },
    timeScale: { borderColor: "#243650", fixLeftEdge: true, fixRightEdge: true },
    width: el.clientWidth || 400, height: el.clientHeight || 230,
  });
  _lwCharts[key] = chart;
  new ResizeObserver(() => chart.applyOptions({ width: el.clientWidth })).observe(el);

  const series = chart.addCandlestickSeries({
    upColor:"#23d18b", downColor:"#e8425a",
    borderUpColor:"#23d18b", borderDownColor:"#e8425a",
    wickUpColor:"#23d18b", wickDownColor:"#e8425a",
  });
  series.setData(candles.map(c => ({
    time: c.period, open: c.open, high: c.high, low: c.low, close: c.close,
  })));

  const markers = candles
    .filter(c => c.z_close !== null && Math.abs(c.z_close) >= 1.5)
    .map(c => ({
      time: c.period,
      position: c.z_close > 0 ? "aboveBar" : "belowBar",
      color: Math.abs(c.z_close) >= 2.5 ? "#e8425a" : "#e5b93c",
      shape: "circle", size: 1,
      text: `z ${c.z_close > 0 ? "+" : ""}${c.z_close.toFixed(1)}σ`,
    })).sort((a, b) => a.time.localeCompare(b.time));
  if (markers.length) series.setMarkers(markers);

  chart.timeScale().fitContent();
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
function renderStaticContext(ctx) {
  const bar = document.getElementById("static-ctx-bar");
  const items = [
    ctx.elevation_m !== null ? `🏔 <strong>${ctx.elevation_m} m</strong> elevación` : null,
    ctx.hand_m      !== null ? `🌊 <strong>${ctx.hand_m} m</strong> HAND` : null,
    ctx.slope_deg   !== null ? `📐 <strong>${ctx.slope_deg}°</strong> pendiente` : null,
  ].filter(Boolean);
  if (!items.length) return;
  bar.style.display = "flex";
  bar.innerHTML = `
    <span class="ctx-label">Topografía estática</span>
    ${items.map(i => `<span class="ctx-chip">${i}</span>`).join("")}
    <span class="ctx-source">MERIT/SRTM · 90 m</span>`;
}

// ── Socioeconomic ─────────────────────────────────────────────
function renderSocio(socio) {
  const el = document.getElementById("socio-section");
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
      <div class="socio-cell">
        <div class="socio-cell-label">Contexto macro (estimado)</div>
        <div class="socio-cell-body">${esc(socio.macro)}</div>
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

  ts.textContent = data._timestamp ? `actualizado ${data._timestamp.slice(0,10)}` : "";

  const cards = [];

  // USD oficial
  const usd = data.usd_oficial;
  if (usd?.venta) {
    cards.push(marketCard("💵 Dólar oficial", `$${usd.venta.toLocaleString("es-AR")}`, "venta BNA", usd.fecha, "var(--accent)"));
  }

  // BCRA vars
  const vars = data.bcra_vars || {};
  if (vars.badlar?.valor) {
    cards.push(marketCard("📈 Tasa BADLAR", `${vars.badlar.valor.toFixed(1)}%`, "n.a. bancos privados", vars.badlar.fecha, "var(--yellow)"));
  }

  // Grain prices
  const granos = data.granos || {};
  const grainDefs = [
    { key: "soja_fas",  icon: "🌱", label: "Soja FAS" },
    { key: "maiz_fas",  icon: "🌽", label: "Maíz FAS" },
    { key: "trigo_fas", icon: "🌾", label: "Trigo FAS" },
  ];
  for (const g of grainDefs) {
    const v = granos[g.key];
    if (v?.valor) {
      cards.push(marketCard(
        `${g.icon} ${g.label}`,
        `$${v.valor.toLocaleString("es-AR")}`,
        "$/tn teórico MAGyP", v.fecha, "var(--green)"
      ));
    }
  }

  if (!cards.length) {
    section.style.display = "none";
    return;
  }

  grid.innerHTML = cards.join("");
  section.style.display = "";
}

function marketCard(title, value, subtitle, fecha, color) {
  return `
    <div class="market-card">
      <div class="market-card-title">${title}</div>
      <div class="market-card-val" style="color:${color}">${value}</div>
      <div class="market-card-sub">${subtitle}</div>
      ${fecha ? `<div class="market-card-date">${fecha}</div>` : ""}
    </div>`;
}

// ── AI Report ─────────────────────────────────────────────────
document.getElementById("btn-report").addEventListener("click", generateReport);

async function generateReport() {
  const point = state.points.find(p => p.id === state.activeId);
  if (!point?.result || point.result._error) return;

  const btn  = document.getElementById("btn-report");
  const body = document.getElementById("report-body");
  btn.disabled = true;
  btn.textContent = "Generando…";
  body.innerHTML = `<span class="cursor"></span>`;

  let fullText   = "";
  let reportDone = false;
  let reportError = null;

  try {
    const res = await fetch(`${API_BASE}/report`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(point.result),
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
        if (line === "") { currentEvent = null; continue; }
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
        } catch { /* partial chunk */ }
        currentEvent = null;
      }
    }

    if (fullText) body.innerHTML = marked.parse(fullText);
    if (reportError) {
      body.innerHTML = `
        <div class="report-error-banner" role="alert">
          <strong>⚠ Error al generar el informe</strong><br>${esc(reportError)}
        </div>` + (fullText ? marked.parse(fullText) : "");
    }
    if (!reportDone && !reportError && fullText) {
      body.innerHTML = marked.parse(fullText) + `
        <div class="report-incomplete-banner" role="status">
          ⚠ <em>El informe fue interrumpido antes de completarse.</em>
        </div>`;
    }
  } catch (e) {
    body.innerHTML = `<p style="color:var(--red)">Error: ${esc(e.message)}</p>`;
  } finally {
    btn.disabled = false;
    btn.textContent = "Regenerar Informe";
  }
}

// ── Helpers ───────────────────────────────────────────────────
const INDIC_ICON = {
  CRÍTICO:"🔴", ALERTA:"🟠", NORMAL:"🔵", FAVORABLE:"🟢", INDETERMINADO:"⚫"
};

function scaleFmt(s) {
  return { "1w":"1 semana","2w":"2 semanas","1m":"1 mes",
           "2m":"2 meses","3m":"3 meses","6m":"6 meses","1y":"1 año" }[s] || s;
}

function esc(str) {
  if (!str) return "";
  return str.replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;");
}
