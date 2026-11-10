-- Standalone harness: uses the real production FS/API/HTTP implementation.
local project = assert(os.getenv("HERMES_PROJECT"))
local root = assert(os.getenv("HERMES_TEST_ROOT"))
package.path = project .. "/tests/vendor/?.lua;" .. package.path
package.preload["libs/libkoreader-lfs"] = function() return require("lfs") end
package.preload["json"] = function() return require("dkjson") end
package.preload["ffi/sha2"] = function() return require("koreader_sha2") end
local lfs = require("lfs")
for name in lfs.dir(project .. "/hermes.koplugin") do
    if name:match("%.lua$") then assert(loadfile(project .. "/hermes.koplugin/" .. name)) end
end
local FS = dofile(project .. "/hermes.koplugin/fs.lua")
local API = dofile(project .. "/hermes.koplugin/api.lua")
local Updater = dofile(project .. "/hermes.koplugin/updater.lua")
local Server = dofile(project .. "/hermes.koplugin/server.lua")
local Connection = dofile(project .. "/hermes.koplugin/connection.lua")
local reader = { data = {} }
function reader:readSetting(key, default) return self.data[key] or default end
function reader:saveSetting(key, value) self.data[key] = value end
function reader:flush() end
function reader:isTrue(key) return self.data[key] == true end
local settings = { allow_writes = true, allow_plugins = true, allow_project_updates = true, preview_updates = true,
    token = assert(os.getenv("KOBO_TOKEN")), device_id = string.rep("d", 32), device_name = "Test Kobo" }
local fs = FS.new{ files = root .. "/files", plugins = root .. "/plugins" }
local updater = Updater.new(root .. "/plugins/hermes.koplugin", settings)
local pending_approval, clock_offset = nil, 0
local connection = Connection.new(settings, function() end, function(_, callback) pending_approval = callback end,
    { discovery_port = 0, version = updater.running_version, clock = function() return require("socket").gettime() + clock_offset end })
local api = API.new(fs, settings, reader, updater, connection)
-- Test-only permission control, absent from the shipped plugin.
local call = api.call
function api:call(request)
    if request.op == "__test_connection" then
        if request.action == "open" then clock_offset = 0; return { code = connection:openPairing() } end
        if request.action == "approve" then if pending_approval then pending_approval(request.accepted); pending_approval = nil end; return { ok = true } end
        if request.action == "expire" then clock_offset = clock_offset + 121; return { ok = true } end
        if request.action == "close" then connection:closePairing(); clock_offset = 0; return { ok = true } end
        return { discovery_port = connection.discovery_port, device_id = settings.device_id }
    end
    if request.op == "__test_request" then settings.project_update_request = request.value; return { ok = true } end
    if request.op == "__test_permissions" then
        settings.allow_writes = request.writes == true
        settings.allow_plugins = request.plugins == true
        settings.allow_project_updates = request.updates ~= false
        settings.preview_updates = request.previews ~= false
        return { ok = true }
    end
    return call(self, request)
end
local server = Server.new(api, assert(os.getenv("KOBO_TOKEN")), connection)
assert(server:start("127.0.0.1", 0))
local _, port = server.listener:getsockname()
print(port)
io.stdout:flush()
while true do server:step(); require("socket").sleep(0.002) end
