#!/bin/bash
# Build "Granola Sync.app" with PyInstaller. Must run on a Mac.
#
# - The venv and PyInstaller work dirs live under ~/Library/Caches so the
#   iCloud-synced project folder doesn't fill up with thousands of build
#   files. Only the final zip is copied back next to this script.
# - Unsigned build (default): PyInstaller ad-hoc signs on Apple Silicon;
#   recipients right-click → Open once (see INSTALL-APP.md).
# - Signed + notarized release (see docs/NOTARIZING.md for one-time setup):
#     GS_SIGN_IDENTITY="Developer ID Application: Your Name (TEAMID)" \
#     GS_NOTARY_PROFILE="granola-sync" \
#     app/build.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
CACHE_DIR="$HOME/Library/Caches/GranolaSyncBuild"
VENV="$CACHE_DIR/venv"

echo "==> Build workspace: $CACHE_DIR"
mkdir -p "$CACHE_DIR"

if [ ! -x "$VENV/bin/python" ]; then
    echo "==> Creating build venv"
    python3 -m venv "$VENV"
fi

echo "==> Installing build dependencies (app/requirements-build.txt)"
"$VENV/bin/pip" install --quiet --upgrade pip setuptools
"$VENV/bin/pip" install --quiet -r "$SCRIPT_DIR/requirements-build.txt"

echo "==> Running PyInstaller"
"$VENV/bin/pyinstaller" --noconfirm --clean \
    --workpath "$CACHE_DIR/build" \
    --distpath "$CACHE_DIR/dist" \
    "$SCRIPT_DIR/GranolaSync.spec"

APP="$CACHE_DIR/dist/Granola Sync.app"
if [ ! -d "$APP" ]; then
    echo "BUILD FAILED: $APP was not produced" >&2
    exit 1
fi

# Stamp the Python version bundled into this build. The weekly
# security-audit workflow compares it against the latest patch release
# (a stale bundled runtime means stale OpenSSL/CPython CVE fixes).
"$VENV/bin/python" -c 'import platform; print(platform.python_version())' \
    > "$SCRIPT_DIR/BUILD_PYTHON"
echo "==> Built with Python $(cat "$SCRIPT_DIR/BUILD_PYTHON")" \
     "(recorded in app/BUILD_PYTHON — commit it with the release)"

mkdir -p "$SCRIPT_DIR/dist"
ZIP="$SCRIPT_DIR/dist/Granola-Sync.zip"
rm -f "$ZIP"
echo "==> Zipping for distribution"
ditto -c -k --keepParent "$APP" "$ZIP"

if [ -n "${GS_NOTARY_PROFILE:-}" ]; then
    if [ -z "${GS_SIGN_IDENTITY:-}" ]; then
        echo "GS_NOTARY_PROFILE is set but GS_SIGN_IDENTITY is not —" \
             "notarization requires a Developer ID signed build." >&2
        exit 1
    fi
    echo "==> Notarizing with Apple (profile: $GS_NOTARY_PROFILE)"
    xcrun notarytool submit "$ZIP" \
        --keychain-profile "$GS_NOTARY_PROFILE" --wait
    echo "==> Stapling the notarization ticket"
    xcrun stapler staple "$APP"
    rm -f "$ZIP"
    ditto -c -k --keepParent "$APP" "$ZIP"   # re-zip the stapled app
fi

echo
echo "Done."
echo "  App:  $APP"
if [ -n "${GS_NOTARY_PROFILE:-}" ]; then
    echo "  Zip:  $ZIP   <- signed + notarized; recipients just unzip"
    echo "                  and drag to Applications"
else
    echo "  Zip:  $ZIP   <- unsigned build (recipients: unzip, drag to"
    echo "                  Applications, right-click -> Open the first time)"
fi
