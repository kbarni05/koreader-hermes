# Changelog

## 2.0.0 — Preview

- Built-in KOReader Update manager: version/status display, update checks, verified downloads, install, preview opt-in, and restore previous plugin.
- Whole-project NAS updater: update all release-managed files, not just the MCP script, and coordinate the matching Kobo plugin update.
- Hermes MCP tools: `hermes_check_updates`, `hermes_update_project`, and `hermes_rollback_project`.
- Optional NAS request handler: choose Update whole project via NAS on the Kobo while Hermes is running. No new NAS listening port is needed.
- SHA-256 and size verification, fixed repository/tag URLs, staged installs, syntax checks, version checks, backups, and explicit interrupted-update recovery.
- Keep tokens, reader settings, Hermes configuration, `.env`, books, and untracked files outside update operations. Refuse to overwrite locally edited managed NAS files.
- Generate ZenPM manifests, release history, versioned ZIPs, a stable plugin asset name, and a file-level project update feed from one release build.

One-time migration: install the v2 Kobo plugin and the complete v2 NAS kit manually or with ZenPM. V1 has no self-update endpoint. For Docker, mount the complete project writable rather than only the bridge directory.

Validation uses standalone Lua and Python, including real HTTP/MCP integration and simulated I/O failures. Physical Kobo/NAS validation is still required. The update feed and tagged source files must be publicly accessible.

## 1.0.0 — Initial preview

- Authenticated LAN bridge for Kobo files and KOReader plugins.
- NAS MCP client with file revisions, overwrite backups, and recoverable deletion.
- ZenPM repository metadata and preview release ZIPs.
