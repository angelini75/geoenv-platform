"""
LLM report generator using Google Gemini with streaming.
Produces a 1-2 page professional report in Spanish from analysis data.
"""
import os
import json
import logging
import google.generativeai as genai

logger = logging.getLogger(__name__)

MODEL = "gemini-1.5-flash"   # free-tier compatible


def _fmt(v, decimals=3, unit=""):
    if v is None:
        return "N/D"
    return f"{round(v, decimals)}{unit}"


def _z_label(z):
    if z is None:
        return "N/D"
    sign = "+" if z > 0 else ""
    return f"{sign}{z:.2f}σ"


def build_prompt(data: dict) -> str:
    meta    = data["meta"]
    idx     = data["indices"]
    indic   = data["situation_indicator"]
    socio   = data["socioeconomic"]
    precip  = idx.get("precipitation", {})

    rows = []
    index_defs = [
        ("NDVI",  idx["ndvi"],  "Salud vegetal / biomasa",       ""),
        ("EVI",   idx["evi"],   "Señal vegetal corregida",       ""),
        ("SAVI",  idx["savi"],  "Veg. en suelos expuestos",      ""),
        ("NDWI",  idx["ndwi"],  "Humedad canopeo (Gao 1996)",    ""),
        ("MNDWI", idx["mndwi"], "Agua superficial",              ""),
        ("VCI",   idx["vci"],   "Condición vegetal vs sequía",   ""),
        ("TCI",   idx["tci"],   "Condición térmica",             ""),
        ("VHI",   idx["vhi"],   "Salud ecosistema (VCI+TCI)/2",  ""),
        ("LST",   idx["lst"],   "Temperatura superficial",       "°C"),
        ("NBR",   idx["nbr"],   "Ratio quema/degradación",       ""),
    ]
    for name, d, desc, unit in index_defs:
        cur = _fmt(d.get("current"), 3, unit)
        hm  = _fmt(d.get("hist_mean"), 3, unit)
        z   = _z_label(d.get("z_score"))
        ac  = d.get("anomaly_class", "N/D")
        rows.append(f"| {name:<6} | {desc:<38} | {cur:<10} | {hm:<10} | {z:<8} | {ac} |")

    table = (
        "| Índice | Descripción                            | Actual     | Media hist | Z-score  | Clase      |\n"
        "|--------|----------------------------------------|------------|------------|----------|------------|\n"
        + "\n".join(rows)
    )

    p_cur  = _fmt(precip.get("current_mm"), 1, " mm")
    p_hist = _fmt(precip.get("hist_mean_mm"), 1, " mm")
    p_z    = _z_label(precip.get("z_score"))

    candle_summary = []
    for name, key in [("NDVI", "ndvi"), ("NDWI", "ndwi"), ("LST", "lst")]:
        candles = idx.get(key, {}).get("candlesticks", [])
        if candles:
            last = candles[-1]
            candle_summary.append(
                f"  - {name} último período ({last['period']}): "
                f"O={last['open']} C={last['close']} → {last['direction']} · {last['anomaly_class']}"
            )

    candle_text = "\n".join(candle_summary) if candle_summary else "  Sin datos de candlestick disponibles."

    return f"""Eres un analista geoespacial y socioeconómico especializado en Argentina. Redacta un informe técnico-profesional de 1 a 2 páginas en español, basado estrictamente en los datos de análisis que se presentan a continuación.

## DATOS DEL ANÁLISIS

**Ubicación:** Lat {meta['lat']}, Lon {meta['lon']} — Región: {meta['region']} · {meta['season']}
**Período analizado:** {meta['period_start']} → {meta['period_end']} (escala: {meta['scale']})
**Indicador de situación:** **{indic}**

### Índices ambientales (baseline histórico 2015–2024, mismos meses calendario)

{table}

### Precipitación (CHIRPS)
- Acumulado período: {p_cur} vs media histórica {p_hist} ({p_z})
- Clase: {precip.get('anomaly_class', 'N/D')}

### Velas temporales (OHLC — últimas observaciones)
{candle_text}

### Contexto socioeconómico
- Impacto agropecuario: {socio['agriculture']['assessment']}
- Cultivos en riesgo: {', '.join(socio['agriculture']['crops_at_risk']) or 'Ninguno identificado'}
- Recurso hídrico: {socio['water']}
- Precipitación: {socio['precipitation']}
- Contexto térmico: {socio['thermal']}
- Contexto macro: {socio['macro']}
- Cadena de causalidad: {socio['causality_chain']}

### Supuestos
{chr(10).join('- ' + a for a in socio['assumptions'])}

---

## INSTRUCCIONES DE FORMATO

El informe debe tener las siguientes secciones en este orden exacto:

1. **Resumen ejecutivo** (3–4 oraciones): síntesis del estado general, indicador y región.
2. **Estado del ecosistema**: describe cuantitativamente cada grupo de índices (vegetación, agua/sequía, temperatura). Usa los valores z-score para contextualizar la severidad.
3. **Análisis de tendencia temporal**: interpreta las velas OHLC y lo que revelan sobre la dinámica reciente.
4. **Impacto productivo-económico**: conecta los indicadores ambientales con la producción agropecuaria y el contexto macroeconómico. Sigue la cadena causal: ambiente → producción → economía → sociedad.
5. **Nivel de riesgo y recomendaciones**: concluye con el nivel de riesgo ({indic}), qué lo determina, y 2-3 acciones concretas recomendadas (monitoreo, decisiones agronómicas, alertas).

Sé preciso y cuantitativo. Menciona explícitamente la incertidumbre cuando corresponda. Usa Markdown con encabezados ## y negritas para datos clave. Extensión objetivo: 600–900 palabras."""


def stream_report(analysis_data: dict):
    """Generator that yields SSE-formatted chunks for FastAPI StreamingResponse."""
    api_key = os.environ.get("GEMINI_API_KEY")
    if not api_key:
        yield 'data: {"chunk": "⚠ GEMINI_API_KEY no configurada en el servidor."}\n\n'
        yield "data: [DONE]\n\n"
        return

    try:
        genai.configure(api_key=api_key)
        model = genai.GenerativeModel(
            model_name=MODEL,
            system_instruction=(
                "Eres un analista geoespacial y socioeconómico de Argentina. "
                "Responde solo en español. Sé técnico, preciso y cuantitativo."
            ),
        )
        prompt = build_prompt(analysis_data)
        response = model.generate_content(prompt, stream=True)

        for chunk in response:
            text = chunk.text if chunk.text else ""
            if text:
                payload = json.dumps({"chunk": text}, ensure_ascii=False)
                yield f"data: {payload}\n\n"

    except Exception as e:
        logger.exception("Report generation failed")
        err_msg = f"\n\n⚠ Error generando informe: {e}"
        yield f"data: {json.dumps({'chunk': err_msg})}\n\n"

    yield "data: [DONE]\n\n"
