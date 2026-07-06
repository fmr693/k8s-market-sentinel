"""Ingestor de tipos de cambio oficiales del BCE vía frankfurter.app.

Tercera aparición del patrón de la casa (último dato → pedir con solape →
bronze → upsert). Las filas van a silver.macro_series con series_id
'EURUSD_ECB': es una serie (id, fecha, valor) como cualquier macro — crear
una tabla propia sería duplicar estructura.

¿Por qué esta fuente si ya tenemos EURUSD=X por yfinance? Son cosas
distintas: EURUSD=X es cotización de mercado (se mueve todo el día); esto es
el FIXING oficial diario del BCE (~16:00 CET, definitivo). La banda
1,10–1,20 de las alertas de Michael se evalúa contra el oficial.

frankfurter: API pública sin key, datos del BCE, formato
GET /v1/2015-01-01..?from=EUR&to=USD → {"rates": {"2015-01-02": {"USD": 1.2043}, ...}}
Solo devuelve días hábiles TARGET (sin festivos europeos): los huecos de
fin de semana son normales, no errores.
(El dominio api.frankfurter.app migró a api.frankfurter.dev/v1 — verificado
2026-07-06: el viejo hace 301 y urllib acababa en 403 tras la redirección.)
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import urllib.parse
import urllib.request

import psycopg
from psycopg.types.json import Jsonb

from ..config import Universe
from .common import compute_fetch_start

log = logging.getLogger(__name__)

FRANKFURTER_BASE = "https://api.frankfurter.dev/v1"

BRONZE_INSERT = """
INSERT INTO bronze.raw_fetches (source, entity, payload, meta)
VALUES ('frankfurter', %(entity)s, %(payload)s, %(meta)s)
"""

SILVER_UPSERT = """
INSERT INTO silver.macro_series (series_id, obs_date, value, source)
VALUES (%(series_id)s, %(obs_date)s, %(value)s, 'frankfurter')
ON CONFLICT (series_id, obs_date) DO UPDATE SET
    value = EXCLUDED.value,
    ingested_at = now()
"""


def series_id_for(pair: str) -> str:
    """'EUR/USD' → 'EURUSD_ECB' (la convención que ya anticipaba el esquema)."""
    base, quote = pair.split("/")
    return f"{base}{quote}_ECB"


def fetch_rates(pair: str, start: dt.date) -> dict:
    """GET del rango [start, hoy] ('start..' = abierto por la derecha).

    Con User-Agent propio: el CDN de frankfurter bloquea el UA por defecto
    de urllib ('Python-urllib/x.y' → 403, verificado 2026-07-06). Además,
    identificarse es cortesía básica con una API pública gratuita.
    """
    base, quote = pair.split("/")
    params = urllib.parse.urlencode({"from": base, "to": quote})
    req = urllib.request.Request(
        f"{FRANKFURTER_BASE}/{start.isoformat()}..?{params}",
        headers={"User-Agent": "k8s-market-sentinel/0.1 (personal portfolio project)"},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.load(resp)


def rates_to_rows(pair: str, payload: dict) -> list[dict]:
    """JSON de frankfurter → filas para silver, ordenadas por fecha."""
    _, quote = pair.split("/")
    sid = series_id_for(pair)
    rows = [
        {
            "series_id": sid,
            "obs_date": dt.date.fromisoformat(day),
            "value": float(quotes[quote]),
        }
        for day, quotes in sorted(payload.get("rates", {}).items())
        if quote in quotes
    ]
    return rows


def _ingest_one(conn: psycopg.Connection, pair: str, universe: Universe) -> int:
    sid = series_id_for(pair)
    with conn.cursor() as cur:
        cur.execute(
            "SELECT max(obs_date) FROM silver.macro_series WHERE series_id = %s",
            (sid,),
        )
        last_date = cur.fetchone()[0]

    # Los fixings del BCE no se revisan: bastaría solape 0, pero reutilizamos
    # el macro (30 días) porque re-upsertear ~20 filas idénticas cuesta nada
    # y nos cubre ante cualquier corrección puntual de la fuente.
    start = compute_fetch_start(
        last_date, universe.price_history_start, universe.macro_overlap_days
    )
    log.info("%s: último dato=%s → pidiendo desde %s", sid, last_date, start)

    payload = fetch_rates(pair, start)
    rows = rates_to_rows(pair, payload)
    if not rows:
        log.warning("%s: frankfurter no devolvió tipos", sid)
        return 0

    with conn.cursor() as cur:
        cur.execute(
            BRONZE_INSERT,
            {
                "entity": sid,
                "payload": Jsonb(payload),
                "meta": Jsonb({"pair": pair, "start": start.isoformat()}),
            },
        )
        cur.executemany(SILVER_UPSERT, rows)

    conn.commit()
    log.info("%s: %d fixings upserteados (%s → %s)", sid, len(rows),
             rows[0]["obs_date"], rows[-1]["obs_date"])
    return len(rows)


def ingest_fx_rates(
    conn: psycopg.Connection, universe: Universe, pairs: list[str] | None = None
) -> dict[str, int]:
    """Ingesta los pares FX de config (o un subconjunto)."""
    result: dict[str, int] = {}
    for pair in pairs or universe.fx_pairs:
        try:
            result[series_id_for(pair)] = _ingest_one(conn, pair, universe)
        except Exception:
            conn.rollback()
            log.exception("%s: fallo ingiriendo; continúo con el resto", pair)
            result[series_id_for(pair)] = -1
    return result
