-- LAN identity, authenticated discovery, and short-lived on-screen pairing.
local socket = require("socket")
local json = require("json")
local sha = require("ffi/sha2")
local Connection = {}
Connection.__index = Connection
Connection.SERVICE = "hermes-koreader"
Connection.DISCOVERY_PORT = 8676

function Connection.randomHex(bytes)
    local f = assert(io.open("/dev/urandom", "rb"), "Secure random source unavailable")
    local data = f:read(bytes)
    f:close()
    assert(data and #data == bytes, "Secure random source failed")
    return (data:gsub(".", function(c) return string.format("%02x", c:byte()) end))
end

function Connection.new(settings, persist, approve, options)
    options = options or {}
    local self = setmetatable({ settings = settings, persist = persist, approve = approve,
        clock = options.clock or socket.gettime, random = options.random or Connection.randomHex,
        discovery_port = options.discovery_port or Connection.DISCOVERY_PORT,
        version = options.version or "2.5.0", packets = 0, packet_window = 0 }, Connection)
    local changed = false
    if not settings.token then settings.token = self.random(32); changed = true end
    if not settings.device_id then settings.device_id = self.random(16); changed = true end
    if not settings.device_name then settings.device_name = "Kobo KOReader"; changed = true end
    if changed then persist() end
    return self
end

function Connection:identity()
    return { device_id = self.settings.device_id, name = self.settings.device_name,
        version = self.version, port = self.tcp_port, pairing_open = self.session ~= nil and self.clock() < self.session.expires }
end

function Connection:openPairing()
    local code = self.random(6):upper()
    self.session = { code = code, expires = self.clock() + 120, failures = 0 }
    return code
end

function Connection:closePairing() self.session = nil end

function Connection:pair(request)
    local session = self.session
    if not session or self.clock() >= session.expires then self.session = nil; error("Pairing closed or expired", 0) end
    if type(request) ~= "table" then error("Invalid pairing request", 0) end
    if request.action == "request" then
        if type(request.code) ~= "string" or request.code:upper():gsub("%-", "") ~= session.code then
            session.failures = session.failures + 1
            if session.failures >= 5 then self.session = nil end
            error("Pairing code invalid or unavailable", 0)
        end
        if type(request.nonce) ~= "string" or #request.nonce ~= 32 or not request.nonce:match("^[a-f0-9]+$") then error("Invalid pairing nonce", 0) end
        if session.request_id then
            if request.nonce ~= session.nonce then error("Another pairing request is pending", 0) end
        else
            if type(request.client_name) ~= "string" or #request.client_name < 1 or #request.client_name > 64 or request.client_name:find("[%z\1-\31\127]") then error("Invalid NAS name", 0) end
            session.request_id, session.nonce = self.random(16), request.nonce
            self.approve(request.client_name, function(accepted)
                if self.session == session and self.clock() < session.expires then
                    session.approved, session.denied = accepted == true, accepted ~= true
                    return true
                end
                return false
            end)
        end
        return { state = "pending", request_id = session.request_id, device_id = self.settings.device_id, name = self.settings.device_name }
    elseif request.action == "claim" then
        if not session.request_id or request.request_id ~= session.request_id or request.nonce ~= session.nonce then error("Invalid pairing claim", 0) end
        if session.denied then self.session = nil; return { state = "denied" } end
        if not session.approved then return { state = "pending" } end
        self.session = nil
        return { state = "paired", token = self.settings.token, device_id = self.settings.device_id, name = self.settings.device_name,
            version = self.version, discovery_port = self.discovery_port }
    end
    error("Invalid pairing action", 0)
end

local function localAddress(peer, port)
    local probe = socket.udp()
    if not probe then return nil end
    local ok = probe:setpeername(peer, port)
    local address = ok and probe:getsockname() or nil
    probe:close()
    return address
end

function Connection:startDiscovery(host, tcp_port)
    self.tcp_port = tcp_port
    if self.settings.discovery_enabled == false then return true end
    local udp, err = socket.udp()
    if not udp then return nil, err end
    local ok
    ok, err = udp:setsockname(host, self.discovery_port)
    if not ok then udp:close(); return nil, err end
    udp:settimeout(0)
    self.udp = udp
    local _, actual_port = udp:getsockname()
    self.discovery_port = tonumber(actual_port)
    return true
end

function Connection:step()
    if self.session and self.clock() >= self.session.expires then self.session = nil end
    if not self.udp then return end
    local now = self.clock()
    if now - self.packet_window >= 1 then self.packet_window, self.packets = now, 0 end
    for _ = 1, 4 do
        local data, peer, port = self.udp:receivefrom(2048)
        if not data then break end
        self.packets = self.packets + 1
        if self.packets <= 20 and #data <= 1024 then
            local ok, query = pcall(json.decode, data)
            if ok and type(query) == "table" and query.service == self.SERVICE and query.protocol == 1 and query.kind == "discover"
                and type(query.nonce) == "string" and #query.nonce == 32 and query.nonce:match("^[a-f0-9]+$") then
                local ip = self.udp:getsockname()
                if ip == "0.0.0.0" or ip == "*" then ip = localAddress(peer, port) end
                if ip then
                    local result = self:identity()
                    result.service, result.protocol, result.kind = self.SERVICE, 1, "device"
                    result.address, result.nonce = ip, query.nonce
                    local message = table.concat({ "hermes-koreader/1", query.nonce, result.device_id, ip, tostring(result.port), self.version }, "\n")
                    result.proof = sha.hmac(sha.sha256, self.settings.token, message)
                    self.udp:sendto(json.encode(result), peer, port)
                end
            end
        end
    end
end

function Connection:stop()
    self:closePairing()
    if self.udp then self.udp:close(); self.udp = nil end
end

return Connection
