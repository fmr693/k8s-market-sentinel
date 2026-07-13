-- 0008: backtest de la señal de descuento (fase 8½) + banda legible del z-score.
--
-- LA TESIS que pone a prueba esta migración: "cuando el descuento de un CEF se
-- vuelve históricamente grande (z-score cruza por debajo de -2), el descuento
-- REVIERTE (se estrecha) y/o el PRECIO sube en los meses siguientes". El
-- backtest no la afirma: la MIDE sobre la propia serie histórica y deja que los
-- números hablen (con la honestidad de que la muestra crece cada día).
--
-- Todo son VISTAS (como el resto de gold, decisión de 0003): nunca desfasadas,
-- sin job que orquestar. Semántica en gold, Grafana solo pinta.

-- =====================================================================
-- 1. BANDA DEL Z-SCORE (capa de presentación, semántica en gold)
-- =====================================================================
-- Traduce el número crudo del z-score a una etiqueta legible. La DEFINICIÓN de
-- los cortes (-2/-1/+1/+2) vive AQUÍ, no en umbrales de Grafana: así el panel y
-- el futuro motor de alertas leen la misma verdad. Grafana mapea cada token a
-- un color + emoji (eso sí es "pintar"). Se conserva SIEMPRE el número al lado.
-- IMMUTABLE: para un mismo z devuelve siempre lo mismo → Postgres puede cachear.
CREATE OR REPLACE FUNCTION gold.zscore_band(z numeric) RETURNS text
LANGUAGE sql IMMUTABLE AS $$
    SELECT CASE
        WHEN z IS NULL   THEN NULL
        WHEN z <= -2     THEN 'historicamente_barato'
        WHEN z <= -1     THEN 'mas_barato_de_lo_normal'
        WHEN z <   1     THEN 'normal'
        WHEN z <=  2     THEN 'mas_caro_de_lo_normal'
        ELSE                  'historicamente_caro'
    END
$$;

-- Añadir la banda a la foto del universo (número + banda juntos, a petición).
-- CREATE OR REPLACE solo permite AÑADIR columnas al final: por eso zscore_band
-- va la última. El resto es idéntico a la definición de 0005.
CREATE OR REPLACE VIEW gold.cef_snapshot AS
WITH last_price AS (
    SELECT DISTINCT ON (ticker) ticker, ts, price
    FROM silver.prices_intraday
    ORDER BY ticker, ts DESC
),
last_nav AS (
    SELECT DISTINCT ON (ticker) ticker, nav_date, nav, quality AS nav_source_quality
    FROM silver.navs
    ORDER BY ticker, nav_date DESC
),
last_z AS (
    SELECT DISTINCT ON (ticker) ticker, trading_date, discount, mean_1y, std_1y, n_obs, zscore
    FROM gold.discount_zscore
    ORDER BY ticker, trading_date DESC
)
SELECT
    n.ticker,
    p.ts AS price_ts,
    p.price,
    n.nav,
    n.nav_date,
    (now() AT TIME ZONE 'UTC')::date - n.nav_date AS nav_age_days,
    CASE
        WHEN (now() AT TIME ZONE 'UTC')::date - n.nav_date <= 4 THEN 'estimado'
        ELSE 'rancio'
    END AS nav_quality,
    (p.price - n.nav) / n.nav AS premium_discount,
    CASE
        WHEN p.price > n.nav THEN 'prima'
        WHEN p.price < n.nav THEN 'descuento'
        ELSE 'par'
    END AS estado,
    z.zscore,
    z.mean_1y,
    z.std_1y,
    z.n_obs,
    z.trading_date AS zscore_date,
    gold.zscore_band(z.zscore) AS zscore_band  -- NUEVO: la etiqueta legible
FROM last_nav n
LEFT JOIN last_price p USING (ticker)
LEFT JOIN last_z z USING (ticker);

-- =====================================================================
-- 2. ENTRADAS DE SEÑAL: cada CRUCE del z-score por debajo de -2
-- =====================================================================
-- Un "cruce" (no "cada día que está por debajo"): el z-score venía por ENCIMA
-- de -2 ayer y hoy cae por debajo. Con LAG comparamos con la sesión anterior.
-- Motivo: un fondo que se queda barato 30 días seguidos es UNA señal, no 30 —
-- contar cada día inflaría y sesgaría la muestra.
--   * n_obs >= 126: no fiarse del z-score sin al menos ~medio año de ventana.
--   * -2 es el umbral que DEFINE la señal (como el 252 define el z-score);
--     puerta de escape: cambiar la constante = nueva migración.
CREATE VIEW gold.signal_entries AS
WITH z AS (
    SELECT
        ticker, trading_date, discount, zscore, n_obs,
        lag(zscore) OVER (PARTITION BY ticker ORDER BY trading_date) AS zscore_prev
    FROM gold.discount_zscore
)
SELECT
    ticker,
    trading_date AS entry_date,
    discount     AS entry_discount,
    zscore       AS entry_zscore
FROM z
WHERE n_obs >= 126
  AND zscore <= -2
  AND zscore_prev > -2;  -- el cruce: ayer arriba, hoy abajo

-- =====================================================================
-- 3. BACKTEST: qué pasó a +1, +3 y +6 meses tras cada entrada
-- =====================================================================
-- Formato LARGO (una fila por entrada × horizonte): más fácil de agregar y
-- pintar. Para cada (entrada, horizonte) se busca la PRIMERA sesión en/tras
-- entry_date + N meses (as-of por fecha de calendario, patrón LATERAL como el
-- PIB en buffett_indicator). Si esa fecha aún es futura → outcome NULL: el
-- backtest no inventa, la fila queda "pendiente de madurar".
--   discount_change > 0  = el descuento se ESTRECHÓ (revirtió hacia su media).
--   price_return         = rentabilidad simple del precio desde la entrada.
CREATE VIEW gold.signal_backtest AS
SELECT
    e.ticker,
    e.entry_date,
    e.entry_discount,
    e.entry_zscore,
    h.horizon_months,
    o.trading_date              AS outcome_date,
    o.discount                  AS outcome_discount,
    o.zscore                    AS outcome_zscore,
    o.discount - e.entry_discount      AS discount_change,  -- >0 = revierte
    o.price_close / e0.price_close - 1 AS price_return
FROM gold.signal_entries e
JOIN gold.discount_daily e0
    ON e0.ticker = e.ticker AND e0.trading_date = e.entry_date   -- precio en la entrada
CROSS JOIN (VALUES (1), (3), (6)) AS h(horizon_months)
LEFT JOIN LATERAL (
    SELECT dd.trading_date, dd.price_close, dd.discount, dz.zscore
    FROM gold.discount_daily dd
    LEFT JOIN gold.discount_zscore dz
        ON dz.ticker = dd.ticker AND dz.trading_date = dd.trading_date
    WHERE dd.ticker = e.ticker
      AND dd.trading_date >= e.entry_date + make_interval(months => h.horizon_months)
    ORDER BY dd.trading_date
    LIMIT 1
) o ON true;

-- =====================================================================
-- 4. RESUMEN: tasa de acierto por horizonte
-- =====================================================================
-- La "foto" del backtest: por cada horizonte, cuántas señales han MADURADO
-- (outcome no NULL), el cambio medio del descuento y del precio, y las tasas
-- de acierto. nullif(...,0) → cuando aún no hay señales maduras, la tasa es
-- NULL (honesto) en vez de una división por cero.
CREATE VIEW gold.signal_backtest_summary AS
SELECT
    horizon_months,
    count(*)                                        AS n_signals_total,
    count(outcome_date)                             AS n_signals_realized,
    round(avg(discount_change) FILTER (WHERE outcome_date IS NOT NULL)::numeric, 4) AS avg_discount_change,
    round(avg(price_return)    FILTER (WHERE outcome_date IS NOT NULL)::numeric, 4) AS avg_price_return,
    round(
        count(*) FILTER (WHERE discount_change > 0)::numeric
        / nullif(count(outcome_date), 0), 3)        AS reversion_hit_rate,
    round(
        count(*) FILTER (WHERE price_return > 0)::numeric
        / nullif(count(outcome_date), 0), 3)        AS price_up_hit_rate
FROM gold.signal_backtest
GROUP BY horizon_months
ORDER BY horizon_months;
