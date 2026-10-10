-- ============================================================
-- IPO Listing Performance — schema
-- PostgreSQL 14+
-- Run: psql -d ipo_analysis -f sql/01_schema.sql
-- ============================================================

BEGIN;

DROP TABLE IF EXISTS gmp_unofficial, aftermarket, listing, subscription, ipo CASCADE;

-- ------------------------------------------------------------
-- ipo : one row per issue. Includes issues that never listed.
-- ------------------------------------------------------------
CREATE TABLE ipo (
    ipo_id          serial       PRIMARY KEY,
    company         text         NOT NULL,
    nse_symbol      text,
    sector          text         NOT NULL,

    issue_open      date,
    issue_close     date,
    listing_date    date,

    price_band_low  numeric(12,2),
    price_band_high numeric(12,2),
    issue_price     numeric(12,2),   -- final cut-off price

    issue_size_cr   numeric(14,2),   -- total, in INR crore
    fresh_issue_cr  numeric(14,2),
    ofs_cr          numeric(14,2),
    lot_size        integer,

    status          text         NOT NULL,

    CONSTRAINT ipo_status_chk CHECK (
        status IN ('listed','withdrawn','deferred','open','upcoming')),
    CONSTRAINT ipo_dates_chk  CHECK (issue_close >= issue_open),
    CONSTRAINT ipo_band_chk   CHECK (price_band_high >= price_band_low),
    CONSTRAINT ipo_lot_chk    CHECK (lot_size > 0),

    -- anything marked 'listed' must actually have the two fields
    -- every gain calculation depends on
    CONSTRAINT ipo_listed_chk CHECK (
        status <> 'listed'
        OR (issue_price IS NOT NULL AND listing_date IS NOT NULL)),

    -- same company can IPO only once per issue window
    CONSTRAINT ipo_company_uniq UNIQUE (company, issue_open)
);

COMMENT ON COLUMN ipo.status IS
  'listed | withdrawn | deferred | open | upcoming. Only ''listed'' rows are valid for gain analysis.';
COMMENT ON COLUMN ipo.issue_size_cr IS 'INR crore. fresh_issue_cr + ofs_cr should reconcile to this.';

-- ------------------------------------------------------------
-- subscription : one row per issue per investor category
-- ------------------------------------------------------------
CREATE TABLE subscription (
    ipo_id           integer     NOT NULL REFERENCES ipo(ipo_id) ON DELETE CASCADE,
    category         text        NOT NULL,
    times_subscribed numeric(10,2),

    PRIMARY KEY (ipo_id, category),
    CONSTRAINT sub_cat_chk   CHECK (
        category IN ('QIB','NII','RII','Employee','Shareholder','Total')),
    CONSTRAINT sub_times_chk CHECK (times_subscribed >= 0)
);

COMMENT ON COLUMN subscription.times_subscribed IS
  'Times the category was subscribed, e.g. 42.75 = 42.75x. Not a percentage.';

-- ------------------------------------------------------------
-- listing : listing-day OHLC. One row per listed issue.
-- ------------------------------------------------------------
CREATE TABLE listing (
    ipo_id        integer       PRIMARY KEY REFERENCES ipo(ipo_id) ON DELETE CASCADE,
    listing_open  numeric(12,2) NOT NULL,
    listing_high  numeric(12,2),
    listing_low   numeric(12,2),
    listing_close numeric(12,2),

    CONSTRAINT listing_range_chk CHECK (listing_high >= listing_low),
    CONSTRAINT listing_open_chk  CHECK (listing_open > 0)
);

-- ------------------------------------------------------------
-- aftermarket : daily closes after listing. Powers gain decay.
-- ------------------------------------------------------------
CREATE TABLE aftermarket (
    ipo_id      integer       NOT NULL REFERENCES ipo(ipo_id) ON DELETE CASCADE,
    trade_date  date          NOT NULL,
    close_price numeric(12,2) NOT NULL,

    PRIMARY KEY (ipo_id, trade_date),
    CONSTRAINT after_price_chk CHECK (close_price > 0)
);

-- ------------------------------------------------------------
-- gmp_unofficial : grey market premium. NOT official data.
-- ------------------------------------------------------------
CREATE TABLE gmp_unofficial (
    ipo_id        integer       NOT NULL REFERENCES ipo(ipo_id) ON DELETE CASCADE,
    observed_date date          NOT NULL,
    gmp_value     numeric(12,2),            -- INR premium over issue price
    source        text,

    PRIMARY KEY (ipo_id, observed_date)
);

COMMENT ON TABLE gmp_unofficial IS
  'Grey market premium: UNOFFICIAL and unregulated. Not an exchange figure. Never present as official.';

-- ------------------------------------------------------------
-- Indexes
-- ------------------------------------------------------------
CREATE INDEX ipo_listed_date_idx  ON ipo (listing_date) WHERE status = 'listed';
CREATE INDEX ipo_sector_idx       ON ipo (sector);
CREATE INDEX subscription_cat_idx ON subscription (category);

COMMIT;
