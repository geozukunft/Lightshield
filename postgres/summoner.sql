CREATE TABLE IF NOT EXIST summoner (

    summoner_id VARCHAR(63) PRIMARY KEY,
    account_id VARCHAR(56),
    puuid VARCHAR(78)

    rank SMALLINT,
    rank_history SMALLINT[],

    wins SMALLINT,
    wins_last_updated SMALLINT,

    losses SMALLINT,
    losses_last_updated SMALLINT,

    priority VARCHAR(1)
);


