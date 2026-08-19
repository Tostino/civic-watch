"""Local proper-noun corrections applied after ASR."""
import re

CORRECTIONS = [
    (r"\bNew\s*port\s+Richie\b", "New Port Richey"),
    (r"\bPort\s+Richie\b", "Port Richey"),
    (r"\bZephyr\s+Hills\b", "Zephyrhills"),
    (r"\bZephyr\s+Hill\b", "Zephyrhills"),
    (r"\bPascoe\b", "Pasco"),
    (r"\bBrussel\s+back\b", "Brusselback"),
    (r"\bWait[e]?man\b", "Weightman"),
]

_COMPILED = [(re.compile(p, re.IGNORECASE), r) for p, r in CORRECTIONS]


def fix(text):
    for pat, repl in _COMPILED:
        text = pat.sub(repl, text)
    return text
