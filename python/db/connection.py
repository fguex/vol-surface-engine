"""Connexion PostgreSQL.

Lit les variables d'environnement standard libpq : PGHOST, PGDATABASE,
PGUSER, PGPASSWORD. Aucune URL hardcodee, aucun secret dans le code.
"""

from __future__ import annotations

import psycopg


def connect() -> psycopg.Connection:
    """Connexion en autocommit=False (defaut psycopg3).

    Utilise le gestionnaire de contexte de l'appelant pour le commit/rollback :

        with connection.connect() as conn:
            with conn.transaction():
                writer.insert_snapshot(conn, meta)
                writer.copy_curated(conn, df)
    """
    return psycopg.connect()   # lit PGHOST/PGDATABASE/PGUSER/PGPASSWORD
