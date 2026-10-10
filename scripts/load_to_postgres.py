#!/usr/bin/env python3
"""
load_to_postgres.py — load CSVs into the five tables.

Idempotent: run it as many times as you like. The ipo table upserts on its
natural key (company, issue_open); child tables are replaced for the issues
present in the incoming files, so a reload never duplicates rows.

CSVs identify their parent issue by `company`, not by ipo_id. ipo_id is a
surrogate key the database owns; nothing outside the database should invent
one. The loader resolves company -> ipo_id after loading the parent table.

Usage:
    python load_to_postgres.py --source data/sample
    python load_to_postgres.py --source data/processed --db ipo_analysis

Connection defaults suit a stock local install (localhost:5432, user
postgres). Set PGPASSWORD, or pass --password, to avoid a prompt.
"""

from __future__ import annotations

import argparse
import getpass
import os
import sys
from pathlib import Path

import pandas as pd
import psycopg2
from psycopg2.extras import execute_values

# column lists, in table order; `company` is resolved to ipo_id before insert
CHILD_SPECS = {
    "subscription":   ["category", "times_subscribed"],
    "listing":        ["listing_open", "listing_high", "listing_low", "listing_close"],
    "aftermarket":    ["trade_date", "close_price"],
    "gmp_unofficial": ["observed_date", "gmp_value", "source"],
}

IPO_COLS = ["company", "nse_symbol", "sector", "issue_open", "issue_close",
            "listing_date", "price_band_low", "price_band_high", "issue_price",
            "issue_size_cr", "fresh_issue_cr", "ofs_cr", "lot_size", "status"]


def read_csv(path: Path) -> pd.DataFrame | None:
    if not path.exists():
        print(f"  {path.name}: not found, skipping")
        return None
    df = pd.read_csv(path)
    if df.get("is_synthetic", pd.Series([False])).any():
        print(f"  {path.name}: SYNTHETIC data")
    df = df.drop(columns=[c for c in ("is_synthetic",) if c in df.columns])
    return df


def rows_of(df: pd.DataFrame, cols: list[str]) -> list[tuple]:
    """DataFrame -> list of tuples, with NaN/NaT turned into None."""
    missing = [c for c in cols if c not in df.columns]
    if missing:
        raise SystemExit(f"  missing expected columns: {missing}")
    sub = df[cols].astype(object).where(pd.notna(df[cols]), None)
    return [tuple(r) for r in sub.itertuples(index=False, name=None)]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default="data/sample",
                    help="directory holding ipo.csv, subscription.csv, ...")
    ap.add_argument("--host", default=os.environ.get("PGHOST", "localhost"))
    ap.add_argument("--port", default=os.environ.get("PGPORT", "5432"))
    ap.add_argument("--db", default=os.environ.get("PGDATABASE", "ipo_analysis"))
    ap.add_argument("--user", default=os.environ.get("PGUSER", "postgres"))
    ap.add_argument("--password", default=os.environ.get("PGPASSWORD"))
    args = ap.parse_args()

    src = Path(args.source)
    if not src.is_dir():
        print(f"No such directory: {src}")
        return 1

    pw = args.password
    if pw is None and args.host not in ("/tmp",) and not args.host.startswith("/"):
        pw = getpass.getpass(f"Password for {args.user}@{args.host}: ")

    print(f"Loading from {src} into {args.db} on {args.host}:{args.port}\n")

    conn = psycopg2.connect(host=args.host, port=args.port, dbname=args.db,
                            user=args.user, password=pw)
    conn.autocommit = False
    cur = conn.cursor()

    try:
        # ---- parent table -------------------------------------------------
        ipo_df = read_csv(src / "ipo.csv")
        if ipo_df is None:
            print("ipo.csv is required.")
            return 1

        execute_values(cur, f"""
            INSERT INTO ipo ({','.join(IPO_COLS)})
            VALUES %s
            ON CONFLICT (company, issue_open) DO UPDATE SET
                nse_symbol      = EXCLUDED.nse_symbol,
                sector          = EXCLUDED.sector,
                issue_close     = EXCLUDED.issue_close,
                listing_date    = EXCLUDED.listing_date,
                price_band_low  = EXCLUDED.price_band_low,
                price_band_high = EXCLUDED.price_band_high,
                issue_price     = EXCLUDED.issue_price,
                issue_size_cr   = EXCLUDED.issue_size_cr,
                fresh_issue_cr  = EXCLUDED.fresh_issue_cr,
                ofs_cr          = EXCLUDED.ofs_cr,
                lot_size        = EXCLUDED.lot_size,
                status          = EXCLUDED.status
        """, ipo_rows := rows_of(ipo_df, IPO_COLS))
        # NB: cur.rowcount after execute_values reports only the final batch
        # (page_size defaults to 100), so count the input instead.
        print(f"  ipo: {len(ipo_rows)} rows upserted")

        cur.execute("SELECT company, ipo_id FROM ipo")
        id_of = dict(cur.fetchall())

        # ---- child tables -------------------------------------------------
        for table, cols in CHILD_SPECS.items():
            df = read_csv(src / f"{table}.csv")
            if df is None or df.empty:
                continue

            df = df.copy()
            df["__id"] = df["company"].map(id_of)
            orphans = int(df["__id"].isna().sum())
            if orphans:
                print(f"  {table}: {orphans} rows dropped — company not in ipo table")
                df = df[df["__id"].notna()]
            if df.empty:
                continue
            df["__id"] = df["__id"].astype(int)

            # replace, don't accumulate: clear only the issues we are reloading
            ids = tuple(sorted(set(df["__id"])))
            cur.execute(f"DELETE FROM {table} WHERE ipo_id IN %s", (ids,))
            deleted = cur.rowcount

            child_rows = rows_of(df, ["__id"] + cols)
            execute_values(cur,
                f"INSERT INTO {table} (ipo_id, {','.join(cols)}) VALUES %s",
                child_rows)
            print(f"  {table}: {len(child_rows)} rows inserted "
                  f"({deleted} replaced)")

        conn.commit()

    except Exception:
        conn.rollback()
        print("\nRolled back — nothing was written.")
        raise
    finally:
        cur.close()
        conn.close()

    print("\nDone.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
