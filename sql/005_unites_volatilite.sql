-- 005 : unites des volatilites, et garde-fou de plausibilite.
--
-- A APPLIQUER APRES LE RECHARGEMENT du bronze. La contrainte ajoutee ici refuse
-- les lignes ecrites avec l'ancienne conversion (voir plus bas) : sur une base
-- qui n'a pas encore ete rechargee, cette migration echouera, et c'est voulu —
-- mieux vaut un echec bruyant qu'une surface calibree sur des unites melangees.
--
-- Deribit publie l'IV dans DEUX unites selon le canal :
--     markprice.options.*   iv       = FRACTION     (0.3557 = 35,57 %)
--     ticker.*.100ms        mark_iv, bid_iv, ask_iv = POURCENTAGE  (35.61)
-- Les deux alimentaient la meme colonne option_mark.mark_iv : un strike d'aile
-- valait 0,36 % de vol et un strike ATM 35,61 % dans la meme colonne, sans que
-- rien ne le signale (constate le 14 octobre 2026, corrige dans load_marks.py).
--
-- La colonne est desormais en POURCENTAGE partout : convention du ticker, de
-- DVOL, et de ce que Deribit affiche dans son interface.

COMMENT ON COLUMN option_mark.mark_iv IS
    'Volatilite implicite en POURCENTAGE (35.61 = 35,61 %). Normalisee depuis markprice.options.iv (fraction) et ticker.mark_iv (pourcentage).';
COMMENT ON COLUMN option_mark.bid_iv IS
    'Volatilite implicite a l''achat, en POURCENTAGE.';
COMMENT ON COLUMN option_mark.ask_iv IS
    'Volatilite implicite a la vente, en POURCENTAGE.';
COMMENT ON COLUMN option_mark.mark_price IS
    'Prix marque en unite du sous-jacent (BTC pour BTC-*, ETH pour ETH-*) : options inverses.';
COMMENT ON COLUMN option_mark.index_price IS
    'Indice Deribit du sous-jacent, en USD.';
COMMENT ON COLUMN option_mark.underlying_price IS
    'Prix du sous-jacent composite, en USD (peut differer legerement de l''indice).';
COMMENT ON COLUMN perp_tick.current_funding IS
    'Taux de financement courant, en fraction par 8 h (0.0000453 = 0,453 point de base).';
COMMENT ON COLUMN vol_index.volatility IS
    'DVOL, l''indice de volatilite Deribit, en POURCENTAGE (38.10 = 38,10 %).';

-- Le garde-fou qui aurait attrape le bug : une IV de BTC/ETH sous 1 % n'existe
-- pas, une IV au-dessus de 500 % non plus, et une valeur dans ]0, 1[ signale une
-- fraction ecrite dans une colonne attendue en pourcentage.
--
-- Deux valeurs SENTINELLES sont tolerees explicitement, parce que Deribit les
-- publie vraiment (constate sur 65 000 lignes : bid_iv min 0.0, ask_iv max
-- 999.0) : 0 = pas de prix, 999 = pas de carnet de ce cote. Un sentinel ne doit
-- jamais faire echouer un lot de plusieurs heures — le rechargement suit une
-- transaction par fichier, donc un echec ferait perdre ce fichier.
ALTER TABLE option_mark ADD CONSTRAINT option_mark_mark_iv_plausible
    CHECK (mark_iv IS NULL OR mark_iv IN (0, 999)
           OR (mark_iv >= 1 AND mark_iv <= 500));
ALTER TABLE option_mark ADD CONSTRAINT option_mark_bid_ask_iv_plausible
    CHECK ((bid_iv IS NULL OR bid_iv IN (0, 999) OR (bid_iv >= 1 AND bid_iv <= 500))
       AND (ask_iv IS NULL OR ask_iv IN (0, 999) OR (ask_iv >= 1 AND ask_iv <= 500)));
ALTER TABLE vol_index ADD CONSTRAINT vol_index_plausible
    CHECK (volatility IN (0, 999) OR (volatility >= 1 AND volatility <= 500));

COMMENT ON COLUMN option_mark.bid_iv IS
    'Volatilite implicite a l''achat, en POURCENTAGE. 0 = pas de prix, 999 = valeur sentinelle Deribit (pas de carnet).';
