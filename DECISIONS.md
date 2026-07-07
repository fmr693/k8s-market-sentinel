# Decisiones de arquitectura — k8s-market-sentinel

> Registro de las decisiones que resuelven las **"Dudas abiertas"** de [PROJECT_BRIEF.md](PROJECT_BRIEF.md).
> Fecha: 2026-07-06. El brief sigue siendo el contexto completo; este documento fija las respuestas y el estado.

## Estado general

- ✅ **Fase de decisiones cerrada** — 7/7 dudas resueltas (la #7 marcada como provisional).
- ✅ **Fase 1 completada** (2026-07-06): esquema medallón en **Neon** + 4 ingestores validados en la nube con idempotencia comprobada — precios yfinance (60.780 velas), FRED (3.707 obs.), FX BCE (2.946 fixings) y **NAV CEFConnect** (4.646 NAVs diarios, 19/19 CEFs). La capa gold produce **descuentos y z-scores reales** que coinciden al céntimo con los publicados por CEFConnect (validación externa por camino independiente).
- ✅ **Fase 2 completada** (2026-07-06): imagen única `sentinel:dev` (python:3.13-slim, non-root, 481 MB) con las 5 caras del CLI; validada ejecutando `migrate` + ingestas reales contra Neon desde el contenedor.
- ✅ **Fase 3 completada en k3d local** (2026-07-07): namespace + Secret/ConfigMap generados por kustomize + 4 CronJobs con `timeZone: Europe/Madrid`; validado end-to-end (Job de migración + CronJob disparado a mano ingiriendo contra Neon desde el clúster). **Pendiente**: replicar en el k3s del Ubuntu (requiere publicar la imagen en GHCR).
- ✅ **Imagen publicada en GHCR** (2026-07-07): `ghcr.io/fmr693/k8s-market-sentinel:0.1.0` (+`latest`, digest `76cc7768…`), paquete **público**. Verificado con pull anónimo (logout + rmi + pull limpio: baja sin credenciales). Manifests apuntados a GHCR (transformador `images:` en kustomization.yaml; `job-migrate.yaml` a mano por vivir fuera) y runbook completo en `deploy/DEPLOY_UBUNTU.md`. **La Fase 3 queda a un solo paso: ejecutar el runbook en el servidor Ubuntu cuando haya acceso (hoy está virgen e inaccesible).**
- ➡️ **Siguiente:** Fase 4 (poller intradía) en paralelo; runbook en el Ubuntu en cuanto haya acceso.

## Decisiones (resuelven las 7 dudas abiertas del brief)

1. **Base de datos → Neon** (Postgres gestionado, free tier). Estado externalizado: cualquier clúster escribe contra la misma base y sigue disponible con las máquinas apagadas. Reversible a Postgres en k3s (vía connection string en un Secret) si algún día hay un servidor 24/7.
2. **Modelo → Postgres plano** (no TimescaleDB). El volumen (~1,5M filas/año) está muy por debajo de donde Timescale aporta; el medallón ya cubre rollups (capa gold) y retención (poda de bronze). Escape futuro aditivo: particionado nativo de Postgres → hypertables solo si se migra a un Postgres con Timescale.
3. **NAV → CEFConnect (primario) + yfinance (cross-check).** Un solo scraper para todo el universo, tras una abstracción `NAVProvider`; webs de gestoras como fallback documentado. Etiquetado por frescura: `definitivo` / `estimado` / `rancio`. Huecos tolerados y documentados (no se inventa dato). Las alertas conocen la confianza del NAV. *(Endpoint concreto pendiente de spike de validación.)*
4. **Intradía → polling yfinance** (no websocket). Cadencia config-driven ~60-90s (no 30s: el dato ya viene ~15 min retrasado). Patrón de backfill idempotente: tick en vivo + gap-fill al arrancar; upsert por `(ticker, ts)`; over-fetch + dedup. Finnhub, opcional no prioritario.
5. **Imagen → una sola + dispatcher CLI** (`sentinel poller` / `nav` / `alerts`). Monorepo de una app; lockstep del mismo SHA contra el esquema compartido; K8s elige la cara vía `command`/`args`. Secretos nunca horneados en la imagen.
6. **Nombre → `k8s-market-sentinel`** + disclaimer fuerte y completo (pura y fuertemente formativo/educativo, no consejo de inversión) + licencia MIT + línea de disclaimer en el pie de las alertas.
7. **Secretos → por fases (PROVISIONAL, revisar en GitOps).** Secrets a pelo (`.env` en `.gitignore`) en fases 1-3 → **SOPS + age** al cablear GitOps (clave que viaja entre k3s/k3d/local, mejor que Sealed Secrets para la portabilidad). Vault/ESO fuera de alcance.

## Decisiones técnicas de Fase 1 (2026-07-06)

Tomadas al implementar la fundación; todas con puerta de escape documentada:

1. **Migraciones → SQL numerado + runner propio** (`sentinel migrate`, tabla `public.schema_migrations`). No Alembic: su fuerte es autogenerar diffs desde un ORM que no usamos; ficheros SQL planos son 100% transparentes. Regla: una migración aplicada nunca se edita — los cambios son migraciones nuevas.
2. **Capas como schemas de Postgres** (`bronze.`, `silver.`, `gold.`): namespace explícito en cada query y permisos por capa si hicieran falta.
3. **Bronze = una sola tabla genérica** (`bronze.raw_fetches`, jsonb, append-only). Lo que varía entre fuentes es la forma del payload → jsonb; nunca se actualiza ni deduplica (es la auditoría que permite re-procesar silver).
4. **Silver separa grano diario e intradía** (`prices_daily` con `date`, `prices_intraday` con `timestamptz` UTC): claves naturales distintas, retención futura distinta; fusionarlas obligaría a inventar timestamps falsos. La PK natural de cada tabla ES el contrato del upsert idempotente.
5. **Precios SIN ajustar** (`auto_adjust=False`): el descuento compara el precio real de pantalla contra el NAV publicado; los precios ajustados por dividendos reescriben la historia (y los CEFs de crédito reparten mucho).
6. **Gold = vistas, no tablas**: nunca desactualizadas, cero orquestación. Escape: `MATERIALIZED VIEW` + refresh en CronJob si algún día pesa (las queries de dashboards no cambiarían).
7. **Sin ORM ni framework de config**: psycopg3 a pelo, SQL visible, `argparse` para el dispatcher. El proyecto es SQL-céntrico y formativo; menos magia = más comprensión.
8. **Commit por ticker** en la ingesta: si el proceso muere a mitad del universo, lo ingerido queda a salvo y la siguiente ejecución se autorrepara. Un ticker que falla no tumba a los demás; el exit code del CLI refleja fallos (los Jobs de K8s se enteran por ahí).
9. **Tests**: lógica pura (ventana de backfill, parseo) con unit tests; el upsert idempotente se valida contra Postgres real (docker-compose.dev.yml), no con mocks.
10. **Numerador del Buffett → `^W5000` vía yfinance** (decisión de Michael, 2026-07-06). WILL5000PR fue **eliminada de FRED** (Wilshire dejó de publicar allí en 2023; la API devuelve 400). Alternativas evaluadas: ^W5000 diario (proxy del market cap, reutiliza el ingestor de precios) vs serie Z.1 trimestral oficial (BOGZ1LM893064105Q, ~2,5 meses de retraso). Se eligió frescura diaria; la vista gold lee de `silver.prices_daily` (migración 0004).
11. **Ingestor FRED**: mismo patrón que precios con solape de **30 días** (FRED revisa hacia atrás — comprobado en vivo: el segundo run recogió una revisión del PIB de Q4-2025). Los huecos `value="."` se omiten. `urllib` de la stdlib, sin dependencia nueva.
12. **FX BCE → `api.frankfurter.dev/v1`** (el dominio `.app` migró: hace 301 y urllib acababa en 403). Serie `EURUSD_ECB` en `silver.macro_series`: es el **fixing oficial** (~16:00 CET, definitivo) contra el que se evalúa la banda 1,10–1,20; el `EURUSD=X` de yfinance es cotización de mercado, otra cosa. Lección reutilizable: mandar **User-Agent propio** — los CDN bloquean el de Python por defecto.
13. **NAV → CEFConnect `api/v3/pricinghistory/{ticker}/1Y` (spike #3 RESUELTO).** Hallazgos: el periodo `1Y` es el único con datos **diarios** (~245 puntos); 1M/3M/5Y vienen muestreados para gráficos. Acepta nuestro UA honesto. El payload trae `NAVTicker` (`XWDIX`...) para el cross-check con yfinance — verificado que funciona. Divergencia del patrón: la API va por periodos, no rangos → siempre se pide 1Y y se upsertea todo (backfill máximo = 1 año; huecos anteriores tolerados, como preveía el brief). Solo se guarda `NAVData`: el descuento es métrica NUESTRA (gold), el suyo se descarta — y aún así ambos coinciden al céntimo (validación externa).
14. *(Fase 2, 2026-07-06)* **Imagen: `python:3.13-slim`, un solo stage, non-root.** Sin multi-stage porque todas las deps llegan como wheels precompilados — no hay toolchain que separar (puerta de escape documentada en el Dockerfile). slim y no alpine: musl obligaría a compilar pandas. Usuario `sentinel` (uid 1000) para que K8s pueda exigir `runAsNonRoot`. `ENTRYPOINT ["sentinel"]` + `CMD ["--help"]`: K8s elige la cara vía `args:`. Cache mount de BuildKit para pip. 481 MB (pandas/numpy pesan; asumido para una imagen de datos).
15. *(Fase 2)* **`pip install -e .` DENTRO de la imagen**: el paquete queda en `/app/src` y `REPO_ROOT=/app` resuelve `config/` y `db/migrations` en las mismas rutas relativas que en desarrollo. Con install normal el código iría a site-packages y esas rutas se romperían. Escape futuro: package-data con `importlib.resources`.
16. *(Fase 2)* **Secretos jamás en la imagen**: `.env` excluido por `.dockerignore` (además de `.gitignore`). Local: `docker run --env-file .env`; K8s: Secret → env vars. Validado end-to-end: la imagen ejecutó `migrate`, `ingest-fx` e `ingest-prices` contra Neon.
17. *(Fase 3, 2026-07-07)* **kustomize con generadores, kustomization.yaml en la RAÍZ del repo.** El ConfigMap del universo se genera desde `config/tickers.yaml` y el Secret desde `.env` (una sola fuente de verdad; secretos fuera del repo). En la raíz porque kustomize se niega a leer ficheros fuera de su directorio (LoadRestrictionsRootOnly). `disableNameSuffixHash: true`: los consumidores son Jobs que nacen en cada ejecución (el rollout que motiva el hash no aplica) y `job-migrate.yaml` puede referenciar nombres estables. Despliegue = `kubectl apply -k .`
18. *(Fase 3)* **CronJobs con `timeZone: "Europe/Madrid"`** (los horarios viven en mi zona, DST incluido), `concurrencyPolicy: Forbid`, `startingDeadlineSeconds: 3600` — una ejecución perdida por tener la máquina apagada NO se recupera: el backfill idempotente se pone al día en la siguiente (el diseño del brief hecho spec de K8s). Migraciones = Job puntual con `generateName` (se lanza con `kubectl create`, no forma parte del estado deseado).
19. *(Fase 3)* **Dos lecciones de infra aprendidas en vivo:** (a) el kubelet de K8s ≥1.35 **se niega a arrancar sobre cgroup v1** — la VM de WSL2 lo usa por defecto; arreglo: `C:\Users\Felipe\.wslconfig` con `kernelCommandLine = cgroup_no_v1=all` + `wsl --shutdown` (el Ubuntu del servidor no lo necesitará). (b) Con `runAsNonRoot: true`, el `USER` de la imagen debe ser **numérico** (`USER 1000`): el kubelet no lee `/etc/passwd` de la imagen y con un nombre falla con `CreateContainerConfigError`.

## Decisiones técnicas de Fase 4 — poller intradía (2026-07-07)

20. **Calendario de mercado → `exchange_calendars` (XNYS)**, envuelta en `market_hours.py` (el resto del código nunca importa la librería: puerta de escape). Resuelve las tres trampas que hacen inviable el "if 15:30-22:00 CET": DST transatlántico (~3 semanas/año el desfase NY-Madrid es 5h, no 6h — el horario real es 9:30-16:00 America/New_York), festivos con reglas móviles/traslados, y medias sesiones (cierre 13:00 NY). Los tests cubren exactamente esas tres trampas. Las alertas (fase 6) reutilizarán esta interfaz.
21. **Anatomía crash-only del bucle** (`poller.py`): gap-fill al arrancar hace que morir sea barato (el camino de recuperación ES el arranque normal); sueño interrumpible con `threading.Event` + SIGTERM (nunca `time.sleep`: la señal despierta al instante y muere limpio dentro de la gracia de K8s); siestas de ≤15 min con latido cuando cierra el mercado; tick agendado por reloj de pared (sin deriva). Errores por tick → log y siguiente; **30 fallos seguidos → crash a conciencia** (mejor reinicio que zombi). El gap-fill fallido NO crashea: reintentar en bucle de reinicios amplificaría un 429 (cada reinicio = otra petición 7d).
22. **Ingesta intradía = velas 1m en UNA petición batch** (`ingest/intraday.py`, decisión 4.3): rate-limit ×24 mejor que pedir por ticker; timestamps DEL MERCADO (la PK (ticker, ts) deduplica de verdad tras reinicios); tick normal y gap-fill son la misma función con distinta ventana ("1d" vs "7d" — el máximo 1m de yfinance; huecos más viejos, irreparables y asumidos). **Bronze solo en el gap-fill**, desviación consciente: guardar 24 DataFrames casi idénticos cada minuto inflaría el free tier repitiendo el 99%.
23. **Conexión a BD POR TICK, no persistente**: Neon autosuspende con la inactividad — una conexión abierta el fin de semana (65h) estaría muerta el lunes. Un handshake cada ~75s no cuesta nada y deja a Neon dormir fuera de horario (amable con el free tier). Cadencia config-driven: `defaults.poll_interval_seconds: 75` en tickers.yaml.
24. **Deployment con `replicas: 1` y `strategy: Recreate`** (rolling = dos pollers unos segundos; preferible un hueco que cubre el gap-fill). **Liveness por fichero-latido + exec probe** (`find /tmp/heartbeat -newermt "-20 minutes"`; 15 de siesta máxima + margen): si el bucle se cuelga sin morir, el fichero envejece y K8s reinicia — el zombi se cura solo. **Sin readinessProbe a conciencia** (el poller no recibe tráfico; ponerla sería cargo cult). Escape futuro: cuando Prometheus (fase 7) pida `/metrics`, la probe podrá migrar a HTTP.

## Roadmap (fases del brief) con estado

| Fase | Descripción | Estado |
|---|---|---|
| 0 | Decisiones de arquitectura (7 dudas abiertas) | ✅ Hecho |
| 1 | Fundación: esquema bronze/silver/gold + 4 ingestores (precios, FRED, FX, NAV) con backfill idempotente, fuentes validadas | ✅ Hecho (2026-07-06) |
| 2 | Contenerización: Dockerfile, `.env.example`, secrets fuera del repo | ✅ Hecho (2026-07-06) |
| 3 | k3s en Ubuntu: namespace, Secrets, ConfigMap de tickers, CronJobs del carril lento | ✅ Validado en k3d local (2026-07-07); Ubuntu pendiente de GHCR |
| 4 | Poller intradía: Deployment con lógica de horario de mercado + festivos USA | 🔶 Código+manifest+tests hechos (2026-07-07); falta publicar imagen 0.2.0 y validar en clúster |
| 5 | Capa gold + Grafana: queries de descuento/z-score/Buffett, dashboards provisionados | ⬜ |
| 6 | Alertas Telegram con reglas declarativas en ConfigMap | ⬜ |
| 7 | Pulido pro: Helm completo, Prometheus, ArgoCD, CI en GitHub Actions, README con diagrama | ⬜ |

## Pendientes / a revisar más adelante

- **Smoke test del poller (cierra Fase 4):** desde el PC con el `.env` bueno — (a) local: `sentinel poller` con mercado abierto y ver ticks en el log (y el gap-fill de arranque al principio); (b) en k3d: `kubectl apply -k .` con la 0.2.0, comprobar que el Deployment levanta, que el latido mantiene la liveness verde y que `silver.prices_intraday` se llena. Verificar también la salida limpia con SIGTERM (`kubectl delete pod` y mirar el log de despedida).

- **Secretos (#7):** revisar la arquitectura en detalle al llegar a la fase GitOps.
- **Cross-check NAV automatizado (fase de calidad/alertas):** comparar el NAV de CEFConnect con el ticker `X...X` de yfinance y degradar `quality` a `estimado` si divergen más de una tolerancia. Evidencia que lo justifica (2026-07-06): el **último** punto de CEFConnect para FSCO (7,14 el 7/02) difiere un 2,4% de XFSCX (6,97) mientras los días anteriores coinciden al céntimo → el punto más reciente puede ser preliminar. Ojo: el `NAVTicker` de ECAT es `ECAT` a secas (rareza de la API).
- **BAMLH0A0HYM2 limitada a ~3 años vía API** (restricción de licencia ICE, verificada en el payload de bronze: se pidió desde 2015 y FRED devolvió desde 2023-07-04). Suficiente de sobra para el z-score a 1 año; documentar en el README como limitación conocida.
- **Backfill de NAV limitado a 1 año** (la API de CEFConnect no da más histórico diario): los descuentos anteriores a 2025-07 no existirán — hueco documentado, no reparable (previsto en el brief).
- **Festivos USA (Fase 4):** decidir librería del calendario de mercado (¿`exchange_calendars`?) — sigue abierta en el brief.
- **Publicar la imagen antes de k3s en el Ubuntu (Fase 3):** el k3d local puede importarla con `k3d image import sentinel:dev`, pero el k3s del servidor necesitará un registry (GHCR manual ahora, CI en fase 7).
- **Lock de dependencias:** la imagen instala "lo último" en cada build (hoy: pandas 3.0.3, yfinance 1.5.1 — verificado que funcionan). Para builds reproducibles, valorar `uv lock`/`pip-tools` antes del CI de fase 7.

---

*El principio transversal de todas estas decisiones: elegir lo simple sabiendo cuál es la puerta de escape, externalizar lo irremplazable, y empujar la diferenciación hacia la capa Kubernetes.*
