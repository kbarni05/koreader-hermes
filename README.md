# Hermes ↔ Kobo KOReader v2.5

Connect **Nous Research's Hermes Agent running on your NAS** to your Kobo over Wi-Fi. This project contains a KOReader plugin and a dependency-free Python MCP bridge that Hermes starts on the NAS.

```text
Hermes on NAS → MCP subprocess on NAS → authenticated HTTP over LAN → KOReader plugin → Kobo files
```

## What it does

- Browse directories, read/create/edit text or small binary files, create folders, rename/move files and folders.
- Back up overwritten files, move deleted items to recovery storage, and restore backups or deleted items.
- List plugins, install new plugins, edit plugin files, save enable/disable settings, and remove plugins to recovery storage.
- Keep file writes and plugin changes under separate switches on the Kobo. Both start disabled.
- Use the built-in Update manager to update the Kobo plugin or request a coordinated update of the complete NAS project and Kobo plugin, with backups and rollback.
- Discover the Kobo automatically on your LAN, generate its token on-device, and pair the NAS with a temporary code and Kobo approval.

## New in 2.5: automatic discovery and pairing

1. Install/update **both** the v2.5 Kobo plugin and the complete NAS kit, then restart KOReader and Hermes. Existing static IP/token settings continue to work, and existing tokens are preserved.
2. On the Kobo, open **Hermes NAS bridge → Connection setup → Pair with NAS (2 minutes)**. This starts the bridge if needed and shows a temporary code. A missing shared token is generated automatically from the operating system's secure random source.
3. On the NAS, in the project directory, run:

   ```sh
   python3 bridge/hermes_kobo_mcp.py --discover
   python3 bridge/hermes_kobo_mcp.py --pair
   ```

4. Enter the displayed Kobo code at the hidden prompt, then **approve the NAS on the Kobo screen**. The NAS stores the token in a private file with mode `600`; the command output does not print the token.
5. In Hermes's MCP entry, set `KOBO_URL: "auto"`. Remove the old `KOBO_TOKEN` entry when using the saved pairing profile, and make sure the MCP subprocess uses the same OS user and connection-file path as the pairing command. Restart Hermes, then run `--doctor` or ask Hermes to check the Kobo.

The default profile is `~/.local/state/hermes-kobo/connection.json` (or under `XDG_STATE_HOME`). Set `HERMES_CONNECTION_FILE` to an absolute path outside the project when you need an explicit/persistent location. The pairing command and MCP environment must use the **same path**. The profile is preserved by whole-project updates.

Use an interactive terminal for the hidden code prompt (`docker exec -it` in a container). A headless setup may supply the temporary code through `KOBO_PAIRING_CODE`; local Kobo approval is still required. Do not save the temporary code in the permanent MCP configuration.

Automatic discovery uses UDP **8676** on the local IPv4 network; the file/plugin API uses TCP **8675** by default. There is no subnet sweep. After pairing, the NAS verifies an HMAC proof bound to a fresh nonce, device ID, address, port and version before choosing an automatic endpoint. The long token is not included in UDP discovery messages. Discovery results shown before pairing are unverified; multiple matching endpoints require an explicit choice rather than sending the token to the first response.

Additional setup commands:

```sh
# Choose a particular Kobo if more than one is found.
python3 bridge/hermes_kobo_mcp.py --pair --device-id DEVICE_ID_FROM_DISCOVERY

# Supply an interface broadcast address if the default broadcast does not work.
python3 bridge/hermes_kobo_mcp.py --discover --discovery-target 192.168.1.255

# Pair directly when UDP/broadcast is blocked. This saves a manual URL profile.
python3 bridge/hermes_kobo_mcp.py --pair --url http://192.168.1.123:8675
```

For automatic discovery with a specific interface, set `KOBO_DISCOVERY_TARGETS` to a comma-separated list of local IPv4/broadcast addresses. Optional `KOBO_DEVICE_ID` pins a selection. The discovery port can be overridden on the NAS with `KOBO_DISCOVERY_PORT` for a deliberately forwarded setup; the Kobo normally listens on 8676. A manually paired URL can be forced into discovery mode later with `KOBO_URL=auto`.

The Kobo's **Connection setup** menu also provides **Generate / rotate token**, **Show shared token**, **Set device name**, **Automatic LAN discovery**, and **Close pairing window**. Token rotation stops the bridge and invalidates old saved credentials; pair the NAS again. Five wrong pairing codes close the current 2-minute window. The code alone does not release a token: local approval is required, and a successful claim can be used only once.

Auto mode caches a verified address for 30 seconds and discovers again on later calls after the cache expires or a connection failure. File writes are never replayed automatically after a timeout. Discovery and pairing use the same trusted-LAN model as the existing HTTP bridge; pairing is not an encrypted transport. The Kobo must remain awake with the bridge running. Suspending the Kobo closes discovery and pairing along with the bridge.

The plugin provides device tools; Hermes remains the assistant on the NAS. There is no chat UI on the Kobo, shell execution endpoint, or dependency on a particular model provider.

## V2 migration and built-in Update manager

**Install v2 once on both sides.** V1 does not contain an updater, so replace its `hermes.koplugin` folder using USB or ZenPM, and install the **complete v2 NAS kit** on the NAS. Keep the existing Kobo settings and Hermes configuration; use the same token and Kobo IP. Restart KOReader and Hermes after this initial migration.

### From the Kobo

Open **Hermes NAS bridge → Update manager**:

- **Update status** shows installed/running Kobo versions, restart requirements and the last whole-project request.
- **Check for updates** reads the official project feed over HTTPS.
- **Install Kobo update** downloads and verifies the entire plugin, stages it, and keeps the old folder. This action updates the Kobo side only.
- **Update whole project via NAS** queues a request for the running NAS bridge to update the complete managed NAS project and the matching Kobo plugin. Enable **Allow project updates** first, and enable **Preview updates** for preview releases. Keep the bridge running and the Kobo awake.
- **Restore previous Kobo version** uses the plugin backup. For a coordinated rollback of both sides, use the Hermes tool or NAS command below instead.

To enable requests from the Kobo menu, add `HERMES_DEVICE_UPDATE_REQUESTS` to the existing MCP environment. Keep your paired auto connection or your existing manual URL/token settings:

```yaml
env:
  KOBO_URL: "auto"
  HERMES_DEVICE_UPDATE_REQUESTS: "1"
```

Keep `command` and `args` as configured for your full v2 installation, and set the MCP `timeout` to `600` seconds. Restart Hermes to load the new environment. While the MCP subprocess is running it polls the Kobo every 15 seconds, without opening a NAS listening port. Check **Update status** on the Kobo for `queued`, `working`, `done` or `error`. Restart Hermes and KOReader only **after** the operation finishes. A restarted handler reconciles an interrupted request rather than blindly retrying it.

### From Hermes or the NAS

Hermes has three built-in project tools:

| Tool | Action |
| --- | --- |
| `hermes_check_updates` | Compare the official release with both installed components; returns a recovery transaction if one exists |
| `hermes_update_project` | Download/verify the complete release, stage both sides, install managed NAS files and commit the matching Kobo plugin |
| `hermes_rollback_project` | Restore managed NAS files and the matching device transaction from the saved backups |

For example, ask Hermes: “Check for project updates, including previews, and update both my NAS project and Kobo to the version you found.” The update tool requires the exact checked `expected_version`. The Kobo must permit project updates; previews also require the Kobo's Preview updates switch. General file/plugin write switches do not grant this updater permission.

Or, with a saved pairing profile or a manual URL/token configured on a Linux/Unix NAS:

```sh
python3 bridge/hermes_kobo_mcp.py --check-updates
python3 bridge/hermes_kobo_mcp.py --update-project --expected-version X.Y.Z
python3 bridge/hermes_kobo_mcp.py --rollback-project TRANSACTION_ID
```

Replace `X.Y.Z` with the checked release version. Add `--allow-preview` only when deliberately selecting a preview. Version `2.5.0` is a regular release and works without preview mode. The updater refuses downgrades, changed releases, checksum mismatches, malformed source and locally edited managed NAS files.

**Whole project** means the release-managed files: the NAS bridge and updater, plugin source, version files, documentation, test files and package metadata. It does not update Hermes Agent itself, KOReader, Kobo firmware, your books, credentials, or other plugins. `.env`, Hermes configuration, reader settings, `.git` and untracked local files are preserved. Move personal modifications out of managed files or reconcile them before updating.

### Update requirements and recovery

- The repository must be **public** for Kobo/ZenPM downloads and unauthenticated NAS update checks. A private GitHub browser session does not provide device download access. No GitHub password/token is stored on the Kobo.
- Downloads use the fixed official repository, HTTPS certificate/hostname verification in current KOReader, SHA-256 and size checks, and immutable release-tag URLs. Checksums detect corrupt/mismatched downloads; they are not an independent publisher signature.
- The complete NAS project directory must be writable. For Docker, mount the **whole project** at `/opt/koreader-hermes:rw`, and run `/opt/koreader-hermes/bridge/hermes_kobo_mcp.py`. A read-only mount or mounting only `bridge/` cannot update the whole project. The existing read-only bridge mount still supports ordinary file/plugin tools.
- NAS backups and the transaction journal are in `<project>/.hermes-updates/`. Kobo backups are in `<plugins>/.hermes-updates/<transaction>/previous.koplugin`. Keep them until you have restarted and verified the new version.
- If the NAS reports `device_commit_unknown`, reconnect the Kobo, inspect `hermes_check_updates`, and run `hermes_rollback_project` with the reported NAS transaction ID to restore both sides. A failed local install restores the old files; a refused device commit also restores the NAS files. If a process is interrupted during the NAS commit, the journal and backups support explicit rollback.
- Networked updates cannot be atomic across two machines. The updater reports uncertain outcomes and retains recovery data. A power loss between the two Kobo directory renames can leave the plugin absent; by USB, move the saved `previous.koplugin` back to `<plugins>/hermes.koplugin`. Do not power off, start another updater, or activate USB storage during an update.
- Cancelled/failed staging folders and old backups consume storage. Clean them over USB/on the NAS only when no update is active and you no longer need recovery. Use only one of ZenPM and the built-in updater at a time for this plugin.

See [CHANGELOG.md](CHANGELOG.md) for the v2 changes.

## New in 2.6: surviving an address change

A paired profile stores the device ID, so a Kobo that receives a different DHCP address does not need a new pairing code.

```sh
python3 bridge/hermes_kobo_mcp.py --set-url http://192.168.1.123:8675   # repoint, keep the token
python3 bridge/hermes_kobo_mcp.py --set-url auto                       # switch to discovery mode
```

A manual-mode profile also recovers by itself on the next **read** (`kobo_status`, `kobo_list_files`, `kobo_read_file`, `kobo_list_recovery`, `kobo_list_plugins`): the bridge rediscovers the stored device ID, checks the HMAC proof, retries that read once, and saves the new address. A write is never replayed after a connection failure — fix the address first, then rerun the write.

Discovery needs UDP broadcast, which a NAT-ed Docker bridge does not deliver. In that kind of container, repoint with `--set-url` and the device's current address, or reserve its address in DHCP; automatic recovery applies where the bridge shares the LAN directly.

## 1. Install on your Kobo

### Using ZenPM

This repository includes a [ZenPM native source index](manifest.json). **It must be publicly accessible before ZenPM can use it.** GitHub login in your browser does not grant ZenPM access to a private repository or its release downloads.

Once the repository is public, open **ZenPM → Sources → Add repository** and enter this base URL, including the trailing slash:

```text
https://raw.githubusercontent.com/kbarni05/koreader-hermes/main/
```

Refresh sources, find **Hermes NAS bridge**, and install `v2.5.0`. Beta updates are not required for this regular release. This unsigned source may show a `warn-unsigned` trust label. Restart KOReader afterward.

ZenPM installs only the Kobo plugin. Continue with **Set up the NAS bridge** below to connect Hermes, configure the shared token, and start the device bridge. Do not choose the complete NAS kit ZIP as a KOReader plugin package.

From the ZenPM command line, the source can be added with:

```sh
zenpm repo add kbarni05-hermes https://raw.githubusercontent.com/kbarni05/koreader-hermes/main/
zenpm repo refresh
```

The source selects the plugin-only `hermes.koplugin.zip` release asset. Its size and SHA-256 digest are recorded in [versions/hermes.json](versions/hermes.json). The release builder refreshes this metadata and the project update feed together.

### Using USB

1. Connect the Kobo by USB. Locate your KOReader installation, usually `.adds/koreader/` on the Kobo's storage. Your computer may hide `.adds` until you enable hidden files.
2. Copy the **entire `hermes.koplugin` folder** into KOReader's `plugins` directory:

   ```text
   .adds/koreader/plugins/hermes.koplugin/
       _meta.lua
       main.lua
       fs.lua
       api.lua
       server.lua
       updater.lua
       version.lua
       connection.lua
   ```

   With the plugin-only ZIP, extract it into `plugins/`. Avoid an extra nested `hermes.koplugin/hermes.koplugin/` folder.
3. Safely eject the Kobo and restart KOReader. Open **Tools → More tools → Hermes NAS bridge**. Menu placement can vary with KOReader version. If the plugin is disabled, enable it in Plugin management and restart.
4. Turn Wi-Fi on and use **Connection setup → Pair with NAS**. Find the Kobo's IP in Network information only if you need the manual connection fallback.

## 2. Set up the NAS bridge

Copy the complete project to the NAS. Python **3.9 or newer** on Linux/Unix is required; no pip packages are needed. Pair from the same OS user that runs Hermes:

```sh
python3 /absolute/path/to/koreader-hermes/bridge/hermes_kobo_mcp.py --pair
```

Open the pairing window on the Kobo first, enter its temporary code on the NAS, and approve on the Kobo. See the v2.5 section above for the profile location. Existing users can retain a manual `KOBO_URL` and `KOBO_TOKEN`; tokens can also still be generated with `--generate-token` and entered through **Set shared token**.

Open `~/.hermes/config.yaml` **on the NAS**, and merge this into the existing configuration, preserving any existing MCP servers:

```yaml
mcp_servers:
  kobo:
    command: python3
    args: ["/absolute/path/to/koreader-hermes/bridge/hermes_kobo_mcp.py"]
    env:
      KOBO_URL: "auto"
      # Optional: must match the path used by the pairing command.
      # HERMES_CONNECTION_FILE: "/absolute/private/path/connection.json"
      HERMES_DEVICE_UPDATE_REQUESTS: "1"
    timeout: 600
```

Replace the script path and use the same pairing profile as the CLI. A copy of this configuration is in [bridge/hermes-config.example.yaml](bridge/hermes-config.example.yaml). For manual mode, replace `auto` with the Kobo's URL and add its `KOBO_TOKEN` explicitly.

On the Kobo, select **Start bridge**. Restart Hermes or start a new Hermes process/session that reloads MCP configuration.

For a direct connection test from the same environment where Hermes runs:

```sh
export KOBO_URL='auto'
python3 /absolute/path/to/koreader-hermes/bridge/hermes_kobo_mcp.py --doctor
```

The result should show the configured roots and permission flags. `--doctor` only checks status.

## If Hermes runs in Docker on your NAS

The Python bridge and connection profile must exist **inside the Hermes container**, because Hermes launches the MCP subprocess there. Mount the complete project writable for project updates and a separate private state directory for credentials, using your real NAS paths:

```yaml
services:
  hermes:
    # Keep the existing image, configuration, and other mounts.
    network_mode: host
    volumes:
      - /your/nas/path/koreader-hermes:/opt/koreader-hermes:rw
      - /your/nas/path/hermes-kobo-state:/var/lib/hermes-kobo:rw
```

Use this path in the Hermes config that the container reads:

```yaml
mcp_servers:
  kobo:
    command: python3
    args: ["/opt/koreader-hermes/bridge/hermes_kobo_mcp.py"]
    env:
      KOBO_URL: "auto"
      HERMES_CONNECTION_FILE: "/var/lib/hermes-kobo/connection.json"
      HERMES_DEVICE_UPDATE_REQUESTS: "1"
    timeout: 600
```

On a Linux NAS, host networking lets broadcast discovery reach the Kobo. If your existing container setup requires Docker bridge networking instead, omit `network_mode: host` and use direct pairing with `--url` plus a manual `KOBO_URL`; plain Docker bridge networking usually does not pass LAN broadcasts. Keep your existing ports/network configuration appropriate for the rest of Hermes.

The container needs outbound TCP access to the Kobo's API and UDP reachability for auto discovery. There is no NAS listening port to publish for the MCP subprocess. Pair **inside** the container so the saved profile belongs to the right user and volume:

To test from inside the container (replace its name):

```sh
docker exec -it YOUR_HERMES_CONTAINER sh
export HERMES_CONNECTION_FILE='/var/lib/hermes-kobo/connection.json'
python3 /opt/koreader-hermes/bridge/hermes_kobo_mcp.py --pair
export KOBO_URL='auto'
python3 /opt/koreader-hermes/bridge/hermes_kobo_mcp.py --doctor
```

## 3. Let Hermes operate the Kobo

Try these prompts in Hermes:

> Check whether my Kobo is connected and list the folders on it.

> Create a Notes folder and write a reading-plan.txt file for this week.

> Read Notes/reading-plan.txt, then add a section for books I finish.

> List my KOReader plugins and disable demo.koplugin.

Before asking for file changes, enable **Allow file writes** on the Kobo. For installing, modifying, removing or enabling plugins, enable **Allow plugin changes** instead. Changes can be turned off while the bridge remains running.

For a new plugin, Hermes supplies a name such as `demo.koplugin` and a mapping of relative filenames to their text contents, including `main.lua` and `_meta.lua`. Files are staged before the new directory becomes visible. New plugins are disabled; explicitly enable the plugin and **restart KOReader manually** to apply. Existing loaded plugins are not hot reloaded. An existing plugin cannot be replaced by the install tool; edit its files or remove it first. Restoring a plugin disables it until explicitly enabled again.

The NAS bridge exposes these MCP tools:

| Tool | Purpose |
| --- | --- |
| `kobo_discover` | Find local bridges without credentials; results contain no token |
| `kobo_status` | Connection, roots, permissions and limits |
| `kobo_list_files` | Paginated directory listing |
| `kobo_read_file` | Read UTF-8 or base64 and obtain a revision |
| `kobo_write_file` | Create or replace a file with revision checking |
| `kobo_create_directory` | Create a folder in files scope |
| `kobo_move_file` | Rename/move files or folders in files scope |
| `kobo_trash_file` | Recoverable deletion in either scope |
| `kobo_list_recovery` | Find backup and trash IDs |
| `kobo_restore_file` | Restore an ID to an unoccupied destination |
| `kobo_list_plugins` | List plugin files and saved disabled settings |
| `kobo_install_plugin` | Install a new disabled plugin |
| `kobo_set_plugin_enabled` | Save an enabled/disabled setting |
| `hermes_check_updates` | Check updates for the complete project |
| `hermes_update_project` | Update NAS managed files and the Kobo plugin together |
| `hermes_rollback_project` | Restore a matching whole-project transaction |

## Scope, recovery and limits

- **Files root:** `/mnt/onboard` by default; change it in the plugin menu to a smaller existing directory if preferred. Paths in tool calls are relative to this root.
- **Plugins root:** the directory containing this plugin, usually `/mnt/onboard/.adds/koreader/plugins`. Use `scope: plugins` to read/edit other plugin files. Extra plugin search paths configured elsewhere in KOReader are not managed by this release.
- Hidden relative paths, traversal, symlink paths, special files, root deletion and changing the Hermes plugin through general file tools are blocked. The dedicated Update manager can replace this plugin with a verified release. File operations cannot enter the plugins root or move/delete an ancestor containing it.
- Reads, creates and edits are limited to **256 KiB per file**. A plugin install is limited to **64 UTF-8 files / 256 KiB combined**. Binary files can be transferred as base64 within that size limit. Use USB for full-size ebook transfers; moving/trashing existing large books has no content-size limit.
- Creation requires `expected_revision: "new"`. For an edit, first read the file and supply its returned revision. A stale revision is rejected. The revision is a content checksum for conflict detection, not a security hash.
- Successful overwrites return `backup_id`; deletions return `recovery_id` and `original_path`. Keep those results so you know which file to restore. Recovery files live in `.hermes-recovery` inside the corresponding root. `kobo_list_recovery` lists their IDs, and `kobo_restore_file` restores an ID to a **new, unoccupied** relative path.
- Backups and trash consume storage. This version has no permanent-delete tool; clean recovery storage over USB when you are satisfied with the changes. A failed plugin install can leave an `install-*` staging folder there, which can also be cleaned over USB.
- Replacements use a temporary file and rename, but there is no power-loss durability guarantee. Avoid unplugging/powering off the Kobo during writes.
- The bridge runs only while KOReader is active and the device is awake. It stops on suspend, standby, network disconnect, Close and exit events, including the Close event before USB storage. Closing a document may also stop the bridge. Start it again from the menu afterward. There is no unattended autostart.
- The device uses HTTP with a bearer token, **without TLS encryption**. Use it on a trusted private LAN; keep ports 8675/8676 off the public internet. The token is stored in KOReader settings and the private NAS pairing profile, or your legacy NAS configuration. For access beyond your LAN, use a private VPN/router arrangement with appropriate routing to the Kobo; broadcast discovery does not cross ordinary routed networks.
- Plugin code can do anything KOReader itself can do once loaded. Review generated plugin code before enabling it.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| Cannot reach Kobo | Keep it awake, turn on Wi-Fi, verify IP/port, select Start bridge; check guest-network/AP isolation and NAS/container routing |
| Discovery finds nothing | Check UDP 8676, the discovery switch, interface broadcast and guest/AP isolation; on Docker use host networking or manual `--pair --url` |
| More than one device found | Pair with `--device-id`; duplicate endpoints for the same ID require an explicit manual URL |
| Pairing expired or refused | Open a new pairing window, enter the code, and approve the NAS on Kobo; five wrong attempts close the window |
| CLI pairs but Hermes cannot connect | Use the same OS user/profile path inside Hermes; remove a stale `KOBO_TOKEN` override and persist the state volume |
| Token was rotated | Pair again; saved profiles using the old token cannot authenticate discovery or API calls |
| HTTP 401 | Tokens must match exactly; saving a token stops the bridge, so start it again |
| File/plugin writes refused | Turn on the corresponding permission in the Kobo menu |
| Revision conflict | Read the file again, reconcile changes, retry with the new revision |
| Plugin enable has no effect | Restart KOReader manually; check KOReader's global external-plugin restriction and the plugin's own disabled flag |
| Root contains a symlink | Configure an actual resolved directory rather than a symlink; symlink roots are deliberately refused |
| Plugin absent from menu | Check folder nesting, enable it in Plugin management, restart; inspect KOReader's `crash.log` for missing LuaSocket/JSON/LFS modules |
| Timeout after a write | Inspect the target before retrying; a lost response does not prove the write failed |
| Update request stays queued | Keep Hermes running with `HERMES_DEVICE_UPDATE_REQUESTS=1`, and keep the Kobo awake with the bridge started |
| Update download returns 404 | Make the GitHub repository public; check that the matching release tag and update feed have been published |
| Whole-project update rejects local edits | Back up/reconcile modified managed files; untracked personal configuration is preserved |
| Installed/running versions differ | The update is on disk; restart KOReader/Hermes after the operation completes |

The implementation was checked against upstream KOReader plugin, settings, UI and network APIs. It was tested with the real Lua filesystem/API/HTTP code and the Python MCP client on Linux. **It has not been tested on physical Kobo hardware or your NAS**, so start with a disposable text file.

## Development

Python runtime: Linux/Unix standard library only, including `fcntl` for process locking. KOReader runtime: its bundled `socket`, `mime`, `json`, `libs/libkoreader-lfs`, `ffi/sha2`, `ssl.https`, and `socketutil` modules. Use a current KOReader release for its HTTPS certificate and hostname verification patches.

Run the tests from the project directory:

```sh
python3 -m unittest discover -s tests -v
```

The standalone test harness requires Lua with LuaSocket and LuaFileSystem (`texlua` also works), or set `LUA_BIN` to a compatible interpreter. `tests/vendor/dkjson.lua` and `tests/vendor/koreader_sha2.lua` are MIT-licensed test-only dependencies; they are not included in the Kobo plugin ZIP. Tests bind a temporary loopback server and exercise the production Lua update/filesystem code, MCP subprocess and project updater. Update tests inject tagged file downloads and failure conditions; they do not contact a physical Kobo or external server.

Build ZIP releases:

```sh
python3 build_release.py
```

The builder reads `VERSION`, checks component versions, generates the ZenPM index/release history and `updates/latest.json`, and builds the plugin ZIP, stable-name plugin alias, complete NAS kit, and checksums. Preview is the default; use `--stable` only when deliberately publishing a stable release. Commit the generated metadata before publishing the matching `vX.Y.Z` tag. Do not rewrite an existing release tag or replace an artifact advertised by a committed checksum.

Public API references used: [KOReader plugin loader](https://github.com/koreader/koreader/blob/master/frontend/pluginloader.lua), [KOReader HTTP inspector](https://github.com/koreader/koreader/blob/master/plugins/httpinspector.koplugin/main.lua), [Hermes MCP configuration](https://github.com/NousResearch/hermes-agent/blob/main/cli-config.yaml.example).
