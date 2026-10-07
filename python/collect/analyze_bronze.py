"""Rapport de qualite d'un run bronze Deribit.

python -m python.collect.analyze_bronze data/deribit_spike

Repond a 5 questions :
1. Combien ca pese ?          -> taux de compression reel, extrapolation GB/jour
2. Qu'a-t-on recu ?           -> messages par canal, instruments couverts
3. Quelle latence ?           -> recv_time - exchange timestamp (p50/p95/p99)
4. Y a-t-il des trous ?       -> deconnexions (events.jsonl) + silences par flux
5. Est-ce exploitable ?       -> silver as-of : surface bid/ask reconstruite a la fin
"""

from __future__ import annotations

import io
import json
import statistics
import sys
from collections import Counter, defaultdict
from pathlib import Path

import zstandard


def iter_bronze(files):
    d = zstandard.ZstdDecompressor()
    for f in files:
        with open(f, "rb") as fh, d.stream_reader(fh) as r:
            for line in io.TextIOWrapper(r, encoding="utf-8"):
                yield json.loads(line)


def pct(xs, q):
    xs = sorted(xs)
    return xs[min(len(xs) - 1, int(q * len(xs)))]


def main(root: Path) -> None:
    files = sorted((root / "bronze").rglob("*.jsonl.zst"))
    partial = sorted((root / "bronze").rglob("*.part"))
    events = [json.loads(line) for line in open(root / "events.jsonl")]
    # debuts de session : le 1er message par instrument apres une (re)connexion
    # est un snapshot d'etat, pas un evenement -> exclu des stats de latence
    connects = sorted(e["t_ns"] for e in events if e["event"] == "connected")
    comp = sum(f.stat().st_size for f in files)

    n = 0
    raw_bytes = 0
    by_kind = Counter()
    lat_ms = []
    n_snapshot = 0
    session = -1
    seen_in_session: set[str] = set()
    first_ns = last_ns = None
    last_seen = {}                      # instrument -> recv_ns
    max_silence = defaultdict(float)    # instrument -> plus long silence (s)
    book = {}                           # instrument -> (bid, ask, ts)  (silver as-of)
    index = {}
    trades = 0

    for rec in iter_bronze(files):
        n += 1
        r = rec["r"]
        first_ns = r if first_ns is None else first_ns
        last_ns = r
        p = rec["m"]["params"]
        ch, data = p["channel"], p["data"]
        kind = ch.split(".")[0]
        by_kind[kind] += 1
        raw_bytes += len(json.dumps(rec["m"]))
        if kind == "quote":
            inst = data["instrument_name"]
            while session + 1 < len(connects) and r >= connects[session + 1]:
                session += 1
                seen_in_session = set()
            if inst in seen_in_session:
                lat_ms.append(r / 1e6 - data["timestamp"])
            else:
                seen_in_session.add(inst)
                n_snapshot += 1
            if inst in last_seen:
                max_silence[inst] = max(max_silence[inst], (r - last_seen[inst]) / 1e9)
            last_seen[inst] = r
            book[inst] = (data.get("best_bid_price"), data.get("best_ask_price"), data["timestamp"])
        elif kind == "trades":
            trades += len(data)
        elif kind == "deribit_price_index":
            index[data["index_name"]] = data["price"]

    dur = (last_ns - first_ns) / 1e9 if n else 0
    print("=" * 64)
    print(f"Run : {root}   fichiers={len(files)}  .part orphelins={len(partial)}")
    print(f"Duree couverte : {dur/60:.1f} min   messages={n:,}  ({n/dur:.0f} msg/s)")
    print()
    print("1. VOLUME")
    print(f"   JSON brut        : {raw_bytes/1e6:8.1f} MB")
    print(f"   bronze zstd      : {comp/1e6:8.1f} MB   ratio x{raw_bytes/comp:.1f}")
    print(f"   extrapolation    : {comp/dur*86400/1e9:.2f} GB/jour  ->  {comp/dur*86400*365/1e9:.0f} GB/an")
    print()
    print("2. CONTENU")
    for k, v in by_kind.most_common():
        print(f"   {k:22s} {v:>9,}")
    print(f"   trades d'options : {trades}")
    n_inst = next((e["n_instruments"] for e in events if e["event"] == "connected"), None)
    print(f"   instruments abonnes={n_inst}  ayant cote au moins 1x={len(last_seen)}")
    print()
    print("3. LATENCE recv - timestamp exchange (ms), hors snapshots de (re)connexion")
    print(f"   snapshots exclus={n_snapshot:,}  updates mesurees={len(lat_ms):,}")
    if lat_ms:
        print(f"   p50={pct(lat_ms,.5):.0f}  p95={pct(lat_ms,.95):.0f}  p99={pct(lat_ms,.99):.0f}  "
              f"min={min(lat_ms):.0f}  max={max(lat_ms):.0f}")
        neg = sum(1 for x in lat_ms if x < 0)
        if neg:
            print(f"   ATTENTION {neg} latences negatives -> horloge locale en avance (NTP ?)")
    print()
    print("4. TROUS")
    for e in events:
        if e["event"] in ("disconnected", "chaos_close", "subscribe_error", "connected", "subscribed"):
            t = (e["t_ns"] - first_ns) / 1e9 if first_ns else 0
            extra = {k: v for k, v in e.items() if k not in ("t_ns", "event")}
            print(f"   t+{t:7.1f}s  {e['event']:16s} {extra}")
    if max_silence:
        sil = sorted(max_silence.values())
        print(f"   silence max par instrument : median={statistics.median(sil):.0f}s  "
              f"p90={pct(sil,.9):.0f}s  max={sil[-1]:.0f}s")
        print("   (un long silence n'est pas un trou : une option illiquide ne bouge simplement pas)")
    print()
    print("5. SILVER AS-OF (etat du carnet a la fin du run)")
    two = {i: v for i, v in book.items() if v[0] and v[1]}
    for cur in ("BTC", "ETH"):
        inst = [i for i in two if i.startswith(cur)]
        exp = Counter(i.split("-")[1] for i in inst)
        print(f"   {cur}: {len(inst)} options cotees des 2 cotes sur {len(exp)} echeances   "
              f"index={index.get(cur.lower()+'_usd')}")
    print("=" * 64)


if __name__ == "__main__":
    main(Path(sys.argv[1] if len(sys.argv) > 1 else "data/deribit_spike"))
