"""
LLM report generator — Google Gemini (google-genai SDK, streaming).
Sends full analysis JSON + index definitions to the model.
Produces a 1-2 page professional report in Spanish.
"""
import os
import json
import logging
from datetime import date
from google import genai
from google.genai import types

logger = logging.getLogger(__name__)

MODEL = "gemini-2.5-flash"   # stable, widely available

MONTH_NAMES = {
    1: "Enero", 2: "Febrero", 3: "Marzo", 4: "Abril", 5: "Mayo", 6: "Junio",
    7: "Julio", 8: "Agosto", 9: "Septiembre", 10: "Octubre", 11: "Noviembre", 12: "Diciembre",
}

# ---------------------------------------------------------------------------
# Index definitions for the LLM (static reference knowledge)
# ---------------------------------------------------------------------------

INDEX_DEFS = {
    "NDVI": {
        "nombre": "Índice de Vegetación de Diferencia Normalizada",
        "formula": "(NIR − RED) / (NIR + RED)",
        "rango_tipico": "−1 a +1; vegetación: 0.1–0.9",
        "fuente": "MODIS MOD13Q1, 250 m, composición 16 días",
        "estacionalidad": "Marcada: máximo en primavera-verano, mínimo en invierno. El z-score compara contra el mismo mes histórico.",
        "anomalia_positiva": "Mayor biomasa, vegetación más densa o sana, mayor actividad fotosintética.",
        "anomalia_negativa": "Estrés hídrico, defoliación, degradación del suelo, cosecha reciente o helada.",
    },
    "EVI": {
        "nombre": "Índice de Vegetación Mejorado",
        "formula": "2.5 × (NIR − RED) / (NIR + 6×RED − 7.5×BLUE + 1)",
        "rango_tipico": "−1 a +1; similar a NDVI pero menos saturado en alta biomasa",
        "fuente": "MODIS MOD13Q1, 250 m, composición 16 días",
        "estacionalidad": "Similar a NDVI, más sensible en dosel denso.",
        "anomalia_positiva": "Igual que NDVI, más robusto en zonas con alta densidad de dosel.",
        "anomalia_negativa": "Igual que NDVI.",
    },
    "SAVI": {
        "nombre": "Índice de Vegetación Ajustado al Suelo",
        "formula": "1.5 × (NIR − RED) / (NIR + RED + 0.5)",
        "rango_tipico": "−1 a +1; similar a NDVI",
        "fuente": "MODIS MOD09A1, 500 m, composición 8 días",
        "estacionalidad": "Como NDVI, más útil en zonas semiáridas con suelo expuesto.",
        "anomalia_positiva": "Mayor cobertura vegetal relativa al suelo.",
        "anomalia_negativa": "Mayor exposición del suelo, pérdida de cobertura.",
    },
    "NDWI": {
        "nombre": "Índice de Agua de Diferencia Normalizada (Gao 1996)",
        "formula": "(NIR − SWIR1) / (NIR + SWIR1)",
        "rango_tipico": "−1 a +1; valores positivos = buena hidratación del canopeo",
        "fuente": "MODIS MOD09A1, 500 m",
        "estacionalidad": "Sigue a la humedad del suelo y precipitación estacional.",
        "anomalia_positiva": "Mayor contenido hídrico en la vegetación, buenas condiciones hídricas.",
        "anomalia_negativa": "Estrés hídrico del dosel, déficit de humedad, sequía.",
    },
    "MNDWI": {
        "nombre": "Índice de Agua de Diferencia Normalizada Modificado",
        "formula": "(GREEN − SWIR1) / (GREEN + SWIR1)",
        "rango_tipico": "−1 a +1; valores positivos indican agua superficial",
        "fuente": "MODIS MOD09A1, 500 m",
        "estacionalidad": "Refleja eventos de lluvia y variación de cuerpos de agua.",
        "anomalia_positiva": "Mayor presencia de agua superficial (inundaciones, lagunas).",
        "anomalia_negativa": "Reducción de agua superficial, sequía, pérdida de cuerpos de agua.",
    },
    "VCI": {
        "nombre": "Índice de Condición de Vegetación",
        "formula": "(NDVI − NDVI_min_hist) / (NDVI_max_hist − NDVI_min_hist)",
        "rango_tipico": "0 a 1; VCI < 0.35 = sequía agrícola",
        "fuente": "Derivado de NDVI MODIS, baseline histórico 20 años",
        "estacionalidad": "Se compara contra el mismo mes histórico mediante climatología estacional.",
        "anomalia_positiva": "Condición vegetal mejor que el mínimo histórico del mes.",
        "anomalia_negativa": "Condición vegetal próxima al mínimo histórico: sequía o estrés severo.",
    },
    "TCI": {
        "nombre": "Índice de Condición Térmica",
        "formula": "(LST_max_hist − LST) / (LST_max_hist − LST_min_hist)",
        "rango_tipico": "0 a 1; TCI bajo = temperatura extrema (calor o frío)",
        "fuente": "Derivado de LST MODIS, baseline histórico 20 años",
        "estacionalidad": "Mide la temperatura relativa al rango histórico del mes.",
        "anomalia_positiva": "Temperatura más fresca que el máximo histórico (favorable en verano).",
        "anomalia_negativa": "Temperatura elevada o extremadamente baja respecto al histórico del mes.",
    },
    "VHI": {
        "nombre": "Índice de Salud de la Vegetación",
        "formula": "0.5 × VCI + 0.5 × TCI",
        "rango_tipico": "0 a 1; VHI < 0.2 = emergencia de sequía; 0.2–0.35 = sequía severa",
        "fuente": "Derivado de VCI y TCI",
        "estacionalidad": "Combina condición hídrica y térmica. Refleja bienestar general del ecosistema.",
        "anomalia_positiva": "Ecosistema en condiciones favorables tanto hídricamente como térmicamente.",
        "anomalia_negativa": "Estrés combinado hídrico-térmico; riesgo de pérdida de productividad.",
    },
    "LST": {
        "nombre": "Temperatura de Superficie Terrestre",
        "formula": "Radiancia infrarroja térmica convertida a Kelvin, luego a °C",
        "rango_tipico": "Variable según región y estación; unidad: °C",
        "fuente": "MODIS MOD11A2, 1 km, composición 8 días",
        "estacionalidad": "Fuerte componente estacional. El z-score compara contra el mismo mes de los 20 años previos.",
        "anomalia_positiva": "Temperatura más alta de lo normal para el mes (estrés calórico, sequía).",
        "anomalia_negativa": "Temperatura más baja de lo normal para el mes (riesgo de heladas, nieve tardía).",
    },
    "LST_NIGHT": {
        "nombre": "Temperatura de Superficie Terrestre Nocturna",
        "formula": "Radiancia infrarroja térmica nocturna → Kelvin → °C",
        "rango_tipico": "Variable; generalmente 3–8°C menor que la LST diurna",
        "fuente": "MODIS MOD11A2, banda LST_Night_1km, 1 km, composición 8 días",
        "estacionalidad": "Alta componente estacional. La amplitud térmica diurna-nocturna es indicadora de cobertura vegetal y humedad.",
        "anomalia_positiva": "Noches más cálidas de lo normal (estrés calórico nocturno, menor disipación de calor).",
        "anomalia_negativa": "Noches más frías de lo normal; riesgo de heladas en cultivos.",
    },
    "NBR": {
        "nombre": "Ratio de Quema Normalizado",
        "formula": "(NIR − SWIR2) / (NIR + SWIR2)",
        "rango_tipico": "−1 a +1; valores bajos post-fuego",
        "fuente": "MODIS MOD09A1, 500 m",
        "estacionalidad": "Sensible a eventos de fuego; valores bajos = área quemada o degradada.",
        "anomalia_positiva": "Vegetación íntegra, mayor biomasa.",
        "anomalia_negativa": "Posible quema reciente, degradación o pérdida de biomasa.",
    },
    "ET": {
        "nombre": "Evapotranspiración Real",
        "formula": "Balance energético MODIS (algoritmo Mu et al. 2011)",
        "rango_tipico": "0–10 mm/8-día; varía fuertemente por bioma y estación",
        "fuente": "MODIS MOD16A2GF, 500 m, composición 8 días (gap-filled)",
        "estacionalidad": "Máxima en verano (alta radiación + vegetación activa). "
                          "El z-score compara contra el mismo mes histórico 2004–2024.",
        "anomalia_positiva": "Alta demanda evaporativa o buena disponibilidad hídrica; riesgo de agotamiento de reservas en sequía.",
        "anomalia_negativa": "Estrés hídrico (la planta cierra estomas), baja radiación o vegetación escasa.",
    },
    "SM": {
        "nombre": "Humedad Superficial del Suelo",
        "formula": "Retrodispersión SAR L-band (NASA SMAP) → mm de agua en los primeros 5 cm",
        "rango_tipico": "0–60 mm; depende de textura y uso del suelo",
        "fuente": "NASA SMAP 10 km (HSL enhanced), baseline 2016–2024",
        "estacionalidad": "Sigue a las precipitaciones con rezago de 1–3 días. "
                          "Baseline más corto (9 años) que los índices MODIS.",
        "anomalia_positiva": "Suelo saturado o con exceso hídrico; riesgo de anegamiento o enfermedades fúngicas.",
        "anomalia_negativa": "Suelo seco; déficit hídrico superficial, riesgo de estrés en cultivos de raíz superficial.",
    },
}


# ---------------------------------------------------------------------------
# Build the structured JSON payload for the LLM
# ---------------------------------------------------------------------------

_MONTH_ABBR = {1:"Ene",2:"Feb",3:"Mar",4:"Abr",5:"May",6:"Jun",
               7:"Jul",8:"Ago",9:"Sep",10:"Oct",11:"Nov",12:"Dic"}


def _compact_climatology(climatology: dict | None) -> dict:
    """Convert {month_int: {mean, p10..p90}} → {month_abbr: {mean, p25, p50, p75}}."""
    if not climatology:
        return {}
    out = {}
    for m, v in climatology.items():
        abbr = _MONTH_ABBR.get(int(m), str(m))
        out[abbr] = {
            "media": round(v["mean"], 4) if v.get("mean") is not None else None,
            "p25":   round(v["p25"],  4) if v.get("p25")  is not None else None,
            "p50":   round(v["p50"],  4) if v.get("p50")  is not None else None,
            "p75":   round(v["p75"],  4) if v.get("p75")  is not None else None,
        }
    return out


def _recent_trend(recent_series: list, n: int = 6) -> list:
    """Return last N monthly values in compact form: [{mes, valor}]."""
    if not recent_series:
        return []
    return [
        {"mes": pt["date"][:7], "valor": pt["value"]}
        for pt in recent_series[-n:]
    ]


def build_llm_payload(data: dict) -> dict:
    """
    Build a structured JSON payload that contains everything the LLM needs:
    index definitions, seasonal climatology, current values, recent trend,
    precipitation and socioeconomic context.
    """
    meta    = data["meta"]
    idx     = data["indices"]
    socio   = data["socioeconomic"]
    precip  = idx.get("precipitation", {})
    cur_m   = meta.get("current_month", date.today().month)

    indices_payload = {}
    for key, d in idx.items():
        if key == "precipitation":
            continue
        if d is None:          # optional indicator not available
            continue
        # Current-month percentiles for context
        clim    = d.get("climatology", {})
        cur_m_clim = clim.get(cur_m, clim.get(str(cur_m), {})) if clim else {}

        indices_payload[key.upper()] = {
            "valor_actual":            d.get("current"),
            "fecha_dato":              d.get("current_date"),
            "media_estacional_mes":    d.get("hist_mean"),
            "desvio_estacional":       d.get("hist_std"),
            "p25_mes_actual":          cur_m_clim.get("p25"),
            "p50_mes_actual":          cur_m_clim.get("p50"),
            "p75_mes_actual":          cur_m_clim.get("p75"),
            "z_score_estacional":      d.get("z_score"),
            "desviacion_pct":          d.get("pct_deviation"),
            "clase_anomalia":          d.get("anomaly_class"),
            "hist_min_20yr":           d.get("hist_min"),
            "hist_max_20yr":           d.get("hist_max"),
            "climatologia_estacional": _compact_climatology(clim),
            "tendencia_reciente_6m":   _recent_trend(d.get("recent_series", [])),
            "n_observaciones_periodo": d.get("n_observations", 0),
        }

    return {
        "analisis": {
            "ubicacion": {
                "lat": meta["lat"], "lon": meta["lon"],
                "region": meta["region"], "estacion": meta["season"],
            },
            "periodo": {
                "inicio": meta["period_start"], "fin": meta["period_end"],
                "escala": meta["scale"],
                "mes_actual": MONTH_NAMES.get(cur_m, str(cur_m)),
                "numero_mes": cur_m,
                "baseline": meta.get("hist_baseline", "2004–2024 (20 años MODIS)"),
                "nota_z_score": (
                    "Todos los z-scores son ESTACIONALES: comparan el valor actual "
                    "contra la media y desvío del MISMO mes calendario en los 20 años anteriores."
                ),
            },
            "indicador_situacion": data["situation_indicator"],
        },
        "indices": indices_payload,
        "precipitacion": {
            "acumulado_mm":     precip.get("current_mm"),
            "media_historica_mm": precip.get("hist_mean_mm"),
            "desvio_historico_mm": precip.get("hist_std_mm"),
            "z_score":          precip.get("z_score"),
            "desviacion_pct":   precip.get("pct_deviation"),
            "clase_anomalia":   precip.get("anomaly_class"),
            "dias_analizados":  precip.get("analysis_days"),
            "fuente":           "CHIRPS, 5.5 km, latencia ~5 días",
        },
        "contexto_socioeconomico": {
            "produccion_agropecuaria": socio["agriculture"]["assessment"],
            "impacto_rendimientos":    socio["agriculture"]["yield_impact"],
            "cultivos_en_riesgo":      socio["agriculture"]["crops_at_risk"],
            "recurso_hidrico":         socio["water"],
            "precipitacion":           socio["precipitation"],
            "contexto_termico":        socio["thermal"],
            "cadena_causalidad":       socio["causality_chain"],
            "supuestos":               socio["assumptions"],
        },
        "contexto_topografico": _format_static_context(data.get("static_context")),
    }


def _format_static_context(ctx: dict | None) -> dict | None:
    """Add human-readable interpretation labels to the static terrain context."""
    if not ctx:
        return None

    def slope_label(deg):
        if deg is None: return None
        if deg < 1:  return "plano"
        if deg < 5:  return "suave"
        if deg < 15: return "moderado"
        return "fuerte"

    def twi_label(twi):
        if twi is None: return None
        if twi < 5:  return "bien drenado / convexo"
        if twi < 8:  return "moderado"
        return "convergente / susceptible a anegamiento"

    def curv_label(c):
        if c is None: return None
        if c < -0.5: return "cóncavo (convergente)"
        if c > 0.5:  return "convexo (divergente)"
        return "plano"

    return {
        "altitud_m":          ctx.get("elevation_m"),
        "pendiente_deg":      ctx.get("slope_deg"),
        "pendiente_clase":    slope_label(ctx.get("slope_deg")),
        "hand_m":             ctx.get("hand_m"),
        "hand_interpretacion": (
            "próximo a drenaje — riesgo de inundación" if (ctx.get("hand_m") or 999) < 5
            else "alejado de drenajes — bajo riesgo hídrico fluvial"
        ) if ctx.get("hand_m") is not None else None,
        "twi":              ctx.get("twi"),
        "twi_clase":        twi_label(ctx.get("twi")),
        "curvatura":        ctx.get("curvature"),
        "curvatura_clase":  curv_label(ctx.get("curvature")),
        "fuente":           "MERIT/Hydro v1.0.1 + SRTM 30m, resolución 90m",
    }


def _index_defs_text() -> str:
    """Format INDEX_DEFS as human-readable text for the system instruction (R-014)."""
    lines = ["DEFINICIÓN DE ÍNDICES SATELITALES:\n"]
    for key, d in INDEX_DEFS.items():
        lines.append(f"{key} — {d['nombre']}")
        lines.append(f"  Fórmula:            {d['formula']}")
        lines.append(f"  Rango típico:       {d['rango_tipico']}")
        lines.append(f"  Fuente:             {d['fuente']}")
        lines.append(f"  Estacionalidad:     {d['estacionalidad']}")
        lines.append(f"  Anomalía positiva:  {d['anomalia_positiva']}")
        lines.append(f"  Anomalía negativa:  {d['anomalia_negativa']}")
        lines.append("")
    return "\n".join(lines)


# R-014: INDEX_DEFS moved to SYSTEM_INSTRUCTION (static content → implicit caching).
# The user prompt only carries per-call data (values, curves, candles).
SYSTEM_INSTRUCTION = f"""Eres un analista geoespacial y socioeconómico especializado en Argentina.
Tu tarea es redactar informes técnicos basados en índices satelitales MODIS y datos climatológicos.

{_index_defs_text()}
METODOLOGÍA DE Z-SCORES (FUNDAMENTAL — leer antes de interpretar cualquier dato):
- Los z-scores son ESTACIONALES: cada índice se compara contra la media del MISMO mes calendario
  a lo largo de 20 años (2004-2024), NO contra un promedio anual.
  Esto corrige la estacionalidad natural (ej: NDVI alto en enero no es anomalía si enero siempre es alto).
- La climatologia_estacional muestra la distribución histórica por mes: media, p25, p50 (mediana) y p75.
- Los percentiles p25–p75 representan el rango "normal" para cada mes. Valores fuera de ese rango son inusuales.
- LIMITACIÓN: el baseline es estático 2004-2024. Tendencias climáticas de largo plazo pueden
  sesgar los z-scores. Menciona esta limitación cuando sea relevante.

INTERPRETACIÓN DEL CONTEXTO TOPOGRÁFICO:
- altitud_m: elevación sobre el nivel del mar
- pendiente_deg: pendiente en grados; >15° = fuerte, 5-15° = moderada, <5° = suave/plana
- hand_m: Height Above Nearest Drainage; <5m = muy próximo a drenaje y susceptible a inundación
- twi: Topographic Wetness Index = ln(área_acumulada / tan(pendiente)); alto (>10) = convergente/húmedo
- curvatura: positiva = convexo (agua escurre), negativa = cóncavo (agua converge y acumula)
- Estos factores condicionan la distribución de humedad, acumulación de agua y riesgo de anegamiento

UMBRALES DE CLASIFICACIÓN (anomaly_class):
- muy_bajo  : z < −1.5σ (extremadamente por debajo del promedio del mes)
- bajo      : −1.5 ≤ z < −0.5σ
- normal    : −0.5 ≤ z ≤ +0.5σ
- alto      : +0.5 < z ≤ +1.5σ
- muy_alto  : z > +1.5σ
- VHI < 0.2 → Emergencia de sequía; 0.2–0.35 → Sequía severa
- La tendencia_reciente_6m muestra los últimos 6 valores mensuales (media mensual real).

Escribe en español técnico, con precisión cuantitativa. Usa Markdown (## y **negritas**).
Objetivo: 600–900 palabras en 5 secciones ordenadas. No inventes datos ausentes del JSON."""


def build_prompt(payload: dict, user_context: str | None = None) -> str:
    payload_json = json.dumps(payload, ensure_ascii=False, indent=2)

    user_ctx_block = ""
    if user_context:
        user_ctx_block = f"""
## CONTEXTO DEL PRODUCTOR / LOTE (proporcionado por el usuario)

{user_context}

> Integrá esta información en el análisis: adaptá las recomendaciones al cultivo/manejo indicado,
> mencioná riesgos específicos para ese sistema productivo y priorizá las secciones más relevantes.

---
"""

    return f"""A continuación encontrarás el JSON completo del análisis ambiental.
Cada índice incluye su valor actual, percentiles estacionales del mes en curso (p25/p50/p75),
z-score estacional, climatología mensual histórica y tendencia de los últimos 6 meses.
{user_ctx_block}
## DATOS DEL ANÁLISIS (JSON)

```json
{payload_json}
```

---

## INSTRUCCIONES DEL INFORME

Redacta el informe con estas 5 secciones en orden estricto:

1. **Resumen ejecutivo** (3–4 oraciones): situación general, indicador de riesgo, región, estación.
   Si el usuario proporcionó contexto de lote/cultivo, integralo aquí.

2. **Estado del ecosistema**: analiza cuantitativamente cada grupo de índices
   (vegetación: NDVI/EVI/SAVI, agua/sequía: NDWI/MNDWI/VCI/VHI, temperatura: LST/TCI, fuego: NBR).
   Para cada uno menciona el valor actual vs el p50 estacional del mes, el z-score y su clase de anomalía.
   Usa los percentiles p25/p75 para contextualizar si el valor está dentro del rango histórico normal.

3. **Tendencia reciente** (últimos 6 meses): interpreta la serie mensual real.
   ¿La situación mejora, empeora o es estable? ¿Hay cambio de tendencia reciente?

4. **Impacto productivo-económico**: conecta los indicadores con la producción agropecuaria.
   {"Si se indicó cultivo/manejo, personaliza el análisis para ese sistema (fenología, umbrales críticos, decisiones de manejo)." if user_context else "Sigue la cadena causal proporcionada en los datos."}
   Menciona cultivos en riesgo y cuantifica el impacto esperado.

5. **Nivel de riesgo y recomendaciones**: concluye con el indicador de situación,
   las variables que lo determinan, y 2–3 acciones concretas y accionables
   (monitoreo satelital, decisiones agronómicas, alertas tempranas).
   {"Adapta las recomendaciones al sistema productivo indicado por el usuario." if user_context else ""}

Sé cuantitativo. Menciona incertidumbre cuando corresponda.
Usa **negrita** para valores clave. No inventes datos que no estén en el JSON."""


# ---------------------------------------------------------------------------
# Streaming generator for FastAPI StreamingResponse
# ---------------------------------------------------------------------------

def stream_report(analysis_data: dict):
    """Yields SSE-formatted chunks: data: {"chunk": "..."}\n\n"""
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        yield 'data: {"chunk": "⚠ GEMINI_API_KEY no configurada en el servidor."}\n\n'
        yield "data: [DONE]\n\n"
        return

    try:
        client       = genai.Client(api_key=api_key)
        user_context = analysis_data.pop("_user_context", None) or None
        payload      = build_llm_payload(analysis_data)
        prompt       = build_prompt(payload, user_context)

        response = client.models.generate_content_stream(
            model=MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_INSTRUCTION,
                temperature=0.3,
                max_output_tokens=8192,
                thinking_config=types.ThinkingConfig(thinking_budget=0),
            ),
        )

        for chunk in response:
            text = chunk.text or ""
            if text:
                payload_sse = json.dumps({"chunk": text}, ensure_ascii=False)
                yield f"data: {payload_sse}\n\n"

    except Exception as e:
        logger.exception("Report generation failed")
        # R-003: use a dedicated SSE event type so the frontend can display
        # the error separately from the report body (not mixed into the text).
        err_payload = json.dumps({"message": str(e), "code": type(e).__name__}, ensure_ascii=False)
        yield f"event: error\ndata: {err_payload}\n\n"
        # Do NOT yield [DONE] after an error so the frontend knows the stream
        # closed abnormally and can mark the report as incomplete.
        return

    yield "data: [DONE]\n\n"
