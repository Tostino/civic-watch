"""Second-pass speaker naming with an LLM, over evidence the patterns missed."""
import argparse
import concurrent.futures as cf
import json
import sys

import ask                    # reuse the chat() client
import db

MIN_LINES = 60
MIN_MEETINGS = 2
CONFIDENCE = 0.7
SAMPLES = 6
MAX_WORKERS = 4
# How many self-identifying voices one run will take. There are 452 of them
# today, so the first run clears the backlog and later ones handle a meeting's
# worth. Separate from --limit because the two arms are not competing for the
# same budget: one is expensive judgement over sparse evidence, the other is
# reading a name out of a sentence.
SELF_LIMIT = 500

SYS = """You identify speakers in county government meeting transcripts.

You get evidence about ONE unidentified voice: things they said, and what was
said immediately before they spoke (often how they were introduced).

Decide who they are. Prefer, in order:
 1. A personal name, if the evidence states it.
 2. A role, if the evidence states it but not a name ("County Administrator",
    "County Attorney", "Planning Director", "Clerk"). A correct role is far
    more useful than a guessed name.
 3. Nothing, if the evidence does not support either.

Return JSON:
{"name": "<name or role, or null>",
 "kind": "person" | "role" | null,
 "confidence": 0.0-1.0,
 "evidence_quote": "<verbatim span copied from the evidence that supports this>",
 "reasoning": "one sentence"}

Hard rules:
- evidence_quote MUST be copied verbatim from the evidence provided. It is
  checked. If you cannot quote support, return name null.
- Never use outside knowledge about who holds an office. Only this evidence.
- A voice that speaks across many meetings on procedure is likely staff, but do
  not invent their name - give the role.
- If torn between two identities, return null. A wrong name is worse than none."""


# THE PODIUM CONVENTION, as a candidate test rather than as an extractor.
#
# `speaker_claims.PODIUM` has to be strict: it writes a name straight into the
# archive, so it demands a comma and a house number in digits and rejects
# everything else. That strictness is right there and wrong here. This decides
# only whether a voice is WORTH ASKING THE MODEL ABOUT, and a false positive
# costs one API call that `verify()` then throws away. So it is deliberately
# loose: any line that opens with something name-shaped, or says "my name is"
# anywhere.
#
# The two are not redundant. The regex fails on the forms people actually use
# at a Florida podium - "Jeff Gray. Forty three hundred, Lanna Lakes
# Boulevard" (a full stop, and a house number the ASR spelled out in words),
# "I'm Carl Wright. I live at ...", "Hey everybody, Jonathan Federa" - and
# reading a name out of a sentence whose SHAPE varies is what a model is good
# at and a pattern is bad at.
SELF_SHAPED = (r"text ~* 'my name is' "
               r"OR text ~ '^[A-Z][a-z]+ [A-Z][a-z]+[,.] '")


def bundle(con, cluster):
    """Evidence for one voice: what they say, and how they get introduced."""
    # LENGTH>80 keeps the model reading substance rather than "Thank you." It
    # also threw away the one line that matters for somebody who came to the
    # podium once: "Natasha Surewood. Thirty five eleven cat can bloom." is 50
    # characters and is the entire evidence for her name. Anything name-shaped
    # is kept whatever its length; everything else still has to earn its place.
    self_lines = [r["text"] for r in con.execute(f"""
        SELECT text FROM utterances
         WHERE cluster=%s AND (LENGTH(text)>80 OR {SELF_SHAPED})
        ORDER BY ({SELF_SHAPED}) DESC, LENGTH(text) DESC LIMIT %s""",
        (cluster, SAMPLES))]
    intros = [r["text"] for r in con.execute("""
        SELECT u2.text FROM utterances u1
        JOIN utterances u2 ON u2.video_id=u1.video_id AND u2.idx=u1.idx-1
        WHERE u1.cluster=%s AND (u2.cluster IS NULL OR u2.cluster<>u1.cluster)
          AND LENGTH(u2.text)>30
        ORDER BY LENGTH(u2.text) DESC LIMIT %s""", (cluster, SAMPLES))]
    named_self = [r["text"] for r in con.execute(f"""
        SELECT text FROM utterances WHERE cluster=%s
          AND (text LIKE '%%my name is%%' OR text LIKE '%%I am the%%'
               OR text LIKE '%%director%%' OR text LIKE '%%administrator%%'
               OR text LIKE '%%attorney%%'
               -- The podium form, which none of the above reaches.
               OR {SELF_SHAPED})
        ORDER BY LENGTH(text) DESC LIMIT 4""", (cluster,))]
    return self_lines, intros, named_self


def make_evidence(con, g):
    """Assemble evidence in the calling thread; SQLite handles are not shareable."""
    self_lines, intros, named_self = bundle(con, g["cluster"])
    return ("WHAT THEY SAID:\n"
                + "\n".join(f"- {t[:400]}" for t in self_lines)
                + "\n\nSAID JUST BEFORE THEY SPOKE (introductions):\n"
                + "\n".join(f"- {t[:300]}" for t in intros)
                + ("\n\nPOSSIBLE SELF-IDENTIFICATION:\n"
                   + "\n".join(f"- {t[:300]}" for t in named_self)
                   if named_self else ""))


def propose(g, evidence):
    raw = ask.chat([{"role": "system", "content": SYS},
                    {"role": "user",
                     "content": f"This voice speaks in {g['mtgs']} meetings, "
                                f"{g['lines']} lines.\n\n{evidence}"}],
                   as_json=True)
    try:
        p = json.loads(raw)
    except json.JSONDecodeError:
        return None
    p["_evidence"] = evidence
    p["cluster"] = g["cluster"]
    p["mtgs"] = g["mtgs"]
    return p


# Roles so generic they identify nobody. "County Commissioner" is true of five
# people; storing it would merge them into one fictional speaker.
VAGUE = {"county commissioner", "commissioner", "board member", "staff",
         "county staff", "speaker", "member of the public", "citizen",
         "applicant", "presenter", "unknown"}


def canonical(name, roster, board=None):
    """Fold an honorific form onto the identity that already exists.

    Left alone, "Commissioner Oakley" becomes a second Oakley sitting beside
    the real one, and every question about him then sees half his record.

    A SHARED SURNAME IS NOT A SHARED PERSON, and this used to assume it was.
    The second test below folds a full name onto a bare surname, which is right
    for a board member because the surname IS their key - "Ron Oakley" and
    "Oakley" are one identity. It fired on last-word alone, so every other
    person in the county who happens to share a commissioner's surname was
    folded onto the commissioner. Doug Anderson, Assistant Director of
    Facilities Management, introduces himself by name and was stored as
    "Anderson"; 121 such claims were one resolve away from publishing county
    staff and members of the public under sitting commissioners' names.

    This is the fifth time a surname has been treated as a natural key here.
    The previous four merged Sean Poole into Commissioner Poole, collapsed
    every public "Camp" into one facet key, blocked residents from existing
    behind UNIQUE(surname), and left roster.py doing ON CONFLICT (surname)
    after the constraint was dropped.

    `board` maps a surname to that member's full name, so the fold happens only
    when the GIVEN name agrees too. Absent it, the old behaviour would return,
    so it is required rather than optional in practice - the caller passes it.
    """
    n = " ".join((name or "").split())
    stripped = n
    for pre in ("Commissioner ", "Chairman ", "Chairwoman ", "Chair ",
                "Mr. ", "Mrs. ", "Ms. ", "Dr. "):
        if stripped.startswith(pre):
            stripped = stripped[len(pre):]
    board = board or {}
    for existing in roster:
        if stripped.lower() == existing.lower():
            return existing              # exact identity already in the roster
        if (" " not in existing and existing.lower() == stripped.split()[-1].lower()):
            # Same surname. Same person only if the given name agrees with the
            # one the roster holds; otherwise this is somebody else and keeps
            # the name they gave.
            given = stripped.rsplit(" ", 1)[0].strip()
            full = board.get(existing.lower()) or ""
            if given and full and given.lower() in full.lower():
                return existing
            if not given:
                return existing
            continue
    return stripped or n


def verify(p):
    """Reject anything the evidence does not actually support."""
    if not p or not p.get("name"):
        return False, "no name proposed"
    if float(p.get("confidence", 0)) < CONFIDENCE:
        return False, f"confidence {p.get('confidence')}"
    if p["name"].strip().lower() in VAGUE:
        return False, f"too generic ({p['name']!r})"
    q = (p.get("evidence_quote") or "").strip()
    if len(q) < 12:
        return False, "no usable quote"
    # Verbatim check: this is what separates reading the evidence from
    # recalling who the county administrator is.
    hay = " ".join(p["_evidence"].split()).lower()
    if " ".join(q.split()).lower() not in hay:
        return False, "quote not found in evidence"
    return True, "ok"


def _claim(con, p):
    """Record the proposal as evidence, with the quote ON THE RUN THAT CARRIES IT.

    A proposal is about a CLUSTER, and a cluster is a voice heard in many
    places; the quote that justified it was said in exactly one of them. The
    first version of this attached that one quote to every run of the cluster
    archive-wide, which put "So as much as Mr. Homby, I've been working with
    you very" onto 1,413 claims across 93 recordings where nobody said it.
    2,928 of 2,945 claims failed `audit.py claims.quotes_are_verbatim`, which
    is the check that exists to assert exactly this and had never had a quoted
    llm claim to bite on before.

    So the quote travels only as far as it is true. Each run is tested against
    it and carries it only if it contains it; every other run makes the same
    claim with no quote, which is honest - that run IS the cluster's, and the
    reason to believe it was said elsewhere.

    Whitespace-and-case normalised, the same comparison `verify()` makes
    against the evidence bundle, so a quote cannot pass one test and fail the
    other on spacing alone.
    """
    try:
        import speaker_claims
    except ImportError:
        return 0
    quote = (p.get("evidence_quote") or "").strip()
    flat = " ".join(quote.split()).lower()
    n, carried = 0, 0
    for r in con.execute("""
            WITH marked AS (
                SELECT u.video_id, u.local_label, u.idx, u.text,
                       u.idx - ROW_NUMBER() OVER (PARTITION BY u.video_id,
                                                               u.local_label
                                                  ORDER BY u.idx) AS island
                  FROM utterances u
                 WHERE u.cluster = %s AND u.local_label IS NOT NULL)
            SELECT video_id, local_label, MIN(idx) lo, MAX(idx) hi,
                   string_agg(text, ' ' ORDER BY idx) AS said
              FROM marked GROUP BY video_id, local_label, island""",
            (p["cluster"],)):
        here = bool(flat) and flat in " ".join((r["said"] or "").split()).lower()
        speaker_claims.append(con, r["video_id"], r["lo"], r["hi"], p["name"],
                              "llm", quote=quote if here else None,
                              label=r["local_label"])
        n += 1
        carried += here
    return n, carried


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=25)
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--min-lines", type=int, default=MIN_LINES)
    ap.add_argument("--self-limit", type=int, default=SELF_LIMIT,
                    help="voices below the impact gate that state a name")
    args = ap.parse_args()

    con = db.connect()
    # TWO POPULATIONS, AND THE GATE ONLY EVER FITTED ONE OF THEM.
    #
    # `lines >= 60 AND mtgs >= 2`, ordered by `mtgs*lines`, is an IMPACT
    # ranking: spend the model where naming one voice moves the most
    # utterances. That is right for a staffer who appears in forty meetings and
    # is never introduced, and it selects precisely AGAINST the person the
    # archive most owes a name to - a resident who walks to the podium once,
    # speaks for two minutes, and produces five lines in one meeting.
    #
    # Measured before this changed: of the unnamed voices, 32 were eligible and
    # 1,720 were below the gate - and 452 of those below it say a name in their
    # own words. Jonathan Federa (5 lines) and Jeff Gray (7 lines) both stand at
    # the podium, give their name and their address, and were never once put to
    # the model.
    #
    # So the second arm asks for the opposite thing. No line threshold, no
    # meeting threshold, and no impact ordering, because impact is not the
    # question: the evidence is INSIDE the utterance being read, so the model
    # needs no cross-meeting context and the call is cheap and self-contained.
    # `verify()` is what keeps it honest - a proposal whose quote is not in the
    # evidence is rejected, which is what stops "Four" and "Heights" becoming
    # people.
    impact = [dict(r, why="impact") for r in con.execute("""
        WITH agg AS (SELECT cluster, COUNT(*) lines,
                            COUNT(DISTINCT video_id) mtgs
                     FROM utterances WHERE cluster IS NOT NULL GROUP BY cluster),
             named AS (SELECT cluster FROM speaker_identity
                       WHERE name IS NOT NULL GROUP BY cluster)
        SELECT * FROM agg
        WHERE cluster NOT IN (SELECT cluster FROM named)
          AND lines >= %s AND mtgs >= %s
        ORDER BY mtgs*lines DESC LIMIT %s""",
        (args.min_lines, MIN_MEETINGS, args.limit))]
    spoken = [dict(r, why="self") for r in con.execute(f"""
        WITH agg AS (SELECT cluster, COUNT(*) lines,
                            COUNT(DISTINCT video_id) mtgs
                     FROM utterances WHERE cluster IS NOT NULL GROUP BY cluster),
             named AS (SELECT cluster FROM speaker_identity
                       WHERE name IS NOT NULL GROUP BY cluster),
             says AS (SELECT DISTINCT cluster FROM utterances
                       WHERE cluster IS NOT NULL AND ({SELF_SHAPED}))
        SELECT * FROM agg
        WHERE cluster NOT IN (SELECT cluster FROM named)
          AND cluster IN (SELECT cluster FROM says)
          AND NOT (lines >= %s AND mtgs >= %s)
        ORDER BY lines DESC LIMIT %s""",
        (args.min_lines, MIN_MEETINGS, args.self_limit))]
    seen, groups = set(), []
    for g in impact + spoken:
        if g["cluster"] in seen:
            continue
        seen.add(g["cluster"])
        groups.append(g)
    print(f"{len(impact)} unnamed voices above the impact threshold, "
          f"{len(spoken)} below it that state a name\n", flush=True)

    # DB reads first (main thread), then parallelise only the LLM calls.
    evidence = [make_evidence(con, g) for g in groups]
    con.commit()   # release the read snapshot before the LLM calls
    with cf.ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        results = list(ex.map(lambda pair: propose(*pair),
                              zip(groups, evidence)))

    roster = [r[0] for r in con.execute(
        "SELECT DISTINCT name FROM speaker_identity WHERE name IS NOT NULL")]
    # Surname -> the full name the county's own roster holds. What makes
    # "Ron Oakley" foldable onto "Oakley" and "Doug Anderson" not.
    board = {r[0].lower(): r[1] for r in con.execute(
        "SELECT surname, full_name FROM people "
        " WHERE kind='board' AND surname IS NOT NULL")}
    accepted, rejected = [], []
    for p in results:
        ok, why = verify(p)
        if ok:
            p["name"] = canonical(p["name"], roster, board)
        (accepted if ok else rejected).append((p, why))

    print(f"{'cluster':>8} {'mtgs':>5} {'conf':>5}  {'kind':<6} name")
    print("-" * 62)
    for p, _ in sorted(accepted, key=lambda x: -x[0]["mtgs"]):
        print(f"{p['cluster']:>8} {p['mtgs']:>5} {p['confidence']:>5.2f}  "
              f"{(p.get('kind') or ''):<6} {p['name']}")
    print(f"\naccepted {len(accepted)} · rejected {len(rejected)}")
    for p, why in rejected[:8]:
        cl = p.get("cluster") if p else "?"
        nm = (p or {}).get("name")
        print(f"   rejected cluster {cl}: {why}" + (f" (proposed {nm!r})" if nm else ""))

    if args.write and accepted:
        n = claimed = quoted = 0
        for p, _ in accepted:
            cur = con.execute(
                "UPDATE speaker_identity SET name=%s, confidence=%s, "
                "source='llm' WHERE cluster=%s AND name IS NULL",
                (p["name"], float(p["confidence"]), p["cluster"]))
            n += cur.rowcount
            c, q = _claim(con, p)
            claimed += c
            quoted += q
        con.commit()
        # `quoted` is deliberately printed beside `claimed`, because the gap
        # between them is the thing worth watching: it is how many runs make
        # this claim on the strength of a sentence said somewhere else.
        print(f"\nwrote {n} voice assignments (source='llm'; human labels and "
              f"existing names untouched), {claimed} claims, "
              f"{quoted} of them carrying the quote that justified them")
    return 0


if __name__ == "__main__":
    sys.exit(main())
