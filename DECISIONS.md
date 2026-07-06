# Decisiones de arquitectura — k8s-market-sentinel

> Registro de las decisiones que resuelven las **"Dudas abiertas"** de [PROJECT_BRIEF.md](PROJECT_BRIEF.md).
> Fecha: 2026-07-06. El brief sigue siendo el contexto completo; este documento fija las respuestas y el estado.

## Estado general

- ✅ **Fase de decisiones cerrada** — 7/7 dudas resueltas (la #7 marcada como provisional).
- ⬜ **Código: sin empezar** — repo aún sin inicializar (git no creado).
- ➡️ **Siguiente:** Fase 1 (esquema bronze/silver/gold + primer ingestor yfinance con backfill idempotente).

## Decisiones (resuelven las 7 dudas abiertas del brief)

1. **Base de datos → Neon** (Postgres gestionado, free tier). Estado externalizado: cualquier clúster escribe contra la misma base y sigue disponible con las máquinas apagadas. Reversible a Postgres en k3s (vía connection string en un Secret) si algún día hay un servidor 24/7.
2. **Modelo → Postgres plano** (no TimescaleDB). El volumen (~1,5M filas/año) está muy por debajo de donde Timescale aporta; el medallón ya cubre rollups (capa gold) y retención (poda de bronze). Escape futuro aditivo: particionado nativo de Postgres → hypertables solo si se migra a un Postgres con Timescale.
3. **NAV → CEFConnect (primario) + yfinance (cross-check).** Un solo scraper para todo el universo, tras una abstracción `NAVProvider`; webs de gestoras como fallback documentado. Etiquetado por frescura: `definitivo` / `estimado` / `rancio`. Huecos tolerados y documentados (no se inventa dato). Las alertas conocen la confianza del NAV. *(Endpoint concreto pendiente de spike de validación.)*
4. **Intradía → polling yfinance** (no websocket). Cadencia config-driven ~60-90s (no 30s: el dato ya viene ~15 min retrasado). Patrón de backfill idempotente: tick en vivo + gap-fill al arrancar; upsert por `(ticker, ts)`; over-fetch + dedup. Finnhub, opcional no prioritario.
5. **Imagen → una sola + dispatcher CLI** (`sentinel poller` / `nav` / `alerts`). Monorepo de una app; lockstep del mismo SHA contra el esquema compartido; K8s elige la cara vía `command`/`args`. Secretos nunca horneados en la imagen.
6. **Nombre → `k8s-market-sentinel`** + disclaimer fuerte y completo (pura y fuertemente formativo/educativo, no consejo de inversión) + licencia MIT + línea de disclaimer en el pie de las alertas.
7. **Secretos → por fases (PROVISIONAL, revisar en GitOps).** Secrets a pelo (`.env` en `.gitignore`) en fases 1-3 → **SOPS + age** al cablear GitOps (clave que viaja entre k3s/k3d/local, mejor que Sealed Secrets para la portabilidad). Vault/ESO fuera de alcance.

## Roadmap (fases del brief) con estado

| Fase | Descripción | Estado |
|---|---|---|
| 0 | Decisiones de arquitectura (7 dudas abiertas) | ✅ Hecho |
| 1 | Fundación: esquema bronze/silver/gold + primer ingestor (yfinance, backfill idempotente), validar fuentes | ➡️ Siguiente |
| 2 | Contenerización: Dockerfile, `.env.example`, secrets fuera del repo | ⬜ |
| 3 | k3s en Ubuntu: namespace, Secrets, ConfigMap de tickers, CronJobs del carril lento | ⬜ |
| 4 | Poller intradía: Deployment con lógica de horario de mercado + festivos USA | ⬜ |
| 5 | Capa gold + Grafana: queries de descuento/z-score/Buffett, dashboards provisionados | ⬜ |
| 6 | Alertas Telegram con reglas declarativas en ConfigMap | ⬜ |
| 7 | Pulido pro: Helm completo, Prometheus, ArgoCD, CI en GitHub Actions, README con diagrama | ⬜ |

## Pendientes / a revisar más adelante

- **Secretos (#7):** revisar la arquitectura en detalle al llegar a la fase GitOps.
- **Spike CEFConnect (#3):** validar endpoint/cobertura/estabilidad antes de fijar la fuente de NAV al 100%.
- **Prerrequisito de Fase 1:** crear el proyecto en Neon y obtener la connection string.
- **Festivos USA (Fase 4):** decidir librería del calendario de mercado (¿`exchange_calendars`?) — sigue abierta en el brief.

---

*El principio transversal de todas estas decisiones: elegir lo simple sabiendo cuál es la puerta de escape, externalizar lo irremplazable, y empujar la diferenciación hacia la capa Kubernetes.*
