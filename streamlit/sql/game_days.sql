SELECT
  tg.date,
  tg.league,
  tg.season,
  tg.game_id,
  tg.team_id,
  t.city || ' ' || t.team_name AS team_label,
  tg.opponent_team_id,
  o.city || ' ' || o.team_name AS opponent_label,
  tg.result,
  tg.pts_for,
  tg.pts_against,
  tg.game_type,
  pgr.round_order AS playoff_round_order,
  pgr.round_name  AS playoff_round,
  COALESCE(tg.is_series_clinching, FALSE)      AS is_series_clinching,
  COALESCE(tg.is_championship_clinching, FALSE) AS is_championship_clinching
FROM team_games tg
JOIN team_location_groups tlg ON tlg.team_id = tg.team_id
JOIN teams t ON t.team_id = tg.team_id
JOIN teams o ON o.team_id = tg.opponent_team_id
LEFT JOIN postseason_game_rounds pgr
  ON pgr.league = tg.league AND pgr.game_id = tg.game_id
WHERE tg.date = ?
  AND tlg.location_group_id = ?
  AND tg.result IS NOT NULL
ORDER BY tg.league, tg.game_type DESC, team_label
