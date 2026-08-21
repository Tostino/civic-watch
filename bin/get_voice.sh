#!/bin/bash
# The voice that reads answers aloud. Kokoro-82M, Apache 2.0, 338 MB.
#
#   bin/get_voice.sh              # into models/kokoro, where web/say.py looks
#   bin/get_voice.sh /voice       # into a container's mounted volume
#
# NOTHING HAS TO RUN THIS. web/say.py fetches the same two files itself, on
# first use, into SAY_MODEL_DIR - the way HF_HOME fills with the embedding
# model. This is here to PRE-SEED that directory so the first reader does not
# wait the fifteen seconds, and to fill it on a host that has SAY_AUTOFETCH=0
# because it must not reach the network by itself.
#
# The weights are not in the repository and not in the image either way:
# derived data, same rule as data/ and the embedding cache.
set -euo pipefail
cd "$(dirname "$0")/.."

DEST="${1:-${SAY_MODEL_DIR:-models/kokoro}}"
BASE=https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0

mkdir -p "$DEST"
for f in kokoro-v1.0.onnx voices-v1.0.bin; do
    if [ -s "$DEST/$f" ]; then
        echo "have  $DEST/$f"
        continue
    fi
    echo "fetch $f"
    # To a temporary name and renamed, so an interrupted download is never
    # left looking like a model. say.py decides whether it can speak by
    # whether these two files exist.
    curl -fL --progress-bar -o "$DEST/$f.part" "$BASE/$f"
    mv "$DEST/$f.part" "$DEST/$f"
done

ls -lh "$DEST"
echo
echo "Set SAY_VOICE to change who reads. 54 are in voices-v1.0.bin; the"
echo "default is af_heart. bin/voice.py lists them and renders a sample."
