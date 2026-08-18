#!/bin/sh
# Fails if the COMMITTED bundle in frontend/dist does not match the current source.
#
# WHY THIS EXISTS. frontend/dist is committed, and it is what the deployed instance
# actually serves: render.yaml's buildCommand is `pip install -r requirements.txt`, so
# nothing rebuilds the bundle on deploy. A commit that edits frontend/src and leaves
# dist alone therefore ships the OLD UI while the repo shows the new code, and the two
# can disagree indefinitely with nothing to say so. That is precisely how the "insert a
# session" button went on handing every new row the next FREE number — "Session 35"
# sitting above Session 1 — for as long as it did: server.py and App.jsx had both been
# fixed, and the bundle being served had not been rebuilt since before the fix.
#
# The check is a rebuild and a byte comparison, which is the only thing that actually
# answers "is the committed bundle this source?". Vite's output is deterministic for a
# given source and lockfile — verified byte-identical across Node 18 and Node 22 and
# across repeated builds — so a difference here means a stale bundle, not build noise.
#
# Run it yourself any time:  frontend/scripts/verify-dist.sh
set -e

root=$(git rev-parse --show-toplevel)
cd "$root/frontend"

if [ ! -d node_modules ]; then
  echo "verify-dist: installing dependencies (node_modules missing)…"
  npm ci
fi

npm run build

cd "$root"

# Compared against the INDEX, not HEAD, so a bundle that has been rebuilt and `git
# add`ed — but not yet committed — is correctly treated as up to date. On CI the index
# is the checkout, so this is the same comparison either way: does the committed
# bundle differ from what this source builds?
drift=$(git diff --name-status -- frontend/dist
        git ls-files --others --exclude-standard -- frontend/dist | sed 's/^/?\t/')

if [ -n "$drift" ]; then
  echo ""
  echo "✗ The committed frontend/dist is STALE — rebuilding it changed these files:"
  echo ""
  echo "$drift" | sed 's/^/    /'
  echo ""
  echo "  frontend/dist is what the deployed instance serves (render.yaml only runs"
  echo "  pip install, so nothing rebuilds the bundle on deploy). As committed, the"
  echo "  live UI is running code that is NOT what frontend/src says."
  echo ""
  echo "  Fix:  cd frontend && npm run build"
  echo "        git add frontend/dist"
  echo ""
  exit 1
fi

echo "✓ frontend/dist matches the source — the deployed bundle is the committed code."
