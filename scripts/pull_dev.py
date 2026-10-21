#!/usr/bin/env python3
"""Tire un extrait de la base du mini pour travailler en local.

C'est la tranche verticale du poste de dev : le tunnel SSH fait office de fil,
et la tranche traverse tout ce qui compte — connexion, requete, fichier,
manifeste, relecture locale. Si elle passe, l'environnement est bon.

    scripts/pull_dev.py                  # 2 jours
    scripts/pull_dev.py --jours 7        # une semaine
    scripts/pull_dev.py --sans-marches   # seulement les instantanes gold

Prerequis : scripts/db-tunnel.sh start (le tunnel ecoute 127.0.0.1:15432).

Sortie, dans data/dev/ (ignore par git) :
    marches.parquet      la matiere de la surface : une ligne par option et par
                         tranche de 5 min, avec l'IV de marque, le bid/ask d'IV
                         quand il existe, et les greeks
    perpetuel.parquet    l'indice et le funding, pour le forward
    v_surface.parquet    les instantanes gold tels quels (petits)
    manifeste.json       les requetes exactes, les comptes, l'horodatage, le commit
"""
from __future__ import annotations

import argparse
import datetime as dt
from decimal import Decimal
import json
import os
import subprocess
import sys
import time

import pandas as pd
import psycopg

HOTE = "127.0.0.1"
PORT = 15432
BASE = "volsurface"
USER = "vse"

# (requete, filtree_par_la_fenetre). Les vues gold sont des instantanes : pas de
# filtre de temps, on les prend telles quelles.
SOURCES: dict[str, tuple[str, bool]] = {
    "marches": (
        """
        select m.observed_at, i.instrument_name, i.underlying, i.expiry,
               i.option_type, i.strike,
               m.mark_iv, m.mark_price, m.bid_iv, m.ask_iv,
               m.delta, m.vega, m.open_interest
          from silver.option_mark m
          join silver.instrument i using (instrument_sk)
         where m.observed_at >= now() - make_interval(days => %(jours)s)
           and i.valid_to is null
        """,
        True,
    ),
    "perpetuel": (
        """
        select p.observed_at, i.instrument_name, p.index_price, p.mark_price,
               p.current_funding, p.open_interest
          from silver.perp_tick p
          join silver.instrument i using (instrument_sk)
         where p.observed_at >= now() - make_interval(days => %(jours)s)
        """,
        True,
    ),
    "v_surface": ("select * from gold.v_surface", False),
    "v_atm_terme": ("select * from gold.v_atm_terme", False),
    "v_spot": ("select * from gold.v_spot", False),
}


def mot_de_passe() -> str:
    """Le mot de passe est dans .env (jamais dans le depot), sinon dans l'environnement."""
    env = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env")
    if os.path.exists(env):
        for ligne in open(env, encoding="utf-8"):
            if ligne.startswith("POSTGRES_PASSWORD="):
                return ligne.split("=", 1)[1].strip().strip('"').strip("'")
    valeur = os.environ.get("POSTGRES_PASSWORD", "")
    if not valeur:
        sys.exit("mot de passe introuvable : ni .env ni POSTGRES_PASSWORD")
    return valeur


def commit_local(repo: str) -> str:
    try:
        return subprocess.run(
            ["git", "-C", repo, "rev-parse", "--short", "HEAD"],
            capture_output=True, text=True, timeout=10,
        ).stdout.strip() or "inconnu"
    except Exception:
        return "inconnu"


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--jours", type=int, default=2, help="fenetre en jours (defaut 2)")
    ap.add_argument("--sortie", default="data/dev", help="dossier de sortie")
    ap.add_argument("--port", type=int, default=PORT)
    ap.add_argument("--sans-marches", action="store_true", help="sauter les grosses tables silver")
    args = ap.parse_args()

    repo = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    sortie = args.sortie if os.path.isabs(args.sortie) else os.path.join(repo, args.sortie)
    os.makedirs(sortie, exist_ok=True)

    choisis = [n for n in SOURCES if not (args.sans_marches and n in ("marches", "perpetuel"))]

    try:
        conn = psycopg.connect(
            host=HOTE, port=args.port, dbname=BASE, user=USER,
            password=mot_de_passe(), connect_timeout=8,
        )
    except Exception as exc:
        message = str(exc)
        if "authentication" in message or "password" in message.lower():
            sys.exit(
                "le tunnel repond, mais le mot de passe est refuse.\n"
                "  le .env local doit porter le POSTGRES_PASSWORD du mini :\n"
                "  scp macmini-ai@macmini-ai.local:~/vol-surface-engine/.env .env\n"
                "  (%s)" % message
            )
        sys.exit(
            "connexion impossible sur %s:%s — le tunnel est-il demarre ?\n"
            "  scripts/db-tunnel.sh start\n  (%s)" % (HOTE, args.port, message)
        )

    manifeste: dict = {
        "genere_le": dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds"),
        "fenetre_jours": args.jours,
        "connexion": f"{USER}@{HOTE}:{args.port}/{BASE}",
        "commit_local": commit_local(repo),
        "sources": {},
    }

    with conn, conn.cursor() as cur:
        for nom in choisis:
            requete, filtree = SOURCES[nom]
            sql = requete
            params = {"jours": args.jours} if filtree else {}
            t0 = time.time()
            cur.execute(sql, params)
            colonnes = [d.name for d in cur.description]
            lignes = cur.fetchall()
            df = pd.DataFrame(lignes, columns=colonnes)

            # psycopg rend les colonnes numeric en Decimal ; pandas les garde en objets et le
            # premier calcul casse (log, division, moyenne). On convertit ici, une fois.
            for colonne in df.columns:
                if df[colonne].dtype == object:
                    valeurs = df[colonne].dropna()
                    if len(valeurs) and isinstance(valeurs.iloc[0], Decimal):
                        df[colonne] = df[colonne].astype("float64")
            chemin = os.path.join(sortie, f"{nom}.parquet")
            df.to_parquet(chemin, index=False)
            taille = os.path.getsize(chemin) / 1024 / 1024
            duree = time.time() - t0
            manifeste["sources"][nom] = {
                "fichier": os.path.relpath(chemin, repo),
                "lignes": int(len(df)),
                "colonnes": colonnes,
                "octets": os.path.getsize(chemin),
                "requete": " ".join(sql.split()),
                "secondes": round(duree, 1),
            }
            print(f"  {nom:12} {len(df):>8} lignes  {taille:6.1f} Mo  {duree:5.1f} s")

    chemin_manifeste = os.path.join(sortie, "manifeste.json")
    with open(chemin_manifeste, "w", encoding="utf-8") as fh:
        json.dump(manifeste, fh, indent=2, ensure_ascii=False)

    print(f"\nmanifeste : {os.path.relpath(chemin_manifeste, repo)}")
    print(f"commit local : {manifeste['commit_local']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
