-- Exercise real menu and lifecycle methods with lightweight KOReader UI doubles.
local project, root = assert(os.getenv("HERMES_PROJECT")), assert(os.getenv("HERMES_TEST_ROOT"))
package.path = project .. "/tests/vendor/?.lua;" .. package.path
package.preload["libs/libkoreader-lfs"] = function() return require("lfs") end
package.preload["json"] = function() return require("dkjson") end
package.preload["ffi/sha2"] = function() return require("koreader_sha2") end
local Widget = {}
function Widget:extend(o) o = o or {}; return setmetatable(o, { __index = self }) end
function Widget:new(o) return self:extend(o) end
function Widget:onShowKeyboard() end
local ui = { scheduled = {}, shown = {} }
function ui:scheduleIn(_, callback) self.scheduled[callback] = true end
function ui:unschedule(callback) self.scheduled[callback] = nil end
function ui:show(widget) self.shown[#self.shown + 1] = widget end
function ui:close() end
package.preload["ui/widget/container/widgetcontainer"] = function() return Widget end
package.preload["ui/uimanager"] = function() return ui end
for _, name in ipairs({ "infomessage", "inputdialog", "confirmbox" }) do
    package.preload["ui/widget/" .. name] = function() return Widget end
end
local settings = { token = "t" .. string.rep("a", 32), files_root = root .. "/files", port = 0 }
G_reader_settings = { data = { hermes_bridge = settings } }
function G_reader_settings:readSetting(k, default) return self.data[k] or default end
function G_reader_settings:saveSetting(k, v) self.data[k] = v end
function G_reader_settings:flush() end
function G_reader_settings:isTrue(k) return self.data[k] == true end
local main = dofile(project .. "/hermes.koplugin/main.lua")
local registered
local instance = main:new{ path = project .. "/hermes.koplugin", ui = { menu = { registerToMainMenu = function(_, value) registered = value end } } }
instance:init()
assert(registered == instance)
local menus = {}
instance:addToMainMenu(menus)
local menu = menus.hermes_bridge.sub_item_table
local function item(text)
    for _, entry in ipairs(menu) do if entry.text == text then return entry end end
    error("Missing menu item: " .. text)
end
assert(menu[1].text_func() == "Start bridge")
menu[1].callback()
assert(menu[1].text_func() == "Stop bridge")
assert(next(ui.scheduled))
-- A new reader instance must not create a duplicate listener.
instance:start()
local count = 0
for _ in pairs(ui.scheduled) do count = count + 1 end
assert(count == 1)
for _, event in ipairs({ "onClose", "onSuspend", "onEnterStandby", "onNetworkDisconnected", "onExit" }) do
    instance[event](instance)
    assert(menu[1].text_func() == "Start bridge")
    assert(not next(ui.scheduled))
    instance:start()
end
instance:stop()
item("Allow file writes").callback()
assert(settings.allow_writes == true)
item("Allow plugin changes").callback()
assert(not settings.allow_plugins)
ui.shown[#ui.shown].ok_callback()
assert(settings.allow_plugins == true)
item("Allow plugin changes").callback()
assert(settings.allow_plugins == false)
local updates = item("Update manager").sub_item_table
local update_items = {}
for _, entry in ipairs(updates) do update_items[entry.text] = entry end
assert(update_items["Update whole project via NAS"])
assert(not settings.allow_project_updates)
update_items["Allow project updates"].callback()
assert(settings.allow_project_updates)
update_items["Preview updates"].callback()
assert(settings.preview_updates)
-- Saving a new token must close the listening socket.
instance:start()
item("Set shared token").callback()
local dialog = ui.shown[#ui.shown]
function dialog:getInputText() return string.rep("b", 40) end
dialog.buttons[1][2].callback()
assert(settings.token == string.rep("b", 40))
assert(menu[1].text_func() == "Start bridge")
print("UI lifecycle OK")
