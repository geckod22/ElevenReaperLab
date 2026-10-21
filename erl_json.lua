-- Strict JSON reading shared by configuration and model registry loaders.
local source_path = debug.getinfo(1, "S").source:sub(2)
local module_folder = assert(source_path:match("^(.*[/\\])"), "Cannot resolve JSON module directory")
local json = dofile(module_folder .. "lib/json.lua")
local reader = {}

local function fail(path, message)
    error((path == "" and "/" or path) .. ": " .. message, 0)
end

local function child_path(path, key)
    return path .. "/" .. tostring(key):gsub("~", "~0"):gsub("/", "~1")
end

-- Preserve container types and reject duplicate keys/null before rxi decoding.
-- Upstream intentionally maps both containers to tables and null to nil.
local function inspect_structure(text)
    local position, kinds = 1, {}
    local function char() return text:sub(position, position) end
    local function whitespace()
        while char() ~= "" and char():match("[ \t\r\n]") do position = position + 1 end
    end
    local function read_string(path)
        local start = position
        position = position + 1
        while position <= #text do
            if char() == '"' then
                position = position + 1
                return json.decode(text:sub(start, position - 1))
            elseif char() == "\\" then
                position = position + 2
            else
                position = position + 1
            end
        end
        fail(path, "unterminated JSON string")
    end
    local function digits(path)
        local start = position
        while char() ~= "" and char():match("%d") do position = position + 1 end
        if start == position then fail(path, "expected a digit at byte " .. position) end
    end
    local read_value
    read_value = function(path, depth)
        if depth > 64 then fail(path, "configuration nesting is too deep") end
        whitespace()
        local opening = char()
        if opening == "{" or opening == "[" then
            local object = opening == "{"
            local closing = object and "}" or "]"
            kinds[path] = object and "object" or "array"
            position = position + 1
            whitespace()
            if char() == closing then position = position + 1; return end
            local seen, index = {}, 1
            while true do
                local key = index
                if object then
                    if char() ~= '"' then fail(path, "expected an object key at byte " .. position) end
                    key = read_string(path)
                    if seen[key] then fail(child_path(path, key), "duplicate JSON key") end
                    seen[key] = true
                    whitespace()
                    if char() ~= ":" then fail(child_path(path, key), "expected ':'") end
                    position = position + 1
                end
                read_value(child_path(path, key), depth + 1)
                whitespace()
                if char() == closing then position = position + 1; return end
                if char() ~= "," then fail(path, "expected ',' or '" .. closing .. "' at byte " .. position) end
                position = position + 1
                whitespace()
                index = index + 1
            end
        elseif opening == '"' then
            read_string(path)
        elseif opening == "-" or opening:match("%d") then
            if char() == "-" then position = position + 1 end
            if char() == "0" then position = position + 1 else digits(path) end
            if char() == "." then position = position + 1; digits(path) end
            if char() == "e" or char() == "E" then
                position = position + 1
                if char() == "+" or char() == "-" then position = position + 1 end
                digits(path)
            end
        elseif text:sub(position, position + 3) == "true" then
            position = position + 4
        elseif text:sub(position, position + 4) == "false" then
            position = position + 5
        elseif text:sub(position, position + 3) == "null" then
            fail(path, "null is not supported; omit optional fields instead")
        else
            fail(path, "expected a JSON value at byte " .. position)
        end
    end
    read_value("", 0)
    whitespace()
    if position <= #text then fail("", "unexpected content at byte " .. position) end
    return kinds
end


function reader.decode(content)
    content = content:gsub("^\239\187\191", "")
    if utf8 and utf8.len then
        local length, invalid_byte = utf8.len(content)
        if not length then fail("", "invalid UTF-8 at byte " .. invalid_byte) end
    end
    local kinds = inspect_structure(content)
    return json.decode(content), kinds
end

function reader.load(path)
    local file, open_error = io.open(path, "rb")
    if not file then error("Cannot read JSON file: " .. tostring(open_error), 0) end
    local content, read_error = file:read(1024 * 1024 + 1)
    file:close()
    if not content then error("Cannot read JSON file: " .. tostring(read_error or "empty file"), 0) end
    if #content > 1024 * 1024 then error("JSON file exceeds the 1 MiB size limit", 0) end
    local data, kinds = reader.decode(content)
    local source_json = content:gsub("^\239\187\191", "")
    return data, kinds, source_json
end

function reader.encode_object(value)
    if type(value) ~= "table" then fail("/settings", "expected an object") end
    for key in pairs(value) do
        if type(key) ~= "string" then fail("/settings", "expected string property names") end
    end
    -- rxi encodes an empty Lua table as []; worker settings must be an object.
    if next(value) == nil then return "{}" end
    return json.encode(value)
end

-- Protocol envelopes contain objects only; empty settings must remain {}.
function reader.encode_object_tree(value)
    if type(value) ~= "table" then return json.encode(value) end
    local keys, encoded = {}, {}
    for key in pairs(value) do
        if type(key) ~= "string" then fail("/protocol", "expected object property names") end
        keys[#keys + 1] = key
    end
    table.sort(keys)
    for _, key in ipairs(keys) do
        encoded[#encoded + 1] = json.encode(key) .. ":" .. reader.encode_object_tree(value[key])
    end
    return "{" .. table.concat(encoded, ",") .. "}"
end

reader.fail = fail
reader.child_path = child_path
return reader
