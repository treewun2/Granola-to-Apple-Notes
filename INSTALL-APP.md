# Granola Sync — the Mac app

Granola Sync copies your Granola meeting notes into Apple Notes
(folder **"Granola"** in your iCloud account), so they're searchable
and on all your devices. It lives in the menu bar — no Terminal, no
scripts.

**What you need**

- A Mac (Apple Silicon or Intel, macOS 11+).
- The Granola desktop app with a **Business or Enterprise** plan
  (API keys are a paid-plan feature).

## Install (once, ~2 minutes)

1. **Get the app.** Unzip `Granola-Sync.zip` and drag
   **Granola Sync.app** into your **Applications** folder.
2. **First open — important.** The app isn't signed with Apple yet, so
   don't double-click the first time. Instead: **right-click (or
   Control-click) the app → Open → Open**. macOS remembers your choice;
   after this one time it opens normally.
   - On newer macOS versions, if right-click → Open doesn't offer an
     Open button: open the app once (it will be blocked), then go to
     **System Settings → Privacy & Security**, scroll down, and click
     **"Open Anyway"**.
3. **Add your API key.** A swirl → note icon appears in the menu bar
   and the app asks for your Granola API key. To create one: open **Granola →
   Settings → API → Create new key**, copy the key (it starts with
   `grn_`), and paste it into the app.
4. **First sync.** The app offers to run your first sync. macOS will
   ask: *"Granola Sync" wants access to control "Notes"* — click
   **Allow**. That's it.

Your notes appear in Apple Notes → **Granola** folder. After the first
sync, the app syncs **once a day at 8:00 AM** (if your Mac was asleep,
it catches up when it wakes).

## The menu

- **Sync Now** — sync immediately.
- **Last sync / notes synced** — what happened most recently.
- **Sync Options** — what gets synced:
  - **New Notes Only** (default): each Granola note is copied once and
    then never touched again, even if it changes in Granola.
  - **New + Updated Notes**: notes you edit in Granola are re-synced.
    If you haven't touched its Apple Notes copy, the copy is refreshed
    in place. If you *have* changed the copy, your version is kept
    untouched and the new Granola version appears beside it as
    "… (updated)". Notes you deleted from Apple Notes stay deleted.
    Syncing of updates starts from the moment you turn this on
    (earlier Granola edits aren't back-filled).
- **Sync Automatically** — Off, Every Hour, Every 4 Hours, or Once a
  Day (default: daily at 8:00 AM; pick "Set Daily Sync Time…" to
  change when).
- **Launch at Login** — recommended: the app must be running for
  automatic syncs to happen. (macOS will ask permission to control
  "System Events" the first time — click Allow.)
- **Set API Key…** — paste a new key any time.
- **View Sync Log** — a plain-English record of every sync.

## Good to know

- **Your writing is never lost.** In every mode, the app only touches
  notes it created itself, never overwrites a note you've edited, and
  never deletes anything — in Apple Notes or in Granola.
- **Two-way sync isn't available yet.** Granola's API is read-only,
  so changes made in Apple Notes can't be pushed back to Granola. If
  Granola adds write access, two-way will appear as a Sync Option.
- **Only AI-summarized meetings sync.** If a meeting has no Granola
  AI summary, it's invisible to the sync — that's the first thing to
  check if a note seems missing.
- **No duplicates.** Even across reinstalls or multiple runs, each
  Granola note is created at most once (the app checks Apple Notes
  itself before every create).
- **If notes stop appearing:** System Settings → Privacy & Security →
  **Automation** → Granola Sync → make sure **Notes** is allowed.
- **Quitting mid-sync is safe** — the next sync picks up where it
  left off.

## Upgrading from the v1 command-line version

Just open the app. It finds your existing setup automatically (same
API key, same sync history — nothing re-syncs) and offers to turn off
the old 8:00 AM background job so the two don't both run.

## Uninstall

Quit the app (menu → Quit Granola Sync), drag it to the Trash, and
optionally delete the folder `~/.granola-sync` (your key, settings,
and sync history). Notes already in Apple Notes stay where they are.
