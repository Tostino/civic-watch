"""Local proper-noun corrections applied after ASR.

Context biasing (phrases.txt) steers decoding toward these names, but it only
helps where the model was already close. Confident mis-mappings like
"Pascoe"/"Newport Richie" survive biasing and are fixed here instead.

Only unambiguous Pasco County names verified in context belong in this list -
a wrong entry forces a wrong spelling rather than fixing one. Order matters:
longer forms are rewritten before their substrings.
"""
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
