"""Configuración: variables de entorno (.env en local, Secret en K8s) y universo de tickers.

Deliberadamente sin frameworks (pydantic-settings, dynaconf...): dos funciones
y un dataclass que se leen en un minuto. Si la config crece, se migra.
"""

from __future__ import annotations

import datetime as dt
import os
from dataclasses import dataclass
from pathlib import Path

import yaml
from dotenv import load_dotenv

# Carga .env si existe. No pisa variables ya definidas en el entorno,
# así que en K8s (donde no hay .env) manda siempre el Secret.
load_dotenv()

# Raíz del repo (src/sentinel/config.py -> dos niveles arriba).
# En la imagen Docker (fase 2) config/ y db/ se copiarán a esta misma ruta relativa.
REPO_ROOT = Path(__file__).resolve().parents[2]
DEFAULT_TICKERS_FILE = REPO_ROOT / "config" / "tickers.yaml"


def get_database_url() -> str:
    url = os.environ.get("DATABASE_URL")
    if not url:
        raise RuntimeError(
            "DATABASE_URL no está definida. Copia .env.example a .env y rellénala."
        )
    return url


def get_fred_api_key() -> str:
    key = os.environ.get("FRED_API_KEY")
    if not key:
        raise RuntimeError(
            "FRED_API_KEY no está definida. Key gratuita en "
            "https://fredaccount.stlouisfed.org/apikeys"
        )
    return key


@dataclass(frozen=True)
class Universe:
    """El universo de series a vigilar, cargado de config/tickers.yaml (o del ConfigMap)."""

    cef_credit: list[str]
    cef_contrast: list[str]
    benchmarks: list[str]
    fred_series: list[str]
    fx_pairs: list[str]  # formato "EUR/USD"
    price_history_start: dt.date
    overlap_days: int
    macro_overlap_days: int

    @property
    def price_tickers(self) -> list[str]:
        """Todo lo que se ingesta vía yfinance (CEFs + benchmarks)."""
        return self.cef_credit + self.cef_contrast + self.benchmarks

    @property
    def nav_tickers(self) -> list[str]:
        """Solo los CEFs: los benchmarks (índices, FX, oro) no tienen NAV."""
        return self.cef_credit + self.cef_contrast


def load_universe(path: Path | None = None) -> Universe:
    # TICKERS_FILE permite que K8s apunte al fichero montado del ConfigMap.
    path = path or Path(os.environ.get("TICKERS_FILE", DEFAULT_TICKERS_FILE))
    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f)
    defaults = raw["defaults"]
    return Universe(
        cef_credit=raw["cef_credit"],
        cef_contrast=raw["cef_contrast"],
        benchmarks=raw["benchmarks"],
        fred_series=raw["fred_series"],
        fx_pairs=raw["fx_pairs"],
        price_history_start=dt.date.fromisoformat(defaults["price_history_start"]),
        overlap_days=int(defaults["overlap_days"]),
        macro_overlap_days=int(defaults["macro_overlap_days"]),
    )
