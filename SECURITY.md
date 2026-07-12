# Security Policy

Granola Sync handles two sensitive things: your Granola API key and
the content of your meeting notes. Security reports are taken
seriously even though this is a side project.

## Reporting a vulnerability

**Please do not open a public issue for security problems.**

Report privately via GitHub:
[Security → Report a vulnerability](../../security/advisories/new)
on this repository.

You can expect an acknowledgment within **7 days**. Fixes for
confirmed vulnerabilities ship as a new release as quickly as a
one-maintainer project allows, and reporters are credited in the
release notes unless they prefer otherwise.

## Supported versions

Only the **latest release** receives security fixes. The app has no
auto-update; update by downloading the newest release from the
[Releases](../../releases) page.

## Scope

In scope:

- The sync engine (`granola_sync.py`) — API client, markdown→HTML
  conversion, AppleScript layer, state/key handling
- The menu-bar app (`app/`) and the PyInstaller build
- The release artifacts published on the Releases page

Out of scope (report to the respective vendor):

- Granola's API, app, or infrastructure → [Granola](https://granola.ai)
- Apple Notes, AppleScript/osascript, or macOS itself → Apple

## Design commitments relevant to security

- The engine is **stdlib-only**; the app layer's only dependency is
  `rumps` (pinned). Build deps are pinned in
  `app/requirements-build.txt` and audited weekly for CVEs.
- Your API key lives only in `~/.granola-sync/api_key` (owner-only
  permissions) and is sent only to `https://public-api.granola.ai`.
  It is never logged.
- All note content is HTML-escaped before being written to Apple
  Notes, and AppleScript arguments are passed via argv — never
  interpolated into script source.
- The tool never deletes notes and never modifies a note it cannot
  prove it wrote itself.

The full threat model and current assessment live in
[docs/SECURITY-ASSESSMENT.md](docs/SECURITY-ASSESSMENT.md); the
ongoing maintenance process is
[docs/SECURITY-PROCESS.md](docs/SECURITY-PROCESS.md).
