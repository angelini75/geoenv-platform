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

MODEL = "gemini-3.1-flash-lite-preview"   # cost-efficient, free-tier compatible

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
    "NBR": {
        "nombre": "Ratio de Quema Normalizado",
        "formula": "(NIR − SWIR2) / (NIR + SWIR2)",
        "rango_tipico": "−1 a +1; valores bajos post-fuego",
        "fuente": "MODIS MOD09A1, 500 m",
        "estacionalidad": "Sensible a eventos de fuego; valores bajos = área quemada o degradada.",
        "anomalia_positiva": "Vegetación íntegra, mayor biomasa.",
        "anomalia_negativa": "Posible quema reciente, degradación o pérdida de biomasa.",
    },
}


# ---------------------------------------------------------------------------
# Build the structured JSON payload for the LLM
# ---------------------------------------------------------------------------

def _compact_curve(seasonal_curve: dict | None) -> dict:
    """Convert {month_int: mean_or_none} to {month_abbr: mean_or_null}."""
    abbrev = {1:"Ene",2:"Feb",3:"Mar",4:"Abr",5:"May",6:"Jun",
              7:"Jul",8:"Ago",9:"Sep",10:"Oct",11:"Nov",12:"Dic"}
    if not seasonal_curve:
        return {}
    return {abbrev[int(m)]: round(v, 4) if v is not None else None
            for m, v in seasonal_curve.items()}


def _recent_candles(candles: list, n: int = 6) -> list:
    """Return last N monthly candles in compact form."""
    if not candles:
        return []
    return [
        {"mes": c["period"][:7], "O": c["open"], "H": c["high"],
         "L": c["low"], "C": c["close"], "z": c["z_close"],
         "clase": c["anomaly_class"]}
        for c in candles[-n:]
    ]


def build_llm_payload(data: dict) -> dict:
    """
    Build a structured JSON payload that contains everything the LLM needs:
    definitions, seasonal curves, current values, recent candles,
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
        defn = INDEX_DEFS.get(key.upper(), {})
        indices_payload[key.upper()] = {
            "definicion": {
                "nombre":           defn.get("nombre", key.upper()),
                "formula":          defn.get("formula", ""),
                "rango_tipico":     defn.get("rango_tipico", ""),
                "fuente":           defn.get("fuente", ""),
                "estacionalidad":   defn.get("estacionalidad", ""),
                "anomalia_positiva":defn.get("anomalia_positiva", ""),
                "anomalia_negativa":defn.get("anomalia_negativa", ""),
            },
            "datos": {
                "valor_actual":            d.get("current"),
                "media_estacional_mes_actual": d.get("hist_mean"),   # current-month seasonal mean
                "desvio_estacional":       d.get("hist_std"),
                "z_score_estacional":      d.get("z_score"),         # vs same calendar month
                "desviacion_pct":          d.get("pct_deviation"),
                "clase_anomalia":          d.get("anomaly_class"),
                "hist_min_20yr":           d.get("hist_min"),
                "hist_max_20yr":           d.get("hist_max"),
                "curva_estacional_medias": _compact_curve(d.get("seasonal_curve")),
                "velas_recientes_6m":      _recent_candles(d.get("candlesticks", [])),
                "n_observaciones_periodo": d.get("n_observations", 0),
            },
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
            "macro_estimado":          socio["macro"],
            "cadena_causalidad":       socio["causality_chain"],
            "supuestos":               socio["assumptions"],
        },
    }


SYSTEM_INSTRUCTION = """Eres un analista geoespacial y socioeconómico especializado en Argentina.
Tu tarea es redactar informes técnicos basados en índices satelitales MODIS y datos climatológicos.

CONOCIMIENTO CLAVE:
- Los z-scores en este sistema son ESTACIONALES: cada índice se compara contra la media del MISMO mes
  calendario a lo largo de 20 años (2004-2024), no contra un promedio anual.
  Esto corrige la estacionalidad natural (ej: NDVI alto en enero no es anomalía si enero siempre es alto).
- La curva_estacional_medias muestra el patrón típico mensual de cada índice (12 valores).
- VHI < 0.2 = emergencia de sequía; 0.2-0.35 = sequía severa.
- Z-score > +2σ o < −2σ = anomalía extrema respecto al mismo mes histórico.
- Las velas recientes muestran la tendencia de los últimos 6 meses (OHLC mensual).

Escribe en español técnico, con precisión cuantitativa. Usa Markdown (## y **negritas**).
Objetivo: 600–900 palabras en 5 secciones ordenadas."""


def build_prompt(payload: dict) -> str:
    payload_json = json.dumps(payload, ensure_ascii=False, indent=2)
    return f"""A continuación encontrarás el JSON completo del análisis ambiental.
Cada índice incluye su definición, interpretación de anomalías, valor actual,
media estacional del mes en curso, z-score estacional, curva de estacionalidad
(media de los últimos 20 años por mes calendario) y las últimas 6 velas mensuales.

## DATOS DEL ANÁLISIS (JSON)

```json
{payload_json}
```

---

## INSTRUCCIONES DEL INFORME

Redacta el informe con estas 5 secciones en orden estricto:

1. **Resumen ejecutivo** (3–4 oraciones): situación general, indicador de riesgo, región, estación.

2. **Estado del ecosistema**: analiza cuantitativamente cada grupo de índices
   (vegetación: NDVI/EVI/SAVI, agua/sequía: NDWI/MNDWI/VCI/VHI, temperatura: LST/TCI, fuego: NBR).
   Para cada uno menciona el valor actual vs la media estacional del mes, el z-score y su severidad.
   Aprovecha la curva estacional para contextualizar si el valor es esperable para esta época del año.

3. **Análisis de tendencia temporal**: interpreta las velas mensuales recientes (últimos 6 meses).
   ¿La situación mejora, empeora o es estable? ¿Hay cambio de tendencia reciente?

4. **Impacto productivo-económico**: conecta los indicadores con la producción agropecuaria
   y el contexto macroeconómico. Sigue la cadena causal proporcionada en los datos.
   Menciona cultivos en riesgo y cuantifica el impacto esperado.

5. **Nivel de riesgo y recomendaciones**: concluye con el indicador de situación,
   las variables que lo determinan, y 2–3 acciones concretas y accionables
   (monitoreo satelital, decisiones agronómicas, alertas tempranas).

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
        client  = genai.Client(api_key=api_key)
        payload = build_llm_payload(analysis_data)
        prompt  = build_prompt(payload)

        response = client.models.generate_content_stream(
            model=MODEL,
            contents=prompt,
            config=types.GenerateContentConfig(
                system_instruction=SYSTEM_INSTRUCTION,
                temperature=0.3,
                max_output_tokens=2048,
            ),
        )

        for chunk in response:
            text = chunk.text or ""
            if text:
                payload_sse = json.dumps({"chunk": text}, ensure_ascii=False)
                yield f"data: {payload_sse}\n\n"

    except Exception as e:
        logger.exception("Report generation failed")
        err_msg = f"\n\n⚠ Error generando informe: {e}"
        yield f"data: {json.dumps({'chunk': err_msg})}\n\n"

    yield "data: [DONE]\n\n"
