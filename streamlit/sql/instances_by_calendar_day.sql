WITH dedup AS (
  SELECT DISTINCT
    tg.date,
    tlg.location_group_id,
    tg.league,
    tg.team_id,
    tg.result
  FROM team_games tg
  JOIN team_location_groups tlg ON tlg.team_id = tg.team_id
  WHERE tg.date IS NOT NULL
    AND tg.result IS NOT NULL
),
daily AS (
  SELECT
    date,
    location_group_id,
    COUNT(DISTINCT CASE WHEN result = 'W' THEN team_id END) AS winners,
    COUNT(DISTINCT CASE WHEN result = 'W' THEN league END)  AS leagues_winning
  FROM dedup
  GROUP BY 1, 2
)
SELECT
  EXTRACT(YEAR FROM date)::INT  AS year,
  EXTRACT(MONTH FROM date)::INT AS month,
  EXTRACT(DAY FROM date)::INT   AS day,
  COUNT(*) AS instances
FROM daily
WHERE winners >= 3
  AND leagues_winning >= 3
GROUP BY 1, 2, 3
ORDER BY 1, 2, 3
