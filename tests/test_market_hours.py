"""Tests del horario de mercado — exactamente los tres casos-trampa que
justificaron usar exchange_calendars en vez de un "if 15:30-22:00 CET"
(decisión 4.1). Sin red: los calendarios de la librería son datos locales.

Convención: los instantes se construyen en UTC. Referencias:
  - En verano NY es UTC-4 (EDT): sesión 13:30-20:00 UTC.
  - En invierno NY es UTC-5 (EST): sesión 14:30-21:00 UTC.
"""

import datetime as dt

import pytest

from sentinel.market_hours import is_market_open, next_close, next_open

UTC = dt.timezone.utc


def _utc(y, m, d, hh, mm):
    return dt.datetime(y, m, d, hh, mm, tzinfo=UTC)


class TestDiaNormal:
    def test_abierto_a_media_sesion(self):
        # Miércoles 2026-07-08, 15:00 UTC = 11:00 NY → plena sesión.
        assert is_market_open(_utc(2026, 7, 8, 15, 0))

    def test_cerrado_de_madrugada(self):
        assert not is_market_open(_utc(2026, 7, 8, 3, 0))

    def test_cerrado_en_fin_de_semana_y_abre_el_lunes(self):
        sabado = _utc(2026, 7, 4, 15, 0)
        assert not is_market_open(sabado)
        # Lunes 2026-07-06, 9:30 NY = 13:30 UTC (julio: EDT).
        assert next_open(sabado) == _utc(2026, 7, 6, 13, 30)


class TestTrampa1DstTransatlantico:
    """EE.UU. adelanta el reloj el 8-mar-2026; Europa no hasta el 29-mar.
    Esas ~3 semanas la bolsa abre a las 14:30 CET, no a las 15:30: la regla
    fija en CET del brief se equivocaría UNA HORA dos veces al año."""

    def test_mercado_abierto_cuando_la_regla_cet_diria_cerrado(self):
        # Martes 2026-03-17, 13:45 UTC = 9:45 NY (EDT ya activo) → ABIERTO.
        # En Madrid son las 14:45 (CET aún sin cambiar): la regla "abre a las
        # 15:30 CET" lo daría por cerrado. exchange_calendars no se equivoca.
        assert is_market_open(_utc(2026, 3, 17, 13, 45))

    def test_apertura_de_esa_semana_en_utc(self):
        madrugada = _utc(2026, 3, 17, 6, 0)
        assert next_open(madrugada) == _utc(2026, 3, 17, 13, 30)


class TestTrampa2FestivoTrasladado:
    """El 4 de julio de 2026 cae en sábado → NYSE cierra el viernes 3.
    Una lista de festivos copiada a mano suele olvidar los traslados."""

    def test_viernes_3_julio_2026_cerrado(self):
        assert not is_market_open(_utc(2026, 7, 3, 15, 0))

    def test_desde_el_jueves_2_la_proxima_apertura_salta_al_lunes_6(self):
        tras_cierre_jueves = _utc(2026, 7, 2, 21, 0)
        assert next_open(tras_cierre_jueves) == _utc(2026, 7, 6, 13, 30)


class TestTrampa3MediaSesion:
    """El día después de Thanksgiving (viernes 27-nov-2026) el NYSE cierra a
    las 13:00 NY. Un poller que asuma 16:00 pediría 3 horas de velas
    inexistentes."""

    def test_abierto_por_la_manana(self):
        # 12:30 NY = 17:30 UTC (noviembre: EST, UTC-5).
        assert is_market_open(_utc(2026, 11, 27, 17, 30))

    def test_cerrado_a_media_tarde_aunque_un_dia_normal_estaria_abierto(self):
        # 14:30 NY: un día normal quedaría hora y media de sesión.
        assert not is_market_open(_utc(2026, 11, 27, 19, 30))

    def test_el_cierre_de_ese_dia_es_a_las_1300_ny(self):
        manana = _utc(2026, 11, 27, 15, 0)
        assert next_close(manana) == _utc(2026, 11, 27, 18, 0)  # 13:00 EST


class TestContratoTzAware:
    def test_datetime_naive_explota_pronto_y_claro(self):
        with pytest.raises(ValueError, match="tz-aware"):
            is_market_open(dt.datetime(2026, 7, 8, 15, 0))  # sin tzinfo
