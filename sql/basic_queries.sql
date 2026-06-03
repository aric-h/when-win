-- Dates where 3+ selected teams won on same day (example set)
SELECT
    date,
    COUNT(*) AS wins
FROM team_games
WHERE season = 2025
  AND league = 'NFL'
  AND team_id IN ('nfl_min_vikings', 'nfl_gb_packers', 'nfl_chi_bears', 'nfl_det_lions')
  AND result = 'W'
GROUP BY date
HAVING COUNT(*) >= 3
ORDER BY date;

-- Dates where all selected teams lost (example set)
SELECT
    date,
    COUNT(*) AS losses
FROM team_games
WHERE season = 2025
  AND league = 'NFL'
  AND team_id IN ('nfl_min_vikings', 'nfl_gb_packers', 'nfl_chi_bears', 'nfl_det_lions')
  AND result = 'L'
GROUP BY date
HAVING COUNT(*) = 4
ORDER BY date;

-- Distribution of win-days for selected teams: #days with 0/1/2/3/4 wins
WITH daily AS (
    SELECT
        date,
        SUM(CASE WHEN result = 'W' THEN 1 ELSE 0 END) AS wins
    FROM team_games
    WHERE season = 2025
      AND league = 'NFL'
      AND team_id IN ('nfl_min_vikings', 'nfl_gb_packers', 'nfl_chi_bears', 'nfl_det_lions')
    GROUP BY date
)
SELECT
    wins,
    COUNT(*) AS days
FROM daily
GROUP BY wins
ORDER BY wins;
