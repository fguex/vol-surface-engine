"""Sonde jetable : quels canaux Deribit sont reellement disponibles pour BTC/ETH ?

Objectif : decider des abonnements du collecteur (donnees pertinentes pour une
surface de vol SSVI + ML) en mesurant, pas en devinant.
Lance dans le conteneur collector : python -m python.collect._probe_channels
"""
from __future__ import annotations

import asyncio
import json
import time

import websockets

URL = "wss://www.deribit.com/ws/api/v2"

TEMPLATES = [
    "ticker.{INSTR}.100ms",
    "book.{INSTR}.none.10.100ms",
    "book.{INSTR}.agg2",
    "markprice.options.btc_usd",
    "markprice.options.eth_usd",
    "deribit_price_index.btc_usd",
    "deribit_volatility_index.btc_usd",
    "estimated_expiration_price.btc_usd",
    "deribit_price_ranking.btc_usd",
    "perpetual.BTC-PERPETUAL",
    "trades.option.BTC.100ms",
    "trades.BTC-PERPETUAL.raw",
    "trades.spot.BTC.raw",
    "block_trades.BTC",
    "platform_state",
]


async def rpc_until(ws, rid: int, timeout: float = 20.0) -> dict:
    """Lit jusqu'a trouver la reponse d'id `rid` (les data frames arrivent en premier)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        raw = await asyncio.wait_for(ws.recv(), timeout=deadline - time.time())
        m = json.loads(raw)
        if m.get("id") == rid:
            return m
    raise TimeoutError(f"pas de reponse pour id={rid}")


async def main() -> None:
    async with websockets.connect(URL, open_timeout=15, max_size=2**24) as ws:
        await ws.send(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "public/get_instruments",
                                  "params": {"currency": "BTC", "kind": "option",
                                             "expired": "false"}}))
        insts = [i["instrument_name"] for i in (await rpc_until(ws, 1))["result"]]
        near = sorted(insts, key=len)[0]
        print(f"instruments BTC vivants : {len(insts)} ; exemple = {near}\n")

        ok, ko = [], []
        for n, t in enumerate(TEMPLATES, start=10):
            ch = t.replace("{INSTR}", near)
            await ws.send(json.dumps({"jsonrpc": "2.0", "id": n, "method": "public/subscribe",
                                      "params": {"channels": [ch]}}))
            try:
                resp = await rpc_until(ws, n)
            except TimeoutError:
                ko.append((ch, "timeout"))
                continue
            if "error" in resp:
                ko.append((ch, resp["error"].get("message", "?")))
            else:
                ok.append(ch)

        print("== CANAUX ACCEPTES ==")
        for c in ok:
            print(f"  OK   {c}")
        print("== REFUSES ==")
        for c, e in ko:
            print(f"  KO   {c}  -> {e}")

        # Debit des canaux interessants : on compte les messages pendant 20 s.
        print("\n== DEBIT (20 s) ==")
        counts: dict[str, int] = {}
        keys: dict[str, list[str]] = {}
        t0 = time.time()
        while time.time() - t0 < 20:
            try:
                raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
            except TimeoutError:
                continue
            m = json.loads(raw)
            ch = m.get("params", {}).get("channel")
            if ch:
                counts[ch] = counts.get(ch, 0) + 1
                if ch not in keys:
                    data = m["params"].get("data")
                    sample = data[0] if isinstance(data, list) and data else data
                    keys[ch] = sorted(sample.keys()) if isinstance(sample, dict) else [type(sample).__name__]
        for ch, c in sorted(counts.items(), key=lambda kv: -kv[1]):
            print(f"  {c:>7} msg / 20 s   {c / 20:>8.1f} msg/s   {ch}")
            print(f"          champs: {keys.get(ch)}")


asyncio.run(main())
