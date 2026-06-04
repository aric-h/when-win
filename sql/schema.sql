CREATE TABLE IF NOT EXISTS teams (
    team_id TEXT PRIMARY KEY,
    league TEXT NOT NULL,
    city TEXT NOT NULL,
    team_name TEXT NOT NULL,
    start_year INTEGER,
    end_year INTEGER,
    franchise_id TEXT
);

CREATE TABLE IF NOT EXISTS team_games (
    game_id TEXT NOT NULL,
    date DATE,
    league TEXT,
    season INTEGER,
    team_id TEXT NOT NULL,
    opponent_team_id TEXT,
    result TEXT,
    pts_for INTEGER,
    pts_against INTEGER,
    game_type TEXT,
    is_championship_clinching BOOLEAN DEFAULT FALSE,
    is_series_clinching BOOLEAN DEFAULT FALSE,
    PRIMARY KEY (game_id, team_id),
    CHECK (result IN ('W', 'L', 'T')),
    CHECK (game_type IN ('regular', 'postseason'))
);

CREATE TABLE IF NOT EXISTS franchises (
    franchise_id TEXT PRIMARY KEY,
    league TEXT NOT NULL,
    franchise_name TEXT NOT NULL,
    start_year INTEGER
);

CREATE TABLE IF NOT EXISTS team_groups (
    group_id TEXT PRIMARY KEY,
    description TEXT
);

CREATE TABLE IF NOT EXISTS team_group_members (
    group_id TEXT,
    team_id TEXT,
    PRIMARY KEY (group_id, team_id)
);

CREATE TABLE IF NOT EXISTS location_groups (
    location_group_id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    description TEXT
);

CREATE TABLE IF NOT EXISTS team_location_groups (
    team_id TEXT PRIMARY KEY,
    location_group_id TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS postseason_game_rounds (
    league TEXT NOT NULL,
    game_id TEXT NOT NULL,
    season INTEGER,
    round_order INTEGER,
    round_name TEXT NOT NULL,
    source TEXT,
    PRIMARY KEY (league, game_id)
);

CREATE TABLE IF NOT EXISTS postseason_series (
    league TEXT NOT NULL,
    season INTEGER NOT NULL,
    series_id TEXT NOT NULL,
    team_id_a TEXT,
    team_id_b TEXT,
    series_start_date DATE,
    series_end_date DATE,
    games_in_matchup INTEGER,
    round_order INTEGER,
    round_name TEXT,
    source TEXT,
    PRIMARY KEY (league, series_id)
);
