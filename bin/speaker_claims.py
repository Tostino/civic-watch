#!/usr/bin/env python3
"""Resolve a speaker's name once, from evidence rather than from a verdict.

SPEAKER_PLAN.md is the argument; this is the shadow build of it. Nothing here
is read by any page. `--compare` is the whole point: it puts the new
resolution beside the old one and says what changed, so the change can be
judged before it is switched on.

    bin/speaker_claims.py --backfill   existing tables -> claims
    bin/speaker_claims.py --extract    transcripts -> self / read_aloud claims
    bin/speaker_claims.py --resolve    claims -> speaker_resolved
    bin/speaker_claims.py --compare    speaker_resolved vs utterance_speaker
    bin/speaker_claims.py --all        all four, in order

Run it against a sandbox first (`bin/sandbox.py --build`), which is what the
maintainer asked for and what makes this non-destructive: it only ever writes
the four new tables, but a full extract over 299k utterances is not something
to try out on production for the first time.

WHY THE PRECEDENCE IS A TABLE. `speaker_method` holds the ranking, so the one
judgement nobody could settle from the transcript - whether an isolated
self-ID should outrank a voiceprint - is a single UPDATE rather than a code
change:

    UPDATE speaker_method SET rank = 9 WHERE method = 'self_weak';   -- strict

It ships lenient (rank 3, beside `self`) because 8% of self-IDs are isolated
and demoting them throws away names that are probably right. The maintainer's
verdict on the one case put to them was "need to listen to tell", so this is
the reversible default rather than a measured one.
"""
import argparse
import collections
import re
import sys

import db

# "My name is X" and the Florida podium convention, which SELF_ID never
# matched: a name, an address, and "I have been sworn". 581 utterances use the
# second form, which is how a voice saying "Shelley Johnson, 6400 Madison
# Street, and I have been sworn" is currently named "What".
# The trigger is case-insensitive, the NAME is not - and that distinction has
# to be scoped, not global. With re.I on the whole pattern `[A-Z]` matches
# lowercase too, so "my name is Dina Fox and I live at" captured "Dina Fox
# and". Every name in the first run came out with a trailing conjunction.
SAYS_NAME = re.compile(r"(?i:\bmy name is)\s+([A-Z][a-z]+(?:\s+[A-Z][a-z']+){0,2})")
# Anchored at the START of what the person says, because that is where the
# convention puts it, and required to be followed by a comma and an address.
# Unanchored it matched street names out of the address itself - "Margaret St"
# from "1234 Margaret Street" - and invented a speaker for every one.
PODIUM = re.compile(
    r"^(?:good (?:morning|afternoon|evening),?\s+)?"
    r"([A-Z][a-z]+\s+[A-Z][a-z']+),\s+"
    r"(?:\d{2,6}\s|\[address removed\])")
SWORN = re.compile(r"\b(?:i have been sworn|been duly sworn|i was sworn)\b", re.I)

# Somebody reading another person's words. The claim then names the AUTHOR and
# covers only the read span; the reader keeps every claim either side of it.
# Without this a staffer reading "My name is Corey Ward and I live at..." is
# confidently named Corey Ward, above the voiceprint.
READING = re.compile(
    r"\b(?:email|e-mail|letter|correspondence|comment card)\b[^.]{0,40}"
    r"\b(?:is )?from\b|\bread (?:it |this )?into the record\b"
    r"|\bi am writing\b|\bwrites\b", re.I)

# How many of the 13 utterances around a self-ID share its diarization label.
# The risk a self-ID carries is never the NAME - nobody misstates their own -
# it is that the utterance landed on the wrong voice. Measured over the
# corpus: 83% sit in a coherent run, 8% are isolated.
NEAR = 6
COHERENT = 4


# A voice's utterances are INTERLEAVED with everyone else's, so MIN(idx) to
# MAX(idx) for one local_label is not "that voice's run" - it is the whole
# meeting, swallowing every other speaker in between. Measured the hard way:
# that span model made 89% of utterances contested and turned Commissioner
# Yeager into James Navarro. A claim covers one CONTIGUOUS run, and a voice
# that speaks four times in a meeting makes four claims.
RUNS = """
    WITH marked AS (
        SELECT video_id, local_label, idx,
               idx - ROW_NUMBER() OVER (PARTITION BY video_id, local_label
                                        ORDER BY idx) AS island
          FROM utterances
         WHERE local_label IS NOT NULL)
    SELECT video_id, local_label, MIN(idx) AS lo, MAX(idx) AS hi
      FROM marked GROUP BY video_id, local_label, island"""


def runs_by_voice(con):
    """(video_id, local_label) -> [(lo, hi), ...], every contiguous run."""
    out = collections.defaultdict(list)
    for r in con.execute(RUNS):
        out[(r["video_id"], r["local_label"])].append((r["lo"], r["hi"]))
    return out


def runs_by_cluster(con):
    out = collections.defaultdict(list)
    for r in con.execute("""
            WITH marked AS (
                SELECT video_id, cluster, idx,
                       idx - ROW_NUMBER() OVER (PARTITION BY video_id, cluster
                                                ORDER BY idx) AS island
                  FROM utterances WHERE cluster IS NOT NULL)
            SELECT video_id, cluster, MIN(idx) AS lo, MAX(idx) AS hi
              FROM marked GROUP BY video_id, cluster, island"""):
        out[(r["video_id"], r["cluster"])].append((r["lo"], r["hi"]))
    return out


def _norm(s):
    return " ".join((s or "").split()).strip().lower()


def claim(cur, video_id, lo, hi, name, method, quote=None, corroborated=False):
    cur.execute("""INSERT INTO speaker_claim
                     (video_id, start_idx, end_idx, name_text, method, quote,
                      corroborated)
                   VALUES (%s, %s, %s, %s, %s, %s, %s)""",
                (video_id, lo, hi, name, method, quote, corroborated))


# ------------------------------------------------------------------ backfill
def backfill(con):
    """Everything the archive already decided, restated as evidence.

    The point is not to change any of these - they resolve exactly as they do
    today - but to get them into one shape so the extractor's claims can be
    negotiated against them rather than overwriting them.
    """
    cur = con.cursor()
    cur.execute("DELETE FROM speaker_claim WHERE method IN "
                "('override','label','voice','cluster','llm','chair')")

    n = collections.Counter()
    # A human, about a span. The only producer that is already append-only.
    for r in con.execute("""SELECT video_id, start_idx, end_idx, name
                              FROM speaker_override WHERE status='applied'"""):
        claim(cur, r["video_id"], r["start_idx"], r["end_idx"], r["name"],
              "override")
        n["override"] += 1

    # A human, about a whole voice. The span is that voice's run in that
    # meeting, which is what `speaker_label` means and has never been able to
    # say.
    voice_runs, cluster_runs = runs_by_voice(con), runs_by_cluster(con)
    for r in con.execute("SELECT video_id, local_label, name FROM speaker_label"):
        for lo, hi in voice_runs.get((r["video_id"], r["local_label"]), []):
            claim(cur, r["video_id"], lo, hi, r["name"], "label")
            n["label"] += 1

    # The pipeline, about this voice in this meeting. `source` says which of
    # three methods produced it for two of them and NULL for the largest
    # bucket, so NULL becomes `voice` - the honest floor. The extractor puts
    # better-evidenced `self` claims on top rather than rewriting these.
    for r in con.execute("""SELECT video_id, local_label, name, source
                              FROM speaker_identity WHERE name IS NOT NULL"""):
        m = {"llm": "llm", "chair": "chair"}.get(r["source"], "voice")
        for lo, hi in voice_runs.get((r["video_id"], r["local_label"]), []):
            claim(cur, r["video_id"], lo, hi, r["name"], m)
            n[m] += 1

    # The archive-wide cluster majority, which is the weakest thing here and
    # the largest by utterance count.
    for r in con.execute("SELECT video_id, cluster, name FROM voice_name"):
        for lo, hi in cluster_runs.get((r["video_id"], r["cluster"]), []):
            claim(cur, r["video_id"], lo, hi, r["name"], "cluster")
            n["cluster"] += 1

    con.commit()
    return n


# ------------------------------------------------------------------- extract
def extract(con):
    """What the transcript says outright, which nothing has been reading.

    Two forms of self-introduction, a guard for people reading somebody else's
    letter, and a corroboration flag. This is where the new resolution differs
    from the old one - a backfill alone would resolve identically and prove
    nothing.
    """
    cur = con.cursor()
    cur.execute("DELETE FROM speaker_claim WHERE method IN "
                "('self','self_weak','read_aloud')")

    n = collections.Counter()
    voice_runs = runs_by_voice(con)
    rows = con.execute("""SELECT video_id, idx, local_label, text
                            FROM utterances
                           WHERE text ILIKE '%%my name is%%'
                              OR text ~* '\\yi have been sworn|been duly sworn\\y'
                           ORDER BY video_id, idx""").fetchall()

    # Names heard anywhere in a meeting, for the corroboration flag. One pass
    # per video rather than a query per claim.
    heard = collections.defaultdict(list)
    for r in con.execute("SELECT video_id, lower(text) t FROM utterances"):
        heard[r["video_id"]].append(r["t"])

    for r in rows:
        text = " ".join(r["text"].split())
        m = SAYS_NAME.search(text)
        if not m and SWORN.search(text):
            m = PODIUM.search(text)
        if not m:
            continue
        name = m.group(1).strip()
        if len(name.split()) > 3 or len(name) < 4:
            continue

        quote = text[max(0, m.start() - 30):m.end() + 40]

        # Reading somebody else's words: the name belongs to the author and
        # the claim covers only this utterance, so the reader keeps her own
        # name either side of it.
        if READING.search(text):
            claim(cur, r["video_id"], r["idx"], r["idx"], name, "read_aloud",
                  quote)
            n["read_aloud"] += 1
            continue

        # Is this utterance attributable? Not "is the name right" - it is
        # whether the utterance landed on the right voice.
        near = con.execute("""SELECT count(*) c FROM utterances
                               WHERE video_id=%s AND local_label=%s
                                 AND idx BETWEEN %s AND %s""",
                           (r["video_id"], r["local_label"],
                            r["idx"] - NEAR, r["idx"] + NEAR)).fetchone()["c"]
        method = "self" if near >= COHERENT else "self_weak"

        surname = name.split()[-1].lower()
        corrob = sum(1 for t in heard[r["video_id"]] if surname in t) > 1

        # The span is the contiguous run CONTAINING this line - the trip to
        # the podium happening now - and not every line this voice speaks in
        # the meeting.
        span = next((s for s in voice_runs.get((r["video_id"], r["local_label"]), [])
                     if s[0] <= r["idx"] <= s[1]), (r["idx"], r["idx"]))
        claim(cur, r["video_id"], span[0], span[1], name, method, quote, corrob)
        n[method] += 1
        n["corroborated"] += bool(corrob)

    con.commit()
    return n


# ------------------------------------------------------------------- resolve
# rank, then corroboration promoting the two unquoted methods, then span
# specificity (narrower is more specific), then recency. Written once, here.
RESOLVE = """
INSERT INTO speaker_resolved (video_id, idx, name_text, method, contested)
SELECT u.video_id, u.idx, w.name_text, w.method, w.contested
  FROM utterances u
  JOIN LATERAL (
      SELECT c.name_text, c.method,
             -- two unvetoed methods asserting different names for one span:
             -- a fact the pipeline already computes and prints away
             (COUNT(DISTINCT lower(c2.name_text)) > 1) AS contested
        FROM speaker_claim c
        JOIN speaker_method m ON m.method = c.method
        LEFT JOIN speaker_claim c2
               ON c2.video_id = c.video_id
              AND u.idx BETWEEN c2.start_idx AND c2.end_idx
              AND c2.name_text IS NOT NULL
       WHERE c.video_id = u.video_id
         AND u.idx BETWEEN c.start_idx AND c.end_idx
         AND c.name_text IS NOT NULL
         AND name_supported(c.video_id, c.name_text)
       GROUP BY c.id, c.name_text, c.method, m.rank, c.corroborated,
                c.start_idx, c.end_idx
       ORDER BY m.rank - CASE WHEN c.corroborated AND m.rank >= 7
                              THEN 1 ELSE 0 END,
                c.end_idx - c.start_idx,
                c.id DESC
       LIMIT 1) w ON TRUE"""


def resolve(con):
    cur = con.cursor()
    cur.execute("DELETE FROM speaker_resolved")
    cur.execute(RESOLVE)
    con.commit()
    return con.execute("SELECT count(*) n, count(*) FILTER (WHERE contested) c "
                       "FROM speaker_resolved").fetchone()


# ------------------------------------------------------------------- compare
def compare(con):
    """The gate. What changed, bucketed, and nothing asserted."""
    print(f"\n{'=' * 70}\nnew resolution against the live one\n{'=' * 70}")
    t = con.execute("""
        SELECT count(*) FILTER (WHERE us.name IS NOT NULL) AS old_named,
               count(*) FILTER (WHERE sr.name_text IS NOT NULL) AS new_named,
               count(*) FILTER (WHERE us.name IS NOT NULL
                                  AND sr.name_text IS NOT NULL
                                  AND lower(us.name) = lower(sr.name_text)) AS same,
               count(*) FILTER (WHERE us.name IS NULL
                                  AND sr.name_text IS NOT NULL) AS gained,
               count(*) FILTER (WHERE us.name IS NOT NULL
                                  AND sr.name_text IS NULL) AS lost,
               count(*) FILTER (WHERE us.name IS NOT NULL
                                  AND sr.name_text IS NOT NULL
                                  AND lower(us.name) <> lower(sr.name_text)) AS changed
          FROM utterance_speaker us
          LEFT JOIN speaker_resolved sr
                 ON sr.video_id = us.video_id AND sr.idx = us.idx""").fetchone()
    for k in ("old_named", "new_named", "same", "gained", "lost", "changed"):
        print(f"  {k:<12} {t[k]:>8,}")

    print("\n  what the changes are, by (old basis -> new method):")
    for r in con.execute("""
            SELECT us.basis AS old, sr.method AS new, count(*) n
              FROM utterance_speaker us
              JOIN speaker_resolved sr
                ON sr.video_id = us.video_id AND sr.idx = us.idx
             WHERE us.name IS NOT NULL AND sr.name_text IS NOT NULL
               AND lower(us.name) <> lower(sr.name_text)
             GROUP BY 1, 2 ORDER BY n DESC LIMIT 12"""):
        print(f"    {str(r['old']):<10} -> {str(r['new']):<12} {r['n']:>7,}")

    print("\n  names GAINED where the archive had none, by method:")
    for r in con.execute("""
            SELECT sr.method, count(*) n
              FROM utterance_speaker us
              JOIN speaker_resolved sr
                ON sr.video_id = us.video_id AND sr.idx = us.idx
             WHERE us.name IS NULL AND sr.name_text IS NOT NULL
             GROUP BY 1 ORDER BY n DESC"""):
        print(f"    {str(r['method']):<12} {r['n']:>7,}")

    print("\n  a sample of the changes, to read by hand:")
    for r in con.execute("""
            SELECT us.video_id, us.idx, us.name AS old, us.basis,
                   sr.name_text AS new, sr.method
              FROM utterance_speaker us
              JOIN speaker_resolved sr
                ON sr.video_id = us.video_id AND sr.idx = us.idx
             WHERE us.name IS NOT NULL AND sr.name_text IS NOT NULL
               AND lower(us.name) <> lower(sr.name_text)
             ORDER BY random() LIMIT 8"""):
        print(f"    {r['video_id']} {r['idx']:>6}  "
              f"{r['old'][:20]:<20} ({r['basis']:<7}) -> "
              f"{r['new'][:20]:<20} ({r['method']})")


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    for f in ("backfill", "extract", "resolve", "compare", "all"):
        ap.add_argument(f"--{f}", action="store_true")
    args = ap.parse_args()
    con = db.connect()

    if args.backfill or args.all:
        print("backfill:", dict(backfill(con)))
    if args.extract or args.all:
        print("extract: ", dict(extract(con)))
    if args.resolve or args.all:
        r = resolve(con)
        print(f"resolve:  {r['n']:,} utterances, {r['c']:,} contested")
    if args.compare or args.all:
        compare(con)
    return 0


if __name__ == "__main__":
    sys.exit(main())
