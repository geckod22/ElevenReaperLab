-- Configuration loading and migration; this module does not call REAPER APIs.
local source_path = debug.getinfo(1, "S").source:sub(2)
local module_folder = assert(source_path:match("^(.*[/\\])"), "Cannot resolve configuration module directory")
local reader = dofile(module_folder .. "erl_json.lua")
local model_registry = dofile(module_folder .. "erl_model_registry.lua")
local fail, child_path = reader.fail, reader.child_path
local config = {}
local registries = setmetatable({}, { __mode = "k" })
local operations = { tts = true, sts = true, stt = true }

local function validate(data, kinds, catalog)
    local warnings, referenced = {}, { tts = {}, sts = {}, stt = {} }
    local function container(value, path, kind)
        if type(value) ~= "table" or kinds[path] ~= kind then fail(path, "expected a JSON " .. kind) end
    end
    local function fields(value, path, allowed)
        container(value, path, "object")
        for key in pairs(value) do
            if not allowed[key] then fail(child_path(path, key), "unknown field") end
        end
    end
    local function text(value, path)
        if type(value) ~= "string" or not value:match("%S") then fail(path, "expected a non-empty string") end
    end
    local function identifier(value, path)
        text(value, path)
        if not value:match("^[a-z][a-z0-9_%-]*$") then
            fail(path, "use a lowercase English identifier starting with a letter, with digits, '_' or '-'")
        end
    end
    local function settings(value, path, operation, model)
        container(value, path, "object")
        for key, entry in pairs(value) do
            local field = child_path(path, key)
            text(key, field)
            local entry_type = type(entry)
            if entry_type ~= "number" and entry_type ~= "boolean" and entry_type ~= "string" then
                fail(field, "expected a number, boolean or string")
            end
            if entry_type == "number" and (entry ~= entry or entry == math.huge or entry == -math.huge) then
                fail(field, "expected a finite number")
            end
        end
        local status = catalog:status(operation, model)
        if not status.known then
            warnings[#warnings + 1] = path .. ": unknown model; configuration retained but inactive."
            return
        end
        if not status.compatible then fail(path, "model is incompatible with operation " .. operation) end
        catalog:validate_settings(operation, model, value, true, path)
        referenced[operation][model] = true
        local entry = catalog.by_id[model]
        if entry.selection_enabled == false then
            warnings[#warnings + 1] = path .. ": " .. (entry.selection_note or "This model is disabled for selection.")
        end
    end
    local function operation_settings(value, path)
        container(value, path, "object")
        for operation, models in pairs(value) do
            local operation_path = child_path(path, operation)
            if not operations[operation] then fail(operation_path, "unknown operation; use tts, sts or stt") end
            container(models, operation_path, "object")
            for model, values in pairs(models) do
                local model_path = child_path(operation_path, model)
                text(model, model_path)
                settings(values, model_path, operation, model)
            end
        end
    end

    fields(data, "", { schema_version = true, voices = true, model_defaults = true, profiles = true, preferences = true })
    if data.schema_version ~= 1 then fail("/schema_version", "expected supported schema version 1") end
    container(data.voices, "/voices", "array")
    if #data.voices == 0 then fail("/voices", "at least one voice is required") end
    local voice_ids, provider_ids = {}, {}
    for index, voice in ipairs(data.voices) do
        local path = child_path("/voices", index)
        fields(voice, path, { id = true, name = true, voice_id = true, description = true })
        identifier(voice.id, path .. "/id")
        text(voice.name, path .. "/name")
        text(voice.voice_id, path .. "/voice_id")
        if voice.description ~= nil then text(voice.description, path .. "/description") end
        if voice_ids[voice.id] then fail(path .. "/id", "duplicate voice identifier") end
        if provider_ids[voice.voice_id] then fail(path .. "/voice_id", "duplicate ElevenLabs voice ID") end
        voice_ids[voice.id], provider_ids[voice.voice_id] = true, true
    end

    operation_settings(data.model_defaults, "/model_defaults")
    for operation, model in pairs(catalog.data.runtime_defaults) do
        local path = "/model_defaults/" .. operation .. "/" .. model
        local defaults = data.model_defaults[operation] and data.model_defaults[operation][model]
        local required = catalog.data.legacy_worker_parameters[operation]
        if #required > 0 and not defaults then fail(path, "defaults for the current model are required") end
        for _, key in ipairs(required) do
            if defaults[key] == nil then fail(path .. "/" .. key, "required default is missing") end
        end
    end

    container(data.profiles, "/profiles", "array")
    if #data.profiles == 0 then fail("/profiles", "at least one profile is required") end
    local profile_ids = {}
    for index, profile in ipairs(data.profiles) do
        local path = child_path("/profiles", index)
        fields(profile, path, { id = true, name = true, settings = true, voice_overrides = true })
        identifier(profile.id, path .. "/id")
        text(profile.name, path .. "/name")
        if profile_ids[profile.id] then fail(path .. "/id", "duplicate profile identifier") end
        profile_ids[profile.id] = true
        if profile.settings ~= nil then operation_settings(profile.settings, path .. "/settings") end
        if profile.voice_overrides ~= nil then
            container(profile.voice_overrides, path .. "/voice_overrides", "object")
            for voice, overrides in pairs(profile.voice_overrides) do
                local override_path = child_path(path .. "/voice_overrides", voice)
                if not voice_ids[voice] then fail(override_path, "unknown voice identifier") end
                operation_settings(overrides, override_path)
            end
        end
    end
    fields(data.preferences, "/preferences", { normalize_audio = true })
    if type(data.preferences.normalize_audio) ~= "boolean" then
        fail("/preferences/normalize_audio", "expected a boolean")
    end
    -- Bind the registry snapshot only after structural validation. A failed reload
    -- leaves the previous config and its own registry snapshot untouched.
    registries[data] = catalog
    for operation, models in pairs(referenced) do
        for model in pairs(models) do
            for profile_index, profile in ipairs(data.profiles) do
                for _, voice in ipairs(data.voices) do
                    config.resolve(data, profile.id, voice.id, operation, model, nil,
                        "/profiles/" .. profile_index .. "/effective_settings/" .. voice.id .. "/" .. operation .. "/" .. model)
                end
            end
        end
    end
    return data, warnings
end

function config.load(path, registry_path)
    local success, result, warnings = pcall(function()
        local catalog = model_registry.load(registry_path)
        local data, kinds = reader.load(path)
        return validate(data, kinds, catalog)
    end)
    if not success then return nil, tostring(result) end
    return result, nil, warnings
end

function config.registry(data)
    local catalog = registries[data]
    assert(catalog, "Configuration has not been loaded and validated")
    return catalog
end

config.encode_settings = reader.encode_object

function config.find_by_id(entries, id)
    for index, entry in ipairs(entries) do
        if entry.id == id then return index end
    end
end

function config.resolve(data, profile_id, voice_id, operation, model, manual, path)
    local catalog = config.registry(data)
    local function values_for(settings)
        return (settings and settings[operation] and settings[operation][model]) or {}
    end
    local profile_index = config.find_by_id(data.profiles, profile_id)
    if not profile_index then fail("/profile_id", "unknown profile identifier") end
    if not config.find_by_id(data.voices, voice_id) then fail("/voice_id", "unknown voice identifier") end
    local profile = data.profiles[profile_index]
    local manual_settings = manual
    if manual_settings == nil then manual_settings = {} end
    return catalog:compose(operation, model, {
        values_for(data.model_defaults), values_for(profile.settings),
        values_for(profile.voice_overrides and profile.voice_overrides[voice_id]), manual_settings
    }, path)
end

-- Legacy indices refer to the original lists, never the reordered JSON lists.
function config.restore_selections(data, get_state)
    local messages = {}
    local voice_key = get_state("selected_voice_key")
    local profile_id = get_state("selected_profile_id")
    local voice_index, profile_index
    if voice_key ~= "" then
        voice_index = config.find_by_id(data.voices, voice_key)
    else
        local provider_id = get_state("selected_voice_id")
        if provider_id == "" then
            local legacy_voices = { "55yFBZr9Z5qIEPStrml6", "DvhZbRTZ1tLwH9fmUXy3", "JMMRfSOPHFZtKfqJOA3h", "hNh5DDgWzileCY9sKkLU" }
            local legacy_value = get_state("selected_voice")
            local legacy_index = tonumber(legacy_value)
            provider_id = legacy_voices[legacy_index] or (legacy_value ~= "" and "(invalid legacy voice)" or "")
        end
        for index, voice in ipairs(data.voices) do
            if voice.voice_id == provider_id then voice_index = index; break end
        end
        voice_key = provider_id
    end
    if profile_id == "" then
        local legacy_profiles = { "bedtime", "meditation", "course" }
        local legacy_value = get_state("selected_project")
        local legacy_index = tonumber(legacy_value)
        profile_id = legacy_profiles[legacy_index] or (legacy_value ~= "" and "(invalid legacy profile)" or "")
    end
    profile_index = config.find_by_id(data.profiles, profile_id)
    if not voice_index and voice_key ~= "" then
        messages[#messages + 1] = "Saved voice is unavailable; selected the first configured voice."
    end
    if not profile_index and profile_id ~= "" then
        messages[#messages + 1] = "Saved profile is unavailable; selected the first configured profile."
    end
    return voice_index or 1, profile_index or 1, messages
end

return config
