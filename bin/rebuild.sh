#!/bin/bash
# Throw away everything DERIVED and build it again, without re-downloading a
# video or re-running ASR. The derived layers are where the bugs live, and
# after enough targeted fixes nobody can say which rows came from which
# version of the code. A rebuild from the same inputs answers that.
#
# KEEP and DROP below are exhaustive over the schema and the block proves it
# every run, so an unclassified table stops the script rather than being
# silently destroyed. KEEP is anything unrecoverable or expensive: the ASR
# output, download state, every HUMAN judgement (labels, overrides, ignores,
# redactions, the curated subject vocabulary, tuned speaker_method ranks),
# the public /ask/<id> answers, the portal payloads, and vec_cache, which is
# what makes a full rebuild twenty minutes instead of hours of GPU.
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
export APPLY
for a in "$@"; do
  case "$a" in
    --yes)      APPLY=1 ;;
    # TWO stages call the model, not one: name_speakers, and segment - which
    # is what cuts a meeting into agenda items. Without credit, segments cannot
    # be rebuilt, so --no-llm PRESERVES them rather than destroying what it cannot
    # put back, and land_agenda then binds spans from the previous run's segments.
    --no-llm)   SKIP_LLM=1 ;;
    *) echo "usage: $0 [--yes] [--no-llm]" >&2; exit 2 ;;
  esac
done

# KEEP holds anything a rebuild cannot re-derive. `redaction` is KEEP because
# dropping it would silently un-redact every home address taken out, which is
# the exact harm the feature exists to prevent. `answers` is KEEP because
# /ask/<id> is a public URL and a redaction surface. `subject`/`subject_term`
# hold a curated vocabulary a rebuild cannot re-derive, and `speaker_method`
# holds precedence ranks somebody tuned. The dropped ones are all cheap and
# deterministic to rebuild, and two stages at the foot of this script do it;
# without them an empty `subject_year` sends the front page to a 163s join.
SKIP_LLM=$SKIP_LLM $PY - <<'PYEOF'
import sys
sys.path.insert(0, "bin")
import db

import os
KEEP = {"utterances", "videos", "speaker_label", "speaker_override",
        "speaker_ignore", "redaction", "answers", "speaker_method",
        "subject", "subject_term",
        "portal_events", "portal_files", "vec_cache"}
DROP = {"meetings", "agenda_items", "cases", "segments", "item_spans",
        "people", "board_terms", "meeting_roster",
        "person_alias", "organizations", "organization_alias",
        "speaker_identity", "voice_affinity",
        "speaker_claim", "speaker_resolved", "subject_year",
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

# SAVED ANSWERS CITE AGENDA ITEMS BY RAW ID and the truncate restarts the
# identity sequence, so a kept citation resolves to the WRONG item on a public
# page. The passage half is safe, keyed by (video_id, start_idx, end_idx).
# This refuses rather than warns: the fix is a policy call about whether to
# drop the item cites or re-key them, and it wants deciding once, by a person.
$PY - <<'ANSWEOF'
import os, sys
sys.path.insert(0, "bin")
import db
con = db.connect(autocommit=True)
n = con.execute("SELECT count(*) FROM answers").fetchone()[0]
c = con.execute("""SELECT count(*) FROM answers
                    WHERE jsonb_array_length(coalesce(cites->'items','[]')) > 0"""
                ).fetchone()[0]
if c:
    print()
    print(f"  !! {c} of {n} saved answers cite agenda items by raw id.")
    print("     This script restarts the agenda_items sequence, so every one")
    print("     of those citations will point at a DIFFERENT item afterwards.")
    print("     Nothing downstream detects it - the page renders a wrong item")
    print("     as confidently as a right one.")
    if os.environ.get("APPLY") == "1" and os.environ.get("REBUILD_ANSWERS_OK") != "1":
        sys.exit("\n  Refusing. Decide what happens to those citations, then\n"
                 "  re-run with REBUILD_ANSWERS_OK=1 to say so.")
ANSWEOF

if [ "$APPLY" -ne 1 ]; then
  echo
  echo "Dry run. Nothing was changed. Re-run with --yes to drop and rebuild."
  exit 0
fi

# PREFLIGHT THE PAID CALL BEFORE DESTROYING ANYTHING. Two stages below call
# the inference API and `set -e` aborts AFTER the truncate, which would leave
# no passages, no index and no spans. name_speakers really did die mid-chain
# on HTTP 402, and the truncate not having happened yet is why it survived.
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
# One statement, so Postgres resolves the foreign-key order and either every
# derived table is empty or none of them is.
SKIP_LLM=$SKIP_LLM $PY - <<'PYEOF'
import sys
sys.path.insert(0, "bin")
import db
import os
KEEP = ["utterances", "videos", "speaker_label", "speaker_override",
        "speaker_ignore", "redaction", "answers", "speaker_method",
        "subject", "subject_term",
        "portal_events", "portal_files", "vec_cache"]
DROP = ["meetings", "agenda_items", "cases", "segments", "item_spans",
        "people", "board_terms", "meeting_roster",
        "person_alias", "organizations", "organization_alias",
        "speaker_identity", "voice_affinity",
        "speaker_claim", "speaker_resolved", "subject_year",
        "passages", "passage_keys", "passage_len", "passage_terms",
        "term_df", "bm25_stats"]
if os.environ.get("SKIP_LLM") == "1":
    DROP.remove("segments")
    KEEP.append("segments")
con = db.connect(autocommit=True)
live = {r[0] for r in con.execute(
    "SELECT tablename FROM pg_tables WHERE schemaname='public'")}
todo = [t for t in DROP if t in live]

# TWO KEPT TABLES POINT AT A DROPPED ONE, and Postgres will not truncate the
# parent of a live foreign key, so this script could never do its job until
# the columns were cleared first. CASCADE is the wrong fix and would cascade
# into videos and utterances, which is the GPU time this exists to preserve.
# Kept exhaustive so a new foreign key cannot reintroduce the failure quietly.
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

# Clearing the values is not enough: Postgres refuses to TRUNCATE any parent
# of a foreign key, referenced or not. DELETE checks per row and passes.
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

# refresh.sh's order, with one difference that only matters from empty:
# land_agenda runs TWICE, because its spans need segments that do not exist on
# the first pass. The first pass gives roster and segment their meetings.
stage "land_agenda (1 of 2: meetings and published items)"
$PY bin/land_agenda.py --redo

stage roster
# BOTH BODIES. `roster.py --body` defaults to the BCC, and this script
# truncates the roster tables first, so a bare call would delete the Planning
# Commission terms and rosters without putting them back, degrading the guard
# that took cross-body misattributions to zero.
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

# BEFORE THE INDEX: the index bakes the resolved speaker name into every
# passage, reading it from `utterance_speaker`, which joins tables this script
# truncates. Run after index_passages, every passage carries no speaker.
stage "speaker_claims (evidence, links and the resolved name)"
$PY bin/speaker_claims.py --all

stage index_passages
$PY bin/index_passages.py

stage "subjects --rollup"
$PY bin/subjects.py --rollup

stage audit
$PY bin/audit.py || true

echo; echo "=== REBUILD DONE ==="; date
