#!/usr/bin/env bash
# run_tests.sh — Runs the full DetourNYC test suite.
# Called directly or via the git pre-push hook.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

PASS=0
FAIL=0

run() {
  local label="$1"; shift
  echo ""
  echo "▶  $label"
  echo "─────────────────────────────────────────────────────────"
  if "$@"; then
    PASS=$((PASS + 1))
    echo "✅  $label passed"
  else
    FAIL=$((FAIL + 1))
    echo "❌  $label FAILED"
  fi
}

# ── 1. Graph integrity ────────────────────────────────────────────────────────
run "Graph integrity" node tests/test_graph_integrity.js

# ── 2. Routing correctness ────────────────────────────────────────────────────
run "Routing tests" node test_routes.js

# ── 3. Python unit tests ──────────────────────────────────────────────────────
PYTEST=""
if command -v pytest &>/dev/null; then
  PYTEST="pytest"
elif python3 -m pytest --version &>/dev/null 2>&1; then
  PYTEST="python3 -m pytest"
else
  echo ""
  echo "⚠  pytest not found — installing..."
  pip3 install pytest -q
  PYTEST="python3 -m pytest"
fi

run "Python unit tests" $PYTEST tests/test_gtfs.py -v

# ── Summary ───────────────────────────────────────────────────────────────────
echo ""
echo "═══════════════════════════════════════════════════════════"
echo "  Test suites passed: $PASS   Failed: $FAIL"
echo "═══════════════════════════════════════════════════════════"

if [ "$FAIL" -gt 0 ]; then
  echo "🚫  Push blocked — fix the failures above before pushing."
  exit 1
fi

echo "✅  All suites passed. Proceeding with push."
