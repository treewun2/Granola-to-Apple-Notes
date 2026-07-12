#!/bin/bash
# Pre-release security gate. Run before building/publishing a release:
#
#     scripts/release-preflight.sh
#
# Read-only: checks the working tree, versions, tests, dependency CVEs,
# static analysis, bundled-runtime freshness, and (if present) the code
# signature of the built app. Writes nothing to the repo. Exits non-zero
# if anything needs attention. See docs/SECURITY-PROCESS.md.
#
# Scanner tools (bandit, pip-audit) live in a private venv under
# ~/Library/Caches (created on first run) so the repo and system Python
# stay clean.
set -uo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO="$(dirname "$SCRIPT_DIR")"
TOOLS_VENV="$HOME/Library/Caches/GranolaSyncBuild/tools-venv"
FAILURES=0

fail() { echo "  ✗ $1"; FAILURES=$((FAILURES + 1)); }
ok()   { echo "  ✓ $1"; }
warn() { echo "  ! $1"; }

cd "$REPO"

echo "== 1/7 Working tree"
if [ -z "$(git status --porcelain)" ]; then
    ok "clean (releases build from committed code only)"
else
    fail "uncommitted changes — commit or stash before a release build"
    git status --short | sed 's/^/      /'
fi

echo "== 2/7 Version consistency"
APP_VER=$(sed -n 's/^APP_VERSION = "\(.*\)"/\1/p' app/granola_sync_app.py)
SPEC_VERS=$(grep -o '"CFBundle\(ShortVersion\)\{0,1\}\(String\|Version\)": "[^"]*"' \
            app/GranolaSync.spec | grep -o '[0-9][0-9.]*' | sort -u)
if [ -n "$APP_VER" ] && [ "$SPEC_VERS" = "$APP_VER" ]; then
    ok "app and PyInstaller spec agree: $APP_VER"
else
    fail "version mismatch: APP_VERSION='$APP_VER' vs spec: $SPEC_VERS"
fi

echo "== 3/7 Unit tests"
if python3 -m unittest discover -s tests >/dev/null 2>&1; then
    ok "all tests pass"
else
    fail "unit tests failed — run: python3 -m unittest discover -s tests -v"
fi

echo "== 4/7 Scanner tools"
if [ ! -x "$TOOLS_VENV/bin/pip-audit" ] || [ ! -x "$TOOLS_VENV/bin/bandit" ]; then
    echo "  … installing bandit + pip-audit into $TOOLS_VENV (one time)"
    python3 -m venv "$TOOLS_VENV" >/dev/null 2>&1
    "$TOOLS_VENV/bin/pip" install --quiet --upgrade pip bandit pip-audit \
        || warn "couldn't install scanners (offline?) — CI still covers these"
fi

if [ -x "$TOOLS_VENV/bin/pip-audit" ]; then
    if "$TOOLS_VENV/bin/pip-audit" -r app/requirements-build.txt \
            >/tmp/granola-preflight-audit.txt 2>&1; then
        ok "pip-audit: no known CVEs in pinned build dependencies"
    else
        fail "pip-audit found problems:"
        sed 's/^/      /' /tmp/granola-preflight-audit.txt
    fi
else
    warn "pip-audit unavailable — skipped (weekly CI run covers it)"
fi

echo "== 5/7 Static analysis (bandit, medium+ severity)"
if [ -x "$TOOLS_VENV/bin/bandit" ]; then
    if "$TOOLS_VENV/bin/bandit" -q --severity-level medium \
            -r granola_sync.py app/granola_sync_app.py app/schedule_logic.py \
            >/tmp/granola-preflight-bandit.txt 2>&1; then
        ok "bandit: no medium/high findings"
    else
        fail "bandit findings:"
        sed 's/^/      /' /tmp/granola-preflight-bandit.txt
    fi
else
    warn "bandit unavailable — skipped (CI runs it on every push)"
fi

echo "== 6/7 Bundled Python runtime freshness"
FRESH_OUT=$(python3 "$SCRIPT_DIR/check_build_freshness.py" 2>&1)
FRESH_CODE=$?
echo "$FRESH_OUT" | sed 's/^/      /'
case $FRESH_CODE in
    0) ok "bundled runtime is current" ;;
    2) fail "bundled runtime is stale — update python.org Python before building" ;;
    *) warn "freshness check couldn't run (offline?)" ;;
esac

echo "== 7/7 Built app signature (build cache)"
APP_BUNDLE="$HOME/Library/Caches/GranolaSyncBuild/dist/Granola Sync.app"
if [ -d "$APP_BUNDLE" ]; then
    if codesign --verify --deep --strict "$APP_BUNDLE" 2>/dev/null; then
        SIGNER=$(codesign -dv "$APP_BUNDLE" 2>&1 | sed -n 's/^Authority=//p' | head -1)
        ok "code signature valid (${SIGNER:-ad-hoc signed})"
    else
        fail "code signature invalid — rebuild with app/build.sh"
    fi
    if spctl --assess --type execute "$APP_BUNDLE" 2>/dev/null; then
        ok "Gatekeeper accepts the app (signed + notarized)"
    else
        warn "Gatekeeper would block this build (unsigned/un-notarized — expected until the Developer ID is active; see docs/NOTARIZING.md)"
    fi
else
    warn "no built app found — run app/build.sh, then re-run this check"
fi

echo
if [ "$FAILURES" -eq 0 ]; then
    echo "PREFLIGHT PASSED — clear to build and release."
else
    echo "PREFLIGHT FAILED: $FAILURES item(s) above need attention."
fi
exit $((FAILURES > 0))
