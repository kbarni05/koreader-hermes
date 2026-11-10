"""Small, tagged release fixtures; all update networking is injected in tests."""
import hashlib
import json
from pathlib import Path
import sys

PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "bridge"))
import project_updater as updates


def release(version, obsolete=False):
    plugin = {p.name: p.read_bytes() for p in (PROJECT / "hermes.koplugin").glob("*.lua")}
    plugin["version.lua"] = f'return "{version}"\n'.encode()
    files = {"VERSION": (version + "\n").encode(), "README.md": ("Fixture " + version).encode(),
             "bridge/hermes_kobo_mcp.py": f'VERSION = "{version}"\n'.encode(),
             "bridge/project_updater.py": b'raise RuntimeError("Downloaded files must never be executed by the updater")\n'}
    files.update({"hermes.koplugin/" + name: content for name, content in plugin.items()})
    if obsolete:
        files["bridge/obsolete.py"] = b"OLD = True\n"
    prefix = f"https://raw.githubusercontent.com/{updates.REPOSITORY}/v{version}/"

    def entry(name, data, url_path=None):
        return {"path": name, "size": len(data), "sha256": hashlib.sha256(data).hexdigest(), "url": prefix + (url_path or name)}

    manifest = {"schema_version": 1, "repository": updates.REPOSITORY, "version": version, "preview": True,
                "plugin": [entry(name, data, "hermes.koplugin/" + name) for name, data in sorted(plugin.items())],
                "project": [entry(name, data) for name, data in sorted(files.items())]}
    return manifest, files


def install(root, manifest, files):
    for name, data in files.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    (root / "updates").mkdir(exist_ok=True)
    (root / "updates/latest.json").write_text(json.dumps(manifest))


class Fetcher:
    def __init__(self, manifest, files):
        self.manifest = manifest
        self.files = {item["url"]: files[item["path"]] for item in manifest["project"]}
        self.corrupt = None

    def __call__(self, url, limit):
        data = json.dumps(self.manifest).encode() if url == updates.FEED else self.files[url]
        return data + b"corrupt" if url == self.corrupt else data


class Kobo:
    def __init__(self):
        self.version = "2.0.0"
        self.phase = "idle"
        self.transaction = None
        self.allowed = True
        self.calls = []
        self.commit_error = False
        self.lost_commit_reply = False
        self.offline_after_commit = False
        self.offline = False
        self.request_state = None

    def request(self, payload):
        self.calls.append(payload["op"])
        if self.offline:
            raise ValueError("Kobo offline")
        op = payload["op"]
        if op == "status":
            return {"version": self.version, "update_manager": self.request({"op": "update_status"})}
        if op == "update_status":
            return {"installed_version": self.version, "running_version": "2.0.0", "phase": self.phase,
                    "transaction_id": self.transaction, "allow_project_updates": self.allowed, "request": self.request_state}
        if not self.allowed:
            raise ValueError("Enable project updates")
        if op == "update_prepare":
            self.transaction, self.phase = "123-1", "staged"
            self.target = payload["release"]["version"]
            return {"transaction_id": self.transaction}
        if op == "update_commit":
            if self.commit_error:
                raise ValueError("Kobo commit rejected")
            self.version, self.phase = self.target, "installed"
            self.offline = self.offline_after_commit
            if self.lost_commit_reply or self.offline:
                raise ValueError("Kobo reply lost")
            return self.request({"op": "update_status"})
        if op == "update_rollback":
            self.version, self.phase = "2.0.0", "rolled_back"
            return self.request({"op": "update_status"})
        if op == "update_cancel":
            self.phase = "cancelled"
            return self.request({"op": "update_status"})
        if op == "update_request_finish":
            self.request_state["state"] = payload["state"]
            self.request_state["message"] = payload["message"]
            return {"ok": True}
        raise AssertionError(op)
