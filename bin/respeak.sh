#!/bin/bash
# Re-derive speaker identity after the voice-corroboration fix, and rebuild
# everything that bakes a speaker name into it.
#
# Order is the one refresh.sh documents: speaker_id resets source='llm', so
# name_speakers must follow it; affinity scores each voice against the name its
# cluster now carries, so it must follow the naming; and index_passages bakes
# the resolved name into exchange-passage text, so it must follow all three.
# Segmentation and the agenda binding are untouched by this and are not re-run.
#
# `affinity` was missing here, and refresh.sh has always had it. Re-deriving
# identity moves names between clusters, so every stored similarity is
# measured against a name that may no longer be there - which surfaced as
# `speaker.no_disproved_names` failing with thousands of violations after a
# clean run, the check correctly reporting that people were shown as someone
# their own voice had been measured not to be.
#
# `chair_anchor` was missing for the same reason and cost the same way. It is
# the only stage that can contradict the archive-wide cluster majority about a
# commissioner, so a re-derivation without it hands three of them back the name
# the drift gave them - 69,596 utterances, reported by
# `speaker.chair_anchor_intact`. It goes after the naming and before affinity,
# exactly as refresh.sh orders it.
set -euo pipefail
cd "$(dirname "$0")/.."
source bin/_env.sh

echo "=== speaker_id ==="; date "+    started %H:%M:%S"
$PY bin/speaker_id.py --write

echo; echo "=== name_speakers ==="; date "+    started %H:%M:%S"
$PY bin/name_speakers.py --write --limit 150

echo; echo "=== chair_anchor ==="; date "+    started %H:%M:%S"
$PY bin/chair_anchor.py --write

echo; echo "=== affinity ==="; date "+    started %H:%M:%S"
$PY bin/affinity.py

echo; echo "=== index_passages ==="; date "+    started %H:%M:%S"
$PY bin/index_passages.py

echo; echo "=== audit ==="; date "+    started %H:%M:%S"
$PY bin/audit.py || true

echo; echo "=== RESPEAK DONE ==="; date
