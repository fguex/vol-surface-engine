"""Trace la surface depuis la base : le smile et la structure par terme.

Lit les vues `v_surface` et `v_atm_terme` (migrations 006/007) et ecrit un PNG.
Aucune dependance a pandas : psycopg rend les lignes, matplotlib les dessine.

    # depuis le MacBook, avec le tunnel ouvert :
    #   ssh -N -L 15432:127.0.0.1:5432 macmini-ai@macmini-ai.local
    set -a; . .env; set +a          # ou exporter les PGHOST/PGPORT/... a la main
    ./.venv/bin/python scripts/plot_surface.py --out docs/figures/surface.png

    # sur le mini :
    set -a; . ./.env; set +a
    export PGHOST=127.0.0.1
    .venv/bin/python scripts/plot_surface.py
"""

from __future__ import annotations

import argparse
import datetime as dt
import os
from pathlib import Path

import matplotlib

matplotlib.use("Agg")            # pas de fenetre : on ecrit un fichier
import matplotlib.pyplot as plt   # noqa: E402
import psycopg                    # noqa: E402

COULEURS = ["#1f77b4", "#d62728", "#2ca02c", "#9467bd", "#ff7f0e", "#8c564b"]


def lire(conn, sql: str, params: tuple = ()):
    with conn.cursor() as cur:
        cur.execute(sql, params)
        colonnes = [c.name for c in cur.description]
        return [dict(zip(colonnes, ligne)) for ligne in cur.fetchall()]


def smile(conn, sous_jacent: str, n_echeances: int):
    """IV en fonction de k = ln(K/F), une courbe par echeance."""
    return lire(conn, """
        SELECT expiry, t_annees, k_logm, iv_pct, option_type
          FROM v_surface
         WHERE underlying = %s AND expiry > current_date
           AND expiry IN (SELECT DISTINCT expiry FROM v_atm_terme
                           WHERE underlying = %s AND expiry > current_date
                           ORDER BY expiry LIMIT %s)
         ORDER BY expiry, k_logm
    """, (sous_jacent, sous_jacent, n_echeances))


def terme(conn):
    return lire(conn, """
        SELECT underlying, expiry, t_annees, iv_atm_pct, strike_le_plus_proche, index_usd
          FROM v_atm_terme ORDER BY underlying, t_annees
    """)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--out", type=Path, default=Path("docs/figures/surface.png"))
    p.add_argument("--sous-jacent", default="BTC")
    p.add_argument("--echeances", type=int, default=4,
                   help="nombre de premieres echeances tracees dans le smile")
    p.add_argument("--dsn", default=None,
                   help="chaine de connexion ; sinon les variables PG* de l'environnement")
    a = p.parse_args()

    # psycopg3 : soit une chaine de connexion, soit les parametres libpq en
    # arguments nommes (le mot de passe vient de PGPASSWORD, jamais du code).
    if a.dsn:
        conn = psycopg.connect(a.dsn)
    else:
        conn = psycopg.connect(
            host=os.environ.get("PGHOST", "127.0.0.1"),
            port=os.environ.get("PGPORT", "5432"),
            user=os.environ.get("PGUSER", "vse"),
            dbname=os.environ.get("PGDATABASE", "volsurface"),
        )

    with conn:
        lignes_smile = smile(conn, a.sous_jacent, a.echeances)
        lignes_terme = terme(conn)

    if not lignes_smile and not lignes_terme:
        print("aucune donnee dans les vues : rien a tracer")
        return 1

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 5.5))
    fig.suptitle("Deribit — surface d'options inverses  (vues v_surface / v_atm_terme)",
                 fontsize=13)

    # --- smile ---------------------------------------------------------------
    echeances = []
    for ligne in lignes_smile:                      # ordre d'apparition = ordre des echeances
        if ligne["expiry"] not in [e[0] for e in echeances]:
            echeances.append((ligne["expiry"], ligne["t_annees"]))
    for i, (expiry, t) in enumerate(echeances):
        pts = [(l["k_logm"], l["iv_pct"], l["option_type"])
               for l in lignes_smile if l["expiry"] == expiry]
        couleurs = COULEURS[i % len(COULEURS)]
        ax1.plot([x for x, _y, _o in pts], [y for _x, y, _o in pts], "-",
                 color=couleurs, lw=1.2, alpha=0.85,
                 label=f"{expiry:%d %b}  (T={float(t):.3f} an)")
        for lettre, marqueur in (("P", "o"), ("C", "^")):
            xs = [x for x, _y, o in pts if o == lettre]
            ys = [y for _x, y, o in pts if o == lettre]
            ax1.plot(xs, ys, marqueur, color=couleurs, ms=4, mfc="white", mew=1.1)
    ax1.axvline(0.0, color="black", lw=0.8, ls=":", alpha=0.6)
    ax1.set_xlabel("k = ln(K / F)")
    ax1.set_ylabel("volatilite implicite (%)")
    ax1.set_title(f"Smile, {a.sous_jacent} (ronds = puts, triangles = calls)")
    ax1.grid(alpha=0.3)
    ax1.legend(fontsize=8)

    # --- structure par terme -------------------------------------------------
    for i, devise in enumerate(sorted({l["underlying"] for l in lignes_terme})):
        pts = sorted([(float(l["t_annees"]), l["iv_atm_pct"])
                      for l in lignes_terme if l["underlying"] == devise])
        if not pts:
            continue
        ax2.plot([x for x, _y in pts], [y for _x, y in pts], "-o",
                 color=COULEURS[i % len(COULEURS)], ms=3.5, lw=1.2, label=devise)
    ax2.set_xscale("log")
    ax2.set_xlabel("maturite (annees, echelle log)")
    ax2.set_ylabel("vol ATM (%)")
    ax2.set_title("Structure par terme de la vol ATM\n(strike le plus proche du spot)")
    ax2.grid(alpha=0.3, which="both")
    ax2.legend(fontsize=9)

    a.out.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout(rect=(0, 0, 1, 0.96))
    fig.savefig(a.out, dpi=140)
    print(f"ecrit : {a.out.resolve()}")
    print(f"  smile          : {len(lignes_smile)} points, {len(echeances)} echeances")
    print(f"  structure terme: {len(lignes_terme)} points")
    print(f"  ecrit a        : {dt.datetime.now(dt.timezone.utc):%Y-%m-%d %H:%M} UTC")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
