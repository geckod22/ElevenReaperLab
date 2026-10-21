-- Offline model metadata and validation shared through erl_models.json.
local source_path = debug.getinfo(1, "S").source:sub(2)
local module_folder = assert(source_path:match("^(.*[/\\])"), "Cannot resolve registry module directory")
local reader = dofile(module_folder .. "erl_json.lua")
local fail, child_path = reader.fail, reader.child_path
local registry = {}
local methods = {}
methods.__index = methods
local operations = { tts = true, sts = true, stt = true }

local function copy(value)
    if type(value) ~= "table" then return value end
    local result = {}
    for key, entry in pairs(value) do result[key] = copy(entry) end
    return result
end

local function sorted_keys(value)
    local result = {}
    for key in pairs(value) do result[#result + 1] = key end
    table.sort(result, function(a, b) return tostring(a) < tostring(b) end)
    return result
end

local function finite(value)
    return type(value) == "number" and value == value and value > -math.huge and value < math.huge
end

local function text(value, path)
    if type(value) ~= "string" or not value:match("%S") then fail(path, "expected a non-empty string") end
end

local function check_value(definition, value, path)
    local kind = definition.type
    if kind == "number" or kind == "integer" then
        if not finite(value) then fail(path, "expected a finite " .. kind) end
        if kind == "integer" and value % 1 ~= 0 then fail(path, "expected an integer") end
        if definition.minimum ~= nil and value < definition.minimum then
            fail(path, "value is below minimum " .. definition.minimum)
        end
        if definition.maximum ~= nil and value > definition.maximum then
            fail(path, "value exceeds maximum " .. definition.maximum)
        end
    elseif type(value) ~= kind then
        fail(path, "expected a " .. kind)
    end
    if kind == "string" then
        local _, length = value:gsub("[^\128-\191]", "")
        if definition.min_length and length < definition.min_length then fail(path, "string is too short") end
        if definition.max_length and length > definition.max_length then fail(path, "string is too long") end
    end
    if definition.enum ~= nil then
        local found = false
        for _, allowed in ipairs(definition.enum) do
            if type(value) == type(allowed) and value == allowed then found = true; break end
        end
        if not found then fail(path, "value is not in the allowed set") end
    end
end

local function merged_definition(base, override)
    local result = copy(base)
    for key, value in pairs(override) do
        if key == "ui" then
            for ui_key, ui_value in pairs(value) do result.ui[ui_key] = copy(ui_value) end
        else
            result[key] = copy(value)
        end
    end
    return result
end

local function validate_catalog(data, kinds)
    local function container(value, path, kind)
        if type(value) ~= "table" or kinds[path] ~= kind then fail(path, "expected a JSON " .. kind) end
    end
    local function fields(value, path, allowed)
        container(value, path, "object")
        for key in pairs(value) do if not allowed[key] then fail(child_path(path, key), "unknown registry field") end end
    end
    local function string_array(value, path, nonempty)
        container(value, path, "array")
        if nonempty and #value == 0 then fail(path, "must not be empty") end
        local seen = {}
        for index, entry in ipairs(value) do
            text(entry, child_path(path, index))
            if seen[entry] then fail(child_path(path, index), "duplicate entry") end
            seen[entry] = true
        end
    end
    local function references(value, path)
        string_array(value, path, true)
        for index, key in ipairs(value) do
            if not data.sources[key] then fail(child_path(path, index), "unknown source reference") end
        end
    end
    local definition_fields = { label = true, type = true, api_field = true, destination = true,
        api_required = true, minimum = true, maximum = true, min_length = true, max_length = true,
        enum = true, suggested_default = true, description = true, ui = true, source_refs = true }
    local function definition(value, path, partial)
        fields(value, path, definition_fields)
        if partial then return end
        text(value.label, path .. "/label")
        text(value.api_field, path .. "/api_field")
        text(value.description, path .. "/description")
        if not ({ number = true, integer = true, boolean = true, string = true })[value.type] then
            fail(path .. "/type", "unknown parameter type")
        end
        if value.destination ~= "request" and value.destination ~= "voice_settings" then
            fail(path .. "/destination", "expected request or voice_settings")
        end
        if type(value.api_required) ~= "boolean" then fail(path .. "/api_required", "expected a boolean") end
        for _, key in ipairs({ "minimum", "maximum", "min_length", "max_length" }) do
            if value[key] ~= nil and not finite(value[key]) then fail(path .. "/" .. key, "expected a finite number") end
        end
        if value.minimum and value.maximum and value.minimum > value.maximum then fail(path, "invalid numeric interval") end
        for _, key in ipairs({ "min_length", "max_length" }) do
            if value[key] and (value[key] < 0 or value[key] % 1 ~= 0) then fail(path .. "/" .. key, "expected a non-negative integer") end
        end
        if value.min_length and value.max_length and value.min_length > value.max_length then fail(path, "invalid string length interval") end
        if value.enum ~= nil then
            container(value.enum, path .. "/enum", "array")
            if #value.enum == 0 then fail(path .. "/enum", "must not be empty") end
            local without_enum = copy(value)
            without_enum.enum = nil
            local seen = {}
            for index, entry in ipairs(value.enum) do
                check_value(without_enum, entry, child_path(path .. "/enum", index))
                local key = type(entry) .. ":" .. (type(entry) == "number" and string.format("%.17g", entry) or tostring(entry))
                if seen[key] then fail(path .. "/enum", "duplicate allowed value") end
                seen[key] = true
            end
        end
        fields(value.ui, path .. "/ui", { control = true, step = true, minimum = true, maximum = true, labels = true })
        if not ({ slider = true, integer = true, checkbox = true, select = true, text = true })[value.ui.control] then
            fail(path .. "/ui/control", "unknown UI control")
        end
        for _, key in ipairs({ "minimum", "maximum", "step" }) do
            if value.ui[key] ~= nil and not finite(value.ui[key]) then fail(path .. "/ui/" .. key, "expected a finite number") end
        end
        if value.ui.step and value.ui.step <= 0 then fail(path .. "/ui/step", "expected a positive step") end
        local ui_min = value.ui.minimum or value.minimum
        local ui_max = value.ui.maximum or value.maximum
        if ui_min and ui_max and ui_min > ui_max then fail(path .. "/ui", "invalid numeric interval") end
        if value.minimum and ui_min and ui_min < value.minimum then fail(path .. "/ui/minimum", "outside API interval") end
        if value.maximum and ui_max and ui_max > value.maximum then fail(path .. "/ui/maximum", "outside API interval") end
        if value.ui.labels ~= nil then
            string_array(value.ui.labels, path .. "/ui/labels", true)
            if not value.enum or #value.ui.labels ~= #value.enum then fail(path .. "/ui/labels", "must match enum length") end
        end
        references(value.source_refs, path .. "/source_refs")
        if value.suggested_default ~= nil then check_value(value, value.suggested_default, path .. "/suggested_default") end
    end

    fields(data, "", { registry_version = true, reviewed_on = true, sources = true, notes = true,
        runtime_defaults = true, legacy_worker_parameters = true, parameters = true, models = true })
    if data.registry_version ~= 1 then fail("/registry_version", "unsupported registry version") end
    text(data.reviewed_on, "/reviewed_on")
    container(data.sources, "/sources", "object")
    for key, url in pairs(data.sources) do
        text(url, child_path("/sources", key))
        if not url:match("^https://") then fail(child_path("/sources", key), "expected an HTTPS source URL") end
    end
    string_array(data.notes, "/notes", false)
    container(data.parameters, "/parameters", "object")
    for key, value in pairs(data.parameters) do definition(value, child_path("/parameters", key), false) end
    container(data.models, "/models", "array")
    if #data.models == 0 then fail("/models", "at least one model is required") end
    local instance = setmetatable({ data = data, by_id = {} }, methods)
    for index, model in ipairs(data.models) do
        local path = child_path("/models", index)
        fields(model, path, { id = true, name = true, reviewed_on = true, source_refs = true, notes = true,
            operations = true, selection_enabled = true, selection_note = true })
        text(model.id, path .. "/id")
        text(model.name, path .. "/name")
        text(model.reviewed_on, path .. "/reviewed_on")
        if model.selection_enabled ~= nil and type(model.selection_enabled) ~= "boolean" then
            fail(path .. "/selection_enabled", "expected a boolean")
        end
        if model.selection_note ~= nil then text(model.selection_note, path .. "/selection_note") end
        if instance.by_id[model.id] then fail(path .. "/id", "duplicate model ID") end
        references(model.source_refs, path .. "/source_refs")
        string_array(model.notes, path .. "/notes", false)
        container(model.operations, path .. "/operations", "object")
        if not next(model.operations) then fail(path .. "/operations", "at least one operation is required") end
        for operation, spec in pairs(model.operations) do
            local spec_path = child_path(path .. "/operations", operation)
            if not operations[operation] then fail(spec_path, "unknown operation") end
            fields(spec, spec_path, { input_kind = true, transport = true, endpoint = true, parameters = true,
                required_settings = true, constraints = true, limits = true })
            local expected_input = operation == "tts" and "text" or "audio_file"
            if spec.input_kind ~= expected_input and not (operation == "stt" and spec.input_kind == "audio_stream") then
                fail(spec_path .. "/input_kind", "incompatible input kind")
            end
            local transport = spec.input_kind == "text" and "json" or (spec.input_kind == "audio_stream" and "websocket" or "multipart")
            if spec.transport ~= transport then fail(spec_path .. "/transport", "incompatible transport") end
            text(spec.endpoint, spec_path .. "/endpoint")
            container(spec.parameters, spec_path .. "/parameters", "object")
            local destinations = {}
            for key, override in pairs(spec.parameters) do
                local parameter_path = child_path(spec_path .. "/parameters", key)
                if not data.parameters[key] then fail(parameter_path, "unknown parameter reference") end
                definition(override, parameter_path, true)
                -- Validate inherited metadata with synthetic paths for its containers.
                local merged = merged_definition(data.parameters[key], override)
                local inherited_path = child_path("/parameters", key)
                for _, field in ipairs({ "ui", "enum", "source_refs" }) do
                    if override[field] == nil then
                        kinds[parameter_path .. "/" .. field] = kinds[inherited_path .. "/" .. field]
                    end
                end
                if not override.ui or override.ui.labels == nil then
                    kinds[parameter_path .. "/ui/labels"] = kinds[inherited_path .. "/ui/labels"]
                end
                definition(merged, parameter_path, false)
                local destination = merged.destination .. "/" .. merged.api_field
                if destinations[destination] then fail(parameter_path, "duplicate API destination") end
                destinations[destination] = true
            end
            string_array(spec.required_settings, spec_path .. "/required_settings", false)
            for _, key in ipairs(spec.required_settings) do
                if not spec.parameters[key] then fail(spec_path .. "/required_settings", "unknown parameter reference") end
            end
            if spec.limits ~= nil then
                fields(spec.limits, spec_path .. "/limits", { max_text_characters = true })
                local limit = spec.limits.max_text_characters
                if not finite(limit) or limit <= 0 or limit % 1 ~= 0 then fail(spec_path .. "/limits", "expected a positive character limit") end
            end
            container(spec.constraints, spec_path .. "/constraints", "array")
            for rule_index, rule in ipairs(spec.constraints) do
                local rule_path = child_path(spec_path .. "/constraints", rule_index)
                fields(rule, rule_path, { setting = true, requires = true, conflicts = true })
                if not spec.parameters[rule.setting] then fail(rule_path .. "/setting", "unknown parameter reference") end
                container(rule.requires, rule_path .. "/requires", "object")
                for key, expected in pairs(rule.requires) do
                    if not spec.parameters[key] then fail(rule_path .. "/requires", "unknown parameter reference") end
                    check_value(merged_definition(data.parameters[key], spec.parameters[key]), expected, child_path(rule_path .. "/requires", key))
                end
                string_array(rule.conflicts, rule_path .. "/conflicts", false)
                for _, key in ipairs(rule.conflicts) do
                    if not spec.parameters[key] then fail(rule_path .. "/conflicts", "unknown parameter reference") end
                end
            end
        end
        instance.by_id[model.id] = model
    end
    fields(data.runtime_defaults, "/runtime_defaults", operations)
    fields(data.legacy_worker_parameters, "/legacy_worker_parameters", operations)
    for operation in pairs(operations) do
        local model_id = data.runtime_defaults[operation]
        text(model_id, "/runtime_defaults/" .. operation)
        local spec = instance:operation(operation, model_id)
        if spec.input_kind == "audio_stream" then fail("/runtime_defaults/" .. operation, "current runtime requires file input") end
        local path = "/legacy_worker_parameters/" .. operation
        string_array(data.legacy_worker_parameters[operation], path, false)
        for _, key in ipairs(data.legacy_worker_parameters[operation]) do
            if not spec.parameters[key] then fail(path, "unknown runtime parameter") end
        end
    end
    return instance
end

function methods:operation(operation, model_id)
    if not operations[operation] then fail("/operation", "unknown operation") end
    local model = self.by_id[model_id]
    if not model then fail("/model_id", "unknown model; its configuration is inactive") end
    if not model.operations[operation] then fail("/model_id", "model is incompatible with operation " .. operation) end
    return copy(model.operations[operation])
end

-- Accept a previously fetched /v1/models response; never fetch from the GUI.
-- Listing a model does not establish account access or expand local rules.
function methods:status(operation, model_id, discovered_models)
    local result = { known = self.by_id[model_id] ~= nil,
        compatible = self.by_id[model_id] ~= nil and self.by_id[model_id].operations[operation] ~= nil,
        account_availability = "unknown", discovery_status = "not_checked", api_capability = "unknown" }
    if discovered_models == nil then return result end
    if type(discovered_models) ~= "table" then fail("/discovered_models", "expected a model list") end
    local count, highest_index = 0, 0
    for key in pairs(discovered_models) do
        if not finite(key) or key < 1 or key % 1 ~= 0 then
            fail("/discovered_models", "expected a model list")
        end
        count = count + 1
        highest_index = math.max(highest_index, key)
    end
    if count ~= highest_index then fail("/discovered_models", "expected a contiguous model list") end
    if operation == "stt" then result.discovery_status = "not_applicable"; return result end
    if not operations[operation] then fail("/operation", "unknown operation") end
    result.discovery_status = "not_listed"
    for _, model in ipairs(discovered_models) do
        if type(model) ~= "table" then fail("/discovered_models", "expected model objects") end
        if model.model_id == model_id then
            result.discovery_status = "listed"
            local flag
            if operation == "tts" then flag = model.can_do_text_to_speech end
            if operation == "sts" then flag = model.can_do_voice_conversion end
            if flag ~= nil then
                if type(flag) ~= "boolean" then fail("/discovered_models", "capability flag must be boolean") end
                result.api_capability = flag and "supported" or "unsupported"
            end
            break
        end
    end
    return result
end

function methods:list_models(operation, input_kind)
    if not operations[operation] then fail("/operation", "unknown operation") end
    local result = {}
    for _, model in ipairs(self.data.models) do
        local spec = model.operations[operation]
        if spec and (not input_kind or spec.input_kind == input_kind) then result[#result + 1] = copy(model) end
    end
    return result
end

function methods:parameters(operation, model_id)
    local result = {}
    local spec = self:operation(operation, model_id)
    for _, key in ipairs(sorted_keys(spec.parameters)) do
        local definition = merged_definition(self.data.parameters[key], spec.parameters[key])
        definition.id = key
        result[#result + 1] = definition
    end
    return result
end

function methods:validate_settings(operation, model_id, settings, partial, path)
    path = path or "/settings"
    local spec = self:operation(operation, model_id)
    if type(settings) ~= "table" then fail(path, "expected a settings object") end
    for _, key in ipairs(sorted_keys(settings)) do
        if not spec.parameters[key] then fail(child_path(path, key), "parameter is not registered for this model and operation") end
        check_value(merged_definition(self.data.parameters[key], spec.parameters[key]), settings[key], child_path(path, key))
    end
    if not partial then
        for _, key in ipairs(spec.required_settings) do
            if settings[key] == nil then fail(child_path(path, key), "required application setting is missing") end
        end
        for _, rule in ipairs(spec.constraints) do
            if settings[rule.setting] ~= nil then
                for key, expected in pairs(rule.requires) do
                    if settings[key] ~= expected then fail(child_path(path, rule.setting), "requires " .. key .. " = " .. tostring(expected)) end
                end
                for _, key in ipairs(rule.conflicts) do
                    if settings[key] ~= nil then fail(child_path(path, rule.setting), "cannot be combined with " .. key) end
                end
            end
        end
    end
    return copy(settings)
end

function methods:compose(operation, model_id, layers, path)
    path = path or "/effective_settings"
    local result = {}
    for index, layer in ipairs(layers) do
        self:validate_settings(operation, model_id, layer, true, child_path(path, index))
        for key, value in pairs(layer) do result[key] = value end
    end
    return self:validate_settings(operation, model_id, result, false, path)
end

function methods:split_settings(operation, model_id, settings)
    local values = self:validate_settings(operation, model_id, settings, false)
    local result = { request = {}, voice_settings = {} }
    for key, value in pairs(values) do
        local definition = merged_definition(self.data.parameters[key], self:operation(operation, model_id).parameters[key])
        result[definition.destination][definition.api_field] = value
    end
    return result
end

function registry.load(path)
    local data, kinds, source_json = reader.load(path or (module_folder .. "erl_models.json"))
    local instance = validate_catalog(data, kinds)
    instance.source_json = source_json
    return instance
end

return registry
