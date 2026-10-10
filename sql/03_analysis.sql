-- ============================================================
-- Analysis: the six questions.
-- Run:  psql -d ipo_analysis -f sql/03_analysis.sql
-- ============================================================

\echo
\echo ===== Q1. Does demand predict the listing gain? =====
-- NTILE splits the issues into four equal-sized demand buckets.
-- If demand matters, median gain should climb across the buckets.
WITH bucketed AS (
    SELECT listing_gain_pct,
           total_sub,
           NTILE(4) OVER (ORDER BY total_sub) AS demand_quartile
    FROM v_listing_gain
    WHERE total_sub IS NOT NULL
)
SELECT demand_quartile,
       count(*)                                                     AS issues,
       ROUND(MIN(total_sub), 1) || ' - ' || ROUND(MAX(total_sub), 1) AS sub_range,
       ROUND(PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY listing_gain_pct)::numeric, 1)
                                                                     AS median_gain_pct,
       ROUND(AVG(listing_gain_pct), 1)                               AS mean_gain_pct,
       ROUND(100.0 * AVG((listing_gain_pct > 0)::int), 0)            AS pct_positive
FROM bucketed
GROUP BY demand_quartile
ORDER BY demand_quartile;

\echo
\echo ----- correlation of each subscription category with listing gain -----
SELECT 'Total' AS category, count(*) AS n,
       ROUND(corr(listing_gain_pct, total_sub)::numeric, 3) AS correlation
FROM v_listing_gain WHERE total_sub IS NOT NULL
UNION ALL
SELECT 'QIB', count(*), ROUND(corr(listing_gain_pct, qib_sub)::numeric, 3)
FROM v_listing_gain WHERE qib_sub IS NOT NULL
UNION ALL
SELECT 'NII', count(*), ROUND(corr(listing_gain_pct, nii_sub)::numeric, 3)
FROM v_listing_gain WHERE nii_sub IS NOT NULL
UNION ALL
SELECT 'RII', count(*), ROUND(corr(listing_gain_pct, rii_sub)::numeric, 3)
FROM v_listing_gain WHERE rii_sub IS NOT NULL
ORDER BY 1;


\echo
\echo ===== Q2. Do larger issues list weaker? =====
WITH bucketed AS (
    SELECT listing_gain_pct, issue_size_cr,
           NTILE(4) OVER (ORDER BY issue_size_cr) AS size_quartile
    FROM v_listing_gain
    WHERE issue_size_cr IS NOT NULL
)
SELECT size_quartile,
       count(*) AS issues,
       ROUND(MIN(issue_size_cr), 0) || ' - ' || ROUND(MAX(issue_size_cr), 0)
                                                        AS size_range_cr,
       ROUND(PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY listing_gain_pct)::numeric, 1)
                                                        AS median_gain_pct
FROM bucketed
GROUP BY size_quartile
ORDER BY size_quartile;


\echo
\echo ===== Q3. Which sectors delivered reliable gains? =====
-- Thin sectors are reported but flagged: a 2-issue sector median
-- is noise, and presenting it as a finding would be dishonest.
SELECT sector,
       count(*) AS issues,
       ROUND(PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY listing_gain_pct)::numeric, 1)
                                                      AS median_gain_pct,
       ROUND(100.0 * AVG((listing_gain_pct > 0)::int), 0) AS pct_positive,
       CASE WHEN count(*) < 8 THEN 'thin sample' ELSE '' END AS caveat
FROM v_listing_gain
GROUP BY sector
HAVING count(*) >= 2
ORDER BY median_gain_pct DESC;


\echo
\echo ===== Q4. Does the UNOFFICIAL grey market premium track reality? =====
-- GMP is unregulated and not an exchange figure. Treated here as a
-- signal to be tested, never as a reference price.
WITH last_gmp AS (
    SELECT DISTINCT ON (g.ipo_id)
           g.ipo_id, gm.gmp_value, gm.observed_date
    FROM v_listing_gain g
    JOIN gmp_unofficial gm ON gm.ipo_id = g.ipo_id
    WHERE gm.observed_date <= g.listing_date
    ORDER BY g.ipo_id, gm.observed_date DESC
),
cmp AS (
    SELECT g.ipo_id, g.listing_gain_pct,
           ROUND(lg.gmp_value / g.issue_price * 100, 2) AS gmp_implied_pct
    FROM v_listing_gain g
    JOIN last_gmp lg ON lg.ipo_id = g.ipo_id
    WHERE g.issue_price > 0
)
SELECT count(*)                                                   AS issues,
       ROUND(corr(listing_gain_pct, gmp_implied_pct)::numeric, 3) AS correlation,
       ROUND(AVG(gmp_implied_pct - listing_gain_pct)::numeric, 1) AS avg_gmp_overstatement_pp,
       ROUND(PERCENTILE_CONT(0.5) WITHIN GROUP
             (ORDER BY ABS(gmp_implied_pct - listing_gain_pct))::numeric, 1)
                                                                  AS median_abs_error_pp
FROM cmp;


\echo
\echo ===== Q5. Does the listing gain survive? =====
SELECT count(*) AS issues,
       ROUND(PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY listing_gain_pct)::numeric, 1) AS median_day0,
       ROUND(PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY gain_d7_pct)::numeric, 1)      AS median_day7,
       ROUND(PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY gain_d30_pct)::numeric, 1)     AS median_day30,
       ROUND(100.0 * AVG((listing_gain_pct > 0)::int), 0) AS pct_positive_day0,
       ROUND(100.0 * AVG((gain_d30_pct   > 0)::int), 0)   AS pct_positive_day30,
       ROUND(100.0 * AVG((gain_d30_pct < listing_gain_pct)::int), 0) AS pct_that_faded
FROM v_gain_decay
WHERE gain_d30_pct IS NOT NULL;


\echo
\echo ===== Q6. How much of this is just the market regime? =====
-- The rolling average of the PRECEDING ten listings is a crude
-- proxy for market conditions at the time. If gains cluster in
-- periods rather than by issue characteristics, the regime is
-- doing the work, not your predictors.
WITH regime AS (
    SELECT listing_date,
           listing_gain_pct,
           AVG(listing_gain_pct) OVER (
               ORDER BY listing_date
               ROWS BETWEEN 10 PRECEDING AND 1 PRECEDING
           ) AS prior_10_avg
    FROM v_listing_gain
)
SELECT DATE_TRUNC('quarter', listing_date)::date      AS quarter,
       count(*)                                        AS issues,
       ROUND(AVG(listing_gain_pct), 1)                 AS avg_gain_pct,
       ROUND(AVG(prior_10_avg)::numeric, 1)            AS regime_at_the_time,
       -- a correlation computed on a handful of rows is noise, not a
       -- finding: suppress it rather than print a confident -1.000
       CASE WHEN count(*) >= 8
            THEN ROUND(corr(listing_gain_pct, prior_10_avg)::numeric, 3)
       END                                             AS corr_with_regime
FROM regime
WHERE prior_10_avg IS NOT NULL
GROUP BY 1
ORDER BY 1;


\echo
\echo ===== Showcase: RANK, NTILE, LAG and an explicit window frame =====
WITH g AS (SELECT * FROM v_listing_gain WHERE total_sub IS NOT NULL)
SELECT company,
       sector,
       listing_date,
       listing_gain_pct,
       RANK()   OVER (PARTITION BY sector ORDER BY listing_gain_pct DESC) AS rank_in_sector,
       NTILE(4) OVER (ORDER BY total_sub)                                 AS demand_quartile,
       LAG(listing_gain_pct) OVER (PARTITION BY sector ORDER BY listing_date)
                                                                          AS prev_in_sector,
       ROUND(AVG(listing_gain_pct) OVER (
             ORDER BY listing_date
             ROWS BETWEEN 10 PRECEDING AND 1 PRECEDING)::numeric, 1)      AS regime_avg
FROM g
ORDER BY listing_date DESC
LIMIT 12;
