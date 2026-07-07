-- 0006: distribuciones de los CEFs + vista de yield (mini-fase pedida 2026-07-07).
--
-- En un CEF de crédito el yield ES la tesis de inversión, y los descuentos
-- explotan casi siempre alrededor de recortes de distribución. Esta tabla es
-- además el prerrequisito de datos para cualquier análisis chollo-vs-trampa.

-- Una fila por distribución pagada (grano = fecha ex-dividendo).
-- yfinance da una cifra por ex-date (agrega ordinaria+extra si coinciden).
CREATE TABLE silver.distributions (
    ticker      text NOT NULL,
    ex_date     date NOT NULL,
    amount      numeric(18, 6) NOT NULL, -- $ por acción
    source      text NOT NULL DEFAULT 'yfinance',
    ingested_at timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (ticker, ex_date) -- el contrato del upsert, como siempre
);

-- Yield por CEF, una fila por ticker con la foto de "ahora".
-- Dos denominadores a propósito (la diferencia entre ambos ES el descuento):
--   sobre PRECIO = lo que cobra quien compra hoy;
--   sobre NAV    = lo que la cartera tiene que generar (sostenibilidad).
-- Y dos numeradores:
--   TTM     = lo realmente pagado en 12 meses (mira atrás);
--   current = última distribución anualizada al ritmo de pagos TTM.
-- Señal de dominio: current < TTM ⇒ RECORTE reciente (la alerta que importa).
CREATE VIEW gold.yield_ttm AS
WITH last_price AS (
    SELECT DISTINCT ON (ticker) ticker, trading_date, close
    FROM silver.prices_daily
    ORDER BY ticker, trading_date DESC
),
last_nav AS (
    SELECT DISTINCT ON (ticker) ticker, nav
    FROM silver.navs
    ORDER BY ticker, nav_date DESC
),
ttm AS (
    SELECT
        ticker,
        sum(amount) AS dist_ttm,
        count(*) AS n_dist_ttm, -- ~12 = pagador mensual, ~4 = trimestral
        max(ex_date) AS last_ex_date
    FROM silver.distributions
    WHERE ex_date > current_date - interval '365 days'
    GROUP BY ticker
),
last_dist AS (
    SELECT DISTINCT ON (ticker) ticker, amount AS last_amount
    FROM silver.distributions
    ORDER BY ticker, ex_date DESC
)
SELECT
    t.ticker,
    t.dist_ttm,
    t.n_dist_ttm,
    t.last_ex_date,
    d.last_amount,
    p.close AS price,
    n.nav,
    t.dist_ttm / p.close * 100 AS yield_ttm_on_price_pct,
    t.dist_ttm / n.nav * 100 AS yield_ttm_on_nav_pct,
    d.last_amount * t.n_dist_ttm / p.close * 100 AS yield_current_pct
FROM ttm t
JOIN last_price p USING (ticker)
LEFT JOIN last_nav n USING (ticker)
LEFT JOIN last_dist d USING (ticker);
