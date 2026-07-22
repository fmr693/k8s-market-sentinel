# k8s-market-sentinel

Plataforma **Kubernetes-nativa** de vigilancia de CEFs (closed-end funds) de crédito de EE. UU.: ingesta el precio (diario e intradía), el NAV, las distribuciones y las señales macro, calcula **descuentos sobre NAV, sus z-scores y el yield de distribución** en una capa gold de Postgres, lo visualiza en **dashboards de Grafana aprovisionados como código**, y (en fases próximas) avisará por Telegram cuando aparezcan descuentos anormalmente anchos o recortes de distribución.

> ## ⚠️ Disclaimer
>
> Este proyecto es **pura y estrictamente formativo/educativo** y una herramienta personal de seguimiento. **Nada de lo que contiene —código, métricas, umbrales, alertas o documentación— es consejo de inversión** ni recomendación de compra o venta de ningún instrumento financiero. Los datos provienen de fuentes públicas gratuitas (con retardos, huecos y errores posibles) y las métricas pueden estar mal calculadas. Si inviertes basándote en esto, es bajo tu única y exclusiva responsabilidad.

## Qué hace

- **Ingesta con backfill idempotente** (el sistema se autorrepara tras apagones: pregunta "¿cuál es mi último dato?" y pide desde ahí):
  - Velas diarias de ~44 tickers vía yfinance (CEFs, benchmarks y watchlist de acciones USA/Europa).
  - NAV diario por CEF desde CEFConnect (la pieza frágil, aislada en su propio job).
  - Distribuciones de los CEFs (el yield ES la tesis en un CEF de crédito; su recorte, la alerta que importa).
  - Series macro de FRED: diferencial high-yield, Treasury 10Y, PIB.
  - Fixing oficial EUR/USD del BCE (frankfurter).
- **Poller intradía**: Deployment crash-only con calendario real de la NYSE (festivos, medias sesiones, DST transatlántico vía `exchange_calendars`), velas 1m en batch, sueño interrumpible y salida limpia con SIGTERM.
- **Medallón sobre Postgres** (Neon, gestionado): `bronze` (crudo jsonb, append-only) → `silver` (tipado, deduplicado por clave natural) → `gold` (vistas: descuento con signo, z-score 252 sesiones, descuento intradía ESTIMADO, yield TTM sobre precio y sobre NAV, indicador Buffett).
- **Grafana aprovisionado como código**: dashboards JSON y datasource en el repo, pod sin estado (ConfigMaps generados por kustomize), rol de Postgres **solo lectura** (`grafana_ro`, mínimo privilegio).
- **Calidad de dato declarativa**: los checks (frescura por fuente, NAVs rancios, divergencia entre fuentes) se **declaran en `config/quality_checks.yaml`** — añadir uno es editar YAML, el código no cambia; un runner los ejecuta en transacción READ ONLY, guarda el veredicto con su historial en gold y sale con código 1 si alguno falla. El NAV, la pieza frágil, tiene **segunda opinión**: se contrasta con el que publica Yahoo para el mismo fondo y `nav_quality` se degrada sola a `sospechoso` si discrepan más de un 2%.
- **Kubernetes**: imagen única multi-comando (`sentinel migrate|ingest-prices|ingest-nav|ingest-nav-proxy|ingest-macro|ingest-fx|ingest-distributions|check-quality|poller`), 7 CronJobs del carril lento con `timeZone: Europe/Madrid`, Deployment del poller con liveness por fichero-latido, ConfigMap del universo de tickers y Secret generados con kustomize.

## Estado (fases)

| Fase | Contenido | Estado |
|---|---|---|
| 0 | Decisiones de arquitectura | ✅ |
| 1 | Esquema medallón + 4 ingestores validados contra Neon | ✅ |
| 2 | Contenerización (imagen única, non-root) | ✅ |
| 3 | K8s: namespace, Secret, ConfigMap, CronJobs (validado en k3d) | ✅ |
| 4 | Poller intradía (Deployment con horario de mercado) | ✅ |
| 5 | Capa gold completa + dashboards Grafana provisionados | ✅ |
| 5½ | Distribuciones + yield TTM (tabla, vista, CronJob y panel) | ✅ |
| 5¾ | Flecos: yield en la tabla del universo, column guide, `intraday_exclude`, annotations de recortes | ✅ |
| 6 | CI/CD: GitHub Actions → GHCR + lock de dependencias (`uv.lock`) | ✅ 0.7.0 publicada en GHCR |
| 7a | Secretos GitOps-ready: cifrados en el repo con SOPS + age | ✅ |
| 7b | ArgoCD + KSOPS: el clúster se sincroniza solo desde git | ✅ |
| 8 | Alertas Telegram con reglas declarativas + digest diario | ⬜ |
| 8½ | Backtest de la señal de descuento (¿revierte tras cruzar z-score −2?) | ✅ |
| 9 | Prometheus + PVC (observabilidad completa) | ✅ |
| 10 | Calidad de dato como framework declarativo (checks en config, cross-check del NAV, panel "Data Quality") | ✅ |
| 11 | Helm chart, score opcional, README final con guía de portado | ⬜ |

> **Reencuadre (2026-07-08):** este proyecto no compite en producto financiero — compite en **arquitectura portable**. La tesis CEF es la carga útil demostrativa; el patrón (medallón, ingesta idempotente, config-driven, crash-only, GitOps) es lo que se deja a prueba de bombas y se puede aplicar a cualquier otro dominio de datos.

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
sentinel ingest-distributions # distribuciones de los CEFs (yield)
sentinel ingest-nav-proxy     # NAV de la segunda fuente (para el cross-check)
sentinel check-quality        # corre los checks de config/quality_checks.yaml
sentinel poller               # (opcional) intradía en vivo, Ctrl+C para salir
pytest                        # tests de la lógica pura

# 4. Kubernetes local (k3d) — requiere cgroup v2 (ver DECISIONS.md #19)
docker build -t sentinel:dev .
# --api-port fija un puerto BAJO a propósito: los aleatorios de k3d caen en
# rangos que WinNAT excluye y el clúster queda incomunicado (DECISIONS.md #22)
k3d cluster create sentinel --api-port 6550 && k3d image import sentinel:dev -c sentinel
kubectl apply -k .            # namespace + ConfigMap + Secret + CronJobs + poller + Grafana
kubectl -n sentinel create -f deploy/k8s/job-migrate.yaml
# Grafana: kubectl -n sentinel port-forward svc/grafana 3000:3000 → http://localhost:3000
```

## Licencia

[MIT](LICENSE) — úsalo, cópialo y aprende de él libremente (bajo el disclaimer de arriba).
