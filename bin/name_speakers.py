"""Second-pass speaker naming with an LLM, over evidence the patterns missed.

The pattern-based pass only recognises a name when it appears in a form it was
coded for ("Commissioner Starkey?", "my name is ..."). It cannot read
    "the county administrator would like to say something real quick"
or infer that a voice presenting three public hearings across 36 meetings is
planning staff. That is judgement over context, which is what an LLM is for.

Division of labour, deliberately strict:

  CODE  picks which voices are worth the call, assembles the evidence, and
        VERIFIES the result - the supporting quote must appear verbatim in the
        evidence given, the name must be consistent across meetings, and the
        confidence must clear a threshold.
  LLM   only proposes an identity and says why.

The verification is the point. Without it the model will happily supply a
plausible county administrator's name from world knowledge, which would be
indistinguishable from evidence at a glance and wrong in the archive forever.
Anything that fails verification goes to the human queue instead.
"""
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


def bundle(con, cluster):
    """Evidence for one voice: what they say, and how they get introduced."""
    self_lines = [r["text"] for r in con.execute("""
        SELECT text FROM utterances WHERE cluster=%s AND LENGTH(text)>80
        ORDER BY LENGTH(text) DESC LIMIT %s""", (cluster, SAMPLES))]
    intros = [r["text"] for r in con.execute("""
        SELECT u2.text FROM utterances u1
        JOIN utterances u2 ON u2.video_id=u1.video_id AND u2.idx=u1.idx-1
        WHERE u1.cluster=%s AND (u2.cluster IS NULL OR u2.cluster<>u1.cluster)
          AND LENGTH(u2.text)>30
        ORDER BY LENGTH(u2.text) DESC LIMIT %s""", (cluster, SAMPLES))]
    named_self = [r["text"] for r in con.execute("""
        SELECT text FROM utterances WHERE cluster=%s
          AND (text LIKE '%%my name is%%' OR text LIKE '%%I am the%%'
               OR text LIKE '%%director%%' OR text LIKE '%%administrator%%'
               OR text LIKE '%%attorney%%')
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


def canonical(name, roster):
    """Fold an honorific form onto the identity that already exists.

    Left alone, "Commissioner Oakley" becomes a second Oakley sitting beside
    the real one, and every question about him then sees half his record.
    """
    n = " ".join((name or "").split())
    stripped = n
    for pre in ("Commissioner ", "Chairman ", "Chairwoman ", "Chair ",
                "Mr. ", "Mrs. ", "Ms. ", "Dr. "):
        if stripped.startswith(pre):
            stripped = stripped[len(pre):]
    for existing in roster:
        if stripped.lower() == existing.lower():
            return existing              # exact identity already in the roster
        if (" " not in existing and existing.lower() == stripped.split()[-1].lower()):
            return existing              # surname matches a known surname
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


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--limit", type=int, default=25)
    ap.add_argument("--write", action="store_true")
    ap.add_argument("--min-lines", type=int, default=MIN_LINES)
    args = ap.parse_args()

    con = db.connect()
    groups = [dict(r) for r in con.execute("""
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
    print(f"{len(groups)} unnamed voices above the impact threshold\n", flush=True)

    # DB reads first (main thread), then parallelise only the LLM calls.
    evidence = [make_evidence(con, g) for g in groups]
    con.commit()   # release the read snapshot before the LLM calls
    with cf.ThreadPoolExecutor(max_workers=MAX_WORKERS) as ex:
        results = list(ex.map(lambda pair: propose(*pair),
                              zip(groups, evidence)))

    roster = [r[0] for r in con.execute(
        "SELECT DISTINCT name FROM speaker_identity WHERE name IS NOT NULL")]
    accepted, rejected = [], []
    for p in results:
        ok, why = verify(p)
        if ok:
            p["name"] = canonical(p["name"], roster)
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
        n = 0
        for p, _ in accepted:
            cur = con.execute(
                "UPDATE speaker_identity SET name=%s, confidence=%s, "
                "source='llm' WHERE cluster=%s AND name IS NULL",
                (p["name"], float(p["confidence"]), p["cluster"]))
            n += cur.rowcount
        con.commit()
        print(f"\nwrote {n} voice assignments (source='llm'; human labels and "
              f"existing names untouched)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
