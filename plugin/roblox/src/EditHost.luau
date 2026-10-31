--!strict
-- The plugin in the edit DataModel: the toolbar button and the dock, and the session with the agent service (hello
-- "host": "editor"). It answers what needs no game: `session` (start: a play test with
-- StudioTestService:ExecutePlayModeAsync, whose server puts the client agent in the game; status), `codex_manifest`, `codex_read`, and `context` /
-- `ping` while no play test runs. The bridge sends the game's requests to the play test's server instead
-- (PlayServer.luau). Frames that aren't requests (status, result, ...) are shown in the dock.

local StudioTestService = game:GetService("StudioTestService")

local Agent = require(script.Parent.Agent)
local Codex = require(script.Parent.Codex)
local Config = require(script.Parent.Config)
local Dock = require(script.Parent.Dock)
local Json = require(script.Parent.Json)
local Link = require(script.Parent.Link)

local EditHost = {}

function EditHost.start(plugin: Plugin)
	Agent.remove() -- (left behind by a run Studio didn't see end)

	local toolbar = plugin:CreateToolbar("Autopilot")
	local toggle = toolbar:CreateButton(
		"Autopilot",
		"Let the Autopilot agent playtest this place",
		"rbxasset://textures/StudioToolbox/AssetPreview/play_button.png"
	)
	toggle.ClickableWhenViewportHidden = true

	local link = nil
	local testRunning = false
	local dock

	local function setSetting(key: string, value: any)
		pcall(function()
			plugin:SetSetting(key, value)
		end)
	end

	local function engineVersion(): string
		-- selene: allow(undefined_variable)
		local ok, v = pcall(version) -- (Luau's global: Studio's version)
		return ok and tostring(v) or ""
	end

	local function hello(): { [string]: any }
		local h: { [string]: any } = {
			type = "hello",
			protocol = Config.PROTOCOL,
			plugin_version = Config.VERSION,
			engine = "roblox",
			engine_version = engineVersion(),
			project = game.Name,
			host = "editor",
			features = Json.array({ "timed_hold" }),
			-- (the other engines' plugins ask in a first-run setup whether runs may be shared; this one has no such
			-- setup yet, so its runs aren't shared)
			share = false,
		}
		if game.PlaceId ~= 0 then
			h.project_id = tostring(game.PlaceId)
		end
		return h
	end

	local function respond(id: any, ok: boolean, result: any, status: number?)
		if link == nil then
			return
		end
		if ok then
			link:send({ type = "response", id = id, ok = true, result = result })
		else
			link:send({ type = "response", id = id, ok = false, status = status or 400, error = tostring(result) })
		end
	end

	local function startTest(args: { [string]: any }): (boolean, any, number?)
		-- (the bridge asks for a start once the last test's server has gone: ExecutePlayModeAsync may take a moment
		-- longer to return here, a restart's start must wait for it, not answer "running")
		local deadline = os.clock() + 20
		while testRunning and os.clock() < deadline do
			task.wait(0.1)
		end
		if testRunning then
			return true, { running = true, loading = true }
		end
		local map = args.map
		if type(map) == "string" and map ~= "" and map ~= game.Name then
			return false, string.format("Roblox Studio plays the open place (%s) only: no map %s", game.Name, map), 400
		end
		testRunning = true
		task.spawn(function()
			local done, problem = pcall(function()
				return StudioTestService:ExecutePlayModeAsync({
					autopilot = true,
					autopilot_session = link and link.session or nil,
				})
			end)
			testRunning = false
			if not done then
				warn("Autopilot: the play test couldn't run: " .. tostring(problem))
			end
			if link then
				link:send({ type = "events", events = Json.array({ { kind = "session", running = false } }) })
			end
		end)
		return true, { running = true, loading = true }
	end

	local function dispatch(op: string, args: { [string]: any }): (boolean, any, number?)
		if op == "session" then
			local action = tostring(args.action or "status")
			if action == "start" or action == "restart" then
				return startTest(args)
			elseif action == "stop" then
				-- (only the play test's server can end it; with its server gone, Studio's Stop button does)
				return true,
					{
						running = testRunning,
						note = testRunning and "the play test's server isn't connected: press Stop in Studio" or nil,
					}
			end
			return true, { running = testRunning, loading = testRunning }
		elseif op == "codex_manifest" then
			return true, Codex.manifest(args)
		elseif op == "codex_read" then
			return true, Codex.read(args)
		elseif op == "context" then
			return true,
				{
					world = "",
					scene = game.Name,
					character = "",
					controller = "",
					input_subsystem = "",
					camera_manager = "",
					camera = "",
					dimension = "3d",
					game_name = game.Name,
					host = "editor",
					engine = "roblox",
					session_running = testRunning,
					paused = false,
					game_time = 0,
					project_dir = "",
					place_id = game.PlaceId,
					controls = Json.array(),
				}
		elseif op == "ping" then
			return true, { game_time = 0, wall_time = os.time() }
		elseif op == "detectors" or op == "pause" or op == "timestep" then
			return true, {}
		end
		return false, "unknown op " .. op, 400
	end

	local function onFrame(frame: { [string]: any })
		local kind = frame.type
		if kind == "request" then
			local a = type(frame.args) == "table" and frame.args or {}
			local ok, good, result, status = pcall(dispatch, tostring(frame.op or ""), a)
			if ok then
				respond(frame.id, good, result, status)
			else
				respond(frame.id, false, "Autopilot: " .. tostring(good), 500)
			end
		elseif kind == "status" then
			dock:setStatus(tostring(frame.text or ""))
		elseif kind == "result" then
			-- (a claim's result: verdict and reason; a goal's or an exploration's: reached and notes. cli.result_frame)
			local r = type(frame.result) == "table" and frame.result or {}
			local head = r.verdict
			if head == nil and r.kind == "goal" then
				head = r.reached and "Goal reached" or "Goal not reached"
			end
			local lines = {}
			if type(r.reason) == "string" and r.reason ~= "" then
				table.insert(lines, r.reason)
			end
			if type(r.notes) == "table" then
				for _, note in r.notes do
					table.insert(lines, "- " .. tostring(note))
				end
			end
			if type(r.alerts) == "table" then
				for _, alert in r.alerts do
					table.insert(lines, "! " .. tostring(alert))
				end
			end
			dock:setResult(string.format("%s\n\n%s", tostring(head or "Done"), table.concat(lines, "\n")))
			dock:setStatus("Finished")
		elseif kind == "run_started" then
			dock:setStatus("A run started from your coding agent")
		elseif kind == "error" then
			dock:setConnection("The agent service refused: " .. tostring(frame.message))
		end
	end

	local function connect()
		if link then
			link:close("reconnecting")
		end
		local kept = nil
		pcall(function()
			kept = plugin:GetSetting(Config.SETTING_SESSION)
		end)
		link = Link.new({
			plugin = plugin,
			context = "edit",
			session = type(kept) == "string" and kept or nil,
			hello = hello,
			onFrame = onFrame,
			onState = function(state, detail)
				if state == "connected" then
					dock:setConnection("Connected to the agent service")
					setSetting(Config.SETTING_SESSION, detail)
				elseif state == "rejected" then
					dock:setConnection(
						"Token rejected: paste the token from `autopilot roblox status --show-token` below"
					)
				elseif state == "lost" then
					dock:setConnection(
						"Can't reach the agent service (autopilot serve), retrying: " .. tostring(detail)
					)
				elseif state == "closed" then
					dock:setConnection("Not connected")
				else
					dock:setConnection("Connecting to " .. Config.url(plugin))
				end
			end,
		})
		link:start()
	end

	local function disconnect()
		setSetting(Config.SETTING_SESSION, "")
		if link then
			link:close("the dock was closed")
			link = nil
		end
	end

	dock = Dock.new(plugin, {
		start = function(text)
			if link == nil or not link.connected then
				dock:setStatus("Not connected to the agent service yet")
				return
			end
			dock:setResult("")
			dock:setStatus("Starting")
			link:send({ type = "start", run = { kind = "auto", prompt = text } })
		end,
		stop = function()
			if link then
				link:send({ type = "stop" })
			end
		end,
		steer = function(text)
			if link then
				link:send({ type = "steer", text = text })
			end
		end,
		saveSettings = function(url, token)
			setSetting(Config.SETTING_URL, url)
			setSetting(Config.SETTING_TOKEN, token)
			connect()
		end,
		connect = connect,
	}, Config.url(plugin), Config.token(plugin))

	toggle:SetActive(dock.widget.Enabled)
	toggle.Click:Connect(function()
		dock.widget.Enabled = not dock.widget.Enabled
	end)
	dock.widget:GetPropertyChangedSignal("Enabled"):Connect(function()
		toggle:SetActive(dock.widget.Enabled)
		if dock.widget.Enabled then
			connect()
		else
			disconnect()
		end
	end)
	if dock.widget.Enabled then
		connect()
	end
	plugin.Unloading:Connect(function()
		disconnect()
		Agent.remove()
	end)
end

return EditHost
