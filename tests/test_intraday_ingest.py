"""Tests de la lógica pura del carril intradía (fase 4): el parseo del
DataFrame batch de yf.download y la aritmética de siestas del poller.
El bucle en sí no se testea: es fino a propósito (señales y waits), y su
lógica con sustancia vive en estas funciones puras.
"""

import datetime as dt

import pandas as pd

from sentinel.ingest.intraday import dataframe_to_ticks
from sentinel.poller import nap_seconds

UTC = dt.timezone.utc


def _batch_df(tickers, minutes, tz="America/New_York", **values):
    """DataFrame con la forma batch de yf.download(group_by="ticker"):
    columnas MultiIndex (ticker, campo), índice tz-aware de la bolsa."""
    idx = pd.DatetimeIndex(pd.to_datetime(minutes)).tz_localize(tz)
    cols = pd.MultiIndex.from_product([tickers, ["Close", "Volume"]])
    df = pd.DataFrame(index=idx, columns=cols, dtype=float)
    for ticker, series in values.items():
        df[(ticker, "Close")] = series["close"]
        df[(ticker, "Volume")] = series["volume"]
    return df


class TestDataframeToTicks:
    def test_multiindex_batch_y_conversion_a_utc(self):
        # 09:30 NY del 8-jul-2026 (EDT, UTC-4) → 13:30 UTC: el ts guardado es
        # DEL MERCADO y en UTC, la clave de que la PK deduplique tras reinicios.
        df = _batch_df(
            ["WDI", "KIO"],
            ["2026-07-08 09:30", "2026-07-08 09:31"],
            WDI={"close": [12.5, 12.6], "volume": [100.0, 200.0]},
            KIO={"close": [14.1, 14.2], "volume": [300.0, 400.0]},
        )
        rows = dataframe_to_ticks(df, ["WDI", "KIO"])
        assert len(rows) == 4
        first_wdi = rows[0]
        assert first_wdi["ticker"] == "WDI"
        assert first_wdi["ts"] == dt.datetime(2026, 7, 8, 13, 30, tzinfo=UTC)
        assert first_wdi["price"] == 12.5
        assert type(first_wdi["volume"]) is int  # nativo, no numpy

    def test_velas_sin_cierre_se_descartan_por_ticker(self):
        # KIO no operó el segundo minuto (NaN): su fila cae, la de WDI no.
        df = _batch_df(
            ["WDI", "KIO"],
            ["2026-07-08 09:30", "2026-07-08 09:31"],
            WDI={"close": [12.5, 12.6], "volume": [100.0, 200.0]},
            KIO={"close": [14.1, float("nan")], "volume": [300.0, float("nan")]},
        )
        rows = dataframe_to_ticks(df, ["WDI", "KIO"])
        assert len(rows) == 3
        assert [r["ticker"] for r in rows] == ["WDI", "WDI", "KIO"]

    def test_ticker_pedido_pero_ausente_en_la_respuesta_no_rompe(self):
        df = _batch_df(
            ["WDI"],
            ["2026-07-08 09:30"],
            WDI={"close": [12.5], "volume": [100.0]},
        )
        rows = dataframe_to_ticks(df, ["WDI", "NOEXISTE"])
        assert [r["ticker"] for r in rows] == ["WDI"]

    def test_columnas_planas_de_un_solo_ticker(self):
        # yfinance devuelve columnas PLANAS con un único ticker (ha cambiado
        # entre versiones): el parser trata ambas formas.
        idx = pd.DatetimeIndex(pd.to_datetime(["2026-07-08 09:30"])).tz_localize(
            "America/New_York"
        )
        df = pd.DataFrame({"Close": [12.5], "Volume": [100.0]}, index=idx)
        rows = dataframe_to_ticks(df, ["WDI"])
        assert rows[0]["price"] == 12.5

    def test_df_vacio_devuelve_lista_vacia(self):
        assert dataframe_to_ticks(pd.DataFrame(), ["WDI"]) == []


class TestNapSeconds:
    def test_lejos_de_la_apertura_se_aplica_el_tope(self):
        # Viernes noche: faltan ~63h para el lunes, pero la siesta es ≤15 min
        # (el latido debe seguir sonando para la liveness probe).
        now = dt.datetime(2026, 7, 3, 22, 0, tzinfo=UTC)
        opens = dt.datetime(2026, 7, 6, 13, 30, tzinfo=UTC)
        assert nap_seconds(now, opens) == 15 * 60

    def test_cerca_de_la_apertura_duerme_lo_justo(self):
        now = dt.datetime(2026, 7, 6, 13, 25, tzinfo=UTC)
        opens = dt.datetime(2026, 7, 6, 13, 30, tzinfo=UTC)
        assert nap_seconds(now, opens) == 5 * 60

    def test_apertura_ya_pasada_no_devuelve_negativo(self):
        now = dt.datetime(2026, 7, 6, 13, 35, tzinfo=UTC)
        opens = dt.datetime(2026, 7, 6, 13, 30, tzinfo=UTC)
        assert nap_seconds(now, opens) == 0.0
