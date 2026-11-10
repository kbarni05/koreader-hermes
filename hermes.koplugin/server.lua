-- Small HTTP/1.1 endpoint, pumped by KOReader's UI loop without blocking reads.
local socket = require("socket")
local json = require("json")
local Server = {}
Server.__index = Server
Server.MAX_BODY = 786432
Server.MAX_HEADERS = 8192

function Server.new(api, token, connection)
    assert(type(token) == "string" and #token >= 32 and token:match("^[A-Za-z0-9_-]+$"), "Set a token of at least 32 letters/digits/_/-")
    return setmetatable({ api = api, token = token, connection = connection, clients = {} }, Server)
end

function Server:start(host, port)
    local listener, err = socket.bind(host, port, 4)
    if not listener then return nil, err end
    listener:settimeout(0)
    self.listener = listener
    if self.connection then
        local _, actual_port = listener:getsockname()
        local ok, reason = self.connection:startDiscovery(host, tonumber(actual_port))
        self.discovery_error = not ok and tostring(reason) or nil
    end
    return true
end

function Server:stop()
    if self.connection then self.connection:stop() end
    for _, c in ipairs(self.clients) do c.socket:close() end
    self.clients = {}
    if self.listener then self.listener:close(); self.listener = nil end
end

function Server:respond(c, status, data)
    local body = json.encode(data)
    c.output = "HTTP/1.1 " .. status .. "\r\nContent-Type: application/json\r\nConnection: close\r\nCache-Control: no-store\r\nContent-Length: " .. #body .. "\r\n\r\n" .. body
    c.sent = 0
    c.input = ""
end

local function boundedJSON(body)
    local depth, quoted, escaped = 0, false, false
    for i = 1, #body do
        local c = body:sub(i, i)
        if quoted then
            if escaped then escaped = false
            elseif c == "\\" then escaped = true
            elseif c == '"' then quoted = false end
        elseif c == '"' then quoted = true
        elseif c == "{" or c == "[" then
            depth = depth + 1
            if depth > 32 then return false end
        elseif c == "}" or c == "]" then depth = depth - 1 end
    end
    return true
end

function Server:parse(c)
    if not c.length then
        local finish = c.input:find("\r\n\r\n", 1, true)
        if not finish then
            if #c.input > self.MAX_HEADERS then self:respond(c, "431 Headers Too Large", { error = "Headers too large" }) end
            return
        end
        if finish > self.MAX_HEADERS then return self:respond(c, "431 Headers Too Large", { error = "Headers too large" }) end
        local head = c.input:sub(1, finish - 1)
        local request_line, rest = head:match("^([^\r\n]+)\r\n(.*)$")
        c.pairing = request_line == "POST /v1/pair HTTP/1.1" and self.connection ~= nil
        if request_line ~= "POST /v1/rpc HTTP/1.1" and not c.pairing then return self:respond(c, "404 Not Found", { error = "Unknown endpoint" }) end
        local headers = {}
        for line in (rest .. "\r\n"):gmatch("(.-)\r\n") do
            local key, value = line:match("^([%w-]+):%s*(.-)%s*$")
            if not key or headers[key:lower()] then return self:respond(c, "400 Bad Request", { error = "Malformed or duplicate headers" }) end
            headers[key:lower()] = value
        end
        if not c.pairing and headers.authorization ~= "Bearer " .. self.token then return self:respond(c, "401 Unauthorized", { error = "Invalid bearer token" }) end
        if headers["transfer-encoding"] or headers.expect then return self:respond(c, "400 Bad Request", { error = "Chunked transfer and Expect are unsupported" }) end
        local length = headers["content-length"]
        if not length or not length:match("^%d+$") or #length > 7 then return self:respond(c, "411 Length Required", { error = "Content-Length required" }) end
        c.length = tonumber(length)
        if c.length > (c.pairing and 4096 or self.MAX_BODY) then return self:respond(c, "413 Payload Too Large", { error = "Request too large" }) end
        c.input = c.input:sub(finish + 4)
    end
    if #c.input >= c.length then
        if #c.input ~= c.length or not boundedJSON(c.input) then return self:respond(c, "400 Bad Request", { error = "Invalid request body" }) end
        local ok, request = pcall(json.decode, c.input)
        if not ok or type(request) ~= "table" then return self:respond(c, "400 Bad Request", { error = "Invalid JSON object" }) end
        local success, result
        if c.pairing then success, result = pcall(self.connection.pair, self.connection, request)
        else success, result = pcall(self.api.call, self.api, request) end
        if success then self:respond(c, "200 OK", { result = result })
        else self:respond(c, "400 Bad Request", { error = tostring(result) }) end
    end
end

function Server:step()
    if not self.listener then return end
    if self.connection then self.connection:step() end
    for _ = 1, 2 do
        local client = self.listener:accept()
        if not client then break end
        client:settimeout(0)
        if #self.clients >= 4 then client:close()
        else self.clients[#self.clients + 1] = { socket = client, input = "", deadline = socket.gettime() + 30 } end
    end
    for i = #self.clients, 1, -1 do
        local c, done = self.clients[i], false
        if socket.gettime() > c.deadline then done = true
        elseif c.output then
            local last = math.min(#c.output, c.sent + 32768)
            local sent, err, partial = c.socket:send(c.output, c.sent + 1, last)
            c.sent = sent or partial or c.sent
            done = c.sent >= #c.output or err == "closed"
        else
            local data, err, partial = c.socket:receive(32768)
            c.input = c.input .. (data or partial or "")
            self:parse(c)
            if err == "closed" and not c.output then done = true end
        end
        if done then c.socket:close(); table.remove(self.clients, i) end
    end
end

return Server
