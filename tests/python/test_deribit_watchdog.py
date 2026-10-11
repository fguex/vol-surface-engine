"""Tests du collecteur Deribit (watchdog de silence).

Scenario reel a l'origine de ces tests : apres le roulement d'echeances de
08:00 UTC, la session s'est reconnectee mais les abonnements sont restes muets.
Le heartbeat Deribit continuait d'arriver, donc la boucle de lecture ne voyait
rien d'anormal : le collecteur a tourne 53 h sans ecrire une seule ligne, en se
declarant en bonne sante du point de vue du socket.
Le watchdog doit fermer la connexion des que plus AUCUNE donnee n'entre.

Lancement : .venv/bin/python -m pytest tests/python -q
(volontairement sans pytest-asyncio : asyncio.run suffit.)
"""

from __future__ import annotations

import asyncio
import json
import time
from pathlib import Path

from python.collect.deribit_ws import Collector


class FakeWS:
    """Remplace la connexion : on ne teste que la decision du watchdog.

    close() reproduit l'effet reel : la session se termine, donc _subscribed
    retombe a False (c'est le finally de _session qui le fait en production).
    Sans ca, le watchdog fermerait la meme connexion a chaque tour.
    """

    def __init__(self, collector: "Collector | None" = None) -> None:
        self.closed = 0
        self._collector = collector

    async def close(self) -> None:
        self.closed += 1
        if self._collector is not None:
            self._collector._subscribed = False


def make_collector(tmp_path: Path, stale_s: float = 30.0) -> Collector:
    return Collector(tmp_path, rotate_s=3600, chaos_at=None, stale_s=stale_s)


def events(tmp_path: Path) -> list[dict]:
    p = tmp_path / "events.jsonl"
    if not p.exists():
        return []
    return [json.loads(line) for line in p.read_text().splitlines()]


async def _run_watchdog(c: Collector, seconds: float = 0.3) -> None:
    task = asyncio.create_task(c.watchdog(every_s=0.05))
    await asyncio.sleep(seconds)
    c.stop.set()
    await asyncio.wait_for(task, timeout=2)


def test_watchdog_ferme_une_session_muette(tmp_path: Path) -> None:
    c = make_collector(tmp_path, stale_s=0.2)
    c._ws = FakeWS(c)
    c._subscribed = True
    c.stats.last_data_at = time.time() - 60      # muet depuis 60 s

    asyncio.run(_run_watchdog(c))

    assert c._ws.closed == 1, "la connexion muette doit etre fermee une fois"
    assert c.stats.stale_events == 1
    assert "stale_watchdog" in [e["event"] for e in events(tmp_path)]


def test_watchdog_ne_touche_pas_une_session_active(tmp_path: Path) -> None:
    c = make_collector(tmp_path, stale_s=30.0)
    c._ws = FakeWS()
    c._subscribed = True
    c.stats.last_data_at = time.time()

    asyncio.run(_run_watchdog(c))

    assert c._ws.closed == 0
    assert c.stats.stale_events == 0
    assert "stale_watchdog" not in [e["event"] for e in events(tmp_path)]


def test_watchdog_ignore_avant_abonnement(tmp_path: Path) -> None:
    """Avant la fin des subscribe, un silence est normal : ne rien couper."""
    c = make_collector(tmp_path, stale_s=0.2)
    c._ws = FakeWS()
    c._subscribed = False
    c.stats.last_data_at = time.time() - 60

    asyncio.run(_run_watchdog(c))

    assert c._ws.closed == 0


def test_timeouts_de_connexion_definis() -> None:
    """Sans open_timeout, un connect() qui traine bloque le lecteur sans fin."""
    from python.collect import deribit_ws as d

    assert d.OPEN_TIMEOUT_S > 0
    assert d.STALE_S > 0
    assert d.CONNECT_CLOSE_TIMEOUT_S > 0
