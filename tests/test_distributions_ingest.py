"""Tests del parser de distribuciones (lógica pura, sin red ni BD)."""

import datetime as dt

import pandas as pd

from sentinel.ingest.distributions import dividends_to_rows


def _df(index_dates, dividends):
    """DataFrame como el de yfinance con actions=True (columna Dividends)."""
    return pd.DataFrame(
        {"Close": [10.0] * len(dividends), "Dividends": dividends},
        index=pd.DatetimeIndex(pd.to_datetime(index_dates)),
    )


class TestDividendsToRows:
    def test_filtra_dias_sin_distribucion(self):
        # días normales valen 0: solo sobreviven los ex-date
        df = _df(
            ["2026-06-10", "2026-06-11", "2026-06-12"],
            [0.0, 0.11, 0.0],
        )
        rows = dividends_to_rows("WDI", df)
        assert rows == [
            {"ticker": "WDI", "ex_date": dt.date(2026, 6, 11), "amount": 0.11}
        ]
        assert type(rows[0]["amount"]) is float  # nativo, no numpy

    def test_varias_distribuciones_en_la_ventana(self):
        df = _df(
            ["2026-05-12", "2026-06-11"],
            [0.11, 0.11],
        )
        rows = dividends_to_rows("WDI", df)
        assert [r["ex_date"] for r in rows] == [
            dt.date(2026, 5, 12),
            dt.date(2026, 6, 11),
        ]

    def test_sin_columna_dividends_devuelve_vacio(self):
        # un fetch con actions=False no trae la columna: no explotar
        df = pd.DataFrame(
            {"Close": [10.0]},
            index=pd.DatetimeIndex(pd.to_datetime(["2026-06-10"])),
        )
        assert dividends_to_rows("WDI", df) == []
