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
the design notes A per-item model label or a cosine threshold would
make every number in that section an inference and oblige it to be drawn as
one.

WHY GROUNDING IS THE LOAD-BEARING STEP. Proposed phrases are wrong in ways that
are invisible in the phrase and obvious in the count. `SHIP` as a substring
matches 942 titles, nearly all of them containing "township"; at a word
boundary it matches 25. Nobody could have told those apart by reading the
pattern, and nobody would have found it buried in 21,274 individual labels.

    bin/subjects.py --propose        sample titles, ask for subjects
    bin/subjects.py --terms          ask for phrases, ground every one
    bin/subjects.py --split          narrow a subject too broad to answer anything
    bin/subjects.py --theme [N]      group the top level under N themes
    bin/subjects.py --triage         keep what grounds cleanly, queue the rest
    bin/subjects.py --rollup         rebuild what the front page reads
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
    """The chat client, imported only when a pass actually needs it."""
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


THEME_SYS = """\
You are grouping the recurring subjects of a Florida county's business into a
small number of TOP-LEVEL THEMES, so a reader meets eight rows instead of
twenty-seven.

Return JSON: {"themes": [{"slug": "...", "label": "...", "q": "...",
"blurb": "...", "members": ["subject-slug", ...]}]}

Rules:
- EXACTLY the number of themes asked for, no more.
- EVERY subject given to you must appear in exactly one theme's `members`.
  A subject left out disappears from the page.
- Use only the slugs given. Do not invent, rename or split them.
- A theme is how a resident would divide up what a county does - land use,
  roads, water, public safety, money, and so on. Not a department chart.
- `label` sentence case, `q` two or three words, `blurb` one sentence.
"""

THEME_COUNT = 8


def theme(con, n=THEME_COUNT):
    """Group the top-level subjects under a handful of themes.

    A THEME HAS NO VOCABULARY OF ITS OWN, and that is the point rather than a
    shortcut. A subject is a thing the county words - it has phrases, they are
    grounded, a person kept them. A theme is not: nobody files an item about
    "public safety". So its pattern is the union of what it contains, which
    means its count needs no curation, cannot disagree with its children, and
    contains them by construction instead of by a constraint somebody has to
    remember to apply.
    """
    subs = [dict(r) for r in con.execute("""
        SELECT s.slug, s.label, s.blurb, COALESCE(SUM(y.items), 0) AS items
          FROM subject s LEFT JOIN subject_year y ON y.slug = s.slug
         WHERE s.status = 'kept' AND s.parent IS NULL
         GROUP BY s.slug, s.label, s.blurb ORDER BY items DESC""")]
    if not subs:
        sys.exit("  no top-level subjects to group.")
    listed = "\n".join(
        f"- {s['slug']}: {s['label']} ({s['items']:,} items) — {(s['blurb'] or '')[:110]}"
        for s in subs)
    raw = _llm().chat(
        [{"role": "system", "content": THEME_SYS},
         {"role": "user", "content": f"Group these {len(subs)} subjects into "
                                     f"{n} themes.\n\n{listed}"}],
        as_json=True, temperature=0.2)
    themes = (json.loads(raw) or {}).get("themes") or []
    known = {s["slug"] for s in subs}
    placed, cur = set(), con.cursor()
    for i, t in enumerate(themes):
        members = [m for m in (t.get("members") or []) if m in known]
        if not t.get("slug") or not members:
            continue
        lab = sentence_case(t["label"])
        cur.execute("""
            INSERT INTO subject (slug, label, q, blurb, proposer, sort, status)
            VALUES (%s, %s, %s, %s, %s, %s, 'kept')
            ON CONFLICT (slug) DO UPDATE
               SET label = EXCLUDED.label, q = EXCLUDED.q,
                   blurb = EXCLUDED.blurb, sort = EXCLUDED.sort""",
            (t["slug"], lab, short_query(t.get("q"), lab), t.get("blurb"),
             PROPOSER, i))
        for m in members:
            cur.execute("UPDATE subject SET parent = %s WHERE slug = %s",
                        (t["slug"], m))
            placed.add(m)
        print(f"  {lab}: {len(members)} subjects")
    # A subject the model forgot would vanish from the page, which is the one
    # outcome worse than a clumsy grouping. Left at the top level, visibly.
    missed = known - placed
    if missed:
        print(f"  {len(missed)} subject(s) left ungrouped and still top-level: "
              + ", ".join(sorted(missed)))
    con.commit()
    rollup(con)


def patterns(con, slug=None):
    """The kept vocabulary as SQL, for every subject in the tree."""
    rows = con.execute("""
        SELECT s.slug, s.label, s.q, s.sort, s.parent, t.phrase, t.negative
          FROM subject s LEFT JOIN subject_term t
            ON t.slug = s.slug AND t.status = 'kept'
         WHERE s.status = 'kept'
         ORDER BY s.sort NULLS LAST, s.slug, t.phrase""")
    out = {}
    for r in rows:
        d = out.setdefault(r["slug"], {"label": r["label"], "q": r["q"],
                                       "sort": r["sort"], "parent": r["parent"],
                                       "pos": [], "neg": []})
        if r["phrase"]:
            d["neg" if r["negative"] else "pos"].append(r["phrase"])

    kids = {}
    for s, d in out.items():
        if d["parent"] in out:
            kids.setdefault(d["parent"], []).append(s)

    def own(s):
        d = out[s]
        return "|".join(rx(p) for p in d["pos"]) if d["pos"] else None

    def effective(s, seen=()):
        """This subject's pattern, or the union of everything under it.

        `seen` guards a cycle. The schema cannot express one today, but a
        parent edited by hand could, and the failure would be a hung front
        page rather than a wrong number.
        """
        if s in seen:
            return None
        parts = [p for p in [own(s)]
                 + [effective(k, seen + (s,)) for k in kids.get(s, [])] if p]
        return "|".join(parts) if parts else None

    def eff_room(s, seen=()):
        if s in seen:
            return None
        mine = (" | ".join(f"({tsq(p)})" for p in out[s]["pos"] if tsq(p))
                or None)
        parts = [p for p in [mine]
                 + [eff_room(k, seen + (s,)) for k in kids.get(s, [])] if p]
        return " | ".join(parts) if parts else None

    for s, d in list(out.items()):
        d["record"] = effective(s)
        d["room"] = eff_room(s)
        # A branch with no phrases anywhere beneath it is a row of nothing.
        if not d["record"]:
            del out[s]
            continue
        # Exclusions stay the subject's OWN. Inheriting a child's negative up
        # would let one sub-subject's disambiguation quietly shrink its
        # siblings.
        d["record_not"] = "|".join(rx(p) for p in d["neg"]) or None
        d["room_not"] = " | ".join(f"({tsq(p)})" for p in d["neg"] if tsq(p)) or None
        # What a child must ALSO match, which is its parent's own phrases and
        # not the parent's union - the union already contains the child, so
        # constraining by it would be a no-op. A theme has no own phrases, so
        # a subject under one is unconstrained, which is correct: the theme is
        # defined AS its members.
        up = d["parent"] if d["parent"] in out else None
        d["parent"] = up
        d["record_in"] = own(up) if up else None
        d["room_in"] = ((" | ".join(f"({tsq(p)})" for p in out[up]["pos"] if tsq(p))
                         or None) if up else None)
    if slug:
        return {k: v for k, v in out.items() if k == slug}
    return out


SPLIT_SYS = """\
These are agenda item titles that ALL matched one broad subject in a Florida
county's record. The subject is too broad to be useful: it puts things a
resident cares about differently into one row.

Narrow it into SUB-SUBJECTS - the distinct kinds of business inside it.

Return JSON: {"subjects": [{"slug": "...", "label": "...", "q": "...",
"blurb": "..."}]}, using the same rules as before: sentence-case label, two or
three words for q, one sentence of blurb.

Rules:
- TWO to FOUR sub-subjects. Each must be a thing a resident would recognise
  as different from the others, not a filing distinction.
- Together they should cover most of what you see. A residual tail is fine and
  does not need a sub-subject of its own.
- Return an EMPTY list if this subject does not genuinely decompose - if the
  titles are all the same kind of business and only the parties differ. That
  is a real and useful answer; do not invent a split to satisfy the request.
"""

SPLIT_MIN = 1000


# ----------------------------------------------------------------- narrowing

def split(con, slug=None):
    """Narrow a subject that has grown too broad to answer anything.

    The model reads titles THIS SUBJECT ACTUALLY MATCHED rather than the
    archive at large, so the sub-subjects it proposes are a decomposition of
    what is there and not a guess at what might be."""
    live = patterns(con)
    targets = []
    for s, d in live.items():
        if slug and s != slug:
            continue
        n = con.execute("SELECT COUNT(*) FROM agenda_items "
                        "WHERE source='agenda' AND title ~* %s",
                        (d["record"],)).fetchone()[0]
        if slug or n >= SPLIT_MIN:
            targets.append((s, d, n))
    if not targets:
        sys.exit(f"  nothing at or above {SPLIT_MIN:,} items to narrow.")

    cur = con.cursor()
    for s, d, n in targets:
        rows = [r[0] for r in con.execute("""
            SELECT title FROM agenda_items
             WHERE source='agenda' AND title ~* %s
             ORDER BY md5(title) LIMIT 220""", (d["record"],))]
        listed = "\n".join(f"- {t[:200]}" for t in rows)
        raw = _llm().chat(
            [{"role": "system", "content": SPLIT_SYS},
             {"role": "user", "content": f"Subject: {d['label']} ({n:,} items)\n\n{listed}"}],
            as_json=True, temperature=0.3)
        kids = (json.loads(raw) or {}).get("subjects") or []
        if not kids:
            print(f"  {d['label']}: does not decompose — left whole")
            continue
        for i, k in enumerate(kids):
            if not k.get("slug") or not k.get("label"):
                continue
            lab = sentence_case(k["label"])
            cur.execute("""
                INSERT INTO subject (slug, label, q, blurb, proposer, sort,
                                     parent, status)
                VALUES (%s, %s, %s, %s, %s, %s, %s, 'proposed')
                ON CONFLICT (slug) DO UPDATE
                   SET parent = EXCLUDED.parent, label = EXCLUDED.label
                 WHERE subject.status = 'proposed'""",
                (k["slug"], lab, short_query(k.get("q"), lab), k.get("blurb"),
                 PROPOSER, i, s))
        con.commit()
        print(f"  {d['label']}: {len(kids)} sub-subjects — "
              + ", ".join(sentence_case(k['label']) for k in kids if k.get('label')))
    print("\n  bin/subjects.py --terms  to give each one a vocabulary")


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
    # Only what has NOT been curated yet, unless a slug is named. Asking again
    # for a subject whose vocabulary a person already kept spends a call to
    # produce phrases the ON CONFLICT clause then declines to write.
    subs = [dict(r) for r in con.execute("""
        SELECT slug, label, blurb FROM subject
         WHERE (%s::text IS NULL AND status = 'proposed'
                OR slug = %s)
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
    rollup(con)
    print(f"  {left} left for a person: nothing found, or broader than "
          f"{cap:,} items")
    print("  bin/subjects.py --review   to read them")


def rollup(con):
    """Recompute `subject_year`, which is what the front page actually reads."""
    sys.path.insert(0, os.path.join(ROOT, "web"))
    import archive
    # The one definition of "the minutes named a nay vote", borrowed rather
    # than restated: two copies of that regex is two answers to the same
    # question.
    NAY_SQL = archive.NAY_SQL

    live = patterns(con)
    if not live:
        print("  nothing kept - subject_year left as it is")
        return

    # MEMBERSHIP FIRST, COUNTS SECOND, and that ordering is the whole fix.
    #
    # So the regexes run ONCE EACH, only for the subjects that actually have
    # phrases, into a set of (subject, item). Containment becomes a set
    # intersection and a theme becomes a union - both of which are what those
    # words meant all along, and both of which Postgres does on an indexed
    # integer column instead of by re-reading titles.
    leaves = {s: d for s, d in live.items() if d["pos"]}
    cur = con.cursor()
    cur.execute("DROP TABLE IF EXISTS pg_temp.m_item")
    cur.execute("CREATE TEMP TABLE m_item (slug text, item_id integer)")
    cur.execute("DROP TABLE IF EXISTS pg_temp.m_utt")
    cur.execute("CREATE TEMP TABLE m_utt (slug text, video_id text, idx integer)")
    # ITS OWN PHRASES, not `record`. `patterns()` hands back the EFFECTIVE
    # pattern - own phrases unioned with everything beneath - because that is
    # what a row displays. Seeding membership with it makes a parent's set
    # already contain its children's, so the containment step below intersects
    # a child with a set it is inside by construction and does nothing.
    # Measured when it happened: "Ordinances and boundaries" went from 21
    # items to 1,219, which is its parent's whole subtree wearing a child's
    # name. Own here, union afterwards, in that order.
    for s, d in leaves.items():
        own_rx = "|".join(rx(p) for p in d["pos"])
        own_ts = " | ".join(f"({tsq(p)})" for p in d["pos"] if tsq(p))
        cur.execute("""
            INSERT INTO pg_temp.m_item (slug, item_id)
            SELECT %s, ai.id FROM agenda_items ai
             WHERE ai.source = 'agenda' AND ai.title ~* %s
               AND (%s::text IS NULL OR ai.title !~* %s)""",
            (s, own_rx, d["record_not"], d["record_not"]))
        if own_ts:
            cur.execute("""
                INSERT INTO pg_temp.m_utt (slug, video_id, idx)
                SELECT %s, u.video_id, u.idx FROM utterances u
                 WHERE u.tsv @@ to_tsquery('english', %s)
                   AND (%s::text IS NULL
                        OR NOT (u.tsv @@ to_tsquery('english', %s)))""",
                (s, own_ts, d["room_not"], d["room_not"]))
    cur.execute("CREATE INDEX ON pg_temp.m_item (slug)")
    cur.execute("CREATE INDEX ON pg_temp.m_utt (slug)")

    # A child is counted INSIDE its parent, so anything it matched that its
    # parent did not is not part of the subject it claims to narrow.
    for s, d in leaves.items():
        up = d["parent"]
        if not up or up not in leaves:
            continue
        cur.execute("""DELETE FROM pg_temp.m_item c
                        WHERE c.slug = %s AND NOT EXISTS (
                              SELECT 1 FROM pg_temp.m_item p
                               WHERE p.slug = %s AND p.item_id = c.item_id)""",
                    (s, up))
        cur.execute("""DELETE FROM pg_temp.m_utt c
                        WHERE c.slug = %s AND NOT EXISTS (
                              SELECT 1 FROM pg_temp.m_utt p
                               WHERE p.slug = %s AND p.video_id = c.video_id
                                 AND p.idx = c.idx)""",
                    (s, up))

    # Themes: DISTINCT across the subtree, never a sum. Two members can name
    # the same item and adding them would count it twice.
    kids = {}
    for s, d in live.items():
        if d["parent"]:
            kids.setdefault(d["parent"], []).append(s)

    # Now roll the unions UP, deepest first, so a theme gathers subjects that
    # have already gathered their own sub-subjects. Every subject with
    # children takes this, not only the ones with no phrases: a subject that
    # has both - "community development district oversight" has five phrases
    # AND four sub-subjects - displays its own work plus theirs.
    def depth(s):
        n, up = 0, live[s]["parent"]
        while up:
            n, up = n + 1, live[up]["parent"]
        return n

    for s in sorted(live, key=depth, reverse=True):
        under = [k for k in kids.get(s, []) if k in live]
        if not under:
            continue
        cur.execute("""INSERT INTO pg_temp.m_item (slug, item_id)
                       SELECT DISTINCT %s, m.item_id FROM pg_temp.m_item m
                        WHERE m.slug = ANY(%s)
                          AND NOT EXISTS (SELECT 1 FROM pg_temp.m_item x
                                           WHERE x.slug = %s
                                             AND x.item_id = m.item_id)""",
                    (s, under, s))
        cur.execute("""INSERT INTO pg_temp.m_utt (slug, video_id, idx)
                       SELECT DISTINCT %s, m.video_id, m.idx FROM pg_temp.m_utt m
                        WHERE m.slug = ANY(%s)
                          AND NOT EXISTS (SELECT 1 FROM pg_temp.m_utt x
                                           WHERE x.slug = %s
                                             AND x.video_id = m.video_id
                                             AND x.idx = m.idx)""",
                    (s, under, s))

    cur.execute("DELETE FROM subject_year")
    cur.execute("""
        INSERT INTO subject_year (slug, year, items, meetings, decided,
                                  continued, refused, divided, first, last)
        SELECT m.slug, left(mt.date, 4),
               COUNT(*), COUNT(DISTINCT mt.id),
               COUNT(*) FILTER (WHERE ai.outcome IS NOT NULL),
               COUNT(*) FILTER (WHERE ai.outcome = 'continued'),
               COUNT(*) FILTER (WHERE ai.outcome IN ('denied','no_action')),
               COUNT(*) FILTER (WHERE ai.outcome_text ~* %s),
               MIN(mt.date), MAX(mt.date)
          FROM pg_temp.m_item m
          JOIN agenda_items ai ON ai.id = m.item_id
          JOIN meetings mt ON mt.id = ai.meeting_id
         WHERE mt.date <= to_char(now(), 'YYYY-MM-DD')
         GROUP BY 1, 2""", (NAY_SQL,))
    # The room lane merges in, and creates the row where a subject was spoken
    # about in a year the record never named it.
    cur.execute("""
        INSERT INTO subject_year (slug, year, lines, heard, first, last)
        SELECT m.slug, left(mt.date, 4), COUNT(*), COUNT(DISTINCT mt.id),
               MIN(mt.date), MAX(mt.date)
          FROM pg_temp.m_utt m
          JOIN videos v ON v.id = m.video_id
          JOIN meetings mt ON mt.id = v.meeting_id
         GROUP BY 1, 2
        ON CONFLICT (slug, year) DO UPDATE
           SET lines = EXCLUDED.lines, heard = EXCLUDED.heard,
               first = LEAST(subject_year.first, EXCLUDED.first),
               last  = GREATEST(subject_year.last, EXCLUDED.last)""")
    con.commit()
    n = con.execute("SELECT COUNT(*) FROM subject_year").fetchone()[0]
    subs = con.execute("SELECT COUNT(DISTINCT slug) FROM subject_year").fetchone()[0]
    print(f"  rebuilt subject_year: {n} rows across {subs} subjects")


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
    rollup(con)


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
    g.add_argument("--rollup", action="store_true")
    g.add_argument("--split", nargs="?", const=True, metavar="SLUG")
    g.add_argument("--theme", nargs="?", const=8, type=int, metavar="N")
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
    if a.rollup:
        return rollup(con)
    if a.split:
        return split(con, None if a.split is True else a.split)
    if a.theme:
        return theme(con, a.theme)
    if a.keep:
        return setstatus(con, a.keep, "kept")
    if a.drop:
        return setstatus(con, a.drop, "dropped")
    if a.recall is not None:
        return recall(con, a.recall)
    return status(con)


if __name__ == "__main__":
    sys.exit(main())
