-- Seed preset team groups for FHI (issue #60).
-- Run manually: duckdb local_data/whenwin.duckdb < sql/seed_preset_groups.sql
-- Do NOT run while Streamlit is active (single-writer constraint).

-- ── Boston / New England (4 teams, one per league) ────────────────────────

INSERT OR REPLACE INTO team_groups (group_id, description)
VALUES ('boston', 'Boston / New England');

INSERT OR REPLACE INTO team_group_members (group_id, team_id) VALUES
  ('boston', 'mlb_bos_boston_red_sox'),
  ('boston', 'nba_bos_celtics'),
  ('boston', 'nfl_ne_patriots'),
  ('boston', 'nhl_bos_bruins');

-- ── New York Metro (8 teams — no NJ teams per user decision) ──────────────

INSERT OR REPLACE INTO team_groups (group_id, description)
VALUES ('new_york', 'New York Metro');

INSERT OR REPLACE INTO team_group_members (group_id, team_id) VALUES
  -- MLB
  ('new_york', 'mlb_nya_new_york_yankees'),
  ('new_york', 'mlb_nyn_new_york_mets'),
  -- NBA (Brooklyn Nets only, not NJ Nets)
  ('new_york', 'nba_ny_knicks'),
  ('new_york', 'nba_bkn_nets'),
  -- NFL
  ('new_york', 'nfl_ny_giants'),
  ('new_york', 'nfl_ny_jets'),
  -- NHL (Rangers + Islanders, no NJ Devils)
  ('new_york', 'nhl_new_rangers'),
  ('new_york', 'nhl_new_islanders');

-- ── LA Metro (16 teams across eras) ───────────────────────────────────────

INSERT OR REPLACE INTO team_groups (group_id, description)
VALUES ('los_angeles', 'LA Metro');

INSERT OR REPLACE INTO team_group_members (group_id, team_id) VALUES
  -- MLB: Dodgers + all 5 Angels eras
  ('los_angeles', 'mlb_lan_los_angeles_dodgers'),
  ('los_angeles', 'mlb_laa_los_angeles_angels'),
  ('los_angeles', 'mlb_cal_california_angels'),
  ('los_angeles', 'mlb_ana_anaheim_angels'),
  ('los_angeles', 'mlb_ana_los_angeles_angels_of_anaheim'),
  ('los_angeles', 'mlb_ana_los_angeles_angels'),
  -- NBA: Lakers + LA Clippers (not SD Clippers)
  ('los_angeles', 'nba_la_lakers'),
  ('los_angeles', 'nba_la_clippers'),
  -- NFL: LA Rams (both eras), LA Chargers (both eras), LA Raiders
  ('los_angeles', 'nfl_la_rams'),
  ('los_angeles', 'nfl_la_rams_1946'),
  ('los_angeles', 'nfl_la_chargers'),
  ('los_angeles', 'nfl_la_chargers_1960'),
  ('los_angeles', 'nfl_la_raiders'),
  -- NHL: Kings + both Ducks eras
  ('los_angeles', 'nhl_los_kings'),
  ('los_angeles', 'nhl_ana_ducks'),
  ('los_angeles', 'nhl_ana_mighty_ducks');
