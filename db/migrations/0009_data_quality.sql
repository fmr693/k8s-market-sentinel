-- 0009: framework de calidad de dato (fase 10).
--
-- Los CHECKS viven en CONFIG (config/quality_checks.yaml): declarativos, se
-- añaden editando YAML, no SQL. Esta tabla es su HISTORIAL — una fila por
-- (check, ejecución). Es una desviación CONSCIENTE de "gold = vistas" (#6):
-- un check es una MEDICIÓN EN EL TIEMPO y necesita estado; es justo el caso
-- que preveía la puerta de escape del MATERIALIZED VIEW. La semántica
-- (valor→status según umbrales) la pone el runner en Python leyendo la config;
-- aquí solo se guarda el veredicto ya calculado.
--
-- status:
--   'ok'    = dentro de umbrales
--   'warn'  = supera el umbral de aviso (dato sospechoso, no roto)
--   'fail'  = supera el umbral de fallo (algo está roto)
--   'error' = el SQL del check no pudo ejecutarse (distinto de 'fail': ni
--             siquiera se pudo medir)
CREATE TABLE gold.data_quality_results (
    check_name  text NOT NULL,
    run_ts      timestamptz NOT NULL,
    value       numeric,          -- lo que devolvió el check (NULL si 'error' o sin datos)
    status      text NOT NULL CHECK (status IN ('ok', 'warn', 'fail', 'error')),
    detail      text,             -- contexto legible opcional (el error, un recuento...)
    PRIMARY KEY (check_name, run_ts)
);

-- Para "¿desde cuándo falla este check?": las corridas ordenables en el tiempo.
CREATE INDEX data_quality_results_ts_idx ON gold.data_quality_results (run_ts);

-- El último veredicto de cada check: LA fila del panel "Data Quality".
-- DISTINCT ON = "la más reciente de cada grupo", el idiom de la casa (cef_snapshot).
CREATE VIEW gold.data_quality_latest AS
SELECT DISTINCT ON (check_name)
    check_name,
    run_ts,
    value,
    status,
    detail
FROM gold.data_quality_results
ORDER BY check_name, run_ts DESC;
