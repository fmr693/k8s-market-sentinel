# k8s-market-sentinel

Plataforma **Kubernetes-nativa** de vigilancia de CEFs (closed-end funds) de crédito de EE. UU.: ingesta el precio, el NAV y las señales macro, calcula **descuentos sobre NAV y sus z-scores** en una capa gold de Postgres, y (en fases próximas) avisará por Telegram cuando aparezcan descuentos anormalmente anchos.

> ## ⚠️ Disclaimer
>
> Este proyecto es **pura y estrictamente formativo/educativo** y una herramienta personal de seguimiento. **Nada de lo que contiene —código, métricas, umbrales, alertas o documentación— es consejo de inversión** ni recomendación de compra o venta de ningún instrumento financiero. Los datos provienen de fuentes públicas gratuitas (con retardos, huecos y errores posibles) y las métricas pueden estar mal calculadas. Si inviertes basándote en esto, es bajo tu única y exclusiva responsabilidad.

## Qué hace

- **Ingesta con backfill idempotente** (el sistema se autorrepara tras apagones: pregunta "¿cuál es mi último dato?" y pide desde ahí):
  - Velas diarias de ~24 tickers vía yfinance (CEFs + benchmarks).
  - NAV diario por CEF desde CEFConnect (la pieza frágil, aislada en su propio job).
  - Series macro de FRED: diferencial high-yield, Treasury 10Y, PIB.
  - Fixing oficial EUR/USD del BCE (frankfurter).
- **Medallón sobre Postgres** (Neon, gestionado): `bronze` (crudo jsonb, append-only) → `silver` (tipado, deduplicado por clave natural) → `gold` (vistas: descuento, z-score 252 sesiones, indicador Buffett).
- **Kubernetes**: imagen única multi-comando (`sentinel migrate|ingest-prices|ingest-nav|ingest-macro|ingest-fx`), CronJobs del carril lento con `timeZone: Europe/Madrid`, ConfigMap del universo de tickers y Secret generados con kustomize.

## Estado (fases)

| Fase | Contenido | Estado |
|---|---|---|
| 0 | Decisiones de arquitectura | ✅ |
| 1 | Esquema medallón + 4 ingestores validados contra Neon | ✅ |
| 2 | Contenerización (imagen única, non-root) | ✅ |
| 3 | K8s: namespace, Secret, ConfigMap, CronJobs (validado en k3d) | ✅ |
| 4 | Poller intradía (Deployment con horario de mercado) | ⬜ |
| 5 | Capa gold completa + dashboards Grafana provisionados | ⬜ |
| 6 | Alertas Telegram con reglas declarativas | ⬜ |
| 7 | Helm, Prometheus, CI a GHCR, ArgoCD | ⬜ |

El detalle vivo de cada decisión (con el porqué y las lecciones aprendidas) está en **[DECISIONS.md](DECISIONS.md)**; el contexto completo del proyecto, en **[PROJECT_BRIEF.md](PROJECT_BRIEF.md)**.

## Nota de honestidad arquitectónica

Esta arquitectura está **deliberadamente sobredimensionada** con fines demostrativos y de aprendizaje (Kubernetes, observabilidad, CD). Para el uso personal real bastaría un cron y una base SQLite. La gracia está en construir la versión "de plataforma" sabiendo en cada decisión cuál sería la alternativa simple — y documentándolo.

## Arranque rápido

```bash
# 1. Configuración (los secretos nunca van al repo)
cp .env.example .env          # rellena DATABASE_URL (Neon o local) y FRED_API_KEY

# 2. Postgres local de desarrollo (opcional: puedes apuntar directo a Neon)
docker compose -f docker-compose.dev.yml up -d

# 3. Instalar y ejecutar
pip install -e ".[dev]"
sentinel migrate              # aplica las migraciones SQL
sentinel ingest-prices        # backfill del universo completo
sentinel ingest-macro && sentinel ingest-fx && sentinel ingest-nav
pytest                        # tests de la lógica pura

# 4. Kubernetes local (k3d) — requiere cgroup v2 (ver DECISIONS.md #19)
docker build -t sentinel:dev .
k3d cluster create sentinel && k3d image import sentinel:dev -c sentinel
kubectl apply -k .            # namespace + ConfigMap + Secret + CronJobs
kubectl -n sentinel create -f deploy/k8s/job-migrate.yaml
```

## Licencia

[MIT](LICENSE) — úsalo, cópialo y aprende de él libremente (bajo el disclaimer de arriba).
