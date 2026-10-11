"""Unit tests for the fiddly parsing helpers.

Every case here is a real label value taken off a Chittorgarh IPO page.
These are the parts that fail silently if they're wrong — a share count
mistaken for a rupee-crore figure produces a number, just the wrong one.
"""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from parse_ipos import crore, date_range, num, date   # noqa: E402

CRORE_CASES = [
    ("33,84,000 shares (agg. up to ₹40 Cr)", 40.0),
    ("24,63,600 shares (agg. up to ₹29 Cr)", 29.0),
    ("6,76,800 shares of ₹10 (agg. up to ₹8 Cr)", 8.0),
    ("2,43,600 shares (agg. up to ₹3 Cr)Mansi Share", 3.0),
    ("₹1,234.50 Cr", 1234.50),
    ("98,27,820 shares", None),          # no crore figure at all
    (None, None),
]

DATE_RANGE_CASES = [
    ("26 to 28 Nov, 2025", ("2025-11-26", "2025-11-28")),
    ("28 Nov to 2 Dec, 2025", ("2025-11-28", "2025-12-02")),   # crosses a month
    ("30 Dec to 1 Jan, 2025", ("2024-12-30", "2025-01-01")),   # crosses a year
    ("30 Dec, 2024 to 1 Jan, 2025", ("2024-12-30", "2025-01-01")),
    ("29 to 2 Jul, 2024", ("2024-06-29", "2024-07-02")),
    ("Wed, Dec 3, 2025", ("2025-12-03", "2025-12-03")),        # single date
    ("", (None, None)),
]

SCALAR_CASES = [
    (num, "₹117 per share", 117.0),
    (num, "₹117.00", 117.0),
    (num, "1,200 Shares", 1200.0),
    (date, "Wed, Dec 3, 2025", "2025-12-03"),
]


def run():
    failures = []
    for text, want in CRORE_CASES:
        got = crore(text)
        if got != want:
            failures.append(f"crore({text!r}) = {got}, want {want}")
    for text, want in DATE_RANGE_CASES:
        got = date_range(text)
        if got != want:
            failures.append(f"date_range({text!r}) = {got}, want {want}")
    for fn, text, want in SCALAR_CASES:
        got = fn(text)
        if got != want:
            failures.append(f"{fn.__name__}({text!r}) = {got}, want {want}")

    total = len(CRORE_CASES) + len(DATE_RANGE_CASES) + len(SCALAR_CASES)
    if failures:
        print(f"{len(failures)} of {total} FAILED")
        for f in failures:
            print("  " + f)
        return 1
    print(f"all {total} cases pass")
    return 0


if __name__ == "__main__":
    raise SystemExit(run())
