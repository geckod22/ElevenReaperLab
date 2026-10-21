-- Operation/model selection and temporary edits; no REAPER or network calls.
local session = {}
local methods = {}
methods.__index = methods
local operation_order = { "tts", "sts", "stt" }

local function copy(values)
    local result = {}
    for key, value in pairs(values or {}) do result[key] = value end
    return result
end

function methods:model_options(operation)
    local input_kind = operation == "tts" and "text" or "audio_file"
    local result = {}
    for _, model in ipairs(self.catalog:list_models(operation, input_kind)) do
        local allowed, reason = true, nil
        if model.selection_enabled == false then
            allowed = false
            reason = model.selection_note or "Selection is disabled for this model."
        else
            local valid, value = pcall(self.config_module.resolve, self.data, self.profile_id,
                self.voice_id, operation, model.id)
            if not valid then
                allowed = false
                reason = "Configure this model's defaults in erl_config.json: " .. tostring(value)
            end
        end
        result[#result + 1] = { id = model.id, name = model.name, enabled = allowed,
            reason = reason, note = model.selection_note }
    end
    return result
end

function methods:select_model(operation, model_id)
    for _, option in ipairs(self:model_options(operation)) do
        if option.id == model_id then
            if not option.enabled then return nil, option.reason end
            self.models[operation] = model_id
            return true
        end
    end
    return nil, "Model is not compatible with this operation and input kind."
end

function methods:reset()
    self.edits = { tts = {}, sts = {}, stt = {} }
end

function methods:set_context(profile_id, voice_id)
    assert(self.config_module.find_by_id(self.data.profiles, profile_id), "Unknown profile")
    assert(self.config_module.find_by_id(self.data.voices, voice_id), "Unknown voice")
    self.profile_id, self.voice_id = profile_id, voice_id
    self:reset()
end

function methods:edit_state(operation)
    local model_id = assert(self.models[operation], "No model selected")
    local state = self.edits[operation][model_id]
    if not state then
        state = { values = {}, omitted = {} }
        self.edits[operation][model_id] = state
    end
    return state
end

function methods:is_modified(operation)
    local state = self:edit_state(operation)
    return next(state.values) ~= nil or next(state.omitted) ~= nil
end

function methods:set_value(operation, parameter_id, value)
    local spec = self.catalog:operation(operation, self.models[operation])
    assert(spec.parameters[parameter_id], "Unknown parameter")
    if value == nil then
        for _, required in ipairs(spec.required_settings) do
            assert(required ~= parameter_id, "A required setting cannot be omitted")
        end
    end
    local state = self:edit_state(operation)
    state.values[parameter_id] = value
    state.omitted[parameter_id] = value == nil and true or nil
end

function methods:settings(operation)
    local model_id = self.models[operation]
    if not model_id then return {}, "No configured model is available for this operation." end
    local valid, base = pcall(self.config_module.resolve, self.data, self.profile_id,
        self.voice_id, operation, model_id)
    if not valid then return {}, tostring(base) end
    local result = copy(base)
    local state = self:edit_state(operation)
    for key, value in pairs(state.values) do result[key] = value end
    for key in pairs(state.omitted) do result[key] = nil end
    -- Keep the editable values available even when a temporary edit violates a
    -- constraint. The UI can display the error and let the user correct it.
    local accepted, value = pcall(self.catalog.validate_settings, self.catalog,
        operation, model_id, result, false)
    if not accepted then return result, tostring(value) end
    local model = self.catalog.by_id[model_id]
    if model.selection_enabled == false then return result, model.selection_note or "Model is disabled." end
    return result, nil
end

function methods:parameters(operation)
    if not self.models[operation] then return {} end
    return self.catalog:parameters(operation, self.models[operation])
end

function methods:text_status(text)
    local model_id = self.models.tts
    if not model_id then return 0, nil, "No text-to-speech model is selected." end
    local spec = self.catalog:operation("tts", model_id)
    local limit = spec.limits and spec.limits.max_text_characters
    local count = #text
    if utf8 and utf8.len then
        local length = utf8.len(text)
        if not length then return count, limit, "Text contains invalid UTF-8." end
        count = length
    end
    if not text:match("%S") then return count, limit, "Enter some text before generating audio." end
    if limit and count > limit then return count, limit, "Text exceeds the selected model's character limit." end
    return count, limit, nil
end

function session.new(data, config_module, profile_id, voice_id, get_state)
    local self = setmetatable({ data = data, config_module = config_module,
        catalog = config_module.registry(data), profile_id = profile_id, voice_id = voice_id,
        models = {} }, methods)
    self:reset()
    local messages = {}
    for _, operation in ipairs(operation_order) do
        local saved = get_state("selected_model_" .. operation)
        local wanted = saved ~= "" and saved or self.catalog.data.runtime_defaults[operation]
        local selected = self:select_model(operation, wanted)
        if not selected then
            local default_model = self.catalog.data.runtime_defaults[operation]
            selected = self:select_model(operation, default_model)
            if not selected then
                for _, option in ipairs(self:model_options(operation)) do
                    if option.enabled then self.models[operation] = option.id; break end
                end
            end
            messages[#messages + 1] = self.models[operation]
                and ("Saved " .. operation .. " model is unavailable; selected a configured compatible model.")
                or ("No configured compatible " .. operation .. " model is available.")
        end
    end
    return self, messages
end

return session
