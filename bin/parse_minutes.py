"""Parse published minutes into an outcome for every agenda item."""
import argparse
import re
import sys

import db

ITEM = re.compile(r"^([A-Z]{1,3})\s?-?\s?(\d{1,3})\s+\S")

# A line that really BEGINS an item, as opposed to one that merely starts with
# something shaped like a code. The difference is the next word:
HEADING = re.compile(r"^[A-Z]{1,3}\s?-?\s?\d{1,3}\s+(?![a-z])")
FIELD = re.compile(r"^(File\s*Number|Me\s?m\w*|Comm\.?\s*Dist\.?|Recommendation"
                   r"|Fiscal Impact|Contact)\b", re.I)
VERB = re.compile(r"^(Approved|Adopted|Continued|Denied|Withdrawn|Tabled|Received"
                  r"|No action|Deferred|Postponed|Failed|Pulled|Heard)\b", re.I)
NOISE = re.compile(r"^(BCC|PC)?\s*(Agenda|Minutes)?\s*Page \d+( of \d+)?\s*$", re.I)
CODE = re.compile(r"\b([A-Z]{1,3})\s?-?\s?(\d{1,3})\b")

# A page marker without the word "Page", which is how it extracts in the
# 2015-2017 minutes. NOISE demands "Page" and misses these, and a stray "15 of
# 17" in the middle of an outcome is what a swallowed line looks like.
PAGE = re.compile(r"^\d{1,3}\s+of\s+\d{1,3}$")

# These minutes carry the video offset after the sentence, which is furniture
# and defeated the "has this sentence finished" test in parse(). The sentence
# stayed open and swallowed the next eight lines, so `cur` never advanced and
# every outcome after it was filed under the wrong item: 86 stored outcomes
# contained a later item's heading.
TAIL = re.compile(r"\s*\(\d{1,2}:\d{2}(?::\d{2})?\)\s*$")

# Motions that are NOT an outcome for the item.
SUBSIDIARY = re.compile(
    # "filed" is the minutes' own typo for "file", and it is still the same
    # motion: "Approved to receive and filed documents submitted by
    # Commissioner Ron Oakley."
    r"\breceive\s+and\s+filed?\b(?=.*\b(?:submitted|presented)\s+by\b"
    r"|.*\bfrom\s+(?:Mr|Ms|Mrs|Dr|Pastor)\b)"
    r"|\b(?:receive\s+and\s+file|accept)\s+(?:the\s+)?ex\s?parte'?\s+forms?\b"
    r"|\bhear\s+(?:the\s+|a\s+|an\s+|\w+\s+)*(?:emergenc|walk[\s-]?on|add[\s-]?on)"
    r"|\bhear\s+the\s+item\s+as\s+a\s+regular\s+agenda\s+item"
    r"|\bto\s+reconsider\s+the\s+item\b"
    r"|\bto\s+adjourn\b",
    re.I)

# The same invariant, in Postgres ARE, for bin/audit.py to count in bulk. It
# lives HERE rather than in audit.py so the two definitions sit in one file and
# a change to either is visibly a change to both - `minutes.no_subsidiary_
# outcome_text` and `choose()` must agree, or the audit will bless exactly what
# the parser broke. `tests/` would be better; a shared constant is what this
# project has, and `bin/audit.py --only minutes` is how you check they agree.
SUBSIDIARY_SQL = (
    r"(receive\s+and\s+filed?.*((submitted|presented)\s+by|from\s+(mr|ms|mrs|dr|pastor)))"
    r"|((receive\s+and\s+filed?|accept)\s+(the\s+)?ex\s?parte'?\s+forms?)"
    r"|(hear\s+(the\s+|a\s+|an\s+|\w+\s+)*(emergenc|walk[\s-]?on|add[\s-]?on))"
    r"|(hear\s+the\s+item\s+as\s+a\s+regular\s+agenda\s+item)"
    r"|(to\s+reconsider\s+the\s+item)"
    r"|(to\s+adjourn)")


def load_agenda(con, meeting_id):
    """The meeting's published items, addressed by ID. (items, by_code)

    Read into a dict keyed on code, with no ORDER BY, whichever row the query
    happened to yield last decided the whole meeting's map. Measured in the
    sandbox with NOTHING changed but `enable_indexscan`: 4 of 688 items stored
    a different outcome_text, and meeting 220's PC5 stored one under one plan
    and nothing at all under the other.

    ORDER BY here makes even the last-resort tie-break deterministic; the
    resolution below does not depend on it.
    """
    items = [{"id": r[0], "code": r[1], "section": r[2], "seq": r[3],
              "title": r[4]}
             for r in con.execute(
                 "SELECT id, code, section, seq, title FROM agenda_items "
                 "WHERE meeting_id=%s AND code IS NOT NULL ORDER BY seq, id",
                 (meeting_id,))]
    by_code = {}
    for it in items:
        by_code.setdefault(it["code"], []).append(it)
    return items, by_code


def words(s):
    return re.findall(r"[a-z0-9]+", (s or "").lower())


def title_agreement(heading, title):
    """How many leading words of an item's title the minutes restate.

        P1     A RESOLUTION OF PASCO COUNTY, FLORIDA, ESTABLISHING AN
        P1     AN ORDINANCE OF THE BOARD OF COUNTY COMMISSIONERS OF"""
    a, b = words(heading), words(title)
    n = 0
    while n < len(a) and n < len(b) and a[n] == b[n]:
        n += 1
    return n


def pick(by_code, code, section=None, heading=None):
    """Which agenda ROW a minutes reference to `code` means. (item, ambiguous)

    A no-op wherever the key was already unique, which is 23,043 of the 23,123
    coded items - the whole point is to leave those alone and decide the 80
    rows, in 39 pairs, where `code` addresses more than one thing. Replayed
    over every minutes document in the archive, 22,055 of 22,087 items resolve
    to exactly what they did before and 32 change."""
    cand = by_code.get(code) or []
    if len(cand) <= 1:
        return (cand[0] if cand else None), False

    if heading:
        scored = sorted(((title_agreement(heading, c["title"]), c) for c in cand),
                        key=lambda t: (-t[0], t[1]["seq"], t[1]["id"]))
        # Two words of agreement, and strictly more than the runner-up. A
        # single shared word is "A" or "ZONING" and decides nothing.
        if scored[0][0] >= 2 and scored[0][0] > scored[1][0]:
            return scored[0][1], False
    if section:
        same = [c for c in cand if (c["section"] or "").lower() == section.lower()]
        if len(same) == 1:
            return same[0], False
        cand = same or cand
    return cand[0], True


def choose(sentences, code=None, by_code=None):
    """Which of an item's recorded motions is its outcome?

    Returns None when EVERY motion is subsidiary. The minutes then record no
    outcome for this item, and saying so is the honest answer - it is
    already the designed state for the 24% of items the minutes do not dispose
    of in writing. Keeping the subsidiary motion instead would not be
    preserving information; it would be asserting something false, which is the
    whole defect."""
    def about_another_item(s):
        """"Approved to accept the withdrawal of N91." under item RS4."""
        if not code or not by_code:
            return False
        named = {c for c in codes_in(s) if c in by_code}
        return bool(named) and code not in named

    candidates = [s for s in sentences if not SUBSIDIARY.search(s)]
    if not candidates:
        return None
    # A motion about another item is worse than one merely out of order, but
    # if that is all there is, keep it rather than losing the item entirely.
    candidates = [s for s in candidates if not about_another_item(s)] or candidates
    refusals = [s for s in candidates if classify(s) in ("denied", "withdrawn")]
    return (refusals or candidates)[-1]

# Which section a bulk sentence is disposing of.
BULK_SECTION = [
    (re.compile(r"Public Hearing Consent", re.I), "public hearings"),
    (re.compile(r"Rezoning Consent", re.I), "public hearings"),
    (re.compile(r"\bConsent Agenda\b", re.I), "consent"),
]


def classify(text):
    """Outcome sentence -> classified outcome. Order matters; see the module
    docstring."""
    t = " ".join((text or "").split()).lower()
    if not t:
        return None
    if "withdraw" in t:
        return "withdrawn"
    if "continu" in t or "postpon" in t or "defer" in t:
        return "continued"
    if "denied" in t or "denial" in t:
        return "denied"
    # "failed" only means denied when it is the MOTION that failed. Matched as
    # a bare substring it also catches "the exhibits that failed to upload in
    # CivicClerk" and files an approval as a denial.
    if re.search(r"\b(motion|it|which)\s+failed\b|\bfailed\s+(to pass|for lack|by)", t):
        return "denied"
    if t.startswith("tabled"):
        return "tabled"
    if t.startswith("no action"):
        return "no_action"
    if "adopt" in t:
        return "adopted"
    if t.startswith("approved"):
        return "approved"
    # Receiving a report, hearing a presentation and accepting a proclamation
    # are real board actions, but they are NOT approvals - the board has not
    # endorsed anything. Recording them as "approved" puts a decision in the
    # record that never happened.
    if t.startswith(("received", "heard", "presented")):
        return "received"
    return "other"


def codes_in(text):
    out = []
    for m in CODE.finditer(text or ""):
        out.append(f"{m.group(1)}{int(m.group(2))}")
    return out


def parse(text):
    """(occurrences, bulk) - the outcome sentences per ITEM OCCURRENCE."""
    lines = [re.sub(r"\s{2,}", " ", l).strip() for l in (text or "").splitlines()]
    lines = [l for l in lines if l and not NOISE.match(l) and not PAGE.match(l)]

    occurrences, bulk = [], []
    cur, after_fields, buf, target = None, False, None, None

    def flush():
        nonlocal buf, target
        if buf and target is not None:
            target["sentences"].append(" ".join(buf).strip())
        elif buf:
            bulk.append(" ".join(buf).strip())
        buf, target = None, None

    for ln in lines:
        # An OPEN outcome sentence swallows everything until it ends, because the
        # exception list wraps and its second line matches ITEM. Break the
        # sentence there and the list loses every code after the line break, so
        # items the minutes say were WITHDRAWN are recorded as approved.
        #
        # Two guards, both earned. The video offset is stripped before asking
        # whether the sentence ended, and a line that begins the NEXT item is
        # never eaten however unfinished this one looks: being wrong about where
        # an item ends silently re-parents everything after it.
        if buf is not None:
            done = TAIL.sub("", " ".join(buf).rstrip()).endswith(".")
            if (not done and len(buf) < 8
                    and not FIELD.match(ln) and not HEADING.match(ln)):
                buf.append(ln)
                continue
            flush()

        if ITEM.match(ln):
            m = ITEM.match(ln)
            cur = {"code": f"{m.group(1)}{int(m.group(2))}",
                   "heading": ln[m.end(2):].strip(),
                   "sentences": []}
            occurrences.append(cur)
            after_fields = False
            continue
        if FIELD.match(ln):
            after_fields = True
            continue
        # The capital is load-bearing. An outcome always begins a sentence
        # in these documents, so a lowercase match is a WRAPPED LINE - "pulled
        # for discussion. Agenda Items C12, C13, and C34 were withdrawn.",
        # "withdrawn.", "denied PDD's recommendation of denial ...". There are
        # 132 of those against 8,589 real ones, and treating a fragment as a
        # new outcome is how one becomes an item's recorded outcome.
        if VERB.match(ln) and ln[:1].isupper():
            buf = [ln]
            # A sentence naming a whole section belongs to the section, not to
            # whichever item happened to precede it in the document.
            is_bulk = any(rx.search(ln) for rx, _ in BULK_SECTION)
            target = None if is_bulk else (cur if after_fields else None)
            continue
    flush()
    # A heading with no motion under it disposes of nothing and would only make
    # the caller test for emptiness everywhere.
    return [o for o in occurrences if o["sentences"]], bulk


def resolve(occurrences, bulk, items, by_code):
    """Fill in the items the bulk sentences cover. Keyed on agenda_items.id."""
    out, ambiguous = {}, 0
    for occ in occurrences:
        d = choose(occ["sentences"], occ["code"], by_code)
        if d is None:
            continue          # every motion here was subsidiary; say nothing
        item, unsure = pick(by_code, occ["code"], heading=occ["heading"])
        if item is None:
            continue
        ambiguous += unsure
        out[item["id"]] = (d, classify(d), "item")
    for sentence in bulk:
        section = next((s for rx, s in BULK_SECTION if rx.search(sentence)), None)
        if not section:
            continue
        # A subsidiary motion that happens to name a section disposes of
        # nothing. "Approved to hear the emergency Addendum to the Consent
        # Agenda" is a decision to TAKE UP an addendum, and it was being spread
        # across all 45 consent items of one meeting as their outcome.
        if SUBSIDIARY.search(sentence):
            continue
        named = set(codes_in(sentence))
        inclusive = re.search(r"which included|included Agenda item", sentence, re.I)
        if inclusive:
            # "Approved the Public Hearing Consent Agenda which included A, B, C".
            # Restricted to codes that are really in that section, so a stray
            # match elsewhere in the sentence cannot adopt an unrelated item.
            for c in named:
                it, unsure = pick(by_code, c, section=section)
                if it and (it["section"] or "").lower() == section:
                    ambiguous += unsure
                    out.setdefault(it["id"],
                                   (sentence, classify(sentence), "bulk_included"))
            continue
        # Exception form. The lead clause disposes of everything in the section;
        # the tail says what happened INSTEAD to the few it names. Scanning the
        # whole string for "withdraw" marks all ~180 approved items as withdrawn.
        lead = re.split(r"\bwith the exception\b|\bexcept\b", sentence, 1,
                        flags=re.I)[0]
        bulk_outcome = classify(lead)
        tail = sentence[len(lead):]
        # Each "... CODES which were TREATMENT" group, in order.
        segs = re.split(r"\bwhich (?:were|was)\b", tail, flags=re.I)
        for i in range(len(segs) - 1):
            treatment = re.split(r"\band Agenda\b|\band noted\b", segs[i + 1],
                                 1, flags=re.I)[0]
            verdict = classify(treatment)
            for c in codes_in(segs[i]):
                it, unsure = pick(by_code, c, section=section)
                if it is None or it["id"] in out:
                    continue
                if "pull" in treatment.lower():
                    continue      # pulled for discussion; it gets its own line
                ambiguous += unsure
                out[it["id"]] = (sentence, verdict, "bulk_exception")
        # The lead clause disposes of the whole section. Walked over the ITEMS
        # rather than over a {code: section} map, so a code that names two rows
        # in two different sections puts this sentence on the one that is
        # actually in the section the sentence named - and only on that one.
        for it in items:
            if ((it["section"] or "").lower() != section
                    or it["code"] in named or it["id"] in out):
                continue
            out[it["id"]] = (lead.strip(), bulk_outcome, "bulk_consent")
    return out, ambiguous


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--limit", type=int, default=0)
    args = ap.parse_args()
    con = db.connect(autocommit=False)

    # Grouped by MEETING, not by file. A meeting-day can publish more than one
    # minutes document, and processing them as independent rows meant the last
    # one cleared what the first had written - so the result depended on the
    # order of a query nobody thought of as ordering anything.
    rows = con.execute("""
        SELECT pe.meeting_id, pf.body_text
        FROM portal_files pf JOIN portal_events pe ON pe.id = pf.event_id
        WHERE pf.kind='Minutes' AND pf.chars > 2000 AND pe.meeting_id IS NOT NULL
        ORDER BY pe.event_date DESC, pf.file_id""").fetchall()
    con.commit()
    docs = {}
    for r in rows:
        docs.setdefault(r["meeting_id"], []).append(r["body_text"])
    if args.limit:
        docs = dict(list(docs.items())[:int(args.limit)])

    tot_items = matched = 0
    by_src = {}
    n_subsidiary = n_multi = n_silent = n_ambiguous = 0
    for meeting_id, texts in docs.items():
        items, by_code = load_agenda(con, meeting_id)
        con.commit()
        if not items:
            continue

        resolved = {}
        for text in texts:
            occurrences, bulk = parse(text)
            for occ in occurrences:
                code, sentences = occ["code"], occ["sentences"]
                if code not in by_code:
                    continue
                n_subsidiary += sum(1 for s in sentences if SUBSIDIARY.search(s))
                kept = [s for s in sentences if not SUBSIDIARY.search(s)
                        and not ({x for x in codes_in(s) if x in by_code} - {code})]
                if len(kept) > 1:
                    n_multi += 1
                if choose(sentences, code, by_code) is None:
                    n_silent += 1
            got, unsure = resolve(occurrences, bulk, items, by_code)
            n_ambiguous += unsure
            for k, v in got.items():
                resolved.setdefault(k, v)

        tot_items += len(items)
        hits = list(resolved.items())
        matched += len(hits)
        for _, (_, _, src) in hits:
            by_src[src] = by_src.get(src, 0) + 1
        if not args.write:
            continue
        with con.cursor() as cur:
            # Clear, then write. These columns are DERIVED, so an UPDATE-only
            # write leaves whatever an older run decided about any item this run
            # no longer resolves - which is exactly how an outcome the parser
            # has since learned to reject survives the fix that rejected it.
            cur.execute(
                "UPDATE agenda_items SET outcome_text=NULL, outcome=NULL, "
                "outcome_source=NULL WHERE meeting_id=%s AND code IS NOT NULL",
                (meeting_id,))
            if hits:
                # BY ID. `WHERE meeting_id=%s AND code=%s` wrote every row
                # sharing the code, and 39 (meeting_id, code) pairs name more
                # than one row: 58 items carried an outcome parsed for a
                # different item, deterministically and wrongly.
                cur.executemany(
                    "UPDATE agenda_items SET outcome_text=%s, outcome=%s, "
                    "outcome_source=%s WHERE id=%s",
                    [(d[:400], o, src, item_id)
                     for item_id, (d, o, src) in hits])
        con.commit()

    print(f"{len(rows)} minutes documents · {tot_items:,} agenda items in those "
          f"meetings\n  {matched:,} given an outcome "
          f"({100*matched//max(tot_items,1)}%)")
    for k, v in sorted(by_src.items(), key=lambda kv: -kv[1]):
        print(f"    {k:<16}{v:>7,}")
    # Said out loud rather than left implied: these are the two places where an
    # item's outcome was a judgement rather than a reading.
    print(f"\n  {n_subsidiary:,} subsidiary motions skipped "
          f"(evidence accepted, or a motion to hear the item at all)")
    print(f"  {n_silent:,} items had NOTHING but subsidiary motions and are "
          f"left with no outcome")
    print(f"  {n_multi:,} items still carry more than one substantive motion; "
          f"the last is taken, unless one of them is a refusal")
    # The third place the parser judges rather than reads. A code that names
    # several agenda rows is normally settled by the title the minutes restate
    # or by the section a bulk sentence names; what is counted here is what
    # neither settled, and it goes to the lowest seq so the result is at least
    # the same on every run.
    print(f"  {n_ambiguous:,} references to a code that names more than one "
          f"item were decided by position, not by evidence")
    if not args.write:
        print("\n(dry run - pass --write to store)")
        return 0
    for x in con.execute("""SELECT outcome, COUNT(*) n FROM agenda_items
                            WHERE outcome IS NOT NULL GROUP BY outcome
                            ORDER BY n DESC"""):
        print(f"    {x['outcome']:<12}{x['n']:>7,}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
