# CLAUDE.md — granola-sync

One-way sync: Granola meeting notes → Apple Notes (iCloud "Granola"
folder), shipped as a Mac menu-bar app with a legacy CLI mode.
Two-way sync is externally blocked: Granola's API is read-only
(verified 2026-07-11). Maintainer note: if an untracked HANDOFF.md is
present (it's .gitignored), read it first — it holds the full project
history, verified API schema, and dead ends already ruled out.

## Ground rules (project decisions — don't change without asking)

- Default mode stays **one-way, one-time per note**. The opt-in
  "New + Updated Notes" mode may replace a note's body **only** when
  its read-back fingerprint proves the copy is untouched; anything
  uncertain degrades to a "(updated)" copy beside it, deletions are
  respected, and nothing is ever deleted by the tool. The tool only
  ever touches notes carrying its `granola-id:` footer.
- No two-way/bidirectional code until Granola ships a write API — and
  it stays a maintainer decision to add it then.
- Target must be the **iCloud** account so notes sync across devices.
- Dedupe stays two-layer: state file + `granola-id:` marker check in
  Apple Notes before every create.
- Data source is the **official API** (`public-api.granola.ai/v1`,
  `grn_` Bearer key). Never scrape Granola's local files — they're
  encrypted.
- Python stdlib-only for the engine; `rumps` allowed in the app layer
  only; app packaging via PyInstaller.

## Layout

- `granola_sync.py` — the engine (CLI: sync / `--dry-run` /
  `--inspect`; library: `run_sync()`, raises `SyncError`)
- `app/granola_sync_app.py` — rumps menu-bar app wrapping the engine
- `app/schedule_logic.py` — pure schedule math (unit-testable, no AppKit)
- `app/GranolaSync.spec` + `app/build.sh` — PyInstaller build (Mac only;
  work dirs in `~/Library/Caches/GranolaSyncBuild`, ships
  `app/dist/*.zip`; optional Developer ID signing + notarization via
  `GS_SIGN_IDENTITY` / `GS_NOTARY_PROFILE` — see docs/NOTARIZING.md)
- `app/menubar_icon.png` — menu-bar template icon (35x16 pt, black+alpha)
- `tests/` — unit tests for all pure logic
- `com.granola-sync.plist` — v1 launchd agent (legacy CLI mode;
  placeholder paths — the app offers to disable it on first launch)
- `INSTALL.md` — v1 CLI install (legacy); `INSTALL-APP.md` — end-user
  app install; `docs/NOTARIZING.md` — release signing runbook
- Runtime state on user Macs: `~/.granola-sync/` (api_key chmod 600,
  state.json, config.json, sync.log) — shared by CLI and app

## Commands

```bash
python3 granola_sync.py --inspect    # dump raw API JSON (schema check)
python3 granola_sync.py --dry-run    # list what would sync, write nothing
python3 granola_sync.py              # real sync
python3 -m unittest discover -s tests    # pure-logic tests (run anywhere)
app/build.sh                         # build the .app (Mac only)
tail -20 ~/.granola-sync/sync.log
```

## API gotchas (verified 2026-07-11)

- List endpoint is metadata-only; content is on `GET /v1/notes/{id}`
  in `summary_markdown` (preferred) / `summary_text`.
- API only exposes notes that have an AI summary; others are invisible.
- Throttle detail fetches (0.3s delay) and retry `ConnectionError` —
  the server drops rapid-fire connections.

## Testing

Unit-test pure logic anywhere (md→html, note building, escaping,
state/config, schedule math) — see `tests/`. API + AppleScript layers
are only testable on a Mac with a real key — always ship engine
changes behind `--dry-run` verification first. The rumps UI layer has
no automated tests; smoke-test the built app manually.
