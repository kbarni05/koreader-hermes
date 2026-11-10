import copy
import base64
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import unittest
from update_fixtures import Fetcher, Kobo, install, release, updates, PROJECT
import hermes_kobo_mcp as bridge


class UpdaterTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="hermes-project-update-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.old, self.old_files = release("2.0.0", obsolete=True)
        install(self.root, self.old, self.old_files)
        self.new, self.new_files = release("2.1.0")
        self.fetch = Fetcher(self.new, self.new_files)
        self.manager = updates.ProjectUpdater(self.root, fetch=self.fetch)
        self.kobo = Kobo()

    def assert_old(self):
        self.assertEqual(self.manager.local_version(), "2.0.0")
        for name, data in self.old_files.items():
            self.assertEqual((self.root / name).read_bytes(), data, name)

    def test_update_every_managed_component_preserve_config_and_rollback(self):
        (self.root / ".env").write_text("private settings")
        (self.root / "custom-config.json").write_text("my config")
        result = self.manager.update(self.kobo, "2.1.0", True)
        self.assertTrue(result["nas_updated"] and result["kobo_updated"])
        for name, data in self.new_files.items():
            self.assertEqual((self.root / name).read_bytes(), data, name)
        self.assertFalse((self.root / "bridge/obsolete.py").exists())
        self.assertEqual(self.kobo.version, "2.1.0")
        self.assertEqual((self.root / ".env").read_text(), "private settings")
        self.assertEqual((self.root / "custom-config.json").read_text(), "my config")
        result = self.manager.rollback(self.kobo, result["transaction_id"])
        self.assertTrue(result["rolled_back"])
        self.assert_old()
        self.assertEqual(self.kobo.version, "2.0.0")

    def test_preview_expected_version_and_device_permission(self):
        with self.assertRaisesRegex(ValueError, "preview"):
            self.manager.update(self.kobo, "2.1.0")
        with self.assertRaisesRegex(ValueError, "changed"):
            self.manager.update(self.kobo, "9.0.0", True)
        self.kobo.allowed = False
        with self.assertRaisesRegex(ValueError, "Enable project updates"):
            self.manager.update(self.kobo, "2.1.0", True)
        self.assert_old()

    def test_bad_checksum_and_python_syntax_never_modify_install(self):
        self.fetch.corrupt = self.new["project"][0]["url"]
        with self.assertRaisesRegex(ValueError, "checksum"):
            self.manager.update(self.kobo, "2.1.0", True)
        self.assertNotIn("update_prepare", self.kobo.calls)
        self.assert_old()
        self.fetch.corrupt = None
        for item in self.new["project"]:
            if item["path"] == "bridge/hermes_kobo_mcp.py":
                body = b"def invalid python"
                item.update(size=len(body), sha256=updates.digest(body))
                self.fetch.files[item["url"]] = body
        with self.assertRaisesRegex(ValueError, "Invalid Python"):
            self.manager.update(self.kobo, "2.1.0", True)
        self.assert_old()

    def test_local_edits_and_symlink_paths_are_refused(self):
        (self.root / "README.md").write_text("my edits")
        with self.assertRaisesRegex(ValueError, "local edits"):
            self.manager.update(self.kobo, "2.1.0", True)
        (self.root / "README.md").unlink()
        outside = self.root / "outside"
        outside.write_text("do not touch")
        (self.root / "README.md").symlink_to(outside)
        with self.assertRaisesRegex(ValueError, "symlink"):
            self.manager.update(self.kobo, "2.1.0", True)
        self.assertEqual(outside.read_text(), "do not touch")

    def test_mid_commit_io_failure_restores_nas_and_cancels_kobo(self):
        failed = False

        def fail_once(source, destination):
            nonlocal failed
            if Path(destination).name == "VERSION" and not failed:
                failed = True
                raise OSError("simulated disk error")
            return updates.os.replace(source, destination)

        self.manager.replace = fail_once
        with self.assertRaisesRegex(OSError, "disk error"):
            self.manager.update(self.kobo, "2.1.0", True)
        self.assert_old()
        self.assertEqual(self.kobo.phase, "cancelled")

    def test_device_commit_failure_restores_nas(self):
        self.kobo.commit_error = True
        with self.assertRaisesRegex(ValueError, "NAS files were restored"):
            self.manager.update(self.kobo, "2.1.0", True)
        self.assert_old()
        self.assertEqual(self.kobo.phase, "cancelled")

    def test_lost_successful_device_reply_is_reconciled(self):
        self.kobo.lost_commit_reply = True
        result = self.manager.update(self.kobo, "2.1.0", True)
        self.assertTrue(result["kobo_updated"])
        self.assertEqual(self.manager.local_version(), self.kobo.version)

    def test_unknown_device_outcome_keeps_backups_for_recovery(self):
        self.kobo.offline_after_commit = True
        with self.assertRaisesRegex(ValueError, "outcome unknown"):
            self.manager.update(self.kobo, "2.1.0", True)
        state = self.manager.state()
        self.assertEqual(state["phase"], "device_commit_unknown")
        self.assertEqual(self.manager.local_version(), "2.1.0")
        self.kobo.offline = False
        self.manager.rollback(self.kobo, state["id"])
        self.assert_old()

    def test_interrupted_nas_commit_is_recoverable(self):
        def interrupt(source, destination):
            if Path(destination).name == "version.lua":
                raise KeyboardInterrupt("simulated process exit")
            return updates.os.replace(source, destination)
        self.manager.replace = interrupt
        with self.assertRaises(KeyboardInterrupt):
            self.manager.update(self.kobo, "2.1.0", True)
        state = self.manager.state()
        self.assertEqual(state["phase"], "applying")
        self.manager.rollback(self.kobo, state["id"])
        self.assert_old()

    def test_manifest_rejects_wrong_origin_traversal_duplicates_and_inconsistent_hashes(self):
        for kind in ("origin", "traversal", "duplicate", "plugin_hash"):
            with self.subTest(kind=kind):
                bad = copy.deepcopy(self.new)
                if kind == "origin": bad["project"][0]["url"] = "https://evil.invalid/payload"
                if kind == "traversal": bad["project"][0]["path"] = "../escape"
                if kind == "duplicate": bad["project"].append(bad["project"][0])
                if kind == "plugin_hash": bad["plugin"][0]["sha256"] = "0" * 64
                with self.assertRaises(ValueError): updates.validate_release(bad)

    def test_readonly_or_incomplete_install_is_clear(self):
        (self.root / "updates/latest.json").unlink()
        with self.assertRaisesRegex(ValueError, "complete v2 NAS kit"):
            self.manager.update(self.kobo, "2.1.0", True)
        self.assert_old()

    def test_interrupted_device_request_is_reported_without_automatic_retry(self):
        self.kobo.request_state = {"id": "interrupted", "state": "working", "expected_version": "2.1.0", "allow_preview": True}
        bridge.poll_device_requests(self.kobo, self.manager)
        self.assertEqual(self.kobo.request_state["state"], "error")
        self.assertNotIn("update_prepare", self.kobo.calls)
        self.assert_old()
        self.kobo.request_state = {"id": "completed", "state": "working", "expected_version": "2.0.0", "allow_preview": True}
        bridge.poll_device_requests(self.kobo, self.manager)
        self.assertEqual(self.kobo.request_state["state"], "done")
        # Matching VERSION files alone must not hide an unfinished file commit.
        self.manager.fetch = Fetcher(self.old, self.old_files)
        with self.manager.exclusive():
            staged, _ = self.manager._stage(self.old)
        self.kobo.request_state = {"id": "unfinished", "state": "working", "expected_version": "2.0.0", "allow_preview": True}
        bridge.poll_device_requests(self.kobo, self.manager)
        self.assertEqual(self.kobo.request_state["state"], "error")

    def test_separate_process_lock_blocks_conflicting_update(self):
        other = updates.ProjectUpdater(self.root, fetch=self.fetch)
        with self.manager.exclusive():
            with self.assertRaisesRegex(ValueError, "Another project update"):
                other.update(self.kobo, "2.1.0", True)
        self.assert_old()

    def test_v1_device_requires_one_time_bootstrap(self):
        class V1:
            def request(self, payload):
                self.assert_payload = payload
                return {"version": "1.0.0"}
        with self.assertRaisesRegex(ValueError, "Install the v2 Kobo plugin once"):
            self.manager.update(V1(), "2.1.0", True)
        self.assert_old()

    def test_lua_directory_exchange_failure_and_lost_journal_recovery(self):
        lua = os.environ.get("LUA_BIN") or shutil.which("texlua") or shutil.which("lua") or shutil.which("luajit")
        (self.root / "release.json").write_text(json.dumps(self.new))
        bundle = {item["path"]: base64.b64encode(self.new_files["hermes.koplugin/" + item["path"]]).decode() for item in self.new["plugin"]}
        (self.root / "bundle.json").write_text(json.dumps(bundle))
        proc = subprocess.run([lua, str(PROJECT / "tests/lua_update_failure.lua")], env={**os.environ, "HERMES_PROJECT": str(PROJECT), "HERMES_TEST_ROOT": str(self.root)}, capture_output=True, text=True, timeout=10)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("Lua updater rollback and reconciliation OK", proc.stdout)


if __name__ == "__main__":
    unittest.main()
