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
- **Grafana aprovisionado como código**: dashboards JSON y datasource en el repo, pod sin estado (ConfigMaps generados por kustomize), rol de Postgres **solo lectura** (`grafana_ro`, mínimo privilegio). El **modo visitante** (entrar sin login, en rol de solo lectura, aterrizando directo en el dashboard) también es configuración versionada, no un usuario creado a mano: sin PVC, un usuario de la UI se evaporaría en el siguiente reinicio.
- **Contexto, no números desnudos**: un nivel suelto ("diferencial de crédito 2,84 %") no se puede juzgar. La capa gold calcula **percentil, z-score y años de historia disponibles** de cada serie macro, y donde la fuente está limitada por licencia a una ventana de 3 años se añade una serie **no restringida con 40 años** como regla de medir — sin sustituir a la relevante, que sigue siendo la del semáforo.
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
| 6 | CI/CD: GitHub Actions → GHCR + lock de dependencias (`uv.lock`) | ✅ 0.8.0 publicada en GHCR |
| 7a | Secretos GitOps-ready: cifrados en el repo con SOPS + age | ✅ |
| 7b | ArgoCD + KSOPS: el clúster se sincroniza solo desde git | ✅ |
| 8 | Alertas Telegram con reglas declarativas + digest diario | ⬜ |
| 8½ | Backtest de la señal de descuento (¿revierte tras cruzar z-score −2?) | ✅ |
| 9 | Prometheus + PVC (observabilidad completa) | ✅ |
| 10 | Calidad de dato como framework declarativo (checks en config, cross-check del NAV, panel "Data Quality") | ✅ |
| 11 | Helm chart, score opcional, README final con guía de portado | ⬜ |
| 12 | Acceso remoto: túnel `cloudflared` como interruptor de demo + Grafana en modo visitante | ✅ |

> **Reencuadre (2026-07-08):** este proyecto no compite en producto financiero — compite en **arquitectura portable**. La tesis CEF es la carga útil demostrativa; el patrón (medallón, ingesta idempotente, config-driven, crash-only, GitOps) es lo que se deja a prueba de bombas y se puede aplicar a cualquier otro dominio de datos.

El detalle vivo de cada decisión (con el porqué y las lecciones aprendidas) está en **[DECISIONS.md](DECISIONS.md)**; el contexto completo del proyecto, en **[PROJECT_BRIEF.md](PROJECT_BRIEF.md)**. Para entender **cómo circula el dato** de una API a un panel —quién hace qué, cómo y por qué— hay un diagrama autocontenido en **[docs/arquitectura.html](docs/arquitectura.html)** (se abre con doble clic, sin dependencias).

## Nota de honestidad arquitectónica

Esta arquitectura está **deliberadamente sobredimensionada** con fines demostrativos y de aprendizaje (Kubernetes, observabilidad, CD). Para el uso personal real bastaría un cron y una base SQLite. La gracia está en construir la versión "de plataforma" sabiendo en cada decisión cuál sería la alternativa simple — y documentándolo.

## Portable no es lo mismo que permanente

Conviene separar dos propiedades que se confunden:

- **Portabilidad — conseguida.** *Todo* lo necesario para reconstruir el sistema viaja: los manifests de Kubernetes (`deploy/k8s/`), el kustomize, las Applications de ArgoCD (`deploy/gitops/`), los dashboards, la config de Prometheus y hasta los **secretos, cifrados** (`deploy/secrets/`). La imagen vive en GHCR y los datos en Neon. El único secreto fuera de git es la clave age, que viaja contigo. Borrar el clúster y rehacerlo desde cero en minutos es parte del flujo normal — se ha hecho varias veces.
- **Permanencia (alta disponibilidad) — pendiente.** El clúster de desarrollo es k3d dentro de Docker Desktop, en un portátil. Cuando el portátil se apaga, no corre nadie: los CronJobs de ingesta no se ejecutan y **no recuperan** las noches perdidas más allá de `startingDeadlineSeconds`.

Lo que salva el dato en ese hueco no es el orquestador, sino el **backfill idempotente**: cada ingestor pregunta "¿cuál es mi último dato?" y sigue desde ahí, así que una sola ejecución tapa días de parón. Kubernetes aporta robustez de *proceso* (pod que muere, nodo que cae, deriva manual); la robustez del *dato* es de la aplicación. La permanencia llega con la fase pendiente: desplegar la misma configuración, sin cambiar una línea, en un servidor que no se apaga.

## Arranque rápido

> ¿Solo quieres **verlo funcionando** en una máquina que no es la tuya?
> Ve a **[DEMO.md](DEMO.md)**: dashboards con datos reales en ~3 minutos.
> Ahí está también el **interruptor de demo** para enseñarlo a distancia
> (`cloudflared`, sin abrir puertos ni IP pública) — con el aviso de por qué
> se apaga al terminar: la URL de un *quick tunnel* no es un secreto.

```bash
# 1. Configuración. Si tienes la clave age del proyecto, el .env se genera solo
#    desde el secreto CIFRADO del repo (no hay que copiar credenciales a mano):
./scripts/env-from-secret.sh prod        # o 'local' para el Postgres del compose
#    Sin la clave age: cp .env.example .env y rellenarlo a mano.

# 2. Stack local de desarrollo (Postgres + Grafana + Prometheus + Pushgateway)
docker compose -f docker-compose.dev.yml up -d

# 3. Instalar y ejecutar (uv es lo que usa el CI; pip install -e también vale)
uv sync --extra dev
sentinel migrate              # aplica las migraciones SQL
sentinel ingest-prices        # backfill del universo completo
sentinel ingest-macro && sentinel ingest-fx && sentinel ingest-nav
sentinel ingest-distributions # distribuciones de los CEFs (yield)
sentinel ingest-nav-proxy     # NAV de la segunda fuente (para el cross-check)
sentinel check-quality        # corre los checks de config/quality_checks.yaml
sentinel poller               # (opcional) intradía en vivo, Ctrl+C para salir
pytest                        # tests de la lógica pura

# 4. Kubernetes local (k3d) — requiere cgroup v2 (ver DECISIONS.md #19)
# --api-port fija un puerto BAJO a propósito: los aleatorios de k3d caen en
# rangos que WinNAT excluye y el clúster queda incomunicado (DECISIONS.md #22)
k3d cluster create sentinel --api-port 6550
# No hace falta construir ni importar la imagen: el transformador de
# kustomization.yaml apunta a la de GHCR, que es pública y el clúster se baja sola.
kubectl apply -k .            # namespace + ConfigMaps + 7 CronJobs + poller + Grafana + Prometheus + Pushgateway
# El Secret NO lo genera kustomize desde la fase 7a (decisión #39): vive cifrado
# en el repo y se aplica descifrándolo al vuelo. Sin este paso, los pods no arrancan.
sops -d deploy/secrets/sentinel-env.prod.yaml | kubectl apply -f -
kubectl -n sentinel create -f deploy/k8s/job-migrate.yaml
# Grafana: kubectl -n sentinel port-forward svc/grafana 3000:3000 → http://localhost:3000
```

## Licencia

[MIT](LICENSE) — úsalo, cópialo y aprende de él libremente (bajo el disclaimer de arriba).
