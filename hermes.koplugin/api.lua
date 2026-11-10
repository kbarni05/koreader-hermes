local mime = require("mime")
local API = {}
API.__index = API

function API.new(fs, settings, reader_settings, updater, connection)
    return setmetatable({ fs = fs, settings = settings, reader = reader_settings, updater = updater, connection = connection }, API)
end

local function need(ok, msg) if not ok then error(msg, 0) end end

function API:writePermission(scope)
    need(scope == "files" or scope == "plugins", "Unknown scope")
    need(scope == "plugins" and self.settings.allow_plugins == true or scope == "files" and self.settings.allow_writes == true,
        scope == "plugins" and "Enable plugin changes on the Kobo first" or "Enable file writes on the Kobo first")
end

function API:disable(name, disabled)
    local state = self.reader:readSetting("plugins_disabled", {})
    state[name:gsub("%.koplugin$", "")] = disabled and true or nil
    self.reader:saveSetting("plugins_disabled", state)
    self.reader:flush()
end

function API:call(request)
    need(type(request) == "table" and type(request.op) == "string", "Expected an operation object")
    local op, scope = request.op, request.scope or "files"
    local fs = self.fs
    if op == "status" then
        return { version = self.updater and self.updater.running_version or "2.5.0", roots = fs.roots, file_writes = self.settings.allow_writes == true,
            device = self.connection and self.connection:identity() or false,
            plugin_changes = self.settings.allow_plugins == true, max_file_bytes = fs.MAX_FILE,
            update_manager = self.updater and self.updater:status() or false,
            plugins_disable_external = self.reader:isTrue("plugins_disable_external") }
    elseif op:match("^update_") then
        need(self.updater, "Update manager unavailable")
        if op == "update_status" then return self.updater:status() end
        need(self.settings.allow_project_updates == true, "Enable project updates on the Kobo first")
        if op == "update_prepare" then return self.updater:prepare(request.release, request.files)
        elseif op == "update_commit" then return self.updater:commit(request.transaction_id)
        elseif op == "update_cancel" then return self.updater:cancel(request.transaction_id)
        elseif op == "update_rollback" then return self.updater:rollback(request.transaction_id)
        elseif op == "update_request_finish" then
            local pending = self.settings.project_update_request
            need(pending and pending.id == request.request_id, "Update request does not match")
            need(request.state == "done" or request.state == "error" or request.state == "working", "Invalid request state")
            need(request.state == "working" and pending.state == "queued" or request.state ~= "working" and pending.state == "working", "Update request was cancelled or already handled")
            pending.state, pending.message = request.state, tostring(request.message or ""):sub(1, 2048)
            self.reader:saveSetting("hermes_bridge", self.settings); self.reader:flush()
            return { ok = true }
        end
        error("Unknown update operation", 0)
    elseif op == "list" then
        return fs:list(scope, request.path, request.offset, request.limit)
    elseif op == "read" then
        local content = fs:read(scope, request.path)
        return { path = request.path, data_base64 = mime.b64(content) or "", revision = fs.revision(content), bytes = #content }
    elseif op == "plugins" then
        local result = fs:list("plugins", "", request.offset, request.limit)
        local disabled = self.reader:readSetting("plugins_disabled", {})
        for _, entry in ipairs(result.entries) do
            entry.disabled = disabled[entry.name:gsub("%.koplugin$", "")] == true
            entry.protected = entry.name == "hermes.koplugin"
        end
        result.restart_required_after_changes = true
        return result
    elseif op == "recovery" then
        self:writePermission(scope)
        return fs:recovery(scope)
    end
    if op == "install_plugin" then scope = "plugins" end
    self:writePermission(scope)
    if op == "write" then
        need(type(request.data_base64) == "string" and #request.data_base64 % 4 == 0 and not request.data_base64:find("[^A-Za-z0-9+/=]"), "Invalid base64 payload")
        local content = mime.unb64(request.data_base64) or ""
        need((mime.b64(content) or "") == request.data_base64, "Invalid base64 payload")
        return fs:write(scope, request.path, content, request.expected_revision)
    elseif op == "mkdir" then
        need(scope ~= "plugins", "Use install_plugin to create a plugin")
        return fs:mkdir(scope, request.path)
    elseif op == "move" then
        need(scope ~= "plugins", "Plugin renames are unsupported; install under a new name")
        return fs:move(scope, request.path, request.destination)
    elseif op == "trash" then
        return fs:trash(scope, request.path)
    elseif op == "restore" then
        if scope == "plugins" then
            local name = type(request.destination) == "string" and request.destination:match("^([^/]+)")
            need(name and name:match("^[%w_-]+%.koplugin$") and name ~= "hermes.koplugin", "Invalid plugin destination")
            self:disable(name, true)
        end
        return fs:restore(scope, request.recovery_id, request.destination)
    elseif op == "set_plugin_enabled" then
        need(scope == "plugins", "Use plugins scope")
        need(type(request.enabled) == "boolean", "enabled must be a boolean")
        need(type(request.path) == "string" and request.path:match("^[%w_-]+%.koplugin$"), "Invalid plugin name")
        fs:list("plugins", request.path, 0, 1) -- validates existence and protects this bridge
        self:disable(request.path, not request.enabled)
        return { plugin = request.path, enabled = request.enabled, restart_required = true }
    elseif op == "install_plugin" then
        need(type(request.name) == "string" and request.name:match("^[%w_-]+%.koplugin$") and request.name ~= "hermes.koplugin", "Invalid plugin name")
        -- New executable content is staged while disabled; enabling is a separate action.
        return fs:install(request.name, request.files, function() self:disable(request.name, true) end)
    end
    error("Unknown operation", 0)
end

return API
