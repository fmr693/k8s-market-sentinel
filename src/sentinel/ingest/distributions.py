"""Ingestor de distribuciones de los CEFs vía yfinance, con backfill idempotente.

Cuarta aparición del patrón de la casa (último dato → pedir con solape →
bronze → upsert), con dos matices:

- yfinance entrega las distribuciones como columna `Dividends` del mismo
  `history()` de siempre (pidiendo actions=True): días normales valen 0,
  los ex-date llevan el importe. Filtramos > 0.
- Solape de 30 días (el macro, no el de precios): una distribución no se
  "revisa" como una vela, pero los anuncios llegan con días de retraso al
  feed y un mes de margen los absorbe todos.

Solo los CEFs (universe.nav_tickers): en un CEF la distribución es la tesis;
los dividendos de las stocks del watchlist serían otra historia (y otra
decisión) — puerta de escape: ampliar el set en config si algún día toca.
"""

from __future__ import annotations

import datetime as dt
import logging

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
INSERT INTO silver.distributions (ticker, ex_date, amount, source)
VALUES (%(ticker)s, %(ex_date)s, %(amount)s, 'yfinance')
ON CONFLICT (ticker, ex_date) DO UPDATE SET
    amount = EXCLUDED.amount,
    ingested_at = now()
"""


def dividends_to_rows(ticker: str, df) -> list[dict]:
    """DataFrame de yfinance (con columna Dividends) → filas para silver.
    Los días sin distribución valen 0: fuera. El índice es el ex-date."""
    if "Dividends" not in df.columns:
        return []
    rows: list[dict] = []
    for idx, value in df["Dividends"].items():
        amount = float(value)
        if amount <= 0:
            continue
        rows.append({"ticker": ticker, "ex_date": idx.date(), "amount": amount})
    return rows


def _ingest_one(conn: psycopg.Connection, ticker: str, universe: Universe) -> int:
    with conn.cursor() as cur:
        cur.execute(
            "SELECT max(ex_date) FROM silver.distributions WHERE ticker = %s",
            (ticker,),
        )
        last_date = cur.fetchone()[0]

    start = compute_fetch_start(
        last_date, universe.price_history_start, universe.macro_overlap_days
    )
    log.info("%s: última distribución=%s → pidiendo desde %s", ticker, last_date, start)

    df = yf.Ticker(ticker).history(
        start=start.isoformat(), interval="1d", auto_adjust=False, actions=True
    )
    rows = dividends_to_rows(ticker, df)
    if not rows:
        log.info("%s: sin distribuciones nuevas en la ventana", ticker)
        return 0

    # Bronze pragmático (desviación como la del intradía, documentada): se
    # guarda la serie de dividendos de la ventana, no el OHLC completo — ese
    # ya entra a bronze por el ingestor de precios y duplicarlo solo engorda.
    payload = {r["ex_date"].isoformat(): r["amount"] for r in rows}
    with conn.cursor() as cur:
        cur.execute(
            BRONZE_INSERT,
            {
                "entity": ticker,
                "payload": Jsonb(payload),
                "meta": Jsonb({"kind": "distributions", "start": start.isoformat()}),
            },
        )
        cur.executemany(SILVER_UPSERT, rows)

    conn.commit()
    log.info("%s: %d distribuciones upserteadas (%s → %s)", ticker, len(rows),
             rows[0]["ex_date"], rows[-1]["ex_date"])
    return len(rows)


def ingest_distributions(
    conn: psycopg.Connection, universe: Universe, tickers: list[str] | None = None
) -> dict[str, int]:
    """Ingesta las distribuciones de todos los CEFs (o un subconjunto)."""
    result: dict[str, int] = {}
    for ticker in tickers or universe.nav_tickers:
        try:
            result[ticker] = _ingest_one(conn, ticker, universe)
        except Exception:
            conn.rollback()
            log.exception("%s: fallo ingiriendo distribuciones; continúo", ticker)
            result[ticker] = -1
    return result
