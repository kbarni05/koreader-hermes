# Hermes ↔ Kobo KOReader

Connect **Nous Research's Hermes Agent running on your NAS** to your Kobo over Wi-Fi. This project contains a KOReader plugin and a dependency-free Python MCP bridge that Hermes starts on the NAS.

```text
Hermes on NAS → MCP subprocess on NAS → authenticated HTTP over LAN → KOReader plugin → Kobo files
```

## What it does

- Browse directories, read/create/edit text or small binary files, create folders, rename/move files and folders.
- Back up overwritten files, move deleted items to recovery storage, and restore backups or deleted items.
- List plugins, install new plugins, edit plugin files, save enable/disable settings, and remove plugins to recovery storage.
- Keep file writes and plugin changes under separate switches on the Kobo. Both start disabled.

The plugin provides device tools; Hermes remains the assistant on the NAS. There is no chat UI on the Kobo, shell execution endpoint, or dependency on a particular model provider.

## 1. Install on your Kobo

### Using ZenPM

This repository includes a [ZenPM native source index](manifest.json). **It must be publicly accessible before ZenPM can use it.** GitHub login in your browser does not grant ZenPM access to a private repository or its release downloads.

Once the repository is public, open **ZenPM → Sources → Add repository** and enter this base URL, including the trailing slash:

```text
https://raw.githubusercontent.com/kbarni05/koreader-hermes/main/
```

Refresh sources and enable **Beta updates**, because the initial `v1.0.0` release is marked as a preview/prerelease. Find **Hermes NAS bridge** and install it, selecting `v1.0.0` if ZenPM offers a version picker. This unsigned source may show a `warn-unsigned` trust label. Restart KOReader afterward.

ZenPM installs only the Kobo plugin. Continue with **Set up the NAS bridge** below to connect Hermes, configure the shared token, and start the device bridge. Do not choose the complete NAS kit ZIP as a KOReader plugin package.

From the ZenPM command line, the source can be added with:

```sh
zenpm repo add kbarni05-hermes https://raw.githubusercontent.com/kbarni05/koreader-hermes/main/
zenpm repo refresh
```

The source points to the existing plugin-only ZIP, with its size and SHA-256 digest recorded in [versions/hermes.json](versions/hermes.json). Future releases need corresponding updates to the source asset name and versions metadata.

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
   ```

   With the plugin-only ZIP, extract it into `plugins/`. Avoid an extra nested `hermes.koplugin/hermes.koplugin/` folder.
3. Safely eject the Kobo and restart KOReader. Open **Tools → More tools → Hermes NAS bridge**. Menu placement can vary with KOReader version. If the plugin is disabled, enable it in Plugin management and restart.
4. Turn Wi-Fi on. Find the Kobo's IP in KOReader's Network information or your router. A DHCP reservation makes the address stable.

## 2. Set up the NAS bridge

Copy this project to the NAS. Python **3.9 or newer** is required; no pip packages are needed. On the NAS, generate a token:

```sh
python3 /absolute/path/to/koreader-hermes/bridge/hermes_kobo_mcp.py --generate-token
```

Keep that token private. On the Kobo, use **Hermes NAS bridge → Set shared token** and enter the same token. You can use KOReader's keyboard to paste it if your setup supports clipboard input. The allowed token format is 32–128 letters, digits, underscores or hyphens.

Open `~/.hermes/config.yaml` **on the NAS**, and merge this into the existing configuration, preserving any existing MCP servers:

```yaml
mcp_servers:
  kobo:
    command: python3
    args: ["/absolute/path/to/koreader-hermes/bridge/hermes_kobo_mcp.py"]
    env:
      KOBO_URL: "http://192.168.1.123:8675"
      KOBO_TOKEN: "YOUR_GENERATED_TOKEN"
    timeout: 45
```

Replace the path, IP address and token. A copy of this configuration is in [bridge/hermes-config.example.yaml](bridge/hermes-config.example.yaml). Protect your Hermes configuration file from other NAS users.

On the Kobo, select **Start bridge**. Restart Hermes or start a new Hermes process/session that reloads MCP configuration.

For a direct connection test from the same environment where Hermes runs:

```sh
export KOBO_URL='http://192.168.1.123:8675'
export KOBO_TOKEN='YOUR_GENERATED_TOKEN'
python3 /absolute/path/to/koreader-hermes/bridge/hermes_kobo_mcp.py --doctor
```

The result should show the configured roots and permission flags. `--doctor` only checks status.

## If Hermes runs in Docker on your NAS

The Python bridge must exist **inside the Hermes container**, because Hermes launches it as an MCP subprocess. Add a read-only bind mount to your existing Hermes service, using the real NAS path:

```yaml
services:
  hermes:
    # Keep the existing image, configuration, and other mounts.
    volumes:
      - /your/nas/path/koreader-hermes/bridge:/opt/kobo-bridge:ro
```

Use this path in the Hermes config that the container reads:

```yaml
mcp_servers:
  kobo:
    command: python3
    args: ["/opt/kobo-bridge/hermes_kobo_mcp.py"]
    env:
      KOBO_URL: "http://192.168.1.123:8675"
      KOBO_TOKEN: "YOUR_GENERATED_TOKEN"
    timeout: 45
```

Apply the updated container configuration, then restart Hermes. The container needs Python 3.9+ and outbound TCP access to the Kobo IP on port 8675. There is no NAS listening port to publish for this MCP subprocess. Use the **Kobo's LAN IP**, not `localhost`, in `KOBO_URL`.

To test from inside the container (replace its name):

```sh
docker exec -it YOUR_HERMES_CONTAINER sh
export KOBO_URL='http://192.168.1.123:8675'
export KOBO_TOKEN='YOUR_GENERATED_TOKEN'
python3 /opt/kobo-bridge/hermes_kobo_mcp.py --doctor
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

## Scope, recovery and limits

- **Files root:** `/mnt/onboard` by default; change it in the plugin menu to a smaller existing directory if preferred. Paths in tool calls are relative to this root.
- **Plugins root:** the directory containing this plugin, usually `/mnt/onboard/.adds/koreader/plugins`. Use `scope: plugins` to read/edit other plugin files. Extra plugin search paths configured elsewhere in KOReader are not managed by this release.
- Hidden relative paths, traversal, symlink paths, special files, root deletion and changing the Hermes plugin itself are blocked. File operations cannot enter the plugins root or move/delete an ancestor containing it.
- Reads, creates and edits are limited to **256 KiB per file**. A plugin install is limited to **64 UTF-8 files / 256 KiB combined**. Binary files can be transferred as base64 within that size limit. Use USB for full-size ebook transfers; moving/trashing existing large books has no content-size limit.
- Creation requires `expected_revision: "new"`. For an edit, first read the file and supply its returned revision. A stale revision is rejected. The revision is a content checksum for conflict detection, not a security hash.
- Successful overwrites return `backup_id`; deletions return `recovery_id` and `original_path`. Keep those results so you know which file to restore. Recovery files live in `.hermes-recovery` inside the corresponding root. `kobo_list_recovery` lists their IDs, and `kobo_restore_file` restores an ID to a **new, unoccupied** relative path.
- Backups and trash consume storage. This version has no permanent-delete tool; clean recovery storage over USB when you are satisfied with the changes. A failed plugin install can leave an `install-*` staging folder there, which can also be cleaned over USB.
- Replacements use a temporary file and rename, but there is no power-loss durability guarantee. Avoid unplugging/powering off the Kobo during writes.
- The bridge runs only while KOReader is active and the device is awake. It stops on suspend, standby, network disconnect, Close and exit events, including the Close event before USB storage. Closing a document may also stop the bridge. Start it again from the menu afterward. There is no unattended autostart.
- The device uses HTTP with a bearer token, **without TLS encryption**. Use it on a trusted private LAN; keep port 8675 off the public internet. The token is stored in KOReader settings and the NAS configuration. For access beyond your LAN, use a private VPN/router arrangement with appropriate routing to the Kobo.
- Plugin code can do anything KOReader itself can do once loaded. Review generated plugin code before enabling it.

## Troubleshooting

| Symptom | Check |
| --- | --- |
| Cannot reach Kobo | Keep it awake, turn on Wi-Fi, verify IP/port, select Start bridge; check guest-network/AP isolation and NAS/container routing |
| HTTP 401 | Tokens must match exactly; saving a token stops the bridge, so start it again |
| File/plugin writes refused | Turn on the corresponding permission in the Kobo menu |
| Revision conflict | Read the file again, reconcile changes, retry with the new revision |
| Plugin enable has no effect | Restart KOReader manually; check KOReader's global external-plugin restriction and the plugin's own disabled flag |
| Root contains a symlink | Configure an actual resolved directory rather than a symlink; symlink roots are deliberately refused |
| Plugin absent from menu | Check folder nesting, enable it in Plugin management, restart; inspect KOReader's `crash.log` for missing LuaSocket/JSON/LFS modules |
| Timeout after a write | Inspect the target before retrying; a lost response does not prove the write failed |

The implementation was checked against upstream KOReader plugin, settings, UI and network APIs. It was tested with the real Lua filesystem/API/HTTP code and the Python MCP client on Linux. **It has not been tested on physical Kobo hardware or your NAS**, so start with a disposable text file.

## Development

Python runtime: standard library only. KOReader runtime: its bundled `socket`, `mime`, `json`, and `libs/libkoreader-lfs` modules.

Run the tests from the project directory:

```sh
python3 -m unittest discover -s tests -v
```

The standalone test harness requires Lua with LuaSocket and LuaFileSystem (`texlua` also works), or set `LUA_BIN` to a compatible interpreter. `tests/vendor/dkjson.lua` is an MIT-licensed test-only dependency; it is not included in the Kobo plugin ZIP. Tests bind a temporary loopback server, exercise the production Lua code and MCP subprocess, and do not contact a Kobo or external server.

Build ZIP releases:

```sh
python3 build_release.py
```

Public API references used: [KOReader plugin loader](https://github.com/koreader/koreader/blob/master/frontend/pluginloader.lua), [KOReader HTTP inspector](https://github.com/koreader/koreader/blob/master/plugins/httpinspector.koplugin/main.lua), [Hermes MCP configuration](https://github.com/NousResearch/hermes-agent/blob/main/cli-config.yaml.example).
