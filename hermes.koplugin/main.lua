local WidgetContainer = require("ui/widget/container/widgetcontainer")
local UIManager = require("ui/uimanager")
local InfoMessage = require("ui/widget/infomessage")
local InputDialog = require("ui/widget/inputdialog")
local ConfirmBox = require("ui/widget/confirmbox")
local lfs = require("libs/libkoreader-lfs")

local Hermes = WidgetContainer:extend{ name = "hermes", is_doc_only = false }
-- Reader and file manager can create separate instances; allow only one listener.
local runtime = { server = nil }
local settings = G_reader_settings:readSetting("hermes_bridge", {
    port = 8675, host = "0.0.0.0", files_root = "/mnt/onboard", allow_writes = false, allow_plugins = false,
})

local function persist()
    G_reader_settings:saveSetting("hermes_bridge", settings)
    G_reader_settings:flush()
end

local function info(text) UIManager:show(InfoMessage:new{ text = text }) end

function Hermes:init()
    self.ui.menu:registerToMainMenu(self)
end

function Hermes:basePath()
    local base = self.path
    if base:sub(1, 1) ~= "/" then base = lfs.currentdir() .. "/" .. base end
    return base:gsub("/%./", "/"):gsub("/+$", "")
end

function Hermes:getUpdater()
    if not runtime.updater then
        local base = self:basePath()
        runtime.updater = dofile(base .. "/updater.lua").new(base, settings)
    end
    return runtime.updater
end

function Hermes:onlineAction(message, action)
    require("ui/network/manager"):runWhenOnline(function()
        local busy = InfoMessage:new{ text = message }
        UIManager:show(busy)
        UIManager:scheduleIn(0.1, function()
            local ok, err = pcall(action)
            UIManager:close(busy)
            if not ok then info("Update manager:\n" .. tostring(err)) end
        end)
    end)
end

function Hermes:installUpdate()
    self:onlineAction("Checking Hermes updates…", function()
        local updater = self:getUpdater()
        local check = updater:check()
        if not check.available then info("Kobo plugin is up to date: " .. check.installed_version); return end
        if check.preview and not settings.preview_updates then info("Enable Preview updates to install this release."); return end
        UIManager:show(ConfirmBox:new{
            text = "Install Hermes " .. check.latest_version .. (check.preview and " (preview)" or "")
                .. " on the Kobo? The bridge will stop. A backup is kept; restart KOReader afterward.",
            ok_callback = function()
                self:stop()
                self:onlineAction("Downloading and verifying the Kobo update…", function()
                    local prepared = updater:prepare(check.release)
                    updater:commit(prepared.transaction_id)
                    info("Hermes " .. check.latest_version .. " installed. Restart KOReader to load it. NAS files are updated through the whole-project updater.")
                end)
            end,
        })
    end)
end

function Hermes:requestProjectUpdate()
    if not settings.allow_project_updates then info("Enable Allow project updates in this menu first."); return end
    if not runtime.server then info("Start the bridge first so the NAS can receive the update request."); return end
    local pending = settings.project_update_request
    if pending and (pending.state == "queued" or pending.state == "working") then info("A project update is already " .. pending.state .. ". See Update status."); return end
    self:onlineAction("Checking the project release…", function()
        local check = self:getUpdater():check()
        if check.preview and not settings.preview_updates then info("Enable Preview updates first."); return end
        UIManager:show(ConfirmBox:new{
            text = "Request a whole-project update to " .. check.latest_version .. "? The NAS bridge must be running with HERMES_DEVICE_UPDATE_REQUESTS=1. This updates managed NAS files and the Kobo plugin; it keeps your settings. Restart Hermes and KOReader afterward.",
            ok_callback = function()
                settings.project_update_request = { id = tostring(os.time()) .. "-" .. tostring(math.floor(require("socket").gettime() * 1000) % 100000),
                    expected_version = check.latest_version, allow_preview = settings.preview_updates == true, state = "queued", message = "Waiting for the NAS bridge" }
                persist()
                info("Project update requested. Keep the bridge running and the Kobo awake. Check Update status for progress. If NAS request handling is disabled, ask Hermes to run hermes_update_project instead.")
            end,
        })
    end)
end

function Hermes:updateMenu()
    return {
        { text = "Update status", callback = function()
            local ok, status = pcall(function() return self:getUpdater():status() end)
            if not ok then info(tostring(status)); return end
            info("Running Kobo version: " .. status.running_version .. "\nInstalled: " .. status.installed_version
                .. "\nUpdate phase: " .. status.phase .. (status.restart_required and "\nRestart KOReader to load the installed version." or "")
                .. (status.request and ("\nWhole project: " .. status.request.state .. "\n" .. tostring(status.request.message or "")) or ""))
        end },
        { text = "Check for updates", callback = function()
            self:onlineAction("Checking Hermes updates…", function()
                local check = self:getUpdater():check()
                info("Installed: " .. check.installed_version .. "\nLatest: " .. check.latest_version
                    .. (check.preview and " (preview)" or "") .. (check.available and "\nAn update is available." or "\nNo newer Kobo version is available."))
            end)
        end },
        { text = "Install Kobo update", callback = function() self:installUpdate() end },
        { text = "Update whole project via NAS", callback = function() self:requestProjectUpdate() end },
        { text = "Cancel queued project request", callback = function()
            local request = settings.project_update_request
            if request and request.state == "queued" then request.state = "cancelled"; request.message = "Cancelled on Kobo"; persist()
            else info("Only a request that has not started can be cancelled here.") end
        end },
        { text = "Restore previous Kobo version", callback = function()
            local updater = self:getUpdater()
            local status = updater:status()
            if not status.rollback_available then info("No installed-update backup is available."); return end
            UIManager:show(ConfirmBox:new{
                text = "Restore the previous Kobo plugin? Restart KOReader afterward. To roll back the NAS files too, use hermes_rollback_project from Hermes.",
                ok_callback = function()
                    self:stop()
                    local ok, err = pcall(updater.rollback, updater, status.transaction_id)
                    info(ok and "Previous Kobo plugin restored. Restart KOReader." or tostring(err))
                end,
            })
        end },
        { text = "Allow project updates", checked_func = function() return settings.allow_project_updates == true end,
            callback = function() settings.allow_project_updates = not settings.allow_project_updates; persist() end },
        { text = "Preview updates", checked_func = function() return settings.preview_updates == true end,
            callback = function() settings.preview_updates = not settings.preview_updates; persist() end },
        { text = "Whole-project setup", callback = function()
            info("On the NAS, install the complete v2 kit with a writable project directory. In Hermes MCP env set HERMES_DEVICE_UPDATE_REQUESTS=1 to handle requests from this menu. Alternatively ask Hermes to check and update the whole project using its built-in tools. GitHub downloads require a public repository. A Docker mount of only bridge/ is insufficient for whole-project updates.")
        end },
    }
end

function Hermes:stop()
    if runtime.tick then UIManager:unschedule(runtime.tick); runtime.tick = nil end
    if runtime.server then runtime.server:stop(); runtime.server = nil end
end

function Hermes:start()
    if runtime.server then return end
    local ok, err = pcall(function()
        local updater = self:getUpdater()
        assert(not updater:status().restart_required, "Restart KOReader before starting the updated bridge")
        local base = self:basePath()
        local plugin_root = assert(base:match("^(.*)/[^/]+$"))
        local FS = dofile(base .. "/fs.lua")
        local API = dofile(base .. "/api.lua")
        local Server = dofile(base .. "/server.lua")
        local fs = FS.new{ files = settings.files_root, plugins = plugin_root }
        fs:root("files"); fs:root("plugins")
        local server = Server.new(API.new(fs, settings, G_reader_settings, updater), settings.token)
        local started, reason = server:start(settings.host or "0.0.0.0", settings.port or 8675)
        assert(started, reason)
        runtime.server = server
        runtime.tick = function()
            if not runtime.server then return end
            local success = pcall(runtime.server.step, runtime.server)
            if not success then self:stop(); info("Hermes bridge stopped after a server error."); return end
            UIManager:scheduleIn(0.05, runtime.tick)
        end
        UIManager:scheduleIn(0.05, runtime.tick)
    end)
    if not ok then self:stop(); info("Could not start Hermes bridge:\n" .. tostring(err)) end
end

function Hermes:edit(title, value, callback, secret)
    local dialog
    dialog = InputDialog:new{
        title = title, input = tostring(value or ""), text_type = secret and "password" or nil,
        buttons = {{
            { text = "Cancel", id = "close", callback = function() UIManager:close(dialog) end },
            { text = "Save", callback = function()
                local valid, err = callback(dialog:getInputText())
                if valid then self:stop(); persist(); UIManager:close(dialog)
                else info(err) end
            end },
        }},
    }
    UIManager:show(dialog)
    dialog:onShowKeyboard()
end

function Hermes:addToMainMenu(menu_items)
    menu_items.hermes_bridge = {
        text = "Hermes NAS bridge", sorting_hint = "more_tools",
        sub_item_table = {
            { text_func = function() return runtime.server and "Stop bridge" or "Start bridge" end,
                callback = function() if runtime.server then self:stop() else self:start() end end },
            { text = "Connection details", callback = function()
                info("On your NAS set KOBO_URL=http://<Kobo Wi-Fi IP>:" .. (settings.port or 8675)
                    .. "\nEndpoint: POST /v1/rpc\nStatus: " .. (runtime.server and "running" or "stopped")
                    .. "\nBooks/files: " .. settings.files_root
                    .. "\nFind the Kobo IP in KOReader's Network menu or your router. Keep Wi-Fi on and the Kobo awake. The bridge stops on suspend and USB storage mode.")
            end },
            { text = "Update manager", sub_item_table = self:updateMenu() },
            { text = "Set shared token", callback = function()
                self:edit("Token (32+ letters/digits/_/-; use the same token on your NAS)", settings.token, function(value)
                    if #value < 32 or #value > 128 or not value:match("^[A-Za-z0-9_-]+$") then return nil, "Use 32 to 128 letters, digits, _ or -." end
                    settings.token = value; return true
                end, true)
            end },
            { text = "Set port", callback = function()
                self:edit("Bridge port", settings.port or 8675, function(value)
                    local port = tonumber(value)
                    if not port or port % 1 ~= 0 or port < 1024 or port > 65535 then return nil, "Use a port from 1024 to 65535." end
                    settings.port = port; return true
                end)
            end },
            { text = "Set files directory", callback = function()
                self:edit("Absolute files directory (default /mnt/onboard)", settings.files_root, function(value)
                    if value:sub(1, 1) ~= "/" or value == "/" or value:find("[%z\1-\31]") or not lfs.attributes(value, "mode") or lfs.attributes(value, "mode") ~= "directory" then return nil, "Use an existing absolute directory." end
                    settings.files_root = value:gsub("/+$", ""); return true
                end)
            end },
            { text = "Allow file writes", checked_func = function() return settings.allow_writes == true end,
                callback = function() settings.allow_writes = not settings.allow_writes; persist() end },
            { text = "Allow plugin changes", checked_func = function() return settings.allow_plugins == true end,
                callback = function()
                    if settings.allow_plugins then settings.allow_plugins = false; persist(); return end
                    UIManager:show(ConfirmBox:new{
                        text = "Allow Hermes to install, edit and enable executable KOReader plugins? Changes take effect after a manual KOReader restart.",
                        ok_callback = function() settings.allow_plugins = true; persist() end,
                    })
                end },
        },
    }
end

-- A network/storage transition always needs an explicit Start bridge afterward.
function Hermes:onSuspend() self:stop() end
function Hermes:onEnterStandby() self:stop() end
function Hermes:onNetworkDisconnected() self:stop() end
-- KOReader broadcasts Close before entering Kobo USB mass storage.
function Hermes:onClose() self:stop() end
function Hermes:onExit() self:stop() end

return Hermes
