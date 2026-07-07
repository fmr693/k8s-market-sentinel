"""Horario de mercado (fase 4, decisión 4.1): ¿está abierta la bolsa? ¿cuándo abre?

Envoltorio FINO sobre exchange_calendars con el calendario XNYS (NYSE). La
librería resuelve las tres trampas que hacen inviable el "if 15:30-22:00 CET"
del brief:

  1. DST transatlántico: EE.UU. y Europa cambian de hora en fechas distintas;
     ~3 semanas al año el desfase NY-Madrid es de 5h, no 6h. El horario real
     es 9:30-16:00 America/New_York — CET es solo presentación.
  2. Festivos con reglas móviles ("tercer lunes de enero", traslados si caen
     en fin de semana).
  3. Medias sesiones (cierre 13:00 NY: día tras Thanksgiving, Nochebuena...).

El resto del código importa ESTAS funciones, nunca exchange_calendars: si la
librería molesta algún día, se reimplementa detrás de esta misma interfaz sin
tocar el poller (puerta de escape). Las alertas (fase 6) reutilizarán esto.

Todas las funciones exigen datetimes CON zona horaria: un datetime naive aquí
es siempre un bug del llamante, y mejor que explote pronto y claro.
"""

from __future__ import annotations

import datetime as dt
from functools import lru_cache

import pandas as pd


@lru_cache(maxsize=1)
def _calendar():
    # Import perezoso + caché: cargar el calendario cuesta ~1s la primera vez
    # (construye años de sesiones); las llamadas del bucle son luego O(lookup).
    import exchange_calendars as xcals

    return xcals.get_calendar("XNYS")


def _to_utc(moment: dt.datetime) -> pd.Timestamp:
    if moment.tzinfo is None:
        raise ValueError(
            "market_hours exige datetimes tz-aware (usa dt.timezone.utc); "
            "un naive aquí es un bug del llamante"
        )
    return pd.Timestamp(moment).tz_convert("UTC")


def is_market_open(moment: dt.datetime) -> bool:
    """¿Está el NYSE cruzando órdenes en este instante?"""
    return bool(_calendar().is_open_on_minute(_to_utc(moment)))


def next_open(moment: dt.datetime) -> dt.datetime:
    """Próxima apertura ESTRICTAMENTE posterior a `moment` (UTC).

    Es lo que el poller usa para dormir con el mercado cerrado: la siesta
    exacta hasta la campana, sea mañana o sea el lunes tras un puente.
    """
    return _calendar().next_open(_to_utc(moment)).to_pydatetime()


def next_close(moment: dt.datetime) -> dt.datetime:
    """Próximo cierre posterior a `moment` (UTC). Con mercado abierto, es el
    cierre de HOY — y en las medias sesiones devuelve las 13:00 NY, no las
    16:00: el poller no se queda 3 horas pidiendo velas que no existen."""
    return _calendar().next_close(_to_utc(moment)).to_pydatetime()
