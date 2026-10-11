#!/usr/bin/env python3
"""
parse_ipos.py — turn cached IPO pages into CSVs the loader can read.

Reads data/raw/html/*.html (produced by scrape_chittorgarh.py fetch) and
writes data/processed/*.csv.

Tables are identified by what their headers CONTAIN, not by position.
Page layouts shift; a parser keyed to `tables[3]` breaks silently and
fills your database with nulls you won't notice until the analysis.

Filtering happens here, not at fetch time: the page is what tells us
whether an issue is mainboard or SME, and when it listed.

    python parse_ipos.py --report          # what matched, what didn't
    python parse_ipos.py --from-year 2021  # write the CSVs
"""

from __future__ import annotations

import argparse
import re
from io import StringIO
from pathlib import Path

import pandas as pd
from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parent.parent
HTML_DIR = ROOT / "data" / "raw" / "html"
OUT_DIR = ROOT / "data" / "processed"

MONEY = re.compile(r"[₹,\s]|Rs\.?|per share", re.I)
# "33,84,000 shares (agg. up to ₹40 Cr)" -> 40.0, not 3384000
CRORE = re.compile(r"₹\s*([\d,]+(?:\.\d+)?)\s*(?:Cr|Crore)", re.I)
NUMBER = re.compile(r"-?\d+(?:\.\d+)?")
DATE_FMTS = ("%a, %b %d, %Y", "%b %d, %Y", "%d %b %Y", "%Y-%m-%d", "%d-%m-%Y")


def num(text) -> float | None:
    if text is None or (isinstance(text, float) and pd.isna(text)):
        return None
    m = NUMBER.search(MONEY.sub("", str(text)))
    return float(m.group()) if m else None


def date(text) -> str | None:
    if not text:
        return None
    s = re.sub(r"\s+", " ", str(text)).strip().rstrip(".")
    for f in DATE_FMTS:
        try:
            return pd.to_datetime(s, format=f).date().isoformat()
        except (ValueError, TypeError):
            continue
    try:
        return pd.to_datetime(s, dayfirst=True).date().isoformat()
    except Exception:
        return None


def crore(text) -> float | None:
    """The rupee-crore figure, ignoring any share count in front of it."""
    if text is None or (isinstance(text, float) and pd.isna(text)):
        return None
    m = CRORE.search(str(text))
    return float(m.group(1).replace(",", "")) if m else None


def date_range(text) -> tuple[str | None, str | None]:
    """'26 to 28 Nov, 2025' -> ('2025-11-26', '2025-11-28').

    The open side is often just a day number, and the range can cross a
    month boundary ('28 Nov to 2 Dec'), so the close date is parsed first
    and the open side borrows whatever it is missing from it.
    """
    if not text:
        return (None, None)
    s = re.sub(r"\s+", " ", str(text)).strip()
    parts = re.split(r"\s+to\s+|\s*[–—]\s*|\s+-\s+", s, maxsplit=1)
    if len(parts) != 2:
        d = date(s)
        return (d, d)
    left, right = parts[0].strip().rstrip(","), parts[1].strip()

    close = date(right)
    if not close:
        return (None, None)
    cd = pd.Timestamp(close)

    opened = None
    if re.search(r"[A-Za-z]", left):                  # has a month name
        if re.search(r"\d{4}", left):                 # ...and a year
            opened = date(left)
        else:                                         # borrow the close year
            opened = date(f"{left} {cd.year}")
            if opened and opened > close:             # range crossed New Year
                opened = date(f"{left} {cd.year - 1}")
    elif re.fullmatch(r"\d{1,2}", left):              # bare day number
        try:
            opened = cd.replace(day=int(left)).date().isoformat()
        except ValueError:
            opened = None
        if opened and opened > close:                 # range crossed a month
            prev_month_end = cd.replace(day=1) - pd.Timedelta(days=1)
            try:
                opened = prev_month_end.replace(day=int(left)).date().isoformat()
            except ValueError:
                opened = None
    return (opened, close)


def kv_from(tables: list[pd.DataFrame]) -> dict[str, str]:
    """Merge every two-column 'Field | Value' table into one dict."""
    out: dict[str, str] = {}
    for t in tables:
        if t.shape[1] != 2:
            continue
        for a, b in zip(t.iloc[:, 0], t.iloc[:, 1]):
            if pd.notna(a) and pd.notna(b):
                out.setdefault(re.sub(r"\s+", " ", str(a)).strip().rstrip(":"),
                               str(b).strip())
    return out


def find_table(tables: list[pd.DataFrame], *needles: str) -> pd.DataFrame | None:
    """First table whose column headers contain all the needles."""
    for t in tables:
        cols = " | ".join(str(c).lower() for c in t.columns)
        if all(n.lower() in cols for n in needles):
            return t
    return None


def parse_one(path: Path) -> dict:
    html = path.read_text(encoding="utf-8", errors="replace")
    soup = BeautifulSoup(html, "html.parser")
    try:
        tables = pd.read_html(StringIO(html))
    except ValueError:
        tables = []

    rec: dict = {"ipo_id": path.stem, "tables_found": len(tables), "problems": []}

    h1 = soup.find("h1")
    name = re.sub(r"\s+", " ", h1.get_text(" ", strip=True)) if h1 else ""
    rec["company"] = re.sub(r"\s*IPO\b.*$", "", name).strip() or None

    m = re.search(r"Recently Listed IPOs in ([^<|\n]+)", html)
    rec["sector"] = m.group(1).strip() if m else None

    kv = kv_from(tables)
    rec["_kv_keys"] = len(kv)
    rec["_kv"] = kv
    rec["_headers"] = [list(t.columns) for t in tables]

    def pick(*names):
        for n in names:
            for key, val in kv.items():
                if n.lower() in key.lower():
                    return val
        return None

    # "Listing At" is the exchange ("BSE SME", "BSE, NSE").
    # "Listed on" is a DATE. Reading the date for the exchange silently
    # marks every SME issue as mainboard, so keep them apart.
    exchange = pick("Listing At", "Listed At", "Exchange")
    rec["exchange"] = exchange
    rec["is_sme"] = bool(exchange and re.search(r"\bSME\b|EMERGE|\bSM\b", exchange, re.I))

    rec["issue_price"] = num(pick("Final Issue Price", "Issue Price"))
    rec["price_band_low"], rec["price_band_high"] = (None, None)
    band = pick("Price Band")
    if band:
        vals = [float(v) for v in NUMBER.findall(MONEY.sub("", band))]
        if len(vals) >= 2:
            rec["price_band_low"], rec["price_band_high"] = min(vals), max(vals)
    rec["lot_size"] = num(pick("Lot Size", "Market Lot"))
    # these labels read "<N> shares (agg. up to ₹<X> Cr)" — we want X
    rec["issue_size_cr"] = crore(pick("Total Issue Size", "Issue Size"))
    rec["fresh_issue_cr"] = crore(pick("Fresh Issue"))
    rec["ofs_cr"] = crore(pick("Offer for Sale"))

    # open and close arrive as one field: "IPO Date = 26 to 28 Nov, 2025"
    rec["issue_open"], rec["issue_close"] = date_range(
        pick("IPO Date", "Issue Date", "Bidding Date"))
    if rec["issue_open"] is None:
        rec["issue_open"] = date(pick("IPO Open Date", "Open Date"))
    if rec["issue_close"] is None:
        rec["issue_close"] = date(pick("IPO Close Date", "Close Date"))
    rec["listing_date"] = date(pick("Listed on", "Listing Date", "Listing on"))

    # subscription: Category | Subscription (times)
    subs = {}
    t = find_table(tables, "subscription")
    if t is not None and t.shape[1] >= 2:
        label_col = t.columns[0]
        sub_col = next((c for c in t.columns if "subscription" in str(c).lower()),
                       t.columns[1])
        for _, row in t.iterrows():
            raw = str(row[label_col]).strip()
            v = num(row[sub_col])
            if v is None:
                continue
            key = raw.lower()
            cat = ("QIB" if "qib" in key or "qualified" in key else
                   "NII" if "nii" in key or "non institutional" in key or "hni" in key else
                   "RII" if "rii" in key or "retail" in key else
                   "Employee" if "employee" in key else
                   "Total" if "total" in key else None)
            if cat:
                subs[cat] = v
    rec["subscription"] = subs
    if not subs:
        rec["problems"].append("no subscription figures")

    # listing-day prices
    lt = find_table(tables, "price details")
    rec["listing_open"] = rec["listing_close"] = None
    rec["listing_high"] = rec["listing_low"] = None
    if lt is not None and lt.shape[1] >= 2:
        for _, row in lt.iterrows():
            label = str(row.iloc[0]).lower()
            v = num(row.iloc[1])
            if v is None:
                continue
            if "open" in label:
                rec["listing_open"] = v
            elif "high" in label:
                rec["listing_high"] = v
            elif "low" in label:
                rec["listing_low"] = v
            elif "close" in label or "last trade" in label:
                rec["listing_close"] = v
    if rec["listing_open"] is None:
        rec["problems"].append("no listing-day open")
    if rec["issue_price"] is None:
        rec["problems"].append("no issue price")
    if rec["company"] is None:
        rec["problems"].append("no company name")
    return rec


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--report", action="store_true",
                    help="print what parsed and what didn't; write nothing")
    ap.add_argument("--from-year", type=int, default=2021)
    ap.add_argument("--include-sme", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    a = ap.parse_args()

    files = sorted(HTML_DIR.glob("*.html"),
                   key=lambda p: int(p.stem) if p.stem.isdigit() else 0,
                   reverse=True)
    if a.limit:
        files = files[: a.limit]
    if not files:
        print(f"No cached pages in {HTML_DIR}. Run scrape_chittorgarh.py fetch first.")
        return 1

    print(f"Parsing {len(files)} cached pages\n")
    recs = [parse_one(p) for p in files]

    if a.report:
        from collections import Counter
        probs = Counter(p for r in recs for p in r["problems"])
        print(f"  parsed            : {len(recs)}")
        print(f"  named a company   : {sum(1 for r in recs if r['company'])}")
        print(f"  found a sector    : {sum(1 for r in recs if r['sector'])}")
        print(f"  flagged SME       : {sum(1 for r in recs if r['is_sme'])}")
        print(f"  had subscriptions : {sum(1 for r in recs if r['subscription'])}")
        print(f"  had listing open  : {sum(1 for r in recs if r['listing_open'])}")
        print(f"  median tables/page: "
              f"{int(pd.Series([r['tables_found'] for r in recs]).median())}")
        print("\n  problems:")
        for k, v in probs.most_common():
            print(f"    {v:>5}  {k}")
        # pick a record that actually has content to show
        best = max(recs, key=lambda r: (bool(r["listing_open"]),
                                        bool(r["subscription"]), r["_kv_keys"]))
        print(f"\n  sample record (ipo_id {best['ipo_id']}):")
        for k, v in best.items():
            if k not in ("problems", "_kv", "_headers"):
                print(f"    {k:18} {v}")

        print(f"\n  ---- the {len(best['_kv'])} field labels on that page ----")
        for key, val in best["_kv"].items():
            print(f"    {key[:40]:42} = {str(val)[:46]}")

        print("\n  ---- every table's column headers on that page ----")
        for i, h in enumerate(best["_headers"]):
            print(f"    [{i:>2}] {str(h)[:110]}")
        return 0

    keep = [r for r in recs
            if r["company"] and (a.include_sme or not r["is_sme"])
            and (r["listing_date"] or "9999")[:4] >= str(a.from_year)]
    print(f"  keeping {len(keep)} of {len(recs)} "
          f"(mainboard{'+SME' if a.include_sme else ''}, {a.from_year}+)")

    OUT_DIR.mkdir(parents=True, exist_ok=True)
    ipo = pd.DataFrame([{
        "company": r["company"], "nse_symbol": None,
        "sector": r["sector"] or "Unclassified",
        "issue_open": r["issue_open"], "issue_close": r["issue_close"],
        "listing_date": r["listing_date"],
        "price_band_low": r["price_band_low"], "price_band_high": r["price_band_high"],
        "issue_price": r["issue_price"], "issue_size_cr": r["issue_size_cr"],
        "fresh_issue_cr": r["fresh_issue_cr"], "ofs_cr": r["ofs_cr"],
        "lot_size": r["lot_size"],
        "status": "listed" if r["listing_open"] and r["issue_price"] else "upcoming",
    } for r in keep]).drop_duplicates(subset=["company", "issue_open"])

    sub = pd.DataFrame([{"company": r["company"], "category": c,
                         "times_subscribed": v}
                        for r in keep for c, v in r["subscription"].items()])
    lst = pd.DataFrame([{"company": r["company"], "listing_open": r["listing_open"],
                         "listing_high": r["listing_high"],
                         "listing_low": r["listing_low"],
                         "listing_close": r["listing_close"]}
                        for r in keep if r["listing_open"]])

    for name, df in (("ipo", ipo), ("subscription", sub), ("listing", lst)):
        path = OUT_DIR / f"{name}.csv"
        df.to_csv(path, index=False)
        print(f"  {path.relative_to(ROOT)}: {len(df)} rows")
    print("\nNote: aftermarket closes are not on these pages — Q5 needs NSE/BSE.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
