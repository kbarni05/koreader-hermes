local project, root = assert(os.getenv("HERMES_PROJECT")), assert(os.getenv("HERMES_TEST_ROOT"))
package.path = project .. "/tests/vendor/?.lua;" .. package.path
package.preload["libs/libkoreader-lfs"] = function() return require("lfs") end
package.preload["json"] = function() return require("dkjson") end
package.preload["ffi/sha2"] = function() return require("koreader_sha2") end
local json, sha = require("json"), require("ffi/sha2").sha256
assert(sha("") == "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855")
assert(sha("abc") == "ba7816bf8f01cfea414140de5dae2223b00361a396177a9cb410ff61f20015ad")
local function read(path) local f = assert(io.open(path, "rb")); local s = f:read("*a"); f:close(); return s end
local release = json.decode(read(root .. "/release.json"))
local files = json.decode(read(root .. "/bundle.json"))
local Updater = dofile(project .. "/hermes.koplugin/updater.lua")
local failed = false
local updater = Updater.new(root .. "/hermes.koplugin", { preview_updates = true }, nil, nil, function(from, to)
    if not failed and from:match("/next%.koplugin$") then failed = true; return nil, "simulated rename failure" end
    return os.rename(from, to)
end)
local prepared = updater:prepare(release, files)
local ok, err = pcall(updater.commit, updater, prepared.transaction_id)
assert(not ok and tostring(err):find("previous plugin restored", 1, true))
assert(updater:status().installed_version == "2.0.0")
updater:cancel(prepared.transaction_id)
prepared = updater:prepare(release, files)
updater:commit(prepared.transaction_id)
assert(updater:status().installed_version == release.version)
-- Reconcile a journal whose final write was lost after the directory swap.
local home = root .. "/.hermes-updates"
local state = json.decode(read(home .. "/state.json"))
state.phase = "staged"
local f = assert(io.open(home .. "/state.json", "wb")); f:write(json.encode(state)); f:close()
assert(updater:status().phase == "installed")
updater:rollback(prepared.transaction_id)
assert(updater:status().installed_version == "2.0.0")
print("Lua updater rollback and reconciliation OK")
