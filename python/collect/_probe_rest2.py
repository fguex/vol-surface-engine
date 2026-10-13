"""Sonde 2 : ou sont exactement les greeks / OI, et quelle profondeur d'historique ?

Usage : PYTHONPATH=. .venv/bin/python -m python.collect._probe_rest2
"""
from __future__ import annotations

import json
import ssl
import time
import urllib.error
import urllib.parse
import urllib.request

BASE = "https://www.deribit.com/api/v2"
CTX = ssl.create_default_context()


def call(method: str, params: dict | None = None) -> dict:
    url = f"{BASE}/{method}"
    if params:
        url += "?" + "&".join(f"{k}={urllib.parse.quote(str(v))}" for k, v in params.items())
    req = urllib.request.Request(url, headers={"User-Agent": "vse-probe/1.0"})
    try:
        with urllib.request.urlopen(req, timeout=25, context=CTX) as r:
            return json.loads(r.read().decode())
    except urllib.error.HTTPError as e:
        return {"error": {"message": f"HTTP {e.code} {e.read().decode()[:120]}"}}
    except Exception as e:  # noqa: BLE001
        return {"error": {"message": f"{type(e).__name__}: {e}"}}


def ok(method: str, params: dict | None = None):
    res = call(method, params)
    if "error" in res:
        return None, res["error"].get("message")
    return res["result"], None


def ms(y: int, m: int, d: int = 1) -> int:
    return int(time.mktime((y, m, d, 0, 0, 0, 0, 0, 0)) * 1000)


def main() -> None:
    # --- 1. Ou sont les greeks ? take a live near-ATM option ---
    insts, _ = ok("public/get_instruments", {"currency": "BTC", "kind": "option", "expired": "false"})
    perp, _ = ok("public/ticker", {"instrument_name": "BTC-PERPETUAL"})
    idx = perp["index_price"]
    cands = [i for i in insts if i.get("strike")]
    atm = min(cands, key=lambda i: abs(i["strike"] - idx))
    name = atm["instrument_name"]
    print(f"option test = {name}  (spot {idx:.0f}, strike {atm['strike']:.0f}, "
          f"exp {time.strftime('%Y-%m-%d', time.gmtime(atm['expiration_timestamp']/1000))})\n")

    for method, params in [
        ("public/ticker", {"instrument_name": name}),
        ("public/get_order_book", {"instrument_name": name, "depth": 1}),
        ("public/get_book_summary_by_instrument", {"instrument_name": name}),
    ]:
        r, err = ok(method, params)
        if err:
            print(f"KO  {method:44s} {err}")
            continue
        r = r[0] if isinstance(r, list) else r
        print(f"OK  {method:44s} champs: {sorted(r.keys())}")
        if "greeks" in r:
            print(f"    greeks = {json.dumps(r['greeks'])}")
        for k in ("mark_iv", "bid_iv", "ask_iv", "open_interest", "interest_rate", "underlying_price"):
            if k in r:
                print(f"    {k} = {r[k]}")

    # --- 2. Instruments expires : peut-on reconstruire les chaines passees ? ---
    for expired in ("true",):
        r, err = ok("public/get_instruments",
                    {"currency": "BTC", "kind": "option", "expired": expired,
                     "instrument_type": "reversed"})
        if err:
            print(f"\nKO  get_instruments expired={expired} : {err}")
        else:
            n = len(r)
            oldest = min((i["expiration_timestamp"] for i in r), default=0)
            newest = max((i["expiration_timestamp"] for i in r), default=0)
            print(f"\nOK  get_instruments expired={expired} : {n} instruments, "
                  f"expirations de {time.strftime('%Y-%m-%d', time.gmtime(oldest/1000))} "
                  f"a {time.strftime('%Y-%m-%d', time.gmtime(newest/1000))}")

    # --- 3. Profondeur d'historique, mesuree ---
    print("\n== PROFONDEUR ==")
    for y, m in [(2019, 1), (2020, 1), (2021, 1), (2022, 1), (2024, 1)]:
        r, err = ok("public/get_tradingview_chart_data",
                    {"instrument_name": "BTC-PERPETUAL", "start_timestamp": ms(y, m),
                     "end_timestamp": ms(y, m + 1), "resolution": "1D"})
        n = len(r["ticks"]) if r and r.get("ticks") else 0
        print(f"  candles BTC-PERPETUAL {y}-{m:02d} : {n} bougies{'  KO ' + err if err else ''}")

    for y, m in [(2021, 1), (2021, 6), (2021, 12), (2022, 6), (2023, 6)]:
        r, err = ok("public/get_volatility_index_data",
                    {"currency": "BTC", "start_timestamp": ms(y, m), "end_timestamp": ms(y, m + 1),
                     "resolution": "1D"})
        n = len(r["data"]) if r and r.get("data") else 0
        print(f"  DVOL {y}-{m:02d} : {n} points{'  KO ' + err if err else ''}")

    for y, m in [(2021, 1), (2022, 1), (2023, 1), (2024, 1), (2026, 1)]:
        r, err = ok("public/get_last_trades_by_currency_and_time",
                    {"currency": "BTC", "kind": "option", "start_timestamp": ms(y, m),
                     "end_timestamp": ms(y, m + 1), "count": 5})
        n = len(r["trades"]) if r and r.get("trades") else 0
        print(f"  trades option BTC {y}-{m:02d} : {n} (echantillon de 5 max){'  KO ' + err if err else ''}")

    r, err = ok("public/get_historical_volatility", {"currency": "BTC"})
    if r:
        print(f"  historical_volatility : {len(r)} points, "
              f"de {time.strftime('%Y-%m-%d %H:%M', time.gmtime(r[0][0]/1000))} "
              f"a {time.strftime('%Y-%m-%d %H:%M', time.gmtime(r[-1][0]/1000))}, "
              f"valeurs ~{r[0][1]:.1f}..{r[-1][1]:.1f}")
    r2, err2 = ok("public/get_last_settlements_by_instrument",
                  {"instrument_name": name, "type": "delivery", "count": 2})
    print(f"  get_last_settlements_by_instrument : {'OK' if r2 else 'KO ' + str(err2)}")


main()
