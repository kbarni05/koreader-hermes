"""LAN discovery, locally approved pairing and private NAS connection profiles."""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
import json
import os
from pathlib import Path
import re
import secrets
import socket
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

SERVICE = "hermes-koreader"
DISCOVERY_PORT = 8676
_cache = {}
_lock = threading.Lock()


def valid_token(token):
    return isinstance(token, str) and re.fullmatch(r"[A-Za-z0-9_-]{32,128}", token) is not None


def checked_url(url):
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password or parsed.path not in ("", "/") or parsed.query or parsed.fragment:
        raise ValueError("Use http://<Kobo-IP>:8675 without credentials or a path")
    try:
        port = parsed.port
    except ValueError:
        raise ValueError("Invalid Kobo port") from None
    if port is not None and not 1 <= port <= 65535:
        raise ValueError("Invalid Kobo port")
    return url.rstrip("/")


def profile_path():
    custom = os.environ.get("HERMES_CONNECTION_FILE")
    if custom:
        return Path(custom).expanduser().absolute()
    home = Path(os.environ.get("XDG_STATE_HOME") or Path.home() / ".local/state")
    return (home / "hermes-kobo/connection.json").absolute()


def _safe_parents(path):
    for parent in (path, *path.parents):
        if parent.is_symlink():
            raise ValueError("Connection profile paths must not contain symlinks")


def load_profile(path=None):
    path = Path(path or profile_path())
    _safe_parents(path)
    if not path.exists():
        return {}
    if not path.is_file():
        raise ValueError("Connection profile must be a regular file")
    if path.stat().st_mode & 0o077:
        raise ValueError("Connection profile must be private; set its permissions to 600")
    if path.stat().st_size > 8192:
        raise ValueError("Connection profile too large")
    profile = json.loads(path.read_text())
    if not isinstance(profile, dict) or profile.get("schema_version") != 1 or not valid_token(profile.get("token")) or not re.fullmatch(r"[a-f0-9]{32}", profile.get("device_id", "")):
        raise ValueError("Invalid connection profile; pair the Kobo again")
    checked_url(profile.get("url", ""))
    return profile


def save_profile(profile, path=None):
    path = Path(path or profile_path())
    _safe_parents(path)
    path.parent.mkdir(parents=True, mode=0o700, exist_ok=True)
    _safe_parents(path)
    fd, temporary = tempfile.mkstemp(prefix=".connection-", dir=path.parent)
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, "w") as file:
            json.dump(profile, file, indent=2)
            file.write("\n")
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return path


def discovery_targets(explicit=None):
    values = explicit or os.environ.get("KOBO_DISCOVERY_TARGETS", "255.255.255.255").split(",")
    result = []
    for value in values:
        try:
            address = ipaddress.IPv4Address(value.strip())
        except (ValueError, AttributeError):
            raise ValueError("Discovery targets must be local IPv4/broadcast addresses") from None
        if address.is_multicast or address.is_unspecified or not (address.is_private or address.is_loopback or address.is_link_local or str(address) == "255.255.255.255"):
            raise ValueError("Discovery targets must be local IPv4/broadcast addresses")
        result.append(str(address))
    if not 1 <= len(result) <= 8:
        raise ValueError("Provide 1–8 discovery targets")
    return result


def decode_device(data, source_ip, nonce, token=None):
    try:
        if len(data) > 2048:
            return None
        item = json.loads(data)
        if not isinstance(item, dict) or item.get("service") != SERVICE or type(item.get("protocol")) is not int or item.get("protocol") != 1 or item.get("kind") != "device" or item.get("nonce") != nonce:
            return None
        if not isinstance(item.get("device_id"), str) or not re.fullmatch(r"[a-f0-9]{32}", item["device_id"]):
            return None
        if type(item.get("port")) is not int or not 1024 <= item["port"] <= 65535 or item.get("address") != source_ip:
            return None
        if not isinstance(item.get("name"), str) or not 1 <= len(item["name"]) <= 64 or any(ord(c) < 32 for c in item["name"]):
            return None
        if not isinstance(item.get("version"), str) or not re.fullmatch(r"\d+\.\d+\.\d+", item["version"]):
            return None
        verified = False
        if token:
            message = "\n".join(("hermes-koreader/1", nonce, item["device_id"], source_ip, str(item["port"]), item["version"]))
            proof = hmac.new(token.encode(), message.encode(), hashlib.sha256).hexdigest()
            if not isinstance(item.get("proof"), str) or not hmac.compare_digest(proof, item["proof"]):
                return None
            verified = True
        return {"device_id": item["device_id"], "name": item["name"], "version": item["version"],
                "url": f"http://{source_ip}:{item['port']}", "pairing_open": item.get("pairing_open") is True, "verified": verified}
    except (ValueError, TypeError, UnicodeError):
        return None


def discover(timeout=2.0, targets=None, port=None, token=None):
    targets = discovery_targets(targets)
    port = int(port if port is not None else os.environ.get("KOBO_DISCOVERY_PORT", DISCOVERY_PORT))
    if not 1024 <= port <= 65535 or not 0 < timeout <= 5:
        raise ValueError("Invalid discovery port or timeout")
    nonce = secrets.token_hex(16)
    query = json.dumps({"service": SERVICE, "protocol": 1, "kind": "discover", "nonce": nonce}).encode()
    devices, deadline = {}, time.monotonic() + timeout
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as udp:
        udp.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)
        udp.bind(("0.0.0.0", 0))
        for target in targets:
            udp.sendto(query, (target, port))
        while time.monotonic() < deadline:
            udp.settimeout(max(0.001, deadline - time.monotonic()))
            try:
                data, source = udp.recvfrom(4096)
            except socket.timeout:
                break
            device = decode_device(data, source[0], nonce, token)
            if device:
                devices[(device["device_id"], device["url"])] = device
    return sorted(devices.values(), key=lambda item: (item["name"], item["device_id"], item["url"]))


def select_device(devices, device_id=None):
    choices = [device for device in devices if not device_id or device["device_id"] == device_id]
    if len(choices) != 1:
        if not choices:
            raise ValueError("No matching Kobo found. Start the bridge, check Wi-Fi/UDP 8676 and Docker networking, or use a manual KOBO_URL.")
        raise ValueError("Multiple Kobo endpoints found; select a device_id or use a manual URL. No token was sent.")
    return choices[0]


def resolve_connection(url=None, token=None):
    explicit_url = url if url is not None else os.environ.get("KOBO_URL")
    explicit_token = token if token is not None else os.environ.get("KOBO_TOKEN")
    profile = {} if explicit_url and explicit_url != "auto" and explicit_token else load_profile()
    secret = explicit_token or profile.get("token")
    if not valid_token(secret):
        raise ValueError("Pair the Kobo with --pair, or set KOBO_TOKEN to a 32–128 character shared token")
    if explicit_url and explicit_url != "auto":
        return checked_url(explicit_url), secret
    if not explicit_url and profile.get("mode") == "manual":
        return checked_url(profile["url"]), secret
    device_id = os.environ.get("KOBO_DEVICE_ID") or profile.get("device_id")
    key = (device_id, hashlib.sha256(secret.encode()).digest(), os.environ.get("KOBO_DISCOVERY_TARGETS"), os.environ.get("KOBO_DISCOVERY_PORT"))
    with _lock:
        cached = _cache.get(key)
        if cached and time.monotonic() < cached[0]:
            return cached[1], secret
    devices = discover(token=secret)
    selected = select_device(devices, device_id)
    with _lock:
        _cache[key] = (time.monotonic() + 30, selected["url"])
    return selected["url"], secret


def set_url(url, path=None):
    """Repoint a paired profile at a new address (or "auto"), keeping its token."""
    profile = load_profile(path)
    if not profile:
        raise ValueError("Pair the Kobo with --pair before changing its address")
    new_url = profile["url"] if url == "auto" else checked_url(url)
    profile["url"] = new_url
    profile["mode"] = "auto" if url == "auto" else "manual"
    save_profile(profile, path)
    return {"url": profile["url"], "mode": profile["mode"], "connection_file": str(path or profile_path())}


def recover(current_url, token):
    """Find a paired manual-mode device after its address changed.

    Returns the new URL, already persisted in the profile, or None when the
    device cannot be found unambiguously. Auto mode needs no recovery: it
    rediscovers on every call.
    """
    try:
        profile = load_profile()
    except ValueError:
        return None
    if profile.get("mode") != "manual" or profile.get("token") != token:
        return None
    try:
        devices = discover(token=token)
    except (ValueError, OSError):
        return None
    matches = [device for device in devices if device.get("device_id") == profile["device_id"]]
    if not matches:
        return None
    if len(matches) > 1:
        raise ValueError("Multiple Kobo endpoints found; set the correct one with --set-url")
    new_url = checked_url(matches[0]["url"])
    if new_url == checked_url(current_url):
        return None
    set_url(new_url)
    return new_url


def clear_discovery_cache():
    with _lock:
        _cache.clear()


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("Pairing endpoint redirected")


def pairing_request(url, payload):
    endpoint = checked_url(url) + "/v1/pair"
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())
    request = urllib.request.Request(endpoint, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json"})
    try:
        with opener.open(request, timeout=10) as response:
            raw = response.read(8193)
    except urllib.error.HTTPError:
        raise ValueError("Pairing refused. Open a fresh pairing window on the Kobo and enter its code; five wrong attempts close the window.") from None
    except (OSError, urllib.error.URLError):
        raise ValueError("Cannot reach the Kobo pairing endpoint") from None
    if len(raw) > 8192:
        raise ValueError("Invalid pairing response")
    result = json.loads(raw)
    if not isinstance(result, dict) or not isinstance(result.get("result"), dict):
        raise ValueError("Invalid pairing response")
    return result["result"]


def pair(url, code, client_name=None, device_id=None, timeout=125, on_pending=None, auto_discovery=True):
    nonce = secrets.token_hex(16)
    pending = pairing_request(url, {"action": "request", "code": code.strip(), "nonce": nonce,
                                    "client_name": client_name or socket.gethostname()[:64] or "NAS"})
    if device_id and pending.get("device_id") != device_id:
        raise ValueError("Pairing response came from a different device")
    if pending.get("state") != "pending" or not isinstance(pending.get("request_id"), str) or not re.fullmatch(r"[a-f0-9]{32}", pending["request_id"]):
        raise ValueError("Invalid pairing request response")
    if on_pending:
        on_pending()
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = pairing_request(url, {"action": "claim", "request_id": pending["request_id"], "nonce": nonce})
        if result.get("state") == "denied":
            raise ValueError("Pairing declined on the Kobo")
        if result.get("state") == "paired":
            if not valid_token(result.get("token")) or result.get("device_id") != pending.get("device_id") or not re.fullmatch(r"[a-f0-9]{32}", result.get("device_id", "")):
                raise ValueError("Invalid paired credentials")
            profile = {"schema_version": 1, "device_id": result["device_id"], "name": result.get("name", "Kobo"),
                       "token": result["token"], "url": checked_url(url), "mode": "auto" if auto_discovery else "manual",
                       "paired_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())}
            path = save_profile(profile)
            clear_discovery_cache()
            return {"device_id": profile["device_id"], "name": profile["name"], "connection_file": str(path), "token_saved": True, "mode": profile["mode"]}
        if result.get("state") != "pending":
            raise ValueError("Invalid pairing state")
        time.sleep(0.5)
    raise ValueError("Pairing approval timed out; open a fresh pairing window on the Kobo")
