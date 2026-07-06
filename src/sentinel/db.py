"""Acceso a Postgres: una función, un driver (psycopg 3), cero ORM.

Sin ORM a propósito: el proyecto es SQL-céntrico (el medallón ES SQL) y
queremos ver cada query. Un pool de conexiones tampoco aporta: los jobs son
procesos batch de vida corta con una única conexión.
"""

from __future__ import annotations

import psycopg

from .config import get_database_url


def connect() -> psycopg.Connection:
    """Conexión nueva. El llamante gestiona commit/rollback (o usa `with`)."""
    return psycopg.connect(get_database_url())
