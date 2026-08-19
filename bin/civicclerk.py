"""Ingest Pasco County's published agendas and minutes from CivicClerk."""
import argparse
import concurrent.futures as cf
import json
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

import db

BASE = "https://pascocofl.api.civicclerk.com/v1"
PAGE = 15                 # server-enforced; stated here so it is not a mystery
WANT = ("Agenda", "Minutes")   # Agenda Packet is the full backup, often 100MB+
WORKERS = 4
PAUSE = 0.25              # be a polite guest on a county web server


def fetch(url, as_json=True, retries=3):
    req = urllib.request.Request(url, headers={
        "Accept": "application/json" if as_json else "text/plain",
        "User-Agent": "pasco-meeting-archive/1.0 (research; contact via repo)"})
    last = None
    for attempt in range(retries):
        try:
            with urllib.request.urlopen(req, timeout=90) as r:
                raw = r.read()
            return json.loads(raw) if as_json else raw.decode("utf-8", "replace")
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as e:
            last = e
            time.sleep(1.5 * (attempt + 1))
    raise RuntimeError(f"{url} failed: {last}")


def walk_events(since=None):
    """Every event, oldest first. Follows nextLink because $top is ignored."""
    q = {"$orderby": "eventDate asc"}
    if since:
        q["$filter"] = f"eventDate ge {since}T00:00:00Z"
    url = f"{BASE}/Events?" + urllib.parse.urlencode(q)
    while url:
        d = fetch(url)
        yield from d["value"]
        url = d.get("@odata.nextLink")
        time.sleep(PAUSE)


def file_text(file_id):
    """The server's own text extraction. No PDF parsing anywhere in here."""
    t = fetch(f"{BASE}/Meetings/GetMeetingFileStream"
              f"(fileId={file_id},plainText=true)", as_json=False)
    # Some extracted PDFs carry NUL bytes, which Postgres text cannot hold.
    # Strip rather than escape: they are extraction artefacts, not content.
    return t.replace("\x00", "")


def sync_events(con, since=None):
    """Upsert the event list and the file manifest. Cheap; run it often."""
    n_ev = n_file = 0
    for e in walk_events(since):
        con.execute("""
            INSERT INTO portal_events
                (id, name, body, event_date, agenda_id, has_agenda, raw, fetched_at)
            VALUES (%s,%s,%s,%s,%s,%s,%s, now())
            ON CONFLICT (id) DO UPDATE SET
                name=EXCLUDED.name, body=EXCLUDED.body,
                event_date=EXCLUDED.event_date, agenda_id=EXCLUDED.agenda_id,
                has_agenda=EXCLUDED.has_agenda, raw=EXCLUDED.raw,
                fetched_at=now()""",
            (e["id"], e.get("eventName"), e.get("categoryName"),
             e.get("eventDate"), e.get("agendaId"), e.get("hasAgenda"),
             json.dumps(e)))
        n_ev += 1
        for f in (e.get("publishedFiles") or []):
            if f.get("type") not in WANT or not f.get("fileId"):
                continue
            # body_text is deliberately not touched on conflict: re-syncing the
            # manifest must not throw away text already fetched.
            con.execute("""
                INSERT INTO portal_files
                    (file_id, event_id, kind, name, published_at)
                VALUES (%s,%s,%s,%s,%s)
                ON CONFLICT (file_id) DO UPDATE SET
                    kind=EXCLUDED.kind, name=EXCLUDED.name,
                    published_at=EXCLUDED.published_at""",
                (f["fileId"], e["id"], f.get("type"), f.get("name"),
                 f.get("publishOn")))
            n_file += 1
        if n_ev % 150 == 0:
            con.commit()
            print(f"  {n_ev} events, {n_file} files ...", flush=True)
    con.commit()
    return n_ev, n_file


def sync_text(con, limit=0, workers=WORKERS):
    """Fetch the text of every file we do not have yet. Resumable by design."""
    rows = con.execute(
        "SELECT file_id, kind, name FROM portal_files WHERE body_text IS NULL "
        "ORDER BY file_id DESC" + (f" LIMIT {int(limit)}" if limit else "")
    ).fetchall()
    con.commit()
    if not rows:
        print("  no files pending")
        return 0
    print(f"  {len(rows)} files to fetch", flush=True)

    def one(r):
        try:
            return r["file_id"], file_text(r["file_id"]), None
        except Exception as e:
            return r["file_id"], None, f"{type(e).__name__}: {e}"

    done = failed = 0
    with cf.ThreadPoolExecutor(max_workers=workers) as ex:
        for fid, text, err in ex.map(one, rows):
            if err:
                failed += 1
                print(f"    file {fid}: {err[:90]}", flush=True)
                continue
            con.execute("UPDATE portal_files SET body_text=%s, chars=%s, "
                        "fetched_at=now() WHERE file_id=%s",
                        (text, len(text), fid))
            done += 1
            if done % 50 == 0:
                con.commit()
                print(f"    {done}/{len(rows)}", flush=True)
    con.commit()
    print(f"  fetched {done}, failed {failed}")
    return done


def report(con):
    r = con.execute("""SELECT COUNT(*) n, MIN(event_date)::date lo,
                              MAX(event_date)::date hi FROM portal_events""").fetchone()
    print(f"\nportal_events  {r['n']:,}   {r['lo']} .. {r['hi']}")
    for x in con.execute("SELECT body, COUNT(*) n FROM portal_events "
                         "GROUP BY body ORDER BY n DESC LIMIT 8"):
        print(f"    {x['n']:>5}  {x['body']}")
    print("\nportal_files")
    for x in con.execute("""SELECT kind, COUNT(*) n,
             COUNT(*) FILTER (WHERE body_text IS NOT NULL) got,
             COALESCE(ROUND(AVG(chars))::int, 0) avg_chars
         FROM portal_files GROUP BY kind ORDER BY n DESC"""):
        print(f"    {x['kind']:<14}{x['got']:>5}/{x['n']:<6} fetched, "
              f"avg {x['avg_chars']:,} chars")
    # How much of it lines up with meetings we actually have recordings for.
    r = con.execute("""
        SELECT COUNT(DISTINCT pe.id) matched FROM portal_events pe
        JOIN videos v ON v.upload_date = to_char(pe.event_date, 'YYYY-MM-DD')
    """).fetchone()
    print(f"\n{r['matched']:,} portal events share a date with a recording we hold")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--events", action="store_true", help="sync the event list")
    ap.add_argument("--text", action="store_true", help="fetch pending file text")
    ap.add_argument("--since", help="YYYY-MM-DD, for incremental event sync")
    ap.add_argument("--limit", type=int, default=0)
    ap.add_argument("--workers", type=int, default=WORKERS)
    args = ap.parse_args()

    con = db.connect(autocommit=False)
    if args.events:
        print("syncing events ...", flush=True)
        print("  %d events, %d files" % sync_events(con, args.since))
    if args.text:
        print("fetching file text ...", flush=True)
        sync_text(con, args.limit, args.workers)
    report(con)
    return 0


if __name__ == "__main__":
    sys.exit(main())
