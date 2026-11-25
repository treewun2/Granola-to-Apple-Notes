"""Unit tests for the pure logic in granola_sync.py.

Runs anywhere (no Mac, no network, no API key needed):
    python3 -m unittest discover -s tests -v
"""

import io
import os
import re
import ssl
import sys
import tempfile
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))  # repo root

import granola_sync as gs  # noqa: E402


class MdToHtmlTests(unittest.TestCase):
    def test_heading_levels(self):
        self.assertEqual(gs.md_to_html("# Title"), "<h1>Title</h1>")
        self.assertEqual(gs.md_to_html("## Sub"), "<h2>Sub</h2>")
        self.assertEqual(gs.md_to_html("### Deep"), "<h3>Deep</h3>")

    def test_headings_deeper_than_h3_are_capped(self):
        self.assertEqual(gs.md_to_html("##### tiny"), "<h3>tiny</h3>")

    def test_bullets_grouped_into_one_list(self):
        self.assertEqual(
            gs.md_to_html("- a\n- b\nafter"),
            "<ul><li>a</li><li>b</li></ul><div>after</div>")

    def test_all_bullet_markers_accepted(self):
        for marker in ("-", "*", "+"):
            self.assertEqual(gs.md_to_html(f"{marker} x"),
                             "<ul><li>x</li></ul>", marker)

    def test_list_still_closed_at_end_of_text(self):
        self.assertTrue(gs.md_to_html("text\n- a").endswith("</ul>"))

    def test_bold_italic_and_escaping(self):
        self.assertEqual(gs.md_to_html("**bold** & *it*"),
                         "<div><b>bold</b> &amp; <i>it</i></div>")

    def test_html_in_source_is_escaped(self):
        out = gs.md_to_html("<script>alert(1)</script>")
        self.assertNotIn("<script>", out)
        self.assertIn("&lt;script&gt;", out)

    def test_blank_lines_become_breaks(self):
        self.assertEqual(gs.md_to_html("a\n\nb"),
                         "<div>a</div><div><br></div><div>b</div>")


class BuildNoteBodyTests(unittest.TestCase):
    def note(self, **over):
        base = {
            "id": "not_abc123",
            "title": "Standup",
            "created_at": "2026-07-10T09:00:00Z",
            "summary_markdown": "# Recap\n- point one",
        }
        base.update(over)
        return base

    def test_title_includes_date(self):
        title, _ = gs.build_note_body(self.note())
        self.assertEqual(title, "Standup — 2026-07-10")

    def test_body_starts_with_h1_title(self):
        title, body = gs.build_note_body(self.note())
        self.assertTrue(body.startswith("<h1>Standup — 2026-07-10</h1>"))

    def test_body_carries_dedupe_marker(self):
        _, body = gs.build_note_body(self.note())
        self.assertIn(f"{gs.ID_MARKER_PREFIX} not_abc123", body)

    def test_title_html_escaped_in_body(self):
        _, body = gs.build_note_body(self.note(title="A & B"))
        self.assertIn("<h1>A &amp; B — 2026-07-10</h1>", body)

    def test_missing_created_at_is_undated(self):
        title, _ = gs.build_note_body(self.note(created_at=None))
        self.assertEqual(title, "Standup — undated")

    def test_null_title_gets_placeholder(self):
        title, _ = gs.build_note_body(self.note(title=None))
        self.assertTrue(title.startswith("Untitled Granola note"))

    def test_summary_markdown_preferred_over_summary_text(self):
        _, body = gs.build_note_body(self.note(
            summary_markdown="from markdown", summary_text="from text"))
        self.assertIn("from markdown", body)
        self.assertNotIn("from text", body)

    def test_no_content_raises_with_note_id(self):
        with self.assertRaises(ValueError) as ctx:
            gs.build_note_body({"id": "not_x", "title": "Empty"})
        self.assertIn("not_x", str(ctx.exception))


class UpdatedCopyTests(unittest.TestCase):
    def note(self):
        return {
            "id": "not_abc123",
            "title": "Standup",
            "created_at": "2026-07-10T09:00:00Z",
            "updated_at": "2026-07-11T10:00:00Z",
            "summary_markdown": "new content",
        }

    def test_body_carries_rev_marker(self):
        _, body = gs.build_note_body(self.note())
        self.assertIn(f"{gs.REV_MARKER_PREFIX} 2026-07-11T10:00:00Z", body)

    def test_updated_copy_title_is_suffixed(self):
        title, body = gs.build_note_body(self.note(), updated_copy=True)
        self.assertTrue(title.startswith("Standup — 2026-07-10 (updated "))
        self.assertIn(f"{gs.ID_MARKER_PREFIX} not_abc123", body)

    def test_note_rev_falls_back_to_created_at(self):
        self.assertEqual(gs.note_rev({"created_at": "2026-01-01"}), "2026-01-01")
        self.assertEqual(gs.note_rev({}), "0")

    def test_fingerprint_stable_and_content_sensitive(self):
        self.assertEqual(gs.fingerprint("abc"), gs.fingerprint("abc"))
        self.assertNotEqual(gs.fingerprint("abc"), gs.fingerprint("abd"))


class SelectUpdateCandidatesTests(unittest.TestCase):
    SYNCED = {"not_a": {"updated_at": "2026-07-10T00:00:00Z"},
              "not_b": {"updated_at": "2026-07-11T00:00:00Z"}}

    def test_changed_synced_note_is_candidate(self):
        notes = [{"id": "not_a", "updated_at": "2026-07-12T00:00:00Z"}]
        got = gs.select_update_candidates(notes, self.SYNCED,
                                          "2026-07-11T00:00:00Z")
        self.assertEqual([n["id"] for n in got], ["not_a"])

    def test_unsynced_note_ignored(self):
        notes = [{"id": "not_new", "updated_at": "2026-07-12T00:00:00Z"}]
        self.assertEqual(gs.select_update_candidates(
            notes, self.SYNCED, ""), [])

    def test_not_newer_than_own_baseline_ignored(self):
        notes = [{"id": "not_b", "updated_at": "2026-07-11T00:00:00Z"}]
        self.assertEqual(gs.select_update_candidates(
            notes, self.SYNCED, "2026-07-01T00:00:00Z"), [])

    def test_not_newer_than_watermark_ignored(self):
        notes = [{"id": "not_a", "updated_at": "2026-07-12T00:00:00Z"}]
        self.assertEqual(gs.select_update_candidates(
            notes, self.SYNCED, "2026-07-12T00:00:00Z"), [])

    def test_sorted_oldest_change_first(self):
        notes = [{"id": "not_b", "updated_at": "2026-07-14T00:00:00Z"},
                 {"id": "not_a", "updated_at": "2026-07-13T00:00:00Z"}]
        got = gs.select_update_candidates(notes, self.SYNCED, "")
        self.assertEqual([n["id"] for n in got], ["not_a", "not_b"])


class PlanUpdateActionTests(unittest.TestCase):
    """The safety table: nothing uncertain may ever resolve to in_place."""

    def entry(self, body):
        return {"body_fp": gs.fingerprint(body), "updated_at": "2026-07-10"}

    def test_untouched_note_updates_in_place(self):
        self.assertEqual(
            gs.plan_update_action(self.entry("stored"), "ok", "stored"),
            "in_place")

    def test_user_edited_note_gets_copy(self):
        self.assertEqual(
            gs.plan_update_action(self.entry("stored"), "ok", "edited!"),
            "copy")

    def test_pre_fingerprint_note_gets_copy(self):
        for old in ({}, {"body_fp": ""}):  # v1 and app-2.0.0 state entries
            self.assertEqual(
                gs.plan_update_action(old, "ok", "anything"), "copy", old)

    def test_deleted_note_is_respected(self):
        self.assertEqual(
            gs.plan_update_action(self.entry("stored"), "none", None),
            "skip_deleted")

    def test_ambiguous_match_touches_nothing(self):
        self.assertEqual(
            gs.plan_update_action(self.entry("stored"), "multi", None),
            "skip_ambiguous")

    def test_missing_body_degrades_to_copy(self):
        self.assertEqual(
            gs.plan_update_action(self.entry("stored"), "ok", None), "copy")


class HasContentTests(unittest.TestCase):
    def test_real_content(self):
        self.assertTrue(gs.has_content({"summary_markdown": "hello"}))

    def test_whitespace_only_is_not_content(self):
        self.assertFalse(gs.has_content({"summary_text": "   "}))

    def test_non_string_is_not_content(self):
        self.assertFalse(gs.has_content({"summary_markdown": 5}))

    def test_empty_note(self):
        self.assertFalse(gs.has_content({}))


class StateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "state.json"
        self.addCleanup(self.tmp.cleanup)

    def test_missing_file_gives_empty_state(self):
        self.assertEqual(gs.load_state(self.path), {"synced": {}})

    def test_round_trip(self):
        state = {"synced": {"not_a": {"title": "T"}}, "last_run": {"created": 1}}
        gs.save_state(state, self.path)
        self.assertEqual(gs.load_state(self.path), state)

    def test_no_tmp_file_left_behind(self):
        gs.save_state({"synced": {}}, self.path)
        self.assertEqual(list(Path(self.tmp.name).iterdir()), [self.path])

    def test_corrupt_state_raises_syncerror(self):
        self.path.write_text("{ not json")
        with self.assertRaises(gs.SyncError):
            gs.load_state(self.path)

    def test_synced_key_backfilled(self):
        self.path.write_text('{"last_auto_sync": "2026-07-11T08:00:00"}')
        self.assertEqual(gs.load_state(self.path)["synced"], {})


class ConfigTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "config.json"
        self.addCleanup(self.tmp.cleanup)

    def test_missing_file_gives_defaults(self):
        cfg = gs.load_config(self.path)
        self.assertEqual(cfg, gs.DEFAULT_CONFIG)
        self.assertIsNot(cfg["schedule"], gs.DEFAULT_CONFIG["schedule"])

    def test_defaults_match_v1_behavior(self):
        cfg = gs.load_config(self.path)
        self.assertEqual(cfg["schedule"], {"mode": "daily", "time": "08:00"})
        self.assertEqual(cfg["sync_mode"], "one_time")
        self.assertIsNone(cfg["initial_lookback_days"])

    def test_partial_schedule_merges_over_defaults(self):
        self.path.write_text('{"schedule": {"mode": "hourly"}}')
        cfg = gs.load_config(self.path)
        self.assertEqual(cfg["schedule"]["mode"], "hourly")
        self.assertEqual(cfg["schedule"]["time"], "08:00")

    def test_unknown_keys_preserved(self):
        self.path.write_text('{"custom_flag": 42}')
        self.assertEqual(gs.load_config(self.path)["custom_flag"], 42)

    def test_corrupt_config_falls_back_to_defaults(self):
        self.path.write_text("not json at all")
        self.assertEqual(gs.load_config(self.path), gs.DEFAULT_CONFIG)

    def test_round_trip(self):
        cfg = gs.load_config(self.path)
        cfg["schedule"]["mode"] = "every_4_hours"
        gs.save_config(cfg, self.path)
        self.assertEqual(gs.load_config(self.path)["schedule"]["mode"],
                         "every_4_hours")


class ApiKeyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.path = Path(self.tmp.name) / "api_key"
        self.addCleanup(self.tmp.cleanup)

    def test_save_and_load_round_trip(self):
        gs.save_api_key("  grn_secret123  ", self.path)
        self.assertEqual(gs.load_api_key(self.path), "grn_secret123")

    def test_saved_key_is_owner_only(self):
        gs.save_api_key("grn_secret123", self.path)
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_resaving_over_loose_permissions_tightens_them(self):
        self.path.write_text("grn_old\n")
        self.path.chmod(0o644)
        gs.save_api_key("grn_new", self.path)
        self.assertEqual(self.path.stat().st_mode & 0o777, 0o600)

    def test_data_dir_created_by_key_save_is_private(self):
        nested = Path(self.tmp.name) / "fresh-dir" / "api_key"
        gs.save_api_key("grn_secret123", nested)
        self.assertEqual(nested.parent.stat().st_mode & 0o777, 0o700)

    def test_data_dir_created_by_state_save_is_private(self):
        nested = Path(self.tmp.name) / "fresh-dir" / "state.json"
        gs.save_state({"synced": {}}, nested)
        self.assertEqual(nested.parent.stat().st_mode & 0o777, 0o700)

    def test_save_rejects_non_granola_key(self):
        with self.assertRaises(gs.SyncError):
            gs.save_api_key("sk-something-else", self.path)

    def test_load_missing_file_raises(self):
        with self.assertRaises(gs.SyncError) as ctx:
            gs.load_api_key(self.path)
        self.assertIn(str(self.path), str(ctx.exception))

    def test_load_rejects_malformed_key(self):
        self.path.write_text("hello\n")
        with self.assertRaises(gs.SyncError):
            gs.load_api_key(self.path)


def _system_ca_certs():
    """Real CA certificates (PEM blocks) from whatever bundle this host has."""
    candidates = [ssl.get_default_verify_paths().cafile,
                  "/etc/ssl/cert.pem",
                  "/etc/ssl/certs/ca-certificates.crt"]
    for path in candidates:
        if path and os.path.isfile(path):
            text = Path(path).read_text()
            return re.findall(r"-----BEGIN CERTIFICATE-----.+?"
                              r"-----END CERTIFICATE-----", text, re.S)
    return []


# A Mac without Homebrew: the bundled OpenSSL's default CA paths are absent.
NO_DEFAULT_CAS = {"SSL_CERT_FILE": "/nonexistent/cert.pem",
                  "SSL_CERT_DIR": "/nonexistent/certs"}


class TlsContextTests(unittest.TestCase):
    def setUp(self):
        with mock.patch.dict(os.environ, NO_DEFAULT_CAS):
            simulated = ssl.create_default_context().cert_store_stats()
        if simulated["x509_ca"]:
            # LibreSSL (Apple's /usr/bin/python3) ignores SSL_CERT_FILE and
            # always loads Apple's bundle, so it can't have this bug.
            self.skipTest(f"{ssl.OPENSSL_VERSION} ignores SSL_CERT_FILE")
        certs = _system_ca_certs()
        if len(certs) < 2:
            self.skipTest("no system CA bundle on this host")
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.ca_a = Path(self.tmp.name) / "a.pem"
        self.ca_b = Path(self.tmp.name) / "b.pem"
        self.ca_a.write_text(certs[0] + "\n")
        self.ca_b.write_text(certs[1] + "\n")

    def test_falls_back_when_default_store_is_empty(self):
        with mock.patch.dict(os.environ, NO_DEFAULT_CAS):
            ctx = gs.make_ssl_context(str(self.ca_a))
        self.assertEqual(ctx.cert_store_stats()["x509_ca"], 1)

    def test_default_store_wins_when_it_has_certs(self):
        with mock.patch.dict(os.environ, {"SSL_CERT_FILE": str(self.ca_a)}):
            ctx = gs.make_ssl_context(str(self.ca_b))
        self.assertEqual(ctx.cert_store_stats()["x509_ca"], 1)

    def test_missing_fallback_never_relaxes_verification(self):
        with mock.patch.dict(os.environ, NO_DEFAULT_CAS):
            ctx = gs.make_ssl_context("/nonexistent/fallback.pem")
        self.assertEqual(ctx.cert_store_stats()["x509_ca"], 0)
        self.assertEqual(ctx.verify_mode, ssl.CERT_REQUIRED)
        self.assertTrue(ctx.check_hostname)


class ApiGetErrorTests(unittest.TestCase):
    def test_cert_failure_raises_plain_error_without_retrying(self):
        reason = ssl.SSLCertVerificationError(1, "certificate verify failed")
        reason.verify_message = "unable to get local issuer certificate"
        with mock.patch("urllib.request.urlopen",
                        side_effect=urllib.error.URLError(reason)) as urlopen, \
                mock.patch("time.sleep") as sleep:
            with self.assertRaises(gs.SyncError) as ctx:
                gs.api_get("/notes", {}, "grn_test")
        self.assertEqual(urlopen.call_count, 1)
        sleep.assert_not_called()
        self.assertNotIsInstance(ctx.exception, gs.AuthError)
        self.assertIn("unable to get local issuer certificate",
                      str(ctx.exception))
        self.assertIn("before anything was sent", str(ctx.exception))

    def test_401_raises_auth_error(self):
        err = urllib.error.HTTPError("https://x", 401, "Unauthorized", {},
                                     io.BytesIO(b""))
        with mock.patch("urllib.request.urlopen", side_effect=err):
            with self.assertRaises(gs.AuthError):
                gs.api_get("/notes", {}, "grn_test")


if __name__ == "__main__":
    unittest.main()
