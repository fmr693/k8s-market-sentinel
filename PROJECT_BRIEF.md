# Proyecto: k8s-market-sentinel (nombre provisional)

> Prompt inicial / brief de proyecto. Contexto completo para arrancar desde cero.
> Este documento puede usarse como primer prompt o guardarse como `CLAUDE.md` en la raíz del repo.

## Quién soy y para qué es esto

Soy Michael (github.com/fmr693), data engineer en formación (Spark, Kafka, Airflow, dbt, Docker, PyTorch). Mi portfolio ya tiene: pipeline streaming con Kafka + medallón (AEMET), ELT batch con Airflow/dbt (crypto), streaming IoT con Spark, y proyectos de LLMs. **El hueco que este proyecto rellena: Kubernetes, observabilidad (Prometheus/Grafana) y CD.** No debe parecer "otro pipeline más": la identidad es *plataforma K8s-nativa con lógica de decisión encima*.

Doble propósito: (1) proyecto demostrativo de portfolio, (2) **herramienta personal real** de vigilancia de inversión que voy a usar de verdad.

Es un **ejercicio de aprendizaje**: explícame cada decisión paso a paso, no me des solo el código. Prefiero commits pequeños y entender cada pieza antes de avanzar.

## Contexto de dominio (imprescindible para entender las métricas)

Vigilo **CEFs (closed-end funds) de crédito** de EE. UU. Conceptos clave:

- Un CEF cotiza en bolsa con un **precio de mercado** distinto de su **NAV** (valor de los activos). La diferencia es el **descuento/prima**: `(precio − NAV) / NAV`. Compro cuando hay descuento anormalmente amplio.
- El **z-score del descuento** (descuento actual vs su media/desviación histórica a 1 año) es la señal de entrada. Lo calculamos nosotros sobre nuestra propia serie acumulada.
- El **NAV se publica una vez al día** tras el cierre. El precio se mueve intradía. El "descuento intradía" usa precio fresco contra NAV de ayer → hay que etiquetarlo como *estimado* en el dashboard.
- Señales macro que vigilo ("semáforos"): diferenciales de crédito high yield (si se ensanchan = oportunidad), EUR/USD (soy inversor en euros; niveles: >1,20 convertir y comprar, <1,10 repatriar), indicador Buffett (market cap/PIB, calculado por nosotros), tipos de la Fed.
- Niveles de alerta personales: descuento ≤ −5% (primer tramo de compra), ≤ −7% (segundo tramo), EUR/USD fuera de banda 1,10–1,20.

## Universo de datos (config-driven, ~25-30 series)

Los tickers viven en configuración (ConfigMap / values.yaml), **nunca hardcodeados**:

- **CEFs de crédito**: WDI, KIO, BIT, HYT, BTZ, BHK, DSL, FSCO, BGH, ARDC, GHY, OPP, PDI, PDO, PTY
- **CEFs de contraste** (alto ROC, para comparar): BCAT, ECAT, ADX, BST
- **Benchmarks**: ^GSPC (S&P 500), ^VIX, GLD (oro), EURUSD=X
- **FRED** (API gratuita con key): BAMLH0A0HYM2 (diferencial HY), DGS10 (10Y), WILL5000PR + GDP (para indicador Buffett)

## Fuentes y sus cadencias reales

| Fuente | Datos | Cadencia real | Método |
|---|---|---|---|
| yfinance | Precios CEFs, índices, FX | Intradía (~15 min delay) | Polling 1-2 min en horario de mercado |
| Finnhub (opcional, fase 2) | Trades en tiempo real | Streaming | Websocket (free tier) |
| FRED API | Series macro | Diaria/semanal | 1×/día |
| BCE / frankfurter.app | EUR/USD oficial | Diaria (~16:00 CET) | 1×/día |
| CEFConnect / webs gestoras | **NAV** | Diaria (tras cierre) | Scraping — pieza frágil, aislar en su propio job y documentar limitación |

Horario de mercado USA: 15:30–22:00 CET, L-V. El poller intradía **solo corre en ese horario** (fuera, duerme).

## Decisiones de arquitectura YA TOMADAS (no reabrir salvo buen motivo)

1. **Kubernetes con dos tipos de carga**: `Deployment` para el poller intradía (proceso vivo con lógica de horario de mercado) + `CronJobs` para el carril lento (NAV, FX, macro, 1×/día tras cierre ~23:00 CET). **Sin Kafka** — el dato es batch/polling; usarlo sería impostado (y ya lo tengo demostrado en otros repos).
2. **Medallón sobre Postgres**: bronze (crudo JSON tal cual llega) → silver (limpio, tipado, deduplicado) → gold (métricas derivadas: descuento, z-score, Buffett, señales). Valorar TimescaleDB vs Postgres plano (duda abierta).
3. **Ingesta idempotente con backfill automático**: cada job pregunta primero "¿cuál es mi último dato?" y pide desde ahí hasta hoy. Upsert por clave `(ticker, timestamp)`. El sistema se autorrepara tras apagones (mis máquinas NO están 24/7). Excepción: el NAV scrapeado no siempre tiene histórico → los huecos de NAV se documentan, los de precio se autorreparan.
4. **Estado externalizado**: Postgres gestionado gratuito (Neon o Supabase) como fuente de verdad, para que cualquier clúster desde cualquier sitio escriba contra la misma base. Alternativa B: Postgres en el k3s del servidor Ubuntu con PVC como "hogar" de los datos. (Decidir al arrancar — duda abierta.)
5. **Portabilidad = el chart, no el clúster**: todo desplegable con `helm install` en cualquier máquina. Servidor Ubuntu de oficina: **k3s** (arranca solo con systemd al encender la máquina). Windows 10 de casa: Docker Desktop + WSL2 con **k3d** (entorno de desarrollo).
6. **Motor de alertas**: CronJob/proceso que evalúa reglas declarativas (en ConfigMap versionado en Git) y notifica por **Telegram**. Es la pieza que convierte "muevo datos" en "sistema que decide".
7. **Observabilidad doble**: Grafana con dashboards *aprovisionados como código* (JSON en el repo) — uno de negocio (descuentos, z-scores, semáforos) y uno de operación (salud del pipeline, frescura del dato). Prometheus para métricas de los jobs.
8. **CI/CD**: GitHub Actions → build de imágenes a GHCR. ArgoCD (GitOps) como extra de fase final.
9. **README honesto**: la arquitectura está deliberadamente sobredimensionada con fines demostrativos; para uso personal bastaría un cron + SQLite. Decirlo explícitamente (demuestra criterio). README en inglés (o bilingüe).

## Plan de construcción por fases (cada fase funciona sola)

1. **Fundación**: esquema de tablas bronze/silver/gold (claves, índices) + scripts Python de ingesta a pelo contra Postgres, con el patrón backfill idempotente. Validar todas las fuentes. Sin K8s todavía.
2. **Contenerización**: Dockerfile por ingestor (o imagen única multi-comando, decidir), .env.example, secrets fuera del repo.
3. **k3s en el Ubuntu**: namespace, Secrets, ConfigMap del universo de tickers, CronJobs del carril lento.
4. **Poller intradía**: Deployment con lógica de horario de mercado (y calendario de festivos USA — duda abierta: ¿librería exchange_calendars?).
5. **Capa gold + Grafana**: queries de descuento/z-score/Buffett, dashboards provisionados.
6. **Alertas Telegram** con reglas en ConfigMap.
7. **Pulido pro**: Helm chart completo, Prometheus, ArgoCD, CI en GitHub Actions, README con diagrama de arquitectura (estilo de mi repo del medallón AEMET).

## Dudas abiertas a resolver ANTES de escribir código (discutir conmigo)

> **✅ RESUELTAS (2026-07-06).** Las 7 dudas de abajo están decididas — ver **[DECISIONS.md](DECISIONS.md)** para las respuestas, el porqué y el roadmap con estado. Se conservan aquí como contexto original.

1. **¿Neon/Supabase (BD externa) o Postgres en el k3s del Ubuntu?** Trade-offs: dependencia externa y límites del free tier vs tener que anclar los datos a una máquina.
2. **¿TimescaleDB o Postgres plano?** Volumen estimado: ~6.000 filas/día intradía (~1,5M/año) — Postgres plano sobra, pero Timescale suma como skill. ¿Merece la complejidad?
3. **Fuente concreta del NAV**: ¿scraping de CEFConnect, de las webs de las gestoras (BlackRock/PIMCO publican NAV diario), o hay algún endpoint semioficial más estable? Investigar antes de comprometerse.
4. **¿Polling yfinance (simple) o websocket Finnhub (más impresionante) para el intradía?** Propuesta: empezar con polling, dejar el websocket como fase 2 documentada.
5. **Estructura de imagen Docker**: ¿una imagen por componente o una imagen única con múltiples entrypoints? (Coste de mantenimiento vs pureza.)
6. **Nombre definitivo del repo** y disclaimer legal en el README (los datos son informativos, no consejo de inversión).
7. **Gestión de secretos** (API keys de FRED/Finnhub/Telegram): Secrets de K8s a pelo, sealed-secrets, o SOPS. Para el alcance de este proyecto, ¿qué es proporcionado?

## Cómo quiero trabajar

- Empieza por las **dudas abiertas** (sección anterior), una a una, con recomendación razonada.
- Después, fase 1: diseña el esquema de tablas y el primer ingestor (yfinance con backfill idempotente) y explícame cada decisión.
- Commits pequeños con mensajes claros. Tests donde aporten (parsers, cálculo de z-score, lógica de horario de mercado).
- Si algo de lo decidido arriba te parece un error, dilo y argumenta — pero no lo cambies en silencio.
