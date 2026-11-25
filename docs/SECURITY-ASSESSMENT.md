# Security Assessment — Granola Sync

**Last full assessment: 2026-07-12** (v2.1.2, pre-notarization).
**Signing update 2026-07-13** (v2.1.4): Developer ID + notarization
live; R2 experiment resolved and R5 closed — see those entries.
This document is both the current assessment and the template for
repeating it — see [Reassessment process](#reassessment-process) at
the end. Day-to-day automation that keeps the assessment honest is
described in [SECURITY-PROCESS.md](SECURITY-PROCESS.md).

## What we're protecting (assets)

1. **The Granola API key** (`~/.granola-sync/api_key`) — grants read
   access to every AI-summarized meeting note in the user's Granola
   account, including other people's meetings shared with them.
2. **Meeting note content** — business-confidential text, attendee
   names, and (in `--inspect` output) attendee email addresses.
3. **The user's Apple Notes** — integrity of notes the user wrote
   themselves; the tool's core promise is that it never damages them.
4. **The user's Mac** — the app runs unsandboxed with Automation
   (Apple Events) permission over Notes and System Events.
5. **The distribution channel** — the GitHub repo, Actions, and
   release zips; a compromised release is a compromise of every
   user's Mac.

## Trust boundaries and data flows

```
Granola API (remote, attacker-influenceable content)
      │  HTTPS GET only, Bearer grn_ key
      ▼
granola_sync.py (engine) ──── ~/.granola-sync/ (key 600, state, log)
      │  osascript, args via argv
      ▼
Apple Notes (iCloud account, folder "Granola")
```

Key observations that shape the threat model:

- **Note content is not fully trusted.** Meeting notes summarize what
  *other people said*; an attacker who attends a meeting (or names a
  calendar event) can inject arbitrary text into the data the engine
  processes. Injection defenses matter even though the API itself is
  official and authenticated.
- The API is **read-only at Granola's end**, which caps blast radius:
  even total compromise of this tool cannot alter or delete Granola
  data.
- Everything else is local. There is no server component, no
  telemetry, no third-party service.

## Findings (2026-07-12)

Severity is judged for this app's actual context: a local, single-user
tool whose worst realistic outcomes are key theft, note disclosure, or
damaging the user's Apple Notes.

### Addressed in this assessment cycle

- **A1 — API key file created non-atomically.** `save_api_key()`
  wrote the file, *then* chmod'd to 600 — a brief window where the
  key was world-readable under the default umask, and the plaintext
  key could land in the file before permissions tightened.
  **Fixed:** the file is now created `0o600` from the first byte
  (`os.open` with mode), with a follow-up chmod to also tighten
  pre-existing files.
- **A2 — `~/.granola-sync/` directory world-readable.** The state
  file and sync log (meeting titles) and the key file's parent dir
  were created with default 755 permissions. **Fixed:** the directory
  is created `0o700` everywhere the engine creates it. Existing
  installs: `chmod 700 ~/.granola-sync` (added to INSTALL.md).
- **A3 — Build dependencies not pinned/audited.** `build.sh`
  installed an unpinned `pyinstaller` — a compromised or vulnerable
  release would ship inside the app. **Fixed:** exact pins in
  `app/requirements-build.txt`, watched by Dependabot and audited
  weekly by pip-audit in CI.
- **A4 — Bundled Python runtime goes stale silently.** The .app
  freezes CPython + OpenSSL at build time (currently 3.12.7, which is
  already 6 patch releases behind 3.12.13); no scanner watches this.
  **Fixed (process):** `app/BUILD_PYTHON` records the build runtime,
  `build.sh` stamps it, and a weekly workflow opens a GitHub issue
  when it falls behind. **Open (action):** update python.org Python
  and rebuild before the next release — the *current* shipped build
  is stale.

### Verified sound (reviewed, no action needed)

- **HTML injection into Apple Notes:** every path from API text to
  note body goes through `html.escape()` (`_inline()`, title, id/rev
  markers). Unit test `test_html_in_source_is_escaped` pins this.
- **AppleScript injection:** all six scripts receive values via
  `on run argv` / `osascript -e script arg1 arg2...`. Nothing is ever
  string-interpolated into script source. No `shell=True` anywhere.
- **Key never logged:** log lines carry titles/IDs/HTTP codes only;
  error paths reference the key *file path*, never the key. The key
  travels only in the `Authorization` header to the hardcoded
  `https://public-api.granola.ai/v1`.
- **TLS:** `urllib.request` against an https URL verifies
  certificates by default; the URL is a module constant, so no
  scheme/host injection (bandit B310 is a false positive here).
  **Trust anchors (fixed 2026-11-25, v2.1.5):** the frozen app's
  OpenSSL looks for CAs only at Homebrew's path, so on Macs without
  Homebrew it had *zero* trusted roots and every sync failed closed
  with CERTIFICATE_VERIFY_FAILED (it never fell back to insecure).
  `make_ssl_context()` now loads Apple's root-owned `/etc/ssl/cert.pem`
  when the default store is empty. Verification mode and hostname
  checking are unchanged and unit-tested. Residual: corporate
  TLS-inspection roots live in the Keychain, which OpenSSL can't
  read; such users get a plain-English certificate error.
  Reading the Keychain would need `truststore`, a dependency change
  for the maintainer to decide.
- **Destructive-action surface:** there is no delete code path at
  all; in-place edits require a SHA-256 fingerprint match proving the
  tool wrote the current body (`plan_update_action` decision table is
  unit-tested to never resolve an uncertain case to overwrite).
- **Repo hygiene:** git history contains source files only (checked
  2026-07-12) — no keys, state files, or build artifacts ever
  committed. `.gitignore` covers `app/dist/`, `HANDOFF.md`, caches.

### Accepted risks (known, deliberate — revisit each assessment)

- **R1 — Marker spoofing via note content.** A meeting participant
  who gets the literal text `granola-id: not_xxx` into a note summary
  could make the dedupe check treat another note as already synced
  (suppressing its sync) or steer update-mode lookups to the wrong
  note. Consequence is capped at *nuisance*: the fingerprint gate
  means it can cause a skipped sync or an extra "(updated)" copy,
  never overwrite or delete. Accepted — defending would complicate
  the marker scheme for an attack with no data-loss payoff.
- **R2 — Hardened-runtime relaxations in `entitlements.plist`.**
  `allow-unsigned-executable-memory` and `disable-library-validation`
  are required for PyInstaller-frozen CPython. Accepted as the cost
  of the packaging choice. **Experiment resolved 2026-07-13:**
  `allow-dyld-environment-variables` was removed for the first
  Developer ID build (v2.1.4) — notarization was accepted and the app
  launches and runs normally without it, so the entitlement is gone
  permanently. Only the two required relaxations remain.
- **R3 — Key on disk rather than macOS Keychain.** A file with 0600
  in a 0700 dir is readable by any process running as the user (macOS
  has no per-app file isolation for unsandboxed apps). Keychain would
  scope reads to the signed app but breaks the stdlib-only rule and
  the CLI/app file-sharing design. Accepted for now; reconsider if
  the user base grows.
- **R4 — `--inspect` prints raw API JSON** (attendee emails, note
  content) to stdout. It's a local, deliberate debug command;
  documented rather than restricted.
- **R5 — Unsigned interim distribution. CLOSED 2026-07-13:** releases
  are Developer ID signed and notarized from v2.1.4 on; the preflight
  gate now fails un-notarized builds. Residual note: the signing key
  lives only in the maintainer's login keychain (backed up as an
  encrypted .p12, never in the repo or CI — deliberate; see the
  distribution-channel asset above).

## Reassessment process

Run a reassessment when **any** of these change (and at least
annually):

- New network endpoint, dependency, or data source
- Any change to `osascript` scripts or how their arguments are built
- Any change to key storage, state files, or file permissions
- Packaging/signing changes (PyInstaller major bump, entitlements,
  notarization setup)
- Granola ships a write API (per project rules this triggers a
  maintainer decision *and* a fresh threat model — write access
  invalidates the "read-only caps the blast radius" assumption)
- A vulnerability report arrives (see SECURITY.md)

Checklist for each pass:

1. Re-read this file's assets/boundaries — still accurate?
2. `grep` audit: every subprocess call still argv-based, no
   `shell=True`; every API-text path still escaped; key still absent
   from all log statements.
3. Run `scripts/release-preflight.sh` — tests, pip-audit, bandit,
   runtime freshness, signature checks.
4. Review open Dependabot/CodeQL/secret-scanning alerts on GitHub.
5. Re-judge each accepted risk above: has its context changed?
6. Update the date at the top and note what changed.
