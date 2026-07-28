-- Seed preset team groups for FHI (issue #60).
-- Run manually: duckdb local_data/whenwin.duckdb < sql/seed_preset_groups.sql
-- Do NOT run while Streamlit is active (single-writer constraint).
--
-- Before running, verify team_ids exist:
--   SELECT team_id, city, team_name, league, start_year, end_year
--   FROM teams
--   WHERE team_id IN ( <paste all IDs below> );

-- ── Boston / New England (4 teams, one per league) ────────────────────

INSERT OR REPLACE INTO team_groups (group_id, description)
VALUES ('boston', 'Boston / New England');

INSERT OR REPLACE INTO team_group_members (group_id, team_id) VALUES
  ('boston', 'mlb_bos_red_sox'),
  ('boston', 'nba_bos_celtics'),
  ('boston', 'nfl_new_patriots'),
  ('boston', 'nhl_bos_bruins');

-- ── New York Metro (8 teams — no NJ teams per user decision) ────────────

INSERT OR REPLACE INTO team_groups (group_id, description)
VALUES ('new_york', 'New York Metro');

INSERT OR REPLACE INTO team_group_members (group_id, team_id) VALUES
  -- MLB
  ('new_york', 'mlb_new_yankees'),
  ('new_york', 'mlb_new_mets'),
  -- NBA (Brooklyn Nets only, not NJ Nets)
  ('new_york', 'nba_new_knicks'),
  ('new_york', 'nba_bro_nets'),
  -- NFL
  ('new_york', 'nfl_new_giants'),
  ('new_york', 'nfl_new_jets'),
  -- NHL (Rangers + Islanders, no NJ Devils)
  ('new_york', 'nhl_new_rangers'),
  ('new_york', 'nhl_new_islanders');

-- ── LA Metro (10+ teams across eras) ──────────────────────────────────

INSERT OR REPLACE INTO team_groups (group_id, description)
VALUES ('los_angeles', 'LA Metro');

INSERT OR REPLACE INTO team_group_members (group_id, team_id) VALUES
  -- MLB: Dodgers + all Angels eras (California, Anaheim, LA)
  ('los_angeles', 'mlb_los_dodgers'),
  ('los_angeles', 'mlb_los_angels'),
  ('los_angeles', 'mlb_ana_angels'),
  ('los_angeles', 'mlb_cal_angels'),
  -- NBA: Lakers + LA Clippers (not SD Clippers)
  ('los_angeles', 'nba_los_lakers'),
  ('los_angeles', 'nba_los_clippers'),
  -- NFL: LA Rams (current + original LA era), LA Chargers (current), LA Raiders
  ('los_angeles', 'nfl_los_rams'),
  ('los_angeles', 'nfl_los_rams_1946'),
  ('los_angeles', 'nfl_los_chargers'),
  ('los_angeles', 'nfl_los_raiders'),
  -- NHL: Kings + Anaheim Ducks (current + Mighty Ducks era if separate)
  ('los_angeles', 'nhl_los_kings'),
  ('los_angeles', 'nhl_ana_ducks'),
  ('los_angeles', 'nhl_ana_mighty_ducks');
