-- 0004: el numerador del Buffett pasa de FRED (WILL5000PR, serie ELIMINADA
-- de FRED cuando Wilshire dejó de publicar allí en 2023) al índice ^W5000
-- vía yfinance, que ya entra por el ingestor de precios (decisión 2026-07-06).
--
-- Nota: migración NUEVA en vez de editar 0003 — las migraciones aplicadas no
-- se tocan. DROP + CREATE (no OR REPLACE) porque cambian las columnas.
--
-- Semántica: el nivel del ^W5000 es un PROXY del market cap total USA
-- (≈ miles de millones de $; la calibración original era 1 punto = $1B).
-- Para un semáforo de valoración lo que importa es la serie relativa a su
-- propia historia, no el nivel absoluto exacto.

DROP VIEW gold.buffett_indicator;

CREATE VIEW gold.buffett_indicator AS
SELECT
    p.trading_date AS obs_date,
    p.close AS w5000_close,
    g.obs_date AS gdp_asof_date,
    g.value AS gdp,
    p.close / g.value * 100 AS buffett_pct
FROM silver.prices_daily p
CROSS JOIN LATERAL (
    SELECT obs_date, value
    FROM silver.macro_series g
    WHERE g.series_id = 'GDP' AND g.obs_date <= p.trading_date
    ORDER BY g.obs_date DESC
    LIMIT 1
) g
WHERE p.ticker = '^W5000';
