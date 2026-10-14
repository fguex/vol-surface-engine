-- 003 : Deribit dans la base.
--
-- Principe (docs/data_architecture_medallion.md) : le brut reste dans le bronze
-- (fichiers zstd, rejouables). La base ne contient que ce qu'on interroge : des
-- etats echantillonnes de la chaine d'options et du perpetuel.
--
-- Deux natures d'objets, deux traitements :
--   - DIMENSION `instrument` : l'instrument lui-meme. Il nait, il vit, il expire.
--     SCD2 : valid_from / valid_to, jamais d'UPDATE destructif, on ferme une
--     ligne et on en ouvre une autre. C'est le seul endroit ou valid_from/to
--     a un sens (un fait, lui, est un point dans le temps).
--   - FAITS : option_mark, perp_tick, vol_index. Hypertables, append-only,
--     cle (instrument_sk, observed_at), donc un rejeu du bronze ne cree
--     jamais de doublon (ON CONFLICT DO NOTHING cote chargeur).
--
-- Temps : `observed_at` = horodatage du FOURNISSEUR (le marche), pas l'heure
-- d'ecriture. Rejouer le meme bronze produit les memes lignes au bit pres.

-- ---------------------------------------------------------------------------
-- Dimension : instruments
-- ---------------------------------------------------------------------------

CREATE TABLE instrument (
    instrument_sk  bigserial PRIMARY KEY,
    provider       text        NOT NULL DEFAULT 'deribit',
    underlying     text        NOT NULL,             -- BTC, ETH
    instrument_name text       NOT NULL,             -- BTC-24SEP27-104000-P
    kind           text        NOT NULL,             -- option | perpetual | future
    expiry         date,                             -- NULL pour un perpetuel
    strike         numeric(18,6),                    -- NULL pour un perpetuel
    option_type    text,                             -- C | P
    is_inverse     boolean     NOT NULL DEFAULT true,-- Deribit : prime en BTC/ETH
    contract_size  numeric(18,8),
    quote_currency text,
    valid_from     timestamptz NOT NULL DEFAULT now(),
    valid_to       timestamptz,                      -- NULL = version courante
    recorded_at    timestamptz NOT NULL DEFAULT now(),

    CONSTRAINT instrument_kind_connu CHECK (kind IN ('option', 'perpetual', 'future')),
    CONSTRAINT instrument_option_complete CHECK (
        (kind = 'option' AND expiry IS NOT NULL AND strike IS NOT NULL
                        AND option_type IN ('C', 'P'))
        OR (kind <> 'option' AND expiry IS NULL AND strike IS NULL AND option_type IS NULL)
    ),
    CONSTRAINT instrument_periode_valide CHECK (valid_to IS NULL OR valid_to > valid_from)
);

-- Une seule version courante par instrument.
CREATE UNIQUE INDEX instrument_courant
    ON instrument (provider, instrument_name) WHERE valid_to IS NULL;

-- Deux versions d'un meme instrument ne peuvent pas se chevaucher dans le temps.
-- C'est ce que btree_gist (migration 002) permet : une egalite sur du texte et un
-- recouvrement de plage dans le meme index.
ALTER TABLE instrument ADD CONSTRAINT instrument_sans_chevauchement
    EXCLUDE USING gist (instrument_name WITH =, tstzrange(valid_from, valid_to) WITH &&);

CREATE INDEX instrument_recherche ON instrument (underlying, kind, expiry, strike);

-- ---------------------------------------------------------------------------
-- Fait : marques de la chaine d'options
-- ---------------------------------------------------------------------------
-- Source : markprice.options.{btc,eth}_usd (mark_iv + mark_price sur TOUTE la
-- chaine, ~1 msg/s par devise). Les colonnes bid_iv / greeks / open_interest ne
-- sont renseignees que pour la bande ATM, alimentee par ticker.{instrument} :
-- elles restent NULL ailleurs, et c'est une information en soi (l'instrument
-- n'etait pas dans la bande a ce moment-la).

CREATE TABLE option_mark (
    observed_at        timestamptz NOT NULL,
    instrument_sk      bigint      NOT NULL REFERENCES instrument(instrument_sk),
    mark_iv            double precision,
    mark_price         double precision,
    underlying_price   double precision,
    index_price        double precision,
    bid_iv             double precision,
    ask_iv             double precision,
    bid_price          double precision,
    ask_price          double precision,
    bid_amount         double precision,
    ask_amount         double precision,
    open_interest      double precision,
    delta              double precision,
    gamma              double precision,
    vega               double precision,
    theta              double precision,
    rho                double precision,
    source             text        NOT NULL,   -- markprice | ticker | markprice+ticker
    PRIMARY KEY (instrument_sk, observed_at)
);

SELECT create_hypertable('option_mark', 'observed_at',
                         chunk_time_interval => INTERVAL '1 day');

-- La requete type est "un instantane de toute la chaine" : on veut donc un index
-- qui mene par le temps, pas par l'instrument.
CREATE INDEX option_mark_instantane ON option_mark (observed_at DESC, instrument_sk);

-- ---------------------------------------------------------------------------
-- Fait : perpetuel (funding, base, open interest)
-- ---------------------------------------------------------------------------
-- Source : ticker.{BTC,ETH}-PERPETUAL.100ms.

CREATE TABLE perp_tick (
    observed_at     timestamptz NOT NULL,
    instrument_sk   bigint      NOT NULL REFERENCES instrument(instrument_sk),
    index_price     double precision,
    mark_price      double precision,
    last_price      double precision,
    best_bid_price  double precision,
    best_ask_price  double precision,
    current_funding double precision,
    funding_8h      double precision,
    interest_rate   double precision,
    open_interest   double precision,
    PRIMARY KEY (instrument_sk, observed_at)
);

SELECT create_hypertable('perp_tick', 'observed_at',
                         chunk_time_interval => INTERVAL '1 day');

CREATE INDEX perp_tick_instantane ON perp_tick (observed_at DESC, instrument_sk);

-- ---------------------------------------------------------------------------
-- Fait : indice de volatilite (DVOL)
-- ---------------------------------------------------------------------------
-- Source : deribit_volatility_index.{btc,eth}_usd. Pas de dimension : ce n'est
-- pas un instrument negociable.

CREATE TABLE vol_index (
    observed_at timestamptz NOT NULL,
    underlying  text        NOT NULL,
    volatility  double precision NOT NULL,
    PRIMARY KEY (underlying, observed_at)
);

SELECT create_hypertable('vol_index', 'observed_at',
                         chunk_time_interval => INTERVAL '1 day');

-- ---------------------------------------------------------------------------
-- Compression (Timescale natif) au-dela de 7 jours
-- ---------------------------------------------------------------------------
-- Les lignes recentes restent chaudes (interrogations courantes) ; au-dela, on
-- compresse : environ 10x sur des series temporelles de ce type. Le segment par
-- instrument garde des blocs coherents (une serie par instrument).

ALTER TABLE option_mark SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'instrument_sk',
    timescaledb.compress_orderby = 'observed_at DESC'
);
ALTER TABLE perp_tick SET (
    timescaledb.compress,
    timescaledb.compress_segmentby = 'instrument_sk',
    timescaledb.compress_orderby = 'observed_at DESC'
);

-- Idempotent : la migration peut etre rejouee sur une base deja configuree.
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM timescaledb_information.jobs
                   WHERE proc_name = 'policy_compression'
                     AND hypertable_name = 'option_mark') THEN
        PERFORM add_compression_policy('option_mark', INTERVAL '7 days');
    END IF;
    IF NOT EXISTS (SELECT 1 FROM timescaledb_information.jobs
                   WHERE proc_name = 'policy_compression'
                     AND hypertable_name = 'perp_tick') THEN
        PERFORM add_compression_policy('perp_tick', INTERVAL '7 days');
    END IF;
END $$;

-- Pas de politique de retention : ces tables contiennent l'etat echantillonne
-- (petit devant le bronze), on le garde. Le bronze, lui, porte la retention.
