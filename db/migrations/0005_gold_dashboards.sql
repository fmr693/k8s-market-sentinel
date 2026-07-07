-- 0005: vistas gold para los dashboards (fase 5, decisión 5.2).
--
-- Principio fijado en esta fase: la SEMÁNTICA del dato (calidad, frescura,
-- prima/descuento) vive en gold; Grafana solo pinta. Así el motor de alertas
-- (fase 6) leerá LA MISMA verdad que el dashboard — nada de duplicar lógica
-- en queries de paneles. Los UMBRALES personales (-5%, -7%, banda EUR/USD)
-- NO van aquí: son líneas de referencia en Grafana hoy y reglas declarativas
-- en ConfigMap en fase 6 (cambiar un umbral no debe ser un ALTER VIEW).
--
-- Sobre el signo, convención del proyecto: (precio - NAV) / NAV con signo.
--   negativo = descuento (zona de compra) · positivo = PRIMA (los PIMCO
--   cotizan con primas persistentes: se verán el primer día).

-- Descuento/prima INTRADÍA — el "estimado" del brief: precio del minuto
-- contra el último NAV conocido (as-of join, mismo patrón LATERAL que el
-- PIB trimestral en buffett_indicator).
--
-- Etiquetado por frescura del NAV (días naturales):
--   'estimado' = NAV de <=4 días (lo normal: el de ayer; un lunes usa el del
--                viernes -3 días- y sigue siendo sano — el finde no hay NAV)
--   'rancio'   = NAV de >4 días: el scraper lleva días fallando; el dato se
--                enseña, pero gritando su edad. (Contar SESIONES con el
--                calendario XNYS sería lo perfecto, pero esa lógica no vive
--                en SQL — puerta de escape si algún día importa.)
CREATE VIEW gold.discount_intraday AS
SELECT
    p.ticker,
    p.ts,
    p.price,
    n.nav,
    n.nav_date,
    (p.ts AT TIME ZONE 'UTC')::date - n.nav_date AS nav_age_days,
    CASE
        WHEN (p.ts AT TIME ZONE 'UTC')::date - n.nav_date <= 4 THEN 'estimado'
        ELSE 'rancio'
    END AS nav_quality,
    (p.price - n.nav) / n.nav AS premium_discount
FROM silver.prices_intraday p
CROSS JOIN LATERAL (
    SELECT nav, nav_date
    FROM silver.navs n
    WHERE n.ticker = p.ticker
      AND n.nav_date <= (p.ts AT TIME ZONE 'UTC')::date
    ORDER BY n.nav_date DESC
    LIMIT 1
) n;

-- La foto "ahora mismo" del universo CEF: una fila por ticker con lo último
-- de cada cosa. Es LA tabla del dashboard de negocio (SELECT * ... ORDER BY
-- zscore) y la que leerá el motor de alertas en fase 6.
-- DISTINCT ON = "la fila más reciente de cada grupo", el idiom de Postgres.
CREATE VIEW gold.cef_snapshot AS
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
    z.zscore, -- del último cierre DEFINITIVO (diario): el intradía aún no tiene z
    z.mean_1y,
    z.std_1y,
    z.n_obs,
    z.trading_date AS zscore_date
FROM last_nav n
LEFT JOIN last_price p USING (ticker)
LEFT JOIN last_z z USING (ticker);
