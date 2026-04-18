```markdown
REVISIÓN TÉCNICA — Elena
Versión revisada: geoenv-platform · commit ~4f1d80f (post migración a google-genai + climatología estacional)
Fecha: 2026-04-18
Alcance: backend (analysis.py, gee_client.py, reporter.py, main.py), frontend (app.js, index.html, style.css), deployment (docker-compose.yml, .env, Traefik)

VEREDICTO: REQUIERE CAMBIOS

Hay un hallazgo crítico de seguridad operacional (clave de API expuesta en canal no controlado) y otro de superficie de ataque (CORS abierto sobre endpoint que consume cuota de un servicio de pago). El resto de la pieza está sorprendentemente bien estructurada para una iteración tan rápida — la lógica científica es defendible y la arquitectura de fetch en paralelo es correcta. Pero no se entrega así. Hay tres cambios bloqueantes que no son refactor, son higiene mínima.

═══════════════════════════════════════════════════════
HALLAZGOS
═══════════════════════════════════════════════════════

[R-001] CRÍTICO — Clave de API de Gemini expuesta en transcripción del chat
Componente: secretos / gestión de credenciales
Problema: La GEMINI_API_KEY (`AIzaSy...`) fue pegada por el usuario en el chat para que yo la pusiera en la VM. Eso quedó registrado en al menos: (a) el JSONL de la sesión de Claude Code en el equipo del usuario, (b) potencialmente logs/telemetría de Anthropic, (c) cualquier sync de OneDrive/Drive sobre la carpeta de proyecto. La clave NO fue rotada después.
Evidencia: el resumen de esta misma sesión cita textualmente "API key AIzaSy..." en la transcripción.
Propuesta:
  1. ROTAR la clave en https://aistudio.google.com/apikey AHORA, antes de cualquier otro cambio.
  2. La nueva clave se inyecta directamente en /opt/mi-stack/.env vía `sudo nano` en la VM, NUNCA se escribe en el chat. Yo la leo con `cat` en la VM si la necesito; nunca la transcribo de vuelta.
  3. Configurar restricciones a la API key en Google Cloud Console: limitar por API (Generative Language API solamente) y por IP de origen (la IP pública de la VM).
  4. Agregar `**/.env` y `**/secrets/*` al `.gitignore` del repo y verificar que el commit actual NO contenga la clave (`git log -p | grep -i "AIzaSy"`).

[R-002] CRÍTICO — CORS abierto + endpoint sin rate-limit que quema cuota ajena
Componente: backend/main.py:43-48 + 76 (POST /analyze)
Problema: `allow_origins=["*"]` combinado con `POST /analyze` (que dispara ~4 fetchs a Earth Engine y puede tardar 10-30s) significa que cualquier sitio web del planeta puede embeber un `<script>` que llame a indicadores.soildecisions.com/analyze en loop. Resultado: agotamiento de cuota de GEE (que es del proyecto personal `ee-angelini75`), facturación inesperada de Gemini, y caída del servicio sin que el dueño se entere.
Evidencia: `curl -X POST https://indicadores.soildecisions.com/analyze -H "Content-Type: application/json" -d '{"lat":-34.6,"lon":-58.4,"scale":"1y"}'` desde cualquier IP funciona.
Propuesta:
  1. `allow_origins=["https://indicadores.soildecisions.com"]` (lista explícita).
  2. Agregar `slowapi` (decorador `@limiter.limit("10/minute")` en /analyze y `5/minute` en /report) — son ~30 líneas.
  3. Loggear IP de origen en cada llamada (`request.client.host`) para detectar abuso.
  4. Plan B si hay urgencia: poner el sitio detrás de auth básica de Traefik (middleware basic-auth) hasta que se implemente rate-limit.

[R-003] CRÍTICO — Falla silenciosa del stream LLM: el error queda enterrado dentro del reporte
Componente: backend/reporter.py:316-325 + frontend SSE handler
Problema: Si Gemini falla a mitad del stream (cuota, timeout, contenido bloqueado por safety filters, red intermitente), el `except` mete el error como UN CHUNK MÁS de texto del informe. El frontend hace `marked.parse()` sobre ese texto y lo muestra como si fuera el cuerpo del análisis. Un usuario que llegue al renglón "⚠ Error generando informe: 429..." después de leer 3 párrafos creíbles no sabe si los 3 párrafos previos son confiables o están truncados a mitad de oración.
Evidencia: ya pasó en producción con el 429 de gemini-2.0-flash — el usuario reportó "⚠ Error generando informe: 429 You exceeded..." mezclado con el HTML del reporte.
Propuesta: usar un evento SSE con `event:` distinto para errores:
```

```yield f"event: error\ndata: {json.dumps({'message': str(e), 'code': type(e).**name**})}\n\n"```



```markdown
y en el frontend manejar `eventSource.addEventListener('error', ...)` mostrando un cartel rojo separado del contenido del informe. Adicional: marcar el reporte como `[INCOMPLETO]` visualmente si el stream cerró sin recibir `[DONE]`.

═══════════════════════════════════════════════════════

[R-004] MAYOR — Baseline climatológico estático 2004-2024 ignora el cambio climático
Componente: backend/analysis.py:37-38 (HIST_START / HIST_END)
Problema: El producto se llama "diagnóstico geoambiental" y reporta z-scores como anomalías. Pero comparar el valor de 2026 contra la media 2004-2024 introduce un sesgo sistemático: si LST tendencialmente subió 1°C en 20 años, todo verano nuevo aparecerá como "anomalía caliente +1.2σ" simplemente porque el baseline está corrido. El usuario va a leer "estrés térmico anómalo" cuando en realidad es la nueva normalidad. Esto es un problema científico, no de código.
Evidencia: la literatura (Anyamba & Tucker 2012, IPCC AR6) recomienda baselines móviles de 30 años o detrending lineal antes del z-score para variables con tendencia.
Propuesta: dos opciones, ordenadas por costo:
  (a) MÍNIMO: documentar la limitación en `socio.assumptions` y en el panel del frontend ("Baseline fijo; z-scores no incorporan tendencia climática"). Ya está parcialmente hecho — reforzar.
  (b) CORRECTO: en `extract_monthly_climatology`, agregar un `ee.Reducer.linearFit()` por mes y reportar z-score residual (valor menos tendencia esperada). Es ~30 líneas de Python y da un número defendible ante un climatólogo.

[R-005] MAYOR — VCI/TCI/VHI fallback con `mean=0.5, std=0.25` produce z-scores fantasma
Componente: backend/analysis.py:558-560
Problema: Cuando `monthly_clim` queda vacío para VCI/TCI (por ejemplo el `_derived_clim` retorna `{}` porque `hmax==hmin`), `_summarize` cae al fallback `vci_alltime = {"mean": 0.5, "std": 0.25, ...}`. El z-score que sale de eso (`(vci_val - 0.5) / 0.25`) NO es una anomalía estadística — es una transformación lineal de un valor que ya estaba en [0,1]. Pero la UI lo va a clasificar como "Anomalía moderada" o "extrema" y el LLM lo va a interpretar como tal.
Evidencia: si NDVI hist_min == hist_max (raro pero posible en un píxel sobre agua o asfalto), todo el pipeline VCI/TCI cae al fallback sin avisar.
Propuesta: si `monthly_clim` viene vacío o `hmax==hmin`, marcar `z_score=None` y `anomaly_class="No calculable"` en lugar de inventar números. El LLM ya sabe ignorar nulls; los números falsos son peores que los faltantes.

[R-006] MAYOR — Sin caché ⇒ cada click cuesta 4 fetchs GEE + 1 llamada Gemini
Componente: backend/main.py:76-99 + analysis.run_analysis
Problema: Si dos usuarios consultan el mismo punto con el mismo scale en la misma hora, GEE se ejecuta dos veces completas (~10-20s c/u) y se gasta cuota duplicada. Con un usuario haciendo doble-click "porque no respondió rápido" ya se duplicó el costo.
Evidencia: no hay capa de caché en main.py ni en analysis.py.
Propuesta: caché en memoria con TTL de 6h, llave = `(round(lat,3), round(lon,3), scale)` (~110m de tolerancia espacial). 15 líneas con `functools.lru_cache` no sirve por el TTL — usar un `dict[tuple, (timestamp, result)]` con limpieza on-write o `cachetools.TTLCache`. Para el reporte LLM, cachear por hash del payload con TTL 1h.

[R-007] MAYOR — ThreadPoolExecutor sin timeout: GEE colgado = request colgado para siempre
Componente: backend/analysis.py:517-524
Problema: `f_veg.result()` sin argumento bloquea indefinidamente. Si GEE tiene latencia alta o un error de red en uno solo de los 4 fetchs, el endpoint /analyze se queda colgado hasta que Uvicorn o Traefik corten por timeout (60s por defecto en Traefik). Mientras tanto el worker de FastAPI está bloqueado.
Propuesta: `f_veg.result(timeout=45)` y `try/except concurrent.futures.TimeoutError` que devuelva 504 con mensaje claro ("Earth Engine no respondió en 45s, reintente"). Adicional: cancelar los demás futures con `pool.shutdown(wait=False, cancel_futures=True)`.

[R-008] MAYOR — La detección de región tiene bboxes superpuestos y orden indeterminado
Componente: backend/analysis.py:364-377
Problema: El bbox de Cuyo `(-34, -28, -70, -64)` se solapa con Pampas `(-38, -30, -65, -57)` en la franja `(-34..-30, -65..-64)`. Un punto en Mendoza norte puede caer en cualquiera de los dos según el orden de iteración del dict. En CPython 3.7+ los dicts preservan orden de inserción, así que hoy "funciona" — pero no por diseño, por accidente.
Evidencia: lat=-31.5, lon=-64.5 cae en ambos rectángulos.
Propuesta: usar polígonos reales de provincias o, mínimo, hacer las regiones mutuamente excluyentes y agregar un test que verifique que ningún punto cae en dos regiones. Alternativa zero-cost: usar geopy + un GeoJSON simplificado de las regiones agroecológicas argentinas (INTA publica uno).

═══════════════════════════════════════════════════════

[R-009] MENOR — Propagación de std en VHI/VCI/TCI matemáticamente incorrecta
Componente: backend/analysis.py:_vhi_clim:268-272 y _derived_clim:231
Problema: `std_vhi = 0.5 * std_vci + 0.5 * std_tci` no es así como se combina la varianza de variables aleatorias. Lo correcto es `sqrt(0.25*var_vci + 0.25*var_tci + 2*0.5*0.5*cov(vci,tci))`, y como VCI/TCI están NEGATIVAMENTE correlacionados (calor → NDVI baja → VCI baja, LST sube → TCI baja), la covarianza importa. El error subestima sistemáticamente la incertidumbre del VHI.
Propuesta: como no se está calculando la covarianza, lo honesto es: `std_vhi = sqrt(0.25*std_vci**2 + 0.25*std_tci**2)` (asume independencia, sobreestima un poco) y documentar el supuesto. Para la versión rigurosa se necesita una segunda pasada por GEE calculando la covarianza temporal — no vale el costo ahora.

[R-010] MENOR — pct_deviation explota cuando hist_mean ≈ 0 (NDWI, MNDWI)
Componente: backend/analysis.py:_pct_dev:57-60
Problema: NDWI y MNDWI pueden tener media estacional cercana a 0 en zonas semiáridas. `(value - 0.001) / abs(0.001) * 100` da valores de miles de porciento que el LLM va a reportar como "+8400% sobre la media" aunque la diferencia absoluta sea trivial.
Propuesta: si `abs(mean) < 0.01` o el rango histórico es comparable a la media, devolver `None` y mostrar solo el z-score (que sí es robusto). O usar una desviación absoluta en unidades del índice cuando el porcentaje no aplica.

[R-011] MENOR — `monthly_clim` se devuelve crudo al frontend y al LLM (12 meses × mean+std × N índices)
Componente: backend/analysis.py:_summarize:192 ("monthly_clim": monthly_clim)
Problema: El payload JSON de /analyze tiene ~10 índices × 12 meses × 2 floats = 240 floats redundantes con `seasonal_curve` (que ya tiene las medias). Infla el payload y el contexto del LLM sin agregar info útil al cliente.
Propuesta: dejar `seasonal_curve` (12 medias) y eliminar `monthly_clim` del payload de /analyze. El reporter ya construye `_compact_curve` desde `seasonal_curve` — no usa `monthly_clim`.

[R-012] MENOR — Macroeconomía hardcodeada con fecha de caducidad
Componente: backend/analysis.py:_socio:446-448
Problema: "inflación ~70% i.a. · tipo de cambio ~$1,100/USD" está literalmente embebido en el código. En 6 meses ese párrafo será incorrecto y el LLM lo repetirá igual porque viene en el JSON.
Propuesta: mover a un archivo `macro_context.json` versionado (con fecha de actualización) y loggear una advertencia si la fecha tiene más de 90 días. Plan ideal: scrappear INDEC/BCRA con caché diario, pero eso es un proyecto separado.

═══════════════════════════════════════════════════════

[R-013] OBSERVACIÓN — No hay tests
Componente: todo el repo
Problema: Cero pytest. La lógica de z-score, climatología estacional, OHLC, propagación VCI/TCI no tiene red. El próximo refactor va a ciegas.
Propuesta: empezar por lo barato — `test_analysis.py` con 3 tests: (a) `_zscore` con casos borde (std=0, value=None), (b) `_monthly_candles` con 30 puntos sintéticos verificando que open/close/high/low coinciden, (c) `_derived_clim` con hmin=hmax devuelve {}. 50 líneas, atrapan el 80% de las regresiones.

[R-014] OBSERVACIÓN — El INDEX_DEFS se reenvía completo a Gemini en cada llamada
Componente: backend/reporter.py:build_llm_payload
Problema: Los ~3KB de definiciones de índices son texto estático que no cambia entre llamadas. Si el modelo lo soportara, prompt caching ahorraría tokens. Hoy `gemini-3.1-flash-lite-preview` no expone explicit cache, pero el implicit cache del SDK puede aprovecharlo si el system_instruction es estable.
Propuesta: mover INDEX_DEFS al system_instruction (no cambia nunca) y dejar en el user prompt solo los datos del análisis. Con flash-lite probablemente no haga diferencia notable de costo, pero es la disposición correcta.

[R-015] OBSERVACIÓN — `.env` por symlink a /opt/mi-stack/.env es frágil para deploy
Componente: docker-compose.yml + workflow de deploy
Problema: La estrategia "git pull en /opt/mi-stack/geoenv + symlink al .env del directorio padre" funciona pero no está documentada. Si alguien clona el repo en otro lado o el symlink se rompe en una migración de host, el GEMINI_API_KEY queda vacío y los reportes fallan con un error críptico (R-003).
Propuesta: README de deploy con los 3 comandos (`git config safe.directory`, `ln -sf ../.env .env`, `docker compose up -d`) y un `make deploy` que los encapsule. Bonus: un `make doctor` que verifique que GEMINI_API_KEY y GEE creds están presentes y válidos antes de levantar.

═══════════════════════════════════════════════════════
PUNTOS POSITIVOS
═══════════════════════════════════════════════════════

- **Climatología estacional bien implementada.** El `extract_monthly_climatology` con 12 meses combinados en un solo `ee.Image.cat` y un único `getInfo()` es el patrón correcto en GEE — ahorra 11 round-trips. Y el z-score "valor actual vs media del MISMO mes calendario" es la decisión científicamente correcta para variables fenológicas. Eso vale más que la mayoría del código.

- **ThreadPoolExecutor para los 4 fetchs.** Reduce latencia perceptible al usuario de ~40s a ~12s. Decisión correcta para una API I/O-bound.

- **SSE streaming del reporte LLM.** El usuario ve texto fluyendo en lugar de un spinner de 30s. Es la diferencia entre "esto funciona" y "esto se siente roto".

- **Pydantic con bbox de Argentina en AnalyzeRequest.** Valida lat/lon en el borde de la API, no en el corazón del análisis. Bien.

- **Migración limpia a `google-genai` SDK.** El SDK viejo está deprecado; la migración a `client.models.generate_content_stream` con `types.GenerateContentConfig` es exactamente como Google lo recomienda hoy.

- **INDEX_DEFS estructurado por índice.** Definición + estacionalidad + interpretación de anomalías positiva/negativa. El LLM tiene el contexto suficiente para no inventar interpretaciones; cumple lo que el cliente pidió explícitamente.

═══════════════════════════════════════════════════════
DEUDA TÉCNICA ACEPTADA (no bloquea, registrar para v2.1)
═══════════════════════════════════════════════════════

- Sin autenticación de usuario (API pública). Aceptable mientras la cuota esté detrás de rate-limit (R-002). Re-evaluar si se monetiza.
- Caché en memoria local (R-006) en lugar de Redis. Aceptable mientras haya un solo container; obligatorio migrar a Redis si se escala a múltiples workers/réplicas.
- LLM no cita fuentes ni números específicos verificables. Aceptable para un informe ejecutivo; inaceptable si esto se usa para decisiones de seguros agrícolas. Documentar en disclaimer del frontend.
- Detección de "región" por bbox simplificado (R-008) en lugar de polígonos provinciales. Aceptable mientras la región solo se use para texto descriptivo y selección de cultivos típicos.

═══════════════════════════════════════════════════════
ACCIÓN INMEDIATA (orden estricto)
═══════════════════════════════════════════════════════

1. Rotar GEMINI_API_KEY (R-001) — 5 minutos.
2. Cerrar CORS + agregar slowapi (R-002) — 30 minutos.
3. Separar event:error en SSE (R-003) — 30 minutos.

Con esos tres cambios el veredicto pasa a APROBADO CON OBSERVACIONES y se puede mostrar a un cliente sin riesgo operacional. El resto se prioriza para la siguiente iteración.

— Elena
```