/* =========================================================
   GeoEnv Platform — Frontend logic
   Leaflet map · API integration · Plotly candlestick charts
   ========================================================= */

const API_BASE = window.location.origin;

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------
const state = {
  lat: null,
  lon: null,
  scale: "1m",
  marker: null,
  loading: false,
};

// ---------------------------------------------------------------------------
// Map
// ---------------------------------------------------------------------------
const map = L.map("map", {
  center: [-38.5, -65],
  zoom: 5,
  zoomControl: true,
});

L.tileLayer("https://{s}.basemaps.cartocdn.com/dark_all/{z}/{x}/{y}{r}.png", {
  attribution: '© <a href="https://carto.com/">CARTO</a>',
  subdomains: "abcd",
  maxZoom: 19,
}).addTo(map);

// Argentina boundary highlight (rough bbox)
L.rectangle([[-55.1, -73.6], [-21.8, -53.4]], {
  color: "#58a6ff",
  weight: 1.2,
  fill: false,
  dashArray: "4 4",
  opacity: 0.4,
}).addTo(map);

const markerIcon = L.divIcon({
  className: "",
  html: `<div style="
    width:14px;height:14px;border-radius:50%;
    background:#58a6ff;border:2px solid #fff;
    box-shadow:0 0 8px rgba(88,166,255,.8)"></div>`,
  iconAnchor: [7, 7],
});

map.on("click", (e) => {
  const { lat, lng } = e.latlng;

  // Clamp to Argentina bounds
  if (lat < -55.1 || lat > -21.8 || lng < -73.6 || lng > -53.4) {
    showCoordsBox("⚠ Punto fuera de los límites de Argentina", "#f78166");
    return;
  }

  state.lat = parseFloat(lat.toFixed(5));
  state.lon = parseFloat(lng.toFixed(5));

  if (state.marker) state.marker.remove();
  state.marker = L.marker([state.lat, state.lon], { icon: markerIcon }).addTo(map);

  showCoordsBox(
    `<span>Lat ${state.lat.toFixed(4)}  Lon ${state.lon.toFixed(4)}</span>`,
    null
  );
  document.getElementById("btn-analyze").disabled = false;
});

function showCoordsBox(html, color) {
  const el = document.getElementById("coords-box");
  el.innerHTML = html;
  el.style.color = color || "";
}

// ---------------------------------------------------------------------------
// Scale buttons
// ---------------------------------------------------------------------------
document.querySelectorAll(".scale-btn").forEach((btn) => {
  btn.addEventListener("click", () => {
    document.querySelectorAll(".scale-btn").forEach((b) => b.classList.remove("active"));
    btn.classList.add("active");
    state.scale = btn.dataset.scale;
  });
});

// ---------------------------------------------------------------------------
// Analyze button
// ---------------------------------------------------------------------------
document.getElementById("btn-analyze").addEventListener("click", async () => {
  if (state.lat === null || state.loading) return;
  await runAnalysis();
});

async function runAnalysis() {
  state.loading = true;
  document.getElementById("btn-analyze").disabled = true;
  showSpinner();

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
    renderResults(data);
  } catch (e) {
    showError(e.message);
  } finally {
    state.loading = false;
    document.getElementById("btn-analyze").disabled = false;
  }
}

// ---------------------------------------------------------------------------
// Rendering
// ---------------------------------------------------------------------------

function showSpinner() {
  document.getElementById("results").innerHTML = `
    <div class="spinner">
      <div class="spin-ring"></div>
      <span style="font-size:.8rem">Consultando Google Earth Engine…<br>
      <span style="font-size:.7rem;color:var(--muted)">Este proceso puede tardar 20–60 segundos</span>
      </span>
    </div>`;
}

function showError(msg) {
  document.getElementById("results").innerHTML = `
    <div class="placeholder">
      <svg width="40" height="40" viewBox="0 0 24 24" fill="none" stroke="#da3633" stroke-width="1.5">
        <circle cx="12" cy="12" r="10"/><line x1="12" y1="8" x2="12" y2="12"/><line x1="12" y1="16" x2="12.01" y2="16"/>
      </svg>
      <p style="color:#da3633;font-size:.82rem">${escHtml(msg)}</p>
    </div>`;
}

function renderResults(data) {
  const { meta, indices, situation_indicator, socioeconomic } = data;
  const results = document.getElementById("results");

  const badgeClass = `badge-${situation_indicator}`;
  const indicatorLabel = {
    CRÍTICO: "🔴 CRÍTICO",
    ALERTA: "🟠 ALERTA",
    NORMAL: "🔵 NORMAL",
    FAVORABLE: "🟢 FAVORABLE",
    INDETERMINADO: "⚫ INDETERMINADO",
  }[situation_indicator] || situation_indicator;

  results.innerHTML = `
    <!-- Situation indicator -->
    <div class="indicator-badge ${badgeClass}">${indicatorLabel}</div>

    <!-- Meta -->
    <div class="meta-row">
      <span>${meta.region} · ${meta.season}</span>
      <span>${meta.period_start} → ${meta.period_end}</span>
    </div>
    <div class="meta-row">
      <span>Escala: <strong>${scaleLabel(meta.scale)}</strong></span>
      <span style="color:var(--muted)">⏱ ${meta.elapsed_seconds}s</span>
    </div>

    <!-- Index grid -->
    <div class="card">
      <div class="card-header">
        <span>Estado del Ecosistema</span>
        <span style="font-size:.65rem;font-weight:400">vs. baseline 2015–2024</span>
      </div>
      <div class="card-body">
        <div class="index-grid">
          ${renderIndexCard("NDVI", indices.ndvi, "Salud vegetal / biomasa", "ndvi")}
          ${renderIndexCard("EVI", indices.evi, "Señal vegetal corregida", "evi")}
          ${renderIndexCard("NDWI", indices.ndwi, "Estrés hídrico / agua", "ndwi")}
          ${renderIndexCard("LST", indices.lst, "Temperatura superficial", "lst")}
        </div>
      </div>
    </div>

    <!-- Candlestick charts -->
    ${renderCandlestickCard("NDVI", indices.ndvi.candlesticks, "ndvi")}
    ${renderCandlestickCard("NDWI", indices.ndwi.candlesticks, "ndwi")}
    ${renderCandlestickCard("LST", indices.lst.candlesticks, "lst")}

    <!-- Socioeconomic -->
    <div class="card">
      <div class="card-header">Impacto Socioeconómico</div>
      <div class="card-body">
        <div class="socio-block">
          <div class="field">Producción agropecuaria</div>
          ${escHtml(socioeconomic.agriculture.assessment)}
          ${socioeconomic.agriculture.crops_at_risk.length ? `
            <br><span style="color:var(--yellow);font-size:.72rem">
              ⚠ Cultivos en riesgo: ${socioeconomic.agriculture.crops_at_risk.join(", ")}
            </span>` : ""}

          <div class="field">Recurso hídrico</div>
          ${escHtml(socioeconomic.water)}

          <div class="field">Contexto térmico</div>
          ${escHtml(socioeconomic.thermal)}

          <div class="field">Contexto macro (estimado)</div>
          ${escHtml(socioeconomic.macro)}

          <div class="causality">
            ${escHtml(socioeconomic.causality_chain)}
          </div>

          <div class="assumptions">
            ${socioeconomic.assumptions.map(a => `<div class="assumption-item">※ ${escHtml(a)}</div>`).join("")}
          </div>
        </div>
      </div>
    </div>
  `;

  // Render Plotly charts after DOM insert
  renderCandlestickPlot("ndvi", indices.ndvi.candlesticks, "#3fb950");
  renderCandlestickPlot("ndwi", indices.ndwi.candlesticks, "#39c5cf");
  renderCandlestickPlot("lst", indices.lst.candlesticks, "#f78166");
}

// ---------------------------------------------------------------------------
// Index card
// ---------------------------------------------------------------------------

function renderIndexCard(name, idx, desc, key) {
  const val = idx.current;
  const z   = idx.z_score;
  const pct = idx.pct_deviation;
  const unit = key === "lst" ? "°C" : "";
  const anomalyClass = anomalyCssClass(idx.anomaly_class);

  const zBarWidth = z !== null ? Math.min(Math.abs(z) / 3 * 100, 100) : 0;
  const zBarColor = z === null ? "var(--muted)"
    : Math.abs(z) < 1 ? "var(--green)"
    : Math.abs(z) < 1.5 ? "var(--yellow)"
    : "var(--red)";

  return `
    <div class="index-card">
      <div class="index-name">${name}</div>
      <div class="index-value" style="color:${zBarColor}">
        ${val !== null ? `${val.toFixed(3)}${unit}` : "N/D"}
      </div>
      <div>
        <span class="index-anomaly ${anomalyClass}">${idx.anomaly_class}</span>
      </div>
      <div class="index-meta">
        μ ${idx.hist_mean.toFixed(3)} · σ ${idx.hist_std.toFixed(3)}<br>
        z = ${z !== null ? z.toFixed(2) : "N/D"}
        ${pct !== null ? ` · ${pct > 0 ? "+" : ""}${pct.toFixed(1)}%` : ""}
      </div>
      <div class="z-bar"><div class="z-bar-fill" style="width:${zBarWidth}%;background:${zBarColor}"></div></div>
    </div>`;
}

function anomalyCssClass(label) {
  if (!label) return "anomaly-Sin";
  if (label.includes("extrema")) return "anomaly-extrema";
  if (label.includes("moderada")) return "anomaly-moderada";
  if (label === "Normal") return "anomaly-Normal";
  return "anomaly-Sin";
}

// ---------------------------------------------------------------------------
// Candlestick card placeholder (charts rendered by Plotly after DOM insert)
// ---------------------------------------------------------------------------

function renderCandlestickCard(name, candles, key) {
  if (!candles || candles.length === 0) {
    return `
      <div class="card">
        <div class="card-header">${name} — Serie temporal</div>
        <div class="card-body" style="color:var(--muted);font-size:.75rem">
          Sin datos disponibles para el período seleccionado.
        </div>
      </div>`;
  }
  return `
    <div class="card">
      <div class="card-header">
        <span>${name} — Serie temporal (OHLC)</span>
        <span style="font-size:.65rem;font-weight:400">${candles.length} períodos</span>
      </div>
      <div class="card-body" style="padding:6px;">
        <div class="chart-wrap" id="chart-${key}"></div>
      </div>
    </div>`;
}

// ---------------------------------------------------------------------------
// Plotly candlestick renderer
// ---------------------------------------------------------------------------

function renderCandlestickPlot(key, candles, color) {
  const el = document.getElementById(`chart-${key}`);
  if (!el || !candles || candles.length === 0) return;

  const dates  = candles.map(c => c.period);
  const opens  = candles.map(c => c.open);
  const highs  = candles.map(c => c.high);
  const lows   = candles.map(c => c.low);
  const closes = candles.map(c => c.close);
  const texts  = candles.map(c =>
    `${c.period}<br>O:${c.open} H:${c.high} L:${c.low} C:${c.close}<br>${c.direction}<br>${c.anomaly_class} (z=${c.z_close ?? "N/D"})`
  );

  const candleTrace = {
    type: "candlestick",
    x: dates,
    open: opens, high: highs, low: lows, close: closes,
    text: texts, hoverinfo: "text",
    increasing: { line: { color: "#3fb950" }, fillcolor: "rgba(63,185,80,.6)" },
    decreasing: { line: { color: "#f78166" }, fillcolor: "rgba(247,129,102,.6)" },
    name: key.toUpperCase(),
  };

  // Z-score line (scaled to data range for visual reference)
  const zVals = candles.map(c => c.z_close).filter(z => z !== null);
  let zLine = null;
  if (zVals.length > 0) {
    const dataRange = Math.max(...highs) - Math.min(...lows) || 1;
    const dataMid   = (Math.max(...highs) + Math.min(...lows)) / 2;
    const zNorm = candles.map(c =>
      c.z_close !== null ? dataMid + (c.z_close / 3) * (dataRange * 0.4) : null
    );
    zLine = {
      type: "scatter",
      x: dates, y: zNorm,
      mode: "lines+markers",
      line: { color: color, width: 1.5, dash: "dot" },
      marker: { size: 5, color: color },
      name: "Z-score (escala)",
      hoverinfo: "skip",
      yaxis: "y",
    };
  }

  const layout = {
    paper_bgcolor: "transparent", plot_bgcolor: "transparent",
    margin: { t: 8, r: 10, b: 28, l: 44 },
    xaxis: {
      color: "#8b949e", gridcolor: "#21262d", tickfont: { size: 9 },
      rangeslider: { visible: false },
    },
    yaxis: { color: "#8b949e", gridcolor: "#21262d", tickfont: { size: 9 } },
    legend: { font: { color: "#8b949e", size: 9 }, bgcolor: "transparent" },
    showlegend: !!zLine,
    font: { color: "#e6edf3" },
  };

  const traces = [candleTrace];
  if (zLine) traces.push(zLine);

  Plotly.react(el, traces, layout, {
    displayModeBar: false,
    responsive: true,
  });
}

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function scaleLabel(s) {
  const m = { "1w": "1 semana", "2w": "2 semanas", "1m": "1 mes",
              "2m": "2 meses", "3m": "3 meses", "6m": "6 meses", "1y": "1 año" };
  return m[s] || s;
}

function escHtml(str) {
  if (!str) return "";
  return str.replace(/&/g, "&amp;").replace(/</g, "&lt;").replace(/>/g, "&gt;").replace(/"/g, "&quot;");
}
