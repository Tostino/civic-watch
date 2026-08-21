#!/usr/bin/env python
"""Audition the voice that reads answers aloud, from the shell.

    bin/voice.py                          # list the voices
    bin/voice.py "any sentence"           # render it, in the configured voice
    bin/voice.py "..." --voice am_michael --out /tmp/try.mp3
    bin/voice.py --answer hu0EVwDMt0KE    # a real answer, as a reader hears it

CHOOSING A VOICE IS A LISTENING TASK and cannot be done from a table of
numbers, which is what this exists for: the same sentence in several voices,
side by side, before SAY_VOICE is set to one of them. It also prints the
NORMALISED text, which is the other thing that has to be checked by ear -
web/say.py rewrites zoning codes, statute numbers and acronyms before the
phonemiser sees them, and a rule that is wrong is wrong out loud.
"""
import argparse
import os
import sys

# NOT NAMED say.py, though that is what it drives. `bin` is on sys.path for
# `db`, so a bin/say.py would shadow web/say.py and import itself.
HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.join(os.path.dirname(HERE), "web"))

import say                                            # noqa: E402

# The ones worth hearing first. `af_heart` is Kokoro's only grade-A voice and
# the default; the rest are here because a reader might reasonably prefer a
# male or a British one, and the argument for any of them is how it sounds.
SHORTLIST = ["af_heart", "af_bella", "am_michael", "am_fenrir", "bf_emma"]


def main():
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("text", nargs="?", help="what to say")
    ap.add_argument("--answer", help="read a saved answer's first paragraph")
    ap.add_argument("--voice", help=f"one of {len(SHORTLIST)} shortlisted, "
                                    f"or any in voices-v1.0.bin")
    ap.add_argument("--all", action="store_true",
                    help="render the shortlist, one file each")
    ap.add_argument("--out", default="/tmp", help="where to write (default /tmp)")
    args = ap.parse_args()

    if not say.available():
        sys.exit(f"no voice in {say.MODEL_DIR}. Run bin/get_voice.sh")

    text = args.text
    if args.answer:
        import db
        with db.connect() as con:
            row = con.execute("SELECT answer FROM answers WHERE id = %s",
                              (args.answer,)).fetchone()
        if not row:
            sys.exit(f"no answer {args.answer}")
        text = row["answer"].split("\n\n")[0]

    if not text:
        names = sorted(say.voice().get_voices())
        print(f"{len(names)} voices in {say.MODEL_DIR}:\n")
        for i in range(0, len(names), 6):
            print("  " + "  ".join(f"{n:<13}" for n in names[i:i + 6]))
        print(f"\nshortlist: {' '.join(SHORTLIST)}")
        print(f"current:   SAY_VOICE={say.VOICE}")
        return

    print(f"as written:  {text}\n")
    print(f"as spoken:   {say.spoken(text)}\n")

    voices = SHORTLIST if args.all else [args.voice or say.VOICE]
    was = say.VOICE
    try:
        for v in voices:
            say.VOICE = v
            blob = say.render(text)
            path = os.path.join(args.out, f"say-{v}.mp3")
            with open(path, "wb") as f:
                f.write(blob)
            print(f"  {v:<13} {len(blob) / 1024:6.0f} KB  {path}")
    finally:
        say.VOICE = was


if __name__ == "__main__":
    main()
