# vol-surface-engine

C++20 volatility surface calibration engine (SSVI → Dupire → Crank-Nicolson PDE).

Donnees : options inverses BTC/ETH et perpetuel, collectees depuis Deribit par
WebSocket. Le brut est archive en fichiers zstd rejouables (bronze) ; la base
TimescaleDB ne contient que des etats echantillonnes et curables.

La chaine SPX / CBOE (source openbb-cboe), premiere version de l'ingestion, a ete
abandonnee le 11 octobre 2026 : code supprime, tables retirees par la migration
`sql/004_retrait_cboe.sql`. Les documents de `docs/` qui en parlent sont conserves
comme trace de la phase 1.
