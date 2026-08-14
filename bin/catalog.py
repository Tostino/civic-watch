"""Populate the catalog from the channel. Safe to re-run to pick up new meetings.

Two tabs must be scraped: /streams holds the live-broadcast meetings (which is
almost all of them) and /videos holds everything else. They do not overlap, and
querying only /videos silently misses the entire meeting archive.

Meeting dates are parsed from titles ("8.11.26 ...", "09.19.2023 ...") because
the flat playlist listing does not expose upload dates.
"""
import argparse
import re
import subprocess
import sys

import db

TABS = ["https://www.youtube.com/@PascoCountyGovernment/streams",
        "https://www.youtube.com/@PascoCountyGovernment/videos"]
YTDLP = "/home/user/.local/bin/yt-dlp"

KINDS = [
    ("bcc", r"board of county commissioner|\bbcc\b"),
    ("planning", r"planning commission"),
    ("mpo", r"\bmpo\b|metropolitan planning"),
    ("workshop", r"workshop"),
]

DATE_RE = re.compile(r"^\s*(\d{1,2})[.\-/](\d{1,2})[.\-/](\d{2,4})")


def parse_date(title):
    m = DATE_RE.match(title)
    if not m:
        return None
    mo, day, yr = m.groups()
    yr = int(yr)
    if yr < 100:
        yr += 2000
    if not (2000 <= yr <= 2100 and 1 <= int(mo) <= 12 and 1 <= int(day) <= 31):
        return None
    return f"{yr:04d}-{int(mo):02d}-{int(day):02d}"


def classify(title):
    for kind, pat in KINDS:
        if re.search(pat, title, re.I):
            return kind
    return "other"


def fetch(tab):
    out = subprocess.run(
        [YTDLP, "--flat-playlist", "--print", "%(id)s\t%(title)s\t%(duration)s",
         tab], capture_output=True, text=True, timeout=2400)
    rows = []
    for line in out.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) != 3:
            continue
        vid, title, dur = parts
        try:
            duration = float(dur)
        except ValueError:
            continue
        rows.append((vid, title, duration))
    return rows


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-duration", type=float, default=1800)
    ap.add_argument("--kinds", default="bcc,planning",
                    help="comma-separated kinds to enqueue, or 'all'")
    args = ap.parse_args()

    con = db.init()
    wanted = None if args.kinds == "all" else set(args.kinds.split(","))

    seen, rows = set(), []
    for tab in TABS:
        got = fetch(tab)
        source = "streams" if tab.endswith("/streams") else "videos"
        print(f"{source}: {len(got)} videos")
        for vid, title, duration in got:
            if vid in seen:
                continue
            seen.add(vid)
            rows.append((vid, title, duration, parse_date(title),
                         classify(title), source))
    if not rows:
        print("no videos returned - channel unreachable?", file=sys.stderr)
        return 1

    keep = [r for r in rows if r[2] >= args.min_duration
            and (wanted is None or r[4] in wanted)]
    before = con.execute("SELECT COUNT(*) FROM videos").fetchone()[0]
    with con.cursor() as cur:
        cur.executemany(
            "INSERT INTO videos "
            "(id, title, duration, upload_date, kind, source) "
            "VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (id) DO NOTHING", keep)
    con.commit()
    after = con.execute("SELECT COUNT(*) FROM videos").fetchone()[0]

    print(f"\n{len(seen)} unique videos on channel, {len(keep)} match filters")
    print(f"catalog: {before} -> {after} ({after - before} new)")
    for r in con.execute(
            "SELECT kind, COUNT(*) n, SUM(duration)/3600.0 h FROM videos "
            "GROUP BY kind ORDER BY h DESC"):
        print(f"  {r['kind']:<10} {r['n']:>4} videos  {r['h']:>7.1f} hr")
    undated = con.execute(
        "SELECT COUNT(*) FROM videos WHERE upload_date IS NULL").fetchone()[0]
    if undated:
        print(f"  ({undated} without a parseable date - processed last)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
