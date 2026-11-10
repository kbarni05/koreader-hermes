#!/usr/bin/env python3
"""Dependency-free MCP stdio bridge from a Hermes NAS to KOReader."""
from __future__ import annotations

import argparse
import base64
import json
import os
import secrets
import sys
import urllib.error
import urllib.parse
import urllib.request

VERSION = "1.0.0"
MAX_MESSAGE = 2 * 1024 * 1024
MAX_FILE = 262144


def string(description, **extra):
    return {"type": "string", "description": description, **extra}


SCOPE = string("files for the configured book directory; plugins for KOReader plugins", enum=["files", "plugins"], default="files")
PATH = string("Relative path inside the selected scope; hidden paths and symlinks are blocked")
PAGING = {"offset": {"type": "integer", "minimum": 0, "default": 0}, "limit": {"type": "integer", "minimum": 1, "maximum": 200, "default": 100}}
ENCODING = string("utf8 for text, base64 for binary data", enum=["utf8", "base64"], default="utf8")


def tool(name, description, op, properties=None, required=()):
    return {"name": name, "description": description, "inputSchema": {
        "type": "object", "properties": properties or {}, "required": list(required), "additionalProperties": False,
    }, "_op": op}


TOOLS = [
    tool("kobo_status", "Check Kobo connectivity, roots, permissions, and file size limit.", "status"),
    tool("kobo_list_files", "Browse a directory on the Kobo. Empty path lists the root. Follow next_offset for more entries.", "list", {"scope": SCOPE, "path": {**PATH, "default": ""}, **PAGING}),
    tool("kobo_read_file", "Read up to 256 KiB. Save the returned revision to edit safely. Use base64 for books/binary files.", "read", {"scope": SCOPE, "path": PATH, "encoding": ENCODING}, ["path"]),
    tool("kobo_write_file", "Create or edit a file, up to 256 KiB. For creation expected_revision must be 'new'; for edits read first and supply its revision. Overwrites create recoverable backups. Device permission required.", "write", {
        "scope": SCOPE, "path": PATH, "content": string("Complete new contents"), "encoding": ENCODING,
        "expected_revision": string("'new' for creation, or the revision returned by kobo_read_file"),
    }, ["path", "content", "expected_revision"]),
    tool("kobo_create_directory", "Create one directory in files scope; parent must exist. Device file-write permission required.", "mkdir", {"path": PATH}, ["path"]),
    tool("kobo_move_file", "Move or rename a file/directory within files scope, including large books. Destination must not exist. Device file-write permission required.", "move", {"path": PATH, "destination": PATH}, ["path", "destination"]),
    tool("kobo_trash_file", "Move a file, directory, or plugin to recoverable storage. Does not free storage. Device write/plugin permission required. Plugin removal needs a manual KOReader restart.", "trash", {"scope": SCOPE, "path": PATH}, ["path"]),
    tool("kobo_list_recovery", "List backup/trash IDs in one scope. Device write/plugin permission required. Original paths are returned by the operation that created the ID.", "recovery", {"scope": SCOPE}),
    tool("kobo_restore_file", "Restore a backup or trashed item to a new, unoccupied relative path. Restored plugins are disabled until explicitly enabled. Device permission required.", "restore", {"scope": SCOPE, "recovery_id": string("ID returned by trash, write, or list_recovery"), "destination": PATH}, ["recovery_id", "destination"]),
    tool("kobo_list_plugins", "List the plugin directory and saved disabled flags. Flags take effect after a manual KOReader restart; this does not report live loaded state. Follow next_offset for more entries.", "plugins", PAGING),
    tool("kobo_install_plugin", "Install executable KOReader code as a NEW, DISABLED plugin. Maximum 64 UTF-8 files / 256 KiB combined; main.lua and _meta.lua required. Device plugin permission required. Enabling is separate and requires a manual restart.", "install_plugin", {
        "name": string("Directory name, e.g. myplugin.koplugin", pattern=r"^[A-Za-z0-9_-]+\.koplugin$"),
        "files": {"type": "object", "description": "Map of relative filenames to full UTF-8 text contents", "additionalProperties": {"type": "string"}, "maxProperties": 64},
    }, ["name", "files"]),
    tool("kobo_set_plugin_enabled", "Save a plugin's enabled/disabled setting. Device plugin permission required. Restart KOReader manually to apply. The Hermes bridge cannot modify itself.", "set_plugin_enabled", {
        "path": string("Plugin directory name, e.g. myplugin.koplugin"), "enabled": {"type": "boolean"},
    }, ["path", "enabled"]),
]
BY_NAME = {t["name"]: t for t in TOOLS}


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        raise ValueError("Kobo endpoint redirected; refusing to forward credentials")


class KoboClient:
    def __init__(self, url=None, token=None):
        url = url or os.environ.get("KOBO_URL", "")
        self.token = token or os.environ.get("KOBO_TOKEN", "")
        parsed = urllib.parse.urlsplit(url)
        if parsed.scheme not in ("http", "https") or not parsed.hostname or parsed.username or parsed.password or parsed.path not in ("", "/") or parsed.query or parsed.fragment:
            raise ValueError("Set KOBO_URL to http://<Kobo-IP>:8675 (no credentials or path)")
        if not 32 <= len(self.token) <= 128 or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789_-" for c in self.token):
            raise ValueError("Set KOBO_TOKEN to the 32–128 character token configured on the Kobo")
        self.endpoint = url.rstrip("/") + "/v1/rpc"
        # NAS proxy variables must not send the token to an HTTP proxy.
        self.opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), NoRedirect())

    def request(self, payload):
        data = json.dumps(payload, ensure_ascii=True, separators=(",", ":")).encode("utf-8")
        if len(data) > 786432:
            raise ValueError("Request exceeds the Kobo's 768 KiB transport limit")
        req = urllib.request.Request(self.endpoint, data=data, headers={"Authorization": "Bearer " + self.token, "Content-Type": "application/json"}, method="POST")
        try:
            with self.opener.open(req, timeout=35) as response:
                raw = response.read(MAX_MESSAGE + 1)
        except urllib.error.HTTPError as exc:
            raw = exc.read(8192)
            try:
                message = json.loads(raw).get("error", "HTTP error")
            except (ValueError, AttributeError):
                message = "Unexpected HTTP response"
            raise ValueError(f"Kobo HTTP {exc.code}: {message}") from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise ValueError("Cannot reach Kobo. Check Wi-Fi, IP/port, NAS routing, and Start bridge in KOReader. After a timeout, inspect the destination before retrying a write.") from None
        if len(raw) > MAX_MESSAGE:
            raise ValueError("Kobo response too large")
        result = json.loads(raw)
        if "result" not in result:
            raise ValueError("Unexpected Kobo response")
        return result["result"]


def validate_args(spec, args):
    if not isinstance(args, dict):
        raise ValueError("arguments must be an object")
    schema = spec["inputSchema"]
    if set(args) - set(schema["properties"]):
        raise ValueError("Unknown tool arguments")
    for key in schema["required"]:
        if key not in args:
            raise ValueError(f"Missing argument: {key}")
    for key, value in args.items():
        rule = schema["properties"][key]
        kind = rule["type"]
        valid = {"string": isinstance(value, str), "object": isinstance(value, dict), "boolean": type(value) is bool, "integer": type(value) is int}[kind]
        if not valid or ("enum" in rule and value not in rule["enum"]):
            raise ValueError(f"Invalid argument: {key}")
        if kind == "integer" and (value < rule.get("minimum", 0) or value > rule.get("maximum", 2**31 - 1)):
            raise ValueError(f"Argument out of range: {key}")


def call_tool(client, name, args):
    if name not in BY_NAME:
        raise ValueError("Unknown tool")
    spec = BY_NAME[name]
    validate_args(spec, args)
    payload = {"op": spec["_op"], **args}
    encoding = payload.pop("encoding", "utf8")
    if name == "kobo_set_plugin_enabled":
        payload["scope"] = "plugins"
    if name == "kobo_write_file":
        content = payload.pop("content")
        try:
            data = content.encode("utf-8") if encoding == "utf8" else base64.b64decode(content, validate=True)
        except (ValueError, UnicodeError):
            raise ValueError("Invalid file encoding") from None
        if len(data) > MAX_FILE:
            raise ValueError("Files are limited to 256 KiB; use USB for larger transfers")
        payload["data_base64"] = base64.b64encode(data).decode("ascii")
    if name == "kobo_install_plugin":
        files = args["files"]
        if not 2 <= len(files) <= 64 or not all(isinstance(v, str) for v in files.values()) or sum(len(v.encode("utf-8")) for v in files.values()) > MAX_FILE:
            raise ValueError("Plugin limit: 2–64 UTF-8 files and 256 KiB combined")
    result = client.request(payload)
    if name == "kobo_read_file":
        result = dict(result)
        data = base64.b64decode(result.pop("data_base64"), validate=True)
        try:
            result["content"] = data.decode("utf-8") if encoding == "utf8" else base64.b64encode(data).decode("ascii")
        except UnicodeError:
            raise ValueError("File is not UTF-8; read it again with encoding=base64") from None
        result["encoding"] = encoding
    return result


def handle(message, client_factory=KoboClient):
    if not isinstance(message, dict) or message.get("jsonrpc") != "2.0" or not isinstance(message.get("method"), str):
        return {"jsonrpc": "2.0", "id": None, "error": {"code": -32600, "message": "Invalid request"}}
    if "id" not in message:
        return None
    ident, method = message["id"], message["method"]
    params = message.get("params", {})
    if not isinstance(params, dict):
        return {"jsonrpc": "2.0", "id": ident, "error": {"code": -32602, "message": "params must be an object"}}
    if method == "initialize":
        offered = params.get("protocolVersion")
        protocol = offered if offered in ("2024-11-05", "2025-03-26", "2025-06-18", "2025-11-25") else "2024-11-05"
        result = {"protocolVersion": protocol, "capabilities": {"tools": {"listChanged": False}}, "serverInfo": {"name": "hermes-kobo", "version": VERSION}}
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        result = {"tools": [{k: v for k, v in t.items() if not k.startswith("_")} for t in TOOLS]}
    elif method == "tools/call":
        try:
            output = call_tool(client_factory(), params.get("name"), params.get("arguments", {}))
            result = {"content": [{"type": "text", "text": json.dumps(output, ensure_ascii=True)}], "isError": False}
        except (ValueError, TypeError, KeyError, OSError) as exc:
            result = {"content": [{"type": "text", "text": str(exc)}], "isError": True}
    else:
        return {"jsonrpc": "2.0", "id": ident, "error": {"code": -32601, "message": "Method not found"}}
    return {"jsonrpc": "2.0", "id": ident, "result": result}


def serve():
    while True:
        line = sys.stdin.buffer.readline(MAX_MESSAGE + 1)
        if not line:
            return
        if len(line) > MAX_MESSAGE:
            print("MCP input exceeds 2 MiB; closing", file=sys.stderr)
            return
        if not line.strip():
            continue
        try:
            message = json.loads(line)
        except (ValueError, UnicodeError):
            response = {"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}}
        else:
            response = handle(message)
        if response is not None:
            print(json.dumps(response, ensure_ascii=True, separators=(",", ":")), flush=True)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--generate-token", action="store_true", help="Generate a new shared token; no connection")
    parser.add_argument("--doctor", action="store_true", help="Check the configured Kobo connection")
    parser.add_argument("--call", choices=list(BY_NAME), help="Call one tool without an MCP client")
    parser.add_argument("--arguments", default="{}", help="JSON object for --call")
    args = parser.parse_args()
    try:
        if args.generate_token:
            print(secrets.token_urlsafe(32))
        elif args.doctor or args.call:
            result = call_tool(KoboClient(), args.call or "kobo_status", json.loads(args.arguments))
            print(json.dumps(result, ensure_ascii=True, indent=2))
        else:
            serve()
    except (ValueError, TypeError, KeyError, OSError) as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
