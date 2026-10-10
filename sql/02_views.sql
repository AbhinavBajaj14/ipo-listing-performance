-- ============================================================
-- Views: the gain definitions, defined once.
--
-- Every analysis query reads from these rather than recomputing
-- the arithmetic. Two reasons: the status = 'listed' filter lives
-- in exactly one place (so an open or withdrawn issue can never
-- leak into a gain figure), and if the definition of "gain" ever
-- changes, it changes here and nowhere else.
-- ============================================================

-- Listing-day performance, one row per listed issue.
CREATE OR REPLACE VIEW v_listing_gain AS
SELECT
    i.ipo_id,
    i.company,
    i.sector,
    i.listing_date,
    i.issue_price,
    i.issue_size_cr,
    l.listing_open,
    l.listing_close,
    ROUND((l.listing_open  - i.issue_price) / i.issue_price * 100, 2) AS listing_gain_pct,
    ROUND((l.listing_close - i.issue_price) / i.issue_price * 100, 2) AS close_gain_pct,
    tot.times_subscribed AS total_sub,
    qib.times_subscribed AS qib_sub,
    nii.times_subscribed AS nii_sub,
    rii.times_subscribed AS rii_sub
FROM ipo i
JOIN listing l            ON l.ipo_id = i.ipo_id
LEFT JOIN subscription tot ON tot.ipo_id = i.ipo_id AND tot.category = 'Total'
LEFT JOIN subscription qib ON qib.ipo_id = i.ipo_id AND qib.category = 'QIB'
LEFT JOIN subscription nii ON nii.ipo_id = i.ipo_id AND nii.category = 'NII'
LEFT JOIN subscription rii ON rii.ipo_id = i.ipo_id AND rii.category = 'RII'
WHERE i.status = 'listed'
  AND i.issue_price > 0;

COMMENT ON VIEW v_listing_gain IS
  'Listed issues only. Gain measured against the final issue price.';


-- Does the listing-day gain survive? Compares day 0 with roughly
-- one week and one month later, using the first available close on
-- or after the target date (markets are shut some days).
CREATE OR REPLACE VIEW v_gain_decay AS
SELECT
    g.ipo_id,
    g.company,
    g.sector,
    g.listing_date,
    g.listing_gain_pct,
    ROUND((d7.close_price  - g.issue_price) / g.issue_price * 100, 2) AS gain_d7_pct,
    ROUND((d30.close_price - g.issue_price) / g.issue_price * 100, 2) AS gain_d30_pct
FROM v_listing_gain g
LEFT JOIN LATERAL (
    SELECT a.close_price FROM aftermarket a
    WHERE a.ipo_id = g.ipo_id AND a.trade_date >= g.listing_date + 7
    ORDER BY a.trade_date LIMIT 1
) d7 ON TRUE
LEFT JOIN LATERAL (
    SELECT a.close_price FROM aftermarket a
    WHERE a.ipo_id = g.ipo_id AND a.trade_date >= g.listing_date + 28
    ORDER BY a.trade_date LIMIT 1
) d30 ON TRUE;

COMMENT ON VIEW v_gain_decay IS
  'Listing-day gain vs ~1 week and ~1 month later. NULLs where aftermarket data is short.';
