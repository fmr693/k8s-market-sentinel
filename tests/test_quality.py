"""Tests del framework de calidad de dato (fase 10): lógica pura, sin BD.

Cubre el traductor umbrales→status (el corazón del framework) y que el loader
parsea los checks y degrada con elegancia los umbrales opcionales que falten.
"""

import yaml

from sentinel.config import QualityCheck, load_quality_checks
from sentinel.quality import evaluate


def _check(**kw) -> QualityCheck:
    base = dict(name="c", description="", query="SELECT 1", unit="")
    base.update(kw)
    return QualityCheck(**base)


class TestEvaluate:
    def test_ok_dentro_de_umbrales(self):
        assert evaluate(10, _check(warn_above=30, fail_above=75)) == "ok"

    def test_warn_por_encima(self):
        assert evaluate(40, _check(warn_above=30, fail_above=75)) == "warn"

    def test_fail_por_encima_manda_sobre_warn(self):
        # 80 supera warn Y fail: gana fail (precedencia comprobada aquí)
        assert evaluate(80, _check(warn_above=30, fail_above=75)) == "fail"

    def test_direccion_below(self):
        # cobertura: menos = peor (below)
        assert evaluate(10, _check(warn_below=5, fail_below=1)) == "ok"
        assert evaluate(2, _check(warn_below=5, fail_below=1)) == "warn"
        assert evaluate(0, _check(warn_below=5, fail_below=1)) == "fail"

    def test_none_es_error(self):
        # el check no pudo medir (p.ej. tabla vacía → max() NULL)
        assert evaluate(None, _check(warn_above=30)) == "error"

    def test_check_sin_umbrales_siempre_ok(self):
        assert evaluate(999, _check()) == "ok"

    def test_limite_exacto_no_dispara(self):
        # comparación estricta (>): el valor EN el umbral no dispara
        assert evaluate(30, _check(warn_above=30)) == "ok"


class TestLoadQualityChecks:
    def test_parsea_y_degrada_umbrales_opcionales(self, tmp_path):
        data = {
            "checks": [
                {"name": "fresh", "description": "d", "query": "SELECT 1",
                 "unit": "hours", "warn_above": 30, "fail_above": 75},
                {"name": "min", "query": "SELECT 2"},  # sin description/unit/umbrales
            ]
        }
        p = tmp_path / "quality_checks.yaml"
        p.write_text(yaml.safe_dump(data), encoding="utf-8")
        checks = load_quality_checks(p)

        assert [c.name for c in checks] == ["fresh", "min"]
        assert checks[0].warn_above == 30 and checks[0].fail_above == 75
        # los umbrales ausentes quedan None; description/unit vacíos por defecto
        assert checks[1].warn_above is None and checks[1].fail_below is None
        assert checks[1].description == "" and checks[1].unit == ""
