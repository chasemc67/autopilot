--!strict
-- The plugin's settings. The token is baked in by `autopilot roblox install` (it replaces the placeholder below); the
-- dock's Token and Address fields override it and the address (kept with plugin:SetSetting, shared by the edit and play
-- instances of the plugin).

local Config = {
	VERSION = "0.1.0",
	PROTOCOL = 1,
	URL = "http://127.0.0.1:8766/v1/roblox",
	TOKEN = "__AUTOPILOT_ROBLOX_TOKEN__",
	POLL_WAIT = 20, -- seconds a poll may wait at the bridge for frames
	MAX_REQUESTS_PER_SECOND = 8, -- (Roblox limits a game server to about 500 requests a minute)
	CLIENT_TIMEOUT = 10, -- seconds to wait for the client agent's answer
	HEARTBEAT_SECONDS = 1,
	-- What the plugin puts in the place for a run (removed when the play test ends)
	AGENT_NAME = "AutopilotClientAgent",
	REMOTE_NAME = "AutopilotAgentRemote",
	EVENTS_NAME = "AutopilotAgentEvents",
	TEMP_TAG = "AutopilotTemporary",
	-- The modules the client agent needs (copied under it)
	AGENT_MODULES = { "Json", "Frame", "Paths", "Values", "World", "Sight", "Input", "Gui" },
	SETTING_TOKEN = "AutopilotToken",
	SETTING_URL = "AutopilotUrl",
	SETTING_SESSION = "AutopilotSession",
}

local function setting(plugin: Plugin?, key: string): string?
	if plugin == nil then
		return nil
	end
	local ok, value = pcall(function()
		return plugin:GetSetting(key)
	end)
	if ok and type(value) == "string" and value ~= "" then
		return value
	end
	return nil
end

-- The token to send: the dock's, else the one baked in at install ("" when neither).
function Config.token(plugin: Plugin?): string
	local given = setting(plugin, Config.SETTING_TOKEN)
	if given then
		return given
	end
	if string.sub(Config.TOKEN, 1, 2) == "__" then
		return ""
	end
	return Config.TOKEN
end

-- The bridge's address: the dock's, else the default.
function Config.url(plugin: Plugin?): string
	return setting(plugin, Config.SETTING_URL) or Config.URL
end

return Config
