--!strict
-- The plugin's settings. The token is baked in by `autopilot roblox install` (it replaces the placeholder below); the
-- dock's Token and Address fields override it and the address (kept with plugin:SetSetting, shared by the edit and play
-- instances of the plugin).

local Config = {
	VERSION = "0.1.0",
	PROTOCOL = 1,
	URL = "http://127.0.0.1:8766/v1/roblox",
	TOKEN = "__AUTOPILOT_ROBLOX_TOKEN__",
	POLL_WAIT = 2, -- seconds a poll may wait at the bridge for frames (events wait for the next poll: at most this)
	MAX_ANSWER_WAIT = 0.25, -- seconds the next poll waits for the answers to what the last one brought
	-- Studio holds a play test's server to Roblox's HttpService budget, about 500 requests a minute (checked live: at
	-- 30 a second it refused requests within seconds). The link spends REQUESTS_PER_SECOND on average (420 a minute)
	-- in bursts of up to REQUEST_BURST, one request a question (Link.luau), and halves its rate, down to
	-- MIN_REQUESTS_PER_SECOND, should Studio refuse one anyway.
	REQUESTS_PER_SECOND = 7,
	REQUEST_BURST = 40,
	MIN_REQUESTS_PER_SECOND = 4,
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
