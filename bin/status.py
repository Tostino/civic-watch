"""Progress report for the ingest run."""
import time

import db

con = db.connect()
r = con.execute("""
    SELECT COUNT(*) n, SUM(duration)/3600.0 hrs,
           COUNT(*) FILTER (WHERE downloaded)  dl,
           COUNT(*) FILTER (WHERE diarized)    di,
           COUNT(*) FILTER (WHERE transcribed) tr,
           SUM(duration) FILTER (WHERE transcribed)/3600.0 done_hrs,
           COUNT(*) FILTER (WHERE error IS NOT NULL) err
    FROM videos""").fetchone()

print(f"catalog     {r['n']} videos, {r['hrs']:.0f} hours")
print(f"downloaded  {r['dl'] or 0:>4} / {r['n']}")
print(f"diarized    {r['di'] or 0:>4} / {r['n']}")
print(f"transcribed {r['tr'] or 0:>4} / {r['n']}   "
      f"({r['done_hrs'] or 0:.0f} of {r['hrs']:.0f} hours)")
if r["err"]:
    print(f"errors      {r['err']}")

pct = (r["done_hrs"] or 0) / r["hrs"] * 100 if r["hrs"] else 0
print(f"\n{pct:.1f}% complete by audio duration")

words = con.execute("SELECT SUM(words) FROM videos").fetchone()[0] or 0
utts = con.execute("SELECT COUNT(*) FROM utterances").fetchone()[0]
print(f"indexed: {utts:,} utterances, {words:,} words")

active = con.execute(
    "SELECT claimed_by, id, title, duration FROM videos "
    "WHERE claimed_by IS NOT NULL ORDER BY claimed_by").fetchall()
if active:
    print("\nin flight:")
    for a in active:
        print(f"  {a['claimed_by']:<8} {a['id']}  {a['duration']/60:>5.0f}min  "
              f"{a['title'][:52]}")

recent = con.execute(
    "SELECT id, title, words, gap_seconds, updated_at FROM videos "
    "WHERE transcribed ORDER BY updated_at DESC LIMIT 5").fetchall()
if recent:
    print("\nmost recently finished:")
    for x in recent:
        print(f"  {x['id']}  {x['words'] or 0:>6} words  "
              f"{x['gap_seconds'] or 0:>4.0f}s weak  {x['title'][:50]}")

errs = con.execute(
    "SELECT id, error FROM videos WHERE error IS NOT NULL LIMIT 5").fetchall()
if errs:
    print("\nerrors:")
    for e in errs:
        print(f"  {e['id']}: {e['error'][:90]}")
