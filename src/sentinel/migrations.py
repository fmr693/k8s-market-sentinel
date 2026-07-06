"""Runner de migraciones minimalista.

¿Por qué no Alembic? Su fuerte es autogenerar diffs desde un ORM que no
usamos. Para este alcance, ficheros SQL numerados + una tabla de control son
100% transparentes: se ve exactamente qué SQL corre y en qué orden, y el
mismo `sentinel migrate` servirá como initContainer/Job en K8s.

Reglas del juego:
- Los ficheros aplicados quedan registrados en public.schema_migrations.
- Un fichero aplicado NUNCA se edita: los cambios de esquema son una
  migración nueva (si no, BD nuevas y viejas divergen silenciosamente).
- Cada migración corre dentro de la transacción; si falla, rollback completo
  y la tabla de control no la registra.
"""

from __future__ import annotations

import logging
from pathlib import Path

import psycopg

from .config import REPO_ROOT

log = logging.getLogger(__name__)

MIGRATIONS_DIR = REPO_ROOT / "db" / "migrations"


def apply_migrations(conn: psycopg.Connection, migrations_dir: Path = MIGRATIONS_DIR) -> list[str]:
    """Aplica las migraciones pendientes en orden de nombre. Devuelve las aplicadas."""
    applied: list[str] = []
    with conn.cursor() as cur:
        cur.execute(
            """
            CREATE TABLE IF NOT EXISTS public.schema_migrations (
                filename   text PRIMARY KEY,
                applied_at timestamptz NOT NULL DEFAULT now()
            )
            """
        )
        cur.execute("SELECT filename FROM public.schema_migrations")
        already = {row[0] for row in cur.fetchall()}

        for path in sorted(migrations_dir.glob("*.sql")):
            if path.name in already:
                continue
            log.info("Aplicando migración %s", path.name)
            cur.execute(path.read_text(encoding="utf-8"))
            cur.execute(
                "INSERT INTO public.schema_migrations (filename) VALUES (%s)",
                (path.name,),
            )
            applied.append(path.name)

    conn.commit()
    return applied
