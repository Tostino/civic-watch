#!/bin/bash
# Throw away everything DERIVED and build it again, without re-downloading a
# single video or re-running ASR.
#
# This exists because the derived layers are where the bugs live - naming,
# segmentation, agenda binding, minutes parsing, the index - and after enough
# targeted fixes nobody can say for certain which rows came from which version
# of the code. A rebuild from the same inputs answers that.
#
# WHAT IS KEPT, and why each one is unrecoverable or expensive:
#
#   utterances        the ASR output. 298,737 rows, 1,036 hours of GPU. This is
#                     the thing this script exists to avoid recomputing.
#   videos            download state and metadata; also the claim/attempt
#                     bookkeeping the fleet uses.
#   speaker_label     HUMAN labels. 59 of them, each a person's judgement.
#   speaker_override  HUMAN corrections at utterance range (§5.8).
#   speaker_ignore    HUMAN "this voice is not a person".
#   portal_events     what the county published, as fetched. Re-fetchable in
#   portal_files      principle - ~2,000 requests against someone else's
#                     server, and they are free to change or withdraw a
#                     document. Not worth re-pulling to prove a parser.
#   vec_cache         454,403 embeddings, 2.5 GB, keyed by content hash. THIS
#                     IS WHY A FULL REBUILD IS CHEAP. Clearing it would turn
#                     twenty minutes into hours of GPU for identical vectors.
#
# Everything else is a function of those, and is dropped. The two VIEWS
# (utterance_speaker, voice_name) are definitions rather than data — they have
# nothing to truncate and resolve again the moment their tables are rebuilt.
#
# The diarization turns and voice centroids live in data/*.json, not in
# Postgres, so they are untouched by definition.
set -euo pipefail
cd "$(dirname "$0")/.."

# AN INJECTED DSN HAS TO SURVIVE env.local.sh, which exports the production one
# unconditionally. Without these three lines the invocation printed in
# bin/sandbox.py -
#
#     PASCO_DSN="$(bin/sandbox.py --dsn)" bash bin/rebuild.sh --yes
#
# - sets the sandbox DSN, has it silently overwritten one line later, and then
# truncates every derived table in the REAL archive. The script whose entire
# purpose is to avoid touching production was the thing that would have
# destroyed it.
source bin/_env.sh

TARGET=$(pasco_target)
echo "target database: $TARGET"
[ "$TARGET" = "pasco_meetings" ] && echo "  *** THIS IS PRODUCTION ***"

APPLY=0
SKIP_LLM=0
for a in "$@"; do
  case "$a" in
    --yes)      APPLY=1 ;;
    # TWO stages call the model, not one: name_speakers, and segment - which
    # is what cuts a meeting into agenda items. Without credit, segments cannot
    # be rebuilt, so --no-llm PRESERVES them rather than destroying something
    # it cannot put back. land_agenda then binds spans from the segments of the
    # previous run, which is honest as long as it is said out loud.
    --no-llm)   SKIP_LLM=1 ;;
    *) echo "usage: $0 [--yes] [--no-llm]" >&2; exit 2 ;;
  esac
done

# The two lists are exhaustive over the schema, and the block below proves it
# every run. A table added later belongs in one of them by a decision somebody
# makes, not by whichever default it happens to fall into - so an unclassified
# table stops this script rather than being silently destroyed.
SKIP_LLM=$SKIP_LLM $PY - <<'PYEOF'
import sys
sys.path.insert(0, "bin")
import db

import os
# `redaction` is KEEP for the same reason the speaker tables are: it holds
# decisions a PERSON made, which outrank anything a rebuild derives. Dropping
# it would silently un-redact every home address the archive has taken out -
# the precise harm the feature exists to prevent - and the transcript would
# keep the marker with no row left to explain or reverse it.
KEEP = {"utterances", "videos", "speaker_label", "speaker_override",
        "speaker_ignore", "redaction", "portal_events", "portal_files",
        "vec_cache"}
DROP = {"meetings", "agenda_items", "cases", "segments", "item_spans",
        "people", "board_terms", "meeting_roster",
        "speaker_identity", "voice_affinity",
        "passages", "passage_keys", "passage_len", "passage_terms",
        "term_df", "bm25_stats"}

con = db.connect(autocommit=True)
live = {r[0] for r in con.execute(
    "SELECT tablename FROM pg_tables WHERE schemaname='public'")}
unclassified = live - KEEP - DROP
missing = (KEEP | DROP) - live
if unclassified:
    sys.exit("rebuild.sh does not know what to do with: "
             + ", ".join(sorted(unclassified))
             + "\nAdd each to KEEP or DROP in bin/rebuild.sh, deliberately.")
if missing:
    print("note: listed but not present: " + ", ".join(sorted(missing)))

if os.environ.get("SKIP_LLM") == "1":
    KEEP.add("segments")
    DROP.discard("segments")

print(f"{'table':22s}{'rows':>12s}   fate")
for t in sorted(live):
    n = con.execute(f"SELECT count(*) FROM {t}").fetchone()[0]
    fate = "KEEP" if t in KEEP else "drop"
    if t == "segments" and fate == "KEEP":
        fate = "KEEP (no LLM to rebuild them)"
    print(f"{t:22s}{n:>12,d}   {fate}")
PYEOF

if [ "$APPLY" -ne 1 ]; then
  echo
  echo "Dry run. Nothing was changed. Re-run with --yes to drop and rebuild."
  exit 0
fi

# PREFLIGHT THE THING THAT CAN FAIL EXPENSIVELY, BEFORE DESTROYING ANYTHING.
#
# Two of the stages below call the inference API, and `set -e` means a failure
# there aborts the script - AFTER the truncate. The archive would be left with
# no passages, no index and no spans, from a run that never had a chance of
# finishing. This costs one token and removes that whole class of outcome.
#
# It is also the specific way this bit us: name_speakers died on
# `HTTP 402 Insufficient Balance` mid-chain, and the only reason that was
# survivable is that the truncate had not happened yet.
if [ "$SKIP_LLM" -ne 1 ]; then
  echo
  echo "=== preflight: inference API ==="
  $PY - <<'PYEOF' || { echo "Refusing to truncate. Fix the API, or use --no-llm." >&2; exit 1; }
import json, os, sys, urllib.error, urllib.request
key = next((os.environ[k] for k in
            ("LLM_API_KEY", "INFERENCE_API_KEY", "DEEPSEEK_API_KEY")
            if os.environ.get(k)), None)
if not key:
    sys.exit("    no API key in the environment")
base = os.environ.get("INFERENCE_API_BASE", "https://api.deepseek.com")
req = urllib.request.Request(
    f"{base}/v1/chat/completions",
    data=json.dumps({"model": os.environ.get("LLM_MODEL", "deepseek-v4-flash"),
                     "messages": [{"role": "user", "content": "ok"}],
                     "max_tokens": 1}).encode(),
    headers={"Content-Type": "application/json",
             "Authorization": f"Bearer {key}"})
try:
    with urllib.request.urlopen(req, timeout=60) as r:
        json.load(r)
    print("    reachable, and the account has credit")
except urllib.error.HTTPError as e:
    sys.exit(f"    HTTP {e.code}: {e.read(300).decode('utf-8', 'replace')}")
except Exception as e:
    sys.exit(f"    {type(e).__name__}: {e}")
PYEOF
fi

echo
echo "=== dropping derived tables ==="
# One statement, so Postgres resolves the foreign-key order itself and the
# whole thing is a single transaction: either every derived table is empty or
# none of them is.
SKIP_LLM=$SKIP_LLM $PY - <<'PYEOF'
import sys
sys.path.insert(0, "bin")
import db
import os
KEEP = ["utterances", "videos", "speaker_label", "speaker_override",
        "speaker_ignore", "redaction", "portal_events", "portal_files",
        "vec_cache"]
DROP = ["meetings", "agenda_items", "cases", "segments", "item_spans",
        "people", "board_terms", "meeting_roster",
        "speaker_identity", "voice_affinity",
        "passages", "passage_keys", "passage_len", "passage_terms",
        "term_df", "bm25_stats"]
if os.environ.get("SKIP_LLM") == "1":
    DROP.remove("segments")
    KEEP.append("segments")
con = db.connect(autocommit=True)
live = {r[0] for r in con.execute(
    "SELECT tablename FROM pg_tables WHERE schemaname='public'")}
todo = [t for t in DROP if t in live]

# TWO KEPT TABLES POINT AT A DROPPED ONE, and Postgres refuses to truncate the
# parent of a live foreign key. `videos.meeting_id` and
# `portal_events.meeting_id` both reference `meetings`, so the TRUNCATE below
# failed outright - meaning this script has never been able to do the thing it
# exists to do. bin/sandbox.py running it against an empty database is what
# found that.
#
# TRUNCATE ... CASCADE is the wrong fix and would be a catastrophe: it cascades
# INTO videos and utterances, which is 1,036 hours of GPU this script exists to
# preserve. Clearing the two columns is right on its own terms - both are
# DERIVED by land_agenda, which re-derives them in the first stage below, so
# they are as much a product of the rebuild as `meetings` itself is.
#
# Kept exhaustive rather than hardcoded: a new foreign key from a kept table
# into a dropped one would otherwise reintroduce the same failure silently.
blockers = [(r[0], r[1]) for r in con.execute("""
    SELECT tc.table_name, kcu.column_name
      FROM information_schema.table_constraints tc
      JOIN information_schema.key_column_usage kcu
        ON kcu.constraint_name = tc.constraint_name
      JOIN information_schema.constraint_column_usage ccu
        ON ccu.constraint_name = tc.constraint_name
     WHERE tc.constraint_type = 'FOREIGN KEY' AND tc.table_schema = 'public'
       AND tc.table_name  = ANY(%s)
       AND ccu.table_name = ANY(%s)""", (KEEP, todo))]
for table, col in blockers:
    con.execute(f'UPDATE {table} SET "{col}" = NULL')
    print(f"cleared {table}.{col} (derived; land_agenda rebuilds it)")

# Clearing the VALUES is not enough: Postgres refuses to TRUNCATE any table
# that is the parent of a foreign key, whether or not a single row actually
# references it. So the referenced ones are emptied with DELETE, which checks
# the constraint per row and passes now that the columns are null. `meetings`
# is 1,214 rows, so the difference in speed is nothing.
parents = {t for t, _ in
           [(r[0], r[1]) for r in con.execute("""
             SELECT ccu.table_name, tc.table_name
               FROM information_schema.table_constraints tc
               JOIN information_schema.constraint_column_usage ccu
                 ON ccu.constraint_name = tc.constraint_name
              WHERE tc.constraint_type = 'FOREIGN KEY'
                AND tc.table_schema = 'public'
                AND tc.table_name  = ANY(%s)""", (KEEP,))]} & set(todo)
quick = [t for t in todo if t not in parents]

con.execute("TRUNCATE " + ", ".join(quick) + " RESTART IDENTITY")
for t in sorted(parents):
    con.execute(f"DELETE FROM {t}")
    seq = con.execute("SELECT pg_get_serial_sequence(%s, 'id')", (t,)).fetchone()[0]
    if seq:
        con.execute(f"ALTER SEQUENCE {seq} RESTART WITH 1")
    print(f"emptied {t} with DELETE (it is a foreign-key parent of a kept table)")
print(f"truncated {len(quick)} tables, deleted {len(parents)}")
PYEOF

stage () { echo; echo "=== $* ==="; date "+    started %H:%M:%S"; }

# The order below is refresh.sh's, with one difference that only matters from
# empty: land_agenda runs TWICE. Its single pass does meetings -> items ->
# spans, and spans need segments, which do not exist yet on the first run. The
# first pass is what gives roster and segment their meetings to work from; the
# second is what binds the spans and lands the transcript-derived items.
stage "land_agenda (1 of 2: meetings and published items)"
$PY bin/land_agenda.py --redo

stage roster
# BOTH BODIES. `roster.py --body` defaults to the Board of County
# Commissioners, so a bare call builds BCC terms and rosters only - and this
# script truncates people, board_terms and meeting_roster first. A rebuild
# would therefore DELETE the 18 Planning Commission board_terms and 1,003
# Planning meeting_roster rows and not put them back, degrading exactly the
# guard that took cross-body misattributions from 10,715 to 0.
#
# The sandbox already reported this and it was misread: `--compare` showed
# "roster rows 16 -> 10 DIFFERS" and that was explained away as a
# five-meeting-fixture artifact. It was this.
$PY bin/roster.py --write
$PY bin/roster.py --write --body "Planning Commission"

stage speaker_id
$PY bin/speaker_id.py --write

if [ "$SKIP_LLM" -eq 1 ]; then
  echo; echo "=== name_speakers SKIPPED (--no-llm) ==="
  echo "    Voices the text signal could not reach stay unnamed."
else
  stage name_speakers
  $PY bin/name_speakers.py --write --limit 150
fi

# chair BEFORE affinity: it decides which cluster carries which commissioner's
# name, and affinity scores voices against the names that exist.
stage chair_anchor
$PY bin/chair_anchor.py --write

stage affinity
$PY bin/affinity.py

if [ "$SKIP_LLM" -eq 1 ]; then
  echo; echo "=== segment SKIPPED (--no-llm) ==="
  echo "    Kept the segments from the previous run. Every item_span below is"
  echo "    bound against THOSE, not against a fresh cut of the meetings."
else
  stage segment
  $PY bin/segment.py --write --jobs 12
fi

stage "land_agenda (2 of 2: spans and transcript items)"
$PY bin/land_agenda.py --redo

# minutes AFTER land: it writes outcomes onto the items land creates.
stage parse_minutes
$PY bin/parse_minutes.py --write

stage index_passages
$PY bin/index_passages.py

stage audit
$PY bin/audit.py || true

echo; echo "=== REBUILD DONE ==="; date
