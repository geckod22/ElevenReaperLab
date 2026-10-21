local EXT_STATE_SECTION = "eleven_reaper_lab"

-- Define the scripts to register
-- Resolve scripts relative to this file so the installation folder can be renamed.
local _, script_path = reaper.get_action_context()
local script_folder = assert(script_path:match("^(.*[/\\])"), "Cannot resolve the script directory")
local protocol_loaded, protocol = pcall(dofile, script_folder .. "erl_protocol.lua")
local ui_helpers_loaded, ui_helpers = pcall(dofile, script_folder .. "erl_ui_state.lua")
local scripts_to_register = {
	"ERL_App.lua",
	"erl_speech_to_speech.py",
    "erl_speech_to_text.py",
	"erl_text_to_speech.py",
}

-- Initialize ReaImGui Context
local ctx = reaper.ImGui_CreateContext('Eleven Reaper Lab UI')

-- Model capabilities are local; live discovery is a later phase.
local config_path = script_folder .. "erl_config.json"
local config_module = nil
local session_module = nil
local active_config = nil
local active_session = nil
local configuration_error = nil
local voices, profiles = {}, {}

local MAX_DURATION_SEC = 120.0 -- 2 minutes

-- INITIAL STATE
local selected_voice, selected_profile = 1, 1
local settings_operation = "tts"
local requested_tab = nil
local voice_search = ""
local editor_height = 220
local log_search = ""
local log_levels = { info = true, warning = true, error = true }
local operation_order = { "tts", "sts", "stt" }
local operation_names = { tts = "Text to Speech", sts = "Voice Conversion", stt = "Speech to Text" }

local api_key = reaper.GetExtState(EXT_STATE_SECTION, "api_key")

-- New State for Text Management
local text_content = ""

-- LOG
local log_messages = {}
local log_content = ""
local scroll_to_bottom = false

-- Processing?
local is_processing = false
local pending_command = ''
local active_request = nil
local active_project = nil
local operation_status = "Ready"

-- options
local use_normalize = true

-- Function to add a new log entry
local function AddLog(text, level)
    local timestamp = os.date("%H:%M:%S")
    if ui_helpers_loaded then
        text = ui_helpers.redact(text, { api_key, text_content })
        level = level or ui_helpers.log_level(text)
    else
        text, level = "Cannot load interface helpers. Check the application installation.", "error"
    end
    table.insert(log_messages, { timestamp = timestamp, message = text, level = level })
    -- Keep only the last 100 lines to avoid memory issues
    if #log_messages > 100 then table.remove(log_messages, 1) end
    -- Set a flag to scroll to bottom in the next frame
    scroll_to_bottom = true
end

-- Function to find the Command ID by searching the Action List description
function GetCommandIdByFilename(filename)
    local i = 0
    repeat
        -- Reverse lookup: get the description of the action at index i
        local cmd_id = reaper.kbd_enumerateActions(0, i)
        if cmd_id == 0 then break end
        
        local description = reaper.kbd_getTextFromCmd(cmd_id, 0)
        
        -- Check if the description contains our filename
        -- We look for "Script: filename" to be precise
        if description:find("Script: " .. filename, 1, true) then
            return cmd_id
        end
        
        i = i + 1
    until i > 50000 -- Safety break
    return nil
end

local function cleanStart()
    for _, key in ipairs({ "request_json", "response_json", "active_request_id", "request_api_key", "worker_script_folder",
        "script_action_feedback", "current_text", "model_registry_snapshot", "settings_tts", "settings_sts", "settings_stt" }) do
        reaper.DeleteExtState(EXT_STATE_SECTION, key, false)
    end
end

cleanStart()

-- --- REGISTRATION LOGIC ---
-- Function to register scripts if they are missing
local function verify_and_register_scripts()
    for _, filename in ipairs(scripts_to_register) do
        local full_path = script_folder .. filename
        
        -- First, check if the action already exists in the list
        -- We reuse your search logic or check if it's registered
        local cmd_id = GetCommandIdByFilename(filename)
        
        if not cmd_id or cmd_id == 0 then
            -- Script not found, let's register it
            -- true: add, 0: main section, full_path, true: commit to ini
            local success = reaper.AddRemoveReaScript(true, 0, full_path, true)
            
            if success then
                reaper.ShowConsoleMsg("Registered new script: " .. filename .. "\n")
            else
                reaper.ShowConsoleMsg("ERROR: Could not register: " .. full_path .. "\n")
            end
        end
    end
end

-- Run the check immediately when the script starts
verify_and_register_scripts()

-- Check the length of the selected item
local function CheckItemLength(item)
    local take = reaper.GetActiveTake(item)
    if not take or reaper.TakeIsMIDI(take) then return false end
	local duration = reaper.GetMediaItemInfo_Value(item, "D_LENGTH")
	if duration > MAX_DURATION_SEC then
		return false
	else
		return true
	end
end

-- Check whether the selected item needs glue.
local function NeedsGlue(item)
    if not item then return false end
    local take = reaper.GetActiveTake(item)
    if not take or reaper.TakeIsMIDI(take) then return false end
    
    -- Check source offset and whether item and source lengths differ.
    local offset = reaper.GetMediaItemTakeInfo_Value(take, "D_STARTOFFS")
    local item_len = reaper.GetMediaItemInfo_Value(item, "D_LENGTH")
    
    local source = reaper.GetMediaItemTake_Source(take)
    local src_len, lengthIsQuirky = reaper.GetMediaSourceLength(source)
    
    -- if offset > 0 or if the item is shorter than original source, Glue is needed
    if offset > 0.001 or math.abs(item_len - src_len) > 0.001 then
        return true
    end
    return false
end

-- Persist stable identifiers without deleting legacy preferences.
local function save_selections()
    reaper.SetExtState(EXT_STATE_SECTION, "selected_voice_key", voices[selected_voice].id, true)
    reaper.SetExtState(EXT_STATE_SECTION, "selected_profile_id", profiles[selected_profile].id, true)
    for _, operation in ipairs(operation_order) do
        local model_id = active_session.models[operation]
        if model_id then reaper.SetExtState(EXT_STATE_SECTION, "selected_model_" .. operation, model_id, true) end
    end
end

local function apply_preset()
    active_session:set_context(profiles[selected_profile].id, voices[selected_voice].id)
    save_selections()
end

local function resolved_settings(operation)
    local settings, settings_error = active_session:settings(operation)
    if settings_error then error(settings_error, 0) end
    return settings
end

local function load_configuration()
    if not ui_helpers_loaded then
        configuration_error = "Cannot load erl_ui_state.lua. Check the application installation."
        AddLog(configuration_error)
        return
    end
    if not protocol_loaded then
        configuration_error = "Cannot load the protocol module: " .. tostring(protocol)
        AddLog(configuration_error)
        return
    end
    if not config_module then
        local loaded, result, selection = pcall(function()
            return dofile(script_folder .. "erl_config.lua"), dofile(script_folder .. "erl_session.lua")
        end)
        if loaded then
            config_module = result
            session_module = selection
        else
            configuration_error = "Cannot load the configuration module: " .. tostring(result)
            AddLog(configuration_error)
            return
        end
    end
    local candidate, load_error, warnings = config_module.load(config_path)
    if not candidate then
        configuration_error = load_error
        AddLog("Configuration error: " .. load_error)
        return
    end
    local next_voice, next_profile, messages = config_module.restore_selections(candidate, function(key)
        return reaper.GetExtState(EXT_STATE_SECTION, key)
    end)
    local ready, candidate_session, model_messages = pcall(session_module.new, candidate, config_module,
        candidate.profiles[next_profile].id, candidate.voices[next_voice].id, function(key)
            return reaper.GetExtState(EXT_STATE_SECTION, key)
        end)
    if not ready then
        configuration_error = tostring(candidate_session)
        AddLog("Configuration error: " .. configuration_error)
        return
    end
    -- Commit only after the entire candidate has been validated.
    active_config = candidate
    active_session = candidate_session
    voices, profiles = candidate.voices, candidate.profiles
    selected_voice, selected_profile = next_voice, next_profile
    use_normalize = candidate.preferences.normalize_audio
    configuration_error = nil
    apply_preset()
    for _, message in ipairs(messages) do AddLog(message) end
    for _, message in ipairs(model_messages) do AddLog(message) end
    for _, warning in ipairs(warnings or {}) do AddLog("WARNING: " .. warning) end
    AddLog("Configuration loaded. Profile preset applied.")
end

local function clear_request()
    for _, key in ipairs({ "request_json", "response_json", "request_api_key", "active_request_id", "worker_script_folder" }) do
        reaper.DeleteExtState(EXT_STATE_SECTION, key, false)
    end
    is_processing = false
    pending_command = ''
    active_request, active_project = nil, nil
end

local function fail_active(code, message)
    operation_status = "Failed: " .. code
    AddLog("ERROR [" .. code .. "]: " .. message)
    clear_request()
end

reaper.atexit(function()
    if active_request then
        operation_status = "Locally cancelled"
        clear_request()
    end
end)

local function input_context(operation)
    local project, project_file = reaper.EnumProjects(-1)
    local context = { count = reaper.CountSelectedMediaItems(0), project = project, insertion_mode = "item" }
    if operation == "tts" and context.count == 0 then
        context.insertion_mode = "cursor"
        context.position = reaper.GetCursorPosition()
        context.project_file = project_file
        if not project_file or project_file == "" then
            context.error = "Save the project before generating audio without a selected item."
        else
            local folder = project_file:match("^(.*[/\\])")
            if not folder then
                context.error = "Cannot resolve the saved project's directory."
            else
                context.directory = folder .. "ElevenReaperLab_Audio"
            end
        end
        return context
    end
    if context.count ~= 1 then
        context.error = "Select exactly one audio item for this operation."
        return context
    end
    context.item = reaper.GetSelectedMediaItem(0, 0)
    local take = reaper.GetActiveTake(context.item)
    if not take or reaper.TakeIsMIDI(take) then
        context.error = "The selected item must contain an audio take."
        return context
    end
    context.path = reaper.GetMediaSourceFileName(reaper.GetMediaItemTake_Source(take))
    local _, guid = reaper.GetSetMediaItemInfo_String(context.item, "GUID", "", false)
    context.guid = guid
    context.position = reaper.GetMediaItemInfo_Value(context.item, "D_POSITION")
    -- TTS uses the item for placement and output location, not audio input.
    context.needs_glue = operation ~= "tts" and NeedsGlue(context.item)
    if operation ~= "tts" and not CheckItemLength(context.item) then
        context.error = "The selected item exceeds the " .. MAX_DURATION_SEC .. "-second limit."
    elseif context.needs_glue then
        context.error = "The selected item is trimmed or offset. Glue it before processing."
    elseif context.path == "" then
        context.error = "The source audio path is unavailable."
    end
    return context
end

-- Snapshot the entire request; workers must never reread editable UI fields.
local function queue_worker(filename, operation, message, normalize_result)
    if is_processing or not active_session then return end
    if not api_key:match("%S") then AddLog("ERROR: Enter an API key first."); return end
    local command = GetCommandIdByFilename(filename)
    if not command then
        AddLog("ERROR: Cannot find the worker action: " .. filename)
        return
    end
    -- Validate the composed values again before writing the worker handoff.
    local valid, settings = pcall(resolved_settings, operation)
    if not valid then
        AddLog("ERROR: " .. tostring(settings))
        return
    end
    if operation == "tts" then
        local _, _, text_error = active_session:text_status(text_content)
        if text_error then AddLog("ERROR: " .. text_error); return end
    end
    local input = input_context(operation)
    if input.error then AddLog("ERROR: " .. input.error); return end
    local project = input.project
    local request = {
        protocol_version = protocol.version, request_id = reaper.genGuid(""), operation = operation,
        model_id = active_session.models[operation],
        voice_id = operation ~= "stt" and voices[selected_voice].voice_id or "",
        settings = settings, registry_json = active_session.catalog.source_json,
        input = { audio_path = input.path },
        context = { project_master_guid = reaper.GetTrackGUID(reaper.GetMasterTrack(project)),
            item_guid = input.guid, position = input.position, insertion_mode = input.insertion_mode,
            project_file = input.project_file },
        output = { track_name = operation == "tts" and "TTS" or (operation == "sts" and "VOICE_REGEN" or ""),
            normalize = normalize_result and use_normalize or false, directory = input.directory },
        timeouts = { connect = protocol.connect_timeout, read = protocol.read_timeout }
    }
    if operation == "tts" then request.input.text = text_content end
    local encoded, raw = pcall(protocol.encode_request, request)
    if not encoded then AddLog("ERROR: Cannot encode the worker request."); return end
    local sent = pcall(function()
        reaper.DeleteExtState(EXT_STATE_SECTION, "response_json", false)
        reaper.SetExtState(EXT_STATE_SECTION, "active_request_id", request.request_id, false)
        reaper.SetExtState(EXT_STATE_SECTION, "request_api_key", api_key, false)
        reaper.SetExtState(EXT_STATE_SECTION, "worker_script_folder", script_folder, false)
        reaper.SetExtState(EXT_STATE_SECTION, "request_json", raw, false)
    end)
    if not sent then fail_active("HANDOFF_FAILED", "Cannot prepare the worker request."); return end
    active_request, active_project = request, project
    is_processing = true
    pending_command = command
    operation_status = "Queued: " .. operation_names[operation]
    AddLog(message .. " [" .. request.request_id .. "]")
end

local function suggested_value(parameter)
    if parameter.suggested_default ~= nil then return parameter.suggested_default end
    if parameter.type == "boolean" then return false end
    if parameter.type == "string" then return "" end
    return parameter.minimum or 0
end

local function draw_parameter(parameter, value)
    local changed, result
    local label = parameter.label .. "##parameter_" .. settings_operation .. "_" .. parameter.id
    local ui = parameter.ui
    if ui.control == "checkbox" then
        changed, result = reaper.ImGui_Checkbox(ctx, label, value)
    elseif ui.control == "select" then
        local preview = tostring(value)
        for index, allowed in ipairs(parameter.enum) do
            if value == allowed then preview = ui.labels and ui.labels[index] or tostring(allowed) end
        end
        if reaper.ImGui_BeginCombo(ctx, label, preview) then
            for index, allowed in ipairs(parameter.enum) do
                local name = ui.labels and ui.labels[index] or tostring(allowed)
                if reaper.ImGui_Selectable(ctx, name .. "##" .. index, value == allowed) then
                    changed, result = true, allowed
                end
            end
            reaper.ImGui_EndCombo(ctx)
        end
    elseif ui.control == "text" then
        changed, result = reaper.ImGui_InputText(ctx, label, value)
    else
        local minimum = ui.minimum or parameter.minimum or -1000000
        local maximum = ui.maximum or parameter.maximum or 1000000
        local integer = parameter.type == "integer"
        changed, result = reaper.ImGui_DragDouble(ctx, label, value, ui.step or (integer and 1 or 0.01),
            minimum, maximum, integer and "%.0f" or "%.2f")
        if changed then
            result = math.max(minimum, math.min(maximum, result))
            if integer then result = math.floor(result + 0.5) end
        end
    end
    if changed then active_session:set_value(settings_operation, parameter.id, result) end
end

local function draw_model_settings()
    local model_id = active_session.models[settings_operation]
    local catalog = active_session.catalog
    local model = model_id and catalog.by_id[model_id]
    if reaper.ImGui_BeginCombo(ctx, "Model", model and model.name or "No configured model") then
        for _, option in ipairs(active_session:model_options(settings_operation)) do
            reaper.ImGui_BeginDisabled(ctx, not option.enabled)
            if reaper.ImGui_Selectable(ctx, option.name .. "##" .. option.id, option.id == model_id) then
                active_session:select_model(settings_operation, option.id)
                save_selections()
                if option.note then AddLog(option.note) end
            end
            reaper.ImGui_EndDisabled(ctx)
            if not option.enabled then reaper.ImGui_TextWrapped(ctx, option.reason) end
        end
        reaper.ImGui_EndCombo(ctx)
    end
    model_id = active_session.models[settings_operation]
    if not model_id then return end
    model = catalog.by_id[model_id]
    if model.selection_note then reaper.ImGui_TextWrapped(ctx, model.selection_note) end
    if active_session:is_modified(settings_operation) then reaper.ImGui_Text(ctx, "Preset modified") end
    if reaper.ImGui_SmallButton(ctx, "Reset preset") then apply_preset() end
    local settings = active_session:settings(settings_operation)
    local required = {}
    for _, key in ipairs(catalog:operation(settings_operation, model_id).required_settings) do required[key] = true end
    local optional = {}
    for _, parameter in ipairs(active_session:parameters(settings_operation)) do
        if required[parameter.id] then
            draw_parameter(parameter, settings[parameter.id])
        else
            optional[#optional + 1] = parameter
        end
    end
    if #optional > 0 and reaper.ImGui_CollapsingHeader(ctx, "Optional settings") then
        reaper.ImGui_TextWrapped(ctx, "Enable a custom value to override the service default. Unchecked fields are omitted from the request.")
        for _, parameter in ipairs(optional) do
            local value = settings[parameter.id]
            local changed, enabled = reaper.ImGui_Checkbox(ctx,
                "Use " .. parameter.label .. "##use_" .. settings_operation .. "_" .. parameter.id, value ~= nil)
            if changed then
                if enabled then
                    value = suggested_value(parameter)
                    active_session:set_value(settings_operation, parameter.id, value)
                else
                    value = nil
                    active_session:set_value(settings_operation, parameter.id, nil)
                end
            end
            if value ~= nil then
                draw_parameter(parameter, value)
                reaper.ImGui_TextWrapped(ctx, parameter.description)
            end
        end
    end
    local _, settings_error = active_session:settings(settings_operation)
    if settings_error then
        reaper.ImGui_TextColored(ctx, 0xFF4444FF, "Settings need attention")
        reaper.ImGui_TextWrapped(ctx, settings_error)
    end
end

load_configuration()

local function normalize_result(request, result, project)
    if not request.output.normalize or result.kind ~= "audio" then return end
    if not reaper.ValidatePtr(project, "ReaProject*") or reaper.EnumProjects(-1) ~= project then
        AddLog("WARNING: Normalization skipped because the original project is not active.")
        return
    end
    local target
    for index = 0, reaper.CountMediaItems(project) - 1 do
        local item = reaper.GetMediaItem(project, index)
        local _, guid = reaper.GetSetMediaItemInfo_String(item, "GUID", "", false)
        if guid == result.item_guid then target = item; break end
    end
    if not target then AddLog("WARNING: Normalization skipped: generated item is unavailable."); return end
    local take = reaper.GetActiveTake(target)
    if not take or reaper.GetMediaSourceFileName(reaper.GetMediaItemTake_Source(take)) ~= result.audio_path then
        AddLog("WARNING: Normalization skipped: generated audio reference changed.")
        return
    end
    local command = reaper.NamedCommandLookup("_BR_NORMALIZE_LOUDNESS_ITEMS")
    if command == 0 then AddLog("WARNING: SWS normalization is unavailable."); return end
    local selected, tracks = {}, {}
    for index = 0, reaper.CountSelectedMediaItems(project) - 1 do
        selected[#selected + 1] = reaper.GetSelectedMediaItem(project, index)
    end
    local master = reaper.GetMasterTrack(project)
    if reaper.IsTrackSelected(master) then tracks[#tracks + 1] = master end
    for index = 0, reaper.CountTracks(project) - 1 do
        local track = reaper.GetTrack(project, index)
        if reaper.IsTrackSelected(track) then tracks[#tracks + 1] = track end
    end
    local cursor = reaper.GetCursorPosition()
    reaper.Undo_BeginBlock2(project)
    local ok = pcall(function()
        reaper.SelectAllMediaItems(project, false)
        reaper.SetMediaItemSelected(target, true)
        reaper.Main_OnCommand(command, 0)
    end)
    local restored = pcall(function()
        reaper.SelectAllMediaItems(project, false)
        for _, item in ipairs(selected) do
            if reaper.ValidatePtr2(project, item, "MediaItem*") then reaper.SetMediaItemSelected(item, true) end
        end
        reaper.SetTrackSelected(master, false)
        for index = 0, reaper.CountTracks(project) - 1 do
            reaper.SetTrackSelected(reaper.GetTrack(project, index), false)
        end
        for _, track in ipairs(tracks) do
            if reaper.ValidatePtr2(project, track, "MediaTrack*") then reaper.SetTrackSelected(track, true) end
        end
        reaper.SetEditCurPos(cursor, false, false)
    end)
    reaper.Undo_EndBlock2(project, "Eleven Reaper Lab: normalize generated audio", -1)
    if not ok then AddLog("WARNING: Audio generation completed, but normalization failed.") end
    if not restored then AddLog("WARNING: Could not fully restore selections after normalization.") end
end

local function HandleScriptMessages()
    local raw = reaper.GetExtState(EXT_STATE_SECTION, "response_json")
    if raw == "" then return end
    reaper.DeleteExtState(EXT_STATE_SECTION, "response_json", false)
    if not active_request then return end
    local response, response_error, stale = protocol.decode_response(raw, active_request)
    if stale then AddLog("WARNING: Ignored a response for an obsolete request."); return end
    if not response then fail_active("INVALID_RESPONSE", response_error); return end
    local request, project = active_request, active_project
    -- Clear busy state before presenting logs or invoking optional post-processing.
    clear_request()
    operation_status = response.status == "completed" and "Completed" or
        (response.status == "cancelled" and "Locally cancelled" or "Failed: " .. response.error.code)
    for _, entry in ipairs(response.logs) do
        AddLog(entry.message, entry.level)
    end
    if response.status == "completed" then
        if response.result.kind == "text" then
            text_content = response.result.text
            requested_tab = "tts"
            reaper.SetExtState(EXT_STATE_SECTION, "current_text", text_content, false)
        else
            local normalized = pcall(normalize_result, request, response.result, project)
            if not normalized then AddLog("WARNING: Audio completed, but normalization could not be handled.") end
        end
    else
        AddLog("ERROR [" .. response.error.code .. "]: " .. response.error.message)
        if response.error.outcome_uncertain then
            AddLog("WARNING: Server processing or credit usage may be uncertain. No automatic retry was made.")
        end
        if response.error.audio_saved and type(response.error.audio_path) == "string" then
            AddLog("Saved audio: " .. response.error.audio_path)
        end
    end
end

-- Draw helpers keep presentation separate from the shared session and request state.
local function draw_settings()
    local flags = api_key == "" and reaper.ImGui_TreeNodeFlags_DefaultOpen() or 0
    if not reaper.ImGui_CollapsingHeader(ctx, "Settings", flags) then return end
    reaper.ImGui_BeginDisabled(ctx, is_processing)
    reaper.ImGui_Text(ctx, "API key")
    reaper.ImGui_SetNextItemWidth(ctx, -1)
    local changed, value = reaper.ImGui_InputText(ctx, "##key", api_key, reaper.ImGui_InputTextFlags_Password())
    if changed then
        api_key = value
        reaper.SetExtState(EXT_STATE_SECTION, "api_key", api_key, true)
    end
    if active_config then
        _, use_normalize = reaper.ImGui_Checkbox(ctx, "Normalize generated audio (TTS / Voice Conversion)", use_normalize)
    end
    if reaper.ImGui_Button(ctx, "Reload configuration") then load_configuration() end
    reaper.ImGui_TextWrapped(ctx, "Reload reapplies presets and clears all manual parameter changes.")
    reaper.ImGui_EndDisabled(ctx)
end

local function draw_configuration_error()
    if not configuration_error then return end
    reaper.ImGui_TextColored(ctx, 0xFF6666FF, "Configuration error")
    reaper.ImGui_TextWrapped(ctx, configuration_error)
    reaper.ImGui_TextWrapped(ctx, active_config and
        "The last valid configuration is still active. Fix the configuration and reload in Settings." or
        "Actions are blocked. Fix the configuration and reload in Settings.")
end

local function draw_voice_and_profile(operation)
    if operation ~= "stt" then
        reaper.ImGui_Text(ctx, "Voice")
        reaper.ImGui_SetNextItemWidth(ctx, -1)
        if reaper.ImGui_BeginCombo(ctx, "##voice", voices[selected_voice].name) then
            reaper.ImGui_Text(ctx, "Search name or ID")
            reaper.ImGui_SetNextItemWidth(ctx, -1)
            local changed, value = reaper.ImGui_InputText(ctx, "##voice_search", voice_search)
            if changed then voice_search = value end
            local matches = ui_helpers.filter_voices(voices, voice_search)
            if #matches == 0 then reaper.ImGui_Text(ctx, "No matching voices.") end
            for _, index in ipairs(matches) do
                local voice = voices[index]
                if reaper.ImGui_Selectable(ctx, voice.name .. "##" .. voice.id, index == selected_voice) then
                    if selected_voice ~= index then
                        selected_voice = index
                        apply_preset()
                    end
                end
            end
            reaper.ImGui_EndCombo(ctx)
        end
        local voice = voices[selected_voice]
        reaper.ImGui_TextWrapped(ctx, "Voice ID: " .. voice.voice_id)
        if voice.description then reaper.ImGui_TextWrapped(ctx, voice.description) end
    end
    reaper.ImGui_Text(ctx, "Profile")
    reaper.ImGui_SetNextItemWidth(ctx, -1)
    if reaper.ImGui_BeginCombo(ctx, "##profile", profiles[selected_profile].name) then
        for index, profile in ipairs(profiles) do
            if reaper.ImGui_Selectable(ctx, profile.name .. "##" .. profile.id, selected_profile == index) then
                if selected_profile ~= index then
                    selected_profile = index
                    apply_preset()
                end
            end
        end
        reaper.ImGui_EndCombo(ctx)
    end
end

local function draw_input_summary(operation, input)
    if input.path then reaper.ImGui_TextWrapped(ctx, "Source: " .. input.path) end
    if operation ~= "stt" and not input.error then
        reaper.ImGui_TextWrapped(ctx, "Insert on " .. (operation == "tts" and "TTS" or "VOICE_REGEN") ..
            " at " .. string.format("%.3f", input.position) .. " seconds.")
        if input.directory then
            reaper.ImGui_TextWrapped(ctx, "Save generated audio in: " .. input.directory)
        else
            reaper.ImGui_TextWrapped(ctx, "Generated audio is saved beside the source file; the source item will be muted.")
        end
    end
end

local function draw_text_editor(read_only)
    reaper.ImGui_Text(ctx, read_only and "Transcription / working text" or "Text to generate")
    reaper.ImGui_SetNextItemWidth(ctx, 180)
    local resized, height = reaper.ImGui_DragDouble(ctx, "Editor height", editor_height, 1, 100, 700, "%.0f px")
    if resized then editor_height = math.max(100, math.min(700, height)) end
    local _, available_height = reaper.ImGui_GetContentRegionAvail(ctx)
    local height_to_use = math.max(100, math.min(editor_height, available_height - 150))
    local flags = read_only and reaper.ImGui_InputTextFlags_ReadOnly() or 0
    local changed, value = reaper.ImGui_InputTextMultiline(ctx, "##working_text", text_content, -1, height_to_use, flags)
    if changed and not read_only then
        text_content = value
        reaper.SetExtState(EXT_STATE_SECTION, "current_text", text_content, false)
    end
end

local function draw_operation_action(operation, input)
    local _, settings_error = active_session:settings(operation)
    local text_error
    if operation == "tts" then
        local count, limit, problem = active_session:text_status(text_content)
        text_error = problem
        reaper.ImGui_Text(ctx, tostring(count) .. (limit and (" / " .. limit) or "") .. " characters")
    end
    local key_error = not api_key:match("%S") and "Enter your API key in Settings." or nil
    local blocked = input.error or settings_error or text_error or key_error
    if input.error then reaper.ImGui_TextWrapped(ctx, input.error) end
    if settings_error then reaper.ImGui_TextWrapped(ctx, settings_error) end
    if text_error then reaper.ImGui_TextWrapped(ctx, text_error) end
    if key_error then reaper.ImGui_TextWrapped(ctx, key_error) end
    if input.needs_glue then
        reaper.ImGui_BeginDisabled(ctx, is_processing)
        if reaper.ImGui_Button(ctx, "Glue selected item") then
            reaper.Undo_BeginBlock2(0)
            reaper.Main_OnCommand(40362, 0)
            reaper.Undo_EndBlock2(0, "Eleven Reaper Lab: glue selected item", -1)
            AddLog("Selected item glued. Review the source before processing.")
        end
        reaper.ImGui_EndDisabled(ctx)
    end
    reaper.ImGui_BeginDisabled(ctx, is_processing or blocked ~= nil)
    local actions = {
        tts = { "Generate audio", "erl_text_to_speech.py", "Starting Text to Speech...", true },
        sts = { "Regenerate audio", "erl_speech_to_speech.py", "Starting Voice Conversion...", true },
        stt = { "Transcribe audio", "erl_speech_to_text.py", "Starting Speech to Text...", false }
    }
    local action = actions[operation]
    if reaper.ImGui_Button(ctx, action[1], -1, 36) then
        queue_worker(action[2], operation, action[3], action[4])
    end
    reaper.ImGui_EndDisabled(ctx)
end

local function draw_operation_tab(operation)
    settings_operation = operation
    reaper.ImGui_BeginDisabled(ctx, is_processing)
    draw_voice_and_profile(operation)
    draw_model_settings()
    reaper.ImGui_EndDisabled(ctx)
    reaper.ImGui_Separator(ctx)
    local input = input_context(operation)
    draw_input_summary(operation, input)
    if operation == "tts" then
        draw_text_editor(false)
    elseif operation == "sts" then
        reaper.ImGui_TextWrapped(ctx, "Convert the selected audio directly into the chosen voice. This does not use the text editor.")
    else
        reaper.ImGui_TextWrapped(ctx, "Transcribe the selected audio. On completion, Text to Speech opens with the result ready to edit.")
        if text_content ~= "" then
            draw_text_editor(true)
            if reaper.ImGui_Button(ctx, "Open in Text to Speech") then requested_tab = "tts" end
        end
    end
    draw_operation_action(operation, input)
end

local function draw_logs()
    if not ui_helpers_loaded then return end
    if not reaper.ImGui_CollapsingHeader(ctx, "Activity log", reaper.ImGui_TreeNodeFlags_DefaultOpen()) then return end
    _, log_levels.info = reaper.ImGui_Checkbox(ctx, "Info", log_levels.info)
    reaper.ImGui_SameLine(ctx)
    _, log_levels.warning = reaper.ImGui_Checkbox(ctx, "Warnings", log_levels.warning)
    reaper.ImGui_SameLine(ctx)
    _, log_levels.error = reaper.ImGui_Checkbox(ctx, "Errors", log_levels.error)
    reaper.ImGui_Text(ctx, "Filter log")
    reaper.ImGui_SetNextItemWidth(ctx, -1)
    _, log_search = reaper.ImGui_InputText(ctx, "##log_search", log_search)
    log_content = ui_helpers.format_logs(log_messages, log_levels, log_search)
    reaper.ImGui_BeginDisabled(ctx, log_content == "")
    if reaper.ImGui_SmallButton(ctx, "Copy visible log") then reaper.ImGui_SetClipboardText(ctx, log_content) end
    reaper.ImGui_EndDisabled(ctx)
    reaper.ImGui_SameLine(ctx)
    if reaper.ImGui_SmallButton(ctx, "Clear log") then
        log_messages, log_content = {}, ""
    end
    local visible = reaper.ImGui_BeginChild(ctx, "LogRegion", -1, 140, 1)
    if visible then
        reaper.ImGui_TextWrapped(ctx, log_content == "" and "No matching log entries." or log_content)
        if scroll_to_bottom then reaper.ImGui_SetScrollHereY(ctx, 1.0); scroll_to_bottom = false end
        -- ReaImGui already ends a child when BeginChild returns false.
        reaper.ImGui_EndChild(ctx)
    end
end

local function dispatch_pending()
    if pending_command == '' then return end
    local command = pending_command
    pending_command = ''
    if not active_project or not reaper.ValidatePtr(active_project, "ReaProject*")
        or reaper.EnumProjects(-1) ~= active_project then
        fail_active("PROJECT_CHANGED", "The original project is no longer active.")
        return
    end
    operation_status = "Processing (synchronous)"
    local dispatched = pcall(reaper.Main_OnCommand, command, 0)
    HandleScriptMessages()
    if is_processing then
        fail_active(dispatched and "MISSING_RESPONSE" or "ACTION_FAILED",
            "The worker returned without a valid final response. Server outcome may be uncertain; no retry was made.")
    end
end

local function loop()
    HandleScriptMessages()
    dispatch_pending()
    reaper.ImGui_SetNextWindowSize(ctx, 620, 820, reaper.ImGui_Cond_FirstUseEver())
    local visible, open = reaper.ImGui_Begin(ctx, 'Eleven Reaper Lab v1', true, 0)
    if visible then
        reaper.ImGui_Text(ctx, "Eleven Reaper Lab")
        reaper.ImGui_TextWrapped(ctx, "Status: " .. operation_status)
        reaper.ImGui_TextWrapped(ctx, "REAPER waits during requests. Network timeouts: 10 s connection / 300 s read inactivity.")
        draw_settings()
        draw_configuration_error()
        if active_config and reaper.ImGui_BeginTabBar(ctx, "Operations") then
            for _, operation in ipairs(operation_order) do
                local flags = requested_tab == operation and reaper.ImGui_TabItemFlags_SetSelected() or 0
                if reaper.ImGui_BeginTabItem(ctx, operation_names[operation], nil, flags) then
                    if requested_tab == operation then requested_tab = nil end
                    reaper.ImGui_PushID(ctx, operation)
                    draw_operation_tab(operation)
                    reaper.ImGui_PopID(ctx)
                    reaper.ImGui_EndTabItem(ctx)
                end
            end
            reaper.ImGui_EndTabBar(ctx)
        end
        reaper.ImGui_Separator(ctx)
        draw_logs()
        reaper.ImGui_End(ctx)
    end
    if open then reaper.defer(loop) end
end

reaper.defer(loop)
