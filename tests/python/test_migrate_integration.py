"""Test d'integration du runner de migrations : contre une vraie base.

Saute automatiquement si aucune base n'est joignable (cas d'un poste de dev sans
Postgres). Sur le Mac mini, il tourne et c'est lui qui a attrape le vrai bug :
en psycopg3, `cursor_factory` se pose sur la connexion, pas sur le curseur, donc
un fichier de migration a plusieurs ordres ne s'executait pas.

Il ne touche jamais la base de production : il cree une base jetable au nom
unique, y applique les migrations, verifie, puis la supprime.
"""

from __future__ import annotations

import uuid
from pathlib import Path

import psycopg
import pytest

from python.db import connection
from python.db.migrate import main as migrate_main

SQL_DIR = Path(__file__).resolve().parents[2] / "sql"


def _base_joignable() -> bool:
    try:
        with connection.connect() as conn:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
        return True
    except Exception:  # noqa: BLE001
        return False


pytestmark = pytest.mark.skipif(
    not _base_joignable(),
    reason="aucune base joignable (PGHOST/PGUSER/PGPASSWORD non definis) : test d'integration saute",
)


@pytest.fixture
def base_jetable():
    """Cree une base vide au nom unique, la supprime a la fin quoi qu'il arrive."""
    nom = f"mig_test_{uuid.uuid4().hex[:12]}"
    with connection.connect() as conn:
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute(f'CREATE DATABASE "{nom}"')
    yield nom
    with connection.connect() as conn:
        conn.autocommit = True
        with conn.cursor() as cur:
            cur.execute(f'DROP DATABASE IF EXISTS "{nom}" WITH (FORCE)')


def _nombre_de_tables(nom_base: str) -> int:
    with psycopg.connect(dbname=nom_base) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM information_schema.tables"
                        " WHERE table_schema='public' AND table_type='BASE TABLE'")
            return cur.fetchone()[0]


def _migrations_du_repo() -> list[str]:
    """Les migrations reellement presentes dans sql/.

    Un test qui code la liste en dur casse a chaque nouvelle migration, sans rien
    apprendre sur le code : le lire depuis le dossier teste la vraie propriete
    ("toutes les migrations sont appliquees").
    """
    return sorted(p.name.split("_")[0] for p in SQL_DIR.glob("[0-9][0-9][0-9]_*.sql"))


def _versions(nom_base: str) -> list[str]:
    with psycopg.connect(dbname=nom_base) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT version FROM schema_migrations ORDER BY version")
            return [r[0] for r in cur.fetchall()]


def test_installation_complete_sur_base_neuve(base_jetable, monkeypatch) -> None:
    """Le chemin d'installation : 001 cree tout, 002 ajoute btree_gist."""
    monkeypatch.setenv("PGDATABASE", base_jetable)
    assert migrate_main(["--sql-dir", str(SQL_DIR)]) == 0
    assert _versions(base_jetable) == _migrations_du_repo()
    assert _nombre_de_tables(base_jetable) >= 7  # 6 tables + schema_migrations


def test_btree_gist_installe(base_jetable, monkeypatch) -> None:
    """002 doit rendre btree_gist disponible : les dimensions bitemporelles en dependent."""
    monkeypatch.setenv("PGDATABASE", base_jetable)
    migrate_main(["--sql-dir", str(SQL_DIR)])
    with psycopg.connect(dbname=base_jetable) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT extversion FROM pg_extension WHERE extname='btree_gist'")
            assert cur.fetchone() is not None


def test_idempotence(base_jetable, monkeypatch) -> None:
    """Rejouer le runner ne reapplique rien et ne casse rien."""
    monkeypatch.setenv("PGDATABASE", base_jetable)
    migrate_main(["--sql-dir", str(SQL_DIR)])
    tables_avant = _nombre_de_tables(base_jetable)
    assert migrate_main(["--sql-dir", str(SQL_DIR)]) == 0
    assert _nombre_de_tables(base_jetable) == tables_avant


def _table_existe(nom_base: str, table: str) -> bool:
    with psycopg.connect(dbname=nom_base) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM information_schema.tables WHERE table_name=%s", (table,))
            return cur.fetchone()[0] == 1


def test_baseline_n_execute_pas_le_sql(base_jetable, monkeypatch, tmp_path) -> None:
    """Sur une base creee avant les migrations : 001 est enregistree sans etre
    executee, et la suite s'applique normalement."""
    monkeypatch.setenv("PGDATABASE", base_jetable)
    dossier = tmp_path / "sql"
    dossier.mkdir()
    (dossier / "001_a.sql").write_text("CREATE TABLE t1 (x int);\n", encoding="utf-8")
    (dossier / "002_b.sql").write_text("CREATE TABLE t2 (x int);\n", encoding="utf-8")
    assert migrate_main(["--sql-dir", str(dossier), "--baseline-through", "001"]) == 0
    assert _versions(base_jetable) == ["001", "002"]
    assert not _table_existe(base_jetable, "t1"), "001 ne doit PAS etre executee (baseline)"
    assert _table_existe(base_jetable, "t2"), "002 doit etre appliquee apres la baseline"


def test_baseline_sur_une_base_deja_peuplee_ne_casse_rien(base_jetable, monkeypatch) -> None:
    """Le scenario reel du Mac mini : les tables de 001 existent deja, on enregistre
    la baseline, et le runner ne doit PAS tenter de rejouer 001 (double creation)."""
    monkeypatch.setenv("PGDATABASE", base_jetable)
    with psycopg.connect(dbname=base_jetable) as conn:
        with conn.cursor() as cur:
            # Une base qui a deja les tables de 001 a aussi ses extensions : c'est
            # 001 qui cree timescaledb. Sans elles, la simulation n'est pas fidele
            # et 003 echoue sur create_hypertable (piege du test, pas du code).
            cur.execute("CREATE EXTENSION IF NOT EXISTS timescaledb")
            # Simule la base existante : une table de 001, creee hors du runner.
            # 004 la supprimera lors du second passage, sans consequence pour le test.
            cur.execute("CREATE TABLE snapshot (x int)")
    assert migrate_main(["--sql-dir", str(SQL_DIR), "--baseline-through", "001"]) == 0
    assert _versions(base_jetable) == _migrations_du_repo()
    # et un second passage doit etre totalement inerte
    assert migrate_main(["--sql-dir", str(SQL_DIR)]) == 0


def test_migration_modifiee_bloque_tout(base_jetable, monkeypatch, tmp_path) -> None:
    """Une migration modifiee apres application doit tout bloquer (exit 3), avant
    d'appliquer quoi que ce soit."""
    monkeypatch.setenv("PGDATABASE", base_jetable)
    dossier = tmp_path / "sql"
    dossier.mkdir()
    (dossier / "001_a.sql").write_text("CREATE TABLE t1 (x int);\n", encoding="utf-8")
    (dossier / "002_b.sql").write_text("CREATE TABLE t2 (x int);\n", encoding="utf-8")
    assert migrate_main(["--sql-dir", str(dossier)]) == 0

    # on retouche 001 et on ajoute une 003 : rien ne doit passer, pas meme 003
    (dossier / "001_a.sql").write_text("CREATE TABLE t1 (x int, y int);\n", encoding="utf-8")
    (dossier / "003_c.sql").write_text("CREATE TABLE t3 (x int);\n", encoding="utf-8")
    assert migrate_main(["--sql-dir", str(dossier)]) == 3

    with psycopg.connect(dbname=base_jetable) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM information_schema.tables WHERE table_name='t3'")
            assert cur.fetchone()[0] == 0, "003 ne doit PAS avoir ete appliquee"


def test_marqueur_no_transaction_execute_hors_transaction(base_jetable, monkeypatch, tmp_path) -> None:
    """CREATE INDEX CONCURRENTLY ne peut pas tourner dans une transaction : le
    marqueur doit le sortir du bloc transactionnel."""
    monkeypatch.setenv("PGDATABASE", base_jetable)
    dossier = tmp_path / "sql"
    dossier.mkdir()
    (dossier / "001_t.sql").write_text("CREATE TABLE t (x int);\n", encoding="utf-8")
    (dossier / "002_idx.sql").write_text(
        "-- migrate:no-transaction\nCREATE INDEX CONCURRENTLY t_x_idx ON t (x);\n", encoding="utf-8")
    assert migrate_main(["--sql-dir", str(dossier)]) == 0
    with psycopg.connect(dbname=base_jetable) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT count(*) FROM pg_indexes WHERE indexname='t_x_idx'")
            assert cur.fetchone()[0] == 1


def test_cible_limite_l_application(base_jetable, monkeypatch, tmp_path) -> None:
    """--target 001 ne doit appliquer que 001."""
    monkeypatch.setenv("PGDATABASE", base_jetable)
    dossier = tmp_path / "sql"
    dossier.mkdir()
    (dossier / "001_a.sql").write_text("CREATE TABLE t1 (x int);\n", encoding="utf-8")
    (dossier / "002_b.sql").write_text("CREATE TABLE t2 (x int);\n", encoding="utf-8")
    assert migrate_main(["--sql-dir", str(dossier), "--target", "001"]) == 0
    assert _versions(base_jetable) == ["001"]


def test_les_vues_de_lecture_sont_interrogeables(base_jetable, monkeypatch) -> None:
    """Une vue qui ne compile pas ne casse pas la migration : elle casse a la
    premiere lecture, en production, chez celui qui interroge. On la lit donc ici.
    """
    monkeypatch.setenv("PGDATABASE", base_jetable)
    assert migrate_main(["--sql-dir", str(SQL_DIR)]) == 0

    with psycopg.connect(dbname=base_jetable) as conn:
        with conn.cursor() as cur:
            cur.execute("""SELECT table_name FROM information_schema.views
                            WHERE table_schema = 'public' ORDER BY 1""")
            vues = [r[0] for r in cur.fetchall()]
            attendues = {"v_surface", "v_atm_terme", "v_perp_funding"}
            assert attendues <= set(vues), f"vues manquantes : {attendues - set(vues)}"
            for vue in vues:
                cur.execute(f"SELECT count(*) FROM {vue}")   # doit compiler, meme vide
