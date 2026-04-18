/* =============================================================
   GeoEnv Platform v2 — Frontend logic
   ============================================================= */

const API_BASE = window.location.origin;

// ── State ──────────────────────────────────────────────────────
const state = {
  lat: null, lon: null,
  scale: "1m",
  marker: null,
  loading: false,
  lastResult: null,
};

// ── Map setup ──────────────────────────────────────────────────
const map = L.map("map", {
  center: [-38.5, -65],
  zoom: 5,
  zoomControl: true,
});

// Satellite imagery (ESRI World Imagery — no API key needed)
L.tileLayer(
  "https://server.arcgisonline.com/ArcGIS/rest/services/World_Imagery/MapServer/tile/{z}/{y}/{x}",
  { attribution: "Imagery © Esri, Earthstar Geographics", maxZoom: 19 }
).addTo(map);

// Labels overlay (ESRI World Reference)
L.tileLayer(
  "https://services.arcgisonline.com/ArcGIS/rest/services/Reference/World_Boundaries_and_Places/MapServer/tile/{z}/{y}/{x}",
  { attribution: "", maxZoom: 19, opacity: 0.85, pane: "shadowPane" }
).addTo(map);

// Argentina bounding box guide
L.rectangle([[-55.1, -73.6], [-21.8, -53.4]], {
  color: "#3b9eff", weight: 1, fill: false, dashArray: "5 5", opacity: 0.5,
}).addTo(map);

const markerIcon = L.divIcon({
  className: "",
  html: `<div style="
    width:16px;height:16px;border-radius:50%;
    background:#3b9eff;border:3px solid #fff;
    box-shadow:0 0 12px rgba(59,158,255,.9)"></div>`,
  iconAnchor: [8, 8],
});

map.on("click", ({ latlng }) => {
  const { lat, lng } = latlng;
  if (lat < -55.1 || lat > -21.8 || lng < -73.6 || lng > -53.4) {
    setCoords(null, null, "⚠ Punto fuera de los límites de Argentina");
    return;
  }
  state.lat = parseFloat(lat.toFixed(5));
  state.lon = parseFloat(lng.toFixed(5));
  if (state.marker) state.marker.remove();
  state.marker = L.marker([state.lat, state.lon], { icon: markerIcon }).addTo(map);
  setCoords(state.lat, state.lon);
  document.getElementById("btn-analyze").disabled = false;
});

function setCoords(lat, lon, msg) {
  const el = document.getElementById("coords-display");
  if (msg) {
    el.innerHTML = msg;
    el.classList.remove("active");
    return;
  }
  el.innerHTML = `<span class="coord-val">Lat ${lat.toFixed(5)}</span>&nbsp;&nbsp;<span class="coord-val">Lon ${lon.toFixed(5)}</span>`;
  el.classList.add("active");
}

// ── Scale buttons ───────────────────────────────────────────────
document.querySelectorAll(".scale-btn").forEach(btn => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".scale-btn").forEach(b => {
      b.classList.remove("active");
      b.setAttribute("aria-pressed", "false");
    });
    btn.classList.add("active");
    btn.setAttribute("aria-pressed", "true");
    state.scale = btn.dataset.scale;
  });
});

// ── Analyze ────────────────────────────────────────────────────
document.getElementById("btn-analyze").addEventListener("click", () => {
  if (state.lat === null || state.loading) return;
  runAnalysis();
});

async function runAnalysis() {
  state.loading = true;
  setBtnState(true);
  showDashboardSpinner();

  try {
    const res = await fetch(`${API_BASE}/analyze`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ lat: state.lat, lon: state.lon, scale: state.scale }),
    });
    if (!res.ok) {
      const err = await res.json();
      throw new Error(err.detail || `HTTP ${res.status}`);
    }
    const data = await res.json();
    state.lastResult = data;
    renderDashboard(data);
  } catch (e) {
    showDashboardError(e.message);
  } finally {
    state.loading = false;
    setBtnState(false);
  }
}

function setBtnState(loading) {
  const btn = document.getElementById("btn-analyze");
  btn.disabled = loading;
  btn.textContent = loading ? "Analizando…" : "Analizar punto";
}

// ── Dashboard visibility ─────────────────────────────────────────
function showDashboard() {
  const d = document.getElementById("dashboard");
  d.classList.add("visible");
  d.scrollIntoView({ behavior: "smooth", block: "start" });
}

function showDashboardSpinner() {
  showDashboard();
  const sections = ["sit-banner","grid-veg","charts-veg","grid-water","charts-water",
                    "grid-thermal","charts-thermal","precip-card","socio-section"];
  sections.forEach(id => { const el = document.getElementById(id); if (el) el.innerHTML = ""; });
  document.getElementById("sit-banner").innerHTML = `
    <div class="spinner-wrap">
      <div class="spin-ring" role="status" aria-label="Cargando análisis"></div>
      <div>
        <div class="spinner-text">Consultando Google Earth Engine…</div>
        <div class="spinner-sub">Las 4 colecciones MODIS se consultan en paralelo · 10–25 seg</div>
        <div class="spinner-steps" id="spinner-steps">
          <div class="spinner-step" data-step="veg">⏳ MOD13Q1 — NDVI / EVI</div>
          <div class="spinner-step" data-step="opt">⏳ MOD09A1 — NDWI / SAVI / NBR</div>
          <div class="spinner-step" data-step="lst">⏳ MOD11A2 — LST</div>
          <div class="spinner-step" data-step="pcp">⏳ CHIRPS — Precipitación</div>
        </div>
      </div>
    </div>`;
  // Animate spinner steps every 3 s
  let i = 0;
  const steps = ["veg","opt","lst","pcp"];
  const ticker = setInterval(() => {
    if (i >= steps.length) { clearInterval(ticker); return; }
    const el = document.querySelector(`[data-step="${steps[i]}"]`);
    if (el) { el.textContent = el.textContent.replace("⏳","✅"); el.classList.add("done"); }
    i++;
  }, 3200);
  document._spinnerTicker = ticker;
}

function showDashboardError(msg) {
  showDashboard();
  document.getElementById("sit-banner").innerHTML = `
    <div class="error-wrap" role="alert">
      <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="#e8425a" stroke-width="1.5" aria-hidden="true">
        <circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><circle cx="12" cy="16" r=".5" fill="#e8425a"/>
      </svg>
      <p>${esc(msg)}</p>
    </div>`;
}

// ── Main render ─────────────────────────────────────────────────
function renderDashboard(data) {
  clearInterval(document._spinnerTicker);
  const { meta, indices: idx, situation_indicator, socioeconomic: socio } = data;

  // Header chip
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

  // Vegetation section
  document.getElementById("grid-veg").innerHTML =
    idxCard("NDVI",  idx.ndvi,  "Salud vegetal / biomasa") +
    idxCard("EVI",   idx.evi,   "Señal vegetal corregida") +
    idxCard("SAVI",  idx.savi,  "Veg. suelo expuesto");
  renderCharts("charts-veg", [
    { key: "ndvi", label: "NDVI", candles: idx.ndvi.candlesticks, color: "#23d18b" },
    { key: "evi",  label: "EVI",  candles: idx.evi.candlesticks,  color: "#18c2b4" },
  ]);

  // Water & Drought
  document.getElementById("grid-water").innerHTML =
    idxCard("NDWI",  idx.ndwi,  "Humedad canopeo") +
    idxCard("MNDWI", idx.mndwi, "Agua superficial") +
    idxCard("VCI",   idx.vci,   "Condición vs sequía") +
    idxCard("VHI",   idx.vhi,   "Salud ecosistema (VCI+TCI)/2");
  renderCharts("charts-water", [
    { key: "ndwi",  label: "NDWI",  candles: idx.ndwi.candlesticks,  color: "#3b9eff" },
    { key: "mndwi", label: "MNDWI", candles: idx.mndwi.candlesticks, color: "#9b72f5" },
  ]);

  // Thermal & Fire
  document.getElementById("grid-thermal").innerHTML =
    idxCard("LST", idx.lst, "Temperatura superficial", "°C") +
    idxCard("TCI", idx.tci, "Condición térmica") +
    idxCard("NBR", idx.nbr, "Degradación / fuego");
  renderCharts("charts-thermal", [
    { key: "lst", label: "LST (°C)", candles: idx.lst.candlesticks, color: "#f07b3a" },
    { key: "nbr", label: "NBR",      candles: idx.nbr.candlesticks, color: "#e8425a" },
  ]);

  // Precipitation
  renderPrecip(idx.precipitation);

  // Socioeconomic
  renderSocio(socio);

  // Enable report button
  document.getElementById("btn-report").disabled = false;
  document.getElementById("report-body").innerHTML =
    `<span style="color:var(--text-dim);font-size:.77rem">Haga clic en "Generar Informe" para obtener el análisis narrativo completo.</span>`;
}

// ── Index card ───────────────────────────────────────────────────
const ANOMALY_CLASS = {
  "Normal":           "normal",
  "Anomalía moderada":"moderate",
  "Anomalía extrema": "extreme",
  "Sin datos":        "nodata",
};

function idxCard(name, d, desc, unit = "") {
  const val  = d.current;
  const z    = d.z_score;
  const pct  = d.pct_deviation;
  const acKey = ANOMALY_CLASS[d.anomaly_class] || "nodata";

  const zW = z !== null ? Math.min(Math.abs(z) / 3 * 100, 100) : 0;
  const zColor = { normal: "#23d18b", moderate: "#e5b93c", extreme: "#e8425a", nodata: "#1c2d42" }[acKey];
  const valDisplay = val !== null ? `${val.toFixed(3)}${unit}` : "N/D";
  const zDisplay   = z !== null ? `${z > 0 ? "+" : ""}${z.toFixed(2)}σ` : "N/D";
  const pctDisplay = pct !== null ? ` · ${pct > 0 ? "+" : ""}${pct.toFixed(1)}%` : "";

  return `
  <div class="idx-card z-${acKey}" role="article" aria-label="${name}: ${valDisplay}">
    <div class="idx-name">${name}</div>
    <div class="idx-desc" title="${desc}">${desc}</div>
    <div class="idx-value c-${acKey}" aria-label="Valor actual">${valDisplay}</div>
    <div class="idx-badge badge-${acKey}">${d.anomaly_class}</div>
    <div class="idx-stats">
      μ ${d.hist_mean.toFixed(3)}${unit} · σ ${d.hist_std.toFixed(3)}<br>
      <span class="z-val">${zDisplay}</span>${pctDisplay}
    </div>
    <div class="z-track" aria-hidden="true">
      <div class="z-fill" style="width:${zW}%;background:${zColor}"></div>
    </div>
  </div>`;
}

// ── Charts ───────────────────────────────────────────────────────
function renderCharts(containerId, series) {
  const container = document.getElementById(containerId);
  container.innerHTML = series
    .filter(s => s.candles && s.candles.length > 0)
    .map(s => `
      <div class="chart-card">
        <div class="chart-header">
          <span>${s.label} — Serie OHLC</span>
          <span>${s.candles.length} período${s.candles.length !== 1 ? "s" : ""}</span>
        </div>
        <div class="chart-wrap" id="chart-${s.key}"></div>
      </div>`)
    .join("");

  // Render after DOM insertion
  requestAnimationFrame(() => {
    series.forEach(s => {
      if (s.candles && s.candles.length > 0) plotCandle(s.key, s.candles, s.color);
    });
  });
}

function plotCandle(key, candles, color) {
  const el = document.getElementById(`chart-${key}`);
  if (!el) return;

  const dates  = candles.map(c => c.period);
  const opens  = candles.map(c => c.open);
  const highs  = candles.map(c => c.high);
  const lows   = candles.map(c => c.low);
  const closes = candles.map(c => c.close);
  const texts  = candles.map(c =>
    `<b>${c.period}</b><br>O:${c.open}  H:${c.high}  L:${c.low}  C:${c.close}<br>${c.direction}<br>${c.anomaly_class} (z=${c.z_close ?? "N/D"})`
  );

  const candleTrace = {
    type: "candlestick",
    x: dates, open: opens, high: highs, low: lows, close: closes,
    text: texts, hoverinfo: "text",
    increasing: { line: { color: "#23d18b" }, fillcolor: "rgba(35,209,139,.55)" },
    decreasing: { line: { color: "#e8425a" }, fillcolor: "rgba(232,66,90,.55)" },
    name: key.toUpperCase(),
  };

  // Normalised z-score overlay
  const zVals = candles.map(c => c.z_close).filter(z => z !== null);
  const traces = [candleTrace];
  if (zVals.length > 0) {
    const range  = Math.max(...highs) - Math.min(...lows) || 0.01;
    const mid    = (Math.max(...highs) + Math.min(...lows)) / 2;
    const zNorm  = candles.map(c => c.z_close !== null ? mid + (c.z_close / 3) * range * 0.38 : null);
    traces.push({
      type: "scatter", x: dates, y: zNorm,
      mode: "lines+markers",
      line: { color, width: 1.8, dash: "dot" },
      marker: { size: 4, color },
      name: "Z-score", hoverinfo: "skip",
    });
  }

  Plotly.react(el, traces, {
    paper_bgcolor: "transparent", plot_bgcolor: "transparent",
    margin: { t: 6, r: 12, b: 30, l: 52 },
    xaxis: { color: "#7a90aa", gridcolor: "#1c2d42", tickfont: { size: 9 }, rangeslider: { visible: false } },
    yaxis: { color: "#7a90aa", gridcolor: "#1c2d42", tickfont: { size: 9 } },
    legend: { font: { color: "#7a90aa", size: 9 }, bgcolor: "transparent" },
    showlegend: zVals.length > 0,
    font: { color: "#c8d8ec" },
  }, { displayModeBar: false, responsive: true });
}

// ── Precipitation card ───────────────────────────────────────────
function renderPrecip(p) {
  const container = document.getElementById("precip-card");
  if (!p || p.current_mm === null) {
    container.innerHTML = `<p style="color:var(--text-dim);font-size:.77rem;padding:0 0 16px">Datos de precipitación no disponibles.</p>`;
    return;
  }
  const acKey = ANOMALY_CLASS[p.anomaly_class] || "nodata";
  const zFmt  = p.z_score !== null ? `${p.z_score > 0 ? "+" : ""}${p.z_score.toFixed(2)}σ` : "N/D";
  const pctFmt = p.pct_deviation !== null ? ` (${p.pct_deviation > 0 ? "+" : ""}${p.pct_deviation.toFixed(1)}%)` : "";
  const color = { normal: "var(--green)", moderate: "var(--yellow)", extreme: "var(--red)", nodata: "var(--text-dim)" }[acKey];

  container.innerHTML = `
    <div class="precip-card">
      <div class="precip-main">
        <div class="precip-label">Precipitación acumulada (${p.analysis_days} días)</div>
        <div class="precip-val" style="color:${color}">${p.current_mm} mm</div>
      </div>
      <div class="precip-compare">
        Media histórica: <strong>${p.hist_mean_mm} mm</strong>${pctFmt}<br>
        Z-score: <strong>${zFmt}</strong>
      </div>
      <div class="precip-badge">
        <span class="idx-badge badge-${acKey}">${p.anomaly_class}</span>
      </div>
    </div>`;
}

// ── Socioeconomic ────────────────────────────────────────────────
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

// ── AI Report (streaming) ────────────────────────────────────────
document.getElementById("btn-report").addEventListener("click", generateReport);

async function generateReport() {
  if (!state.lastResult) return;
  const btn  = document.getElementById("btn-report");
  const body = document.getElementById("report-body");
  btn.disabled = true;
  btn.textContent = "Generando…";
  body.innerHTML = `<span class="cursor"></span>`;

  let fullText = "";

  try {
    const res = await fetch(`${API_BASE}/report`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(state.lastResult),
    });

    if (!res.ok) throw new Error(`HTTP ${res.status}`);

    const reader  = res.body.getReader();
    const decoder = new TextDecoder();

    while (true) {
      const { done, value } = await reader.read();
      if (done) break;
      const lines = decoder.decode(value, { stream: true }).split("\n");
      for (const line of lines) {
        if (!line.startsWith("data: ")) continue;
        const payload = line.slice(6).trim();
        if (payload === "[DONE]") break;
        try {
          const { chunk } = JSON.parse(payload);
          fullText += chunk;
          body.innerHTML = marked.parse(fullText) + `<span class="cursor" aria-hidden="true"></span>`;
          body.scrollIntoView({ behavior: "smooth", block: "end" });
        } catch { /* partial chunk */ }
      }
    }
    body.innerHTML = marked.parse(fullText);
  } catch (e) {
    body.innerHTML = `<p style="color:var(--red)">Error generando informe: ${esc(e.message)}</p>`;
  } finally {
    btn.disabled = false;
    btn.textContent = "Regenerar Informe";
  }
}

// ── Helpers ──────────────────────────────────────────────────────
const INDIC_ICON = {
  CRÍTICO: "🔴", ALERTA: "🟠", NORMAL: "🔵", FAVORABLE: "🟢", INDETERMINADO: "⚫"
};

function scaleFmt(s) {
  return { "1w": "1 semana", "2w": "2 semanas", "1m": "1 mes",
           "2m": "2 meses", "3m": "3 meses", "6m": "6 meses", "1y": "1 año" }[s] || s;
}

function esc(str) {
  if (!str) return "";
  return str.replace(/&/g,"&amp;").replace(/</g,"&lt;").replace(/>/g,"&gt;").replace(/"/g,"&quot;");
}
