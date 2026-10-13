"""Runner de migrations SQL.

Pourquoi ce fichier existe
-------------------------
Le schema etait applique par `docker-entrypoint-initdb.d`, ce qui ne se declenche
qu'a la PREMIERE initialisation du volume : toute evolution ensuite se faisait en
SQL tape a la main, sans trace ni reproductibilite. Ici, les migrations sont la
seule source de verite, pour une base neuve comme pour une base existante.

Regles
------
- Les fichiers vivent dans `sql/` et s'appellent `NNN_nom.sql` (NNN a 3 chiffres
  minimum, croissant, jamais reutilise).
- Une migration appliquee ne se modifie JAMAIS : le runner enregistre son sha256
  et refuse toute modification ulterieure (exit 3), avant d'appliquer quoi que ce soit.
- Chaque migration s'execute dans une transaction, sauf si le fichier declare
  `-- migrate:no-transaction` en tete (utile pour CREATE INDEX CONCURRENTLY).
- Le runner prend un verrou consultatif : deux migrations simultanees sont impossibles.
- Idempotent : rejouer le runner ne fait rien sur une base a jour.

Usage
-----
    python -m python.db.migrate --status              # ou en est-on ?
    python -m python.db.migrate --dry-run             # que ferait-il ?
    python -m python.db.migrate                       # applique ce qui manque
    python -m python.db.migrate --target 003          # jusqu'a 003 inclus
    python -m python.db.migrate --baseline-through 001  # base existante : enregistre
                                                       # les migrations deja appliquees
                                                       # a la main, sans les executer

Codes de sortie : 0 ok, 1 erreur, 2 usage, 3 migration modifiee apres application.
"""

from __future__ import annotations

import argparse
import hashlib
import os
import re
import socket
import sys
import time
from dataclasses import dataclass
from pathlib import Path

from psycopg import ClientCursor

from python.db import connection

SQL_DIR_DEFAULT = Path(__file__).resolve().parents[2] / "sql"
NAME_RE = re.compile(r"^(\d{3,})_([a-z0-9_]+)\.sql$")
NO_TX_MARKER = "-- migrate:no-transaction"
LOCK_KEY = 0x5653454D  # "VSEM", verrou consultatif du runner

SCHEMA_MIGRATIONS_DDL = """
CREATE TABLE IF NOT EXISTS schema_migrations (
    version     text        PRIMARY KEY,
    name        text        NOT NULL,
    checksum    text        NOT NULL,
    applied_at  timestamptz NOT NULL DEFAULT now(),
    applied_by  text        NOT NULL,
    duration_ms integer     NOT NULL,
    note        text
)
"""


@dataclass(frozen=True)
class Migration:
    version: str
    name: str
    path: Path
    sql: str
    checksum: str

    @property
    def transactional(self) -> bool:
        return NO_TX_MARKER not in self.sql.splitlines()[:3]


def discover(sql_dir: Path) -> list[Migration]:
    """Liste les migrations, ordonnees par version. Refuse tout nom non conforme."""
    if not sql_dir.is_dir():
        raise SystemExit(f"repertoire de migrations introuvable : {sql_dir}")
    found: list[Migration] = []
    problems: list[str] = []
    for p in sorted(sql_dir.glob("*.sql")):
        m = NAME_RE.match(p.name)
        if not m:
            problems.append(f"  {p.name} : nom invalide (attendu NNN_nom_en_minuscules.sql)")
            continue
        body = p.read_text(encoding="utf-8")
        found.append(Migration(m.group(1), m.group(2), p, body,
                               hashlib.sha256(body.encode()).hexdigest()))
    if problems:
        raise SystemExit("migrations mal nommees :\n" + "\n".join(problems))
    versions = [m.version for m in found]
    dupes = {v for v in versions if versions.count(v) > 1}
    if dupes:
        raise SystemExit(f"versions dupliquees : {sorted(dupes)}")
    return sorted(found, key=lambda m: m.version)


def applied_rows(conn) -> dict[str, dict]:
    with conn.cursor() as cur:
        cur.execute("SELECT version, name, checksum, applied_at, duration_ms, note"
                    " FROM schema_migrations")
        return {r[0]: {"name": r[1], "checksum": r[2], "applied_at": r[3],
                       "duration_ms": r[4], "note": r[5]} for r in cur.fetchall()}


def show_status(migrations: list[Migration], applied: dict[str, dict]) -> int:
    """Imprime l'etat. Retourne 0 si tout est coherent, 3 si une migration a ete modifiee."""
    bad = 0
    print(f"{'version':>7}  {'etat':<12} {'appliquee le':<26} {'ms':>6}  nom")
    for m in migrations:
        row = applied.get(m.version)
        if row is None:
            print(f"{m.version:>7}  {'A APPLIQUER':<12} {'-':<26} {'-':>6}  {m.name}")
            continue
        if row["checksum"] != m.checksum:
            bad += 1
            print(f"{m.version:>7}  {'!! MODIFIEE':<12} {row['applied_at']:%Y-%m-%d %H:%M:%S %z}"
                  f"  {row['duration_ms']:>6}  {m.name}")
        else:
            print(f"{m.version:>7}  {'appliquee':<12} {row['applied_at']:%Y-%m-%d %H:%M:%S %z}"
                  f"  {row['duration_ms']:>6}  {m.name}")
    inconnues = sorted(set(applied) - {m.version for m in migrations})
    for v in inconnues:
        bad += 1
        print(f"{v:>7}  {'!! FICHIER ABSENT':<12} {applied[v]['applied_at']:%Y-%m-%d %H:%M:%S %z}"
              f"  {'-':>6}  {applied[v]['name']}  (enregistree en base, fichier introuvable dans sql/)")
    return 3 if bad else 0


def record(conn, m: Migration, duration_ms: int, note: str | None = None) -> None:
    who = f"{os.getenv('USER') or os.getenv('USERNAME') or '?'}@{socket.gethostname()}"
    with conn.cursor() as cur:
        cur.execute(
            "INSERT INTO schema_migrations (version, name, checksum, applied_by, duration_ms, note)"
            " VALUES (%s, %s, %s, %s, %s, %s)",
            (m.version, m.name, m.checksum, who, duration_ms, note),
        )


def _executer_fichier(conn, sql: str) -> None:
    """Execute un fichier SQL entier.

    psycopg3 refuse plusieurs ordres par `execute()` avec son protocole etendu, et
    en psycopg3 le `cursor_factory` se pose sur la CONNEXION (le parametre par
    curseur de psycopg2 n'existe plus). On bascule donc la connexion en ClientCursor
    (protocole simple, cote libpq) le temps du fichier, puis on restaure.
    """
    precedente = conn.cursor_factory
    conn.cursor_factory = ClientCursor
    try:
        with conn.cursor() as cur:
            cur.execute(sql)
    finally:
        conn.cursor_factory = precedente


def apply_one(conn, m: Migration) -> int:
    """Execute une migration et enregistre son application. Retourne la duree en ms."""
    t0 = time.perf_counter()
    if m.transactional:
        with conn.transaction():
            _executer_fichier(conn, m.sql)
            dur = int((time.perf_counter() - t0) * 1000)
            record(conn, m, dur)
    else:
        conn.commit()                       # fermer toute transaction ouverte avant de basculer
        conn.autocommit = True              # CREATE INDEX CONCURRENTLY, etc.
        try:
            _executer_fichier(conn, m.sql)
            dur = int((time.perf_counter() - t0) * 1000)
            record(conn, m, dur)
        finally:
            conn.autocommit = False
    return dur


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="python -m python.db.migrate",
        description="Applique les migrations SQL de sql/ dans l'ordre, une seule fois chacune.",
    )
    p.add_argument("--status", action="store_true", help="etat des migrations, ne modifie rien")
    p.add_argument("--dry-run", action="store_true", help="liste ce qui serait applique")
    p.add_argument("--target", metavar="NNN", help="s'applique jusqu'a cette version incluse")
    p.add_argument("--baseline-through", metavar="NNN",
                   help="enregistre les migrations <= NNN comme deja appliquees, SANS les executer "
                        "(base existante creee avant l'introduction des migrations)")
    p.add_argument("--sql-dir", metavar="PATH", default=str(SQL_DIR_DEFAULT),
                   help="repertoire des migrations (defaut : sql/)")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    sql_dir = Path(args.sql_dir).expanduser().resolve()
    migrations = discover(sql_dir)
    if not migrations:
        print(f"aucune migration dans {sql_dir}")
        return 0

    with connection.connect() as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT pg_advisory_lock(%s)", (LOCK_KEY,))
            cur.execute(SCHEMA_MIGRATIONS_DDL)
        conn.commit()
        applied = applied_rows(conn)

        if args.status:
            return show_status(migrations, applied)

        # 1. Coherence AVANT toute action : une migration modifiee bloque tout.
        modifiees = [m for m in migrations
                     if m.version in applied and applied[m.version]["checksum"] != m.checksum]
        if modifiees:
            for m in modifiees:
                print(f"!! {m.version}_{m.name}.sql a ete modifiee apres application "
                      f"(sha256 different). Une migration appliquee ne se modifie jamais : "
                      f"cree une nouvelle migration {int(m.version) + 1:03d}_... à la place.",
                      file=sys.stderr)
            return 3

        cible = args.target or "999999"

        # 2. Baseline D'ABORD : sur une base existante, on enregistre les migrations
        #    deja appliquees a la main SANS les executer. Il faut ensuite recalculer
        #    ce qui reste a faire, sinon le runner tenterait de rejouer la baseline.
        if args.baseline_through:
            limite = args.baseline_through
            a_marquer = [m for m in migrations if m.version <= limite and m.version not in applied]
            if not a_marquer:
                print(f"baseline : rien a enregistrer jusqu'a {limite}")
            for m in a_marquer:
                with conn.transaction():
                    record(conn, m, 0, note="baseline (appliquee avant l'introduction des migrations)")
                print(f"  baseline {m.version} {m.name}")
            conn.commit()
            applied = applied_rows(conn)          # recalcul indispensable
            if a_marquer and not (args.dry_run or args.status):
                print("  (baseline enregistree : ces versions ne seront pas executees)")

        a_faire = [m for m in migrations if m.version not in applied and m.version <= cible]

        if not a_faire:
            print(f"a jour : {len(applied)} migration(s) appliquee(s), rien a faire")
            return 0

        if args.dry_run:
            print(f"{len(a_faire)} migration(s) a appliquer :")
            for m in a_faire:
                mode = "transaction" if m.transactional else "autocommit"
                print(f"  {m.version}_{m.name}.sql   ({len(m.sql)} octets, {mode})")
            return 0

        # 3. Application, une par une, avec trace.
        for m in a_faire:
            print(f"-> {m.version}_{m.name} ...", end="", flush=True)
            try:
                dur = apply_one(conn, m)
            except Exception as exc:  # noqa: BLE001
                print(" ECHEC")
                print(f"   {type(exc).__name__}: {exc}", file=sys.stderr)
                print(f"   migration {m.version} NON enregistree (rollback) : la base est "
                      f"restee dans l'etat precedent.", file=sys.stderr)
                return 1
            print(f" ok ({dur} ms)")
        print(f"termine : {len(a_faire)} migration(s) appliquee(s)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
