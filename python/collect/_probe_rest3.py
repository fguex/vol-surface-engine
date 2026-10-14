"""Sonde 3 : dimensionnement (debit, octets) et bornes du backfill.

Usage : PYTHONPATH=. .venv/bin/python -m python.collect._probe_rest3
"""
from __future__ import annotations

import asyncio
import json
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request

import websockets

BASE = "https://www.deribit.com/api/v2"
WS = "wss://www.deribit.com/ws/api/v2"
CTX = ssl.create_default_context()


def raw_call(method: str, params: dict | None = None) -> tuple[bytes, float]:
    url = f"{BASE}/{method}"
    if params:
        url += "?" + "&".join(f"{k}={urllib.parse.quote(str(v))}" for k, v in params.items())
    req = urllib.request.Request(url, headers={"User-Agent": "vse-probe/1.0"})
    t0 = time.time()
    try:
        with urllib.request.urlopen(req, timeout=30, context=CTX) as r:
            return r.read(), time.time() - t0
    except urllib.error.HTTPError:
        return b"", time.time() - t0


def call(method: str, params: dict | None = None) -> dict:
    body, _ = raw_call(method, params)
    try:
        return json.loads(body.decode())
    except Exception:  # noqa: BLE001
        return {"error": {"message": "unreadable"}}


def ms(y: int, m: int, d: int = 1) -> int:
    return int(time.mktime((y, m, d, 0, 0, 0, 0, 0, 0)) * 1000)


def main() -> None:
    # --- 1. Taille d'un snapshot de chaine complete (REST) ---
    for kind, currency in [("option", "BTC"), ("option", "ETH")]:
        body, dt = raw_call("public/get_book_summary_by_currency", {"currency": currency, "kind": kind})
        print(f"book_summary {currency} {kind} : {len(body)/1024:.0f} Ko en {dt:.2f} s")

    # --- 2. Debit d'un echantillon stratifie de ticker.{instrument} ---
    async def sample_ticker() -> None:
        async with websockets.connect(WS, open_timeout=15, max_size=2**24) as ws:
            await ws.send(json.dumps({"jsonrpc": "2.0", "id": 1, "method": "public/get_instruments",
                                      "params": {"currency": "BTC", "kind": "option"}}))
            while True:
                m = json.loads(await asyncio.wait_for(ws.recv(), timeout=20))
                if m.get("id") == 1:
                    break
            insts = m["result"]
            # echantillon stratifie : toutes les expiries, 5 strikes par expiry
            by_exp: dict[int, list] = {}
            for i in insts:
                by_exp.setdefault(i["expiration_timestamp"], []).append(i)
            sample = []
            for exp, lst in sorted(by_exp.items())[:8]:
                lst.sort(key=lambda i: abs(i["strike"] - 80000))
                for i in lst[:5]:
                    sample.append(i["instrument_name"])
            print(f"\nechantillon ticker : {len(sample)} instruments sur {len(insts)} "
                  f"({len(sample)/len(insts)*100:.1f} %)")
            for n, nm in enumerate(sample, start=100):
                await ws.send(json.dumps({"jsonrpc": "2.0", "id": n, "method": "public/subscribe",
                                          "params": {"channels": [f"ticker.{nm}.100ms"]}}))
            t0 = time.time()
            n_msg, n_bytes = 0, 0
            while time.time() - t0 < 30:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
                except TimeoutError:
                    continue
                if '"channel"' in raw and "ticker." in raw:
                    n_msg += 1
                    n_bytes += len(raw)
            dur = time.time() - t0
            rate = n_msg / dur
            bpm = n_bytes / n_msg if n_msg else 0
            print(f"  {rate:.1f} msg/s pour {len(sample)} instruments = {rate/len(sample):.3f} msg/s/instrument")
            print(f"  {bpm:.0f} octets/message")
            full = rate / len(sample) * len(insts)
            print(f"  extrapolation aux {len(insts)} instruments BTC : {full:.0f} msg/s "
                  f"= {full*bpm*86400/1e9:.2f} Go/jour (brut, avant zstd)")

    asyncio.run(sample_ticker())

    # --- 3. Profondeur funding ---
    r = call("public/get_funding_rate_history",
             {"instrument_name": "BTC-PERPETUAL", "start_timestamp": ms(2019, 1), "end_timestamp": ms(2019, 2)})
    print(f"\nfunding 2019-01 : {len(r.get('result', []))} points"
          f"{' KO ' + str(r.get('error')) if 'error' in r else ''}")
    r = call("public/get_funding_rate_history",
             {"instrument_name": "BTC-PERPETUAL", "start_timestamp": ms(2026, 9), "end_timestamp": ms(2026, 10)})
    print(f"funding 2026-09 : {len(r.get('result', []))} points")

    # --- 4. Borne du backfill des trades d'options ---
    print("\nborne des trades d'options BTC :")
    for y, m in [(2026, 4), (2026, 5), (2026, 6), (2026, 7), (2026, 8), (2026, 9), (2026, 10)]:
        r = call("public/get_last_trades_by_currency_and_time",
                 {"currency": "BTC", "kind": "option", "start_timestamp": ms(y, m),
                  "end_timestamp": ms(y, m + 1), "count": 5})
        tr = (r.get("result") or {}).get("trades", [])
        first = time.strftime("%Y-%m-%d %H:%M", time.gmtime(tr[0]["timestamp"] / 1000)) if tr else "-"
        print(f"  {y}-{m:02d} : {len(tr)} trades   premier = {first}")
    r = call("public/get_last_trades_by_currency_and_time",
             {"currency": "BTC", "kind": "option", "start_timestamp": ms(2025, 1),
              "end_timestamp": ms(2025, 12), "count": 1000})
    tr = (r.get("result") or {}).get("trades", [])
    print(f"  2025 full : {len(tr)} trades en un appel (count=1000)")

    # --- 5. Chain complete : intervalles valides du ticker WS ---
    print("\nintervalles ticker :", end=" ")
    asyncio.run(_test_intervals())


async def _test_intervals() -> None:
    async with websockets.connect(WS, open_timeout=15) as ws:
        for n, interval in enumerate(["100ms", "agg2", "raw"], start=1):
            ch = f"ticker.BTC-PERPETUAL.{interval}"
            await ws.send(json.dumps({"jsonrpc": "2.0", "id": n, "method": "public/subscribe",
                                      "params": {"channels": [ch]}}))
            while True:
                m = json.loads(await asyncio.wait_for(ws.recv(), timeout=20))
                if m.get("id") == n:
                    break
            print(f"{interval}={'OK' if 'error' not in m else 'KO'}", end="  ")
    print()


main()
