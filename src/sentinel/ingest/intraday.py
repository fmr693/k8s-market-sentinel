"""Ingesta intradía: velas de 1 minuto, en UNA petición batch (decisión 4.3).

Por qué velas 1m y no "último precio" por ticker:
  - 1 petición para todo el universo, no 24 → presupuesto de rate-limit ×24.
  - El timestamp es DEL MERCADO ("el minuto 17:42"), no "cuando yo pregunté":
    tras un reinicio los ts encajan con los ya guardados y la PK (ticker, ts)
    deduplica de verdad.
  - Tick normal y gap-fill de arranque son ESTA MISMA función con distinta
    ventana (`period="1d"` vs `"7d"`): el camino de recuperación es el camino
    normal — la simetría del crash-only aplicada al dato.

Límite documentado: yfinance solo da 1m hasta ~7 días atrás. Un apagón más
largo deja hueco intradía irreparable (asumido en la duda #4; la serie DIARIA
sí se recupera siempre, así que el z-score EOD nunca tiene huecos).

Bronze, desviación consciente del patrón de los ingestores lentos: solo se
guarda el payload del GAP-FILL (auditoría del momento delicado). Guardar 24
DataFrames casi idénticos cada minuto inflaría bronze (free tier de Neon)
para repetir un 99% de lo ya visto; la PK de silver garantiza la corrección.
"""

from __future__ import annotations

import json
import logging
import math

import psycopg
import yfinance as yf
from psycopg.types.json import Jsonb

from ..config import Universe

log = logging.getLogger(__name__)

BRONZE_INSERT = """
INSERT INTO bronze.raw_fetches (source, entity, payload, meta)
VALUES ('yfinance', %(entity)s, %(payload)s, %(meta)s)
"""

# DO UPDATE y no DO NOTHING por la vela PARCIAL: la del minuto en curso llega
# a medio hacer y el tick siguiente la corrige — mismo motivo que en diario.
INTRADAY_UPSERT = """
INSERT INTO silver.prices_intraday (ticker, ts, price, volume, source)
VALUES (%(ticker)s, %(ts)s, %(price)s, %(volume)s, 'yfinance')
ON CONFLICT (ticker, ts) DO UPDATE SET
    price = EXCLUDED.price,
    volume = EXCLUDED.volume,
    ingested_at = now()
"""


def _to_float(value) -> float | None:
    if value is None:
        return None
    f = float(value)
    return None if math.isnan(f) else f


def dataframe_to_ticks(df, tickers: list[str]) -> list[dict]:
    """DataFrame batch de yf.download → filas para silver.prices_intraday.

    Con varios tickers y group_by="ticker" las columnas son MultiIndex
    (ticker, campo); con uno solo, planas — se tratan ambas formas porque
    yfinance ha cambiado este detalle entre versiones. El precio guardado es
    el cierre de la vela; ts se normaliza SIEMPRE a UTC (el índice llega en
    la zona de la bolsa). Filas sin cierre = hueco de Yahoo: fuera.
    """
    if df.empty:
        return []
    multi = hasattr(df.columns, "levels")  # ¿MultiIndex?
    rows: list[dict] = []
    for ticker in tickers:
        try:
            sub = df[ticker] if multi else df
        except KeyError:  # ticker sin datos en la respuesta batch
            continue
        for idx, r in sub.iterrows():
            price = _to_float(r.get("Close"))
            if price is None:
                continue
            volume = _to_float(r.get("Volume"))
            rows.append(
                {
                    "ticker": ticker,
                    "ts": idx.tz_convert("UTC").to_pydatetime(),
                    "price": price,
                    "volume": int(volume) if volume is not None else None,
                }
            )
    return rows


def ingest_intraday(
    conn: psycopg.Connection,
    universe: Universe,
    period: str = "1d",
    save_bronze: bool = False,
) -> int:
    """Un ciclo completo: UNA petición batch → upsert. Devuelve filas upserteadas.

    El llamante elige la ventana: "1d" (tick del bucle) o "7d" (gap-fill de
    arranque, el máximo de 1m que da yfinance). Un fallo aquí lanza excepción:
    el poller decide si es "log y siguiente tick" o motivo de crash.
    """
    tickers = universe.price_tickers
    df = yf.download(
        tickers=" ".join(tickers),
        period=period,
        interval="1m",
        auto_adjust=False,  # SIN ajustar, como en diario: precio real vs NAV
        actions=False,
        group_by="ticker",
        progress=False,
    )
    rows = dataframe_to_ticks(df, tickers)
    if not rows:
        log.warning("intradía: yfinance no devolvió velas (¿mercado recién abierto?)")
        return 0

    with conn.cursor() as cur:
        if save_bronze:
            payload = json.loads(df.to_json(orient="split", date_format="iso"))
            cur.execute(
                BRONZE_INSERT,
                {
                    "entity": "__intraday_gapfill__",
                    "payload": Jsonb(payload),
                    "meta": Jsonb({"period": period, "interval": "1m"}),
                },
            )
        cur.executemany(INTRADAY_UPSERT, rows)
    conn.commit()
    return len(rows)
