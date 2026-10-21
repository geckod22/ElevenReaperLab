-- Pure display helpers. Filtering must never mutate operational selections.
local ui_state = {}

function ui_state.filter_voices(voices, query)
    local result = {}
    query = (query or ""):lower()
    for index, voice in ipairs(voices) do
        local searchable = (voice.name .. " " .. voice.id .. " " .. voice.voice_id):lower()
        if query == "" or searchable:find(query, 1, true) then result[#result + 1] = index end
    end
    return result
end

function ui_state.redact(message, secrets)
    message = tostring(message)
    for _, secret in ipairs(secrets or {}) do
        if type(secret) == "string" and secret:match("%S") then
            local escaped = secret:gsub("([^%w])", "%%%1")
            message = message:gsub(escaped, function() return "[redacted]" end)
        end
    end
    return message
end

function ui_state.log_level(message)
    if message:match("^ERROR") then return "error" end
    if message:match("^WARNING") then return "warning" end
    return "info"
end

function ui_state.format_logs(entries, levels, query)
    local result = {}
    query = (query or ""):lower()
    for _, entry in ipairs(entries) do
        if levels[entry.level] and (query == "" or entry.message:lower():find(query, 1, true)) then
            result[#result + 1] = "[" .. entry.timestamp .. "] " .. entry.level:upper() .. ": " .. entry.message
        end
    end
    return table.concat(result, "\n")
end

return ui_state
