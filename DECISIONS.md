# Decisiones de arquitectura — k8s-market-sentinel

> Registro de las decisiones que resuelven las **"Dudas abiertas"** de [PROJECT_BRIEF.md](PROJECT_BRIEF.md).
> Fecha: 2026-07-06. El brief sigue siendo el contexto completo; este documento fija las respuestas y el estado.

## Estado general

- ✅ **Fase de decisiones cerrada** — 7/7 dudas resueltas (la #7 marcada como provisional).
- 🔶 **Fase 1 en curso** (2026-07-06): esquema medallón desplegado en **Neon** (proyecto creado ✓); ingestor de precios (universo completo: 60.780 velas) e ingestor FRED (3.707 observaciones) funcionando y validados en la nube con idempotencia comprobada. Semáforos ya consultables: HY OAS y Buffett operativos en gold.
- ➡️ **Siguiente:** resto de fase 1 — ingestor FX (BCE/frankfurter) y spike de CEFConnect para el NAV.

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

## Roadmap (fases del brief) con estado

| Fase | Descripción | Estado |
|---|---|---|
| 0 | Decisiones de arquitectura (7 dudas abiertas) | ✅ Hecho |
| 1 | Fundación: esquema bronze/silver/gold + primer ingestor (yfinance, backfill idempotente), validar fuentes | 🔶 En curso — hecho: esquema + ingestor precios validado; falta: FRED, FX, spike NAV |
| 2 | Contenerización: Dockerfile, `.env.example`, secrets fuera del repo | ⬜ |
| 3 | k3s en Ubuntu: namespace, Secrets, ConfigMap de tickers, CronJobs del carril lento | ⬜ |
| 4 | Poller intradía: Deployment con lógica de horario de mercado + festivos USA | ⬜ |
| 5 | Capa gold + Grafana: queries de descuento/z-score/Buffett, dashboards provisionados | ⬜ |
| 6 | Alertas Telegram con reglas declarativas en ConfigMap | ⬜ |
| 7 | Pulido pro: Helm completo, Prometheus, ArgoCD, CI en GitHub Actions, README con diagrama | ⬜ |

## Pendientes / a revisar más adelante

- **Secretos (#7):** revisar la arquitectura en detalle al llegar a la fase GitOps.
- **Spike CEFConnect (#3):** validar endpoint/cobertura/estabilidad antes de fijar la fuente de NAV al 100%.
- **BAMLH0A0HYM2 limitada a ~3 años vía API** (restricción de licencia ICE, verificada en el payload de bronze: se pidió desde 2015 y FRED devolvió desde 2023-07-04). Suficiente de sobra para el z-score a 1 año; documentar en el README como limitación conocida.
- **Festivos USA (Fase 4):** decidir librería del calendario de mercado (¿`exchange_calendars`?) — sigue abierta en el brief.

---

*El principio transversal de todas estas decisiones: elegir lo simple sabiendo cuál es la puerta de escape, externalizar lo irremplazable, y empujar la diferenciación hacia la capa Kubernetes.*
