"""Ingestor del NAV-proxy de yfinance para el cross-check (fase 10, slice 2).

Yahoo publica el NAV de los CEFs con un ticker propio (X…X, p.ej. XWDIX). Este
ingestor baja su cierre diario y lo guarda en silver.nav_proxy KEYED POR EL CEF
(WDI), resolviendo el mapping WDI→XWDIX de config aquí en Python — así el
cross-check de gold es un JOIN directo por (ticker, fecha) y la config nunca
toca el SQL. Mismo patrón de backfill idempotente que el resto de ingestores.
"""

from __future__ import annotations

import json
import logging
import math

import psycopg
import yfinance as yf
from psycopg.types.json import Jsonb

from ..config import Universe
from .common import compute_fetch_start

log = logging.getLogger(__name__)

BRONZE_INSERT = """
INSERT INTO bronze.raw_fetches (source, entity, payload, meta)
VALUES ('yfinance', %(entity)s, %(payload)s, %(meta)s)
"""

SILVER_UPSERT = """
INSERT INTO silver.nav_proxy (cef_ticker, nav_date, nav, proxy_ticker, source)
VALUES (%(cef)s, %(nav_date)s, %(nav)s, %(proxy)s, 'yfinance')
ON CONFLICT (cef_ticker, nav_date) DO UPDATE SET
    nav = EXCLUDED.nav,
    proxy_ticker = EXCLUDED.proxy_ticker,
    ingested_at = now()
"""


def _to_float(value) -> float | None:
    if value is None:
        return None
    f = float(value)
    return None if math.isnan(f) else f


def _ingest_one(conn: psycopg.Connection, cef: str, proxy: str, universe: Universe) -> int:
    with conn.cursor() as cur:
        cur.execute("SELECT max(nav_date) FROM silver.nav_proxy WHERE cef_ticker = %s", (cef,))
        last_date = cur.fetchone()[0]

    start = compute_fetch_start(last_date, universe.price_history_start, universe.overlap_days)
    df = yf.Ticker(proxy).history(
        start=start.isoformat(), interval="1d", auto_adjust=False, actions=False
    )
    if df.empty:
        log.warning("%s (proxy %s): yfinance no devolvió NAV (¿ticker correcto?)", cef, proxy)
        return 0

    rows: list[dict] = []
    for idx, r in df.iterrows():
        nav = _to_float(r.get("Close"))
        if nav is None:  # el close es el NAV; sin él la fila no sirve
            continue
        rows.append({"cef": cef, "nav_date": idx.date(), "nav": nav, "proxy": proxy})
    if not rows:
        return 0

    # Bronze lean: solo la serie de close (el NAV), no el OHLC entero.
    payload = json.loads(df[["Close"]].to_json(orient="split", date_format="iso"))
    with conn.cursor() as cur:
        cur.execute(
            BRONZE_INSERT,
            {
                "entity": proxy,
                "payload": Jsonb(payload),
                "meta": Jsonb({"cef": cef, "proxy": proxy, "interval": "1d"}),
            },
        )
        cur.executemany(SILVER_UPSERT, rows)

    conn.commit()  # commit por CEF: si el proceso muere, lo ingerido queda a salvo
    log.info("%s (proxy %s): %d NAVs upserteados (%s → %s)", cef, proxy, len(rows),
             rows[0]["nav_date"], rows[-1]["nav_date"])
    return len(rows)


def ingest_nav_proxy(
    conn: psycopg.Connection, universe: Universe, tickers: list[str] | None = None
) -> dict[str, int]:
    """Ingesta el NAV-proxy de los CEFs de nav_check (o un subconjunto). Un CEF
    que falle no tumba a los demás — el backfill lo recuperará."""
    result: dict[str, int] = {}
    mapping = universe.nav_check
    for cef in tickers or list(mapping.keys()):
        proxy = mapping.get(cef)
        if not proxy:
            log.warning("%s: sin proxy en nav_check; salto", cef)
            result[cef] = -1
            continue
        try:
            result[cef] = _ingest_one(conn, cef, proxy, universe)
        except Exception:
            conn.rollback()
            log.exception("%s (proxy %s): fallo ingiriendo; continúo con el resto", cef, proxy)
            result[cef] = -1
    return result
