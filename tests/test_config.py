"""Tests de la carga del universo (lógica pura, sin red ni BD).

Cubre el contrato config-driven: qué se ingesta en cada carril y que las
claves opcionales del YAML degradan con elegancia cuando faltan (compatibilidad
hacia atrás con ConfigMaps viejos).
"""

import datetime as dt

import yaml

from sentinel.config import Universe, load_universe

BASE_YAML = {
    "cef_credit": ["WDI", "KIO"],
    "cef_contrast": ["BST"],
    "benchmarks": ["^GSPC", "^W5000"],
    "stocks": ["NVDA"],
    "fred_series": ["GDP"],
    "fx_pairs": ["EUR/USD"],
    "defaults": {
        "price_history_start": "2015-01-01",
        "overlap_days": 5,
        "macro_overlap_days": 30,
        "poll_interval_seconds": 75,
        "intraday_exclude": ["^W5000"],
    },
}


def _write(tmp_path, data):
    p = tmp_path / "tickers.yaml"
    p.write_text(yaml.safe_dump(data), encoding="utf-8")
    return p


class TestPriceTickersIntraday:
    def test_excluye_lo_configurado_del_carril_intradia(self):
        # construir el Universe directamente prueba la property sin tocar disco
        u = Universe(
            cef_credit=["WDI"], cef_contrast=["BST"], benchmarks=["^GSPC", "^W5000"],
            stocks=["NVDA"], fred_series=["GDP"], fx_pairs=["EUR/USD"],
            intraday_exclude=["^W5000"],
            price_history_start=dt.date(2015, 1, 1), overlap_days=5,
            macro_overlap_days=30, poll_interval_seconds=75,
        )
        assert "^W5000" in u.price_tickers  # el diario SÍ lo quiere
        assert "^W5000" not in u.price_tickers_intraday  # el intradía NO
        # el resto del universo intradía queda intacto y en orden
        assert u.price_tickers_intraday == ["WDI", "BST", "^GSPC", "NVDA"]

    def test_sin_exclusiones_los_dos_carriles_coinciden(self):
        u = Universe(
            cef_credit=["WDI"], cef_contrast=[], benchmarks=["^GSPC"],
            stocks=[], fred_series=["GDP"], fx_pairs=["EUR/USD"],
            intraday_exclude=[],
            price_history_start=dt.date(2015, 1, 1), overlap_days=5,
            macro_overlap_days=30, poll_interval_seconds=75,
        )
        assert u.price_tickers_intraday == u.price_tickers


class TestLoadUniverse:
    def test_carga_intraday_exclude_del_yaml(self, tmp_path):
        u = load_universe(_write(tmp_path, BASE_YAML))
        assert u.intraday_exclude == ["^W5000"]
        assert "^W5000" not in u.price_tickers_intraday

    def test_sin_la_clave_no_excluye_nada(self, tmp_path):
        # ConfigMap viejo sin intraday_exclude: no debe romper (como stocks)
        data = {**BASE_YAML, "defaults": {k: v for k, v in BASE_YAML["defaults"].items()
                                          if k != "intraday_exclude"}}
        u = load_universe(_write(tmp_path, data))
        assert u.intraday_exclude == []
        assert u.price_tickers_intraday == u.price_tickers


class TestNavCheck:
    def test_carga_nav_check_y_proxies(self, tmp_path):
        data = {**BASE_YAML, "nav_check": {"WDI": "XWDIX", "FSCO": "XFSCX", "ECAT": "ECAT"}}
        u = load_universe(_write(tmp_path, data))
        assert u.nav_check["WDI"] == "XWDIX"
        # los proxies para el ingestor: ordenados y sin duplicados
        assert u.nav_proxy_tickers == ["ECAT", "XFSCX", "XWDIX"]

    def test_sin_la_clave_nav_check_vacio(self, tmp_path):
        # ConfigMap viejo sin nav_check: degrada a {} (como stocks/intraday_exclude)
        u = load_universe(_write(tmp_path, BASE_YAML))  # BASE_YAML no tiene nav_check
        assert u.nav_check == {}
        assert u.nav_proxy_tickers == []


class TestMacroHistoryStart:
    """El backfill macro arranca antes que el de precios: una serie macro sirve
    para dar CONTEXTO histórico y ahí cada década cuenta (con 11 años no se ve
    2008). Clave opcional: sin ella cae en price_history_start."""

    def test_usa_su_clave_propia_si_existe(self, tmp_path):
        data = {**BASE_YAML, "defaults": {**BASE_YAML["defaults"],
                                          "macro_history_start": "1986-01-01"}}
        u = load_universe(_write(tmp_path, data))
        assert u.macro_history_start == dt.date(1986, 1, 1)
        assert u.macro_start == dt.date(1986, 1, 1)
        # y NO arrastra el de precios, que sigue siendo el suyo
        assert u.macro_start != u.price_history_start

    def test_sin_la_clave_cae_en_el_de_precios(self, tmp_path):
        # ConfigMap viejo: el comportamiento no cambia (compat hacia atrás)
        data = {**BASE_YAML, "defaults": {k: v for k, v in BASE_YAML["defaults"].items()
                                          if k != "macro_history_start"}}
        u = load_universe(_write(tmp_path, data))
        assert u.macro_history_start is None
        assert u.macro_start == u.price_history_start
