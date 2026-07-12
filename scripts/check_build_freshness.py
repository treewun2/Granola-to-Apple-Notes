#!/usr/bin/env python3
"""Is the Python runtime bundled into the shipped .app still current?

PyInstaller freezes a full CPython (plus its linked OpenSSL) into the
app bundle, so security fixes in Python only reach users via a rebuild
and re-release. Dependency scanners never see this — pip-audit and
Dependabot only look at pip packages — hence this check.

Reads app/BUILD_PYTHON (stamped by app/build.sh) and compares it
against the release data on endoflife.date. Stdlib only; needs network.

Run directly or via scripts/release-preflight.sh; CI runs it weekly
(.github/workflows/security-audit.yml) and opens an issue when stale.

Exit codes: 0 = current, 2 = rebuild recommended, 1 = check failed.
"""

import datetime
import json
import sys
import time
import urllib.request
from pathlib import Path

BUILD_PYTHON_FILE = Path(__file__).resolve().parent.parent / "app" / "BUILD_PYTHON"
EOL_API = "https://endoflife.date/api/python.json"
HTTP_TIMEOUT = 30
MAX_RETRIES = 3


def version_tuple(v: str) -> tuple:
    return tuple(int(p) for p in v.strip().split("."))


def fetch_cycles() -> list:
    req = urllib.request.Request(EOL_API, headers={
        "Accept": "application/json",
        "User-Agent": "granola-sync-build-freshness-check",
    })
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            # nosec B310 — EOL_API is a constant https URL
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:  # nosec B310
                return json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            if attempt == MAX_RETRIES:
                raise
            print(f"fetch attempt {attempt} failed ({e}), retrying ...")
            time.sleep(3 * attempt)


def main() -> int:
    try:
        built = BUILD_PYTHON_FILE.read_text().strip()
        version_tuple(built)  # validate format early
    except (OSError, ValueError) as e:
        print(f"ERROR: can't read a version from {BUILD_PYTHON_FILE}: {e}")
        return 1

    series = ".".join(built.split(".")[:2])
    try:
        cycles = fetch_cycles()
    except Exception as e:
        print(f"ERROR: couldn't fetch {EOL_API}: {e}")
        return 1

    cycle = next((c for c in cycles if c.get("cycle") == series), None)
    if cycle is None:
        print(f"ERROR: Python series {series} not found in endoflife.date data")
        return 1

    stale = False
    latest = cycle.get("latest", "")
    print(f"Bundled Python (app/BUILD_PYTHON): {built}")
    print(f"Latest {series}.x release:          {latest}")

    if latest and version_tuple(latest) > version_tuple(built):
        stale = True
        print(f"STALE: the shipped app bundles Python {built}; {latest} is "
              "available. Patch releases regularly contain CPython/OpenSSL "
              "security fixes — update python.org Python, rebuild "
              "(app/build.sh), and publish a release.")

    # eol == False means no date announced; a string is an ISO date.
    eol = cycle.get("eol")
    if isinstance(eol, str):
        today = datetime.date.today().isoformat()
        if eol <= today:
            stale = True
            print(f"STALE: Python {series} reached end-of-life on {eol} — "
                  "no more security fixes. Move the build to a supported "
                  "series.")
        else:
            print(f"Python {series} receives security fixes until {eol}.")

    if not stale:
        print("OK: bundled runtime is current.")
    return 2 if stale else 0


if __name__ == "__main__":
    sys.exit(main())
