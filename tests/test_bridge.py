import base64
import copy
import importlib.util
import json
import os
from pathlib import Path
import select
import shutil
import socket
import subprocess
import tempfile
import unittest
import urllib.error
import urllib.request

PROJECT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT / "bridge/hermes_kobo_mcp.py"
spec = importlib.util.spec_from_file_location("bridge", SCRIPT)
bridge = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bridge)
TOKEN = "test_token_" + "a" * 32


class ProtocolTests(unittest.TestCase):
    def test_mcp_discovery_without_configuration(self):
        messages = [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {"protocolVersion": "2025-03-26"}},
            {"jsonrpc": "2.0", "method": "notifications/initialized"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "ping"},
        ]
        proc = subprocess.run(["python3", str(SCRIPT)], input="\n".join(map(json.dumps, messages)) + "\n", text=True, capture_output=True, timeout=5)
        self.assertEqual(proc.returncode, 0, proc.stderr)
        output = [json.loads(line) for line in proc.stdout.splitlines()]
        self.assertEqual(len(output), 3)
        self.assertEqual(output[0]["result"]["protocolVersion"], "2025-03-26")
        tools = {tool["name"] for tool in output[1]["result"]["tools"]}
        self.assertTrue({"kobo_read_file", "hermes_check_updates", "hermes_update_project", "hermes_rollback_project"} <= tools)
        self.assertNotIn("_op", output[1]["result"]["tools"][0])

    def test_invalid_protocol_and_arguments(self):
        self.assertEqual(bridge.handle([])["error"]["code"], -32600)
        self.assertEqual(bridge.handle({"jsonrpc": "2.0", "id": 1, "method": "missing"})["error"]["code"], -32601)
        with self.assertRaises(ValueError):
            bridge.validate_args(bridge.BY_NAME["kobo_write_file"], {"path": "x"})
        with self.assertRaises(ValueError):
            bridge.validate_args(bridge.BY_NAME["kobo_list_files"], {"limit": True})

    def test_url_token_and_redirect_controls(self):
        for url in ["file:///tmp", "http://user:pw@localhost", "http://localhost/evil", "http://localhost?x=1"]:
            with self.assertRaises(ValueError):
                bridge.KoboClient(url, TOKEN)
        with self.assertRaises(ValueError):
            bridge.KoboClient("http://localhost", "short")
        with self.assertRaises(ValueError):
            bridge.NoRedirect().redirect_request(None, None, 302, "", {}, "http://evil")


class IntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.lua = os.environ.get("LUA_BIN") or shutil.which("texlua") or shutil.which("lua") or shutil.which("luajit")
        if not cls.lua:
            raise RuntimeError("Install lua + luasocket + luafilesystem or set LUA_BIN; integration tests require a real Lua runtime")
        cls.temp = tempfile.TemporaryDirectory(prefix="hermes-kobo-test-")
        cls.root = Path(cls.temp.name)
        (cls.root / "files").mkdir()
        (cls.root / "plugins").mkdir()
        shutil.copytree(PROJECT / "hermes.koplugin", cls.root / "plugins/hermes.koplugin")
        cls.env = {**os.environ, "HERMES_PROJECT": str(PROJECT), "HERMES_TEST_ROOT": str(cls.root), "KOBO_TOKEN": TOKEN}
        cls.proc = subprocess.Popen([cls.lua, str(PROJECT / "tests/lua_server.lua")], stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=cls.env)
        ready, _, _ = select.select([cls.proc.stdout], [], [], 5)
        if not ready:
            cls.proc.kill()
            raise RuntimeError("Lua harness did not start: " + cls.proc.communicate()[1])
        line = cls.proc.stdout.readline().strip()
        if not line.isdigit():
            cls.proc.kill()
            raise RuntimeError("Lua harness failed: " + line + cls.proc.communicate()[1])
        cls.port = int(line)
        cls.url = f"http://127.0.0.1:{cls.port}"
        cls.client = bridge.KoboClient(cls.url, TOKEN)
        cls.env["KOBO_URL"] = cls.url

    @classmethod
    def tearDownClass(cls):
        cls.proc.terminate()
        cls.proc.communicate(timeout=5)
        cls.temp.cleanup()

    def call(self, tool_name, **args):
        return bridge.call_tool(self.client, tool_name, args)

    def test_file_roundtrip_conflicts_backup_move_trash_restore(self):
        self.call("kobo_create_directory", path="notes")
        created = self.call("kobo_write_file", path="notes/test.txt", content="hello 世界\n", expected_revision="new")
        read = self.call("kobo_read_file", path="notes/test.txt")
        self.assertEqual(read["content"], "hello 世界\n")
        self.assertEqual(read["revision"], created["revision"])
        with self.assertRaisesRegex(ValueError, "Revision conflict"):
            self.call("kobo_write_file", path="notes/test.txt", content="oops", expected_revision="new")
        edited = self.call("kobo_write_file", path="notes/test.txt", content="edited", expected_revision=read["revision"])
        self.assertTrue(edited["backup_id"])
        self.call("kobo_restore_file", recovery_id=edited["backup_id"], destination="notes/old.txt")
        self.assertEqual(self.call("kobo_read_file", path="notes/old.txt")["content"], read["content"])
        self.call("kobo_move_file", path="notes/test.txt", destination="notes/new.txt")
        deleted = self.call("kobo_trash_file", path="notes/new.txt")
        self.assertTrue(any(e["id"] == deleted["recovery_id"] for e in self.call("kobo_list_recovery")["entries"]))
        self.call("kobo_restore_file", recovery_id=deleted["recovery_id"], destination="notes/restored.txt")
        self.assertEqual(self.call("kobo_read_file", path="notes/restored.txt")["content"], "edited")

    def test_binary_limit_and_large_book_moves(self):
        self.call("kobo_write_file", path="empty.txt", content="", expected_revision="new")
        self.assertEqual(self.call("kobo_read_file", path="empty.txt")["content"], "")
        data = bytes(range(256)) * 1024
        encoded = base64.b64encode(data).decode()
        self.call("kobo_write_file", path="binary.bin", content=encoded, encoding="base64", expected_revision="new")
        read = self.call("kobo_read_file", path="binary.bin", encoding="base64")
        self.assertEqual(read["content"], encoded)
        with self.assertRaisesRegex(ValueError, "not UTF-8"):
            self.call("kobo_read_file", path="binary.bin")
        with self.assertRaisesRegex(ValueError, "256 KiB"):
            self.call("kobo_write_file", path="too-big", content="x" * (262144 + 1), expected_revision="new")
        (self.root / "files/book.epub").write_bytes(b"x" * 300000)
        with self.assertRaisesRegex(ValueError, "limit"):
            self.call("kobo_read_file", path="book.epub")
        self.call("kobo_move_file", path="book.epub", destination="moved.epub")
        self.assertTrue((self.root / "files/moved.epub").exists())

    def test_scopes_symlinks_and_traversal(self):
        (self.root / "outside.txt").write_text("untouched")
        (self.root / "files/link").symlink_to(self.root / "outside.txt")
        (self.root / "files/linkdir").symlink_to(self.root / "plugins", target_is_directory=True)
        for path in ["../outside.txt", "/etc/passwd", ".hidden", "a/../b", "a//b", "a\\b", "link", "linkdir/x", "a\x00b"]:
            with self.subTest(path=path), self.assertRaises(ValueError):
                self.call("kobo_write_file", path=path, content="bad", expected_revision="new")
        with self.assertRaises(ValueError):
            self.call("kobo_trash_file", path="")
        with self.assertRaises(ValueError):
            self.call("kobo_write_file", scope="plugins", path="hermes.koplugin/main.lua", content="bad", expected_revision="new")
        self.assertEqual((self.root / "outside.txt").read_text(), "untouched")

    def test_permissions_on_device(self):
        self.client.request({"op": "__test_permissions", "writes": False, "plugins": False})
        try:
            self.assertFalse(self.call("kobo_status")["file_writes"])
            self.call("kobo_list_files")
            with self.assertRaisesRegex(ValueError, "Enable file writes"):
                self.call("kobo_write_file", path="blocked", content="x", expected_revision="new")
            with self.assertRaisesRegex(ValueError, "Enable plugin changes"):
                self.call("kobo_install_plugin", name="blocked.koplugin", files={"main.lua": "return {}", "_meta.lua": "return {}"})
        finally:
            self.client.request({"op": "__test_permissions", "writes": True, "plugins": True})

    def test_plugin_install_enable_edit_remove_restore(self):
        files = {"main.lua": "return {}\n", "_meta.lua": "return { fullname = 'Test' }\n", "lib/helper.lua": "return true"}
        result = self.call("kobo_install_plugin", name="demo.koplugin", files=files)
        self.assertTrue(result["disabled"])
        self.assertTrue((self.root / "plugins/demo.koplugin/lib/helper.lua").is_file())
        self.call("kobo_set_plugin_enabled", path="demo.koplugin", enabled=True)
        self.assertFalse(next(e for e in self.call("kobo_list_plugins")["entries"] if e["name"] == "demo.koplugin")["disabled"])
        with self.assertRaises(ValueError):
            self.call("kobo_install_plugin", name="demo.koplugin", files=files)
        self.assertFalse(next(e for e in self.call("kobo_list_plugins")["entries"] if e["name"] == "demo.koplugin")["disabled"])
        revision = self.call("kobo_read_file", scope="plugins", path="demo.koplugin/main.lua")["revision"]
        self.call("kobo_write_file", scope="plugins", path="demo.koplugin/main.lua", content="return { updated = true }", expected_revision=revision)
        deleted = self.call("kobo_trash_file", scope="plugins", path="demo.koplugin")
        self.call("kobo_restore_file", scope="plugins", recovery_id=deleted["recovery_id"], destination="demo.koplugin")
        self.assertTrue(next(e for e in self.call("kobo_list_plugins")["entries"] if e["name"] == "demo.koplugin")["disabled"])
        for badfiles in [{"main.lua": "x", "_meta.lua": "x", "../escape": "x"}, {"main.lua": "x", "_meta.lua": "x", "lib": "x", "lib/x": "x"}]:
            with self.assertRaises(ValueError):
                self.call("kobo_install_plugin", name="bad.koplugin", files=badfiles)
        self.assertFalse((self.root / "plugins/bad.koplugin").exists())

    def raw(self, request):
        with socket.create_connection(("127.0.0.1", self.port), timeout=5) as conn:
            conn.sendall(request)
            chunks = []
            while True:
                data = conn.recv(65536)
                if not data:
                    return b"".join(chunks)
                chunks.append(data)

    def test_transport_auth_and_malformed_requests(self):
        with self.assertRaisesRegex(ValueError, "401"):
            bridge.KoboClient(self.url, "b" * 32).request({"op": "status"})
        auth = f"Authorization: Bearer {TOKEN}\r\n".encode()
        for headers, body, expected in [
            (b"Content-Length: 2\r\nContent-Length: 2\r\n", b"{}", b"400"),
            (b"Content-Length: 999999\r\n", b"", b"413"),
            (b"Transfer-Encoding: chunked\r\nContent-Length: 2\r\n", b"{}", b"400"),
            (b"Content-Length: 3\r\n", b"bad", b"400"),
            (b"Content-Length: 67\r\n", b"[" * 33 + b"0" + b"]" * 33, b"400"),
        ]:
            with self.subTest(headers=headers):
                result = self.raw(b"POST /v1/rpc HTTP/1.1\r\n" + auth + headers + b"\r\n" + body)
                self.assertIn(expected, result.split(b"\r\n", 1)[0])
        # An incomplete client must not block another request or the UI pump.
        with socket.create_connection(("127.0.0.1", self.port), timeout=5) as slow:
            slow.sendall(b"POST /v1/rpc HTTP/1.1\r\nContent-Length: 100\r\n")
            self.assertEqual(self.call("kobo_status")["version"], "2.0.0")

    def test_stdio_mcp_to_real_lua_endpoint(self):
        message = {"jsonrpc": "2.0", "id": 9, "method": "tools/call", "params": {"name": "kobo_status", "arguments": {}}}
        proc = subprocess.run(["python3", str(SCRIPT)], input=json.dumps(message) + "\n", text=True, capture_output=True, timeout=10, env=self.env)
        result = json.loads(proc.stdout)["result"]
        self.assertFalse(result["isError"], result)
        self.assertEqual(json.loads(result["content"][0]["text"])["version"], "2.0.0")

    def test_koreader_menu_and_lifecycle(self):
        proc = subprocess.run([self.lua, str(PROJECT / "tests/lua_ui.lua")], text=True, capture_output=True, timeout=5, env=self.env)
        self.assertEqual(proc.returncode, 0, proc.stdout + proc.stderr)
        self.assertIn("UI lifecycle OK", proc.stdout)

    def test_update_integrity_real_lua_commit_and_whole_project_rollback(self):
        from update_fixtures import Fetcher, install, release, updates
        target, files = release("2.1.0")
        bundle = {item["path"]: base64.b64encode(files["hermes.koplugin/" + item["path"]]).decode() for item in target["plugin"]}
        corrupt = dict(bundle)
        corrupt["main.lua"] = base64.b64encode(b"return {}").decode()
        with self.assertRaisesRegex(ValueError, "Checksum mismatch"):
            self.client.request({"op": "update_prepare", "release": target, "files": corrupt})
        bad = copy.deepcopy(target)
        bad["plugin"][0]["path"] = "../escape.lua"
        with self.assertRaises(ValueError):
            self.client.request({"op": "update_prepare", "release": bad, "files": bundle})
        # Changed staged bytes must be checked again before directory exchange.
        staged = self.client.request({"op": "update_prepare", "release": target, "files": bundle})
        staged_file = self.root / "plugins/.hermes-updates" / staged["transaction_id"] / "next.koplugin/main.lua"
        staged_file.write_text("return {}")
        with self.assertRaisesRegex(ValueError, "Staged checksum"):
            self.client.request({"op": "update_commit", "transaction_id": staged["transaction_id"]})
        self.client.request({"op": "update_cancel", "transaction_id": staged["transaction_id"]})
        self.assertEqual(self.client.request({"op": "update_status"})["installed_version"], "2.0.0")
        # The NAS coordinator now talks to the real Lua transaction engine.
        with tempfile.TemporaryDirectory(prefix="hermes-real-project-") as temp:
            root = Path(temp)
            old, old_files = release("2.0.0")
            install(root, old, old_files)
            manager = updates.ProjectUpdater(root, fetch=Fetcher(target, files))
            result = manager.update(self.client, "2.1.0", True)
            status = self.client.request({"op": "update_status"})
            self.assertEqual(status["installed_version"], "2.1.0")
            self.assertEqual(status["running_version"], "2.0.0")
            self.assertTrue(status["restart_required"])
            for item in target["plugin"]:
                actual = self.root / "plugins/hermes.koplugin" / item["path"]
                self.assertEqual(updates.digest(actual.read_bytes()), item["sha256"])
            manager.rollback(self.client, result["transaction_id"])
            self.assertEqual(self.client.request({"op": "update_status"})["installed_version"], "2.0.0")
            self.assertEqual(manager.local_version(), "2.0.0")

    def test_whole_project_request_processed_from_kobo_menu_queue(self):
        from update_fixtures import Fetcher, install, release, updates
        old, old_files = release("2.0.0")
        target, files = release("2.1.0")
        with tempfile.TemporaryDirectory(prefix="hermes-queued-project-") as temp:
            root = Path(temp)
            install(root, old, old_files)
            manager = updates.ProjectUpdater(root, fetch=Fetcher(target, files))
            self.client.request({"op": "__test_request", "value": {"id": "test-request", "state": "queued", "expected_version": "2.1.0", "allow_preview": True}})
            try:
                bridge.poll_device_requests(self.client, manager)
                status = self.client.request({"op": "update_status"})
                self.assertEqual(status["request"]["state"], "done", status["request"])
                self.assertEqual(status["installed_version"], "2.1.0")
                manager.rollback(self.client, manager.state()["id"])
            finally:
                self.client.request({"op": "__test_request", "value": False})


if __name__ == "__main__":
    unittest.main()
