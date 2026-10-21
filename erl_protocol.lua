-- Versioned messages and request correlation; no REAPER or network calls.
local source_path = debug.getinfo(1, "S").source:sub(2)
local folder = assert(source_path:match("^(.*[/\\])"), "Cannot resolve protocol directory")
local reader = dofile(folder .. "erl_json.lua")
local protocol = { version = 2, connect_timeout = 10, read_timeout = 300 }
local operations = { tts = true, sts = true, stt = true }

local function text(value)
    return type(value) == "string" and value:match("%S") ~= nil
end

function protocol.encode_request(request)
    assert(request.protocol_version == protocol.version, "Unsupported protocol version")
    assert(text(request.request_id) and operations[request.operation], "Invalid request identity")
    return reader.encode_object_tree(request)
end

function protocol.decode_response(raw, active)
    local ok, data, kinds = pcall(reader.decode, raw)
    if not ok then return nil, "Malformed worker response JSON." end
    if kinds[""] ~= "object" then return nil, "Worker response must be an object." end
    -- Ignore stale identities before considering their status or result.
    if data.request_id ~= active.request_id then return nil, nil, true end
    if data.protocol_version ~= protocol.version or data.operation ~= active.operation then
        return nil, "Worker response protocol or operation does not match. Update the complete installation and restart REAPER."
    end
    if data.status ~= "completed" and data.status ~= "failed" and data.status ~= "cancelled" then
        return nil, "Worker response has no valid terminal status."
    end
    if kinds["/logs"] ~= "array" then return nil, "Worker response logs must be an array." end
    for _, entry in ipairs(data.logs) do
        if type(entry) ~= "table" or not text(entry.message)
            or (entry.level ~= "info" and entry.level ~= "warning" and entry.level ~= "error") then
            return nil, "Worker response contains an invalid log entry."
        end
    end
    if data.status == "completed" then
        if kinds["/result"] ~= "object" or data.error ~= nil then
            return nil, "Completed response has an invalid result."
        end
        if active.operation == "stt" then
            if data.result.kind ~= "text" or type(data.result.text) ~= "string" then
                return nil, "Transcription response has no text result."
            end
        elseif data.result.kind ~= "audio" or not text(data.result.item_guid)
            or not text(data.result.audio_path)
            or data.result.project_master_guid ~= active.context.project_master_guid then
            return nil, "Audio response has no valid imported-item reference."
        end
    elseif kinds["/error"] ~= "object" or data.result ~= nil or not text(data.error.code)
        or not text(data.error.message) or type(data.error.outcome_uncertain) ~= "boolean" then
        return nil, "Failed or cancelled response has no valid error details."
    end
    return data
end

return protocol
