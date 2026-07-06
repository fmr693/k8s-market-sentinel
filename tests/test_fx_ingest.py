"""Tests del parser de frankfurter.app (lógica pura, sin red ni BD)."""

import datetime as dt

from sentinel.ingest.fx import rates_to_rows, series_id_for


class TestSeriesIdFor:
    def test_convencion_de_nombre(self):
        assert series_id_for("EUR/USD") == "EURUSD_ECB"


class TestRatesToRows:
    def test_convierte_y_ordena_por_fecha(self):
        # frankfurter devuelve un dict {fecha: {divisa: tipo}} sin orden garantizado
        payload = {
            "base": "EUR",
            "rates": {
                "2026-07-03": {"USD": 1.1750},
                "2026-07-01": {"USD": 1.1732},
                "2026-07-02": {"USD": 1.1741},
            },
        }
        rows = rates_to_rows("EUR/USD", payload)
        assert [r["obs_date"] for r in rows] == [
            dt.date(2026, 7, 1),
            dt.date(2026, 7, 2),
            dt.date(2026, 7, 3),
        ]
        assert rows[0] == {
            "series_id": "EURUSD_ECB",
            "obs_date": dt.date(2026, 7, 1),
            "value": 1.1732,
        }
        assert type(rows[0]["value"]) is float

    def test_payload_vacio_devuelve_lista_vacia(self):
        assert rates_to_rows("EUR/USD", {"rates": {}}) == []
        assert rates_to_rows("EUR/USD", {}) == []
