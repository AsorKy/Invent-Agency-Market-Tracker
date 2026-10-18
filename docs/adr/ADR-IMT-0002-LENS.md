# ADR-IMT-0002-LENS — Lentes analíticas, subcapas y capa de validación

| Field | Value |
|---|---|
| ADR | ADR-IMT-0002-LENS |
| Version | 1.1 |
| Layer / run | L2 / Round 2: Analytics layer: descriptive/diagnostic/predictive lenses, deterministic funnel, validation V1-V6 |
| Status | accepted (promoted), decided 2026-10-18 |
| Authority | Source of truth for implementation (see `AGENTS.md`). On conflict, the higher-numbered amendment wins over the base text |
| Language | Spanish. All other repository documentation is English |
| Origin | Exported from the project vault on 2026-10-18. Do not edit here; see `docs/README.md` |


> **Status:** accepted · **Decided:** 2026-10-18 · **Supersedes:** — · **Acronym:** LENS
>
> **Toda feature de la plataforma se organiza, se construye y se evalúa a través de 5 capas ortogonales a los servicios: 3 lentes analíticas (descriptiva, diagnóstica, predictiva), 1 capa prescriptiva (= funnel determinista, §2.4), y 1 capa de validación de proyectos con 6 subcapas. Cada capa responde a una pregunta de negocio distinta, se corre a una cadencia distinta y tiene sus propias métricas de calidad.** Esta decisión no cambia servicios ni schemas; añade un eje mental y operativo que recorre todo.

---

## 1. Context & Problem

[ADR-IMT-0001-ARCH](ADR-IMT-0001-ARCH.md) definió **servicios y topología** (7 servicios + streamlit, outbox+inbox, mapa de schemas y puntos de extensión en §E2). Lo que **no** definió es:

- **Cómo se organizan las técnicas de ML/analytics por propósito de negocio**, en vez de por servicio donde viven. Un rollup en `pulse` y una métrica de precisión del modelo en `ml` son los dos descriptivos, aunque vivan en servicios distintos.
- **Qué métricas de calidad acompañan cada análisis** — no basta con reportar un AUC; un diagnóstico sin intervalo de confianza es peor que no tenerlo.
- **Cómo las métricas de evaluación de proyectos** (financieras, unit economics, growth, portafolio, meta-ML) se enganchan con las lentes analíticas que las producen.
- **El patrón mental** que todo miembro del equipo debe aplicar antes de pedir una feature nueva.

La tensión concreta: ARCH-0001 acumula técnicas (BERTopic, UMAP+HDBSCAN, Pirámide ICP, uplift, DID, drift…) **sin organizarlas por para qué pregunta de negocio sirven**. El riesgo es construir modelos que nadie usa, dashboards que mienten sobre su freshness, o métricas financieras calculadas sobre datos que ya no son representativos.

**La pregunta que resuelve este ADR:** ¿cuál es la organización analítica canónica del proyecto, qué subcapas tiene cada lente, qué métricas acompañan cada análisis, y cómo las lentes alimentan la validación de proyectos?

**Nota conceptual (madurez analítica Gartner):** las 3 lentes de datos (descriptiva/diagnóstica/predictiva) + prescriptiva (= funnel determinista, §2.4) completan la escalera clásica. Validación cierra el círculo midiendo si todo funcionó.

## 2. Decision

Toda feature de la plataforma pertenece a **al menos una de 5 capas**:

```
   ¿Qué pasó?          →  DESCRIPTIVA     ←  dashboards, perfiles, topic modeling
   ¿Por qué pasó?      →  DIAGNÓSTICA     ←  causalidad, drift, SHAP, atribución
   ¿Qué va a pasar?    →  PREDICTIVA      ←  scoring, forecast, Pirámide ICP, simulación
   ¿Qué debo hacer?    →  PRESCRIPTIVA    ←  funnel determinista + uplift + bandits
                                              (= funnel determinista, §2.4)
   ¿Funcionó?          →  VALIDACIÓN      ←  V1 financiera · V2 unit econ · V3 funnel ·
                                              V4 growth · V5 portafolio · V6 meta-ML
```

Las lentes **no son silos físicos**; son un eje transversal. Un `signal.created` atraviesa las 5 capas en segundos; un `project` las atraviesa en meses. Las secciones §2.1–§2.6 detallan cada una.

### 2.1 Lente DESCRIPTIVA — subcapas

Responde "¿qué pasó?". Es el cimiento — sin esto no hay nada que predecir ni diagnosticar.

**Métricas de calidad del propio análisis descriptivo:** freshness (`as_of`), completitud (% de empresas con feature X), consistencia inter-fuente, cobertura temporal.

**Cadencia:** continua (eventos) + rollup diario.

#### Subcapas y herramientas

| Subcapa | Qué hace | Técnicas | Dónde vive |
|---|---|---|---|
| **D1 Agregación** | Rollups por evento, estadística básica, concentración | `GROUP BY` + CTEs · percentiles · HHI · Gini · tasa de cambio | Servicio `pulse` |
| **D2 Perfilado** | Clustering interpretable, pertenencia a arquetipo | **K-Prototypes** · **Leiden** (comunidades) · **centralidad** (degree, PageRank) | Servicio `ml` |
| **D3 Topic / semántica** | Qué temas aparecen en texto, qué palabras repiten | **LDA** + diccionarios + regex + `YAKE` · TF-IDF | Worker en `ml`, resultado en `pulse` |
| **D4 Visualización** | Mapas, series, tablas, cross-filter | Streamlit + Plotly + pydeck · Next.js KPI tiles | Servicio `streamlit` + Next.js |

#### Métricas estadísticas estándar (en el nivel D1)

Cosas básicas que suelen quedar implícitas y conviene nombrar:

- **Centrales**: media, mediana, moda, geomedia (buena para ratios como CAC payback)
- **Dispersión**: desviación estándar, MAD (median absolute deviation, robusto), IQR
- **Forma**: skewness, kurtosis (indican si una distribución es "normal" o sesgada)
- **Concentración**: **HHI** (Herfindahl, concentración de mercado), **Gini** (desigualdad), curva de Lorenz
- **Rankings**: percentiles p10/p50/p90 (siempre reportar al menos estos tres), top-K
- **Freshness**: `as_of`, latencia media/p99, % de datos > 26h

Todo esto va en los endpoints `/api/v1/pulse/*` como campos del response, no como gráficos aparte.

#### Métricas descriptivas **de proyecto** embebidas aquí

- Tamaño actual del pipeline (señales activas, leads por fase)
- Distribución de scores (histograma, percentiles)
- Burn hasta la fecha vs plan
- Pipeline value por línea de servicio
- Tiempo mediano en cada fase del funnel

### 2.2 Lente DIAGNÓSTICA — subcapas

Responde "¿por qué pasó eso?". Es el músculo que separa una agencia profesional de una que dispara al aire.

**Métricas de calidad del propio diagnóstico:** ATE con IC, p-value, elasticidad, importancia de feature, PSI (drift).

**Cadencia:** on-demand + revisión mensual.

#### Subcapas y herramientas

| Subcapa | Qué hace | Técnicas | Dónde vive |
|---|---|---|---|
| **Dx1 Atribución** | Qué canal/fuente/campaña trajo qué resultado | **Last-touch**, **time-decay**, **Markov attribution**, **Shapley attribution** | Worker en `ml` → `pulse.attribution` |
| **Dx2 Inferencia causal** | Efecto **real** de una acción, aislado de confounders | **DID** (`linearmodels`), **Synthetic control** (`pysyncon`), **PSM** (`dowhy`), **IV** cuando aplica | Worker on-demand en `ml` |
| **Dx3 Anomalía + drift** | Qué cambió en el flujo de datos o en el performance del modelo | **CUSUM / EWMA / change point** (`ruptures`) · **PSI** (Population Stability Index) · **Evidently** | Worker nocturno en `ml` |
| **Dx4 Explicabilidad** | Por qué el modelo dijo lo que dijo; qué palanca mover | **SHAP** · **counterfactual explanations** (`dice-ml`) · árbol surrogate | Worker en `ml`, resultado en Brief |

#### Métricas de calidad de **la propia inferencia causal**

Un diagnóstico sin incertidumbre es peor que no tenerlo. Siempre reportar:

- **ATE** (Average Treatment Effect) con **IC 95%**
- **ATT** (en los tratados) y **ATU** (en los no tratados) por separado
- **p-value** y **effect size** (Cohen's d) — rechazar p-hacking: effect size pequeño con p significativo no vale
- **Robustness checks**: cambiar la ventana, cambiar el control sintético, reportar varias — si cambia el signo, no concluir
- **E-value**: cuánto confounding invisible haría falta para invalidar el resultado

#### Patrones de atribución que valen la pena

En orden de complejidad:

| Modelo | Qué asume | Cuándo |
|---|---|---|
| **Last-touch** | El último contacto se lleva todo el crédito | Baseline conservador; nunca creerlo pero mostrarlo como referencia |
| **First-touch** | El primer contacto se lleva todo el crédito | Comparar con last-touch para ver si el funnel es largo |
| **Time-decay** | Peso exponencial decreciente hacia atrás | Útil en funnels medianos (semanas) |
| **Markov attribution** | Probabilidad de llegar al cierre dado el camino; crédito proporcional al **removal effect** | **El estándar razonable** para IMT: no requiere entrenamiento supervisado, es interpretable |
| **Shapley value attribution** | Crédito "justo" basado en teoría de juegos cooperativos | Máxima calidad; costoso computacionalmente — F6+ |

**Recomendación**: Markov attribution en F5, Shapley en F6 solo si hay > 100 cierres etiquetados.

#### Métricas diagnósticas **de proyecto** embebidas

- **Lift** de una campaña / canal / mensaje (DID real)
- **Elasticidad** del precio (cuánto baja la conversión si subes 10% la tarifa)
- **Drift score** del modelo de scoring (si > umbral → alerta)
- **Model utility**: cuántos USD netos atribuibles al modelo vs decisión manual
- **Root cause** cuando un KPI cae: change point + factor decomposition

### 2.3 Lente PREDICTIVA — subcapas

Responde "¿qué va a pasar?". Es la más vistosa pero también la más fácil de hacer mal. Siempre con intervalo de incertidumbre.

**Métricas de calidad del propio modelo predictivo:** AUC, calibración (Brier), lift@K, NDCG, intervalo P90/P10 del forecast.

**Cadencia:** diaria/semanal, por `source_version`.

#### Subcapas y herramientas

| Subcapa | Qué hace | Técnicas | Dónde vive |
|---|---|---|---|
| **P1 Scoring** | Probabilidad de conversión por lead/empresa | **Regla ponderada** F1 → **LightGBM** calibrado F6 · uplift modeling | Servicio `ml` |
| **P2 Forecasting** | Series temporales (volumen, ingresos, costos) | **StatsForecast** (ETS, AutoARIMA, Theta) · **Prophet** si hay estacionalidad fuerte · hierarchical reconciliation | Worker nocturno en `ml` |
| **P3 Match + lookalike** | Buscar empresas parecidas a X; filtrar por perfiles ICP | **Pirámide ICP** · k-NN sobre `pgvector` · learning-to-rank (`LGBMRanker`) | Servicio `ml` |
| **P4 Simulación** | Distribución de resultados posibles bajo supuestos | **Monte Carlo** (`numpy`) · árboles de decisión bayesianos · scenario analysis | Worker on-demand del funnel (en `core`) |

#### Métricas de calidad del propio modelo predictivo

Rechazar modelos que solo reportan AUC. Pedir el pack completo:

**Para clasificación:**
- **AUC-ROC** (0.5 random, 0.8+ útil) **y** PR-AUC (más robusto con clases desbalanceadas como en IMT)
- **Calibración** (reliability diagram + Brier score): si el modelo dice 70%, de 100 casos así ¿convierten 70?
- **Lift@K**: cuánto mejor que random en el top K
- **Precision@K** por sub-población (ciudad, línea de servicio) — nunca una métrica global sin desagregar

**Para ranking:**
- **NDCG@K** (normalized discounted cumulative gain)
- **MAP** (mean average precision)
- **MRR** si solo importa el primer click

**Para forecasting:**
- **MAPE** (fácil de leer pero rompe con ceros) **o** **sMAPE**
- **MASE** (relativo a baseline naïve — el estándar correcto)
- **Pinball loss** si se reportan intervalos (P10/P50/P90)
- **Coverage** del IC: si el P90 debe cubrir el 90% real, verificarlo

**Para uplift:**
- **Qini coefficient** (área bajo la curva Qini)
- **Uplift@K**: ganancia incremental en el top K
- **Policy value**: valor esperado de aplicar la política derivada

#### Métricas predictivas **de proyecto** embebidas

- **Pipeline forecast**: cuántos cierres esperados en los próximos 90 días (con IC)
- **TAM/SAM/SOM** con intervalo
- **NPV distribution** del proyecto (via Monte Carlo)
- **Probability of success** = prob(NPV > 0) × prob(payback < horizon)
- **Risk-adjusted return**: NPV_mean / NPV_std

### 2.4 Capa PRESCRIPTIVA — funnel determinista

Responde "¿qué debo hacer?". **Es el funnel determinista definido aquí; [ADR-IMT-0001-ARCH](ADR-IMT-0001-ARCH.md) §E2.2/§E2.3 reserva su ubicación en `core` (7 gates: G1 Discovery → G2 Qualify → G3 Validate → G4 Design → G5 Launch → G6 Scale → G7 Sunset)**. No se reemplaza aquí; se nombra y se identifica como la 4ª capa de madurez analítica.

Insumos que recibe de las lentes anteriores:
- De **Descriptiva**: tamaño actual del pipeline, distribución de scores
- De **Diagnóstica**: drift del modelo, atribución por canal, root cause de KPIs caídos
- De **Predictiva**: scores, forecasts, Pirámide ICP matches, Monte Carlo

Outputs (emitidos por `core`): `funnel.gate_decision` (veredicto ADVANCE/PIVOT/KILL/WAIT), `funnel.pivot_requested`, `funnel.project_killed`, `funnel.kill_trigger_event`.

### 2.5 Capa de VALIDACIÓN de proyectos — 6 subcapas

Responde "¿funcionó?". Es **ortogonal** a las 3 lentes: toma outputs de las tres y los convierte en métricas estándar de evaluación de proyectos.

**Las 6 subcapas:**

| Subcapa | Qué mide | Métricas específicas |
|---|---|---|
| **V1 Financiera** | ¿El proyecto genera valor? | **NPV**, **IRR**, **Payback period**, **DPV** (discounted payback), **Expected Monetary Value (EMV)** |
| **V2 Unit economics** | ¿Cada unidad vendida es rentable? | **CAC**, **LTV**, **LTV:CAC ratio** (benchmark 3:1), **CAC payback** (meses), **gross margin** |
| **V3 Funnel y conversión** | ¿El pipeline funciona? | **Conversion rate** por gate, **drop-off** por fase, **win rate**, **cycle time**, **pipeline velocity**, **pipeline coverage** (3-4× cuota es sano) |
| **V4 Growth y retención** | ¿El negocio crece y sostiene? | **MRR / ARR**, **NRR** (net revenue retention), **GRR** (gross), **churn** (logo + revenue), **Rule of 40**, **Burn multiple**, **Quick ratio**, **Magic Number** |
| **V5 Portafolio** | ¿La mezcla de proyectos es saludable? | **Horizon mix** (H1/H2/H3 — McKinsey), **concentración** (HHI por línea), **Sharpe-like ratio** (retorno/varianza), **correlación entre iniciativas** |
| **V6 Meta-ML** | ¿La plataforma analítica misma aporta? | **Model utility**, **decision latency**, **drift score**, **precision@K real**, **lift del funnel vs baseline histórico** |

#### V1 Financiera — detalle

Cada métrica tiene un umbral de decisión asociado al funnel:

| Métrica | Fórmula | Umbral acción |
|---|---|---|
| **NPV** | Σ CF_t / (1+r)^t | `NPV_p10 > 0` → avanza a G4; `< 0` en todos los escenarios → kill |
| **IRR** | r tal que NPV = 0 | `IRR > hurdle rate` (15-25% CO) → avanza |
| **Payback** | tiempo hasta `cumulative CF ≥ 0` | `< 18 meses` para H1 Software; `< 24 meses` H2; H3 relaja |
| **DPV** | payback descontado | Más honesto que payback cuando r es alto |
| **EMV** | Σ (prob × valor) | Útil cuando el proyecto tiene árboles de decisión |

#### V2 Unit economics — las que importan

Son las que un VC pregunta al minuto 2 de la reunión:

- **CAC** = costo total adquisición / clientes nuevos
- **LTV** = ARPA × margen bruto × (1/churn mensual)
- **LTV:CAC**: **< 1 → muerte**, 1-3 → vigilar, **3-5 → sano**, > 5 → probablemente estás sub-invirtiendo en marketing
- **CAC payback**: meses para recuperar el CAC con flujo mensual bruto; **< 12 meses sano**, > 18 → preocupante
- **Gross margin**: (revenue − COGS) / revenue; en SaaS puro 70-80%, en servicios 40-60%

#### V3 Funnel metrics

- **Conversion rate por gate G_i→G_j** — compara contra benchmark de la agencia por línea
- **Pipeline velocity** = (deals × avg deal size × win rate) / sales cycle length
- **Pipeline coverage** = valor del pipeline en Qn / cuota Qn; **3-4× es sano**
- **Win rate** = ganados / (ganados + perdidos) — tracker por mes móvil
- **Cycle time** = días en pipeline, p50 y p90
- **Drop-off**: % que se queda atascado en cada fase > N días

#### V4 Growth metrics (para F6+)

- **MRR** (monthly recurring revenue) + descomposición: new + expansion − contraction − churn = net new MRR
- **NRR** = (starting MRR + expansion − contraction − churn) / starting MRR; **> 100% es excelente**, > 120% es world-class
- **GRR** = NRR sin contar expansión; **> 90% sano**
- **Rule of 40**: growth% + profit margin% ≥ 40 → saludable para SaaS
- **Burn multiple** = net burn / net new ARR; **< 1 excelente**, 1-2 bueno, 2-3 vigilar, > 3 kill
- **Magic Number** = ΔARR / S&M del trimestre anterior; **> 1 sano**

#### V5 Portafolio metrics

Para no tener 15 proyectos H3 y ninguno H1:

- **Horizon mix** (McKinsey Three Horizons): H1 (core, ingreso hoy), H2 (adyacente, 12-24m), H3 (apuesta, 24m+). Target: 60/30/10 en ingresos
- **Concentración (HHI)** por línea de servicio: si un cliente aporta > 30% del ARR → riesgo
- **Sharpe-like**: (NPV_mean − baseline) / NPV_std — permite rankear proyectos ajustados por riesgo
- **Correlación entre iniciativas**: dos proyectos H1 que dependen del mismo canal tienen correlación 1 — amplifica riesgo

#### V6 Meta-ML metrics (salud de la plataforma analítica)

- **Model utility**: USD atribuibles al modelo vs baseline (DID sobre periodo pre/post deploy)
- **Decision latency**: tiempo entre `funnel.gate_decision` (candidato presentado) y la confirmación humana — si crece, modelo mal calibrado o equipo saturado
- **Drift score (PSI)**: < 0.1 estable, 0.1-0.25 vigilar, > 0.25 reentrenar
- **Precision@K real**: de los top-K marcados hot, ¿cuántos cerraron?
- **Lift del funnel**: antes del sistema, X% de ideas se formulaban; después, Y% — Y/X es el lift

### 2.6 Mapeo integrado — flujo end-to-end

El diseño cierra cuando una **señal cruda** atraviesa las 5 capas y acaba en un **gate decision**. Trazado con un ejemplo concreto:

> **Evento:** aparece una vacante nueva de "Desarrollador Senior PHP" en Computrabajo para empresa `NIT X` en Medellín, abierta hace 65 días.

| Paso | Capa | Subcapa | Qué hace | Output |
|---|---|---|---|---|
| 1 | DESCRIPTIVA | D1 Agregación | Registra en `pulse.fact_signal` | — |
| 2 | DESCRIPTIVA | D3 Topic | Regex: `php` → stack obsoleto; "senior" + "65 días abierta" → señal fuerte | tags: `[stack:php, hiring_stale:true]` |
| 3 | PREDICTIVA | P1 Scoring | Regla: `vacante_stale_tech` = +15 pts; `NIT en RUES, 150 empleados` = +10 pts | `score=78, cold`→`hot` |
| 4 | PREDICTIVA | P3 Match | Pirámide ICP contra perfiles del proyecto "Consultoría Transformación Digital" | matches perfil `ideal` dist=0.21 |
| 5 | DIAGNÓSTICA | Dx4 Explicabilidad | SHAP: driver principal = `hiring_stale`, secundario = `size_match` | drivers con pesos |
| 6 | **PRESCRIPTIVA** (= funnel) | G2 Qualify | Checklist auto: 7/10 ítems pasan; `rues_match=true`; `score>65` | `ADVANCE` |
| 7 | VALIDACIÓN | V3 Funnel | Incrementa `pipeline_value` del proyecto en `avg_deal_size × 0.3` (prob) | pipeline updated |
| 8 | VALIDACIÓN | V1 Financiera | Monte Carlo re-run: NPV_p10 sube 2% | forecast refreshed |

Si en el paso 6 el score fuese 55, el flujo pararía con `PIVOT` o `WAIT` sin tocar validación. Si pasa pero el checklist experto dice "legal_risk=high", se mata.

#### Catálogo de eventos entre capas

Al traducir lo anterior en el patrón outbox+inbox:

```
signal.created          (D1)
  → signal.tagged       (D3)
  → signal.scored       (P1)
  → company.matched     (P3)
  → signal.explained    (Dx4)
  → funnel.gate_decision    (funnel; veredicto ADVANCE/PIVOT/KILL/WAIT)
  → project.pipeline_updated (V3)
  → project.forecast_updated (V1 + V4)
  → review.candidate       (si V5 detecta desalineación de portafolio)
  → component.evaluated    (ADR-0003 §2.7; veredicto ADOPT/ITERATE/KILL de un componente)
  → funnel.kill_trigger_event (si un umbral de validación dispara stop-loss)
```

> **Nombres canónicos de eventos del funnel** (los emite `core`, no un servicio `funnel`): `funnel.gate_decision`, `funnel.pivot_requested`, `funnel.project_killed`, `funnel.kill_trigger_event`, y `component.evaluated` para la gobernanza de componentes analíticos (ADR-0003). Cualquier consumidor del outbox se suscribe a estos nombres exactos.

Cada evento lleva `source_version` que permite trazar la cadena entera para auditar una decisión.

### 2.7 Arquitectura analítica final — dónde vive cada subcapa

Mapeo a los servicios de la arquitectura actual ([ADR-IMT-0001-ARCH](ADR-IMT-0001-ARCH.md)):

| Capa × Subcapa | Servicio responsable | Cadencia |
|---|---|---|
| **D1** Agregación | `pulse` (worker + API) | Por evento |
| **D2** Perfilado | `ml` (worker nocturno) | Semanal |
| **D3** Topic/semántica | `ml` (worker) | Semanal; LDA mensual |
| **D4** Visualización | `streamlit` + Next.js | On-demand |
| **Dx1** Atribución | `ml` (worker) → `pulse.attribution` | Nocturna |
| **Dx2** Causalidad | `ml` (worker on-demand desde funnel) | Manual + revisión mensual |
| **Dx3** Anomalía + drift | `ml` (worker) → alertas vía `n8n` | Diaria |
| **Dx4** Explicabilidad | `ml` (sync, en respuesta a `brief.regenerate`) | On-demand |
| **P1** Scoring | `ml` (sync + worker) con single-flight | Por evento |
| **P2** Forecasting | `ml` (worker) → `pulse.forecast` | Semanal; en-demand al abrir funnel |
| **P3** Match + lookalike | `ml` (sync; Pirámide ICP) | Query time |
| **P4** Simulación | funnel worker (en `core`) usando `ml.forecast` | On-demand al pasar G3 |
| **V1-V6** Validación | funnel/validación worker (en `core`) consumiendo de `pulse` y `ml` | Trigger-based + mensual |

**Patrón importante**: la Capa de Validación no vive en `ml` ni en `pulse`; vive en **`core`**, junto al funnel determinista (las tablas `pipeline_stage` y `project` están en el schema `core` — ADR-0001 §E2.1). No existe un servicio `funnel` separado: el funnel y la validación son una **capa lógica dentro de `core`**, servida por sus workers. Es la capa prescriptiva extendida. Toma inputs de las otras lentes y los **traduce a métricas de proyecto que disparan gates y kill triggers**.

**Nuevo schema sugerido en Postgres: `validation`, propiedad del servicio `core`** (owner `core_owner`; no hay servicio `funnel`), con tablas `financial_run`, `unit_economics`, `funnel_metric`, `growth_metric`, `portfolio_snapshot`, `meta_ml_metric`. Todas con `source_version` + `project_id` + `measured_at`. Su creación se formaliza en el ADR de datos pendiente; mientras tanto puede vivir como tablas dentro del schema `core`.

### 2.8 La escalera de madurez cerrada

Las 3 lentes del operador + la capa prescriptiva del funnel + la capa de validación cierran la escalera de madurez analítica de Gartner **completa**:

```
   ¿Qué pasó?          →  DESCRIPTIVA     ←  dashboards, perfiles, topic modeling
   ¿Por qué pasó?      →  DIAGNÓSTICA     ←  causalidad, drift, SHAP
   ¿Qué va a pasar?    →  PREDICTIVA      ←  scoring, forecast, Pirámide ICP
   ¿Qué debo hacer?    →  PRESCRIPTIVA    ←  funnel determinista + uplift + bandits
   ¿Funcionó?          →  VALIDACIÓN      ←  V1-V6: financiera, unit econ, funnel,
                                              growth, portafolio, meta-ML
```

Las 5 son estados del dato en movimiento, no silos. Un signal.created pasa por las 5 en segundos; un project pasa por las 5 en meses.

### 2.9 Patrón mental para operar las 5 capas

Para cada feature que vayas a construir, pide **5 respuestas**:

1. **D**: ¿qué métrica descriptiva produce y con qué freshness?
2. **Dx**: ¿cómo diagnosticamos cuando algo sale mal?
3. **P**: ¿qué predicción habilita y con qué calibración?
4. **Prescriptiva** (funnel): ¿qué gate consulta su output?
5. **V**: ¿a qué métrica de proyecto alimenta y qué kill trigger lo monitorea?

**Si no hay respuesta clara a las 5, la feature no está lista para pasar G4 Design.**

## 3. Rationale

**Por qué 5 capas y no 3, 4 o 7.**

- **5 encaja con la escalera de madurez analítica consolidada** (Gartner): tres estados de datos (descriptivo, diagnóstico, predictivo) + prescriptivo + evaluación. No se inventa nada nuevo; se adopta un marco estándar y se instancia al dominio de IMT.
- **Separar prescriptiva de validación es crítico**: lo prescriptivo *decide* (funnel determinista); lo de validación *mide si la decisión funcionó*. Juntarlos hace imposible medir meta-ML (V6).
- **Las 3 lentes de datos son ortogonales a los servicios**: la misma técnica puede vivir en `ml` o en `pulse`; la lente la clasifica por **propósito de negocio**, no por implementación. Esto previene el silo-por-servicio.

**Por qué subcapas con 4 ítems cada una** (D1-D4, Dx1-Dx4, P1-P4):

- Son el **mínimo cardinal útil** para cubrir cada lente. Menos deja hueco (p. ej. descriptiva sin topic modeling se queda ciega a texto); más fragmenta sin ganancia.
- Encaja con la **capacidad cognitiva humana** (Miller's 7±2): un equipo pequeño puede recordar 4 subcapas por lente sin consultar el manual.

**Por qué 6 subcapas de validación y no 4**:

- V1 (financiera), V2 (unit economics) y V4 (growth) son la **trinidad clásica del venture-backable**. Omitir cualquiera es imposible.
- V3 (funnel) es necesaria porque el funnel determinista ya es parte del diseño; medirlo cierra el loop.
- V5 (portafolio) es la que casi nadie formaliza y es la que previene concentrar en H3.
- V6 (meta-ML) es la que separa este proyecto de una agencia tradicional: medir si la plataforma analítica misma aporta.

**Por qué "métricas de calidad del propio análisis" en cada lente**:

- Un AUC solo nunca es suficiente; sin calibración, un modelo con 0.85 AUC puede mentir sobre probabilidades absolutas.
- Un diagnóstico sin IC es inservible — pueden ser puros artefactos de muestra.
- Freshness en descriptiva es el equivalente para datos.
- Esta disciplina **convierte la plataforma en auditable**: si un cliente pregunta "¿por qué este lead es hot?", hay una cadena de calidades verificables.

## 4. Alternatives Considered

- **Alt A — Organización por servicio (default de ADR-0001)**. Cada técnica vive en `ml` o `pulse` o `core` (funnel), sin más. *Rechazada:* no da lenguaje común para pensar en "qué pregunta de negocio resuelvo"; invita a construir modelos sin pensar en el gate que los consume.
- **Alt B — Solo 3 lentes (D/P/Dx) sin la capa de validación**. *Rechazada:* deja fuera las métricas estándar de evaluación de proyectos (NPV, LTV/CAC, Rule of 40), que son el lenguaje que los socios y clientes esperan.
- **Alt C — Agregar también una capa "exploratoria"** (análisis ad-hoc del data scientist). *Rechazada:* la exploración es un *modo de trabajo*, no una capa de madurez. Vive dentro de descriptiva y diagnóstica dependiendo de qué pregunta responda ese análisis.
- **Alt D — Nombrar las capas en inglés** (descriptive/diagnostic/predictive/prescriptive/validation). *Rechazada:* el equipo trabaja en español y el frontend está en español ([ADR-IMT-0001-ARCH](ADR-IMT-0001-ARCH.md) §12). Los acrónimos **D/Dx/P/Prescriptiva/V** son los contratos técnicos.
- **Alt E — Convertir cada subcapa en un servicio** (p. ej. servicio `attribution`, servicio `drift-detection`). *Rechazada:* explosión combinatoria de servicios sin beneficio. Las subcapas son unidades de pensamiento, no de deployment.
- **Alt F — Semantic Layer formal (dbt MetricFlow como única fuente de métricas)**. *Parcialmente aceptada:* MetricFlow sigue siendo el motor para definir medidas semánticas (ADR-0001 §8); pero las métricas de calidad del análisis (V6, PSI, Qini) viven en el servicio que las produce.

## 5. Consequences

**Positive**

- Lenguaje común: cualquier feature nueva se discute en términos de "¿qué capa toca?", lo que acelera decisiones.
- Las métricas de calidad son parte del contrato de cada endpoint, no un afterthought.
- La capa de validación convierte decisiones subjetivas (¿matamos este proyecto?) en reglas con umbrales.
- La escalera de madurez cerrada permite medir "qué tan maduros estamos" en cada lente por fase del roadmap.
- Alinea la plataforma con el vocabulario que los socios y clientes esperan (NPV, LTV/CAC, Rule of 40).

**Negative / costs**

- Más disciplina al escribir código: cada endpoint debe declarar su lente y producir sus métricas de calidad.
- Más tablas: el schema `validation` nuevo con 6 tablas base.
- Curva de aprendizaje para quien no está familiarizado con la escalera de Gartner o con métricas SaaS modernas.
- Riesgo de over-engineering si se intenta llenar las 5 capas antes de tener datos reales (empezar simple, agregar capas cuando duelan).

**Risks**

- *El equipo mide por métricas vanity (D1) sin diagnóstico (Dx) ni predicción (P).* **Se controla con:** el patrón mental de §2.9 — una feature sin respuesta a las 5 preguntas no pasa G4 Design.
- *La capa V6 (meta-ML) genera métricas pero nadie las mira.* **Se controla con:** incluir V6 en la revisión mensual del operador y en el dashboard `7_Ops.py` de Streamlit.
- *Las métricas de evaluación (V1-V5) se calculan con datos sucios y mienten.* **Se controla con:** `source_version` + Dx3 (drift) + V6 vigilan la calidad del dato que alimenta validación.
- *Confusión entre "la lente predictiva es el futuro" y "la lente prescriptiva es el futuro".* **Se controla con:** el ejemplo de §2.6 y la escalera de §2.8 — predictiva responde "qué pasará", prescriptiva responde "qué debo hacer".

**Spawns**

- Schema `validation` con sus 6 tablas (dependerá de un ADR específico de datos cuando se consolide).
- Página `9_Validation.py` en Streamlit (futura).
- Página `/projects/:id/validation` en Next.js (futura).
- Catálogo de métricas estándar por fase del funnel: candidato `STUDY-IMT-0002-METRICS` (qué métricas mirar en G2, G3, …, G7).
- Posible ADR específico sobre la implementación del motor Monte Carlo (`services/funnel/forecasts/`).

## 6. Invariant Compliance

Este ADR es **transversal** y respeta todos los principios P1-P7 de [ADR-IMT-0001-ARCH](ADR-IMT-0001-ARCH.md) §3:

| Principio | Cómo lo respeta esta decisión |
|---|---|
| **P1 Un servicio, una base** | No crea servicios nuevos; el schema `validation` es propiedad de `core` (owner `core_owner`) |
| **P2 Signal-first, source-second** | Las métricas descriptivas D1 no se calculan sin señales reales; las predictivas P1 no se publican sin calibración |
| **P3 RUES como eje de verdad** | V2 unit economics solo cuenta clientes RUES-matched; V3 funnel solo cuenta leads que pasaron entity resolution |
| **P4 Outbox obligatorio** | El catálogo de eventos de §2.6 extiende el outbox existente; cada evento lleva `source_version` |
| **P5 Degradación por tarjeta** | Las métricas V1-V6 se calculan por tarjeta en Streamlit; si cae `ml`, V6 drift queda `stale` pero V1-V3 siguen |
| **P6 Frescura visible** | Cada métrica de validación lleva su `measured_at` y `source_version`; freshness es métrica de calidad descriptiva (D1) |
| **P7 Outcomes desde el día 1** | V3 funnel + V2 unit economics + V6 meta-ML son impotentes sin outcomes; este ADR eleva P7 de principio a infraestructura medible |

## 7. Status & Supersession

**Status:** `accepted` · **Stage:** `promoted`. Aceptado por el operador el 2026-10-18; se implementa en la ronda 2 (capa L2). La instrumentación de las tres primeras métricas por subcapa es criterio de cierre de esa ronda, no de aceptación.

**Supersedes:** nada. **Completa:** [ADR-IMT-0001-ARCH](ADR-IMT-0001-ARCH.md) añadiendo la organización analítica transversal que ese ADR no detalló.

**Revisar si:**

- La escalera de Gartner evoluciona (p. ej. aparece una 6ª lente ampliamente adoptada: *generative* o *agentic* analytics)
- Las métricas de validación necesitan reconfiguración por decisión del operador (p. ej. cambiar de Rule of 40 a otro benchmark)
- Se agrega un servicio nuevo que mueve subcapas de lugar
- Un ADR futuro (p. ej. `ADR-IMT-NNNN-METRICS`) consolida el catálogo de métricas estándar por fase

## 8. Related

- **Completa:** [ADR-IMT-0001-ARCH](ADR-IMT-0001-ARCH.md) (§10 ML, §E2.2/§E2.3 ubicación del funnel en `core` — este ADR define el funnel y la validación y los estructura en una escalera coherente de 5 capas)
- **Fuentes externas:** Gartner Analytics Ascendancy Model (descriptivo→diagnóstico→predictivo→prescriptivo) · McKinsey Three Horizons · SaaS metrics canon (Rule of 40, Magic Number, Burn Multiple, NRR/GRR) · Markov attribution (Anderl et al.) · Qini coefficient (Radcliffe)
- **Spawns futuros:** `ADR-IMT-NNNN-METRICS` (catálogo cerrado), `STUDY-IMT-0002-METRICS-PER-STAGE`, página Streamlit `9_Validation.py`, página Next.js `/projects/:id/validation`

---

## Changelog

| Fecha | Versión | Cambio |
|---|---|---|
| 2026-10-17 | 1.0 | Creación del ADR. Promovido desde la ronda de exploración 3. Define 5 capas (3 lentes de datos + prescriptiva + validación), 12 subcapas de las lentes de datos (D1-D4, Dx1-Dx4, P1-P4), 6 subcapas de validación (V1-V6), métricas de calidad por lente, mapeo a servicios, flujo end-to-end y patrón mental de 5 preguntas. |
| 2026-10-18 | 1.1 | **Congruencia 0001→0002→0003.** Elimina el servicio fantasma `funnel`: el funnel determinista y la capa de validación son una capa lógica dentro de **`core`** (ahí viven `pipeline_stage`, `project`; el schema `validation` es propiedad de `core_owner`), consistente con la topología de 7 servicios de ADR-0001 v2.3 §E2. Corrige la lista de gates (eran 8 nombres con «Build»; el funnel canónico son **7 gates G1–G7 sin Build**). Unifica el catálogo de eventos con ADR-0003: `funnel.gate_decision` (antes `gate_passed`/`gate_evaluated`), `funnel.kill_trigger_event` (antes `kill_trigger_fired`), y añade `component.evaluated` al catálogo. |
| 2026-10-18 | 1.1 | **Aceptado por el operador** (`status: accepted`, `stage: promoted`). Fuente de verdad para la ronda 2 de implementación (capa L2). |
