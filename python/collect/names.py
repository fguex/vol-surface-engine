"""Noms d'instruments Deribit : parsing partage entre le collecteur et les jobs.

Pourquoi un module separe : le collecteur importe `websockets`, les jobs de
chargement non. Mettre le parsing ailleurs evite de tirer une dependance
reseau dans un job qui ne fait que lire des fichiers.
"""

from __future__ import annotations

from datetime import date

# Le mois est resolu par table et non par strptime : %b depend de la locale du
# process, et une locale non anglaise ferait echouer la selection en silence.
MONTHS = {m: i for i, m in enumerate(
    "JAN FEB MAR APR MAY JUN JUL AUG SEP OCT NOV DEC".split(), start=1)}


def parse_option_name(name: str) -> tuple[date, float, bool] | None:
    """BTC-24SEP27-104000-P -> (2027-09-24, 104000.0, False). None si non parsable."""
    parts = name.split("-")
    if len(parts) != 4 or parts[3] not in ("C", "P"):
        return None
    jour_mois = parts[1]
    try:
        jour, mois, annee = int(jour_mois[:-5]), MONTHS[jour_mois[-5:-2]], int(jour_mois[-2:])
        strike = float(parts[2])
        return date(2000 + annee, mois, jour), strike, parts[3] == "C"
    except (KeyError, ValueError):
        return None


def instrument_meta(name: str, provider: str = "deribit") -> dict | None:
    """Decrit un instrument a partir de son nom, pour la dimension.

    Renvoie None pour ce qui n'est pas un instrument negociable (un indice, par
    exemple). C'est le seul endroit qui sait lire un nom Deribit : le collecteur
    s'en sert pour la bande ATM, le chargement pour remplir la dimension.
    """
    parts = name.split("-")
    devise = parts[0]
    if len(parts) == 2 and parts[1] in ("PERPETUAL", "PERP"):
        return {"provider": provider, "underlying": devise, "instrument_name": name,
                "kind": "perpetual", "expiry": None, "strike": None, "option_type": None,
                "is_inverse": True, "contract_size": 1.0, "quote_currency": devise}
    info = parse_option_name(name)
    if info is None:
        return None
    expiry, strike, is_call = info
    return {"provider": provider, "underlying": devise, "instrument_name": name,
            "kind": "option", "expiry": expiry, "strike": strike,
            "option_type": "C" if is_call else "P", "is_inverse": True,
            "contract_size": 1.0, "quote_currency": devise}
