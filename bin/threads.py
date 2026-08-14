"""Cross-meeting topic threading.

A rezoning or a policy fight spans years of meetings, so the archive needs join
keys that survive across them. Three kinds, in descending reliability:

  case IDs   PDE 267934, R57, PC-6   - stable across continuances
  projects   "Denton MPUD"           - proper names, survive ASR better
  topics     ALPR / Flock cameras    - curated aliases for policy threads

The catch: ASR writes numbers as words about two thirds of the time ("item P
eighty two", "R fifty seven"), so "R57" in one meeting will not join to "R
fifty seven" in the next unless spoken numbers are normalised first. That
normalisation is what makes the whole join work.
"""
import re

UNITS = {"zero": 0, "one": 1, "two": 2, "three": 3, "four": 4, "five": 5,
         "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10,
         "eleven": 11, "twelve": 12, "thirteen": 13, "fourteen": 14,
         "fifteen": 15, "sixteen": 16, "seventeen": 17, "eighteen": 18,
         "nineteen": 19}
TENS = {"twenty": 20, "thirty": 30, "forty": 40, "fifty": 50, "sixty": 60,
        "seventy": 70, "eighty": 80, "ninety": 90}

_NUMWORD = "|".join(list(UNITS) + list(TENS) + ["hundred"])
_SPOKEN = re.compile(rf"\b((?:{_NUMWORD})(?:[\s-]+(?:{_NUMWORD}))*)\b", re.I)


def spoken_to_int(phrase):
    """'eighty two' -> 82, 'two six seven' -> 267 (digit-string reading)."""
    words = re.split(r"[\s-]+", phrase.lower())
    if not words:
        return None
    # digit-string style: every word a single digit, three or more of them
    if len(words) >= 3 and all(w in UNITS and UNITS[w] < 10 for w in words):
        return int("".join(str(UNITS[w]) for w in words))
    total, cur = 0, 0
    for w in words:
        if w in UNITS:
            cur += UNITS[w]
        elif w in TENS:
            cur += TENS[w]
        elif w == "hundred":
            cur = (cur or 1) * 100
        else:
            return None
    total += cur
    return total or None


def normalize_numbers(text):
    """Rewrite spoken numbers as digits so IDs match across meetings."""
    def sub(m):
        v = spoken_to_int(m.group(1))
        return str(v) if v is not None else m.group(0)
    return _SPOKEN.sub(sub, text)


# Letter-prefixed agenda items (R57, PC-6, C55) and long case numbers.
CASE_PATTERNS = [
    re.compile(r"\b(PDE|PDA|CPA|MPUD|PUD|LDC)\s*-?\s*(\d{3,7})\b", re.I),
    re.compile(r"\bitem\s+([A-Z]{1,2})\s*-?\s*(\d{1,3})\b", re.I),
    # Bare agenda references: "R57", "R-57", "R 57" must all collapse to the
    # same key. Restricted to prefixes this board actually uses, because a
    # loose letter+digit rule invents threads out of noise.
    re.compile(r"\b(R|C|P|PC|PH|CA|GB|BC)\s*-?\s*(\d{1,3})\b"),
]
PROJECT = re.compile(r"\b([A-Z][a-zA-Z']+(?:\s+[A-Z][a-zA-Z']+)?)\s+(MPUD|PUD)\b")
STOPWORDS = "the|this|that|a|an|said|proposed|subject|our|your|their|its|his|her"
STOPSET = set(STOPWORDS.split("|"))

# Curated policy threads: things discussed for years under shifting wording.
TOPICS = {
    "alpr": r"\bflock\b|license plate|\balpr\b|plate reader|automatic.{0,12}reader",
    "school-zone-cameras": r"school zone.{0,30}(camera|speed)|speed.{0,15}school zone",
    "impact-fees": r"impact fee",
    "orange-belt-trail": r"orange belt",
    "comprehensive-plan": r"comprehensive plan|comp plan amendment",
    "millage": r"millage|ad valorem|tentative budget",
}
TOPIC_RE = {k: re.compile(v, re.I) for k, v in TOPICS.items()}

CONTINUANCE = re.compile(
    r"continue(?:d|s)?\s+(?:this\s+|the\s+|that\s+)?(?:item\s+|hearing\s+)?"
    r"(?:to|until)\s+([A-Z][a-z]+\s+\d{1,2}|\d{1,2}[./]\d{1,2})", re.I)


def extract(text):
    """Return join keys found in one passage of text."""
    norm = normalize_numbers(text)
    cases, projects, topics = set(), set(), set()

    # Long application numbers identify a case for life - they survive
    # continuances and reappear years later, so they join ACROSS meetings.
    for m in CASE_PATTERNS[0].finditer(norm):
        cases.add(f"{m.group(1).upper()}-{m.group(2)}")

    # Short agenda references (C-2, PC-4, R-57) are POSITIONAL, not identity:
    # every agenda has a C-1, and "C 2" is also a commercial zoning district
    # code. They link discussion within one meeting and must never be used as
    # a cross-meeting key, or unrelated cases merge into fictional threads.
    local = set()
    for pat in CASE_PATTERNS[1:]:
        for m in pat.finditer(norm):
            local.add(f"{m.group(1).upper()}-{int(m.group(2))}")
    for m in PROJECT.finditer(text):
        name = re.sub(rf"^(?:{STOPWORDS})\s+", "", m.group(1), flags=re.I).strip()
        # "the MPUD" / "this PUD" are references, not project names; a bare
        # article would otherwise become a thread spanning every meeting.
        if name and name.lower() not in STOPSET:
            projects.add(f"{name.title()} {m.group(2).upper()}")
    for name, rx in TOPIC_RE.items():
        if rx.search(text):
            topics.add(name)

    # Run on the normalised text: raw transcripts say "continued to September
    # nine", which no date pattern will match.
    conts = [m.group(1) for m in CONTINUANCE.finditer(norm)]
    return {"cases": sorted(cases), "projects": sorted(projects),
            "topics": sorted(topics), "local_ids": sorted(local),
            "continued_to": conts}


def global_keys(text):
    """Only the keys safe to join on across meetings."""
    e = extract(text)
    return ([("case", c) for c in e["cases"]]
            + [("project", p) for p in e["projects"]]
            + [("topic", t) for t in e["topics"]])


if __name__ == "__main__":
    import collections
    import db

    con = db.connect()
    rows = con.execute("SELECT video_id, text FROM utterances").fetchall()
    keys = collections.defaultdict(set)
    kinds = collections.Counter()
    for r in rows:
        e = extract(r["text"])
        for k in e["cases"]:
            keys[("case", k)].add(r["video_id"]); kinds["case"] += 1
        for k in e["projects"]:
            keys[("project", k)].add(r["video_id"]); kinds["project"] += 1
        for k in e["topics"]:
            keys[("topic", k)].add(r["video_id"]); kinds["topic"] += 1

    print(f"{len(rows)} utterances / "
          f"{len(set(r['video_id'] for r in rows))} meetings\n")
    print(f"{'kind':<10}{'distinct':>10}{'multi-meeting':>15}")
    for kind in ("case", "project", "topic"):
        sel = {k: v for k, v in keys.items() if k[0] == kind}
        multi = {k: v for k, v in sel.items() if len(v) > 1}
        print(f"{kind:<10}{len(sel):>10}{len(multi):>15}")

    print("\nlongest-running threads:")
    for (kind, k), v in sorted(keys.items(), key=lambda x: -len(x[1]))[:12]:
        print(f"  {kind:<8} {k:<28} {len(v):>3} meetings")
