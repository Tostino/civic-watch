"""Parse a published agenda into structured items.

The layout is regular enough to parse without a model, which is the point of
using the published document rather than inferring from audio:

    Development Services - Planning and Economic Growth   <- department
    R77 Confidential Project Wonka Economic Incentive ...  <- code + title
    File Number CA26-5027                                  <- case / file no.
    Comm. Dist. All                                        <- districts affected
    Recommendation Approve                                 <- staff recommendation

Titles wrap across lines and are interrupted by page furniture ("BCC Agenda",
"Page 3 of 32"), so a title is accumulated until a labelled field or the next
item code, with furniture dropped.

This is deliberately a parser, not an LLM pass. The document is machine-made
and consistent; anything here that needs judgement is a sign the layout
changed, and it should fail visibly rather than be smoothed over.
"""
import re

# Section headings. Pre-2020 agendas set these in caps ("CONSENT"), later ones
# in title case; re.I covers both. WORK SESSION only exists in the old layout.
SECTION = re.compile(
    r"^(Public Comment|Resolutions?|Proclamations?|Consent|Regular"
    r"|Public Hearings?|Work Session|Board Reports?|Call To Order|Invocation"
    r"|Pledge Of Allegiance|Roll Call|County Administrator|County Attorney"
    r"|Adjourn\w*)\s*:?\s*$", re.I)

# C5, R75, P79, RS1 - a short letter prefix and a number, at the line start.
ITEM = re.compile(r"^([A-Z]{1,3})\s?-?\s?(\d{1,3})\s+(\S.*)$")

# The case identifier: a department prefix, a two-digit year, a sequence.
# PDD15-587, CAO17-0033, PDE26-0033 - one shape across every era.
CASE_ID = re.compile(r"^(?:N[ou]\.?\s*|Number\s+)?"
                     r"([A-Z]{2,5})\s*-?\s*(\d{2})\s*-?\s*(\d{3,5})\b", re.I)

# The label carrying it changed - "Memorandum CO17-194" before ~2020,
# "File Number CO26-0183" after - and PDF extraction chews it up on the way
# out ("Me morandum", "Memorand", "Mem"). So the label is matched loosely and
# the decision is made on whether what FOLLOWS looks like a case id.
FILE_LABEL = re.compile(r"^(?:File(?:\s*Number)?|Me\s?m\w*)\b\s*[:.]?\s*(.*)$", re.I)

FIELD = re.compile(r"^(Comm\.?\s*Dist\.?|Recommendation|Fiscal Impact"
                   r"|Contact|Department)\s*[:.]?\s*(.*)$", re.I)

# Page furniture that interrupts wrapped titles.
NOISE = re.compile(r"^(BCC Agenda|PC Agenda|Page \d+ of \d+|Page \d+|Agenda|\d+)\s*$",
                   re.I)

FIELD_KEY = {"comm. dist": "districts", "comm dist": "districts",
             "commdist": "districts", "recommendation": "recommendation",
             "fiscal impact": "fiscal_impact", "contact": "contact",
             "department": "department"}


def lines_of(text):
    return [re.sub(r"\s{2,}", " ", ln).strip() for ln in (text or "").splitlines()]


def parse(text):
    """Return (items, sections_seen). Items are in document order."""
    items, seen = [], []
    section = None
    dept = None
    cur = None

    def close():
        nonlocal cur
        if cur:
            cur["title"] = re.sub(r"\s+", " ", cur["title"]).strip(" -–—")
            items.append(cur)
            cur = None

    for ln in lines_of(text):
        if not ln:
            continue
        if NOISE.match(ln):
            continue

        m = SECTION.match(ln)
        if m and len(ln) < 40:
            close()
            section = m.group(1).title()
            seen.append(section)
            dept = None
            continue

        m = ITEM.match(ln)
        if m and section:
            close()
            cur = {"code": f"{m.group(1)}{int(m.group(2))}", "section": section,
                   "department": dept, "title": m.group(3), "file_number": None,
                   "districts": None, "recommendation": None,
                   "fiscal_impact": None}
            continue

        # File/Memorandum line. Accepted only when what follows is shaped like
        # a case id (or the literal "Recurring"), so a title beginning with
        # "Member..." or "File a complaint..." is not mistaken for a field.
        m = FILE_LABEL.match(ln)
        if m and cur:
            rest = (m.group(1) or "").strip()
            if CASE_ID.match(rest):
                cur["file_number"] = re.sub(r"^(?:N[ou]\.?\s*|Number\s+)", "",
                                            rest, flags=re.I).strip()
                continue
            if rest.lower().startswith("recurring"):
                cur["file_number"] = None
                continue

        m = FIELD.match(ln)
        if m and cur:
            key = FIELD_KEY.get(re.sub(r"\s+", " ", m.group(1).lower()).rstrip("."),
                                None)
            if key:
                cur[key] = (m.group(2) or "").strip() or None
            continue

        # Once an item has reached its labelled fields, a plain line is no
        # longer part of its title - it is the department heading introducing
        # the NEXT item. Without this the heading is swallowed, because an item
        # is not closed until the following item code appears, and every item
        # inherits the department of the one before it.
        if cur and (cur["file_number"] or cur["districts"] or cur["recommendation"]):
            close()
            dept = ln
        elif cur:
            cur["title"] += " " + ln        # a wrapped title line
        else:
            dept = ln                        # heading that precedes the next item
    close()
    return items, seen


def normalise_case(file_number):
    """PDE26-0033, 'Memorandum PDD15-587' and 'PDE-260033' are the same shape.

    The agenda writes prefix + 2-digit year + sequence. Which label carries it
    changed in 2020 and the transcript runs the digits together; all of them
    normalise to PREFIX-YY-SEQ so they can join. The sequence is NOT zero-padded
    to a fixed width, because the county does not pad it consistently either -
    PDD15-587 and PDE26-0033 are both real.
    """
    if not file_number:
        return None
    m = CASE_ID.match(file_number.strip().upper())
    return f"{m.group(1).upper()}-{m.group(2)}-{m.group(3)}" if m else None


if __name__ == "__main__":
    import sys
    import db
    con = db.connect(autocommit=True)
    fid = int(sys.argv[1])
    text = con.execute("SELECT body_text FROM portal_files WHERE file_id=%s",
                       (fid,)).fetchone()[0]
    items, seen = parse(text)
    print(f"sections: {' | '.join(seen)}")
    print(f"{len(items)} items\n")
    for it in items:
        print(f"  {it['code']:<6} [{it['section'][:14]:<14}] {it['title'][:66]}")
        if it["file_number"] or it["recommendation"]:
            print(f"         file={it['file_number']} "
                  f"({normalise_case(it['file_number'])})  "
                  f"dist={it['districts']}  rec={it['recommendation']}")
