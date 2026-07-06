"""Ingestor de series macro desde la API de FRED, con backfill idempotente.

Mismo patrón que prices.py (a propósito — es EL patrón de la casa):
último dato → pedir desde (último − solape) → bronze crudo → upsert en silver.

Particularidades de FRED:
- El solape es mayor (macro_overlap_days=30 vs 5): FRED revisa hacia atrás
  (el PIB se revisa dos veces en los meses siguientes a su publicación).
- Los huecos vienen como value="." (festivos en series diarias): se OMITEN,
  el esquema exige value NOT NULL — no se inventa dato.
- urllib de la stdlib y no `requests`: es UNA llamada GET con parámetros;
  una dependencia menos que auditar y actualizar.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import urllib.parse
import urllib.request

import psycopg
from psycopg.types.json import Jsonb

from ..config import Universe, get_fred_api_key
from .common import compute_fetch_start

log = logging.getLogger(__name__)

FRED_OBSERVATIONS_URL = "https://api.stlouisfed.org/fred/series/observations"

BRONZE_INSERT = """
INSERT INTO bronze.raw_fetches (source, entity, payload, meta)
VALUES ('fred', %(entity)s, %(payload)s, %(meta)s)
"""

SILVER_UPSERT = """
INSERT INTO silver.macro_series (series_id, obs_date, value, source)
VALUES (%(series_id)s, %(obs_date)s, %(value)s, 'fred')
ON CONFLICT (series_id, obs_date) DO UPDATE SET
    value = EXCLUDED.value,
    ingested_at = now()
"""


def fetch_observations(series_id: str, api_key: str, start: dt.date) -> dict:
    """GET a /fred/series/observations. Devuelve el JSON crudo (va a bronze tal cual)."""
    params = urllib.parse.urlencode(
        {
            "series_id": series_id,
            "api_key": api_key,
            "file_type": "json",
            "observation_start": start.isoformat(),
        }
    )
    with urllib.request.urlopen(f"{FRED_OBSERVATIONS_URL}?{params}", timeout=60) as resp:
        return json.load(resp)


def observations_to_rows(series_id: str, payload: dict) -> list[dict]:
    """JSON de FRED → filas para silver. Los '.' (sin dato) se descartan."""
    rows: list[dict] = []
    for obs in payload.get("observations", []):
        if obs["value"] == ".":
            continue
        rows.append(
            {
                "series_id": series_id,
                "obs_date": dt.date.fromisoformat(obs["date"]),
                "value": float(obs["value"]),
            }
        )
    return rows


def _ingest_one(conn: psycopg.Connection, series_id: str, universe: Universe, api_key: str) -> int:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT max(obs_date) FROM silver.macro_series WHERE series_id = %s",
            (series_id,),
        )
        last_date = cur.fetchone()[0]

    start = compute_fetch_start(
        last_date, universe.price_history_start, universe.macro_overlap_days
    )
    log.info("%s: último dato=%s → pidiendo desde %s", series_id, last_date, start)

    payload = fetch_observations(series_id, api_key, start)
    rows = observations_to_rows(series_id, payload)
    if not rows:
        log.warning("%s: FRED no devolvió observaciones", series_id)
        return 0

    with conn.cursor() as cur:
        cur.execute(
            BRONZE_INSERT,
            {
                "entity": series_id,
                "payload": Jsonb(payload),
                "meta": Jsonb({"observation_start": start.isoformat()}),
            },
        )
        cur.executemany(SILVER_UPSERT, rows)

    conn.commit()  # commit por serie: misma lógica de supervivencia que en precios
    log.info("%s: %d observaciones upserteadas (%s → %s)", series_id, len(rows),
             rows[0]["obs_date"], rows[-1]["obs_date"])
    return len(rows)


def ingest_macro_series(
    conn: psycopg.Connection, universe: Universe, series: list[str] | None = None
) -> dict[str, int]:
    """Ingesta todas las series FRED de config (o un subconjunto)."""
    api_key = get_fred_api_key()
    result: dict[str, int] = {}
    for series_id in series or universe.fred_series:
        try:
            result[series_id] = _ingest_one(conn, series_id, universe, api_key)
        except Exception:
            conn.rollback()
            log.exception("%s: fallo ingiriendo; continúo con el resto", series_id)
            result[series_id] = -1
    return result
