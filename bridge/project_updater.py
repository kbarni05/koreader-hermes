"""Versioned, checksum-verified updates for the NAS project and Kobo plugin.

Only this repository's tagged files are accepted. Configuration and untracked
files are never included. No downloaded Python or Lua is executed by this module.
"""
from __future__ import annotations

from contextlib import contextmanager
import base64
import fcntl
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import secrets
import shutil
import threading
import urllib.request

REPOSITORY = "kbarni05/koreader-hermes"
FEED = f"https://raw.githubusercontent.com/{REPOSITORY}/main/updates/latest.json"
MAX_FILE = 1048576
MAX_TOTAL = 8 * 1048576
ROOT_FILES = {"VERSION", "LICENSE", "README.md", "CHANGELOG.md", "build_release.py", "manifest.json", ".gitignore"}
ROOT_DIRS = {"bridge", "hermes.koplugin", "tests", "versions"}


def digest(data):
    return hashlib.sha256(data).hexdigest()


def version_tuple(value):
    if not isinstance(value, str) or not re.fullmatch(r"\d+\.\d+\.\d+", value):
        raise ValueError("Invalid project version")
    return tuple(map(int, value.split(".")))


def checked_path(name):
    if not isinstance(name, str) or not name or len(name) > 512 or "\\" in name or any(ord(c) < 32 for c in name):
        raise ValueError("Invalid managed project path")
    parts = PurePosixPath(name).parts
    if name != "/".join(parts) or name.startswith("/") or any(p in (".", "..") or p.startswith(".") for p in parts) and name != ".gitignore":
        raise ValueError("Traversal or hidden project path")
    if name not in ROOT_FILES and (len(parts) < 2 or parts[0] not in ROOT_DIRS):
        raise ValueError("Path is outside managed project files")
    return name


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("Update server redirected; refusing an unexpected origin")


def fetch_https(url, limit):
    prefix = f"https://raw.githubusercontent.com/{REPOSITORY}/"
    if not isinstance(url, str) or not url.startswith(prefix):
        raise ValueError("Untrusted update origin")
    opener = urllib.request.build_opener(NoRedirect())
    request = urllib.request.Request(url, headers={"User-Agent": "Hermes-NAS-Updater/2"})
    try:
        with opener.open(request, timeout=25) as response:
            data = response.read(limit + 1)
    except OSError:
        raise ValueError("Cannot download update. The GitHub repository and tagged files must be public and reachable over HTTPS.") from None
    if len(data) > limit:
        raise ValueError("Update response exceeds its size limit")
    return data


def validate_release(release):
    if not isinstance(release, dict) or type(release.get("schema_version")) is not int or release.get("schema_version") != 1 or release.get("repository") != REPOSITORY:
        raise ValueError("Invalid release manifest")
    version_tuple(release.get("version"))
    if type(release.get("preview")) is not bool:
        raise ValueError("Release preview flag must be a boolean")
    for component in ("plugin", "project"):
        items = release.get(component)
        if not isinstance(items, list) or not 1 <= len(items) <= 128:
            raise ValueError("Missing or oversized release file list")
        names, total = set(), 0
        for item in items:
            if not isinstance(item, dict):
                raise ValueError("Invalid release file")
            path = item.get("path")
            if component == "plugin":
                if not isinstance(path, str) or not re.fullmatch(r"[A-Za-z0-9_-]+\.lua", path):
                    raise ValueError("Invalid plugin update path")
                source_path = "hermes.koplugin/" + path
            else:
                checked_path(path)
                source_path = path
            if path in names:
                raise ValueError("Duplicate release path")
            names.add(path)
            size, checksum = item.get("size"), item.get("sha256")
            if type(size) is not int or not 0 <= size <= MAX_FILE or not isinstance(checksum, str) or not re.fullmatch(r"[a-f0-9]{64}", checksum):
                raise ValueError("Invalid update size or checksum")
            total += size
            if total > (524288 if component == "plugin" else MAX_TOTAL):
                raise ValueError("Release exceeds component size limit")
            expected = f"https://raw.githubusercontent.com/{REPOSITORY}/v{release['version']}/{source_path}"
            if item.get("url") != expected:
                raise ValueError("Update files must use the matching repository release tag")
        required = {"main.lua", "_meta.lua", "version.lua", "updater.lua", "api.lua", "fs.lua", "server.lua"} if component == "plugin" else {"VERSION", "bridge/hermes_kobo_mcp.py", "bridge/project_updater.py"}
        if not required <= names:
            raise ValueError("Release is missing a required component")
    project = {item["path"]: item for item in release["project"]}
    for item in release["plugin"]:
        other = project.get("hermes.koplugin/" + item["path"])
        if not other or item["sha256"] != other["sha256"] or item["size"] != other["size"]:
            raise ValueError("Plugin and project release files disagree")
    return release


class ProjectUpdater:
    def __init__(self, root=None, fetch=fetch_https, replace=os.replace):
        self.root = Path(root or os.environ.get("HERMES_PROJECT_ROOT") or Path(__file__).resolve().parents[1]).absolute()
        self.fetch, self.replace = fetch, replace
        self.home = self.root / ".hermes-updates"
        self.lock = threading.RLock()
        self.running_version = self.local_version()

    def local_version(self):
        file = self.root / "VERSION"
        if file.is_symlink():
            raise ValueError("VERSION must not be a symlink")
        value = file.read_text().strip() if file.exists() else "1.0.0"
        version_tuple(value)
        return value

    def path(self, name, make_parents=False):
        if name != "updates/latest.json":
            checked_path(name)
        current = self.root
        if current.is_symlink():
            raise ValueError("Project root must not be a symlink")
        for part in PurePosixPath(name).parts:
            current = current / part
            if current.is_symlink():
                raise ValueError("Managed project paths must not contain symlinks")
            if current.exists() and current != self.root / name and not current.is_dir():
                raise ValueError("Managed project parent is not a directory")
        if current.exists() and not current.is_file():
            raise ValueError("Managed project destination is not a file")
        if make_parents:
            current.parent.mkdir(parents=True, exist_ok=True)
        return current

    def _home(self):
        if self.home.is_symlink():
            raise ValueError("Unsafe update storage")
        self.home.mkdir(exist_ok=True)
        if not self.home.is_dir():
            raise ValueError("Update storage is not a directory")

    @contextmanager
    def exclusive(self):
        with self.lock:
            self._home()
            fd = os.open(self.home / "lock", os.O_CREAT | os.O_RDWR | getattr(os, "O_NOFOLLOW", 0), 0o600)
            try:
                try:
                    fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                except BlockingIOError:
                    raise ValueError("Another project update is running") from None
                yield
            finally:
                os.close(fd)

    def state(self):
        if self.home.is_symlink():
            raise ValueError("Unsafe update storage")
        file = self.home / "state.json"
        if file.is_symlink():
            raise ValueError("Unsafe update journal")
        if not file.exists():
            return None
        state = json.loads(file.read_text())
        if not isinstance(state, dict) or not re.fullmatch(r"[a-f0-9]{24}", state.get("id", "")):
            raise ValueError("Invalid update journal")
        tx = self.home / state["id"]
        if tx.is_symlink() or not tx.is_dir():
            raise ValueError("Unsafe update transaction")
        return state

    def journal(self, state):
        for name in ("state.tmp", "state.json"):
            if (self.home / name).is_symlink():
                raise ValueError("Unsafe update journal")
        temp = self.home / "state.tmp"
        with temp.open("w") as file:
            json.dump(state, file, indent=2)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temp, self.home / "state.json")

    def release(self, allow_preview=False):
        release = validate_release(json.loads(self.fetch(FEED, 131072)))
        if release["preview"] and not allow_preview:
            raise ValueError("Latest release is a preview; explicitly enable preview updates to use it")
        return release

    def check(self, client, allow_preview=False):
        release = self.release(allow_preview)
        device = client.request({"op": "status"})
        manager = device.get("update_manager")
        state = self.state()
        return {"latest_version": release["version"], "preview": release["preview"],
                "nas_installed_version": self.local_version(), "nas_running_version": self.running_version,
                "kobo": manager or {"installed_version": device.get("version"), "bootstrap_required": True},
                "nas_update_available": version_tuple(release["version"]) > version_tuple(self.local_version()),
                "transaction_id": state["id"] if state else None, "phase": state["phase"] if state else "idle"}

    def _stage(self, release):
        if not (self.root / "bridge/hermes_kobo_mcp.py").is_file() or not (self.root / "VERSION").is_file():
            raise ValueError("Install the complete v2 NAS kit first; a bridge-only or read-only Docker mount cannot update the whole project")
        current = self.state()
        if current and current["phase"] in ("applying", "device_commit_unknown", "staged"):
            raise ValueError("An unfinished project update needs rollback/recovery before another update")
        baseline_path = self.path("updates/latest.json")
        if not baseline_path.exists():
            raise ValueError("Installed update metadata missing; install the complete v2 NAS kit first")
        baseline = validate_release(json.loads(baseline_path.read_text()))
        old = {item["path"]: item for item in baseline["project"]}
        for name, item in old.items():
            path = self.path(name)
            if path.exists() and digest(path.read_bytes()) != item["sha256"]:
                raise ValueError(f"Managed file has local edits: {name}. Save/reconcile your edits before updating.")
        ident = secrets.token_hex(12)
        tx = self.home / ident
        tx.mkdir(mode=0o700)
        downloaded = {}
        for item in release["project"]:
            self.path(item["path"])
            data = self.fetch(item["url"], item["size"])
            if len(data) != item["size"] or digest(data) != item["sha256"]:
                raise ValueError(f"Update checksum mismatch: {item['path']}")
            downloaded[item["path"]] = data
        if downloaded["VERSION"] != (release["version"] + "\n").encode():
            raise ValueError("Project VERSION disagrees with release")
        for name, data in downloaded.items():
            if name.startswith("bridge/") and name.endswith(".py"):
                try:
                    compile(data, name, "exec")
                except SyntaxError:
                    raise ValueError(f"Invalid Python source in update: {name}") from None
        downloaded["updates/latest.json"] = (json.dumps(release, indent=2) + "\n").encode()
        names = sorted(set(old) | set(downloaded))
        originals = {}
        for name in names:
            path = self.path(name)
            originals[name] = path.exists()
            if path.exists():
                backup = tx / "backup" / name
                backup.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(path, backup)
            if name in downloaded:
                stage = tx / "next" / name
                stage.parent.mkdir(parents=True, exist_ok=True)
                stage.write_bytes(downloaded[name])
        state = {"id": ident, "phase": "staged", "version": release["version"], "previous_version": self.local_version(),
                 "originals": originals, "new_hashes": {n: digest(d) for n, d in downloaded.items()}, "device_transaction_id": None}
        self.journal(state)
        return state, downloaded

    def _commit(self, state):
        state["phase"] = "applying"
        self.journal(state)
        tx = self.home / state["id"]
        for name in state["originals"]:
            path = self.path(name, make_parents=True)
            staged = tx / "next" / name
            if name in state["new_hashes"]:
                if staged.is_symlink() or digest(staged.read_bytes()) != state["new_hashes"][name]:
                    raise ValueError("Staged project file changed")
                self.replace(staged, path)
            elif path.exists():
                path.unlink()

    def _restore(self, state):
        tx = self.home / state["id"]
        for name, existed in state["originals"].items():
            path = self.path(name, make_parents=True)
            if existed:
                backup = tx / "backup" / name
                if backup.is_symlink() or not backup.is_file():
                    raise ValueError("Project backup is missing or unsafe")
                temp = tx / "restore.tmp"
                if temp.is_symlink():
                    raise ValueError("Unsafe restore path")
                shutil.copy2(backup, temp)
                os.replace(temp, path)
            elif path.exists():
                path.unlink()
        state["phase"] = "rolled_back"
        self.journal(state)

    def update(self, client, expected_version, allow_preview=False):
        with self.exclusive():
            release = self.release(allow_preview)
            if release["version"] != expected_version:
                raise ValueError("Release changed since checking; check updates again")
            if version_tuple(release["version"]) < version_tuple(self.local_version()):
                raise ValueError("Downgrades require rollback, not the update command")
            device_info = client.request({"op": "status"})
            device = device_info.get("update_manager")
            if not isinstance(device, dict) or "allow_project_updates" not in device:
                raise ValueError("Install the v2 Kobo plugin once by USB or ZenPM to enable updates")
            if not device["allow_project_updates"]:
                raise ValueError("Enable project updates in the Kobo Update manager first")
            device_version = device["installed_version"]
            if version_tuple(device_version) > version_tuple(release["version"]):
                raise ValueError("Kobo is newer than the requested release")
            if self.local_version() == device_version == release["version"]:
                return {"up_to_date": True, "version": release["version"]}
            state, downloaded = self._stage(release)
            remote_id = None
            try:
                if device_version != release["version"]:
                    files = {item["path"]: base64.b64encode(downloaded["hermes.koplugin/" + item["path"]]).decode() for item in release["plugin"]}
                    prepared = client.request({"op": "update_prepare", "release": release, "files": files})
                    remote_id = prepared["transaction_id"]
                    state["device_transaction_id"] = remote_id
                    self.journal(state)
                self._commit(state)
            except Exception:
                self._restore(state)
                if remote_id:
                    try:
                        client.request({"op": "update_cancel", "transaction_id": remote_id})
                    except Exception:
                        pass
                raise
            if remote_id:
                try:
                    client.request({"op": "update_commit", "transaction_id": remote_id})
                except Exception as commit_error:
                    try:
                        status = client.request({"op": "update_status"})
                    except Exception:
                        state["phase"] = "device_commit_unknown"
                        self.journal(state)
                        raise ValueError("NAS updated; Kobo commit outcome unknown. Keep both backups, reconnect, and run rollback/recovery with transaction " + state["id"]) from commit_error
                    if not (status.get("transaction_id") == remote_id and status.get("phase") == "installed" and status.get("installed_version") == release["version"]):
                        self._restore(state)
                        try:
                            client.request({"op": "update_cancel", "transaction_id": remote_id})
                        except Exception:
                            pass
                        raise ValueError("Kobo did not commit the update; NAS files were restored") from commit_error
            state["phase"] = "installed"
            self.journal(state)
            return {"version": release["version"], "transaction_id": state["id"], "nas_updated": True,
                    "kobo_updated": bool(remote_id), "restart_hermes_required": True, "restart_koreader_required": bool(remote_id) or device.get("restart_required", False)}

    def rollback(self, client, transaction_id):
        with self.exclusive():
            state = self.state()
            if not state or state["id"] != transaction_id or state["phase"] not in ("installed", "applying", "staged", "device_commit_unknown"):
                raise ValueError("No matching recoverable project transaction")
            remote_id = state.get("device_transaction_id")
            if remote_id:
                device = client.request({"op": "update_status"})
                if device.get("transaction_id") != remote_id:
                    raise ValueError("Kobo has a different update transaction; refusing an unrelated rollback")
                if device.get("phase") == "installed":
                    client.request({"op": "update_rollback", "transaction_id": remote_id})
                elif device.get("phase") == "staged":
                    client.request({"op": "update_cancel", "transaction_id": remote_id})
                elif device.get("phase") not in ("rolled_back", "cancelled"):
                    raise ValueError("Kobo update state needs manual recovery")
            self._restore(state)
            return {"version": state["previous_version"], "transaction_id": state["id"], "rolled_back": True,
                    "restart_hermes_required": True, "restart_koreader_required": bool(remote_id)}
