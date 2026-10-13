"""Tests du runner de migrations (logique pure, sans base de donnees)."""

from __future__ import annotations

import hashlib
from datetime import datetime, timezone
from pathlib import Path

import pytest

from python.db.migrate import Migration, discover, show_status


def ecrire(dir_: Path, nom: str, contenu: str = "SELECT 1;\n") -> Path:
    p = dir_ / nom
    p.write_text(contenu, encoding="utf-8")
    return p


def test_decouverte_ordonnee_par_version(tmp_path: Path) -> None:
    ecrire(tmp_path, "003_dimensions.sql")
    ecrire(tmp_path, "001_schema.sql")
    ecrire(tmp_path, "010_faits.sql")
    versions = [m.version for m in discover(tmp_path)]
    assert versions == ["001", "003", "010"]


def test_nom_non_conforme_refuse(tmp_path: Path) -> None:
    ecrire(tmp_path, "004_Majuscules.sql")
    with pytest.raises(SystemExit) as e:
        discover(tmp_path)
    assert "nom invalide" in str(e.value)


def test_version_dupliquee_refusee(tmp_path: Path) -> None:
    ecrire(tmp_path, "001_a.sql")
    ecrire(tmp_path, "001_b.sql")
    with pytest.raises(SystemExit) as e:
        discover(tmp_path)
    assert "dupliquees" in str(e.value)


def test_repertoire_absent(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        discover(tmp_path / "inexistant")


def test_checksum_change_avec_le_contenu(tmp_path: Path) -> None:
    p = ecrire(tmp_path, "001_a.sql", "SELECT 1;\n")
    avant = discover(tmp_path)[0].checksum
    p.write_text("SELECT 1;  -- retouche apres application\n", encoding="utf-8")
    apres = discover(tmp_path)[0].checksum
    assert avant != apres


def test_marqueur_no_transaction(tmp_path: Path) -> None:
    ecrire(tmp_path, "001_sans_tx.sql",
           "-- migrate:no-transaction\nCREATE INDEX CONCURRENTLY i ON t (x);\n")
    ecrire(tmp_path, "002_avec_tx.sql", "SELECT 1;\n")
    par_version = {m.version: m for m in discover(tmp_path)}
    assert par_version["001"].transactional is False
    assert par_version["002"].transactional is True


def test_marqueur_ignore_s_il_est_plus_bas(tmp_path: Path) -> None:
    """Le marqueur ne compte que dans les 3 premieres lignes (un commentaire tardif ne doit pas changer le mode)."""
    corps = "-- 001 : commentaire\n-- suite\n-- encore\nselect 1;\n-- migrate:no-transaction\n"
    ecrire(tmp_path, "001_a.sql", corps)
    assert discover(tmp_path)[0].transactional is True


def _migration(version: str, nom: str, contenu: str) -> Migration:
    return Migration(version, nom, Path(f"{version}_{nom}.sql"), contenu,
                     hashlib.sha256(contenu.encode()).hexdigest())


def _ligne(applied_at: datetime, checksum: str, duration_ms: int = 3) -> dict:
    return {"name": "a", "checksum": checksum, "applied_at": applied_at,
            "duration_ms": duration_ms, "note": None}


def test_status_detecte_migration_modifiee(capsys) -> None:
    m = _migration("001", "a", "SELECT 1;\n")
    applique = {"001": _ligne(datetime(2026, 10, 13, tzinfo=timezone.utc), "empreinte-differente", 12)}
    code = show_status([m], applique)
    out = capsys.readouterr().out
    assert code == 3
    assert "MODIFIEE" in out


def test_status_detecte_fichier_absent(capsys) -> None:
    applique = {"001": _ligne(datetime(2026, 10, 13, tzinfo=timezone.utc), "x", 5)}
    code = show_status([], applique)
    out = capsys.readouterr().out
    assert code == 3
    assert "FICHIER ABSENT" in out


def test_status_ok_quand_tout_est_coherent(capsys) -> None:
    m = _migration("001", "a", "SELECT 1;\n")
    applique = {"001": _ligne(datetime(2026, 10, 13, tzinfo=timezone.utc), m.checksum)}
    assert show_status([m], applique) == 0
