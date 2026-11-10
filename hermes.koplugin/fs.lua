-- All filesystem access stays inside named roots. Never execute remote code here.
local lfs = require("libs/libkoreader-lfs")
local FS = {}
FS.__index = FS
FS.MAX_FILE = 262144

local function check(ok, message)
    if not ok then error(message, 0) end
    return ok
end

local function attrs(path) return lfs.symlinkattributes(path) end

function FS.new(roots)
    return setmetatable({ roots = roots, sequence = 0 }, FS)
end

function FS:root(scope)
    local root = check(self.roots[scope], "Unknown scope")
    check(root:sub(1, 1) == "/" and root ~= "/", "Root must be an absolute directory")
    local p = ""
    for part in root:gmatch("[^/]+") do
        check(part ~= "." and part ~= "..", "Invalid root")
        p = p .. "/" .. part
        local a = attrs(p)
        check(a and a.mode == "directory", "Root missing or contains a symlink: " .. p)
    end
    return root:gsub("/+$", "")
end

function FS:resolve(scope, relative, allow_root)
    check(type(relative) == "string" and #relative <= 1024, "Invalid path")
    check(not relative:find("[%z\1-\31\127\\]") and relative:sub(1, 1) ~= "/", "Use a relative path")
    check(relative == "" or (relative:sub(-1) ~= "/" and not relative:find("//", 1, true)), "Invalid path separators")
    check(allow_root or relative ~= "", "Cannot modify a root")
    local path = self:root(scope)
    local first = true
    for part in relative:gmatch("[^/]+") do
        check(part:sub(1, 1) ~= "." and #part <= 240, "Hidden paths and dot traversal are blocked")
        if scope == "plugins" and first then
            check(part:match("^[%w_-]+%.koplugin$") and part ~= "hermes.koplugin", "Invalid or protected plugin")
        end
        first = false
        local parent = attrs(path)
        check(parent and parent.mode == "directory", "Parent directory does not exist")
        path = path .. "/" .. part
        local a = attrs(path)
        check(not a or a.mode ~= "link", "Symlinks are blocked")
        check(not a or a.mode == "file" or a.mode == "directory", "Special files are blocked")
    end
    if scope == "files" and relative ~= "" then
        local plugins = self.roots.plugins:gsub("/+$", "")
        check(path ~= plugins and path:sub(1, #plugins + 1) ~= plugins .. "/", "Use the plugins scope for plugin files")
        check(allow_root or plugins:sub(1, #path + 1) ~= path .. "/", "Cannot modify a directory containing plugins")
    end
    return path
end

function FS:read(scope, relative)
    local path = self:resolve(scope, relative)
    local a = attrs(path)
    check(a and a.mode == "file", "File not found")
    check(a.size <= self.MAX_FILE, "File exceeds the 256 KiB read/edit limit")
    local file = check(io.open(path, "rb"), "Cannot read file")
    local content = file:read(self.MAX_FILE + 1) or ""
    file:close()
    check(#content <= self.MAX_FILE, "File exceeds the read limit")
    return content
end

-- A revision guards against accidentally overwriting edits made since a read.
function FS.revision(content)
    local a, b = 1, 0
    for i = 1, #content do
        a = (a + content:byte(i)) % 65521
        b = (b + a) % 65521
    end
    return string.format("%d-%08x", #content, b * 65536 + a)
end

function FS:list(scope, relative, offset, limit)
    local path = self:resolve(scope, relative or "", true)
    check(attrs(path) and attrs(path).mode == "directory", "Directory not found")
    offset, limit = offset or 0, limit or 100
    check(type(offset) == "number" and offset >= 0 and offset % 1 == 0, "Invalid offset")
    check(type(limit) == "number" and limit >= 1 and limit <= 200 and limit % 1 == 0, "Limit must be 1..200")
    local names = {}
    for name in lfs.dir(path) do
        if name:sub(1, 1) ~= "." then
            names[#names + 1] = name
            check(#names <= 10000, "Directory too large; browse a subdirectory")
        end
    end
    table.sort(names)
    local entries = {}
    for i = offset + 1, math.min(#names, offset + limit) do
        local name = names[i]
        local a = attrs(path .. "/" .. name)
        if a then entries[#entries + 1] = { name = name, kind = a.mode, size = a.size, modified = a.modification } end
    end
    return { entries = entries, total = #names, offset = offset, next_offset = offset + limit < #names and offset + limit or false }
end

function FS:privateDir(scope)
    local path = self:root(scope) .. "/.hermes-recovery"
    local a = attrs(path)
    if not a then check(lfs.mkdir(path), "Cannot create recovery directory"); a = attrs(path) end
    check(a and a.mode == "directory", "Recovery path is not a real directory")
    return path
end

function FS:unique(parent, prefix)
    for _ = 1, 10000 do
        self.sequence = self.sequence + 1
        local id = prefix .. os.time() .. "-" .. self.sequence
        if not attrs(parent .. "/" .. id) then return parent .. "/" .. id, id end
    end
    error("Cannot allocate a unique filename", 0)
end

local function save(path, content)
    local f = check(io.open(path, "wb"), "Cannot create file")
    local ok = f:write(content)
    local closed = f:close()
    if not ok or not closed then os.remove(path); error("Write failed (storage full?)", 0) end
end

function FS:write(scope, relative, content, expected)
    check(type(content) == "string" and #content <= self.MAX_FILE, "File exceeds the 256 KiB write limit")
    local path = self:resolve(scope, relative)
    local a = attrs(path)
    local backup
    if a then
        check(a.mode == "file", "Destination is not a file")
        local old = self:read(scope, relative)
        check(expected == self.revision(old), "Revision conflict: read the file again before editing")
        local recovery = self:privateDir(scope)
        local backup_path, id = self:unique(recovery, "backup-")
        save(backup_path, old)
        backup = id
    else
        check(expected == "new", "Creation requires expected_revision=new")
    end
    local temp = self:unique(path:match("^(.*)/[^/]+$"), ".hermes-temp-")
    save(temp, content)
    local ok = os.rename(temp, path)
    if not ok then os.remove(temp); error("Atomic replacement failed", 0) end
    return { path = relative, revision = self.revision(content), backup_id = backup or false }
end

function FS:mkdir(scope, relative)
    local path = self:resolve(scope, relative)
    check(not attrs(path), "Destination already exists")
    check(lfs.mkdir(path), "Cannot create directory")
    return { path = relative }
end

function FS:move(scope, source, destination)
    local from, to = self:resolve(scope, source), self:resolve(scope, destination)
    check(attrs(from), "Source not found")
    check(not attrs(to), "Destination already exists")
    check(to:sub(1, #from + 1) ~= from .. "/", "Cannot move a directory into itself")
    check(os.rename(from, to), "Move failed")
    return { path = destination }
end

function FS:trash(scope, relative)
    local path = self:resolve(scope, relative)
    check(attrs(path), "Path not found")
    local recovery = self:privateDir(scope)
    local destination, id = self:unique(recovery, "trash-")
    check(os.rename(path, destination), "Could not move to recovery directory")
    return { recovery_id = id, original_path = relative }
end

function FS:recovery(scope)
    local dir = self:privateDir(scope)
    local result = {}
    for name in lfs.dir(dir) do
        if name:match("^backup%-%d+%-%d+$") or name:match("^trash%-%d+%-%d+$") then
            local a = attrs(dir .. "/" .. name)
            result[#result + 1] = { id = name, kind = a.mode, size = a.size }
            check(#result <= 1000, "Too many recovery items; clean up by USB")
        end
    end
    table.sort(result, function(a, b) return a.id < b.id end)
    return { entries = result }
end

function FS:restore(scope, id, destination)
    check(type(id) == "string" and (id:match("^backup%-%d+%-%d+$") or id:match("^trash%-%d+%-%d+$")), "Invalid recovery ID")
    local from = self:privateDir(scope) .. "/" .. id
    local a = attrs(from)
    check(a and (a.mode == "file" or a.mode == "directory"), "Recovery item not found or unsafe")
    local to = self:resolve(scope, destination)
    check(not attrs(to), "Restore destination already exists; choose another name")
    check(os.rename(from, to), "Restore failed")
    return { path = destination }
end

function FS:install(name, files, before_commit)
    check(type(name) == "string" and name:match("^[%w_-]+%.koplugin$"), "Use a name ending in .koplugin")
    local destination = self:resolve("plugins", name)
    check(not attrs(destination), "Plugin already exists; edit its files instead")
    check(type(files) == "table" and type(files["main.lua"]) == "string" and type(files["_meta.lua"]) == "string", "Provide main.lua and _meta.lua")
    local count, size = 0, 0
    for path, content in pairs(files) do
        check(type(path) == "string" and #path > 0 and #path <= 512 and path:sub(1, 1) ~= "/" and path:sub(-1) ~= "/" and not path:find("//", 1, true), "Invalid plugin file path")
        check(not path:find("[%z\1-\31\127\\]"), "Invalid plugin file path")
        for part in path:gmatch("[^/]+") do check(part:sub(1, 1) ~= "." and #part <= 240, "Hidden plugin file paths are blocked") end
        check(type(content) == "string", "Plugin contents must be strings")
        count, size = count + 1, size + #content
        check(count <= 64 and size <= self.MAX_FILE, "Install limit: 64 files and 256 KiB total")
        local parent = path:match("^(.*)/[^/]+$")
        while parent do
            check(files[parent] == nil, "A plugin file cannot also be a directory")
            parent = parent:match("^(.*)/[^/]+$")
        end
    end
    local stage = self:unique(self:privateDir("plugins"), "install-")
    check(lfs.mkdir(stage), "Cannot create staging directory")
    for path, content in pairs(files) do
        local current = stage
        local parent = path:match("^(.*)/[^/]+$")
        if parent then
            for part in parent:gmatch("[^/]+") do
                current = current .. "/" .. part
                if not attrs(current) then check(lfs.mkdir(current), "Cannot create plugin subdirectory") end
            end
        end
        save(stage .. "/" .. path, content)
    end
    if before_commit then before_commit() end
    check(os.rename(stage, destination), "Cannot commit staged plugin")
    return { plugin = name, disabled = true, restart_required = true }
end

return FS
