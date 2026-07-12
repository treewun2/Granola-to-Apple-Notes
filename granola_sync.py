#!/usr/bin/env python3
"""
granola_sync.py — one-way sync: Granola meeting notes -> Apple Notes (iCloud).

Source:  Granola official public API (https://public-api.granola.ai/v1)
Target:  Apple Notes, folder "Granola" in the iCloud account
Dedupe:  (1) local state file of synced note IDs
         (2) before creating, verify no note in the target folder already
             contains the Granola note ID (embedded in each note's footer)

Design notes:
- One-time sync per note: once synced, a note is never touched again.
  State entries record created_at/updated_at so a future opt-in
  "sync updated notes" / bidirectional mode has a baseline to diff
  against (see config "sync_mode", currently always "one_time").
- stdlib only (urllib, subprocess) — no pip installs required.
- Importable as a library: the menu-bar app (app/granola_sync_app.py)
  calls run_sync(). Fatal problems raise SyncError instead of exiting
  so the app stays alive; the CLI catches SyncError and exits 1.
- Fails loudly on unexpected API responses; run with --inspect to see
  the raw API payload if Granola changes their schema.

Usage:
  python3 granola_sync.py --dry-run    # show what would sync, write nothing
  python3 granola_sync.py              # real sync
  python3 granola_sync.py --inspect    # dump raw JSON of first API page

Setup (one time, CLI use — the menu-bar app does this via its UI):
  mkdir -p ~/.granola-sync
  echo 'grn_YOUR_KEY_HERE' > ~/.granola-sync/api_key
  chmod 600 ~/.granola-sync/api_key
"""

import argparse
import datetime
import hashlib
import html
import json
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

# ----------------------------- configuration -------------------------------

API_BASE = "https://public-api.granola.ai/v1"
# GRANOLA_SYNC_DIR redirects all runtime files (key, state, config, log) —
# used by the scratch-folder test protocol so tests never touch real state.
APP_DIR = Path(os.environ.get("GRANOLA_SYNC_DIR") or (Path.home() / ".granola-sync"))
API_KEY_FILE = APP_DIR / "api_key"
STATE_FILE = APP_DIR / "state.json"
CONFIG_FILE = APP_DIR / "config.json"
LOG_FILE = APP_DIR / "sync.log"

NOTES_ACCOUNT = "iCloud"      # Apple Notes account (must be iCloud to sync)
NOTES_FOLDER = "Granola"      # created automatically if missing

# Set to an integer (e.g. 30) to limit the FIRST run to notes created in the
# last N days. None = sync everything the API returns. Subsequent runs always
# use created_after based on the newest note already synced. The config file
# key "initial_lookback_days" overrides this when set.
INITIAL_LOOKBACK_DAYS = None

HTTP_TIMEOUT = 30             # seconds per request
MAX_RETRIES = 3               # on 429/5xx

ID_MARKER_PREFIX = "granola-id:"     # footer marker embedded in each Apple note
REV_MARKER_PREFIX = "granola-rev:"   # content-version marker (update mode
                                     # targets a note by id + rev together)


class SyncError(RuntimeError):
    """Fatal sync problem (bad key, API unreachable, corrupt state).

    The CLI turns this into a logged FATAL + exit(1); the menu-bar app
    shows it in the UI and keeps running.
    """


# ------------------------------- logging -----------------------------------


def log(msg: str) -> None:
    line = f"{datetime.datetime.now().isoformat(timespec='seconds')}  {msg}"
    try:
        print(line)
    except Exception:
        pass  # frozen windowed app may have no usable stdout
    try:
        APP_DIR.mkdir(mode=0o700, parents=True, exist_ok=True)
        with open(LOG_FILE, "a") as f:
            f.write(line + "\n")
    except OSError:
        pass  # logging must never kill the sync


# ------------------------------ Granola API --------------------------------


def load_api_key(path=API_KEY_FILE) -> str:
    try:
        key = Path(path).read_text().strip()
    except FileNotFoundError:
        raise SyncError(
            f"API key file not found: {path}\n"
            f"  Create it with:  echo 'grn_...' > {path} "
            f"&& chmod 600 {path}")
    if not key.startswith("grn_"):
        raise SyncError(
            f"API key in {path} doesn't look like a Granola key "
            f"(expected it to start with 'grn_').")
    return key


def save_api_key(key: str, path=API_KEY_FILE) -> None:
    """Store the API key with owner-only permissions (used by the app UI)."""
    key = (key or "").strip()
    if not key.startswith("grn_"):
        raise SyncError("That doesn't look like a Granola API key "
                        "(expected it to start with 'grn_').")
    p = Path(path)
    p.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    # Owner-only from the first byte — the mode on os.open applies only
    # when the file is created, so chmod covers pre-existing files too.
    fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as f:
        f.write(key + "\n")
    p.chmod(0o600)


def api_get(path: str, params: dict, api_key: str) -> dict:
    """GET with retry on 429/5xx. Raises SyncError on anything else."""
    qs = urllib.parse.urlencode({k: v for k, v in params.items() if v is not None})
    url = f"{API_BASE}{path}" + (f"?{qs}" if qs else "")
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {api_key}",
        "Accept": "application/json",
        "User-Agent": "granola-sync/2.0 (personal one-way Apple Notes sync)",
    })
    last_err = None
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            # url is always built from the constant https API_BASE
            with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT) as resp:  # nosec B310
                return json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", errors="replace")[:500]
            if e.code in (429, 500, 502, 503, 504) and attempt < MAX_RETRIES:
                wait = 5 * attempt
                log(f"HTTP {e.code} from API (attempt {attempt}), "
                    f"retrying in {wait}s ...")
                time.sleep(wait)
                last_err = f"HTTP {e.code}: {body}"
                continue
            if e.code == 401:
                raise SyncError(
                    "API returned 401 Unauthorized — the key in "
                    f"{API_KEY_FILE} is invalid, expired, or revoked.")
            raise SyncError(f"API error HTTP {e.code} for {url}\n  Response: {body}")
        except (urllib.error.URLError, TimeoutError, ConnectionError) as e:
            if attempt < MAX_RETRIES:
                wait = 5 * attempt
                log(f"Network error ({e}), retrying in {wait}s ...")
                time.sleep(wait)
                last_err = str(e)
                continue
            raise SyncError(f"Network error talking to Granola API: {e}")
    raise SyncError(f"Giving up after {MAX_RETRIES} attempts. Last error: {last_err}")


def fetch_all_notes(api_key: str, created_after, updated_after=None) -> list:
    """Page through /v1/notes. Returns list of note dicts (metadata level).

    Both filters verified against the live API 2026-07-11: created_after
    and updated_after are honored server-side; list entries carry
    created_at and updated_at.
    """
    notes = []
    cursor = None
    page = 0
    while True:
        page += 1
        data = api_get("/notes", {
            "created_after": created_after,
            "updated_after": updated_after,
            "cursor": cursor,
        }, api_key)
        if not isinstance(data, dict) or "notes" not in data:
            raise SyncError(
                "Unexpected API response shape from /notes — expected a dict "
                "with a 'notes' key. Run with --inspect to see the raw "
                f"payload. Got: {list(data) if isinstance(data, dict) else type(data)}")
        batch = data["notes"]
        if not isinstance(batch, list):
            raise SyncError(
                f"'notes' in API response is not a list (got {type(batch)}). "
                "Run with --inspect.")
        notes.extend(batch)
        log(f"Fetched page {page}: {len(batch)} notes "
            f"(total so far: {len(notes)})")
        if data.get("hasMore") and data.get("cursor"):
            cursor = data["cursor"]
        else:
            break
    return notes


def fetch_note_detail(note_id: str, api_key: str) -> dict:
    return api_get(f"/notes/{urllib.parse.quote(note_id)}", {}, api_key)


# --------------------------- content formatting ----------------------------

CONTENT_FIELDS = ("summary_markdown", "summary_text",
                  "notes_markdown", "notes", "content", "summary")


def md_to_html(text: str) -> str:
    """
    Minimal markdown -> HTML for Apple Notes bodies. Handles headings,
    bullet lists, bold/italic, and paragraphs. Everything else passes
    through escaped. Deliberately conservative — Apple Notes' HTML
    support is limited anyway.
    """
    out = []
    in_list = False
    for raw in text.splitlines():
        stripped = raw.strip()
        is_bullet = bool(re.match(r"^[-*+]\s+", stripped))
        if in_list and not is_bullet:
            out.append("</ul>")
            in_list = False
        if not stripped:
            out.append("<div><br></div>")
            continue
        m = re.match(r"^(#{1,6})\s+(.*)$", stripped)
        if m:
            level = min(len(m.group(1)), 3)  # Notes renders h1-h3 well
            out.append(f"<h{level}>{_inline(m.group(2))}</h{level}>")
            continue
        if is_bullet:
            if not in_list:
                out.append("<ul>")
                in_list = True
            out.append(f"<li>{_inline(re.sub(r'^[-*+]\s+', '', stripped))}</li>")
            continue
        out.append(f"<div>{_inline(stripped)}</div>")
    if in_list:
        out.append("</ul>")
    return "".join(out)


def _inline(s: str) -> str:
    s = html.escape(s)
    s = re.sub(r"\*\*(.+?)\*\*", r"<b>\1</b>", s)
    s = re.sub(r"(?<!\*)\*([^*]+)\*(?!\*)", r"<i>\1</i>", s)
    return s


def has_content(note: dict) -> bool:
    return any(isinstance(note.get(f), str) and note.get(f).strip()
               for f in CONTENT_FIELDS)


def note_rev(note: dict) -> str:
    """The content-version stamp for a note (its API updated_at)."""
    return note.get("updated_at") or note.get("created_at") or "0"


def id_marker(note_id: str) -> str:
    return f"{ID_MARKER_PREFIX} {note_id}"


def rev_marker(rev: str) -> str:
    return f"{REV_MARKER_PREFIX} {rev}"


def fingerprint(body: str) -> str:
    """Fingerprint of a note body as READ BACK from Apple Notes (Notes
    normalizes HTML on ingest, so always fingerprint the read-back form,
    never the HTML we wrote)."""
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


def build_note_body(note: dict, updated_copy: bool = False):
    """Returns (display_title, html_body) for an Apple note.

    updated_copy=True builds the "keep both" variant created when a
    Granola note changed but the existing Apple note can't be safely
    replaced (user edited it, or it predates fingerprint tracking).
    """
    note_id = note.get("id", "unknown-id")
    title = (note.get("title") or "Untitled Granola note").strip()
    created = note.get("created_at") or note.get("createdAt") or ""
    date_str = created[:10] if created else "undated"
    display_title = f"{title} — {date_str}"
    if updated_copy:
        display_title += f" (updated {datetime.date.today().isoformat()})"

    content = None
    for field in CONTENT_FIELDS:
        val = note.get(field)
        if isinstance(val, str) and val.strip():
            content = val
            break
    if content is None:
        raise ValueError(
            f"Note {note_id} ('{title}') has no usable content field. "
            f"Available keys: {sorted(note.keys())}. "
            "Run with --inspect and adjust CONTENT_FIELDS.")

    body = (
        f"<h1>{html.escape(display_title)}</h1>"
        + md_to_html(content)
        + "<div><br></div>"
        + f"<div><i>{id_marker(html.escape(note_id))}"
        + f" · {rev_marker(html.escape(note_rev(note)))}"
        + f" · synced from Granola {datetime.date.today().isoformat()}</i></div>"
    )
    return display_title, body


def select_update_candidates(notes: list, synced: dict, watermark: str) -> list:
    """Which API notes (metadata level) need an update pass?

    A candidate is already synced, has an updated_at newer than both the
    global watermark and the content revision we last wrote for it.
    ISO-8601 Z strings compare correctly as plain strings.
    """
    out = []
    for n in notes:
        nid = n.get("id")
        up = n.get("updated_at") or ""
        if not nid or nid not in synced or not up:
            continue
        baseline = synced[nid].get("updated_at") or ""
        if up > baseline and (not watermark or up > watermark):
            out.append(n)
    return sorted(out, key=lambda n: n.get("updated_at", ""))


def plan_update_action(entry: dict, lookup_status: str, current_body) -> str:
    """Decide how to apply a Granola update to an already-synced note.

    entry:         the state entry for the note (may lack body_fp if the
                   note was synced before fingerprint tracking existed)
    lookup_status: "ok" | "none" | "multi" — result of finding the Apple
                   note by its id+rev markers
    current_body:  the note body as read back from Apple Notes (when "ok")

    Returns one of:
      "in_place"       safe to replace the body (fingerprint matches)
      "copy"           keep both: leave the note alone, add updated copy
      "skip_deleted"   user deleted the synced note — respect that
      "skip_ambiguous" multiple marker matches — touch nothing

    Every uncertain case degrades toward "copy" or a skip — never toward
    overwriting something we can't prove the tool wrote.
    """
    if not entry.get("body_fp"):
        # Synced before fingerprints existed (v1 CLI / app 2.0.0): we
        # can't prove the Apple copy is untouched, so never modify it.
        return "copy"
    if lookup_status == "none":
        return "skip_deleted"
    if lookup_status == "multi":
        return "skip_ambiguous"
    if current_body is not None and fingerprint(current_body) == entry["body_fp"]:
        return "in_place"
    return "copy"


# ------------------------------ Apple Notes --------------------------------


def osascript(script: str, *args: str) -> str:
    """Run AppleScript with arguments passed safely via argv."""
    cmd = ["osascript", "-e", script] + list(args)
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
    if result.returncode != 0:
        raise RuntimeError(f"AppleScript failed: {result.stderr.strip()}")
    return result.stdout.strip()


ENSURE_FOLDER_SCRIPT = '''
on run argv
    set acctName to item 1 of argv
    set folderName to item 2 of argv
    tell application "Notes"
        tell account acctName
            if not (exists folder folderName) then
                make new folder with properties {name:folderName}
            end if
        end tell
    end tell
    return "ok"
end run
'''

NOTE_EXISTS_SCRIPT = '''
on run argv
    set acctName to item 1 of argv
    set folderName to item 2 of argv
    set marker to item 3 of argv
    tell application "Notes"
        tell folder folderName of account acctName
            set matches to (count of (notes whose body contains marker))
        end tell
    end tell
    return matches as string
end run
'''

CREATE_NOTE_SCRIPT = '''
on run argv
    set acctName to item 1 of argv
    set folderName to item 2 of argv
    set noteBody to item 3 of argv
    tell application "Notes"
        tell folder folderName of account acctName
            make new note with properties {body:noteBody}
        end tell
    end tell
    return "ok"
end run
'''

# Update-mode scripts target a note by BOTH markers (id + rev) so an
# older "(updated ...)" copy of the same Granola note is never matched.
# They answer with a status line first; "@@" sentinels can't collide with
# real bodies because we check the first line only.
GET_NOTE_BODY_SCRIPT = '''
on run argv
    set acctName to item 1 of argv
    set folderName to item 2 of argv
    set markerA to item 3 of argv
    set markerB to item 4 of argv
    tell application "Notes"
        tell folder folderName of account acctName
            set matches to (notes whose body contains markerA and body contains markerB)
            if (count of matches) is 0 then return "@@NONE@@"
            if (count of matches) > 1 then return "@@MULTI@@"
            return "@@OK@@" & linefeed & (body of item 1 of matches)
        end tell
    end tell
end run
'''

SET_NOTE_BODY_SCRIPT = '''
on run argv
    set acctName to item 1 of argv
    set folderName to item 2 of argv
    set markerA to item 3 of argv
    set markerB to item 4 of argv
    set newBody to item 5 of argv
    tell application "Notes"
        tell folder folderName of account acctName
            set matches to (notes whose body contains markerA and body contains markerB)
            if (count of matches) is 0 then return "@@NONE@@"
            if (count of matches) > 1 then return "@@MULTI@@"
            set body of item 1 of matches to newBody
            return "@@OK@@"
        end tell
    end tell
end run
'''


def ensure_folder(folder=NOTES_FOLDER) -> None:
    osascript(ENSURE_FOLDER_SCRIPT, NOTES_ACCOUNT, folder)


def note_exists_in_apple_notes(marker: str, folder=NOTES_FOLDER) -> bool:
    count = osascript(NOTE_EXISTS_SCRIPT, NOTES_ACCOUNT, folder, marker)
    return int(count) > 0


def create_apple_note(body_html: str, folder=NOTES_FOLDER) -> None:
    osascript(CREATE_NOTE_SCRIPT, NOTES_ACCOUNT, folder, body_html)


def get_note_body(note_id: str, rev: str, folder=NOTES_FOLDER):
    """Find the tool-written note for (id, rev). Returns (status, body)
    where status is "ok" | "none" | "multi"."""
    out = osascript(GET_NOTE_BODY_SCRIPT, NOTES_ACCOUNT, folder,
                    id_marker(note_id), rev_marker(rev))
    first, _, rest = out.partition("\n")
    if first == "@@OK@@":
        return "ok", rest
    if first == "@@NONE@@":
        return "none", None
    return "multi", None


def set_note_body(note_id: str, rev: str, new_body: str,
                  folder=NOTES_FOLDER) -> str:
    """Replace the body of the tool-written note for (id, rev).
    Returns "ok" | "none" | "multi"."""
    out = osascript(SET_NOTE_BODY_SCRIPT, NOTES_ACCOUNT, folder,
                    id_marker(note_id), rev_marker(rev), new_body)
    return {"@@OK@@": "ok", "@@NONE@@": "none"}.get(out, "multi")


# -------------------------------- state ------------------------------------


def load_state(path=STATE_FILE) -> dict:
    path = Path(path)
    if path.exists():
        try:
            state = json.loads(path.read_text())
        except json.JSONDecodeError:
            raise SyncError(
                f"State file {path} is corrupt JSON. Fix or delete it "
                "(deleting will re-check every note against Apple Notes — "
                "safe, just slow).")
        state.setdefault("synced", {})
        return state
    return {"synced": {}}


def save_state(state: dict, path=STATE_FILE) -> None:
    path = Path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, indent=2))
    tmp.replace(path)


# -------------------------------- config -----------------------------------
# Settings written by the menu-bar app. The v1 CLI + launchd flow works
# without this file existing (all defaults preserve v1 behavior).

DEFAULT_CONFIG = {
    "version": 1,
    # Automatic sync schedule, enforced by the menu-bar app's internal
    # timer (the CLI relies on launchd instead and ignores this).
    # modes: "off" | "hourly" | "every_4_hours" | "daily"
    "schedule": {"mode": "daily", "time": "08:00"},
    # Cached user intent for the app's Launch at Login toggle (the source
    # of truth is the macOS login items list).
    "launch_at_login": False,
    # "one_time" (default): created notes are never touched again.
    # "update_changed": notes edited in Granola are re-synced — replaced
    #   in place when the Apple copy is provably untouched (fingerprint
    #   match), otherwise a "(updated ...)" copy is added beside it.
    # "bidirectional" is NOT implemented: Granola's API is read-only
    #   (verified 2026-07-11) — there is no way to write back.
    "sync_mode": "one_time",
    # Optional: first-ever sync only looks back N days (null = everything).
    "initial_lookback_days": None,
    # Apple Notes target folder. Only override for the scratch-folder
    # test protocol (HANDOFF.md); end users always get "Granola".
    "notes_folder": NOTES_FOLDER,
}


def load_config(path=CONFIG_FILE) -> dict:
    """Config merged over defaults. Unknown keys are preserved; a corrupt
    file falls back to defaults (never fatal — settings are recoverable)."""
    cfg = json.loads(json.dumps(DEFAULT_CONFIG))  # deep copy
    path = Path(path)
    if path.exists():
        try:
            data = json.loads(path.read_text())
        except json.JSONDecodeError:
            log(f"WARNING: {path} is corrupt JSON; using default settings")
            return cfg
        if isinstance(data, dict):
            for k, v in data.items():
                if k == "schedule" and isinstance(v, dict):
                    cfg["schedule"].update(v)
                else:
                    cfg[k] = v
    return cfg


def save_config(cfg: dict, path=CONFIG_FILE) -> None:
    path = Path(path)
    path.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cfg, indent=2))
    tmp.replace(path)


# ------------------------------- sync pass ---------------------------------


def run_sync(dry_run: bool = False, progress=None, trigger: str = "cli") -> dict:
    """Run one sync pass. Returns a stats dict (also persisted as
    state["last_run"] for real runs so the app can show "last sync" info).

    Pass 1 (always): create Apple notes for new Granola notes — identical
    to v1 behavior.
    Pass 2 (only when config sync_mode == "update_changed"): re-sync notes
    edited in Granola since the last pass. In-place replacement happens
    ONLY when the Apple note's body fingerprint proves the tool's copy is
    untouched; otherwise a "(updated ...)" copy is added beside it. Notes
    deleted from Apple Notes are respected (never re-created); nothing is
    ever deleted in either direction.

    Raises SyncError on fatal problems (missing/bad key, API unreachable,
    corrupt state). Per-note failures don't abort the pass — they're
    counted in stats (the CLI exits 2 if any occurred, as in v1).

    progress: optional callable receiving short human-readable status
    strings (drives the menu-bar app's status line).
    trigger:  recorded in last_run — "cli", "manual", or "scheduled".
    """
    def report(msg: str) -> None:
        if progress is not None:
            try:
                progress(msg)
            except Exception:
                pass  # a broken progress callback must never kill a sync

    api_key = load_api_key()
    config = load_config()
    state = load_state()
    synced_ids = set(state["synced"].keys())

    folder = config.get("notes_folder") or NOTES_FOLDER
    sync_mode = config.get("sync_mode") or "one_time"
    if sync_mode not in ("one_time", "update_changed"):
        log(f"WARNING: unknown sync_mode '{sync_mode}' — treating as one_time")
        sync_mode = "one_time"

    folder_ready = False

    def ready_folder():
        nonlocal folder_ready
        if not folder_ready:
            ensure_folder(folder)
            folder_ready = True

    def read_back_fingerprint(note_id: str, rev: str) -> str:
        """Fingerprint the note as Apple Notes stored it. Best-effort: an
        empty fingerprint just means later updates use the safe copy path."""
        try:
            status, body = get_note_body(note_id, rev, folder)
            if status == "ok":
                return fingerprint(body)
        except Exception as e:
            log(f"WARNING: couldn't fingerprint note {note_id}: {e}")
        return ""

    # ---- pass 1: new notes (v1 semantics, unchanged) ----

    # Incremental fetch: only ask the API for notes newer than what we have.
    created_after = None
    if synced_ids:
        newest = max(v.get("created_at", "") for v in state["synced"].values())
        if newest:
            created_after = newest
    else:
        lookback = config.get("initial_lookback_days")
        if lookback is None:
            lookback = INITIAL_LOOKBACK_DAYS
        if lookback is not None:
            cutoff = (datetime.datetime.now(datetime.timezone.utc)
                      - datetime.timedelta(days=int(lookback)))
            created_after = cutoff.strftime("%Y-%m-%dT%H:%M:%SZ")

    log(f"Starting sync (dry_run={dry_run}, mode={sync_mode}, "
        f"created_after={created_after or 'beginning of time'})")
    report("Checking Granola for new notes…")

    notes = fetch_all_notes(api_key, created_after)
    new_notes = [n for n in notes if n.get("id") and n["id"] not in synced_ids]
    log(f"{len(notes)} notes from API; {len(new_notes)} not yet synced")

    created = skipped = failed = updated = update_copies = 0
    if not new_notes:
        log("Nothing new to create.")
    else:
        if not dry_run:
            ready_folder()
        total = len(new_notes)
        for idx, note in enumerate(
                sorted(new_notes, key=lambda n: n.get("created_at", "")), 1):
            note_id = note["id"]
            try:
                # Some list responses are metadata-only; fetch detail if needed.
                if not has_content(note):
                    time.sleep(0.3)  # be polite; rapid-fire fetches get dropped
                    note = fetch_note_detail(note_id, api_key)

                title, body = build_note_body(note)
                report(f"Syncing {idx} of {total}: {title[:40]}")

                if dry_run:
                    log(f"DRY-RUN would create: '{title}'  [{note_id}]")
                    created += 1
                    continue

                # Duplicate check against Apple Notes itself (belt-and-suspenders
                # on top of the state file, per design).
                body_fp = ""
                if note_exists_in_apple_notes(id_marker(note_id), folder):
                    log(f"SKIP (already in Apple Notes): '{title}' [{note_id}] "
                        "— recording in state file")
                    skipped += 1
                else:
                    create_apple_note(body, folder)
                    log(f"Created: '{title}'  [{note_id}]")
                    created += 1
                    body_fp = read_back_fingerprint(note_id, note_rev(note))

                state["synced"][note_id] = {
                    "title": title,
                    "created_at": note.get("created_at", ""),
                    # Content revision this entry's Apple note holds; the
                    # update pass compares the API's updated_at against it.
                    "updated_at": note.get("updated_at", ""),
                    # Fingerprint of the body as stored by Apple Notes;
                    # proves "untouched" before any in-place replacement.
                    "body_fp": body_fp,
                    "synced_at": datetime.datetime.now().isoformat(timespec="seconds"),
                }
                save_state(state)  # save after each note: crash-safe
            except SyncError:
                raise  # fatal (auth/API/network) — same abort semantics as v1
            except Exception as e:
                failed += 1
                log(f"ERROR on note {note_id}: {e}")

    # ---- pass 2: updated notes (only in update_changed mode) ----

    if sync_mode == "update_changed":
        watermark = state.get("update_watermark") or ""
        if not watermark:
            # First run in update mode: arm the watermark from the newest
            # server-side updated_at we know of (server clock, no skew),
            # updating nothing. Changes AFTER this point will sync — the
            # agreed "no mass rewrite on enable" behavior.
            seen = ([v.get("updated_at") or "" for v in state["synced"].values()]
                    + [n.get("updated_at") or "" for n in notes])
            seen = [s for s in seen if s]
            watermark = max(seen) if seen else (
                datetime.datetime.now(datetime.timezone.utc)
                .strftime("%Y-%m-%dT%H:%M:%S.000Z"))
            if dry_run:
                log("DRY-RUN: update tracking not armed yet — the first real "
                    "run starts tracking, then later edits sync")
            else:
                state["update_watermark"] = watermark
                save_state(state)
                log(f"Update tracking armed — Granola edits after "
                    f"{watermark} will sync from now on")
        else:
            report("Checking Granola for updated notes…")
            changed = fetch_all_notes(api_key, None, updated_after=watermark)
            candidates = select_update_candidates(changed, state["synced"],
                                                  watermark)
            log(f"{len(changed)} notes changed since last check; "
                f"{len(candidates)} synced notes need updating")
            hi_water = max([watermark]
                           + [n.get("updated_at") or "" for n in changed])
            for idx, meta in enumerate(candidates, 1):
                note_id = meta["id"]
                entry = state["synced"][note_id]
                try:
                    time.sleep(0.3)
                    note = fetch_note_detail(note_id, api_key)
                    new_rev = note_rev(note)
                    title, body = build_note_body(note)
                    report(f"Updating {idx} of {len(candidates)}: {title[:40]}")

                    if dry_run:
                        log(f"DRY-RUN would update: '{title}' [{note_id}] "
                            "(in place if untouched, else adds a copy)")
                        updated += 1
                        continue

                    ready_folder()
                    old_rev = entry.get("updated_at") or ""
                    status, cur_body = "none", None
                    if entry.get("body_fp") and old_rev:
                        status, cur_body = get_note_body(note_id, old_rev, folder)
                    action = plan_update_action(entry, status, cur_body)

                    if action == "in_place":
                        result = set_note_body(note_id, old_rev, body, folder)
                        if result == "ok":
                            log(f"Updated in place: '{title}'  [{note_id}]")
                            updated += 1
                            entry["body_fp"] = read_back_fingerprint(note_id,
                                                                     new_rev)
                        else:  # note vanished between read and write
                            action = ("skip_deleted" if result == "none"
                                      else "skip_ambiguous")

                    if action == "copy":
                        if not note_exists_in_apple_notes(id_marker(note_id),
                                                          folder):
                            # Every copy of this note was deleted by the
                            # user — respect that; don't resurrect it.
                            log(f"SKIP update (deleted in Apple Notes): "
                                f"'{title}' [{note_id}]")
                        else:
                            probe, _ = get_note_body(note_id, new_rev, folder)
                            if probe == "none":  # not already created (crash-safe)
                                copy_title, copy_body = build_note_body(
                                    note, updated_copy=True)
                                create_apple_note(copy_body, folder)
                                log(f"Added updated copy: '{copy_title}'  "
                                    f"[{note_id}]")
                            else:
                                log(f"Updated copy already present: '{title}' "
                                    f"[{note_id}]")
                            update_copies += 1
                            # The fresh copy becomes the tracked version.
                            entry["body_fp"] = read_back_fingerprint(note_id,
                                                                     new_rev)

                    if action == "skip_deleted":
                        # Distinguish "user deleted it" from "a copy exists
                        # at another revision" (another Mac synced the same
                        # account and already applied this update).
                        if note_exists_in_apple_notes(id_marker(note_id),
                                                      folder):
                            log(f"SKIP update ('{title}' [{note_id}]) — note "
                                "is at a different revision than this Mac's "
                                "records (updated elsewhere?); leaving it")
                        else:
                            log(f"SKIP update (deleted in Apple Notes): "
                                f"'{title}' [{note_id}]")
                    elif action == "skip_ambiguous":
                        log(f"WARNING: multiple Apple notes match '{title}' "
                            f"[{note_id}] — leaving all of them untouched")

                    # Always advance this note's baseline so one decision
                    # isn't re-litigated every sync.
                    entry["title"] = title
                    entry["updated_at"] = new_rev
                    entry["synced_at"] = datetime.datetime.now().isoformat(
                        timespec="seconds")
                    save_state(state)
                except SyncError:
                    raise
                except Exception as e:
                    failed += 1
                    log(f"ERROR updating note {note_id}: {e}")
            if not dry_run and hi_water > watermark:
                state["update_watermark"] = hi_water
                save_state(state)

    stats = {
        "trigger": trigger,
        "dry_run": dry_run,
        "finished_at": datetime.datetime.now().isoformat(timespec="seconds"),
        "fetched": len(notes),
        "created": created,
        "skipped": skipped,
        "failed": failed,
        "updated": updated,
        "update_copies": update_copies,
    }
    if not dry_run:
        state["last_run"] = stats
        save_state(state)
    log(f"Done. created={created} skipped={skipped} failed={failed} "
        f"updated={updated} copies={update_copies}")
    return stats


# --------------------------------- main ------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(description="Granola -> Apple Notes one-way sync")
    parser.add_argument("--dry-run", action="store_true",
                        help="show what would sync; write nothing")
    parser.add_argument("--inspect", action="store_true",
                        help="dump raw JSON of the first API page and exit")
    args = parser.parse_args()

    try:
        if args.inspect:
            api_key = load_api_key()
            data = api_get("/notes", {}, api_key)
            print(json.dumps(data, indent=2))
            if isinstance(data, dict) and data.get("notes"):
                first = data["notes"][0]
                print("\n--- detail of first note ---")
                detail = fetch_note_detail(first["id"], api_key)
                print(json.dumps(detail, indent=2))
            return
        stats = run_sync(dry_run=args.dry_run)
    except SyncError as e:
        log(f"FATAL: {e}")
        sys.exit(1)

    if stats["failed"]:
        sys.exit(2)


if __name__ == "__main__":
    main()
