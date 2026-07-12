# Granola → Apple Notes sync — install (v1 CLI, legacy)

> **Non-technical users: use the menu-bar app instead** — see
> `INSTALL-APP.md`. This page is the original manual/Terminal install;
> it still works, but the app supersedes it (and offers to disable
> this version's daily job if it finds one).

One-way, one-time-per-note sync into the **Granola** folder of your
**iCloud** Apple Notes account. Runs daily at 8:00 AM via launchd
(runs on next wake if the Mac was asleep).

Requires: macOS with `python3` available (comes with Xcode Command
Line Tools) and a Granola API key (Granola → Settings → API →
Create new key; requires a Business/Enterprise plan).

## 1. Put files in place

```bash
mkdir -p ~/.granola-sync
mv ~/Downloads/granola_sync.py ~/.granola-sync/
mv ~/Downloads/com.granola-sync.plist ~/Library/LaunchAgents/
```

## 2. Store your API key (never goes in the script)

```bash
chmod 700 ~/.granola-sync
echo 'grn_PASTE_YOUR_KEY_HERE' > ~/.granola-sync/api_key
chmod 600 ~/.granola-sync/api_key
```

## 3. Dry run, then first real run

```bash
python3 ~/.granola-sync/granola_sync.py --dry-run
python3 ~/.granola-sync/granola_sync.py
```

The first real run triggers a macOS permission prompt:
**"Terminal" wants access to control "Notes"** — click OK.
(When launchd runs it later, approve again if prompted, or check
System Settings → Privacy & Security → Automation if notes stop
appearing.)

If anything looks off, `--inspect` dumps raw API data. The API
schema was verified 2026-07-11: note content arrives in
`summary_markdown` / `summary_text` on the per-note detail
endpoint; the list endpoint is metadata-only.

## 4. Enable the daily schedule

```bash
launchctl load ~/Library/LaunchAgents/com.granola-sync.plist
```

## Useful afterwards

```bash
tail -20 ~/.granola-sync/sync.log        # what happened on recent runs
launchctl start com.granola-sync   # force a run now
launchctl unload ~/Library/LaunchAgents/com.granola-sync.plist  # disable
```

## Known limits

- Only notes with a generated AI summary appear in Granola's API —
  un-processed notes won't sync, silently.
- One-time sync: edits made in Granola after a note has synced are
  never pushed (by design).
- Nested sub-bullets flatten to one level; markdown links render as
  plain text (cosmetic; Apple Notes HTML support is limited).
- The plist hardcodes /Users/YOUR_USERNAME paths — replace them with
  your real home directory before loading it (launchd doesn't expand
  ~ or environment variables).
