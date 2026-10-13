"""Inventaire des donnees publiques Deribit : WS channels + REST (snapshot + historique).

Sonde jetable. Mesure, ne devine pas : pour chaque methode on affiche
  OK/KO, les champs du premier element, et la profondeur d'historique disponible.
Usage : PYTHONPATH=. .venv/bin/python -m python.collect._probe_rest
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


def call(method: str, params: dict | None = None, timeout: int = 25) -> dict:
    url = f"{BASE}/{method}"
    if params:
        url += "?" + "&".join(f"{k}={urllib.parse.quote(str(v))}" for k, v in params.items())
    req = urllib.request.Request(url, headers={"User-Agent": "vse-probe/1.0"})
    with urllib.request.urlopen(req, timeout=timeout, context=CTX) as r:
        return json.loads(r.read().decode())


def describe(res: dict, limit_keys: int = 40) -> str:
    r = res.get("result")
    if isinstance(r, list):
        head = r[0] if r else None
        n = len(r)
    else:
        head, n = r, 1
    if isinstance(head, dict):
        keys = sorted(head.keys())
        shown = ", ".join(keys[:limit_keys]) + (" ..." if len(keys) > limit_keys else "")
        return f"{n} elem. | champs: {shown}"
    if isinstance(head, list):
        return f"{n} elem. | listes de {len(head)} : {', '.join(str(x) for x in head[:8])}"
    return f"{n} elem. | {head}"


def probe(name: str, method: str, params: dict | None = None, unwrap: str | None = None) -> None:
    try:
        res = call(method, params)
    except urllib.error.HTTPError as e:
        body = e.read().decode()[:160]
        print(f"KO  {method:52s} {body}")
        return
    except Exception as e:  # noqa: BLE001
        print(f"KO  {method:52s} {type(e).__name__}: {e}")
        return
    if "error" in res:
        print(f"KO  {method:52s} {res['error'].get('message')}")
        return
    out = res
    if unwrap and isinstance(res.get("result"), dict):
        out = {"result": res["result"].get(unwrap)}
    print(f"OK  {method:52s} {describe(out)}")


def main() -> None:
    now_ms = int(time.time() * 1000)
    d0 = now_ms - 2 * 86400 * 1000

    print("== SPECS & LISTES ==")
    probe("instruments", "public/get_instruments", {"currency": "BTC", "kind": "option", "expired": "false"})
    probe("instrument", "public/get_instrument", {"instrument_name": "BTC-PERPETUAL"})
    probe("currencies", "public/get_currencies")
    probe("expirations", "public/get_expirations", {"currency": "BTC", "kind": "option"})
    probe("index_names", "public/get_index_price_names")

    print("\n== SNAPSHOTS (un appel = toute la chaine) ==")
    probe("book_summary", "public/get_book_summary_by_currency", {"currency": "BTC", "kind": "option"}, unwrap=None)
    probe("book_summary_perp", "public/get_book_summary_by_currency", {"currency": "BTC", "kind": "future"})
    probe("ticker", "public/ticker", {"instrument_name": "BTC-PERPETUAL"})
    probe("order_book", "public/get_order_book", {"instrument_name": "BTC-PERPETUAL", "depth": 3})
    probe("index_price", "public/get_index_price", {"index_name": "btc_usd"})

    print("\n== HISTORIQUE (backfill) ==")
    probe("trades_cur", "public/get_last_trades_by_currency", {"currency": "BTC", "kind": "option", "count": 5})
    probe("trades_time", "public/get_last_trades_by_currency_and_time",
          {"currency": "BTC", "kind": "option", "start_timestamp": d0, "end_timestamp": now_ms, "count": 5})
    probe("trades_instr", "public/get_last_trades_by_instrument", {"instrument_name": "BTC-PERPETUAL", "count": 5})
    probe("chart_data", "public/get_tradingview_chart_data",
          {"instrument_name": "BTC-PERPETUAL", "start_timestamp": now_ms - 30 * 86400 * 1000,
           "end_timestamp": now_ms, "resolution": "1D"})
    probe("dvol", "public/get_volatility_index_data",
          {"currency": "BTC", "start_timestamp": 1609459200000, "end_timestamp": now_ms, "resolution": "1D"})
    probe("hist_vol", "public/get_historical_volatility", {"currency": "BTC"})
    probe("delivery_prices", "public/get_delivery_prices", {"index_name": "btc_usd", "count": 5})
    probe("settlement_cur", "public/get_settlement_history_by_currency",
          {"currency": "BTC", "type": "delivery", "count": 5})
    probe("settlement_instr", "public/get_settlement_history_by_instrument",
          {"instrument_name": "BTC-27DEC24-100000-C", "count": 2})
    probe("funding_hist", "public/get_funding_rate_history",
          {"instrument_name": "BTC-PERPETUAL", "start_timestamp": now_ms - 5 * 86400 * 1000,
           "end_timestamp": now_ms})
    probe("funding_val", "public/get_funding_rate_value",
          {"instrument_name": "BTC-PERPETUAL", "start_timestamp": now_ms - 86400 * 1000, "end_timestamp": now_ms})
    probe("apr_hist", "public/get_apr_history", {"currency": "BTC", "count": 3})
    probe("mark_hist", "public/get_mark_price_history",
          {"instrument_name": "BTC-PERPETUAL", "start_timestamp": d0, "end_timestamp": now_ms})
    probe("block_trade", "public/get_block_trade", {"count": 3})
    probe("last_settle", "public/get_last_settlements_by_currency",
          {"currency": "BTC", "type": "delivery", "count": 3})

    print("\n== PROFONDEUR D'HISTORIQUE ==")
    try:
        r = call("public/get_volatility_index_data",
                 {"currency": "BTC", "start_timestamp": 1577836800000,  # 2020-01-01
                  "end_timestamp": 1609459200000, "resolution": "1D"})["result"]
        pts = r.get("data", [])
        print(f"DVOL BTC : {len(pts)} points des 2020-01-01 -> 2021-01-01, "
              f"premier = {time.strftime('%Y-%m-%d', time.gmtime(pts[0][0] / 1000)) if pts else 'aucun'}")
    except Exception as e:  # noqa: BLE001
        print(f"DVOL BTC 2020 : {type(e).__name__} {e}")
    try:
        r = call("public/get_last_trades_by_currency_and_time",
                 {"currency": "BTC", "kind": "option", "start_timestamp": 1577836800000,
                  "end_timestamp": 1577923200000, "count": 5})["result"]
        tr = r.get("trades", [])
        print(f"trades option BTC en janv. 2020 : {len(tr)} "
              f"(premier = {time.strftime('%Y-%m-%d %H:%M', time.gmtime(tr[0]['timestamp'] / 1000)) if tr else 'aucun'})")
    except Exception as e:  # noqa: BLE001
        print(f"trades janv. 2020 : {type(e).__name__} {e}")
    try:
        r = call("public/get_tradingview_chart_data",
                 {"instrument_name": "BTC-PERPETUAL", "start_timestamp": 1500000000000,
                  "end_timestamp": 1520000000000, "resolution": "1D"})["result"]
        print(f"candles BTC-PERPETUAL 2017-2018 : {len(r.get('ticks', []))} bougies "
              f"(premier = {time.strftime('%Y-%m-%d', time.gmtime(r['ticks'][0] / 1000)) if r.get('ticks') else 'aucun'})")
    except Exception as e:  # noqa: BLE001
        print(f"candles 2017 : {type(e).__name__} {e}")


main()
