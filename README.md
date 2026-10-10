# IPO Listing Performance

**What actually predicts a listing-day gain on Indian mainboard IPOs — demand, issue
size, or sector — and whether that gain survives the month.**

A SQL-first analysis: PostgreSQL schema, a polite scraper, window-function analysis,
and a self-contained dashboard.

---

## Finding

> **Pending.** The pipeline is complete and verified, but it has not yet been run
> against real data. This section will carry a one-sentence finding once it has.
> Writing a conclusion before collecting the data would defeat the point of the
> project.

What the pipeline currently demonstrates, on a **synthetic** dataset with signals
planted deliberately (`scripts/gen_sample_data.py`), is that the analysis detects
those signals: demand quartiles separate cleanly, larger issues list weaker, the
planted hot and cold sectors surface, and roughly half the listing-day gain erodes
within a month. **Those are not findings about real IPOs** — they are a test that
the queries measure what they claim to.

---

## The question, and why it is worth asking

"IPOs give listing gains" is widely repeated and rarely measured. Three things are
usually conflated:

1. whether a gain happens at all,
2. whether anything observable *before* listing predicts it,
3. whether the gain is still there a week or a month later.

The third is the one almost nobody checks, and it is the one that decides whether a
listing-day number means anything. So the analysis is built around six questions:

| # | Question | Status |
|---|----------|--------|
| 1 | Which subscription category predicts listing gain best — QIB, NII, retail? | Blocked — see limitations |
| 2 | Do larger issues list weaker than smaller ones? | Implemented |
| 3 | Which sectors delivered the most reliable gains? | Implemented |
| 4 | Does the unofficial grey market premium track the real listing price? | Implemented |
| 5 | Do listing-day gains survive a week? A month? | Implemented |
| 6 | How much of this is simply the market regime? | Implemented |

Question 6 is the control. Without it, a bull run reads as a finding.

---

## Data

| Source | Used for |
|--------|----------|
| Chittorgarh | Issue details, dates, lot size, listing-day prices, **total** subscription |
| NSE / BSE | *Planned* — per-category subscription, aftermarket closes |
| SEBI filings | Issue size, fresh issue vs offer-for-sale split |

**Grey market premium is unofficial.** It is an unregulated indicator, not an
exchange figure. It is labelled as such in the schema (`gmp_unofficial`, with a
table comment), in the analysis, and on the dashboard. It is treated as a signal
to be tested, never as a reference price.

**Scraped data is not committed.** `data/` is gitignored. The source asserts
copyright over its compiled content and restricts redistribution, so this
repository carries the pipeline and a synthetic sample, not someone else's
dataset. Clone it and point the scraper at the source yourself.

---

## Running it

```bash
# 1. database
createdb ipo_analysis
psql -d ipo_analysis -f sql/01_schema.sql
psql -d ipo_analysis -f sql/02_views.sql

# 2. dependencies
pip install -r requirements.txt

# 3a. see it work immediately, on synthetic data
python scripts/gen_sample_data.py
python scripts/load_to_postgres.py --source data/sample

# 3b. or collect real data (slow, polite, resumable)
python scripts/scrape_chittorgarh.py discover
python scripts/scrape_chittorgarh.py fetch --min-id 2100
python scripts/parse_ipos.py              # -> data/processed/*.csv
python scripts/load_to_postgres.py --source data/processed

# 4. analysis and dashboard
psql -d ipo_analysis -f sql/03_analysis.sql
python scripts/build_dashboard.py         # -> docs/dashboard.html
```

---

## Layout

```
sql/01_schema.sql        five tables, typed and constrained
sql/02_views.sql         gain definitions, written once
sql/03_analysis.sql      the six questions
scripts/scrape_chittorgarh.py   discover / fetch / inspect
scripts/parse_ipos.py           cached HTML -> CSV
scripts/load_to_postgres.py     CSV -> database, idempotent
scripts/gen_sample_data.py      synthetic fixture with planted signals
scripts/build_dashboard.py      database -> self-contained HTML
docs/dashboard.html             open in any browser, no server needed
```

---

## Method notes

**Money is `numeric`, never `float`.** Floating point cannot represent 0.1 exactly;
`numeric` is exact decimal.

**The `listed` filter lives in a view, not in twenty queries.** `v_listing_gain`
applies `status = 'listed'` once, so an open, upcoming or withdrawn issue cannot
leak into a gain figure through a forgotten `WHERE` clause.

**Withdrawn issues are kept in the database.** Deleting them would be survivorship
bias. They are excluded from gain maths by status, not by omission.

**Fetching and parsing are separate steps.** Pages are cached to disk once; the
parser runs against local copies. Re-scraping on every parser bug fix is slow for
you and rude to the source.

**Median, not mean, for sector performance.** One 150% debut moves an average on
its own.

**Small-sample statistics are suppressed, not printed.** Sectors under 8 issues are
flagged; quarterly correlations under 8 observations are returned as `NULL` rather
than as a confident-looking `-1.000`.

---

## Limitations

Read this section before using any number above.

1. **No real data has been collected yet.** Every figure currently produced is
   synthetic. This is the largest limitation and it supersedes the rest.

2. **Per-category subscription is paywalled at source.** Chittorgarh now shows only
   the *total* subscription row for free; QIB, NII and retail detail sits behind
   their paid product. Question 1 is therefore unanswered. NSE publishes the bid
   detail officially and free, and is the right fix — it is not yet implemented.

3. **Survivorship bias is handled but not eliminated.** Withdrawn and deferred
   issues are retained, but an issue that was never filed because the window looked
   bad leaves no trace in any dataset.

4. **Market regime is a confound, only partly controlled.** The rolling average of
   the preceding ten listings is a crude proxy. A proper control would index
   against a broad market benchmark over the same window.

5. **Sector labels come from the aggregator** and are not a standard
   classification. Thin sectors are flagged, but the boundaries are someone else's
   judgement.

6. **Grey market premium is unofficial and unaudited.** Where it correlates with
   outcomes, that is a measured association within one unregulated dataset, not
   evidence that it is reliable.

7. **Listing-day open is one price at one moment.** It is not an achievable return:
   it assumes allotment, which is itself not guaranteed in an oversubscribed issue.

---

## What this is not

This is analysis, not investment advice. It describes what has historically
correlated with listing-day outcomes in a specific sample over a specific period.
Past listing patterns do not predict any future issue, and nothing here should be
read as a recommendation to apply for, or avoid, any IPO.
