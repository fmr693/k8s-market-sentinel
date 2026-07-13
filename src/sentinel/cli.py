"""Dispatcher CLI (decisión #5: una imagen, varias caras).

En K8s cada carga elegirá su cara vía `args:` del contenedor:
    CronJob nocturno  → ["ingest-prices"]
    Job de migración  → ["migrate"]
    (futuro)          → ["poller"], ["nav"], ["alerts"]

argparse y no typer/click: es un dispatcher de 3 subcomandos, la stdlib sobra.
"""

from __future__ import annotations

import argparse
import logging
import signal
import sys
import threading

from prometheus_client import start_http_server

from . import db, poller
from .config import load_universe
from .ingest.distributions import ingest_distributions
from .ingest.fx import ingest_fx_rates
from .ingest.macro import ingest_macro_series
from .ingest.nav import ingest_navs
from .ingest.prices import ingest_daily_prices
from .migrations import apply_migrations


def _print_ingest_summary(result: dict[str, int], unit: str) -> int:
    """Resumen común de ingesta + exit code (≠0 si algo falló, para los Jobs de K8s)."""
    errors = [k for k, n in result.items() if n < 0]
    ok = {k: n for k, n in result.items() if n >= 0}
    print(f"Ingesta terminada: {len(ok)} OK, {len(errors)} con error.")
    for key, n in ok.items():
        print(f"  {key:14s} {n:6d} {unit}")
    if errors:
        print("  ERRORES:", ", ".join(errors))
    return 1 if errors else 0


def main(argv: list[str] | None = None) -> int:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    parser = argparse.ArgumentParser(prog="sentinel", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("migrate", help="Aplica las migraciones SQL pendientes")

    p_prices = sub.add_parser(
        "ingest-prices",
        help="Backfill idempotente de velas diarias (yfinance) para el universo",
    )
    p_prices.add_argument(
        "--tickers",
        nargs="*",
        help="Subconjunto de tickers (por defecto, todo el universo de config)",
    )

    p_macro = sub.add_parser(
        "ingest-macro",
        help="Backfill idempotente de series macro (FRED) de la config",
    )
    p_macro.add_argument(
        "--series",
        nargs="*",
        help="Subconjunto de series FRED (por defecto, todas las de config)",
    )

    p_fx = sub.add_parser(
        "ingest-fx",
        help="Backfill idempotente de fixings oficiales del BCE (frankfurter.app)",
    )
    p_fx.add_argument(
        "--pairs",
        nargs="*",
        help='Subconjunto de pares "EUR/USD" (por defecto, todos los de config)',
    )

    sub.add_parser(
        "poller",
        help="Poller intradía: proceso vivo con horario de mercado (fase 4)",
    )

    p_dist = sub.add_parser(
        "ingest-distributions",
        help="Distribuciones de los CEFs (yfinance, backfill idempotente)",
    )
    p_dist.add_argument(
        "--tickers",
        nargs="*",
        help="Subconjunto de CEFs (por defecto, todos los del universo)",
    )

    p_nav = sub.add_parser(
        "ingest-nav",
        help="NAV diario de los CEFs desde CEFConnect (último año, upsert completo)",
    )
    p_nav.add_argument(
        "--tickers",
        nargs="*",
        help="Subconjunto de CEFs (por defecto, todos los del universo)",
    )

    args = parser.parse_args(argv)

    if args.command == "migrate":
        with db.connect() as conn:
            applied = apply_migrations(conn)
        if applied:
            print("Migraciones aplicadas:", ", ".join(applied))
        else:
            print("Nada pendiente: el esquema ya está al día.")
        return 0

    if args.command == "ingest-prices":
        universe = load_universe()
        with db.connect() as conn:
            result = ingest_daily_prices(conn, universe, args.tickers)
        return _print_ingest_summary(result, "velas")

    if args.command == "ingest-macro":
        universe = load_universe()
        with db.connect() as conn:
            result = ingest_macro_series(conn, universe, args.series)
        return _print_ingest_summary(result, "observaciones")

    if args.command == "ingest-fx":
        universe = load_universe()
        with db.connect() as conn:
            result = ingest_fx_rates(conn, universe, args.pairs)
        return _print_ingest_summary(result, "fixings")

    if args.command == "poller":
        # El Event + el manejador de señales viven AQUÍ (capa de proceso);
        # poller.run() solo conoce el Event — así el bucle es testeable sin
        # tocar señales. SIGTERM = K8s pide morir; SIGINT = Ctrl+C en local.
        stop = threading.Event()
        for sig in (signal.SIGTERM, signal.SIGINT):
            signal.signal(sig, lambda *_: stop.set())
        # /metrics para Prometheus (fase 9): servidor HTTP en un hilo daemon.
        # Vive AQUÍ (capa de proceso) y no en run(): abrir el socket es un
        # efecto de proceso, y así el bucle se testea sin puertos. El hilo
        # daemon muere con el proceso; no interfiere con la salida por SIGTERM.
        start_http_server(poller.METRICS_PORT)
        poller.run(stop, load_universe())
        return 0

    if args.command == "ingest-distributions":
        universe = load_universe()
        with db.connect() as conn:
            result = ingest_distributions(conn, universe, args.tickers)
        return _print_ingest_summary(result, "distribuciones")

    if args.command == "ingest-nav":
        universe = load_universe()
        with db.connect() as conn:
            result = ingest_navs(conn, universe, args.tickers)
        return _print_ingest_summary(result, "NAVs")

    return 2  # unreachable con required=True


if __name__ == "__main__":
    sys.exit(main())
