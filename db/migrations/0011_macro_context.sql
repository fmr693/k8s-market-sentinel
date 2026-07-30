-- 0011 — Contexto histórico de las series macro.
--
-- EL PROBLEMA QUE RESUELVE. Los semáforos macro del dashboard pintan un NIVEL
-- desnudo: "HY spread 2,84%". Ese número no se puede juzgar sin saber contra
-- qué. Y la trampa es doble:
--   · La serie de HY solo tiene ~3 años (licencia de ICE, ver 0011 en config):
--     en esa ventana el 2,84% cae en el percentil 26 y parece "normal", cuando
--     medido contra décadas es históricamente ESTRECHO.
--   · Un umbral absoluto en Grafana (rojo por encima de 6%) envejece y no dice
--     nada sobre dónde está el valor DENTRO de su propia distribución.
--
-- El proyecto ya resolvió exactamente este problema para los CEFs y no con un
-- umbral, sino con un z-score: la pregunta buena no es "¿es grande?" sino
-- "¿es grande PARA ESTA serie?" (misma lógica que gold.discount_zscore, #26).
-- Esta vista lleva ese tratamiento a las macro.
--
-- POR QUÉ VISTA Y NO TABLA: se recalcula sola con cada ingesta y nunca puede
-- quedarse vieja (decisión #6, "gold = vistas"). Escape documentado: si algún
-- día pesa, MATERIALIZED VIEW + refresh en CronJob.
--
-- POR QUÉ EN GOLD Y NO EN EL PANEL: el percentil y el z-score son SEMÁNTICA,
-- no dibujo (#26). Calcularlos en el SQL del panel los ataría a Grafana y
-- habría que repetirlos en cada sitio que los necesite (las alertas de la
-- fase 8 los querrán igual).
CREATE OR REPLACE VIEW gold.macro_context AS
WITH stats AS (
    SELECT
        series_id,
        count(*)          AS n_obs,
        min(obs_date)     AS first_date,
        max(obs_date)     AS last_date,
        min(value)        AS min_value,
        max(value)        AS max_value,
        avg(value)        AS mean_value,
        stddev_samp(value) AS std_value
      FROM silver.macro_series
     GROUP BY series_id
),
latest AS (
    -- El último valor de cada serie. DISTINCT ON es el patrón de la casa para
    -- "la fila de ahora" (mismo que gold.cef_snapshot).
    SELECT DISTINCT ON (series_id) series_id, obs_date, value
      FROM silver.macro_series
     ORDER BY series_id, obs_date DESC
)
SELECT
    l.series_id,
    l.obs_date        AS as_of,
    l.value           AS current_value,
    s.n_obs,
    s.first_date,
    s.last_date,
    s.min_value,
    s.max_value,
    round(s.mean_value, 4)  AS mean_value,
    -- Percentil del valor actual dentro de TODA la historia disponible:
    -- "qué porcentaje de los días históricos estuvieron por debajo de hoy".
    -- 50 = en su mediana; 5 = históricamente muy bajo; 95 = muy alto.
    round(
        100.0 * (
            SELECT count(*) FROM silver.macro_series m
             WHERE m.series_id = l.series_id AND m.value <= l.value
        )::numeric / NULLIF(s.n_obs, 0)
    , 1) AS pct_rank,
    -- Z-score sobre la historia completa (no ventana móvil: aquí interesa el
    -- contexto de todo el registro, al contrario que en los CEFs donde la
    -- ventana de 252 sesiones es el punto). NULL si no hay dispersión.
    CASE WHEN s.std_value > 0
         THEN round((l.value - s.mean_value) / s.std_value, 2)
    END AS zscore,
    -- Años de historia: hace visible de un golpe si el contexto es fiable.
    -- Con 3 años el percentil dice poco; con 40 dice bastante.
    round(((s.last_date - s.first_date) / 365.25)::numeric, 1) AS years_history
  FROM latest l
  JOIN stats  s ON s.series_id = l.series_id;

COMMENT ON VIEW gold.macro_context IS
  'Contexto de cada serie macro: valor actual, percentil y z-score sobre TODA '
  'su historia, mas los anos de historia disponibles (para saber si el '
  'contexto es fiable). Responde "es alto o bajo PARA ESTA serie" en vez de '
  'dejar un nivel desnudo que hay que saber interpretar.';
