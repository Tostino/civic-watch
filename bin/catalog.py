"""Populate the catalog from the channel. Safe to re-run to pick up new meetings.

Two tabs must be scraped: /streams holds the live-broadcast meetings (which is
almost all of them) and /videos holds everything else. They do not overlap, and
querying only /videos silently misses the entire meeting archive."""
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

# A date ANYWHERE in the title, not only at the front, and spelled out as
# well as numeric. This used to be `^\s*(\d{1,2})[.\-/]...`, anchored, and the
# anchor is what stranded 17 recordings: the county writes its regular
# meetings as "8.11.26 ..." but its workshops freehand - "Pasco BCC
# Legislative Workshop (8.24.23)", "Board of County Commissioners Emergency
# Mtg 09-24-2024", "Pasco County BCC Workshop, October 17, 2017". Every one
# of those read as undated.
DATE_RE = re.compile(
    r"(?<!\d)(\d{1,2})\s*[.\-/]\s*(\d{1,2})\s*[.\-/]\s*(\d{2,4})(?!\d)")
MONTHS = ("january", "february", "march", "april", "may", "june", "july",
          "august", "september", "october", "november", "december")
# "October 17, 2017" and "Oct. 17 2017" alike. The month must be followed by
# a day, so the word "may" in a title cannot become a date on its own.
WORDY_RE = re.compile(
    r"\b(" + "|".join(m[:3] for m in MONTHS) + r")\w*\.?\s+(\d{1,2})\s*,?\s+(\d{4})",
    re.I)


def _ymd(mo, day, yr):
    """A date, or None if those three numbers cannot be one."""
    mo, day, yr = int(mo), int(day), int(yr)
    if yr < 100:
        yr += 2000
    if not (2000 <= yr <= 2100 and 1 <= mo <= 12 and 1 <= day <= 31):
        return None
    return f"{yr:04d}-{mo:02d}-{day:02d}"


def parse_date(title):
    """The meeting date a title carries, or None."""
    for i in range(len(title)):
        m = DATE_RE.match(title, i)
        if m:
            got = _ymd(*m.groups())
            if got:
                return got
    m = WORDY_RE.search(title)
    if m:
        return _ymd(MONTHS.index(next(x for x in MONTHS
                                      if x.startswith(m.group(1).lower()))) + 1,
                    m.group(2), m.group(3))
    return None


def classify(title):
    for kind, pat in KINDS:
        if re.search(pat, title, re.I):
            return kind
    return "other"


# A broadcast that is not over is not a recording. yt-dlp's own states:
# is_upcoming, is_live, and post_live - the dangerous one, where the stream
# has ended, the VOD is still being cut, and a duration can be present and
# wrong. Previously this was an accident: `float("NA")` raising.
UNFINISHED = {"is_upcoming", "is_live", "post_live"}


def fetch(tab):
    out = subprocess.run(
        [YTDLP, "--flat-playlist",
         "--print", "%(id)s\t%(title)s\t%(duration)s\t%(live_status)s",
         tab], capture_output=True, text=True, timeout=2400)
    rows, held = [], []
    for line in out.stdout.splitlines():
        parts = line.split("\t")
        if len(parts) != 4:
            continue
        vid, title, dur, live = parts
        if live in UNFINISHED:
            held.append((vid, title, live))
            continue
        try:
            duration = float(dur)
        except ValueError:
            continue
        rows.append((vid, title, duration))
    for vid, title, live in held:
        print(f"  holding {vid} ({live}): {title[:60]}")
    if held:
        print(f"  {len(held)} not finished - they will be catalogued by a "
              f"later sweep, once YouTube has finished with them")
    return rows


def redate(con):
    """Give a date to catalogued videos that have none. Returns how many."""
    todo = [(r["id"], r["title"]) for r in con.execute(
        "SELECT id, title FROM videos WHERE upload_date IS NULL").fetchall()]
    fixed = [(parse_date(t), v) for v, t in todo]
    fixed = [(d, v) for d, v in fixed if d]
    if fixed:
        with con.cursor() as cur:
            cur.executemany(
                "UPDATE videos SET upload_date = %s WHERE id = %s", fixed)
        con.commit()
        print("  run bin/land_agenda.py to attach them to their meetings")
    return len(fixed)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-duration", type=float, default=1800)
    ap.add_argument("--kinds", default="bcc,planning",
                    help="comma-separated kinds to enqueue, or 'all'")
    # The backfill below runs on every pass anyway. This flag is for the case
    # that produced it: the parser got better, and the only thing needed is
    # to re-read titles already in the table. Scraping two YouTube tabs to
    # re-parse strings we already hold is minutes of network for nothing.
    ap.add_argument("--redate", action="store_true",
                    help="re-parse the dates of catalogued videos and exit; "
                         "does not touch the channel")
    args = ap.parse_args()

    con = db.init()
    if args.redate:
        print(f"redated {redate(con)} video(s)")
        return 0
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
    # DO NOTHING froze a duration that was wrong once. It corrects itself now,
    # but ONLY before download: after that the audio, diarization and
    # transcript are all measured against it. 1OmEmpL-7qY holds 7451 where
    # YouTube says 7410, so without the guard a sweep would make it worse.
    with con.cursor() as cur:
        cur.executemany(
            "INSERT INTO videos "
            "(id, title, duration, upload_date, kind, source) "
            "VALUES (%s,%s,%s,%s,%s,%s) ON CONFLICT (id) DO UPDATE "
            "SET duration = EXCLUDED.duration "
            "WHERE NOT videos.downloaded "
            "  AND videos.duration IS DISTINCT FROM EXCLUDED.duration", keep)
    con.commit()
    after = con.execute("SELECT COUNT(*) FROM videos").fetchone()[0]

    print(f"\n{len(seen)} unique videos on channel, {len(keep)} match filters")
    print(f"catalog: {before} -> {after} ({after - before} new)")
    for r in con.execute(
            "SELECT kind, COUNT(*) n, SUM(duration)/3600.0 h FROM videos "
            "GROUP BY kind ORDER BY h DESC"):
        print(f"  {r['kind']:<10} {r['n']:>4} videos  {r['h']:>7.1f} hr")
    print(f"redated {redate(con)} video(s)")
    undated = con.execute(
        "SELECT COUNT(*) FROM videos WHERE upload_date IS NULL").fetchone()[0]
    if undated:
        print(f"  ({undated} without a parseable date - processed last)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
