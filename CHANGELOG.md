# Changelog

## 2.6.0 — Address changes without re-pairing

- `--set-url URL` repoints an existing paired profile at a new Kobo address, or `auto`, and keeps the token and device ID, so an address change no longer needs a fresh pairing code.
- A read-only call that cannot reach a paired manual endpoint rediscovers the stored device ID with the verified HMAC proof, retries that read once, and saves the new address in the profile.
- Writes are still never replayed after a connection failure; the error names `--set-url` so the address is fixed deliberately before a write is retried.
- A profile is validated before it is rewritten: an empty or invalid profile is refused instead of being replaced by a token-less entry.

## 2.5.0 — Discovery and pairing

- Automatic IPv4 LAN discovery on UDP 8676, persistent device IDs and friendly names; no subnet scanning and no bearer tokens in discovery packets.
- Paired discovery validates an HMAC proof bound to nonce, device ID, source address, port and version before automatic endpoint selection.
- Secure token generation on the Kobo, explicit token rotation, and preservation of existing tokens during migration.
- Two-minute pairing windows with a temporary code, mandatory approval on the Kobo, five-attempt lockout and single-use token claims.
- Private NAS connection profiles outside the project, automatic connection mode, cached discovery and rediscovery on later calls; mutations are never replayed automatically.
- `--discover`, `--pair`, explicit device selection, manual URL fallback and a credential-free `kobo_discover` MCP tool.
- Existing static URL/token settings, whole-project updates, rollback and ZenPM remain supported. Includes NAS/Docker profile and network instructions.

## 2.0.1 — Release download compatibility

- Publish a regular GitHub release marked Latest, with attached plugin and NAS kit ZIPs.
- Advertise the regular release in ZenPM and the built-in update feed, so beta/preview mode is not required.
- Keep the original v2.0.0 preview tag and downloads unchanged.
- Physical Kobo/NAS testing is still needed; this release changes distribution metadata and component version labels.

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
