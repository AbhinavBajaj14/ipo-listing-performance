#!/usr/bin/env python3
"""
scrape_chittorgarh.py — collect mainboard IPO pages from Chittorgarh.

Personal, non-commercial research use. Raw HTML and CSVs stay local:
data/ is gitignored and must not be committed or redistributed.

Three subcommands, run in this order:

    python scrape_chittorgarh.py discover
        Walks the published sitemap, collects every /ipo/<slug>/<id>/ URL,
        saves them to data/raw/ipo_urls.json

    python scrape_chittorgarh.py fetch --limit 10
        Downloads those pages and caches the HTML in data/raw/html/
        Resumable: already-cached pages are skipped.

    python scrape_chittorgarh.py inspect <url-or-cached-id>
        Prints every HTML table found on one page, with its headers.
        Use this to work out the parsing rules before writing a parser.

Fetching and parsing are deliberately separate. You fetch once, politely,
then parse the local copies as many times as you need while you get the
parsing right. Re-running a scraper every time you fix a bug is rude to
the source and slow for you.
"""

from __future__ import annotations

import argparse
import json
import random
import re
import sys
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import requests

# --- configuration ---------------------------------------------------------

BASE = "https://www.chittorgarh.com"
SITEMAP_INDEX = f"{BASE}/sitemap.xml"

# robots.txt declares no Crawl-delay, so we impose our own.
# Do not lower this. A free source you hammer is a source you lose.
DELAY_SECONDS = 2.5
TIMEOUT = 30
MAX_RETRIES = 3

# Identify yourself honestly. Put your real contact in here.
USER_AGENT = (
    "ipo-listing-research/0.1 (personal portfolio project; "
    "contact: bajajabhinav2002@gmail.com)"
)

PROJECT_ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = PROJECT_ROOT / "data" / "raw"
HTML_DIR = RAW_DIR / "html"
URLS_FILE = RAW_DIR / "ipo_urls.json"

# https://www.chittorgarh.com/ipo/some-company-ipo/1304/
IPO_URL_RE = re.compile(r"^https://www\.chittorgarh\.com/ipo/([^/]+)/(\d+)/?$")


# --- plumbing --------------------------------------------------------------

def make_session() -> requests.Session:
    s = requests.Session()
    s.headers.update({
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-IN,en;q=0.9",
    })
    return s


def polite_get(session: requests.Session, url: str) -> str | None:
    """GET with delay, retries and exponential backoff. Returns text or None."""
    for attempt in range(1, MAX_RETRIES + 1):
        # jitter stops us hitting the server on an exact rhythm
        time.sleep(DELAY_SECONDS + random.uniform(0, 0.75))
        try:
            r = session.get(url, timeout=TIMEOUT)
        except requests.RequestException as exc:
            print(f"    attempt {attempt}: {type(exc).__name__}: {exc}")
        else:
            if r.status_code == 200:
                return r.text
            if r.status_code == 404:
                print(f"    404 — skipping")
                return None
            if r.status_code == 429:
                wait = 30 * attempt
                print(f"    429 rate-limited — backing off {wait}s")
                time.sleep(wait)
                continue
            print(f"    attempt {attempt}: HTTP {r.status_code}")
        time.sleep(5 * attempt)
    print(f"    gave up after {MAX_RETRIES} attempts")
    return None


def loc_values(xml_text: str) -> list[str]:
    """Pull every <loc> value out of a sitemap, ignoring XML namespaces."""
    try:
        root = ET.fromstring(xml_text)
    except ET.ParseError as exc:
        print(f"    not valid XML: {exc}")
        return []
    out = []
    for el in root.iter():
        # tags look like '{http://www.sitemaps.org/schemas/sitemap/0.9}loc'
        if el.tag.rsplit("}", 1)[-1] == "loc" and el.text:
            out.append(el.text.strip())
    return out


# --- discover --------------------------------------------------------------

def cmd_discover(args: argparse.Namespace) -> int:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    session = make_session()

    print(f"Fetching sitemap index: {SITEMAP_INDEX}")
    index_xml = polite_get(session, SITEMAP_INDEX)
    if not index_xml:
        print("Could not read the sitemap index. Aborting.")
        return 1

    children = loc_values(index_xml)
    print(f"  {len(children)} child sitemaps\n")

    found: dict[str, dict] = {}
    for i, child in enumerate(children, 1):
        print(f"[{i}/{len(children)}] {child}")
        xml_text = polite_get(session, child)
        if not xml_text:
            continue
        hits = 0
        for url in loc_values(xml_text):
            m = IPO_URL_RE.match(url)
            if m:
                slug, ipo_id = m.group(1), m.group(2)
                if ipo_id not in found:
                    found[ipo_id] = {"ipo_id": ipo_id, "slug": slug, "url": url}
                    hits += 1
        if hits:
            print(f"    +{hits} IPO urls (total {len(found)})")

    URLS_FILE.write_text(
        json.dumps(sorted(found.values(), key=lambda d: int(d["ipo_id"])), indent=2),
        encoding="utf-8",
    )
    print(f"\nSaved {len(found)} IPO urls to {URLS_FILE}")
    if not found:
        print("No IPO urls found. Run 'inspect' on a child sitemap to see why.")
        return 1
    return 0


# --- fetch -----------------------------------------------------------------

def cmd_fetch(args: argparse.Namespace) -> int:
    if not URLS_FILE.exists():
        print(f"{URLS_FILE} missing. Run 'discover' first.")
        return 1

    HTML_DIR.mkdir(parents=True, exist_ok=True)
    records = json.loads(URLS_FILE.read_text(encoding="utf-8"))

    # newest ids first — recent IPOs are the ones you care about most
    records.sort(key=lambda d: int(d["ipo_id"]), reverse=True)

    if args.min_id:
        records = [r for r in records if int(r["ipo_id"]) >= args.min_id]

    remaining = [r for r in records
                 if not (HTML_DIR / f"{r['ipo_id']}.html").exists()]
    cached = len(records) - len(remaining)

    todo = remaining[: args.limit] if args.limit else remaining

    print(f"{len(records)} in range, {cached} already cached, "
          f"{len(remaining)} still missing — fetching {len(todo)} now")
    if todo:
        secs = int(len(todo) * (DELAY_SECONDS + 0.4))
        print(f"estimated time: {secs // 60}m {secs % 60}s\n")

    session = make_session()
    ok = 0
    for i, rec in enumerate(todo, 1):
        print(f"[{i}/{len(todo)}] {rec['slug']} ({rec['ipo_id']})")
        html = polite_get(session, rec["url"])
        if html:
            (HTML_DIR / f"{rec['ipo_id']}.html").write_text(html, encoding="utf-8")
            ok += 1

    print(f"\nCached {ok} pages in {HTML_DIR}")
    return 0


# --- inspect ---------------------------------------------------------------

def cmd_inspect(args: argparse.Namespace) -> int:
    try:
        import pandas as pd
    except ImportError:
        print("pandas not installed. Run: pip install pandas lxml html5lib requests")
        return 1

    target = args.target
    if target.startswith("http"):
        html = polite_get(make_session(), target)
        if not html:
            return 1
    else:
        path = HTML_DIR / f"{target}.html"
        if not path.exists():
            print(f"No cached page at {path}")
            return 1
        html = path.read_text(encoding="utf-8")

    from io import StringIO
    try:
        tables = pd.read_html(StringIO(html))
    except ValueError as exc:
        print(f"pandas found no tables: {exc}")
        return 1

    print(f"\n{len(tables)} tables found\n" + "=" * 70)
    for i, t in enumerate(tables):
        print(f"\n--- table[{i}]  shape={t.shape}")
        print(f"    columns: {list(t.columns)}")
        with pd.option_context("display.max_columns", None, "display.width", 200):
            print(t.head(6).to_string())
    return 0


# --- entry point -----------------------------------------------------------

def main() -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="command", required=True)

    sub.add_parser("discover", help="walk the sitemap for IPO urls")

    f = sub.add_parser("fetch", help="download and cache IPO pages")
    f.add_argument("--limit", type=int, default=0,
                   help="stop after N pages (use a small number first)")
    f.add_argument("--min-id", type=int, default=0, dest="min_id",
                   help="ignore IPO ids below this (bounds the job by rough date)")

    i = sub.add_parser("inspect", help="dump all tables on one page")
    i.add_argument("target", help="full url, or a cached ipo_id")

    args = p.parse_args()
    return {"discover": cmd_discover,
            "fetch": cmd_fetch,
            "inspect": cmd_inspect}[args.command](args)


if __name__ == "__main__":
    sys.exit(main())
