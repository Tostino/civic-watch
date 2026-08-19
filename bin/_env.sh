# Sourced by every pipeline script, not executable on its own.
# Use it as the first lines after `set -euo pipefail`:
#
#     cd "$(dirname "$0")/.."
#     source bin/_env.sh
#
# Afterwards $PY and $PYTHONPATH are set and $PASCO_DSN is whichever DSN the
# caller actually meant. env.local.sh exports the production DSN
# unconditionally, so an injected one has to be saved and put back or the
# sandbox silently operates on the real archive.
INJECTED_DSN="${PASCO_DSN:-}"
source ./env.local.sh
[ -n "$INJECTED_DSN" ] && export PASCO_DSN="$INJECTED_DSN"
export PYTHONPATH=bin
PY=./emb-venv/bin/python

# Printed every run: a run pointed at the wrong archive looks exactly like a
# run pointed at the right one.
pasco_target() {
    $PY -c 'import sys, re; sys.path.insert(0, "bin"); import db
d = db.dsn()
m = re.search(r"dbname=(\S+)", d) or re.search(r"/([^/?]+)(\?|$)", d)
print(m.group(1) if m else "?")'
}
