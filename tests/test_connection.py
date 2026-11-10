import hashlib
import hmac
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "bridge"))
import kobo_connection as connection

TOKEN = "x" * 64
DEVICE_ID = "d" * 32


class ConnectionTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="hermes-pair-profile-")
        self.addCleanup(self.temp.cleanup)
        self.file = Path(self.temp.name) / "state/connection.json"
        self.env = mock.patch.dict(os.environ, {"HERMES_CONNECTION_FILE": str(self.file)}, clear=False)
        self.env.start()
        self.addCleanup(self.env.stop)
        connection.clear_discovery_cache()
        self.addCleanup(connection.clear_discovery_cache)

    def profile(self, mode="auto"):
        return {"schema_version": 1, "device_id": DEVICE_ID, "name": "Kobo", "token": TOKEN, "mode": mode, "url": "http://192.168.1.44:8675"}

    def test_profile_private_atomic_and_symlink_protected(self):
        connection.save_profile(self.profile())
        self.assertEqual(self.file.stat().st_mode & 0o777, 0o600)
        self.assertEqual(self.file.parent.stat().st_mode & 0o777, 0o700)
        self.assertEqual(connection.load_profile()["token"], TOKEN)
        self.file.chmod(0o644)
        with self.assertRaisesRegex(ValueError, "private"):
            connection.load_profile()
        self.file.unlink()
        target = Path(self.temp.name) / "target"
        target.write_text("preserve")
        self.file.symlink_to(target)
        with self.assertRaisesRegex(ValueError, "symlink"):
            connection.save_profile(self.profile())
        self.assertEqual(target.read_text(), "preserve")

    def test_discovery_proof_binds_nonce_device_address_port_and_version(self):
        nonce = "a" * 32
        data = {"service": connection.SERVICE, "protocol": 1, "kind": "device", "nonce": nonce,
                "device_id": DEVICE_ID, "address": "192.168.1.44", "port": 8675, "version": "2.5.0", "name": "Kobo", "pairing_open": False}
        message = "\n".join(("hermes-koreader/1", nonce, DEVICE_ID, data["address"], "8675", "2.5.0"))
        data["proof"] = hmac.new(TOKEN.encode(), message.encode(), hashlib.sha256).hexdigest()
        decoded = connection.decode_device(json.dumps(data).encode(), data["address"], nonce, TOKEN)
        self.assertTrue(decoded["verified"])
        self.assertNotIn("proof", decoded)
        self.assertNotIn("token", decoded)
        for key, value in (("nonce", "b" * 32), ("port", 9000), ("version", "9.0.0"), ("device_id", "e" * 32), ("address", "192.168.1.99")):
            bad = {**data, key: value}
            with self.subTest(key=key):
                self.assertIsNone(connection.decode_device(json.dumps(bad).encode(), data["address"], nonce, TOKEN))
        self.assertIsNone(connection.decode_device(json.dumps(data).encode(), "192.168.1.99", nonce, TOKEN))
        self.assertIsNone(connection.decode_device(json.dumps(data).encode(), data["address"], nonce, "wrong" * 10))

    def test_untrusted_discovery_cannot_receive_saved_token(self):
        connection.save_profile(self.profile())
        with mock.patch.dict(os.environ, {"KOBO_URL": "auto", "KOBO_TOKEN": ""}), mock.patch.object(connection, "discover", return_value=[]) as discover:
            with self.assertRaisesRegex(ValueError, "No matching Kobo"):
                connection.resolve_connection()
            self.assertEqual(discover.call_args.kwargs["token"], TOKEN)

    def test_auto_selection_cache_manual_fallback_and_environment_override(self):
        connection.save_profile(self.profile())
        device = {"device_id": DEVICE_ID, "url": "http://192.168.1.55:8675"}
        with mock.patch.dict(os.environ, {"KOBO_URL": "auto", "KOBO_TOKEN": ""}), mock.patch.object(connection, "discover", return_value=[device]) as discover:
            self.assertEqual(connection.resolve_connection(), (device["url"], TOKEN))
            self.assertEqual(connection.resolve_connection(), (device["url"], TOKEN))
            self.assertEqual(discover.call_count, 1)
            connection.clear_discovery_cache()
            device["url"] = "http://192.168.1.66:8675"
            self.assertEqual(connection.resolve_connection()[0], device["url"])
        connection.save_profile(self.profile(mode="manual"))
        with mock.patch.dict(os.environ, {"KOBO_URL": "", "KOBO_TOKEN": ""}), mock.patch.object(connection, "discover", side_effect=AssertionError("manual mode must not discover")):
            self.assertEqual(connection.resolve_connection(), (self.profile()["url"], TOKEN))
        self.assertEqual(connection.resolve_connection("http://127.0.0.1:8675", "y" * 64), ("http://127.0.0.1:8675", "y" * 64))

    def test_multiple_devices_or_endpoints_require_explicit_selection(self):
        devices = [{"device_id": DEVICE_ID, "url": "http://192.168.1.2:8675"}, {"device_id": "e" * 32, "url": "http://192.168.1.3:8675"}]
        with self.assertRaisesRegex(ValueError, "Multiple"):
            connection.select_device(devices)
        self.assertEqual(connection.select_device(devices, DEVICE_ID), devices[0])
        devices[1]["device_id"] = DEVICE_ID
        with self.assertRaisesRegex(ValueError, "Multiple"):
            connection.select_device(devices, DEVICE_ID)

    def test_discovery_targets_are_bounded_local_ipv4_only(self):
        self.assertEqual(connection.discovery_targets(["255.255.255.255", "192.168.1.255", "127.0.0.1"]), ["255.255.255.255", "192.168.1.255", "127.0.0.1"])
        for targets in (["8.8.8.8"], ["example.com"], ["224.0.0.1"], ["0.0.0.0"], ["::1"], ["127.0.0.1"] * 9):
            with self.subTest(targets=targets), self.assertRaises(ValueError):
                connection.discovery_targets(targets)


if __name__ == "__main__":
    unittest.main()
