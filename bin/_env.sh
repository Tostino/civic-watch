# Sourced by every pipeline script. Not executable on its own.
#
# ONE copy of the DSN handling, because there were seven and they have to agree.
# `env.local.sh` exports the production DSN unconditionally, so a caller that
# injects one - the sandbox does exactly this - has it silently overwritten and
# then operates on the real archive. That is not hypothetical: the invocation
# printed in bin/sandbox.py's own docstring would have truncated production.
#
# Usage, as the first lines of a script after `set -euo pipefail`:
#
#     cd "$(dirname "$0")/.."
#     source bin/_env.sh
#
# Afterwards $PY and $PYTHONPATH are set and $PASCO_DSN is whichever DSN the
# caller actually meant.
#
# ONE env file, and it is in this repo. This used to source a SECOND file by
# absolute path - another project's `active-reading/env.local.sh` - which is
# where the inference key lived. Anything started without going through this
# script therefore held no key, which is precisely how `web/server.py` came to
# run for weeks with `llm_key: false` and `/api/ask` hanging on "thinking".
# A dependency that only some entry points satisfy is not a dependency, it is
# a trap. Everything is in ./env.local.sh now.
INJECTED_DSN="${PASCO_DSN:-}"
source ./env.local.sh
[ -n "$INJECTED_DSN" ] && export PASCO_DSN="$INJECTED_DSN"
export PYTHONPATH=bin
PY=./emb-venv/bin/python

# Which database is about to be written to. Printed every run, because the bug
# above was invisible: a run pointed at the wrong archive looked exactly like a
# run pointed at the right one.
pasco_target() {
    $PY -c 'import sys, re; sys.path.insert(0, "bin"); import db
d = db.dsn()
m = re.search(r"dbname=(\S+)", d) or re.search(r"/([^/?]+)(\?|$)", d)
print(m.group(1) if m else "?")'
}
