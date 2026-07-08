-- 0007: vista gold.distribution_cuts — los RECORTES de distribución (fase 5¾).
--
-- Por qué existe: la tesis del proyecto es que un recorte de distribución
-- precede a la explosión del descuento sobre NAV. Esta vista aísla ESOS
-- eventos (no todas las distribuciones) para pintarlos como annotations sobre
-- la serie de descuento en Grafana: se VE si el recorte llegó antes que la
-- ampliación del descuento. Semántica en gold, Grafana solo pinta (decisión #26).
--
-- Definición de "recorte": un pago por debajo (>2%, para filtrar redondeo) de
-- LOS DOS pagos anteriores del mismo CEF. Por qué los dos y no solo el
-- inmediato: muchos CEFs (sobre todo los de contraste de renta variable, tipo
-- ADX o BST) reparten una distribución ESPECIAL de fin de año enorme; el pago
-- normal siguiente sería un "recorte" del -90% frente a ella, un falso positivo
-- que taparía los recortes reales. Exigir que el pago quede por debajo también
-- del pago pre-especial descarta ese caso (el pago normal NO está por debajo de
-- su propio nivel previo) y conserva los recortes de verdad, incluidos los
-- graduales. Transparente y auditable en SQL (decisión #7 de fase 1).
--
-- Limitación conocida y acotada (estilo de la casa): un recorte real que ocurra
-- justo DESPUÉS de una distribución especial podría no marcarse. Caso raro y
-- documentado. Escape futuro aditivo: mediana móvil de los N pagos previos.
CREATE VIEW gold.distribution_cuts AS
WITH seq AS (
    SELECT
        ticker,
        ex_date,
        amount,
        lag(amount, 1) OVER w AS prev_amount,
        lag(amount, 2) OVER w AS prev2_amount
    FROM silver.distributions
    WINDOW w AS (PARTITION BY ticker ORDER BY ex_date)
)
SELECT
    ticker,
    ex_date,
    amount,
    prev_amount,
    round((amount / prev_amount - 1) * 100, 1) AS change_pct  -- negativo: cuánto recorta
FROM seq
WHERE prev_amount IS NOT NULL
  AND prev2_amount IS NOT NULL
  AND amount < prev_amount * 0.98    -- por debajo del pago anterior
  AND amount < prev2_amount * 0.98;  -- y del de antes (descarta post-especial)
