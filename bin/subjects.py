#!/usr/bin/env python3
"""Derive what the county keeps coming back to, and the words it uses for it.

WHY THIS EXISTS. `web/archive.py` held eighteen subjects as a literal, each
with a hand-written regex over agenda titles and a hand-written tsquery over
the transcript. Both halves were guesses, and the guess was measurably bad:
`affordable housing|workforce housing` matched 23 published items, while the
SHIP program - the State Housing Initiatives Partnership, which is how Florida
funds affordable housing - is 66 more that it caught none of, and Community
Development Block Grants are 129 more that it caught one of. The row said 23
where the subject is 304, and the shape a reader saw was the pattern's rather
than the county's.

WHAT REPLACES IT, AND WHAT DELIBERATELY DOES NOT. A model is good at knowing
that SHIP means housing in Florida and that a human writing regexes will not
think of it. A model is not good at 21,274 separate judgements nobody will
ever read. So it is used for VOCABULARY and never for classification:

    propose   a stratified sample of real titles -> the subjects themselves
    terms     each subject -> the phrases a Florida county actually uses
    ground    every candidate phrase -> its count in this corpus, and a real
              sample title, BEFORE anybody decides whether to keep it
    review    a person keeps or drops each phrase, on the count and the sample

Matching then stays lexical, deterministic and in SQL. That is not
conservatism: counting published titles by phrase is an exact operation over
the county's own words, which is what keeps this surface on the record side of
UI_REQUIREMENTS section 2. A per-item model label or a cosine threshold would
make every number in that section an inference and oblige it to be drawn as
one (R2.1, R2.3).

WHY GROUNDING IS THE LOAD-BEARING STEP. Proposed phrases are wrong in ways that
are invisible in the phrase and obvious in the count. `SHIP` as a substring
matches 942 titles, nearly all of them containing "township"; at a word
boundary it matches 25. Nobody could have told those apart by reading the
pattern, and nobody would have found it buried in 21,274 individual labels.

    bin/subjects.py --propose        sample titles, ask for subjects
    bin/subjects.py --terms          ask for phrases, ground every one
    bin/subjects.py --triage         keep what grounds cleanly, queue the rest
    bin/subjects.py --review         the queue, with counts and samples
    bin/subjects.py --keep SLUG…     accept a subject, or a term by id
    bin/subjects.py --drop SLUG…     reject one
    bin/subjects.py --status         what state the derivation is in
    bin/subjects.py --recall         sample UNMATCHED items and ask what we
                                     are missing - the audit worth paying for
"""
import argparse
import json
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "bin"))

import db                                                      # noqa: E402


def _llm():
    """The chat client, imported only when a pass actually needs it.

    `web/archive.py` imports this module for `patterns()` alone - it is the
    one place a phrase becomes SQL, and a second copy over there would drift
    from this one, which is the failure the whole table exists to end. It has
    no business loading the model client to do that, and the reader API should
    not fail to start because an inference key is absent.
    """
    import ask
    return ask


PROPOSER = "subjects-v1"

# How many titles the model reads before proposing. Stratified by year so a
# subject that only existed in 2016 can still be proposed, and deduplicated on
# a normalised prefix because the county files the same boilerplate thousands
# of times and a raw sample is mostly "An Ordinance By The Board Of County
# Commissioners Of Pasco County, Florida, Amending...".
SAMPLE_PER_YEAR = 140
BATCH = 120

PROPOSE_SYS = """\
You are reading agenda item titles from a Florida county government (Pasco
County) covering 2015-2026. Propose the RECURRING SUBJECTS this county's
business is about.

A subject is something a resident would recognise as a topic that comes back
year after year: a road project, a funding programme, a category of land-use
decision, a facility, a policy fight. It is not a procedural form ("resolution",
"consent agenda", "budget amendment") and it is not one project that appeared
once.

Return JSON: {"subjects": [{"slug": "...", "label": "...", "q": "...",
"blurb": "..."}]}

  slug   short, lowercase, hyphenated, stable
  label  how a reader would name it, in SENTENCE case - capitalise only the
         first word and any proper noun. "Impact fees", not "Impact Fees".
  q      TWO OR THREE WORDS to search the archive for, never a question and
         never a sentence. "impact fees", not "What impact fees does the
         county charge?"
  blurb  one sentence on what it covers and why it recurs

Rules:
- Only propose what you actually see recurring in these titles.
- Prefer the specific over the generic: "Ridge Road extension" over
  "transportation", "impact fees" over "fees".
- Do not propose a subject that is really a department or a meeting type.
- 8 to 15 subjects per batch. Fewer good ones beats more.
"""

MERGE_SYS = """\
You are consolidating subject lists proposed from separate samples of one
county's agenda titles. Merge duplicates and near-duplicates into a single
list.

Return JSON: {"subjects": [{"slug": "...", "label": "...", "q": "...",
"blurb": "..."}]}

Rules:
- `label` is SENTENCE case and `q` is two or three words, never a question.
- Merge subjects that a resident would consider the same thing. Keep the
  clearest label and slug.
- Keep subjects that are genuinely distinct even when related: a specific road
  project is not the same subject as road funding generally.
- Drop anything procedural, anything that is really a department, and anything
  that appeared in only one batch AND reads like a one-off.
- Order by how much county business you expect each to be, most first.
- 18 to 28 subjects.
"""

TERMS_SYS = """\
You know how Florida county governments word their agenda items and how people
talk in a county commission meeting.

For the given subject, list the PHRASES that would appear in the text when this
subject is being handled - in a published agenda item title, or spoken aloud in
the meeting. Include the formal programme names, the acronyms, the statutory
names, and the ordinary spoken wording.

Return JSON: {"terms": [{"phrase": "...", "negative": false}]}

  phrase    the literal words, lowercase. Multi-word phrases are matched as a
            phrase; single words are matched as whole words.
  negative  true if the phrase means an item is NOT about this subject, and is
            needed to keep a broader phrase from over-matching.

Rules:
- 8 to 20 phrases. Include acronyms AND what they stand for.
- Be specific enough that the phrase is unlikely to appear in an item about
  something else. Prefer "housing authority" over "housing".
- Add negative phrases where an obvious ambiguity exists. Example: for licence
  plate cameras, "specialty plate" is negative, because a county also grants
  money for specialty licence plates.
- Do not include the county's name, "Pasco", or generic government words.
"""


# ------------------------------------------------------------ house style
#
# Enforced here rather than only asked for. The first run returned every label
# in Title Case and every `q` as a full question - "What impact fees does the
# county charge on new development?" - which would have shipped as the row's
# heading and as its /search link. The prompt now says both plainly AND these
# repair what comes back, because a prompt is a request and a surface needs a
# guarantee.

# PHRASES, not words. A word list gets this wrong in both directions and did:
# "Development" is a proper noun in "Community Development District" and a
# common one in "Economic development incentives", and no per-word rule can
# tell those apart. So everything after the first word is lowered, and then
# the names below are restored wherever they appear.
PROPER = ("Pasco", "Suncoast", "Moffitt", "Ridge Road", "Orange Belt Trail",
          "Connected City", "Penny for Pasco", "State Road", "Sheriff",
          "Community Development District")
ACRONYM = re.compile(r"^[A-Z0-9]{2,}$")
QUESTION = re.compile(r"^(what|how|who|where|when|why|which|can|does|do|is|are)\b",
                      re.I)
STOP = {"the", "a", "an", "and", "or", "of", "for", "in", "on", "to", "county",
        "pasco", "county's", "its", "what", "how", "does", "do", "is", "are",
        "can", "who", "where", "when", "why", "which"}


def sentence_case(label):
    """Title Case -> sentence case, restoring proper names and acronyms."""
    words = label.split()
    out = [words[0]] + [
        w if ACRONYM.match(w.strip("(),:.")) else
        (w[0].lower() + w[1:] if w[:1].isupper() else w)
        for w in words[1:]]
    s = " ".join(out)
    for name in PROPER:
        s = re.sub(r"(?<!^)\b" + re.escape(name) + r"\b", name, s,
                   flags=re.IGNORECASE)
    return s[0].upper() + s[1:] if s else s


def short_query(q, label):
    """Two or three searchable words, never a question.

    Falls back to the label's own content words, which is where a usable query
    was going to come from anyway: the row's title IS the subject, and the
    link under it should search for that rather than for a sentence the search
    page will tear into fragments.
    """
    q = (q or "").strip()
    if q and not QUESTION.match(q) and len(q.split()) <= 4 and "?" not in q:
        return q
    # Apostrophes stay inside the word: splitting on \W turns "Sheriff's" into
    # "Sheriff s", and "s" is not a search term.
    words = [w for w in re.split(r"[^\w']+", label)
             if w and w.lower() not in STOP]
    return " ".join(words[:3]) or label


# --------------------------------------------------------------- matching
#
# ONE list drives both lanes. The old literal carried a regex for the record
# and a separate tsquery for the room, which could - and did - disagree about
# what a subject was without anything saying so.

def rx(phrase):
    r"""A phrase as a Postgres regex, at word boundaries.

    `\m` and `\M` are what stop `SHIP` matching `township`, which is not a
    hypothetical: without them it matched 942 titles instead of 25. Internal
    whitespace is loosened because ASR and the county's own typing disagree
    about how many spaces are in "State  Housing Initiatives".
    """
    return r"\m" + r"\s+".join(re.escape(w) for w in phrase.split()) + r"\M"


def tsq(phrase):
    """The same phrase as a tsquery, adjacent words as a phrase search."""
    words = [w for w in re.split(r"\W+", phrase) if w]
    return " <-> ".join(words) if words else ""


def patterns(con, slug=None):
    """The kept vocabulary, as one record regex and one room tsquery each.

    Returns {slug: {"record": rx, "room": tsq, "label":…, "q":…}} for every
    subject that is kept and has at least one kept positive term. A subject
    with only negative terms would match everything and is skipped rather than
    shipped.
    """
    rows = con.execute("""
        SELECT s.slug, s.label, s.q, s.sort, t.phrase, t.negative
          FROM subject s JOIN subject_term t ON t.slug = s.slug
         WHERE s.status = 'kept' AND t.status = 'kept'
           AND (%s::text IS NULL OR s.slug = %s)
         ORDER BY s.sort NULLS LAST, s.slug, t.phrase""", (slug, slug))
    out = {}
    for r in rows:
        d = out.setdefault(r["slug"], {"label": r["label"], "q": r["q"],
                                       "sort": r["sort"], "pos": [], "neg": []})
        d["neg" if r["negative"] else "pos"].append(r["phrase"])
    for slug, d in list(out.items()):
        if not d["pos"]:
            del out[slug]
            continue
        d["record"] = "|".join(rx(p) for p in d["pos"])
        d["record_not"] = "|".join(rx(p) for p in d["neg"]) or None
        d["room"] = " | ".join(f"({tsq(p)})" for p in d["pos"] if tsq(p))
        d["room_not"] = " | ".join(f"({tsq(p)})" for p in d["neg"] if tsq(p)) or None
    return out


# --------------------------------------------------------------- proposing

def _sample(con):
    """Titles to propose from: stratified by year, deduplicated on a prefix.

    The county files the same boilerplate thousands of times, so a uniform
    sample is mostly one sentence repeated. Cutting on the first 60 characters
    of a normalised title is enough to spread the sample over what the items
    are actually about.
    """
    rows = con.execute("""
        WITH t AS (
            SELECT ai.title, left(m.date, 4) AS year,
                   left(regexp_replace(lower(ai.title), '[^a-z0-9]+', ' ', 'g'), 60) AS head
              FROM agenda_items ai JOIN meetings m ON m.id = ai.meeting_id
             WHERE ai.source = 'agenda' AND length(ai.title) > 25
        ), d AS (
            SELECT DISTINCT ON (head) title, year FROM t ORDER BY head, title
        ), r AS (
            SELECT title, year, row_number() OVER (PARTITION BY year ORDER BY md5(title)) AS n
              FROM d
        )
        SELECT title, year FROM r WHERE n <= %s ORDER BY year, n""",
        (SAMPLE_PER_YEAR,))
    return [dict(r) for r in rows]


def propose(con):
    titles = _sample(con)
    print(f"  sampled {len(titles):,} distinct titles across "
          f"{len({t['year'] for t in titles})} years")
    batches = [titles[i:i + BATCH] for i in range(0, len(titles), BATCH)]
    seen = []
    for k, b in enumerate(batches, 1):
        body = "\n".join(f"- [{t['year']}] {t['title'][:220]}" for t in b)
        raw = _llm().chat([{"role": "system", "content": PROPOSE_SYS},
                        {"role": "user", "content": body}],
                       as_json=True, temperature=0.3)
        got = (json.loads(raw) or {}).get("subjects") or []
        seen.extend(got)
        print(f"    batch {k}/{len(batches)}: {len(got)} proposed "
              f"({len(seen)} so far)")

    # One consolidation pass. Without it the same subject arrives under five
    # slugs, because each batch names it from what that batch happened to see.
    listed = "\n".join(
        f"- {s.get('slug')}: {s.get('label')} — {s.get('blurb', '')}" for s in seen)
    raw = _llm().chat([{"role": "system", "content": MERGE_SYS},
                    {"role": "user", "content": listed}],
                   as_json=True, temperature=0.2)
    final = (json.loads(raw) or {}).get("subjects") or []
    print(f"  consolidated {len(seen)} proposals into {len(final)} subjects")

    cur = con.cursor()
    for i, s in enumerate(final):
        if not s.get("slug") or not s.get("label"):
            continue
        s["label"] = sentence_case(s["label"])
        s["q"] = short_query(s.get("q"), s["label"])
        cur.execute("""
            INSERT INTO subject (slug, label, q, blurb, proposer, sort, status)
            VALUES (%s, %s, %s, %s, %s, %s, 'proposed')
            ON CONFLICT (slug) DO UPDATE
               SET label = EXCLUDED.label, q = EXCLUDED.q,
                   blurb = EXCLUDED.blurb, sort = EXCLUDED.sort
             WHERE subject.status = 'proposed'""",
            (s["slug"], s["label"], s.get("q") or s["label"],
             s.get("blurb"), PROPOSER, i))
    con.commit()
    print(f"  wrote {len(final)} subjects as 'proposed'")


# ---------------------------------------------------------------- grounding

def ground(con, phrase):
    """What this phrase actually finds here. Never guessed, always measured."""
    pat = rx(phrase)
    n_items = con.execute(
        "SELECT COUNT(*) FROM agenda_items WHERE source='agenda' AND title ~* %s",
        (pat,)).fetchone()[0]
    sample = con.execute(
        "SELECT title FROM agenda_items WHERE source='agenda' AND title ~* %s "
        "ORDER BY length(title) LIMIT 1", (pat,)).fetchone()
    q = tsq(phrase)
    n_utt = con.execute(
        "SELECT COUNT(*) FROM utterances WHERE tsv @@ to_tsquery('english', %s)",
        (q,)).fetchone()[0] if q else 0
    return n_items, n_utt, (sample[0][:160] if sample else None)


def terms(con, only=None):
    subs = [dict(r) for r in con.execute("""
        SELECT slug, label, blurb FROM subject
         WHERE status IN ('proposed', 'kept')
           AND (%s::text IS NULL OR slug = %s)
         ORDER BY sort NULLS LAST, slug""", (only, only))]
    if not subs:
        sys.exit("  no subjects to ask about. Run --propose first.")
    cur = con.cursor()
    for s in subs:
        raw = _llm().chat(
            [{"role": "system", "content": TERMS_SYS},
             {"role": "user", "content":
              f"Subject: {s['label']}\nWhat it covers: {s.get('blurb') or ''}"}],
            as_json=True, temperature=0.2)
        got = (json.loads(raw) or {}).get("terms") or []
        kept = 0
        for t in got:
            p = (t.get("phrase") or "").strip().lower()
            if not p or len(p) < 3:
                continue
            n_i, n_u, sample = ground(con, p)
            cur.execute("""
                INSERT INTO subject_term
                       (slug, phrase, negative, n_items, n_utterances, sample)
                VALUES (%s, %s, %s, %s, %s, %s)
                ON CONFLICT (slug, phrase) DO UPDATE
                   SET n_items = EXCLUDED.n_items,
                       n_utterances = EXCLUDED.n_utterances,
                       sample = EXCLUDED.sample
                 WHERE subject_term.status = 'proposed'""",
                (s["slug"], p, bool(t.get("negative")), n_i, n_u, sample))
            kept += 1
        con.commit()
        print(f"    {s['slug']:<28} {kept:>3} phrases grounded")


# ----------------------------------------------------------------- curating

def review(con):
    """The queue, with the two things a decision is actually made on."""
    subs = [dict(r) for r in con.execute("""
        SELECT s.slug, s.label, s.status, s.blurb,
               COUNT(*) FILTER (WHERE t.status = 'kept')     AS kept,
               COUNT(*) FILTER (WHERE t.status = 'proposed') AS todo
          FROM subject s LEFT JOIN subject_term t ON t.slug = s.slug
         GROUP BY s.slug, s.label, s.status, s.blurb, s.sort
         ORDER BY s.sort NULLS LAST, s.slug""")]
    for s in subs:
        print(f"\n  [{s['status']:<8}] {s['slug']}  —  {s['label']}")
        if s["blurb"]:
            print(f"      {s['blurb'][:110]}")
        for t in con.execute("""
            SELECT id, phrase, negative, status, n_items, n_utterances, sample
              FROM subject_term WHERE slug = %s
             ORDER BY negative, n_items DESC NULLS LAST""", (s["slug"],)):
            mark = "NOT " if t["negative"] else ""
            flag = {"kept": " ", "proposed": "?", "dropped": "x"}[t["status"]]
            print(f"      {flag} #{t['id']:<5} {mark}{t['phrase']:<38} "
                  f"{(t['n_items'] or 0):>6} items {(t['n_utterances'] or 0):>7} said")
            if t["sample"] and t["status"] == "proposed":
                print(f"              e.g. {t['sample'][:96]}")


def triage(con):
    """Keep the phrases that ground cleanly; leave the rest for a person.

    The design says a person decides every phrase, and 27 subjects at ~16
    phrases each is 430 decisions, most of which are not decisions: a phrase
    that names between one item and a twentieth of the archive, with a sample
    title that contains it, is doing exactly what it claimed. Reviewing those
    one by one is how a review queue stops being read.

    So this keeps that majority and leaves exactly the two classes the audit
    invariants name - a phrase that finds NOTHING, and a phrase broad enough
    to name most of the archive - sitting at 'proposed' for `--review`. They
    are the two that were ever going to be wrong, and they are few enough to
    read.

    Nothing is auto-DROPPED. A dead phrase might be a real programme this
    county spells differently, and deleting it silently would lose the one
    signal that says so.
    """
    total = con.execute(
        "SELECT COUNT(*) FROM agenda_items WHERE source = 'agenda'").fetchone()[0]
    cap = max(50, total // 20)
    cur = con.cursor()
    cur.execute("""
        UPDATE subject_term SET status = 'kept'
         WHERE status = 'proposed'
           AND (coalesce(n_items, 0) > 0 OR coalesce(n_utterances, 0) > 0)
           AND (negative OR coalesce(n_items, 0) <= %s)""", (cap,))
    kept = cur.rowcount
    cur.execute("UPDATE subject SET status = 'kept' WHERE status = 'proposed'")
    subs = cur.rowcount
    con.commit()
    left = con.execute(
        "SELECT COUNT(*) FROM subject_term WHERE status = 'proposed'").fetchone()[0]
    print(f"  kept {kept} phrases across {subs} subjects")
    print(f"  {left} left for a person: nothing found, or broader than "
          f"{cap:,} items")
    print("  bin/subjects.py --review   to read them")


def setstatus(con, targets, status):
    cur, n = con.cursor(), 0
    for t in targets:
        if t.isdigit():
            cur.execute("UPDATE subject_term SET status = %s WHERE id = %s",
                        (status, int(t)))
        else:
            cur.execute("UPDATE subject SET status = %s WHERE slug = %s", (status, t))
            # Keeping a subject with nothing kept under it ships an empty row,
            # so accepting a subject accepts the phrases that survived
            # grounding with it. Dropping one drops its vocabulary too.
            cur.execute("UPDATE subject_term SET status = %s "
                        "WHERE slug = %s AND status = 'proposed'", (status, t))
        n += cur.rowcount
    con.commit()
    print(f"  {status}: {n} row(s)")


def status(con):
    for r in con.execute("""
        SELECT s.status, COUNT(DISTINCT s.slug) AS subjects,
               COUNT(*) FILTER (WHERE t.status = 'kept') AS terms
          FROM subject s LEFT JOIN subject_term t ON t.slug = s.slug
         GROUP BY s.status ORDER BY 1"""):
        print(f"  {r['status']:<10} {r['subjects']:>3} subjects, "
              f"{r['terms']:>4} kept terms")
    live = patterns(con)
    print(f"  {len(live)} subject(s) would be served to the front page")
    if live:
        n = con.execute("""
            SELECT COUNT(*) FROM agenda_items ai
             WHERE ai.source = 'agenda' AND (%s)""" % " OR ".join(
                ["ai.title ~* %s"] * len(live)),
            [d["record"] for d in live.values()]).fetchone()[0]
        tot = con.execute(
            "SELECT COUNT(*) FROM agenda_items WHERE source='agenda'").fetchone()[0]
        print(f"  they name {n:,} of {tot:,} published items ({n / tot:.0%})")


# ------------------------------------------------------------------- recall

RECALL_SYS = """\
Each line is an agenda item title from a Florida county that matched NONE of
the subjects listed. For each, say whether it actually belongs to one of them.

Return JSON: {"missed": [{"n": 3, "slug": "...", "phrase": "..."}]}
  n       the line number
  slug    the subject it belongs to
  phrase  the words IN THAT TITLE that should have matched, copied exactly

Only report a title that clearly belongs to one of the listed subjects. Most
will belong to none - that is the expected answer and reporting it is wrong.
"""


def recall(con, n=200):
    """What the vocabulary is missing, sampled and asked about.

    This is the right place for model spend. Labelling all 21,274 titles
    produces 21,274 judgements nobody reviews; sampling the UNMATCHED ones
    produces a recall estimate and a list of phrases to add, both of which a
    person can check in a minute.
    """
    live = patterns(con)
    if not live:
        sys.exit("  nothing kept yet - run --propose, --terms and --keep first.")
    where = " AND ".join(["ai.title !~* %s"] * len(live))
    rows = [dict(r) for r in con.execute(f"""
        SELECT ai.title FROM agenda_items ai
         WHERE ai.source = 'agenda' AND length(ai.title) > 25 AND {where}
         ORDER BY md5(ai.title) LIMIT %s""",
        [d["record"] for d in live.values()] + [n])]
    listed = "\n".join(f"{i}. {r['title'][:200]}" for i, r in enumerate(rows, 1))
    known = "\n".join(f"- {s}: {d['label']}" for s, d in live.items())
    raw = _llm().chat([{"role": "system", "content": RECALL_SYS},
                    {"role": "user", "content": f"Subjects:\n{known}\n\nTitles:\n{listed}"}],
                   as_json=True, temperature=0.1)
    missed = (json.loads(raw) or {}).get("missed") or []
    print(f"  sampled {len(rows)} unmatched titles, {len(missed)} judged to belong "
          f"to a subject ({len(missed) / max(len(rows), 1):.0%} miss rate)")

    # The loop closes here. A recall estimate that only prints is a number; the
    # phrases it found are the fix, and they arrive already copied out of a
    # real title, so they ground by construction. They enter as 'proposed'
    # like anything else - the model saying a phrase belongs is not the same
    # as it having been checked, and `--review` is still where that happens.
    cur, added = con.cursor(), 0
    for m in missed:
        i, slug = m.get("n"), m.get("slug")
        phrase = (m.get("phrase") or "").strip().lower()
        if slug not in live or len(phrase) < 3:
            continue
        # Verify the model actually copied it out of the title it cited, rather
        # than paraphrasing. A phrase that is not in the line it came from is
        # the same defect `redaction.span_is_plausible` exists to catch.
        if not (isinstance(i, int) and 0 < i <= len(rows)
                and phrase in rows[i - 1]["title"].lower()):
            continue
        n_i, n_u, sample = ground(con, phrase)
        cur.execute("""
            INSERT INTO subject_term
                   (slug, phrase, negative, n_items, n_utterances, sample)
            VALUES (%s, %s, false, %s, %s, %s)
            ON CONFLICT (slug, phrase) DO NOTHING""",
            (slug, phrase, n_i, n_u, sample))
        added += cur.rowcount
    con.commit()
    print(f"  {added} new phrase(s) written as 'proposed' and grounded\n")
    for m in missed[:40]:
        i = m.get("n")
        t = rows[i - 1]["title"][:90] if isinstance(i, int) and 0 < i <= len(rows) else "?"
        print(f"    {str(m.get('slug'))[:24]:<24} {str(m.get('phrase'))[:34]:<34} {t}")


def main():
    ap = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--propose", action="store_true")
    g.add_argument("--terms", nargs="?", const=True, metavar="SLUG")
    g.add_argument("--review", action="store_true")
    g.add_argument("--triage", action="store_true")
    g.add_argument("--keep", nargs="+", metavar="SLUG|ID")
    g.add_argument("--drop", nargs="+", metavar="SLUG|ID")
    g.add_argument("--status", action="store_true")
    g.add_argument("--recall", nargs="?", const=200, type=int, metavar="N")
    a = ap.parse_args()
    con = db.connect()

    if a.propose:
        return propose(con)
    if a.terms:
        return terms(con, None if a.terms is True else a.terms)
    if a.review:
        return review(con)
    if a.triage:
        return triage(con)
    if a.keep:
        return setstatus(con, a.keep, "kept")
    if a.drop:
        return setstatus(con, a.drop, "dropped")
    if a.recall is not None:
        return recall(con, a.recall)
    return status(con)


if __name__ == "__main__":
    sys.exit(main())
