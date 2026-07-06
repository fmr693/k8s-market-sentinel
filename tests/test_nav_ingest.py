"""Tests del parser de CEFConnect (lógica pura, sin red ni BD)."""

import datetime as dt

from sentinel.ingest.nav import history_to_rows


def _payload(*points, nav_ticker="XWDIX"):
    """Imita la respuesta de /api/v3/pricinghistory/{ticker}/1Y."""
    return {
        "Data": {
            "Period": "1Y",
            "NAVTicker": nav_ticker,
            "PriceHistory": [
                {
                    "NAVData": nav,
                    "Data": price,
                    "DiscountData": -5.0,
                    "DataDate": f"{d}T00:00:00",
                    "DataDateJs": d.replace("-", "/"),
                }
                for d, nav, price in points
            ],
        }
    }


class TestHistoryToRows:
    def test_extrae_solo_el_nav(self):
        payload = _payload(
            ("2026-07-01", 14.19, 13.66),
            ("2026-07-02", 14.20, 13.69),
        )
        rows = history_to_rows("WDI", payload)
        assert rows == [
            {"ticker": "WDI", "nav_date": dt.date(2026, 7, 1), "nav": 14.19},
            {"ticker": "WDI", "nav_date": dt.date(2026, 7, 2), "nav": 14.20},
        ]
        # el precio y el descuento de CEFConnect se descartan a propósito:
        # el descuento es métrica NUESTRA (gold), contra nuestros precios

    def test_omite_puntos_sin_nav(self):
        payload = _payload(
            ("2026-07-01", None, 13.66),  # punto solo-precio: fuera
            ("2026-07-02", 14.20, 13.69),
        )
        rows = history_to_rows("WDI", payload)
        assert [r["nav_date"] for r in rows] == [dt.date(2026, 7, 2)]

    def test_payload_vacio_devuelve_lista_vacia(self):
        assert history_to_rows("WDI", {"Data": {"PriceHistory": []}}) == []
        assert history_to_rows("WDI", {}) == []
