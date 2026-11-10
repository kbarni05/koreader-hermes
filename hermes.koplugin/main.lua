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

function Hermes:stop()
    if runtime.tick then UIManager:unschedule(runtime.tick); runtime.tick = nil end
    if runtime.server then runtime.server:stop(); runtime.server = nil end
end

function Hermes:start()
    if runtime.server then return end
    local ok, err = pcall(function()
        local base = self.path
        if base:sub(1, 1) ~= "/" then base = lfs.currentdir() .. "/" .. base end
        base = base:gsub("/%./", "/"):gsub("/+$", "")
        local plugin_root = assert(base:match("^(.*)/[^/]+$"))
        local FS = dofile(base .. "/fs.lua")
        local API = dofile(base .. "/api.lua")
        local Server = dofile(base .. "/server.lua")
        local fs = FS.new{ files = settings.files_root, plugins = plugin_root }
        fs:root("files"); fs:root("plugins")
        local server = Server.new(API.new(fs, settings, G_reader_settings), settings.token)
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
