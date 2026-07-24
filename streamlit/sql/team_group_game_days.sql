SELECT
  gg.date,
  gg.game_id,
  gg.league,
  gg.season,
  gg.team_id,
  gg.opponent_team_id,
  gg.result,
  gg.game_type,
  pgr.round_order AS playoff_round_order,
  pgr.round_name  AS playoff_round,
  CASE
    WHEN gg.result = 'W' THEN COALESCE(gg.is_series_clinching, FALSE)
    WHEN gg.result = 'L' THEN COALESCE(winner.is_series_clinching, FALSE)
    ELSE FALSE
  END AS series_clinching_derived,
  CASE
    WHEN gg.result = 'W' THEN COALESCE(gg.is_championship_clinching, FALSE)
    WHEN gg.result = 'L' THEN COALESCE(winner.is_championship_clinching, FALSE)
    ELSE FALSE
  END AS championship_clinching_derived
FROM team_games gg
LEFT JOIN team_games winner
  ON winner.game_id = gg.game_id
 AND winner.result   = 'W'
LEFT JOIN postseason_game_rounds pgr
  ON pgr.league = gg.league AND pgr.game_id = gg.game_id
WHERE gg.team_id = ANY(?)
  AND gg.result IS NOT NULL
  AND gg.season BETWEEN ? AND ?
ORDER BY gg.date, gg.team_id
