"""Parse published minutes into a disposition for every agenda item.

The minutes restate each item exactly as the agenda did, then add one line
saying what the board actually did with it:

    C29 Agreement for Sale and Purchase of Property - Blueberry Hills ...
    File Number FAC26-0136
    Comm. Dist. 4
    Recommendation Approve
    Approved Staff's recommendation with Commissioner Oakley absent from the vote.

Most consent items never get a line of their own, because they are disposed of
in bulk:

    Approved the Consent Agenda with the exception of Agenda Items C29, C48,
    C50, and C69 which were pulled for discussion or revision and Agenda Items
    C27 and C72 which were withdrawn.

Resolving that sentence is the whole job. A regex over disposition lines finds
maybe a fifth of the items; the other four fifths are covered by exactly one
sentence per meeting that has to be read as "everything in this section EXCEPT
these, and here is what happened to those instead".

`outcome` is the classification, and it is not the leading verb. "Approved to
continue the item to the August 11 meeting" begins with "Approved" and is a
CONTINUANCE - reading the first word would file a delay as a decision, which
is the single most misleading thing this table could say.

An item may carry SEVERAL motions, and most of them are not its disposition:

    P83     Zoning Amendment (Regular) - Evans County Line 80 MPUD ...
            Recommendation    Approval with Conditions

    Approved to receive and file documents submitted by Mr. William Vermillion.
    Approved Staff's recommendation.

The first is evidentiary - the board accepting a member of the public's
exhibits into the record - and the second is the decision. Keeping the first
put "Approved" on 106 items, 88 of them public hearings, where what was
approved was somebody's paperwork. See `choose()`.
"""
import argparse
import re
import sys

import db

ITEM = re.compile(r"^([A-Z]{1,3})\s?-?\s?(\d{1,3})\s+\S")

# A line that really BEGINS an item, as opposed to one that merely starts with
# something shaped like a code. The difference is the next word:
#
#   C53 2017 Great American Cleanup ...   a heading
#   C69 which were pulled ...             the tail of a wrapped exception list
#   PC4 and PC5.                          likewise
#   I-75 and Wesley Chapel Boulevard      a road, in the middle of a title
#
# so a following LOWERCASE word means this is prose continuing, not a new item.
# Measured across every minutes document: 29,996 headings, 95 continuations,
# and the 95 are all exception lists or road names. Using ITEM here instead
# re-creates the exact bug the swallow rule exists to prevent - see parse().
HEADING = re.compile(r"^[A-Z]{1,3}\s?-?\s?\d{1,3}\s+(?![a-z])")
FIELD = re.compile(r"^(File\s*Number|Me\s?m\w*|Comm\.?\s*Dist\.?|Recommendation"
                   r"|Fiscal Impact|Contact)\b", re.I)
VERB = re.compile(r"^(Approved|Adopted|Continued|Denied|Withdrawn|Tabled|Received"
                  r"|No action|Deferred|Postponed|Failed|Pulled|Heard)\b", re.I)
NOISE = re.compile(r"^(BCC|PC)?\s*(Agenda|Minutes)?\s*Page \d+( of \d+)?\s*$", re.I)
CODE = re.compile(r"\b([A-Z]{1,3})\s?-?\s?(\d{1,3})\b")

# A page marker without the word "Page", which is how it extracts in the
# 2015-2017 minutes. NOISE demands "Page" and misses these, and a stray "15 of
# 17" in the middle of a disposition is what a swallowed line looks like.
PAGE = re.compile(r"^\d{1,3}\s+of\s+\d{1,3}$")

# These minutes carry the video offset after the sentence:
#
#     Continued to June 20, 2017 in New Port Richey.  (3:29:46)
#
# which is furniture, and it defeated the "has this sentence finished" test in
# parse(). The sentence then stayed open and swallowed the next eight lines -
# the following item's heading, its File Number, its Recommendation - so `cur`
# never advanced and every disposition after it was filed under the wrong item.
# Measured: 86 stored dispositions contained a later item's heading. Now 0.
TAIL = re.compile(r"\s*\(\d{1,2}:\d{2}(?::\d{2})?\)\s*$")

# Motions that are NOT a disposition of the item.
#
# Two kinds, both read from the minutes rather than imagined:
#
#   evidentiary  the board accepting a person's exhibits into the record -
#                "Approved to receive and file documents submitted by Mr.
#                Vermillion", "Approved to accept ex parte forms". 300-odd of
#                these. The person's name is the tell: a bare "receive and
#                file the report" IS a disposition, and Noted Items are
#                recommended "Receive and File" as their whole substance.
#   procedural   whether to take the item up at all - "Approved to hear the
#                emergency", "to hear the walk-on", "Approved to reconsider
#                the item". The board has decided to have the discussion, not
#                decided the matter.
#   the meeting  "Approved to adjourn the meeting." That is a motion about the
#                MEETING and it lands on whichever item happened to be last.
#                All 20 items carrying it are zoning amendments, ordinances and
#                special exceptions - not one of them is an adjournment item.
#                It only surfaced once `choose()` started taking the last
#                motion, which is what a fix exposing the next defect looks
#                like. ("recess" and "call to order" never reach an item; not
#                matched here, because a pattern with no measured hits is a
#                guess.)
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
# disposition` and `choose()` must agree, or the audit will bless exactly what
# the parser broke. `tests/` would be better; a shared constant is what this
# project has, and `bin/audit.py --only minutes` is how you check they agree.
#
# One apostrophe, because this is a REGEX and not a SQL literal. Doubling it
# for a literal is the caller's job and audit.py does it at the one place it
# inlines this; passed as a bind parameter, as it should be, nothing is doubled.
SUBSIDIARY_SQL = (
    r"(receive\s+and\s+filed?.*((submitted|presented)\s+by|from\s+(mr|ms|mrs|dr|pastor)))"
    r"|((receive\s+and\s+filed?|accept)\s+(the\s+)?ex\s?parte'?\s+forms?)"
    r"|(hear\s+(the\s+|a\s+|an\s+|\w+\s+)*(emergenc|walk[\s-]?on|add[\s-]?on))"
    r"|(hear\s+the\s+item\s+as\s+a\s+regular\s+agenda\s+item)"
    r"|(to\s+reconsider\s+the\s+item)"
    r"|(to\s+adjourn)")


def load_agenda(con, meeting_id):
    """The meeting's published items, addressed by ID. (items, by_code)

    NOT {code: section}, which is what this used to be, because **code is not
    unique within a meeting**. 39 (meeting_id, code) pairs in this archive
    carry more than one row and 25 of them sit in DIFFERENT sections: meeting
    27's C1 is both a Consent resolution and a Public Hearings rezoning, and a
    Planning Commission agenda that lists "PC 01-09-2020 Final Approved Meeting
    Minutes" under Consent yields the code PC1 a second time, for a document
    about a previous meeting.

    Read into a dict keyed on code, with no ORDER BY, whichever row the query
    happened to yield last decided the whole meeting's map. Measured in the
    sandbox with NOTHING changed but `enable_indexscan`: 4 of 688 items stored
    a different disposition, and meeting 220's PC5 stored one under one plan
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

    The minutes reprint the agenda's own title under the code, wrapped, so the
    heading line is a PREFIX of it. That is what tells two items with the same
    code apart - meeting 657 has both

        P1     A RESOLUTION OF PASCO COUNTY, FLORIDA, ESTABLISHING AN
        P1     AN ORDINANCE OF THE BOARD OF COUNTY COMMISSIONERS OF

    and the first two words already separate them. Counting shared leading
    words rather than comparing strings is what survives the line wrap and the
    extractor's spacing ("09 -2020", "PC 05-07-20 20").
    """
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
    to exactly what they did before and 32 change.

    Two signals, in order, because they fail in different places: the title the
    minutes restate under the code (which separates two items in the SAME
    section, as meeting 657's two P1s need), then the section a bulk sentence
    names (which separates a Consent resolution from a Public Hearings rezoning
    when the minutes give no title, as an exception list does). Neither
    deciding is reported rather than hidden - see `n_ambiguous` in main().
    """
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
    """Which of an item's recorded motions is its disposition?

    Subsidiary motions are dropped, and of what is left the LAST is the answer:
    the minutes are chronological, so the final action on an item is what became
    of it. Read against ten items where the first and last disagree, last is
    better or equal on eight - "Approved to hear the Resolution for Richard
    Gehring" then "Approved to adopt the Resolution", "Approved to accept
    exparte forms" then "Approved to continue to June 6".

    **Except that a refusal is never overridden by what follows it.** Taking
    the last motion unconditionally lost a denial or a withdrawal on 4 of the 6
    items where it mattered, and that is the most damaging direction this table
    can be wrong in:

        Denied Staff's recommendation for approval.
        Approved to authorize the Chairman to sign a letter ... expressing
        their opposition ...

        Denied the applicant the right to pave the road ...
        Approved remainder of Staff's Recommendation.

    An approval that follows a denial is a consequential action - write the
    letter, approve what is left - not a reversal. The board said no.

    Returns None when EVERY motion is subsidiary. The minutes then record no
    disposition for this item, and saying so is the honest answer - it is
    already the designed state for the 24% of items the minutes do not dispose
    of in writing. Keeping the subsidiary motion instead would not be
    preserving information; it would be asserting something false, which is the
    whole defect.

    ~200 items still keep more than one substantive motion, and for most the
    minutes genuinely record several unrelated actions under one heading -
    Miscellaneous Business, where each commissioner's motion lands under the
    same item. No positional rule is right there; `minutes.one_motion_per_item`
    in bin/audit.py counts them so the ambiguity is visible rather than implied.
    """
    def about_another_item(s):
        """"Approved to accept the withdrawal of N91." under item RS4.

        A motion naming a DIFFERENT agenda code, and not this one, is that
        item's disposition sitting in the wrong place - and it drags its
        outcome with it, so RS4 was being recorded as `withdrawn`. Restricted
        to codes that are really on this agenda, so a contract number or a road
        name cannot disqualify a good sentence.
        """
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
    """Disposition sentence -> outcome. Order matters; see the module docstring."""
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
    """(occurrences, bulk) - the disposition sentences per ITEM OCCURRENCE.

    Each occurrence is {code, heading, sentences}, in document order, and
    carries a LIST of sentences because an item routinely records several
    motions and only one of them is its disposition (see `choose()`). It used
    to `setdefault` the first and discard the rest, which is how an evidentiary
    motion became 106 items' recorded outcome.

    Per OCCURRENCE and not per code, because a code can appear TWICE in one
    document about two different things: meeting 657's minutes carry a P1 that
    is a stormwater resolution and a P1 that is an animal ordinance. Collapsing
    them into one list made `choose()` pick between motions belonging to
    different items, and then stamped its answer on both. The heading - the
    title the minutes restate under the code - is kept for the same reason: it
    is what tells the two apart.
    """
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
        # An OPEN disposition sentence swallows everything until it ends. This
        # is not fussiness: the exception list wraps, and its second line reads
        # "C69 which were pulled for discussion ..." - which matches ITEM. Let
        # that break the sentence and the exception list silently loses every
        # code after the line break, so items the minutes say were WITHDRAWN
        # get recorded as approved by the bulk motion.
        #
        # Two guards on that swallow, both earned. The video offset is stripped
        # before asking whether the sentence has ended (TAIL), and a line that
        # begins the NEXT item is never eaten however unfinished this sentence
        # looks - being wrong about where an item ends is worse than truncating
        # a disposition, because it silently re-parents everything after it.
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
        # The capital is load-bearing. A disposition always begins a sentence
        # in these documents, so a lowercase match is a WRAPPED LINE - "pulled
        # for discussion. Agenda Items C12, C13, and C34 were withdrawn.",
        # "withdrawn.", "denied PDD's recommendation of denial ...". There are
        # 132 of those against 8,589 real ones, and treating a fragment as a
        # new disposition is how one becomes an item's recorded outcome.
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
    """Fill in the items the bulk sentences cover. Keyed on agenda_items.id.

    `items` is the meeting's published rows and `by_code` indexes them, which
    is what makes "the Consent Agenda with the exception of ..." resolvable:
    the exception list names the few, and the agenda names the many.

    The key is the item's ID, not its code. Returning {code: ...} and then
    updating `WHERE meeting_id=%s AND code=%s` wrote one minutes sentence onto
    every row sharing the code - measured: 58 rows across 28 pairs, each
    carrying a disposition parsed for a genuinely different item, with meeting
    27's consent resolution and its rezoning both reading `approved` from the
    same sentence.
    """
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
        # Exception form. The sentence has two halves that mean different
        # things, and classifying it whole gets the majority exactly backwards:
        #
        #   "Approved the Consent Agenda | with the exception of C29, C48, C50
        #    and C69 which were pulled ... and C27 and C72 which were withdrawn."
        #
        # The lead clause disposes of everything in the section. The tail says
        # what happened INSTEAD to the few it names. Scanning the whole string
        # for "withdraw" marks all ~180 approved items as withdrawn.
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
            # no longer resolves - which is exactly how a disposition the parser
            # has since learned to reject survives the fix that rejected it.
            #
            # The clear runs whenever this meeting's minutes were READ, not only
            # when they yielded something. Guarding on `hits` looked safer and
            # was the bug: a meeting whose only motions are subsidiary correctly
            # resolves to nothing, and the guard then preserved precisely the
            # subsidiary dispositions that decision had just rejected.
            cur.execute(
                "UPDATE agenda_items SET disposition=NULL, outcome=NULL, "
                "outcome_source=NULL WHERE meeting_id=%s AND code IS NOT NULL",
                (meeting_id,))
            if hits:
                # BY ID. `WHERE meeting_id=%s AND code=%s` wrote every row
                # sharing the code, and 39 (meeting_id, code) pairs name more
                # than one row: 58 items carried a disposition parsed for a
                # different item, deterministically and wrongly.
                cur.executemany(
                    "UPDATE agenda_items SET disposition=%s, outcome=%s, "
                    "outcome_source=%s WHERE id=%s",
                    [(d[:400], o, src, item_id)
                     for item_id, (d, o, src) in hits])
        con.commit()

    print(f"{len(rows)} minutes documents · {tot_items:,} agenda items in those "
          f"meetings\n  {matched:,} given a disposition "
          f"({100*matched//max(tot_items,1)}%)")
    for k, v in sorted(by_src.items(), key=lambda kv: -kv[1]):
        print(f"    {k:<16}{v:>7,}")
    # Said out loud rather than left implied: these are the two places where an
    # item's disposition was a judgement rather than a reading.
    print(f"\n  {n_subsidiary:,} subsidiary motions skipped "
          f"(evidence accepted, or a motion to hear the item at all)")
    print(f"  {n_silent:,} items had NOTHING but subsidiary motions and are "
          f"left with no disposition")
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
