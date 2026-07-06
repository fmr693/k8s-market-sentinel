"""Ingestor de NAV diario desde CEFConnect (la pieza frágil, aislada aquí).

Hallazgos del spike (2026-07-06), que explican el diseño:
- GET /api/v3/pricinghistory/{TICKER}/1Y devuelve ~241 puntos DIARIOS con
  fecha explícita. Los demás periodos (1M, 3M, 5Y) vienen muestreados para
  gráficos — inservibles para una serie diaria.
- La API acepta nuestro User-Agent honesto; solo bloquea el de Python por
  defecto (devuelve HTML de challenge, no JSON).
- El payload incluye NAVTicker (p. ej. XWDIX): el ticker con el que yfinance
  cotiza el NAV — queda en bronze para el cross-check (fase de calidad).

Divergencia deliberada del patrón de la casa: aquí NO hay "pedir desde el
último dato" porque la API va por periodos fijos, no por rangos. Siempre se
pide 1Y entero y se upsertea todo: el over-fetch llevado al extremo (~241
filas/ticker/noche, coste trivial). Consecuencia documentada en el brief:
el backfill de NAV llega como mucho 1 año atrás — los huecos anteriores se
toleran, no se inventan.

quality='definitivo': todo lo que da este endpoint son NAVs publicados.
'estimado'/'rancio' los pondrán el poller intradía y el motor de alertas.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import time
import urllib.request

import psycopg
from psycopg.types.json import Jsonb

from ..config import Universe

log = logging.getLogger(__name__)

CEFCONNECT_URL = "https://www.cefconnect.com/api/v3/pricinghistory/{ticker}/1Y"
USER_AGENT = "k8s-market-sentinel/0.1 (personal portfolio project)"
# Pausa entre peticiones: 19 tickers/noche no estresan a nadie, pero no
# somos un scraper agresivo — cortesía con una API pública sin contrato.
SLEEP_BETWEEN_REQUESTS_S = 0.5

BRONZE_INSERT = """
INSERT INTO bronze.raw_fetches (source, entity, payload, meta)
VALUES ('cefconnect', %(entity)s, %(payload)s, %(meta)s)
"""

SILVER_UPSERT = """
INSERT INTO silver.navs (ticker, nav_date, nav, quality, source)
VALUES (%(ticker)s, %(nav_date)s, %(nav)s, 'definitivo', 'cefconnect')
ON CONFLICT (ticker, nav_date) DO UPDATE SET
    nav = EXCLUDED.nav,
    quality = EXCLUDED.quality,
    source = EXCLUDED.source,
    ingested_at = now()
"""


def fetch_history(ticker: str) -> dict:
    req = urllib.request.Request(
        CEFCONNECT_URL.format(ticker=ticker),
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, timeout=60) as resp:
        return json.load(resp)


def history_to_rows(ticker: str, payload: dict) -> list[dict]:
    """JSON de CEFConnect → filas para silver.navs.

    Solo nos llevamos NAVData: el precio y el descuento que da CEFConnect se
    descartan — el precio ya lo tenemos de yfinance y el descuento es NUESTRA
    métrica (se calcula en gold contra nuestros propios datos)."""
    rows: list[dict] = []
    for point in payload.get("Data", {}).get("PriceHistory", []):
        nav = point.get("NAVData")
        if nav is None:
            continue
        rows.append(
            {
                "ticker": ticker,
                # 'DataDate': '2026-07-02T00:00:00' → la parte de fecha
                "nav_date": dt.date.fromisoformat(point["DataDate"][:10]),
                "nav": float(nav),
            }
        )
    return rows


def _ingest_one(conn: psycopg.Connection, ticker: str) -> int:
    payload = fetch_history(ticker)
    rows = history_to_rows(ticker, payload)
    if not rows:
        log.warning("%s: CEFConnect no devolvió historial de NAV", ticker)
        return 0

    nav_ticker = payload.get("Data", {}).get("NAVTicker")
    with conn.cursor() as cur:
        cur.execute(
            BRONZE_INSERT,
            {
                "entity": ticker,
                "payload": Jsonb(payload),
                "meta": Jsonb({"period": "1Y", "nav_ticker": nav_ticker}),
            },
        )
        cur.executemany(SILVER_UPSERT, rows)

    conn.commit()
    log.info("%s: %d NAVs upserteados (%s → %s), nav_ticker=%s", ticker,
             len(rows), rows[0]["nav_date"], rows[-1]["nav_date"], nav_ticker)
    return len(rows)


def ingest_navs(
    conn: psycopg.Connection, universe: Universe, tickers: list[str] | None = None
) -> dict[str, int]:
    """Ingesta el NAV de todos los CEFs del universo (o un subconjunto)."""
    result: dict[str, int] = {}
    targets = tickers or universe.nav_tickers
    for i, ticker in enumerate(targets):
        if i > 0:
            time.sleep(SLEEP_BETWEEN_REQUESTS_S)
        try:
            result[ticker] = _ingest_one(conn, ticker)
        except Exception:
            conn.rollback()
            log.exception("%s: fallo ingiriendo NAV; continúo con el resto", ticker)
            result[ticker] = -1
    return result
