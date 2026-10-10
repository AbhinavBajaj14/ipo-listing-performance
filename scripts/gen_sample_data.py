#!/usr/bin/env python3
"""
gen_sample_data.py — generate a SYNTHETIC sample dataset.

This data is invented. It exists for two reasons:

  1. So the pipeline can be run and the SQL verified before any real
     data is collected.
  2. So the repository is runnable by anyone who clones it, without
     redistributing a third party's compiled dataset.

It must never be used to make claims about real IPOs. Every file it
writes is prefixed with a SYNTHETIC marker column for that reason.

Planted signals (so tests can confirm the queries detect them):
  * total subscription correlates positively with listing gain
  * larger issues list slightly weaker
  * two sectors run systematically hot, one systematically cold
  * roughly half of a listing-day gain erodes within a month
  * a bull window in 2024 lifts everything (the regime confound)

Usage:  python gen_sample_data.py
Output: data/sample/*.csv
"""

from __future__ import annotations

import numpy as np
import pandas as pd
from pathlib import Path

SEED = 20261011
N_LISTED = 140
N_WITHDRAWN = 9
N_PENDING = 4

OUT = Path(__file__).resolve().parent.parent / "data" / "sample"

SECTORS = {
    "Information Technology":      6.0,   # hot
    "Capital Goods":               4.0,   # hot
    "Pharmaceuticals":             1.0,
    "Financial Services":         -1.0,
    "Consumer Durables":           0.5,
    "Chemicals":                   1.5,
    "Automobile & Ancillaries":    0.0,
    "Realty & Infrastructure":    -5.0,   # cold
    "Textiles":                   -2.0,
    "Healthcare Services":         2.0,
}

# a bull window that lifts every listing inside it
REGIME_START = pd.Timestamp("2024-02-01")
REGIME_END   = pd.Timestamp("2024-11-30")
REGIME_BOOST = 11.0


def main() -> None:
    rng = np.random.default_rng(SEED)
    OUT.mkdir(parents=True, exist_ok=True)

    sectors = list(SECTORS)
    n_total = N_LISTED + N_WITHDRAWN + N_PENDING

    # --- issue-level attributes -------------------------------------------
    opens = pd.to_datetime("2021-01-15") + pd.to_timedelta(
        np.sort(rng.integers(0, 365 * 5 + 180, n_total)), unit="D")

    companies = [f"Synthetic Issuer {i + 1:03d} Ltd" for i in range(n_total)]
    symbols = [f"SYN{i + 1:03d}" for i in range(n_total)]
    sector_pick = rng.choice(sectors, n_total)

    # issue size: lognormal, INR crore, 80 -> ~25000
    issue_size = np.round(np.exp(rng.normal(6.6, 1.05, n_total)), 2).clip(80, 25000)

    issue_price = np.round(np.exp(rng.normal(5.3, 0.65, n_total)), 0).clip(30, 2400)
    band_low = np.round(issue_price * rng.uniform(0.93, 0.99, n_total), 0)
    band_high = np.round(issue_price * rng.uniform(1.00, 1.03, n_total), 0)
    band_high = np.maximum(band_high, band_low)
    issue_price = np.clip(issue_price, band_low, band_high)

    lot_size = np.where(issue_price > 900, 15,
                np.where(issue_price > 400, 30,
                 np.where(issue_price > 150, 75, 150)))

    ofs_share = rng.uniform(0.0, 0.85, n_total)
    ofs_cr = np.round(issue_size * ofs_share, 2)
    fresh_cr = np.round(issue_size - ofs_cr, 2)

    # --- subscription ------------------------------------------------------
    # driven partly by a latent "demand" factor that also drives gains
    demand = rng.normal(0, 1, n_total)
    total_sub = np.round(np.exp(1.1 + 0.95 * demand + rng.normal(0, 0.55, n_total)), 2)
    total_sub = total_sub.clip(0.15, 320)

    qib = np.round(total_sub * rng.uniform(0.6, 2.1, n_total), 2).clip(0.0, 500)
    nii = np.round(total_sub * rng.uniform(0.4, 2.4, n_total), 2).clip(0.0, 500)
    rii = np.round(total_sub * rng.uniform(0.3, 1.5, n_total), 2).clip(0.0, 200)

    # --- status ------------------------------------------------------------
    status = np.array(["listed"] * n_total, dtype=object)
    # withdrawn issues: scattered, deliberately weak demand
    wd_idx = rng.choice(np.arange(n_total - N_PENDING), N_WITHDRAWN, replace=False)
    status[wd_idx] = "withdrawn"
    total_sub[wd_idx] = np.round(rng.uniform(0.05, 0.85, N_WITHDRAWN), 2)
    # the most recent few are still open / upcoming
    status[-N_PENDING:] = ["open", "open", "upcoming", "upcoming"]

    closes = opens + pd.to_timedelta(rng.integers(2, 4, n_total), unit="D")
    listing_dates = closes + pd.to_timedelta(rng.integers(3, 9, n_total), unit="D")

    # --- listing gain ------------------------------------------------------
    size_effect = -2.6 * (np.log(issue_size) - np.log(issue_size).mean())
    sector_effect = np.array([SECTORS[s] for s in sector_pick])
    in_regime = ((listing_dates >= REGIME_START) & (listing_dates <= REGIME_END))
    regime_effect = np.where(in_regime, REGIME_BOOST, 0.0)

    gain_pct = (6.0
                + 7.2 * demand
                + size_effect
                + sector_effect
                + regime_effect
                + rng.normal(0, 13.0, n_total))
    gain_pct = gain_pct.clip(-45, 165)

    listing_open = np.round(issue_price * (1 + gain_pct / 100), 2).clip(1.0, None)
    intraday = rng.uniform(0.03, 0.14, n_total)
    listing_high = np.round(listing_open * (1 + intraday * rng.uniform(0.3, 1.0, n_total)), 2)
    listing_low = np.round(listing_open * (1 - intraday * rng.uniform(0.3, 1.0, n_total)), 2)
    listing_close = np.round(
        np.clip(listing_open * (1 + rng.normal(0, 0.045, n_total)), listing_low, listing_high), 2)

    # --- assemble ipo table ------------------------------------------------
    ipo = pd.DataFrame({
        "company": companies,
        "nse_symbol": symbols,
        "sector": sector_pick,
        "issue_open": opens.date,
        "issue_close": closes.date,
        "listing_date": listing_dates.date,
        "price_band_low": band_low,
        "price_band_high": band_high,
        "issue_price": issue_price,
        "issue_size_cr": issue_size,
        "fresh_issue_cr": fresh_cr,
        "ofs_cr": ofs_cr,
        "lot_size": lot_size,
        "status": status,
        "is_synthetic": True,
    })
    # unlisted issues carry no listing date or final price
    unlisted = ipo["status"] != "listed"
    ipo.loc[unlisted, ["listing_date", "issue_price"]] = None

    # --- subscription table ------------------------------------------------
    sub_rows = []
    for i, comp in enumerate(companies):
        sub_rows += [
            (comp, "QIB", qib[i]),
            (comp, "NII", nii[i]),
            (comp, "RII", rii[i]),
            (comp, "Total", total_sub[i]),
        ]
    subscription = pd.DataFrame(sub_rows,
                                columns=["company", "category", "times_subscribed"])
    subscription["is_synthetic"] = True
    # mimic the real-world gap: category detail missing for older issues
    old = subscription["company"].isin(np.array(companies)[opens < pd.Timestamp("2022-07-01")])
    drop = old & subscription["category"].isin(["QIB", "NII", "RII"])
    subscription = subscription[~drop]

    # --- listing table -----------------------------------------------------
    listed_mask = ipo["status"] == "listed"
    listing = pd.DataFrame({
        "company": ipo.loc[listed_mask, "company"],
        "listing_open": listing_open[listed_mask.values],
        "listing_high": listing_high[listed_mask.values],
        "listing_low": listing_low[listed_mask.values],
        "listing_close": listing_close[listed_mask.values],
        "is_synthetic": True,
    })

    # --- aftermarket: 30 trading days, with ~half the gain decaying --------
    after_rows = []
    gmp_rows = []
    for i, comp in enumerate(companies):
        if status[i] != "listed":
            continue
        start = pd.Timestamp(listing_dates[i])
        price = listing_close[i]
        # target: half the listing-day premium erodes over the month
        target = issue_price[i] * (1 + (gain_pct[i] / 100) * 0.5)
        drift = (target / price) ** (1 / 30) if price > 0 else 1.0
        for d in range(1, 31):
            day = start + pd.Timedelta(days=d)
            if day.weekday() >= 5:
                continue
            price = max(price * drift * (1 + rng.normal(0, 0.022)), 0.5)
            after_rows.append((comp, day.date(), round(price, 2), True))

        # unofficial GMP: tracks the eventual gain, but noisily and with bias
        for d in (-6, -3, -1):
            obs = start + pd.Timedelta(days=d)
            implied = gain_pct[i] * rng.uniform(0.55, 1.5) + rng.normal(0, 9)
            gmp_rows.append((comp, obs.date(),
                             round(issue_price[i] * implied / 100, 2),
                             "synthetic-aggregator", True))

    aftermarket = pd.DataFrame(after_rows,
        columns=["company", "trade_date", "close_price", "is_synthetic"])
    gmp = pd.DataFrame(gmp_rows,
        columns=["company", "observed_date", "gmp_value", "source", "is_synthetic"])

    for name, df in [("ipo", ipo), ("subscription", subscription),
                     ("listing", listing), ("aftermarket", aftermarket),
                     ("gmp_unofficial", gmp)]:
        path = OUT / f"{name}.csv"
        df.to_csv(path, index=False)
        print(f"  {path.relative_to(OUT.parent.parent)}: {len(df)} rows")

    print(f"\nSYNTHETIC sample written to {OUT}")
    print(f"  listed={int(listed_mask.sum())} "
          f"withdrawn={int((ipo['status'] == 'withdrawn').sum())} "
          f"pending={int(ipo['status'].isin(['open', 'upcoming']).sum())}")
    print(f"  median listing gain (planted): {np.median(gain_pct[listed_mask.values]):.1f}%")


if __name__ == "__main__":
    main()
