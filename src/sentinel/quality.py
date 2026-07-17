"""Runner del framework de calidad de dato (fase 10).

Los checks se DECLARAN en config/quality_checks.yaml; aquí solo se ejecutan:
correr el SQL de cada uno, traducir el número a un status según sus umbrales y
guardar el veredicto en gold.data_quality_results (el historial). La SEMÁNTICA
de "qué es un dato sano" vive en la config (los umbrales) — este módulo es
fontanería.

Dos garantías deliberadas:
- El SQL de cada check corre en una transacción READ ONLY: un check es una
  MEDICIÓN, jamás debe mutar; si uno lo intenta (bug de config), Postgres lo
  corta y el check queda en 'error', no rompe nada.
- Un check que peta (SQL malo, tabla que no existe) NO tumba a los demás: se
  registra con status 'error' y se sigue — misma filosofía "commit por ticker"
  de los ingestores.
"""

from __future__ import annotations

import datetime as dt
import logging

import psycopg

from .config import QualityCheck

log = logging.getLogger(__name__)

RESULT_UPSERT = """
INSERT INTO gold.data_quality_results (check_name, run_ts, value, status, detail)
VALUES (%(check_name)s, %(run_ts)s, %(value)s, %(status)s, %(detail)s)
ON CONFLICT (check_name, run_ts) DO UPDATE SET
    value = EXCLUDED.value, status = EXCLUDED.status, detail = EXCLUDED.detail
"""


def evaluate(value: float | None, check: QualityCheck) -> str:
    """Número → status según los umbrales del check. 'fail' manda sobre 'warn';
    `value is None` (el check no midió nada) → 'error'."""
    if value is None:
        return "error"
    if check.fail_above is not None and value > check.fail_above:
        return "fail"
    if check.fail_below is not None and value < check.fail_below:
        return "fail"
    if check.warn_above is not None and value > check.warn_above:
        return "warn"
    if check.warn_below is not None and value < check.warn_below:
        return "warn"
    return "ok"


def _run_one(
    conn: psycopg.Connection, check: QualityCheck
) -> tuple[float | None, str, str | None]:
    """Corre el SQL del check en READ ONLY y lo evalúa → (value, status, detail)."""
    try:
        with conn.cursor() as cur:
            # SET TRANSACTION READ ONLY como PRIMER comando de la transacción:
            # psycopg abre la transacción con él, así el check no puede mutar.
            cur.execute("SET TRANSACTION READ ONLY")
            cur.execute(check.query)
            row = cur.fetchone()
        conn.rollback()  # cierra la transacción de medición sin dejar rastro
        value = None if row is None or row[0] is None else float(row[0])
        return value, evaluate(value, check), None
    except Exception as exc:
        conn.rollback()
        log.exception("check %s: el SQL falló", check.name)
        return None, "error", str(exc)[:500]


def run_checks(conn: psycopg.Connection, checks: list[QualityCheck]) -> list[dict]:
    """Corre todos los checks, persiste sus veredictos con un `run_ts` COMÚN (así
    una corrida es una columna en el historial) y los devuelve para el resumen.
    Un check que peta se registra como 'error' y no tumba a los demás."""
    run_ts = dt.datetime.now(dt.timezone.utc)
    results: list[dict] = []
    for check in checks:
        value, status, detail = _run_one(conn, check)
        with conn.cursor() as cur:
            cur.execute(
                RESULT_UPSERT,
                {
                    "check_name": check.name,
                    "run_ts": run_ts,
                    "value": value,
                    "status": status,
                    "detail": detail,
                },
            )
        conn.commit()  # veredicto por veredicto: si el proceso muere, lo escrito queda
        log.info("check %s: %s (value=%s)", check.name, status, value)
        results.append(
            {"name": check.name, "value": value, "status": status,
             "detail": detail, "unit": check.unit}
        )
    return results
