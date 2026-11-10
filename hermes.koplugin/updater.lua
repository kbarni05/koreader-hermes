-- Self-update is deliberately separate from general file/plugin operations.
local lfs = require("libs/libkoreader-lfs")
local json = require("json")
local mime = require("mime")
local Updater = {}
Updater.__index = Updater
Updater.FEED = "https://raw.githubusercontent.com/kbarni05/koreader-hermes/main/updates/latest.json"
Updater.REPOSITORY = "kbarni05/koreader-hermes"
Updater.MAX_FILE = 524288

local function need(ok, message) if not ok then error(message, 0) end; return ok end
local function attr(path) return lfs.symlinkattributes(path) end
local function read(path, limit)
    need(attr(path) and attr(path).mode == "file", "Unsafe or missing update file")
    local f = need(io.open(path, "rb"), "Cannot read " .. path)
    local body = f:read((limit or Updater.MAX_FILE) + 1) or ""
    f:close()
    need(#body <= (limit or Updater.MAX_FILE), "File exceeds update limit")
    return body
end
local function save(path, body)
    need(not attr(path) or attr(path).mode == "file", "Unsafe update output path")
    local f = need(io.open(path, "wb"), "Cannot write " .. path)
    local ok = f:write(body)
    local closed = f:close()
    need(ok and closed, "Update write failed (storage full?)")
end
local function directory(path)
    if not attr(path) then need(lfs.mkdir(path), "Cannot create update directory") end
    need(attr(path).mode == "directory", "Update directory contains a symlink or special file")
end
local function version(path)
    if not attr(path .. "/version.lua") then return "1.0.0" end
    need(attr(path .. "/version.lua").mode == "file", "Unsafe version file")
    return need(read(path .. "/version.lua", 100):match('^return "(%d+%.%d+%.%d+)"%s*$'), "Invalid version file")
end

function Updater.new(root, settings, fetch, hash, rename)
    need(root:sub(1, 1) == "/" and root:match("/hermes%.koplugin$"), "Invalid Hermes plugin directory")
    local path = ""
    for part in root:gmatch("[^/]+") do
        need(part ~= "." and part ~= "..", "Invalid update root")
        path = path .. "/" .. part
        need(attr(path) and attr(path).mode == "directory", "Update root contains a symlink or is missing")
    end
    return setmetatable({ root = root, settings = settings, running_version = version(root),
        fetch = fetch or Updater.fetchHTTPS, hash = hash or require("ffi/sha2").sha256,
        rename = rename or os.rename, home = root:match("^(.*)/[^/]+$") .. "/.hermes-updates" }, Updater)
end

function Updater.fetchHTTPS(url, limit)
    need(url:match("^https://raw%.githubusercontent%.com/kbarni05/koreader%-hermes/"), "Untrusted update URL")
    -- KOReader's LuaSec enables certificate AND hostname verification by default.
    local https, socketutil = require("ssl.https"), require("socketutil")
    local chunks, count = {}, 0
    socketutil:set_timeout(10, 20)
    local ok, result, code = pcall(https.request, {
        url = url, redirect = false, verify = "peer",
        headers = { ["User-Agent"] = "Hermes-KOReader-Updater/2" },
        sink = function(chunk)
            if chunk then
                count = count + #chunk
                if count > limit then return nil, "Update response too large" end
                chunks[#chunks + 1] = chunk
            end
            return 1
        end,
    })
    socketutil:reset_timeout()
    need(ok and result and tonumber(code) == 200, "Update download failed: " .. tostring(code or result))
    return table.concat(chunks)
end

function Updater.validate(release)
    need(type(release) == "table" and release.schema_version == 1 and release.repository == Updater.REPOSITORY, "Invalid update manifest")
    need(type(release.version) == "string" and release.version:match("^%d+%.%d+%.%d+$"), "Invalid release version")
    need(type(release.preview) == "boolean" and type(release.plugin) == "table" and #release.plugin >= 7 and #release.plugin <= 64, "Invalid plugin release")
    local seen, total = {}, 0
    for _, item in ipairs(release.plugin) do
        need(type(item) == "table" and type(item.path) == "string" and item.path:match("^[%w_-]+%.lua$") and not seen[item.path], "Invalid or duplicate update filename")
        need(type(item.size) == "number" and item.size >= 0 and item.size <= Updater.MAX_FILE and item.size % 1 == 0, "Invalid update size")
        need(type(item.sha256) == "string" and #item.sha256 == 64 and item.sha256:match("^[a-f0-9]+$"), "Invalid update checksum")
        local expected = "https://raw.githubusercontent.com/" .. Updater.REPOSITORY .. "/v" .. release.version .. "/hermes.koplugin/" .. item.path
        need(item.url == expected, "Update URL must reference the matching release tag")
        seen[item.path], total = true, total + item.size
        need(total <= 524288, "Plugin release exceeds 512 KiB")
    end
    for _, name in ipairs({ "main.lua", "_meta.lua", "api.lua", "fs.lua", "server.lua", "updater.lua", "version.lua" }) do
        need(seen[name], "Release is missing " .. name)
    end
    return release
end

local function newer(a, b)
    local aa, ab, ac = a:match("^(%d+)%.(%d+)%.(%d+)$")
    local ba, bb, bc = b:match("^(%d+)%.(%d+)%.(%d+)$")
    if tonumber(aa) ~= tonumber(ba) then return tonumber(aa) > tonumber(ba) end
    if tonumber(ab) ~= tonumber(bb) then return tonumber(ab) > tonumber(bb) end
    return tonumber(ac) > tonumber(bc)
end

function Updater:check()
    local release = self.validate(json.decode(self.fetch(self.FEED, 131072)))
    return { installed_version = version(self.root), running_version = self.running_version,
        latest_version = release.version, preview = release.preview,
        available = newer(release.version, version(self.root)), release = release }
end

function Updater:state()
    if not attr(self.home) then return nil end
    directory(self.home)
    local path = self.home .. "/state.json"
    if not attr(path) then return nil end
    need(attr(path).mode == "file", "Unsafe update journal")
    local state = json.decode(read(path, 16384))
    need(type(state) == "table" and type(state.id) == "string" and state.id:match("^%d+%-%d+$"), "Invalid update journal")
    local tx = self.home .. "/" .. state.id
    need(attr(tx) and attr(tx).mode == "directory", "Unsafe update transaction")
    -- Reconcile an interrupted journal write after the directory exchange.
    if attr(tx .. "/previous.koplugin") and not attr(tx .. "/next.koplugin") then state.phase = "installed" end
    return state
end

function Updater:journal(state)
    save(self.home .. "/state.tmp", json.encode(state))
    need(self.rename(self.home .. "/state.tmp", self.home .. "/state.json"), "Cannot save update journal")
end

function Updater:status()
    local state = self:state()
    local installed = version(self.root)
    return { running_version = self.running_version, installed_version = installed,
        restart_required = installed ~= self.running_version, allow_project_updates = self.settings.allow_project_updates == true,
        transaction_id = state and state.id or false, phase = state and state.phase or "idle",
        rollback_available = state and state.phase == "installed" or false,
        request = self.settings.project_update_request or false }
end

function Updater:prepare(release, bundle)
    self.validate(release)
    need(not release.preview or self.settings.preview_updates == true, "Enable Preview updates on the Kobo first")
    local old = self:state()
    need(not old or old.phase ~= "staged", "An update is already staged; cancel it first")
    need(newer(release.version, version(self.root)), "Only newer plugin versions can be installed")
    directory(self.home)
    local id, tx
    for n = 1, 10000 do
        id = tostring(os.time()) .. "-" .. n
        tx = self.home .. "/" .. id
        if not attr(tx) then break end
    end
    need(not attr(tx) and lfs.mkdir(tx), "Cannot create update transaction")
    directory(tx .. "/next.koplugin")
    local seen = {}
    for _, item in ipairs(release.plugin) do
        local body
        if bundle then
            need(type(bundle[item.path]) == "string", "Missing plugin update data")
            body = mime.unb64(bundle[item.path]) or ""
            need((mime.b64(body) or "") == bundle[item.path], "Invalid update encoding")
        else body = self.fetch(item.url, item.size + 1) end
        need(#body == item.size and self.hash(body) == item.sha256, "Checksum mismatch: " .. item.path)
        need((loadstring or load)(body, item.path), "Invalid Lua source: " .. item.path)
        if item.path == "version.lua" then need(body == 'return "' .. release.version .. '"\n', "Version file does not match release") end
        save(tx .. "/next.koplugin/" .. item.path, body)
        seen[item.path] = true
    end
    if bundle then for name in pairs(bundle) do need(seen[name], "Unexpected update file") end end
    save(tx .. "/release.json", json.encode(release))
    local state = { id = id, phase = "staged", version = release.version, previous_version = version(self.root),
        previous_state = old and old.phase == "installed" and old or nil }
    self:journal(state)
    return { transaction_id = id, version = release.version, phase = "staged" }
end

function Updater:commit(id)
    local state = need(self:state(), "No staged update")
    need(state.id == id and state.phase == "staged", "Update transaction does not match")
    local tx = self.home .. "/" .. id
    need(not attr(tx .. "/previous.koplugin"), "Update backup already exists")
    -- Verify staged data again so a changed file cannot be committed unnoticed.
    need(attr(tx .. "/next.koplugin") and attr(tx .. "/next.koplugin").mode == "directory", "Unsafe staged plugin")
    local release = self.validate(json.decode(read(tx .. "/release.json", 131072)))
    for _, item in ipairs(release.plugin) do
        local body = read(tx .. "/next.koplugin/" .. item.path)
        need(#body == item.size and self.hash(body) == item.sha256, "Staged checksum mismatch: " .. item.path)
    end
    local staged_version = version(tx .. "/next.koplugin")
    need(staged_version == state.version, "Staged version changed")
    need(version(self.root) == state.previous_version, "Installed version changed since staging")
    need(self.rename(self.root, tx .. "/previous.koplugin"), "Cannot back up current plugin")
    local installed = self.rename(tx .. "/next.koplugin", self.root)
    if not installed then
        need(self.rename(tx .. "/previous.koplugin", self.root), "Plugin exchange failed; restore previous.koplugin from the update transaction by USB")
        error("Plugin exchange failed; previous plugin restored", 0)
    end
    state.phase, state.previous_state = "installed", nil
    -- A journal failure is recoverable: state() derives installed from the backup.
    pcall(self.journal, self, state)
    return self:status()
end

function Updater:cancel(id)
    local state = need(self:state(), "No staged update")
    need(state.id == id and state.phase == "staged", "Update transaction does not match")
    state.phase = "cancelled"; self:journal(state.previous_state or state)
    return self:status()
end

function Updater:rollback(id)
    local state = need(self:state(), "No update backup")
    need(state.id == id and state.phase == "installed", "Rollback transaction does not match")
    local tx = self.home .. "/" .. id
    need(attr(tx .. "/previous.koplugin") and attr(tx .. "/previous.koplugin").mode == "directory", "Unsafe plugin backup")
    need(not attr(tx .. "/replaced.koplugin"), "Rollback already attempted")
    need(self.rename(self.root, tx .. "/replaced.koplugin"), "Cannot stage plugin rollback")
    if not self.rename(tx .. "/previous.koplugin", self.root) then
        need(self.rename(tx .. "/replaced.koplugin", self.root), "Rollback failed; restore replaced.koplugin by USB")
        error("Rollback failed; current plugin restored", 0)
    end
    state.phase = "rolled_back"; self:journal(state)
    return self:status()
end

return Updater
