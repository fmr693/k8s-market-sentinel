"""Tests de la lógica PURA del ingestor (sin red ni BD).

La estrategia de test del proyecto: las funciones puras (ventana de backfill,
parseo) se testean unitariamente; el upsert idempotente se valida contra un
Postgres real (docker-compose.dev.yml) porque mockear la BD no probaría nada.
"""

import datetime as dt
import math

import pandas as pd
import pytest

from sentinel.ingest.common import compute_fetch_start
from sentinel.ingest.prices import dataframe_to_rows

DEFAULT_START = dt.date(2015, 1, 1)


class TestComputeFetchStart:
    def test_tabla_vacia_arranca_en_el_inicio_configurado(self):
        assert compute_fetch_start(None, DEFAULT_START, 5) == DEFAULT_START

    def test_con_datos_reanuda_con_solape(self):
        last = dt.date(2026, 7, 1)
        assert compute_fetch_start(last, DEFAULT_START, 5) == dt.date(2026, 6, 26)

    def test_solape_cero_repite_el_ultimo_dia(self):
        # overlap_days=0 re-pide desde el último día conocido (que puede
        # haberse guardado parcial): incluso sin solape hay autocorrección.
        last = dt.date(2026, 7, 1)
        assert compute_fetch_start(last, DEFAULT_START, 0) == last


def _df(index_dates, **cols):
    """DataFrame con la forma que devuelve yfinance (índice DatetimeIndex)."""
    return pd.DataFrame(cols, index=pd.DatetimeIndex(pd.to_datetime(index_dates)))


class TestDataframeToRows:
    def test_convierte_velas_normales(self):
        df = _df(
            ["2026-07-01", "2026-07-02"],
            Open=[10.0, 10.5],
            High=[10.6, 10.9],
            Low=[9.9, 10.4],
            Close=[10.5, 10.8],
            Volume=[1000.0, 2000.0],
        )
        rows = dataframe_to_rows("WDI", df)
        assert len(rows) == 2
        assert rows[0] == {
            "ticker": "WDI",
            "trading_date": dt.date(2026, 7, 1),
            "open": 10.0,
            "high": 10.6,
            "low": 9.9,
            "close": 10.5,
            "volume": 1000,
        }
        # tipos nativos de Python, no numpy (psycopg no adapta numpy):
        assert type(rows[0]["close"]) is float
        assert type(rows[0]["volume"]) is int

    def test_descarta_filas_sin_cierre(self):
        df = _df(
            ["2026-07-01", "2026-07-02"],
            Open=[10.0, math.nan],
            High=[10.6, math.nan],
            Low=[9.9, math.nan],
            Close=[10.5, math.nan],  # hueco de Yahoo: fila fuera, no se inventa
            Volume=[1000.0, math.nan],
        )
        rows = dataframe_to_rows("WDI", df)
        assert [r["trading_date"] for r in rows] == [dt.date(2026, 7, 1)]

    def test_nan_parcial_se_vuelve_null_no_cero(self):
        df = _df(
            ["2026-07-01"],
            Open=[math.nan],  # sin apertura pero con cierre: fila válida, open NULL
            High=[10.6],
            Low=[9.9],
            Close=[10.5],
            Volume=[math.nan],
        )
        rows = dataframe_to_rows("WDI", df)
        assert rows[0]["open"] is None
        assert rows[0]["volume"] is None
        assert rows[0]["close"] == 10.5

    def test_indice_con_timezone_se_reduce_a_fecha(self):
        # yfinance a veces devuelve el índice diario tz-aware (tz de la bolsa)
        idx = pd.DatetimeIndex(pd.to_datetime(["2026-07-01"])).tz_localize(
            "America/New_York"
        )
        df = pd.DataFrame(
            {"Open": [10.0], "High": [10.6], "Low": [9.9], "Close": [10.5],
             "Volume": [1000.0]},
            index=idx,
        )
        rows = dataframe_to_rows("WDI", df)
        assert rows[0]["trading_date"] == dt.date(2026, 7, 1)
