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
import sys

from . import db
from .config import load_universe
from .ingest.prices import ingest_daily_prices
from .migrations import apply_migrations


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
        errors = [t for t, n in result.items() if n < 0]
        ok = {t: n for t, n in result.items() if n >= 0}
        print(f"Ingesta terminada: {len(ok)} tickers OK, {len(errors)} con error.")
        for ticker, n in ok.items():
            print(f"  {ticker:10s} {n:6d} velas")
        if errors:
            print("  ERRORES:", ", ".join(errors))
        # Exit code != 0 si algo falló: los Jobs de K8s y las alertas de
        # observabilidad se enteran por aquí, no leyendo logs.
        return 1 if errors else 0

    return 2  # unreachable con required=True


if __name__ == "__main__":
    sys.exit(main())
