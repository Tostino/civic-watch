"""Extract who sat on the board, and in what office, from published agendas.

Every agenda opens with a roster block:

    County Commissioners  Honorable Ronald E. Oakley, District 1
                          Honorable Vice Chairman Seth Weightman, District 2
                          Honorable Kathryn Starkey, District 3
                          Honorable Second Vice Chairman Lisa Yeager, District 4
                          Honorable Chairman Jack Mariano, District 5

That block is the answer to two questions the transcript cannot answer:
WHO was on the board on a given date, and WHICH of them was chairing. Both
change - seats turn over, and the chair rotates annually.

Why this matters more than it sounds: speaker_id.py matched voices against a
hardcoded list of the five CURRENT commissioners and applied it to the whole
archive. Checked against these rosters, 23% of commissioner voice assignments
were to someone who was not seated that day, including 14,148 utterances
credited to a commissioner who had not yet taken office. Per-meeting
assignment is only sound if the candidate list is per-meeting too.
"""
import argparse
import collections
import re
import sys

import db

# "Honorable [Chairman] Jack Mariano, District 5". The office, when present,
# sits between the honorific and the name, and the district follows it.
ENTRY = re.compile(
    r"Honorable\s+"
    r"(?P<office>Second\s+Vice[-\s]?Chair(?:man|woman)?|Vice[-\s]?Chair(?:man|woman)?"
    r"|Chair(?:man|woman)?)?\s*,?\s*"
    r"(?P<name>[A-Z][A-Za-z.'\-]+(?:\s+[A-Z][A-Za-z.'\-]+){0,3}?)\s*,?\s*"
    r"(?:Chair(?:man|woman)?|Vice[-\s]?Chair(?:man|woman)?)?\s*,?\s*"
    r"District\s*(?P<district>\d)", re.I)

OFFICE = {"chairman": "chair", "chairwoman": "chair", "chair": "chair",
          "vicechairman": "vice_chair", "vicechairwoman": "vice_chair",
          "vicechair": "vice_chair", "secondvicechairman": "second_vice_chair",
          "secondvicechairwoman": "second_vice_chair",
          "secondvicechair": "second_vice_chair"}

# Words that are part of the honorific, never part of a name.
NOISE = {"esq", "ph", "d", "jr", "sr", "ii", "iii", "honorable"}


def surname(name):
    """The last real word of a name: 'Ronald E. Oakley' -> 'Oakley'."""
    parts = [p.strip(".,") for p in name.split()]
    parts = [p for p in parts if p and p.lower().strip(".") not in NOISE
             and len(p.strip(".")) > 1]
    return parts[-1] if parts else None


def read_roster(text, head_lines=48):
    """{district: (surname, full_name, office)} from an agenda's opening block."""
    head = re.sub(r"\s+", " ", "\n".join((text or "").splitlines()[:head_lines]))
    out = {}
    for m in ENTRY.finditer(head):
        sn = surname(m.group("name"))
        if not sn:
            continue
        office = OFFICE.get(re.sub(r"[-\s]", "", (m.group("office") or "")).lower())
        d = int(m.group("district"))
        # First mention of a district wins; later ones are cross-references.
        out.setdefault(d, (sn, " ".join(m.group("name").split()), office))
    return out


# --------------------------------------------------------- Planning Commission
# A different board, and its agendas say so in a different shape: no
# "Honorable", no district in the modern era, and professional credentials
# where the BCC puts a district ("Jaime Girardi, P.E., Vice Chairman").
#
# Parsing this is not a nicety. With no roster of its own, every Planning
# Commission meeting fell through to the County Commissioners' seats, and
# 54,000 utterances - 21% of the archive - were attributed to commissioners
# who do not sit on this board at all.
PC_CRED = (r"(?:[A-Z]{2,6}|[A-Z]\.\s?[A-Z]\.?|P\.?\s?E\.?|Ph\.?\s?D\.?"
           r"|Esq\.?|Jr\.?|Sr\.?|I{2,3})")
PC_OFFICE = (r"(?:Second\s+Vice[-\s]?Chair(?:man|woman)?"
             r"|Vice[-\s]?Chair(?:man|woman)?|Chair(?:man|woman)?)")
PC_STOP = re.compile(
    r"^\s*(School Board|Legal Counsel|Staff|Clerk|County Attorney|Pasco County"
    r"|Planning Commission (Addendum |Regular )?Agenda|Development Review"
    r"|Board of County)", re.I)
PC_ENTRY = re.compile(
    r"^\s*(?P<name>[A-Z][A-Za-z.'’\-]+(?:\s+[A-Z][A-Za-z.'’\-]+){1,3}?)"
    r"(?:\s*,\s*" + PC_CRED + r")*"
    r"(?:\s*,\s*(?P<office>" + PC_OFFICE + r"))?"
    r"(?:\s*,\s*District\s*(?P<district>\d+))?\s*$")


def read_planning_roster(text, head_lines=60):
    """{seat: (surname, full_name, office, district)} from a PC agenda head."""
    out, seat, started = {}, 0, False
    for raw in (text or "").splitlines()[:head_lines]:
        line = raw.strip()
        if not started:
            # The block label and the first member share a line.
            m = re.search(r"Planning Commission\s*:?\s+(.*)$", line)
            if m and m.group(1).strip():
                started, line = True, m.group(1).strip()
            elif re.fullmatch(r"Planning Commission\s*:?", line, re.I):
                started = True
                continue
            else:
                continue
        if not line:
            continue
        if PC_STOP.match(line):
            break
        m = PC_ENTRY.match(line)
        if not m:
            continue
        name = " ".join(m.group("name").split())
        sn = surname(name)
        if not sn:
            continue
        off = re.sub(r"[-\s]", "", (m.group("office") or "")).lower()
        office = ("second_vice_chair" if off.startswith("secondvice")
                  else "vice_chair" if off.startswith("vice")
                  else "chair" if off.startswith("chair") else None)
        seat += 1
        out[seat] = (sn, name, office,
                     int(m.group("district")) if m.group("district") else None)
    return out


def read_bcc_roster(text):
    """BCC rows keyed by seat, in the same shape as the PC reader."""
    return {d: (sn, fn, office, d)
            for d, (sn, fn, office) in read_roster(text).items()}


# Which reader to use, and how a seat maps onto board_terms.district. For the
# BCC the district IS the seat and is stable across decades. The Planning
# Commission's modern agendas carry no district at all, so a single synthetic
# 0 keeps one term row per person instead of one per seat ordering.
BODIES = {
    "Board of County Commissioners": (read_bcc_roster, lambda seat, dist: dist),
    "Planning Commission":           (read_planning_roster, lambda seat, dist: 0),
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--body", default="Board of County Commissioners")
    args = ap.parse_args()
    if args.body not in BODIES:
        print(f"no roster reader for {args.body!r}; known: "
              f"{', '.join(BODIES)}", file=sys.stderr)
        return 2
    read_for, term_district = BODIES[args.body]
    con = db.connect(autocommit=False)

    rows = con.execute("""
        SELECT pe.meeting_id, pe.event_date::date d, pf.body_text
        FROM portal_files pf JOIN portal_events pe ON pe.id = pf.event_id
        WHERE pf.kind='Agenda' AND pf.chars > 2000 AND pe.body = %s
        ORDER BY pe.event_date""", (args.body,)).fetchall()
    con.commit()

    seats = collections.defaultdict(list)      # (surname, district) -> [dates]
    full = {}
    per_meeting = []
    for r in rows:
        rost = read_for(r["body_text"])
        if len(rost) < 3:                      # a stub agenda, not a real roster
            continue
        for seat, (sn, fn, office, dist) in rost.items():
            seats[(sn, term_district(seat, dist))].append(r["d"])
            full.setdefault(sn, fn)
            if r["meeting_id"]:
                per_meeting.append((r["meeting_id"], sn, dist, office))

    print(f"{len(rows)} agendas · {len(per_meeting)} seat-meetings · "
          f"{len({s for s, _ in seats})} distinct people\n")
    print(f"  {'surname':<14}{'dist':>5}{'meetings':>10}  span")
    for (sn, d), ds in sorted(seats.items(), key=lambda kv: min(kv[1])):
        print(f"  {sn:<14}{d:>5}{len(ds):>10}  {min(ds)} .. {max(ds)}")

    if not args.write:
        print("\n(dry run - pass --write to store)")
        return 0

    with con.cursor() as cur:
        for sn in {s for s, _ in seats}:
            cur.execute("INSERT INTO people (surname, full_name) VALUES (%s,%s) "
                        "ON CONFLICT (surname) DO UPDATE SET "
                        "full_name = COALESCE(people.full_name, EXCLUDED.full_name)",
                        (sn, full.get(sn)))
        for (sn, d), ds in seats.items():
            cur.execute("""
                INSERT INTO board_terms (person_id, body, district, first_seen,
                                         last_seen, meetings)
                VALUES ((SELECT id FROM people WHERE surname=%s), %s,%s,%s,%s,%s)
                ON CONFLICT (person_id, body, district) DO UPDATE SET
                    first_seen = LEAST(board_terms.first_seen, EXCLUDED.first_seen),
                    last_seen  = GREATEST(board_terms.last_seen, EXCLUDED.last_seen),
                    meetings   = EXCLUDED.meetings""",
                (sn, args.body, d, min(ds), max(ds), len(ds)))
        cur.execute("DELETE FROM meeting_roster WHERE meeting_id IN "
                    "(SELECT id FROM meetings WHERE body=%s)", (args.body,))
        cur.executemany("""
            INSERT INTO meeting_roster (meeting_id, person_id, district, office)
            VALUES (%s, (SELECT id FROM people WHERE surname=%s), %s, %s)
            ON CONFLICT (meeting_id, person_id) DO UPDATE
                SET district=EXCLUDED.district, office=EXCLUDED.office""",
            per_meeting)
    con.commit()
    print(f"\nwrote {len(per_meeting)} roster entries")
    return 0


if __name__ == "__main__":
    sys.exit(main())
