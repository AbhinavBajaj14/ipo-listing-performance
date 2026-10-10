#!/usr/bin/env python3
"""
build_dashboard.py — query the database and emit a self-contained dashboard.

Writes docs/dashboard.html with the data inlined, so the file opens from
disk with no server, no CDN and no network. That matters for a portfolio
piece: whoever opens it should see the charts, not a broken import.

Usage:
    python build_dashboard.py --host localhost --port 5432 --db ipo_analysis
"""

from __future__ import annotations

import argparse
import getpass
import json
import os
from datetime import datetime, timezone
from pathlib import Path

import psycopg2

OUT = Path(__file__).resolve().parent.parent / "docs" / "dashboard.html"
TEMPLATE = Path(__file__).resolve().parent / "dashboard_template.html"

Q_KPI = """
SELECT count(*),
       PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY listing_gain_pct),
       100.0 * AVG((listing_gain_pct > 0)::int),
       MIN(listing_gain_pct), MAX(listing_gain_pct)
FROM v_listing_gain;
"""

Q_DECAY = """
SELECT count(*),
       PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY listing_gain_pct),
       PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY gain_d7_pct),
       PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY gain_d30_pct),
       100.0 * AVG((gain_d30_pct > 0)::int),
       100.0 * AVG((gain_d30_pct < listing_gain_pct)::int)
FROM v_gain_decay WHERE gain_d30_pct IS NOT NULL;
"""

Q_GAINS = "SELECT listing_gain_pct FROM v_listing_gain ORDER BY 1;"

Q_SCATTER = """
SELECT company, total_sub, listing_gain_pct
FROM v_listing_gain
WHERE total_sub IS NOT NULL AND total_sub > 0
ORDER BY total_sub;
"""

Q_SECTORS = """
SELECT sector, count(*),
       PERCENTILE_CONT(0.5) WITHIN GROUP (ORDER BY listing_gain_pct),
       100.0 * AVG((listing_gain_pct > 0)::int)
FROM v_listing_gain
GROUP BY sector
HAVING count(*) >= 2
ORDER BY 3 DESC;
"""

Q_QUARTERS = """
WITH regime AS (
    SELECT listing_date, listing_gain_pct,
           AVG(listing_gain_pct) OVER (
               ORDER BY listing_date ROWS BETWEEN 10 PRECEDING AND 1 PRECEDING
           ) AS prior_10_avg
    FROM v_listing_gain
)
SELECT DATE_TRUNC('quarter', listing_date)::date, count(*),
       AVG(listing_gain_pct), AVG(prior_10_avg)
FROM regime WHERE prior_10_avg IS NOT NULL
GROUP BY 1 ORDER BY 1;
"""

Q_SYNTH = """
SELECT EXISTS (SELECT 1 FROM ipo WHERE company LIKE 'Synthetic Issuer%');
"""


def f(x, nd=1):
    return None if x is None else round(float(x), nd)


def histogram(values, width=10.0):
    if not values:
        return []
    lo = width * (min(values) // width)
    hi = width * (max(values) // width + 1)
    bins, edge = [], lo
    while edge < hi:
        n = sum(1 for v in values if edge <= v < edge + width)
        bins.append({"lo": edge, "hi": edge + width, "n": n})
        edge += width
    return bins


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--host", default=os.environ.get("PGHOST", "localhost"))
    ap.add_argument("--port", default=os.environ.get("PGPORT", "5432"))
    ap.add_argument("--db", default=os.environ.get("PGDATABASE", "ipo_analysis"))
    ap.add_argument("--user", default=os.environ.get("PGUSER", "postgres"))
    ap.add_argument("--password", default=os.environ.get("PGPASSWORD"))
    a = ap.parse_args()

    pw = a.password
    if pw is None and not a.host.startswith("/"):
        pw = getpass.getpass(f"Password for {a.user}@{a.host}: ")

    conn = psycopg2.connect(host=a.host, port=a.port, dbname=a.db,
                            user=a.user, password=pw)
    cur = conn.cursor()

    cur.execute(Q_SYNTH); is_synth = cur.fetchone()[0]
    cur.execute(Q_KPI); n, med, hit, worst, best = cur.fetchone()
    cur.execute(Q_DECAY); dn, d0, d7, d30, pos30, faded = cur.fetchone()
    cur.execute(Q_GAINS); gains = [float(r[0]) for r in cur.fetchall()]
    cur.execute(Q_SCATTER)
    scatter = [{"c": r[0], "sub": f(r[1], 2), "gain": f(r[2], 2)}
               for r in cur.fetchall()]
    cur.execute(Q_SECTORS)
    sectors = [{"sector": r[0], "n": int(r[1]), "median": f(r[2]),
                "pct_pos": f(r[3], 0), "thin": int(r[1]) < 8}
               for r in cur.fetchall()]
    cur.execute(Q_QUARTERS)
    quarters = [{"q": str(r[0]), "n": int(r[1]), "avg": f(r[2]),
                 "regime": f(r[3])} for r in cur.fetchall()]

    cur.close(); conn.close()

    data = {
        "generated": datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "is_synthetic": bool(is_synth),
        "kpi": {"n_listed": int(n), "median_gain": f(med), "hit_rate": f(hit, 0),
                "worst": f(worst), "best": f(best),
                "median_d30": f(d30), "pct_faded": f(faded, 0),
                "pos_d30": f(pos30, 0), "n_decay": int(dn)},
        "histogram": histogram(gains),
        "scatter": scatter,
        "sectors": sectors,
        "decay": [{"label": "Listing day", "v": f(d0)},
                  {"label": "+1 week", "v": f(d7)},
                  {"label": "+1 month", "v": f(d30)}],
        "quarters": quarters,
    }

    html = TEMPLATE.read_text(encoding="utf-8").replace(
        "/*__DATA__*/null", json.dumps(data, separators=(",", ":")))
    OUT.write_text(html, encoding="utf-8")
    print(f"Wrote {OUT}  ({OUT.stat().st_size // 1024} KB)")
    print(f"  listed={data['kpi']['n_listed']} "
          f"median_gain={data['kpi']['median_gain']}% "
          f"synthetic={data['is_synthetic']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
