-- 0010: cross-check del NAV (fase 10, slice 2).
--
-- La tesis del proyecto trata el NAV como la pieza frágil (scraping). Este
-- check da una SEGUNDA OPINIÓN: comparar el NAV de CEFConnect con el que Yahoo
-- publica para el mismo fondo (ticker X…X). Si discrepan, el punto reciente de
-- CEFConnect puede ser preliminar/erróneo — la evidencia de FSCO del brief
-- (7,14 vs 6,97 = 2,4%, cuando los días previos coincidían al céntimo).

-- El NAV del proxy yfinance, keyed POR EL CEF (no por el ticker X…X): el
-- ingestor resuelve el mapping en Python (config nav_check), así el join de
-- gold es trivial y la config nunca se filtra al SQL. Grano y clave natural
-- iguales que silver.navs → el cross-check es un JOIN directo por fecha.
CREATE TABLE silver.nav_proxy (
    cef_ticker   text NOT NULL,
    nav_date     date NOT NULL,
    nav          numeric(18, 6) NOT NULL, -- el close del proxy = el NAV según Yahoo
    proxy_ticker text NOT NULL,           -- el X…X de donde salió (auditoría)
    source       text NOT NULL DEFAULT 'yfinance',
    ingested_at  timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (cef_ticker, nav_date)
);

-- Divergencia relativa entre las dos fuentes de NAV, por CEF y fecha con dato
-- en AMBAS. Join por misma fecha: ambos son NAV del mismo día de mercado, así
-- que las fechas casan; un CEF sin proxy (BCAT/ADX) simplemente no aparece.
CREATE VIEW gold.nav_crosscheck AS
SELECT
    n.ticker,
    n.nav_date,
    n.nav                          AS nav_cefconnect,
    x.nav                          AS nav_proxy,
    x.proxy_ticker,
    abs(n.nav - x.nav) / n.nav     AS divergence
FROM silver.navs n
JOIN silver.nav_proxy x
    ON x.cef_ticker = n.ticker
    AND x.nav_date = n.nav_date;

-- Degradación automática de nav_quality (el objetivo de la fase 10): la foto
-- "ahora" del universo ahora juzga la confianza del NAV por DOS ejes —
-- frescura Y acuerdo entre fuentes. Antes solo por frescura (0005/0008). Se
-- reescribe con CREATE OR REPLACE partiendo de la definición VIGENTE (la de
-- 0008, que ya añadió zscore_band al final) y añadiendo nav_divergence AL FINAL:
-- Postgres solo deja añadir columnas al final, jamás renombrar/reordenar.
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
),
-- La última divergencia conocida por CEF: alimenta la degradación de abajo.
last_xcheck AS (
    SELECT DISTINCT ON (ticker) ticker, divergence
    FROM gold.nav_crosscheck
    ORDER BY ticker, nav_date DESC
)
SELECT
    n.ticker,
    p.ts AS price_ts,
    p.price,
    n.nav,
    n.nav_date,
    (now() AT TIME ZONE 'UTC')::date - n.nav_date AS nav_age_days,
    -- nav_quality con DOS ejes (fase 10):
    --   'rancio'     = NAV de >4 días (el scraper lleva días sin refrescar)
    --   'sospechoso' = fresco PERO CEFConnect y el proxy yfinance discrepan >2%
    --                  (el punto reciente puede estar mal) — degradación automática
    --   'estimado'   = fresco y las fuentes concuerdan (lo normal)
    -- El 2% es el mismo tipo de umbral en-SQL que el "≤4 días" de la frescura
    -- (0005): vive en la vista, no en config, por coherencia con aquél.
    CASE
        WHEN (now() AT TIME ZONE 'UTC')::date - n.nav_date > 4 THEN 'rancio'
        WHEN xc.divergence > 0.02 THEN 'sospechoso'
        ELSE 'estimado'
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
    gold.zscore_band(z.zscore) AS zscore_band, -- de 0008: se conserva idéntico
    xc.divergence AS nav_divergence -- NUEVA (al final): NULL si el CEF no tiene proxy
FROM last_nav n
LEFT JOIN last_price p USING (ticker)
LEFT JOIN last_z z USING (ticker)
LEFT JOIN last_xcheck xc USING (ticker);
