#!/usr/bin/env python3
"""
granola_sync_app.py — menu-bar Mac app around the granola_sync engine.

Phase 2 of granola-sync: the exact same one-way, one-time-per-note sync
as the v1 CLI, packaged for non-technical users. See HANDOFF.md.

- UI: rumps (the only non-stdlib dependency; the engine stays stdlib-only).
- Scheduling is app-managed: an internal timer with launchd-like
  "run on next wake" catch-up (see app/schedule_logic.py). No plist.
- Engine work runs on a background thread; rumps/AppKit UI is touched
  only from the main thread (a timer tick drains an event queue).
- Shares ~/.granola-sync (api_key, state.json, config.json, sync.log)
  with the v1 CLI, so an existing install carries over with no
  migration and no duplicate notes.

Run in development:  python3 app/granola_sync_app.py  (pip install rumps)
Build the .app:      app/build.sh  (must run on a Mac)
"""

import datetime
import queue
import subprocess
import sys
import threading
from contextlib import contextmanager
from pathlib import Path

# In development the engine lives one directory up. (In the built app,
# PyInstaller bundles granola_sync as a module — see GranolaSync.spec.)
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import rumps  # noqa: E402

import granola_sync as engine  # noqa: E402
import schedule_logic as sl  # noqa: E402

APP_NAME = "Granola Sync"
APP_VERSION = "2.1.4"

# Menu-bar icon: black template PNG (spiral → arrow → note), drawn at
# 35x16 pt. macOS recolors template images for dark mode / highlight.
MENU_BAR_ICON_FILE = "menubar_icon.png"
MENU_BAR_ICON_SIZE = (35.0, 16.0)
MENU_BAR_FALLBACK = "🥣"  # only if the icon file is missing (dev checkouts)

TICK_SECONDS = 2           # event-pump cadence (fast so progress feels live)
SCHEDULE_EVERY_TICKS = 15  # schedule check + stats refresh ≈ every 30s

# v1 launchd agent (legacy CLI installs). The repo plist now ships with
# the generic label; the original personal label covers older installs.
V1_PLISTS = (
    Path.home() / "Library/LaunchAgents/com.granola-sync.plist",
    Path.home() / "Library/LaunchAgents/com.stevepettit.granola-sync.plist",
)

MODE_LABELS = (("off", "Off"),
               ("hourly", "Every Hour"),
               ("every_4_hours", "Every 4 Hours"),
               ("daily", "Once a Day"))

SYNC_MODE_LABELS = (("one_time", "New Notes Only"),
                    ("update_changed", "New + Updated Notes"))

HELP_TEXT = (
    "Granola Sync copies your Granola meeting notes into Apple Notes — "
    "folder “Granola” in your iCloud account — so they're searchable "
    "and on all your devices.\n\n"
    "Sync Options\n"
    "• New Notes Only (default): each note is copied once and never "
    "touched again, even if it later changes in Granola.\n"
    "• New + Updated Notes: notes you edit in Granola are re-synced. "
    "If you haven't touched the Apple Notes copy, it's refreshed in "
    "place; if you have, your version is left alone and the new "
    "Granola version appears next to it as “… (updated)”. Notes you "
    "deleted from Apple Notes stay deleted. Nothing you write in "
    "Apple Notes is ever overwritten or lost.\n"
    "• Two-way sync (Apple Notes → Granola) isn't possible yet: "
    "Granola's API is read-only. If Granola adds write access, it'll "
    "show up here as an option.\n\n"
    "How it works\n"
    "• Only meetings Granola has AI-summarized are available to sync — "
    "a note with no summary won't appear.\n"
    "• Duplicates are prevented twice over: a local record, plus a "
    "check inside Apple Notes before every create.\n"
    "• The app only ever touches notes it created itself (they carry a "
    "“granola-id” footer) — the rest of your Apple Notes are off-limits.\n\n"
    "Tips\n"
    "• If notes stop appearing: System Settings → Privacy & Security → "
    "Automation → Granola Sync → allow “Notes”.\n"
    "• Quitting mid-sync is safe — the next sync picks up where it "
    "left off.\n"
    "• “View Sync Log” shows exactly what every sync did.\n\n"
    f"Version {APP_VERSION} · data lives in ~/.granola-sync"
)

# Login-item AppleScripts follow the engine's convention: values arrive
# via argv, never string-interpolated into the script source.
LOGIN_ITEM_ADD = '''
on run argv
    set appPath to item 1 of argv
    tell application "System Events"
        if (count of (login items whose name is "Granola Sync")) is 0 then
            make login item at end with properties {path:appPath, hidden:false}
        end if
    end tell
    return "ok"
end run
'''

LOGIN_ITEM_REMOVE = '''
on run argv
    tell application "System Events"
        delete (every login item whose name is "Granola Sync")
    end tell
    return "ok"
end run
'''


def frozen_app_bundle():
    """Path to the .app bundle when running the PyInstaller build, else None."""
    if getattr(sys, "frozen", False):
        for p in Path(sys.executable).resolve().parents:
            if p.suffix == ".app":
                return p
    return None


def resource_path(name: str) -> Path:
    """A bundled data file: next to this script in dev, in the PyInstaller
    extraction dir (sys._MEIPASS) inside the built app."""
    return Path(getattr(sys, "_MEIPASS", Path(__file__).resolve().parent)) / name


class GranolaSyncApp(rumps.App):
    def __init__(self):
        icon = resource_path(MENU_BAR_ICON_FILE)
        if icon.exists():
            super().__init__(APP_NAME, icon=str(icon), template=True,
                             quit_button="Quit Granola Sync")
            # rumps (pinned 0.4.0) forces status-bar images to 20x20 pt,
            # which would squash this wide glyph; restore its real aspect.
            self._icon_nsimage.setSize_(MENU_BAR_ICON_SIZE)
        else:
            super().__init__(APP_NAME, title=MENU_BAR_FALLBACK,
                             quit_button="Quit Granola Sync")
        self.config = engine.load_config()

        self._events = queue.Queue()
        self._sync_thread = None
        self._tick_count = 0
        self._did_startup = False
        self._modal_depth = 0
        self._first_sync_pending = False
        self._last_tick_error = None

        self.mi_sync = rumps.MenuItem("Sync Now", callback=self.on_sync_now)
        self.mi_last = rumps.MenuItem("Starting up…")
        self.mi_total = rumps.MenuItem("")
        self.mi_options = rumps.MenuItem("Sync Options")
        self.mi_sync_mode = {}
        for mode, label in SYNC_MODE_LABELS:
            self.mi_sync_mode[mode] = rumps.MenuItem(
                label, callback=self.on_pick_sync_mode)
        self.mi_auto = rumps.MenuItem("Sync Automatically")
        self.mi_mode = {}
        for mode, label in MODE_LABELS:
            self.mi_mode[mode] = rumps.MenuItem(label, callback=self.on_pick_mode)
        self.mi_daily_time = rumps.MenuItem("Set Daily Sync Time…",
                                            callback=self.on_set_daily_time)
        self.mi_login = rumps.MenuItem("Launch at Login",
                                       callback=self.on_toggle_login)
        self.mi_key = rumps.MenuItem("Set API Key…", callback=self.on_set_key)
        self.mi_log = rumps.MenuItem("View Sync Log", callback=self.on_view_log)
        self.mi_help = rumps.MenuItem("Help", callback=self.on_help)

        self.menu = [self.mi_sync, None, self.mi_last, self.mi_total, None,
                     self.mi_options, self.mi_auto, self.mi_login,
                     self.mi_key, None, self.mi_log, self.mi_help]
        for mode, _ in SYNC_MODE_LABELS:
            self.mi_options.add(self.mi_sync_mode[mode])
        for mode, _ in MODE_LABELS:
            self.mi_auto.add(self.mi_mode[mode])
        self.mi_auto.add(self.mi_daily_time)

        self._refresh_schedule_ui()
        self._refresh_stats()

        self._timer = rumps.Timer(self._tick, TICK_SECONDS)
        self._timer.start()

    # ------------------------------------------------------------- UI state

    @contextmanager
    def _modal(self):
        """Marks 'a dialog is up' so reentrant timer ticks stay out."""
        self._modal_depth += 1
        try:
            yield
        finally:
            self._modal_depth -= 1

    def _is_syncing(self) -> bool:
        return self._sync_thread is not None and self._sync_thread.is_alive()

    def _set_error_badge(self, on: bool):
        """⚠️ next to the menu-bar icon (or appended to the fallback title)."""
        base = "" if self.icon else MENU_BAR_FALLBACK
        self.title = (base + "⚠️") if on else (base or None)

    def _total_synced(self) -> int:
        try:
            return len(engine.load_state().get("synced", {}))
        except engine.SyncError:
            return 0

    def _refresh_schedule_ui(self):
        current = self.config.get("schedule", {}).get("mode", "off")
        for mode, _ in MODE_LABELS:
            self.mi_mode[mode].state = 1 if mode == current else 0
        h, m = sl.parse_daily_time(self.config.get("schedule", {}).get("time"))
        self.mi_daily_time.title = f"Set Daily Sync Time… ({sl.format_daily_time(h, m)})"
        self.mi_login.state = 1 if self.config.get("launch_at_login") else 0
        current_sync = self.config.get("sync_mode", "one_time")
        for mode, _ in SYNC_MODE_LABELS:
            self.mi_sync_mode[mode].state = 1 if mode == current_sync else 0

    def _refresh_stats(self):
        try:
            state = engine.load_state()
        except engine.SyncError as e:
            self.mi_last.title = ("⚠ " + str(e))[:80]
            return
        total = len(state.get("synced", {}))
        self.mi_total.title = f"{total} note{'' if total == 1 else 's'} synced so far"
        last_run = state.get("last_run")
        if last_run and last_run.get("finished_at"):
            try:
                dt = datetime.datetime.fromisoformat(last_run["finished_at"])
                when = sl.friendly_timestamp(dt, datetime.datetime.now())
            except ValueError:
                when = last_run["finished_at"]
            text = f"Last sync: {when} — {last_run.get('created', 0)} new"
            refreshed = (last_run.get("updated", 0)
                         + last_run.get("update_copies", 0))
            if refreshed:
                text += f", {refreshed} updated"
            if last_run.get("failed"):
                text += f", {last_run['failed']} failed ⚠"
            self.mi_last.title = text
        elif total:
            # v1 CLI history exists but the CLI predates last_run tracking.
            self.mi_last.title = "Last sync: before this app was installed"
        else:
            self.mi_last.title = "Not synced yet"

    # ------------------------------------------------------------- syncing

    def on_sync_now(self, _sender=None):
        if self._is_syncing():
            return
        if not engine.API_KEY_FILE.exists():
            with self._modal():
                rumps.alert("Add your Granola API key first",
                            "Granola Sync needs your Granola API key "
                            "before it can read your meeting notes.")
            self.on_set_key()
            return
        self._start_sync("manual")

    def _start_sync(self, trigger: str):
        self.mi_sync.set_callback(None)
        self.mi_sync.title = "Syncing…"
        self._sync_thread = threading.Thread(
            target=self._sync_worker, args=(trigger,), daemon=True)
        self._sync_thread.start()

    def _sync_worker(self, trigger: str):
        try:
            stats = engine.run_sync(
                dry_run=False, trigger=trigger,
                progress=lambda msg: self._events.put(("progress", msg)))
            self._events.put(("sync_done", trigger, stats))
        except engine.SyncError as e:
            self._events.put(("sync_fatal", trigger, str(e)))
        except Exception as e:  # bug net: the app must survive anything here
            self._events.put(("sync_fatal", trigger, f"Unexpected error: {e!r}"))

    def _sync_finished(self):
        self._sync_thread = None
        self.mi_sync.title = "Sync Now"
        self.mi_sync.set_callback(self.on_sync_now)

    def _maybe_scheduled_sync(self):
        if self._is_syncing() or not engine.API_KEY_FILE.exists():
            return
        try:
            state = engine.load_state()
        except engine.SyncError:
            return  # surfaced to the user via _refresh_stats
        last_auto = None
        raw = state.get("last_auto_sync")
        if raw:
            try:
                last_auto = datetime.datetime.fromisoformat(raw)
            except ValueError:
                pass
        if sl.is_sync_due(self.config.get("schedule"), last_auto,
                          datetime.datetime.now()):
            # Stamp at fire time (like launchd): a failed run waits for the
            # next scheduled instant rather than retrying every tick.
            state["last_auto_sync"] = datetime.datetime.now().isoformat(
                timespec="seconds")
            engine.save_state(state)
            self._start_sync("scheduled")

    # ------------------------------------------------------------ event pump

    def _tick(self, _timer=None):
        if self._modal_depth:
            return  # a dialog is up; its run loop re-fires timers
        try:
            self._drain_events()
            if not self._did_startup:
                self._did_startup = True
                self._startup_once()
            self._tick_count += 1
            if self._tick_count % SCHEDULE_EVERY_TICKS == 0 and not self._is_syncing():
                self._refresh_stats()
                self._maybe_scheduled_sync()
        except Exception as e:  # a broken tick must never crash the app
            msg = f"App error: {e!r}"
            if msg != self._last_tick_error:
                self._last_tick_error = msg
                engine.log(msg)

    def _drain_events(self):
        while True:
            try:
                event = self._events.get_nowait()
            except queue.Empty:
                return
            kind = event[0]

            if kind == "progress":
                self.mi_last.title = event[1][:70]

            elif kind == "sync_done":
                _, trigger, stats = event
                self._sync_finished()
                self._set_error_badge(bool(stats["failed"]))
                self._refresh_stats()
                if self._first_sync_pending and trigger == "manual":
                    self._first_sync_pending = False
                    with self._modal():
                        rumps.alert(
                            "First sync complete",
                            f"{stats['created']} note(s) created in Apple "
                            "Notes → “Granola” folder (iCloud).\n\nFrom now "
                            "on it syncs on your schedule — see “Sync "
                            "Automatically” in the menu.")

            elif kind == "sync_fatal":
                _, trigger, message = event
                self._sync_finished()
                self._set_error_badge(True)
                self.mi_last.title = "⚠ Sync failed — see View Sync Log"
                engine.log(f"FATAL: {message}")
                if trigger == "manual":
                    with self._modal():
                        rumps.alert("Sync failed", message)

            elif kind == "key_ok":
                _, visible = event
                with self._modal():
                    message = ("Your key works — Granola is reachable "
                               f"({visible} notes visible on the first page).")
                    if self._total_synced() == 0:
                        if rumps.alert(
                                "Connected to Granola",
                                message + "\n\nRun your first sync now? "
                                "macOS will ask for permission to control "
                                "Notes — click “Allow”.",
                                ok="Sync Now", cancel="Later") == 1:
                            self._first_sync_pending = True
                            self.on_sync_now()
                    else:
                        rumps.alert("Connected to Granola", message)
                self._refresh_stats()

            elif kind == "key_bad":
                _, message = event
                with self._modal():
                    rumps.alert(
                        "Key saved, but Granola rejected it",
                        message + "\n\nDouble-check the key in Granola "
                        "(Settings → API) and use “Set API Key…” to "
                        "re-enter it.")
                self._refresh_stats()

    # ------------------------------------------------------------- startup

    def _startup_once(self):
        with self._modal():
            v1_plist = next((p for p in V1_PLISTS if p.exists()), None)
            if v1_plist and not self.config.get("v1_plist_prompted"):
                self.config["v1_plist_prompted"] = True
                engine.save_config(self.config)
                if rumps.alert(
                        "Old daily sync job found",
                        "This Mac still has the v1 “granola-sync” launchd "
                        "job (daily at 8:00 AM). The app now handles "
                        "scheduling itself, so the old job should be turned "
                        "off — otherwise both will run (harmless thanks to "
                        "the duplicate checks, but wasteful).\n\n"
                        "Turn the old job off now?",
                        ok="Turn It Off", cancel="Leave It") == 1:
                    subprocess.run(
                        ["launchctl", "unload", "-w", str(v1_plist)],
                        capture_output=True)
                    engine.log("Disabled v1 LaunchAgent via app migration prompt")
            if not engine.API_KEY_FILE.exists():
                rumps.alert(
                    "Welcome to Granola Sync",
                    "This app copies your Granola meeting notes into Apple "
                    "Notes (folder “Granola” in your iCloud account), once "
                    "per note.\n\nFirst step: add your Granola API key.")
                self.on_set_key()
        self._refresh_stats()

    # ------------------------------------------------------------- settings

    def on_pick_mode(self, sender):
        mode = next(m for m, label in MODE_LABELS if label == sender.title)
        self.config.setdefault("schedule", {})["mode"] = mode
        self._arm_schedule_and_save()
        self._refresh_schedule_ui()

    def on_pick_sync_mode(self, sender):
        mode = next(m for m, label in SYNC_MODE_LABELS if label == sender.title)
        if mode == self.config.get("sync_mode", "one_time"):
            return
        self.config["sync_mode"] = mode
        engine.save_config(self.config)
        self._refresh_schedule_ui()
        if mode == "update_changed":
            with self._modal():
                rumps.alert(
                    "Update syncing is on",
                    "From the next sync onward, notes you edit in Granola "
                    "are re-synced to Apple Notes.\n\n"
                    "Your own edits are safe: a note you've changed in "
                    "Apple Notes is never overwritten — the new Granola "
                    "version simply appears next to it as “… (updated)”. "
                    "Notes you deleted stay deleted.\n\n"
                    "(Earlier Granola edits aren't back-filled; syncing "
                    "starts with edits made from now on.)")

    def on_set_daily_time(self, _sender=None):
        current = self.config.get("schedule", {}).get("time", "08:00")
        with self._modal():
            while True:
                resp = rumps.Window(
                    title="Daily Sync Time",
                    message="When should the daily sync run?\n"
                            "Use 24-hour HH:MM — e.g. 08:00 or 17:30.",
                    default_text=current, ok="Save", cancel="Cancel",
                    dimensions=(120, 24)).run()
                if resp.clicked != 1:
                    return
                text = resp.text.strip()
                if sl.is_valid_daily_time(text):
                    break
                rumps.alert("Not a valid time",
                            f"“{text}” isn't HH:MM (e.g. 08:00 or 17:30).")
        h, m = sl.parse_daily_time(text)
        self.config.setdefault("schedule", {})["time"] = f"{h:02d}:{m:02d}"
        self.config["schedule"]["mode"] = "daily"
        self._arm_schedule_and_save()
        self._refresh_schedule_ui()

    def _arm_schedule_and_save(self):
        """Persist settings and restart the schedule clock from now, so a
        just-picked schedule never counts as already missed."""
        engine.save_config(self.config)
        try:
            state = engine.load_state()
            state["last_auto_sync"] = datetime.datetime.now().isoformat(
                timespec="seconds")
            engine.save_state(state)
        except engine.SyncError:
            pass  # corrupt state is surfaced by the stats line

    def on_toggle_login(self, sender):
        bundle = frozen_app_bundle()
        if bundle is None:
            with self._modal():
                rumps.alert("Only available in the built app",
                            "Launch at Login needs the packaged "
                            "“Granola Sync.app” (run build.sh and use the "
                            "app it produces).")
            return
        try:
            if self.config.get("launch_at_login"):
                engine.osascript(LOGIN_ITEM_REMOVE)
                self.config["launch_at_login"] = False
            else:
                engine.osascript(LOGIN_ITEM_ADD, str(bundle))
                self.config["launch_at_login"] = True
            engine.save_config(self.config)
            sender.state = 1 if self.config["launch_at_login"] else 0
        except (RuntimeError, subprocess.SubprocessError) as e:
            with self._modal():
                rumps.alert(
                    "Couldn't change Launch at Login",
                    "macOS blocked the change. Allow “Granola Sync” to "
                    "control “System Events” under System Settings → "
                    "Privacy & Security → Automation, then try again.\n\n"
                    f"Details: {e}")

    def on_set_key(self, _sender=None):
        with self._modal():
            while True:
                resp = rumps.Window(
                    title="Granola API Key",
                    message="Paste your Granola API key — it starts with "
                            "“grn_”.\n\nTo create one: open Granola → "
                            "Settings → API → Create new key.\n(API keys "
                            "require a Granola Business or Enterprise plan.)",
                    default_text="", ok="Save", cancel="Cancel",
                    dimensions=(340, 24)).run()
                if resp.clicked != 1:
                    return
                key = resp.text.strip()
                try:
                    engine.save_api_key(key)
                    break
                except engine.SyncError as e:
                    rumps.alert("That key doesn't look right", str(e))
        self.mi_last.title = "Checking the key against Granola…"
        threading.Thread(target=self._key_test_worker, args=(key,),
                         daemon=True).start()

    def _key_test_worker(self, key: str):
        try:
            data = engine.api_get("/notes", {}, key)
            visible = len(data.get("notes", [])) if isinstance(data, dict) else 0
            self._events.put(("key_ok", visible))
        except engine.SyncError as e:
            self._events.put(("key_bad", str(e)))
        except Exception as e:
            self._events.put(("key_bad", f"Unexpected error: {e!r}"))

    # --------------------------------------------------------------- misc

    def on_view_log(self, _sender=None):
        try:
            engine.APP_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
            engine.LOG_FILE.touch(exist_ok=True)
            subprocess.run(["open", str(engine.LOG_FILE)], check=False)
        except OSError as e:
            with self._modal():
                rumps.alert("Couldn't open the log", str(e))

    def on_help(self, _sender=None):
        with self._modal():
            rumps.alert("About Granola Sync", HELP_TEXT)


def main():
    GranolaSyncApp().run()


if __name__ == "__main__":
    main()
