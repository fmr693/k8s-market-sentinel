"""Lógica compartida por todos los ingestores.

El patrón de reanudación es idéntico para cualquier fuente (precios, macro,
FX...): mirar el último dato propio y re-pedir desde un poco antes. Vive aquí
una sola vez; cada ingestor decide su `overlap` según cómo revise su fuente.
"""

from __future__ import annotations

import datetime as dt


def compute_fetch_start(
    last_date: dt.date | None, default_start: dt.date, overlap_days: int
) -> dt.date:
    """Desde qué fecha pedir: histórico completo si la tabla está vacía,
    o (último dato − solape) para reanudar autorreparando."""
    if last_date is None:
        return default_start
    return last_date - dt.timedelta(days=overlap_days)
