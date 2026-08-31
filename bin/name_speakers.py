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


# Worth ASKING the model about: anything name-shaped, or "my name is".
# Looser than speaker_claims.PODIUM on purpose - that one writes a name
# straight into the archive, this only picks candidates and verify() is the
# gate. Up to two leading words, so "Hello, Adam Brusselback." counts.
SELF_SHAPED = (r"text ~* 'my name is' "
               r"OR text ~ '^(?:[A-Za-z'']+[,.]?[[:space:]]+){0,2}"
               r"[A-Z][a-z]+[[:space:]][A-Z][a-z]+[,.][[:space:]]'")


def bundle(con, cluster):
    """Evidence for one voice: what they say, and how they get introduced."""
    # LENGTH>80 keeps the model on substance, and threw away the only line
    # that matters for a one-off speaker: "Natasha Surewood. Thirty five
    # eleven cat can bloom." is 50 characters and is the whole evidence.
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
         "applicant", "presenter", "unknown",
         # Single-word offices name a chair, not a person: the archive holds
         # many attorneys and one Madam Clerk already.
         "clerk", "attorney", "coach", "professor", "director", "manager",
         "engineer", "planner", "chairman", "chairwoman", "chair"}


def canonical(name, roster, board=None):
    """Fold an honorific form onto the identity that already exists.

    Left alone, "Commissioner Oakley" becomes a second Oakley sitting beside
    the real one, and every question about him then sees half his record.

    A SHARED SURNAME IS NOT A SHARED PERSON. Folding on the last word alone
    put Doug Anderson, Assistant Director of Facilities Management, under
    Commissioner Donald E. Anderson's key. `board` maps surname -> full name
    so the fold needs the given name to agree too.
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


def verify(p, board=None):
    """Reject anything the evidence does not actually support."""
    if not p or not p.get("name"):
        return False, "no name proposed"
    if float(p.get("confidence", 0)) < CONFIDENCE:
        return False, f"confidence {p.get('confidence')}"
    if p["name"].strip().lower() in VAGUE:
        return False, f"too generic ({p['name']!r})"
    # A PERSON HAS TWO NAMES HERE, and one word is not a person, it is a
    # collision waiting to happen. The first widened run proposed "Mike" for
    # two different voices, which would have merged two people into one facet
    # key, and "Thomas", "Radman", "Land" and "JN" - the last from a line the
    # ASR mangled. Somebody at a podium giving their name gives both halves.
    #
    # A one-word person is a collision waiting to happen ("Mike" proposed for
    # two voices). No exemption for board surnames: a bare "Fitzpatrick" went
    # onto a 2024 voice two years after she left the board. This runs BEFORE
    # canonical(), so "Ron Oakley" is two words here and folds afterwards.
    name = " ".join(p["name"].split())
    if p.get("kind") == "person" and len(name.split()) < 2:
        return False, f"one word for a person ({name!r})"
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

    A proposal is about a cluster; the quote was said in one place. Stamping
    it on every run put one sentence on 1,413 claims across 93 recordings
    and failed quotes_are_verbatim 2,928 times. Each run carries the quote
    only if it contains it; the rest claim the name with none.
    """
    try:
        import speaker_claims
    except ImportError:
        return 0
    # The archive's own words, not the model's retyping: it returned
    # "planning and development." against a transcript reading "Planning and
    # Development." - true, not verbatim. Match loosely, store the source.
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
        said = " ".join((r["said"] or "").split())
        at = said.lower().find(flat) if flat else -1
        exact = said[at:at + len(flat)] if at >= 0 else None
        speaker_claims.append(con, r["video_id"], r["lo"], r["hi"], p["name"],
                              "llm", quote=exact, label=r["local_label"])
        here = exact is not None
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
    # Two populations. `lines>=60 AND mtgs>=2` ranked by mtgs*lines is an
    # IMPACT gate - right for a staffer in forty meetings, and it excluded
    # every resident who speaks once. Measured: 32 eligible, 1,720 below it,
    # 452 of those saying a name out loud. The second arm drops the
    # thresholds because the evidence is inside the utterance being read.
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
        ok, why = verify(p, board)
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
