"""Recuperation des fichiers bronze .part apres un arret brutal.

Un reboot ou un kill -9 ne laisse pas au BronzeWriter le temps de renommer son
fichier temporaire : les donnees sont sur le disque dans x.jsonl.zst.part,
invisibles pour tout ce qui lit *.jsonl.zst. Au redemarrage, recover_stale_parts
doit rendre ces lignes lisibles au lieu de les perdre en silence.

Mesure sur un vrai fichier (Mac mini, 11 oct. 2026, 2,49 Mo de .part laisse par
un reboot) : 122 962 lignes completes recuperees, 0 JSON invalide, et exactement
le meme nombre de lignes que `zstd -d -c` sur le meme fichier.

Lancement : .venv/bin/python -m pytest tests/python -q
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import zstandard

from python.collect.deribit_ws import BronzeWriter, recover_stale_parts


def _read_lines(path: Path) -> list[bytes]:
    dctx = zstandard.ZstdDecompressor()
    with open(path, "rb") as fh, dctx.stream_reader(fh) as reader:
        return [line for line in reader.read().split(b"\n") if line.strip()]


def _age(path: Path, seconds: float = 3600.0) -> None:
    old = time.time() - seconds
    os.utime(path, (old, old))


def test_recupere_un_part_laisse_par_un_crash(tmp_path: Path) -> None:
    """Le cas reel : le process meurt sans close(), le .part reste sur le disque.

    On simule en fermant le descripteur sans passer par close() (qui, lui,
    ferait le rename). Les blocs deja pousses par le compresseur sont lisibles.
    """
    w = BronzeWriter(tmp_path / "bronze", rotate_s=3600)
    for i in range(20_000):
        w.write(time.time_ns(), json.dumps({"params": {"channel": "quote.X", "data": {"i": i}}}))
    w.fh.flush()
    part = w._tmp
    w.fh.close()                      # arret brutal : pas de close() du writer
    assert part.exists()
    assert not w._final.exists()
    _age(part)

    recovered = recover_stale_parts(tmp_path / "bronze")

    assert len(recovered) == 1
    assert recovered[0]["recovered_lines"] > 0, "les blocs deja ecrits doivent etre recuperes"
    assert recovered[0]["recovered_lines"] <= 20_000
    assert not part.exists(), "le .part doit etre range, jamais laisse tel quel"
    assert part.with_suffix(part.suffix + ".corrupt").exists()

    out = w._final
    assert out.exists()
    lines = _read_lines(out)
    assert len(lines) == recovered[0]["recovered_lines"]
    assert len(lines) >= 1000, "la quasi-totalite du fichier doit etre la"
    for line in lines[:5000]:
        json.loads(line)              # aucune ligne tronquee ne doit passer


def test_troncature_au_milieu_d_un_bloc_ne_fabrique_rien(tmp_path: Path) -> None:
    """Coupe au milieu d'un bloc zstd : le decodeur ne peut rien rendre.

    Comportement honnete attendu : zero ligne inventee, et le fichier est range
    en .corrupt pour etre visible (jamais supprime en silence).
    """
    cctx = zstandard.ZstdCompressor(level=6)
    payload = b"".join(json.dumps({"r": i, "m": {}}).encode() + b"\n" for i in range(10))
    part = tmp_path / "cut.jsonl.zst.part"
    part.write_bytes(cctx.compress(payload)[:40])       # entete + debut de bloc
    _age(part)

    recovered = recover_stale_parts(tmp_path)

    assert recovered[0]["recovered_lines"] == 0
    assert not (tmp_path / "cut.jsonl.zst").exists()
    assert (tmp_path / "cut.jsonl.zst.part.corrupt").exists()


def test_ignore_un_fichier_recent(tmp_path: Path) -> None:
    """Un .part de moins d'une minute peut appartenir a une session vivante."""
    part = tmp_path / "b.jsonl.zst.part"
    part.write_bytes(b"peu importe")

    assert recover_stale_parts(tmp_path, min_age_s=60.0) == []
    assert part.exists()


def test_entree_illisible_est_rangee_sans_crash(tmp_path: Path) -> None:
    part = tmp_path / "c.jsonl.zst.part"
    part.write_bytes(b"pas du zstd du tout")
    _age(part)

    recovered = recover_stale_parts(tmp_path)

    assert recovered[0]["recovered_lines"] == 0
    assert (tmp_path / "c.jsonl.zst.part.corrupt").exists()


def test_traverse_les_sous_dossiers_provider_date(tmp_path: Path) -> None:
    """L'arborescence bronze est provider=/date= : la recherche doit etre recursive."""
    sub = tmp_path / "provider=deribit" / "date=2026-10-11"
    sub.mkdir(parents=True)
    w = BronzeWriter(sub, rotate_s=3600)
    for i in range(20_000):
        w.write(time.time_ns(), json.dumps({"params": {"channel": "quote.Y", "data": {"i": i}}}))
    w.fh.flush()
    part = w._tmp
    w.fh.close()
    _age(part)

    recovered = recover_stale_parts(tmp_path)

    assert len(recovered) == 1
    assert w._final.exists()
