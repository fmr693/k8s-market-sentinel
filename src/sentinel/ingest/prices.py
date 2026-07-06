"""Ingestor de velas diarias vía yfinance, con backfill idempotente.

El patrón (decisión #3 del brief), pieza a pieza:

1. "¿Cuál es mi último dato?" → MAX(trading_date) en silver para ese ticker.
2. Pedir desde (último − overlap_days) hasta hoy. El solape re-pide las
   últimas velas a propósito: la más reciente pudo guardarse a media sesión
   (parcial) y Yahoo revisa datos a posteriori. Over-fetch + dedup.
3. Guardar el batch crudo en bronze (append-only: auditoría y re-proceso).
4. Upsert en silver por (ticker, trading_date): re-ejecutar nunca duplica,
   solo corrige. Tras un apagón de días, el paso 1 hace que el sistema se
   autorrepare solo, sin intervención.

Nota de dominio: pedimos precios SIN ajustar (auto_adjust=False). El
descuento compara el precio que de verdad cruza en pantalla con el NAV
publicado; los precios ajustados por dividendos reescriben la historia y
distorsionarían la serie de descuentos (los CEFs de crédito reparten mucho).
"""

from __future__ import annotations

import datetime as dt
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

# ON CONFLICT contra la PK (ticker, trading_date): la re-ejecución es segura
# por diseño. DO UPDATE (y no DO NOTHING) para que una vela parcial guardada
# ayer a media sesión quede corregida hoy con el cierre real.
SILVER_UPSERT = """
INSERT INTO silver.prices_daily
    (ticker, trading_date, open, high, low, close, volume, source)
VALUES
    (%(ticker)s, %(trading_date)s, %(open)s, %(high)s, %(low)s, %(close)s,
     %(volume)s, 'yfinance')
ON CONFLICT (ticker, trading_date) DO UPDATE SET
    open = EXCLUDED.open,
    high = EXCLUDED.high,
    low = EXCLUDED.low,
    close = EXCLUDED.close,
    volume = EXCLUDED.volume,
    ingested_at = now()
"""


def _to_float(value) -> float | None:
    """numpy.float64/NaN → float de Python o None (psycopg no adapta tipos numpy)."""
    if value is None:
        return None
    f = float(value)
    return None if math.isnan(f) else f


def dataframe_to_rows(ticker: str, df) -> list[dict]:
    """DataFrame de yfinance → filas para silver. Descarta filas sin cierre
    (huecos de Yahoo) en vez de inventarles valor."""
    rows: list[dict] = []
    for idx, r in df.iterrows():
        close = _to_float(r.get("Close"))
        if close is None:
            continue
        volume = _to_float(r.get("Volume"))
        rows.append(
            {
                "ticker": ticker,
                "trading_date": idx.date(),  # el índice diario de yfinance es un Timestamp
                "open": _to_float(r.get("Open")),
                "high": _to_float(r.get("High")),
                "low": _to_float(r.get("Low")),
                "close": close,
                "volume": int(volume) if volume is not None else None,
            }
        )
    return rows


def _ingest_one(conn: psycopg.Connection, ticker: str, universe: Universe) -> int:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT max(trading_date) FROM silver.prices_daily WHERE ticker = %s",
            (ticker,),
        )
        last_date = cur.fetchone()[0]

    start = compute_fetch_start(last_date, universe.price_history_start, universe.overlap_days)
    log.info("%s: último dato=%s → pidiendo desde %s", ticker, last_date, start)

    df = yf.Ticker(ticker).history(
        start=start.isoformat(), interval="1d", auto_adjust=False, actions=False
    )
    if df.empty:
        log.warning("%s: yfinance no devolvió datos (¿ticker correcto?)", ticker)
        return 0

    rows = dataframe_to_rows(ticker, df)
    payload = json.loads(df.to_json(orient="split", date_format="iso"))

    with conn.cursor() as cur:
        cur.execute(
            BRONZE_INSERT,
            {
                "entity": ticker,
                "payload": Jsonb(payload),
                "meta": Jsonb({"start": start.isoformat(), "interval": "1d"}),
            },
        )
        cur.executemany(SILVER_UPSERT, rows)

    # Commit POR TICKER: si el proceso muere a mitad del universo, lo ya
    # ingerido queda a salvo y la siguiente ejecución retoma el resto sola.
    conn.commit()
    log.info("%s: %d velas upserteadas (%s → %s)", ticker, len(rows),
             rows[0]["trading_date"], rows[-1]["trading_date"])
    return len(rows)


def ingest_daily_prices(
    conn: psycopg.Connection, universe: Universe, tickers: list[str] | None = None
) -> dict[str, int]:
    """Ingesta el universo entero (o un subconjunto). Un ticker que falle no
    tumba a los demás: se registra y se sigue — el backfill lo recuperará."""
    result: dict[str, int] = {}
    for ticker in tickers or universe.price_tickers:
        try:
            result[ticker] = _ingest_one(conn, ticker, universe)
        except Exception:
            conn.rollback()
            log.exception("%s: fallo ingiriendo; continúo con el resto", ticker)
            result[ticker] = -1  # marca de error para el resumen
    return result
