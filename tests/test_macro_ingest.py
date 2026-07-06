"""Tests del parser de observaciones FRED (lógica pura, sin red ni BD)."""

import datetime as dt

from sentinel.ingest.macro import observations_to_rows


def _payload(*obs):
    """Imita la respuesta JSON de /fred/series/observations."""
    return {
        "observations": [
            {"date": d, "value": v, "realtime_start": "x", "realtime_end": "x"}
            for d, v in obs
        ]
    }


class TestObservationsToRows:
    def test_convierte_observaciones_normales(self):
        payload = _payload(("2026-07-01", "2.85"), ("2026-07-02", "2.90"))
        rows = observations_to_rows("BAMLH0A0HYM2", payload)
        assert rows == [
            {"series_id": "BAMLH0A0HYM2", "obs_date": dt.date(2026, 7, 1), "value": 2.85},
            {"series_id": "BAMLH0A0HYM2", "obs_date": dt.date(2026, 7, 2), "value": 2.90},
        ]
        assert type(rows[0]["value"]) is float

    def test_omite_huecos_de_fred(self):
        # FRED marca los días sin dato (festivos) con value=".": fila fuera.
        payload = _payload(("2026-07-03", "."), ("2026-07-06", "2.95"))
        rows = observations_to_rows("DGS10", payload)
        assert [r["obs_date"] for r in rows] == [dt.date(2026, 7, 6)]

    def test_payload_vacio_devuelve_lista_vacia(self):
        assert observations_to_rows("GDP", {"observations": []}) == []
        assert observations_to_rows("GDP", {}) == []
