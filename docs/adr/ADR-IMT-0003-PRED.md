# ADR-IMT-0003-PRED — Componentes predictivos, validación científica y extensión territorial

| Field | Value |
|---|---|
| ADR | ADR-IMT-0003-PRED |
| Version | 1.2 |
| Layer / run | L3 / Round 3: Predictive layer: ML/NLP/graph components, experimental protocol, territorial extension |
| Vault status | proposed (staged) |
| Authority | Source of truth for implementation (see `AGENTS.md`). On conflict, the higher-numbered amendment wins over the base text |
| Language | Spanish. All other repository documentation is English |
| Origin | Exported from the project vault on 2026-10-18. Do not edit here; see `docs/README.md` |


> **Status:** proposed · **Decided:** — · **Supersedes:** — · **Acronym:** PRED
>
> **Fijamos el catálogo cerrado de componentes de la lente predictiva (P1-P4), con tres reglas duras: (1) diccionario antes que modelo — NLP por escalera determinista; (2) todo componente ML se valida con un estadístico simple del LENS, nunca con otro modelo; (3) ningún componente entra sin responder las 5 preguntas de ADR-0002 §2.9 con validadores pre-registrados y decisión ADOPT/ITERATE/KILL. Además extendemos el LENS con la dimensión territorial: Índice de Oportunidad Zonal por comuna, umbrales de funnel geo-adaptativos justificados estadísticamente, nodos Zona en el grafo y dashboards con granularidad ciudad → comuna → H3.**

---

## 1. Context & Problem

[ADR-IMT-0002-LENS](ADR-IMT-0002-LENS.md) definió la organización analítica (5 capas, subcapas P1-P4, patrón mental de 5 preguntas) pero dejó abierto **qué componentes concretos** pueblan la lente predictiva y **cómo se evalúa científicamente** que cada uno aporta.

Tres fuerzas empujan esta decisión:

1. **Riesgo de sobre-complejización.** La exploración inicial proponía NER semántico, zero-shot por default y embeddings pesados. El operador corrigió: *lo más potente posible, pero lo más simple posible* — regex y NLP clásico primero, modelos solo como fallback. Esa corrección debe quedar fijada como regla, no como preferencia pasajera.
2. **Validación barata y confiable.** El LENS ofrece métodos deterministas y estadísticos — un solo cálculo, sin pipeline, sin recalibración de modelos. Un componente ML complejo debe validarse con esos métodos simples, no con otro modelo encima. El principio: **lo complejo se valida con lo simple.**
3. **Geografía como dimensión de primera clase.** La agencia vende software (geografía débil) y catering/eventos (geografía es destino). El funnel debe adaptarse por ciudad, y el análisis descriptivo debe responder "¿en qué comunas de Medellín hay dinero Y la competencia no asfixia a un caterer emergente?". Hoy el LENS trata el territorio como un indicador más; debe ser una dimensión transversal.

**Las preguntas que resuelve este ADR:** qué componentes predictivos entran (y cuáles no), cómo responde cada uno las 5 preguntas del LENS con validadores de un solo estadístico, cuál es el protocolo científico que evalúa esas respuestas, y cómo se territorializa el LENS.

## 2. Decision

### 2.1 Componentes finales por subcapa P

| Subcapa | Componente | Fase | Reemplaza / sucede a |
|---|---|---|---|
| **P1 Scoring** | Regla ponderada (fit ICP × intensidad señal × territorial), SQL puro | F1 | — baseline permanente |
| | **LightGBM calibrado** (isotonic o Platt) | F6 | la regla, cuando ≥ 200 outcomes — pero la regla **nunca se apaga**: queda como baseline shadow |
| | **Uplift modeling** (`causalml` X-learner) | F6 | complementa el supervisado |
| **P2 Forecasting** | **StatsForecast** (AutoARIMA + Theta + ETS, ensemble) | F3 | — |
| | **Hierarchical reconciliation** por ciudad × línea | F4 | — |
| **P3 Match + Lookalike** | **Pirámide ICP** (§2.4) | F5 | k-NN flat sobre pgvector |
| | **Learning-to-rank** (`LGBMRanker`, NDCG) | F6 | ordenar el inbox del BD rep |
| **P4 Simulación** | **Monte Carlo** sobre TAM/SAM/SOM, adopción, pricing | F4 | — (worker del funnel, en `core`) |

### 2.2 NLP — escalera determinista (regla dura)

**Regla rectora: diccionario antes que modelo.** Solo subir de nivel si el anterior demostrablemente no alcanza (medido, no supuesto).

| Tarea | Nivel 1 (regex/diccionario) | Nivel 2 (clásico) | Fallback modelo |
|---|---|---|---|
| Tech stack | regex + dict (~500 tecnologías) | — | — |
| Título de cargo | `rapidfuzz` + dict → CIIU | — | solo variantes raras (<5%) |
| Sentimiento ES | lexicón + negadores | VADER-ES | — |
| Clasificar vacante en línea | reglas sobre keywords | — | zero-shot si `undefined` (<10%) |
| Topic modeling | — | **LDA** (`gensim`) | BERTopic si LDA incoherente (c_v < 0.45) |
| Keyword extraction | — | **YAKE** (puro Python) | KeyBERT |
| Lookalike semántico | — | TF-IDF + cosine | embeddings e5 solo si TF-IDF da ruido |

Los diccionarios viven en `libs/domain/dictionaries/*.yaml`, versionados con git, editables por PR — son el **documento vivo de la experiencia de la agencia**, igual que los checklists del funnel.

### 2.3 Grafos por niveles

Tres niveles principales + tres auxiliares + **el nivel territorial Z** (§2.8). Todo en Postgres + CTEs recursivas hasta F5; Neo4j solo si > 500k nodos en F6+.

| Nivel | Nodos | Aristas | Tamaño esperado |
|---|---|---|---|
| **N3 Sector-Ciudad** | CIIU × ciudad | similitud semántica + flujo económico | ~100 |
| **N2 Compañía** | NIT | competencia, cadena suministro, directivos compartidos | 5k-50k |
| **N1 Lead** | empresa × ventana × proyecto | señal compartida, decisor común, campaña común | 500-5k activos |
| **Z Zona** (§2.8) | comuna / corregimiento | adyacencia espacial, similitud socioeconómica | ~20-50 por ciudad |

**Auxiliares:** Decisor-Empresa (movimientos de CTO/CEO → warm leads), Establecimientos (franquicias, dueños compartidos HORECA), Proyecto-Servicio-Cliente (histórico → market basket).

**Análisis en batch nocturno:** degree centrality, PageRank, Leiden communities, Jaccard link prediction. **No GNN** antes de F6 con > 100k empresas.

### 2.4 Pirámide ICP

Índice jerárquico de búsqueda de leads, inspirado en HNSW pero con **semántica interpretable en cada nivel** (los niveles son fijos y con significado humano, no probabilísticos):

```
query = ProfileICPQuery (vector + hard_filters + soft_weights)
  ↓
Nivel 3 (sectores):   top-k=5 por distancia + filtros
  ↓
Nivel 2 (arquetipos): top-k=5 dentro de esos sectores (K-Prototypes por sector)
  ↓
Nivel 0 (empresas):   top-k=20 dentro de esos arquetipos + filtro final
  ↓
rerank: distancia × score ML × uplift esperado
```

- Niveles pre-calculados: N3 mensual, N2 semanal (worker de `ml`); N0 = vector 128-dim en `pgvector`
- **Output interpretable por match:** `path_narrative` → *"HORECA Medellín · PyME 20-50 sin app · Restaurante X · distancia 0.18 al perfil ideal"*
- ~50 ms contra Postgres con beam width 5
- Múltiples perfiles en paralelo (óptimo + ideal + sub-óptimo) con interleaving
- Perfiles por proyecto: 3-5, declarados top-down en G4 Design (pydantic) y refinados bottom-up con ≥ 10 outcomes

### 2.5 Mapeo componente × gate × métrica V

Cada componente **debe** llenar la fila entera o no entra al plan:

| Componente | Gate que lo consume | Métrica V afectada | Métrica de calidad propia |
|---|---|---|---|
| Regla v0 | G1 Discovery, G2 Qualify | V3 win rate baseline | — |
| LightGBM calibrado | G5 Launch | V6 precision@K real | AUC, PR-AUC, Brier |
| Uplift modeling | G5 Launch (decisión contactar) | V2 CAC payback, V6 Qini | Qini coefficient |
| StatsForecast | G3 Validate | V1 NPV p10, V4 velocity | MASE, pinball loss, coverage |
| Pirámide ICP | G2 Qualify (match perfil) | V3 conversion por perfil | precision@K por perfil |
| Learning-to-rank | operación diaria | V3 cycle time | NDCG@K |
| Monte Carlo | G3 Validate, G4 Design | V1 NPV distribution, V5 Sharpe | coverage del IC |
| LDA / BERTopic | G1 Discovery | V3 nichos nuevos detectados | coherencia c_v |
| Grafos (centralidad/comunidades) | G2 (contexto), G6 (casos faro) | V3 win rate por centralidad | ARI/NMI de estabilidad |
| Zero-shot fallback | G2 Qualify | V6 cobertura del clasificador | Cohen's kappa vs reglas |

> **Nota de alcance:** `LDA/BERTopic` (§2.2) y `Grafos` (§2.3) no son componentes de la subcapa P — pertenecen a las capas Descriptiva/Diagnóstica del LENS (ADR-IMT-0002, D4/Dx). Aparecen en esta tabla porque también alimentan gates del funnel y deben llenar la fila completa igual que los predictivos.

### 2.6 Las 5 preguntas respondidas por componente — validadores deterministas del LENS

El principio operativo: **el LENS (ADR-0002) es la capa estándar determinista y estadística de validación**. Sus métodos son un solo cálculo estadístico — sin pipeline, sin recalibración, sin feature engineering. Cada componente ML/grafo complejo se valida con uno de esos cálculos simples. Catálogo por componente:

#### Regla ponderada v0

| Pregunta | Respuesta con validador determinista |
|---|---|
| **D** | Distribución de scores (p10/p50/p90, histograma por banda); freshness = por evento |
| **Dx** | **PSI mensual** del score vs mes base (umbral 0.1/0.25); Spearman score↔outcome cuando haya outcomes |
| **P** | Probabilidad implícita por banda, calibrada con **tabla banda × win-rate observado** (un `GROUP BY`, no un modelo) |
| **Prescriptiva** | G1 Discovery, G2 Qualify |
| **V + kill** | V3 win rate por banda; **kill:** si banda "hot" no supera 1.5× la base rate en 2 trimestres (test de proporciones, α=0.05) → recalibrar pesos o retirar |

#### LightGBM calibrado

| Pregunta | Respuesta |
|---|---|
| **D** | Distribución del score + feature importances (snapshot versionado) |
| **Dx** | **PSI por feature** (0.1 vigilar / 0.25 reentrenar); **Brier rolling mensual**; comparación campeón-retador contra la regla v0, que corre siempre en shadow |
| **P** | AUC/PR-AUC + **reliability table de 10 deciles** (observado vs predicho — un `GROUP BY`) |
| **Prescriptiva** | G5 Launch |
| **V + kill** | V6 precision@K real + model utility (DID); **kill:** si no supera a la regla en lift@K durante 2 trimestres → rollback a la regla (que nunca se apagó) |

#### Uplift modeling

| Pregunta | Respuesta |
|---|---|
| **D** | Distribución del uplift predicho; % de "sleeping dogs" (uplift negativo) |
| **Dx** | **Holdout aleatorio permanente del 5-10%** — el holdout ES el validador determinista: diferencia de medias con test t / bootstrap BCa, un cálculo |
| **P** | Ganancia incremental esperada por decil (curva Qini) |
| **Prescriptiva** | G5 Launch — decide a quién contactar |
| **V + kill** | V2 CAC payback; **kill:** si uplift@top-30% ≤ 0 con IC 95% en el holdout → apagar y contactar por score plano |

#### StatsForecast

| Pregunta | Respuesta |
|---|---|
| **D** | Forecast publicado con P10/P50/P90 + `as_of` |
| **Dx** | **Coverage empírico del intervalo** (¿~80% de los reales caen en P10–P90? — un conteo); **MASE rolling** vs naïve estacional |
| **P** | Volumen de leads/cierres a 90 días con intervalo |
| **Prescriptiva** | G3 Validate |
| **V + kill** | V4 pipeline velocity; **kill:** MASE > 1 (peor que naïve) por 3 meses → degradar a media móvil y repensar |

#### Pirámide ICP

| Pregunta | Respuesta |
|---|---|
| **D** | Distribución de distancias por perfil; nº de matches por perfil × ciudad |
| **Dx** | **ARI (Adjusted Rand Index)** entre reconstrucciones semanales de arquetipos — un número; % de empresas que cambian de arquetipo (churn de arquetipo > 20%/semana = inestable) |
| **P** | Ranking de candidatos por perfil con distancia |
| **Prescriptiva** | G2 Qualify (match de perfil) |
| **V + kill** | V3 conversion por perfil; **kill:** si conversion(óptimo) ≤ conversion(control aleatorio del mismo sector) con **test de proporciones de dos muestras** → la pirámide no discrimina; volver a k-NN flat |

#### LDA / BERTopic

| Pregunta | Respuesta |
|---|---|
| **D** | Nº de temas activos, tamaño, novedad (temas nuevos/mes) |
| **Dx** | **Coherencia c_v por tema** (umbral 0.45); % de docs en tema "ruido" |
| **P** | Que un tema emergente crecerá — validado con la **pendiente de su serie temporal** (regresión lineal simple: un coeficiente con p-value) |
| **Prescriptiva** | G1 Discovery |
| **V + kill** | V3 nichos descubiertos que llegaron a G3; **kill:** si en 2 trimestres ningún tema descubierto pasó G2 → bajar cadencia o apagar |

#### Grafos (centralidad, comunidades, link prediction)

| Pregunta | Respuesta |
|---|---|
| **D** | Nº nodos/aristas por nivel, densidad, modularidad de Leiden |
| **Dx** | **NMI/ARI de estabilidad de comunidades** entre corridas; % de aristas con `source` confiable |
| **P** | Hubs candidatos a caso-faro; links predichos (Jaccard) |
| **Prescriptiva** | G2 (contexto del scorecard), G6 Scale (casos faro) |
| **V + kill** | V3 win rate de leads con alta vs baja centralidad (**test de proporciones**); **kill:** si centralidad no correlaciona con conversión (Spearman + IC bootstrap) en 2 trimestres → el grafo se degrada a "contexto informativo" y sale del score |

#### Monte Carlo

| Pregunta | Respuesta |
|---|---|
| **D** | Distribución NPV publicada (p10/p50/p90) por proyecto |
| **Dx** | **Backtest de supuestos:** al cerrar el primer año de un proyecto, error porcentual simulado vs real por supuesto (adopción, pricing, CAC) — un número por supuesto |
| **P** | prob(NPV>0), payback esperado |
| **Prescriptiva** | G3 Validate, G4 Design |
| **V + kill** | V1 NPV, V5 Sharpe-like; **kill del método:** error sistemático > 50% en un supuesto → revisar priors con los socios antes de volver a usarlo en un gate |

#### Zero-shot fallback

| Pregunta | Respuesta |
|---|---|
| **D** | % de casos que caen al fallback (si crece, los diccionarios se quedaron cortos — señal para ampliar el dict, no el modelo) |
| **Dx** | **Cohen's kappa** contra una muestra mensual etiquetada a mano (~50 casos) — un número |
| **P** | Clasificación de línea de servicio donde las reglas dan `undefined` |
| **Prescriptiva** | G2 Qualify |
| **V + kill** | V6 cobertura del clasificador; **kill:** kappa < 0.6 → el fallback confunde más de lo que aporta; esos casos van a revisión manual |

**El hilo común:** todos los validadores son **un estadístico con distribución conocida** — PSI, ARI, NMI, kappa, MASE, Brier, coverage count, test de proporciones, Spearman, pendiente con p-value. Ninguno requiere pipeline, feature store ni recalibración. Lo complejo se valida con lo simple.

### 2.7 Protocolo científico de evaluación — la ficha experimental

Las 5 preguntas no se responden con opinión; se responden con un **experimento pre-registrado**. Todo componente nuevo lleva una **ficha experimental** (archivo `docs/experiments/EXP-NNN-<component>.md`) con 7 elementos, escritos **antes de construir**:

| # | Elemento | Regla |
|---|---|---|
| 1 | **Hipótesis falsable** | "El componente X mejora la métrica Y en ≥ Z% frente al baseline B." Pre-registrada en el repositorio — escribir la hipótesis después de ver los datos (HARKing) invalida la evaluación |
| 2 | **Baseline obligatorio** | Todo componente se mide contra un baseline trivial (regla, naïve estacional, azar estratificado, k-NN flat). Nunca contra cero |
| 3 | **Diseño del contraste** | Con intervención (contactar/no): **holdout aleatorio permanente 5-10%** → test t / proporciones / bootstrap BCa. Sin intervención (forecast, clasificación): **backtest temporal con ventanas rodantes** — nunca validación aleatoria en series temporales |
| 4 | **Potencia estadística** | Antes de concluir: con base rate p y efecto mínimo detectable δ, calcular n requerido (`statsmodels.stats.power`, una llamada). Si no hay n suficiente, la conclusión **se pospone, no se inventa** — el componente queda en `ITERATE` |
| 5 | **Umbrales pre-registrados** | MDE (efecto mínimo relevante para el negocio, no solo significancia) + α = 0.05 + **Benjamini-Hochberg** cuando se evalúan varios componentes a la vez |
| 6 | **Decisión trinaria** | **ADOPT** (supera MDE con potencia suficiente) / **ITERATE** (inconcluso o mejora sin alcanzar MDE) / **KILL** (no supera baseline o daña una métrica V). Cada evaluación emite `component.evaluated` con veredicto + `source_version` — auditable igual que una decisión de gate |
| 7 | **Re-evaluación trimestral** | Toda adopción es provisional. V6 (meta-ML) agenda la re-evaluación con los mismos umbrales; un componente que deja de superar su baseline vuelve a `ITERATE` |

**Mapeo de las 5 preguntas a chequeos medibles:**

| Pregunta | Chequeo medible | Cadencia |
|---|---|---|
| **D** | SLO de freshness (`as_of` ≤ umbral) + % completitud — chequeo nocturno determinista | Diaria |
| **Dx** | Estadístico de drift (PSI/ARI/kappa según componente) contra umbral pre-registrado | Diaria/semanal |
| **P** | **Skill score vs baseline**: MASE (forecast), lift@K (scoring), mejora de Brier (calibración) | Semanal/mensual |
| **Prescriptiva** | **Gate consumption rate**: % de decisiones del gate donde el output del componente fue input efectivo — auditable desde `funnel.gate_decision` (si un gate nunca consume el componente, el componente sobra) | Mensual |
| **V** | Atribución a la métrica V vía holdout/DID + kill trigger registrado en `funnel.kill_trigger_event` | Trimestral |

### 2.8 Extensión territorial del LENS

La geografía pasa de "un indicador más" a **dimensión transversal** de las 5 capas. Extiende [ADR-IMT-0002-LENS](ADR-IMT-0002-LENS.md) sin modificarlo.

#### 2.8.1 Granularidad territorial canónica

Jerarquía: **Ciudad → Comuna/Localidad → Barrio → celda H3 (res 8-9)**. Tabla `socio.territory` con la jerarquía; toda empresa (N2) geocodificada a su H3 y comuna.

Fuentes públicas Colombia (deterministas, gratuitas): **DANE** (marco geoestadístico MGN, **estratificación socioeconómica por manzana** — el proxy de poder adquisitivo más fino que existe en Colombia), **MEData** (datos abiertos Medellín: 16 comunas + 5 corregimientos), datos.gov.co, catastro municipal (valor m²), OSM/Overture (POIs, densidad comercial).

#### 2.8.2 Índice de Oportunidad Zonal (IOZ)

Por zona × línea de servicio — responde "¿dónde hay dinero Y la competencia no asfixia?":

```
IOZ(z, línea) = Poder(z) × Demanda(z, línea) × (1 − Saturación(z, línea))
```

| Factor | Proxies deterministas |
|---|---|
| **Poder(z)** | Estrato promedio ponderado por manzana (DANE), valor catastral m², densidad de empresas de alto ticket |
| **Demanda(z, línea)** | Densidad de señales de la línea en la zona — vacantes tech (software); hoteles con salón, eventos corporativos, aperturas (catering) |
| **Saturación(z, línea)** | Densidad de competidores por 1 000 empresas objetivo + HHI local de competidores — para catering: caterers/venues existentes vs demanda estimada |

Normalización **z-score dentro de cada ciudad** (nunca comparar crudos entre ciudades). Vive en `socio.territory_index` con `method_version`.

**Validación del IOZ (consistente con §2.7):** **Moran's I** para autocorrelación espacial (¿el índice forma clusters reales o es ruido? — un estadístico con p-value) y Spearman IOZ↔win-rate por zona cuando haya outcomes. Kill: si Moran's I no es significativo y el Spearman es ~0 tras 2 trimestres, el IOZ se degrada a "contexto" y sale de los scorecards.

#### 2.8.3 Funnel geo-adaptativo

Los umbrales de los gates se parametrizan por ciudad (y opcionalmente comuna) en `tenant_config.funnel_rules.geo_overrides`:

```json
{"geo_overrides": {
  "medellin":     {"min_score_for_hot": 70},
  "barranquilla": {"min_score_for_hot": 60, "market_size_floor_usd": 300000}
}}
```

**Regla anti-proliferación:** un geo-override solo se crea con justificación estadística — base rate de conversión distinta con test de proporciones significativo entre las dos geografías. Sin evidencia, se hereda el default. Cada override queda auditado con su evidencia en `funnel.gate_decision`.

#### 2.8.4 Grafo enriquecido — nodos Zona (Z)

Nuevo tipo de nodo **Z** = comuna/corregimiento (~20-50 por ciudad; costo marginal nulo).

| Arista | Definición |
|---|---|
| empresa —`ubicada_en`→ zona | Geocodificación H3 → comuna |
| zona —`adyacente_a`→ zona | Contigüidad espacial (queen contiguity, `libpysal`) |
| zona —`similar_a`→ zona | Distancia euclidiana sobre vector [estrato, densidad comercial, mix CIIU, valor m²] |

**Lo que habilita — transferencia zonal:** si el perfil óptimo de catering funciona en Laureles, las zonas `similar_a` Laureles en Bogotá son candidatas de expansión. Es un **lookalike de zonas**, no solo de empresas. También refina la Pirámide ICP: para HORECA/catering (donde la geografía es destino), el nivel N3 se refina a sector-comuna; para SaaS B2B la comuna pesa menos y se queda en sector-ciudad.

#### 2.8.5 Dashboards descriptivos granulares (Streamlit)

- **Selector de granularidad** en `1_Market.py` y `4_Territorios.py`: Ciudad → Comuna → H3
- **Coropleta del IOZ por comuna** con doble codificación: color = IOZ, opacidad/borde = confianza (una comuna con 3 empresas no pinta igual que una con 300 — reutiliza el flag `low_sample` del contrato Pulse)
- **Mapa bivariado Poder × Saturación** por cuadrantes: **oro** (alto poder + baja saturación — entrar ya), **guerra** (alto poder + alta saturación — solo con diferenciador), **siembra** (bajo poder + baja saturación — monitorear), **evitar** (bajo poder + alta saturación)
- Todos los endpoints geo llevan `as_of`, `method_version` y `low_sample` como el resto del contrato Pulse (P6: frescura visible)

### 2.9 Anti-patrones ratificados (qué NO entra)

- **Deep learning tabular** — LightGBM gana con cientos a miles de empresas
- **GNN antes de F6** y > 100k empresas
- **NER semántico pesado** — `PhraseMatcher` + diccionarios cubre 95%
- **Zero-shot por default** — solo fallback medido con kappa
- **Embeddings pesados** donde TF-IDF basta
- **Neo4j prematuro** — Postgres + CTEs hasta 500k nodos
- **AutoML / HPO extensivo** — rompe interpretabilidad frente al cliente
- **RL puro para el agente MCP** — bandits bastan
- **Validar un modelo con otro modelo** — todo validador es un estadístico simple del LENS
- **Geo-overrides sin evidencia** — proliferación de umbrales arbitrarios por ciudad

## 3. Rationale

**Lo complejo se valida con lo simple.** Un modelo validado por otro modelo crea una torre de dependencias imposible de auditar. Un modelo validado por un PSI, un kappa o un test de proporciones puede defenderse frente a un socio o un cliente con una sola fórmula. Esta asimetría — componentes sofisticados, validadores elementales — es la decisión central del ADR y la razón por la que el LENS (ADR-0002) precede lógicamente a este catálogo.

**Diccionario antes que modelo** porque el 70-95% del valor de NLP en este dominio sale de vocabulario controlado que el propio equipo cura. Un diccionario es auditable, corregible por PR y gratuito en inferencia; un modelo es ninguna de las tres. El % de casos que caen al fallback es además un sensor natural de cuándo el diccionario se quedó corto.

**Ficha experimental pre-registrada** porque sin hipótesis previa, baseline y potencia, toda evaluación degenera en confirmación retrospectiva. La decisión trinaria ADOPT/ITERATE/KILL replica deliberadamente la semántica de los gates del funnel: los componentes analíticos se gobiernan igual que los proyectos.

**Territorio como dimensión transversal** porque las dos líneas de negocio tienen acoplamiento geográfico opuesto (software débil, catering fuerte), y un solo juego de umbrales nacionales castigaría a ambas. El estrato socioeconómico colombiano por manzana es un dato público excepcional que casi ningún competidor explota sistemáticamente — ventaja estructural barata.

## 4. Alternatives Considered

- **Alt A — Validar modelos con métricas de ML puras (AUC y ya).** *Rechazada:* un AUC no dice si el modelo gana dinero ni cuándo matarlo; sin baseline de negocio ni kill trigger, los modelos zombis se acumulan.
- **Alt B — MLOps completo (MLflow tracking + evaluación continua automatizada + feature store).** *Parcialmente aceptada:* MLflow queda para versionado (ADR-0001), pero la **evaluación** es deliberadamente manual-estadística con ficha pre-registrada — un equipo de este tamaño audita mejor 10 fichas que un sistema de evaluación continua que nadie lee.
- **Alt C — NER semántico y zero-shot como primera línea.** *Rechazada por el operador:* costo y opacidad sin ganancia medida; queda la escalera determinista con fallback acotado.
- **Alt D — Barrio o H3 como unidad territorial del IOZ.** *Rechazada para F4:* demasiada varianza con pocas empresas por celda (problema de small-area estimation); la comuna es el equilibrio señal/ruido. H3 queda para visualización y geocodificación, no para índices.
- **Alt E — Umbrales de funnel únicos nacionales.** *Rechazada:* las base rates difieren por ciudad; pero el geo-override libre también se rechaza — solo con test de proporciones significativo.
- **Alt F — Neo4j para los nodos Z.** *Rechazada:* 20-50 nodos por ciudad caben en cualquier cosa; Postgres + `libpysal` para contigüidad.

## 5. Consequences

**Positive**

- Catálogo cerrado: ninguna discusión futura de "¿y si metemos X?" sin pasar por la ficha experimental.
- Cada componente tiene kill trigger propio — los modelos zombis mueren solos.
- La validación es enseñable: cualquier analista nuevo entiende un PSI o un test de proporciones en una tarde.
- El IOZ convierte "¿dónde vendemos catering?" de intuición a mapa con p-value.
- La Pirámide ICP y el grafo Z dan lookalike de empresas **y** de zonas — expansión a Bogotá/Cali con evidencia.

**Negative / costs**

- Disciplina de pre-registro: escribir la ficha antes de construir es fricción real (deliberada).
- Los diccionarios requieren curaduría continua del equipo — si nadie los mantiene, el fallback crece en silencio (el sensor D del zero-shot lo delata).
- Datos territoriales colombianos requieren ETL inicial no trivial (MGN, estrato por manzana, catastro).
- El holdout permanente del uplift sacrifica 5-10% de leads contactables — es el precio de saber la verdad.

**Risks**

- *El equipo salta la ficha experimental "por esta vez".* **Se controla con:** el gate G4 Design exige la ficha como checklist item duro (hard block).
- *Small samples en comunas con pocas empresas producen IOZ ruidoso.* **Se controla con:** flag `low_sample`, Moran's I como validador, y la comuna (no el barrio) como unidad de índice.
- *Los geo-overrides proliferan por presión comercial.* **Se controla con:** la regla anti-proliferación (test de proporciones obligatorio) auditada en `funnel.gate_decision`.
- *Benjamini-Hochberg mal aplicado o ignorado al evaluar muchos componentes.* **Se controla con:** la ficha experimental lo lista como campo obligatorio; V6 lo revisa trimestralmente.

**Spawns**

- `STUDY-IMT-0004-PYRAMID` — esquema de tablas y pseudocódigo de la Pirámide ICP
- `STUDY-IMT-0003-IOZ` — ETL de fuentes territoriales (MGN, estrato, catastro, MEData) y primera corrida del IOZ para Medellín
- Template de **ficha experimental** en `docs/experiments/_TEMPLATE.md` (campos: hipótesis, baseline, MDE, potencia)
- Página `4_Territorios.py` enriquecida (coropleta IOZ + mapa bivariado)

## 6. Invariant Compliance

| Principio (ADR-0001 §3) | Cómo lo respeta |
|---|---|
| **P1 Un servicio, una base** | Componentes viven donde ADR-0002 §2.7 los asignó; IOZ en `socio`, propiedad del worker correspondiente |
| **P2 Signal-first** | Ningún componente se construye sin señal que lo alimente; el zero-shot solo existe donde las reglas dan `undefined` |
| **P3 RUES como eje** | La Pirámide ICP solo indexa empresas RUES-matched; el IOZ solo cuenta competidores verificados |
| **P4 Outbox obligatorio** | `component.evaluated` entra al catálogo de eventos con `source_version` |
| **P5 Degradación por tarjeta** | Un componente en `ITERATE`/`KILL` degrada su tarjeta, no el tablero; el grafo degradado pasa a "contexto informativo" |
| **P6 Frescura visible** | Todos los endpoints geo y de componentes llevan `as_of`, `method_version`, `low_sample` |
| **P7 Outcomes desde el día 1** | Toda la cadena de validación (tests de proporciones, Spearman, holdout) es imposible sin outcomes — este ADR eleva el costo de no registrarlos |

## 7. Status & Supersession

**Status:** `proposed` · **Stage:** `staged`. Pasa a `accepted` cuando el operador apruebe y la primera ficha experimental (previsiblemente la de la regla v0) esté escrita en `docs/experiments/`.

**Supersedes:** nada. **Completa:** [ADR-IMT-0002-LENS](ADR-IMT-0002-LENS.md) (puebla P1-P4 y extiende el LENS con la dimensión territorial) y [ADR-IMT-0001-ARCH](ADR-IMT-0001-ARCH.md) (el grafo Z y las tablas `socio.territory` y `socio.territory_index` extienden §E1.2 y §E2.1).

**Revisar si:**

- El % de fallback del zero-shot supera sostenidamente el 10% (los diccionarios no dan abasto → repensar la escalera)
- El volumen de empresas supera 100k (GNN y Neo4j entran a evaluación con su propia ficha)
- Una tercera línea de negocio con acoplamiento geográfico distinto entra al portafolio
- DANE cambia la metodología de estratificación

## 8. Related

- **Completa:** [ADR-IMT-0002-LENS](ADR-IMT-0002-LENS.md) (5 preguntas §2.9, subcapas P) · [ADR-IMT-0001-ARCH](ADR-IMT-0001-ARCH.md) (mapa de schemas §E2.1 y puntos de extensión §E2.3)
- **Fuentes externas:** HNSW (Malkov & Yashunin) como inspiración estructural de la Pirámide ICP · PSI/population stability · Qini (Radcliffe) · MASE (Hyndman & Koehler) · Moran's I / PySAL · DANE MGN + estratificación socioeconómica · MEData Medellín
- **Spawns:** `STUDY-IMT-0004-PYRAMID` · `STUDY-IMT-0003-IOZ` · ficha experimental en `docs/experiments/`

---

## Changelog

| Fecha | Versión | Cambio |
|---|---|---|
| 2026-10-17 | 1.0 | Creación. Consolida las rondas 4-5: catálogo cerrado de componentes P1-P4, NLP por escalera determinista, grafos N1/N2/N3 + nodos Z, Pirámide ICP, las 5 preguntas respondidas por componente con validadores deterministas de un solo estadístico, protocolo científico con ficha experimental pre-registrada y decisión ADOPT/ITERATE/KILL, y extensión territorial del LENS (IOZ por comuna, funnel geo-adaptativo con regla anti-proliferación, grafo de zonas, dashboards granulares ciudad→comuna→H3). |
| 2026-10-18 | 1.2 | **Congruencia con ADR-0002 v1.1:** P4 Simulación corrige «vive en `funnel`» → worker del funnel **en `core`** (no existe servicio `funnel`; el funnel y la validación son capa lógica de `core`, ADR-0001 §E2). Catálogo de eventos alineado: `funnel.gate_decision` y `funnel.kill_trigger_event` son los nombres canónicos definidos en ADR-0002 §2.6. |
| 2026-10-18 | 1.1 | Revisión de consistencia: `STUDY-IMT-0001-PYRAMID` renumerado a `STUDY-IMT-0004-PYRAMID` (el 0001 está reservado para `STUDY-IMT-0001-BRIGHT-DATA-ICP`); nota de alcance en §2.5 (LDA/BERTopic y Grafos pertenecen a las capas D/Dx del LENS, no a la subcapa P). Se añaden 4 figuras SVG embebidas: flujo de la capa P dentro del LENS (Fig. 1), Pirámide ICP (Fig. 2), arnés de validación baseline-en-sombra (Fig. 3) y ficha experimental como flujo de decisión (Fig. 4). |
