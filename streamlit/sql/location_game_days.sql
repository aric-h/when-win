WITH dedup AS (
  SELECT DISTINCT
    tg.date,
    tlg.location_group_id,
    tg.league,
    tg.team_id,
    t.team_name,
    tg.result,
    tg.game_type,
    COALESCE(tg.is_series_clinching, FALSE)       AS is_series_clinching,
    COALESCE(tg.is_championship_clinching, FALSE)  AS is_championship_clinching
  FROM team_games tg
  JOIN team_location_groups tlg ON tlg.team_id = tg.team_id
  JOIN teams t ON t.team_id = tg.team_id
  WHERE tg.date IS NOT NULL
    AND tg.result IS NOT NULL
),
daily AS (
  SELECT
    date,
    location_group_id,
    COUNT(DISTINCT team_id) AS teams_playing,
    COUNT(DISTINCT CASE WHEN result = 'W' THEN team_id END) AS winners,
    COUNT(DISTINCT CASE WHEN result = 'L' THEN team_id END) AS losers,
    COUNT(DISTINCT CASE WHEN result = 'T' THEN team_id END) AS ties,
    COUNT(DISTINCT league) AS leagues_playing,
    COUNT(DISTINCT CASE WHEN result = 'W' THEN league END) AS leagues_winning,
    MAX(CASE WHEN game_type = 'postseason' THEN 1 ELSE 0 END) = 1 AS has_playoff_games,
    MIN(CASE WHEN game_type = 'postseason' THEN 1 ELSE 0 END) = 1 AS all_games_playoffs,
    COUNT(DISTINCT CASE WHEN is_series_clinching AND result='W' THEN team_id END) AS series_clinching_wins,
    COUNT(DISTINCT CASE WHEN is_championship_clinching AND result='W' THEN team_id END) AS championship_clinching_wins
  FROM dedup
  GROUP BY 1,2
)
SELECT
  d.*,
  lg.name AS location_group_name,
  CASE WHEN d.winners = d.teams_playing THEN 'Sweep' ELSE 'Partial' END AS sweep_status
FROM daily d
LEFT JOIN location_groups lg ON lg.location_group_id = d.location_group_id
WHERE d.winners >= 3
  AND d.leagues_winning >= 3
