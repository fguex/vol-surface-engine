# vol-surface-engine

C++20 volatility surface calibration engine (SSVI → Dupire → Crank-Nicolson PDE).

Donnees : options inverses BTC/ETH et perpetuel, collectees depuis Deribit par
WebSocket. Le brut est archive en fichiers zstd rejouables (bronze) ; la base
TimescaleDB ne contient que des etats echantillonnes et curables.

La chaine SPX / CBOE (source openbb-cboe), premiere version de l'ingestion, a ete
abandonnee le 11 octobre 2026 : code supprime, tables retirees par la migration
`sql/004_retrait_cboe.sql`. Les documents de `docs/` qui en parlent sont conserves
comme trace de la phase 1.

## Les trois couches

Le decoupage bronze / silver / gold n'est pas une convention de nommage : c'est la
structure de la base, portee par les schemas (migration `sql/008_couches.sql`). Un outil
graphique les affiche donc en dossiers, sans qu'on ait a connaitre le vocabulaire.

    bronze    les messages bruts de Deribit : des FICHIERS zstd sous
              data/deribit/bronze/, ~18 Mo/h, rejouables a volonte. Aucune table — le
              schema `bronze` existe pour que cette absence soit explicite.
    silver    ce que le chargement du bronze produit (python/jobs/load_marks.py) : la
              dimension `instrument` (SCD2) et trois tables de faits — `option_mark`,
              `perp_tick`, `vol_index` — echantillonnees en tranches de 5 min.
    gold      les vues de lecture, sans stockage : `v_spot`, `v_surface`, `v_atm_terme`,
              `v_perp_funding`. C'est la couche que l'on interroge, depuis psql, DBeaver
              ou Python.

`schema_migrations` vit dans `public` : c'est le registre du migrateur, pas une couche de
donnees, et il doit avoir un emplacement fixe.

Le role `vse` recoit `search_path = silver, gold, public`. Une requete ecrite a la main
(`select count(*) from option_mark`) se resout donc sans prefixe, ce qui evite de casser
une session ouverte au moment du changement. Le code du depot, lui, qualifie les noms : ce
search_path est un confort pour l'exploration, pas une dispense.
