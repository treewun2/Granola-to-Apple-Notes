# Security & Maintenance Process — Granola Sync

How this project stays up to date and secure with near-zero recurring
effort. Almost everything below is automated; the maintainer's job is
to **respond to notifications**, run one script before releases, and
do a short annual review.

Companion documents: [SECURITY-ASSESSMENT.md](SECURITY-ASSESSMENT.md)
(threat model + findings), [SECURITY.md](../SECURITY.md) (public
reporting policy).

## The coverage map

Each risk class has exactly one owner, so nothing is double-reported
and nothing falls through:

| Risk | Watched by | When | You hear about it via |
|---|---|---|---|
| Bugs/regressions in the code | unit tests (`ci.yml`) | every push & PR | failed check on the commit/PR |
| Insecure code patterns | bandit (`ci.yml`) | every push & PR | failed check |
| Injection/taint flaws | CodeQL (`codeql.yml`) | every push + weekly | Security tab → Code scanning + email |
| Known CVEs in pinned build deps | pip-audit (`security-audit.yml`) | weekly | failed workflow email |
| New versions of rumps/PyInstaller | Dependabot (pip) | weekly | automated PR |
| New versions of GitHub Actions | Dependabot (actions) | weekly | automated PR |
| Stale bundled Python/OpenSSL in the shipped .app | `scripts/check_build_freshness.py` (`security-audit.yml`) | weekly | auto-filed GitHub issue labeled `security` |
| Leaked credentials in the repo | GitHub secret scanning + push protection | every push | email + blocked push |
| Vulnerabilities found by others | private vulnerability reporting | as reported | email (Security tab) |
| Bad release artifacts | `scripts/release-preflight.sh` | before each release | script output |

Single point of attention: **GitHub notifications for this repo**
(plus the auto-filed issues). If notifications are quiet, the project
is current.

## Responding to each alert type

**A CI / bandit / CodeQL check fails on a push or PR**
Fix before merging — these gate code changes. A bandit or CodeQL
false positive gets a targeted suppression with a justification
comment, never a blanket disable.

**pip-audit workflow fails (CVE in a pinned build dep)**
1. Read the advisory — does the CVE affect how *this app* uses the
   dependency? (Most PyInstaller CVEs concern building untrusted
   specs — not this project's situation.)
2. If a fixed version exists: take the Dependabot PR (or bump
   `app/requirements-build.txt` manually), then rebuild and release
   if the vulnerable code ships inside the .app (rumps: yes,
   PyInstaller bootloader: yes; PyInstaller build-host-only issues:
   no release needed).
3. If no fix exists yet: note it as an accepted risk in
   SECURITY-ASSESSMENT.md with a revisit date.

**Dependabot PR arrives**
- CI must be green on the PR, then:
- `pyinstaller` bump: merge; next release picks it up.
- `rumps` bump: the app pins a version-specific icon-size workaround
  (see comment in `app/requirements-build.txt`) — build the app and
  smoke-test the menu-bar icon + menus on the Mac before merging.
- GitHub Actions bumps: merge when CI is green.

**"Rebuild recommended" issue appears (stale bundled runtime)**
On the build Mac: install the latest python.org Python in that
series → `scripts/release-preflight.sh` → `app/build.sh` → publish a
release → close the issue. Budget ~20 minutes.

**A private vulnerability report arrives**
Acknowledge within 7 days (SECURITY.md promise). Reproduce, fix, run
a reassessment pass (checklist at the end of SECURITY-ASSESSMENT.md),
release, credit the reporter, publish the advisory.

## Release security gate

Before every release, on the Mac:

```bash
scripts/release-preflight.sh   # must end with PREFLIGHT PASSED
app/build.sh                   # then build (stamps app/BUILD_PYTHON)
```

The preflight verifies: clean tree, version numbers consistent
(app ↔ PyInstaller spec), tests green, no known CVEs in pinned deps,
no medium+ bandit findings, bundled runtime current, and the built
app's code signature (Gatekeeper assessment becomes a hard
expectation once notarized releases start — see docs/NOTARIZING.md).

Release checklist beyond the script:

1. Bump `APP_VERSION` in `app/granola_sync_app.py` **and** both
   version strings in `app/GranolaSync.spec` (preflight cross-checks).
2. Build; commit the updated `app/BUILD_PYTHON` with the release.
3. Upload `app/dist/Granola-Sync.zip` to a GitHub Release; note the
   Python/rumps/PyInstaller versions in the release notes so users
   and future audits can tell what's inside any given build.
4. After notarization is live: verify `spctl --assess` passes on a
   fresh download, not just the local build.

## Scheduled cadence (all times UTC, all on GitHub's runners)

- **Every push/PR:** tests, bandit, CodeQL
- **Monday 06:24:** CodeQL full re-scan (new queries catch old code)
- **Monday 06:42:** pip-audit + bundled-runtime freshness check
- **Monday:** Dependabot version checks (pip + actions)
- **Annually (or on any trigger listed in SECURITY-ASSESSMENT.md):**
  reassessment pass — ~30 minutes with the checklist

## One-time setup state

Enabled on the GitHub repo (2026-07-12): Dependabot alerts,
Dependabot security updates, secret scanning, push protection,
private vulnerability reporting. If the repo is ever transferred or
recreated, re-enable these under Settings → Advanced Security — the
workflow files alone don't cover them.

## Maintaining this process itself

- If a workflow is renamed/added/removed, update the coverage map
  above — the map is the contract that every risk class has an owner.
- The freshness checker consumes the free
  [endoflife.date](https://endoflife.date) API; if that service dies,
  the workflow errors loudly (it does not fail silent) — replace the
  data source rather than deleting the check.
- Keep the engine stdlib-only (project ground rule): it means user
  installs have **zero** runtime dependencies to patch — the whole
  supply-chain surface stays confined to build time, which is exactly
  what this process watches.
