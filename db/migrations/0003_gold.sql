-- 0003: capa gold — métricas derivadas como VISTAS, no tablas.
--
-- ¿Por qué vistas? Con ~1,5M filas/año Postgres calcula esto al vuelo sin
-- esfuerzo, y una vista nunca está desactualizada ni necesita orquestación
-- (no hay "job de gold" que se pueda olvidar de correr). Puerta de escape
-- documentada: si algún día pesa, se convierten en MATERIALIZED VIEW con un
-- REFRESH en el CronJob nocturno — las queries de los dashboards no cambian.
--
-- Nota sobre config-driven: los tickers del universo viven en config, pero
-- 'GDP'/'WILL5000PR' aparecen aquí a fuego porque DEFINEN la métrica Buffett
-- (no son universo intercambiable, son parte de la fórmula).

-- Descuento diario definitivo: cierre del día vs NAV del mismo día.
-- discount < 0 = el CEF cotiza con descuento (nuestra zona de interés).
CREATE VIEW gold.discount_daily AS
SELECT
    p.ticker,
    p.trading_date,
    p.close AS price_close,
    n.nav,
    n.quality AS nav_quality,
    (p.close - n.nav) / n.nav AS discount
FROM silver.prices_daily p
JOIN silver.navs n
    ON n.ticker = p.ticker
    AND n.nav_date = p.trading_date;

-- Z-score del descuento sobre ventana móvil de 252 sesiones (~1 año bursátil).
-- Ventana por FILAS (sesiones), no por días de calendario: 252 velas son un
-- año de mercado aunque haya festivos o huecos.
CREATE VIEW gold.discount_zscore AS
WITH stats AS (
    SELECT
        ticker,
        trading_date,
        discount,
        avg(discount) OVER w AS mean_1y,
        stddev_samp(discount) OVER w AS std_1y,
        count(*) OVER w AS n_obs
    FROM gold.discount_daily
    WINDOW w AS (
        PARTITION BY ticker
        ORDER BY trading_date
        ROWS BETWEEN 251 PRECEDING AND CURRENT ROW
    )
)
SELECT
    ticker,
    trading_date,
    discount,
    mean_1y,
    std_1y,
    n_obs, -- consumidores: exigir n_obs suficiente (p.ej. >=126) antes de fiarse del z-score
    CASE WHEN std_1y > 0 THEN (discount - mean_1y) / std_1y END AS zscore
FROM stats;

-- Indicador Buffett: market cap total USA / PIB nominal, en %.
-- El PIB es trimestral y el índice diario → para cada observación del índice
-- se toma el ÚLTIMO PIB conocido a esa fecha (LATERAL = "as of").
CREATE VIEW gold.buffett_indicator AS
SELECT
    w.obs_date,
    w.value AS wilshire,
    g.obs_date AS gdp_asof_date,
    g.value AS gdp,
    w.value / g.value * 100 AS buffett_pct
FROM silver.macro_series w
CROSS JOIN LATERAL (
    SELECT obs_date, value
    FROM silver.macro_series g
    WHERE g.series_id = 'GDP' AND g.obs_date <= w.obs_date
    ORDER BY g.obs_date DESC
    LIMIT 1
) g
WHERE w.series_id = 'WILL5000PR';
