--!strict
-- The plugin in a play test's server: attaches to the edit instance's session (StudioTestService:GetTestArgs() names
-- it; a play test started with Studio's own Play button joins the dock's session) and answers the game's requests:
-- world reads, rc, watch here; what the player sees and does through the client agent (ClientAgent.luau), which it
-- puts in each player's PlayerGui (Agent.luau). Sends heartbeats, LogService's warnings and errors (`log` events) and
-- `log_error` findings. `session stop` ends the test (StudioTestService:EndTest).

local LogService = game:GetService("LogService")
local Players = game:GetService("Players")
local RunService = game:GetService("RunService")
local StudioTestService = game:GetService("StudioTestService")
local Workspace = game:GetService("Workspace")

local Agent = require(script.Parent.Agent)
local Config = require(script.Parent.Config)
local Json = require(script.Parent.Json)
local Link = require(script.Parent.Link)
local Paths = require(script.Parent.Paths)
local Rc = require(script.Parent.Rc)
local Sight = require(script.Parent.Sight)
local World = require(script.Parent.World)

local PlayServer = {}

local CLIENT_OPS =
	{ pose = true, view = true, screen = true, ui = true, ui_act = true, key = true, input = true, move = true }

local function t(): number
	return math.round(Workspace.DistributedGameTime * 100) / 100
end

local function normalize(s: string): string
	return string.sub((s:gsub("%d+", "#")), 1, 160)
end

function PlayServer.start(plugin: Plugin)
	local args = nil
	pcall(function()
		args = StudioTestService:GetTestArgs()
	end)
	local session = type(args) == "table" and args.autopilot_session or nil
	if session == nil then
		-- (a play test started with Studio's own Play button: it joins the dock's session when the dock is connected,
		-- else the plugin stays out of the way)
		local ok, kept = pcall(function()
			return plugin:GetSetting(Config.SETTING_SESSION)
		end)
		if not ok or type(kept) ~= "string" or kept == "" then
			return
		end
		session = kept
	end
	local link
	local remote: RemoteFunction?, events: RemoteEvent? = nil, nil
	local agentProblem: string? = nil
	pcall(function()
		remote, events = Agent.remotes()
	end)
	local function inject(player: Player)
		local ok, why = Agent.inject(player)
		if not ok then
			agentProblem = why
			warn("Autopilot: the client agent couldn't be put in the game: " .. tostring(why))
		end
	end
	Players.PlayerAdded:Connect(inject)
	for _, player in Players:GetPlayers() do
		task.spawn(inject, player)
	end
	local counts: { [string]: number } = {}
	local clientReady = false
	local pending: { { [string]: any } } = {}

	local function emit(event: { [string]: any })
		table.insert(pending, event)
	end

	local function respond(id: any, ok: boolean, result: any, status: number?)
		if ok then
			link:send({ type = "response", id = id, ok = true, result = result, t = t() })
		else
			link:send({
				type = "response",
				id = id,
				ok = false,
				status = status or 400,
				error = tostring(result),
				t = t(),
			})
		end
	end

	local function logged(verbosity: string, message: string, category: string)
		if string.sub(message, 1, 10) == "Autopilot:" then
			return
		end
		emit({ kind = "log", t = t(), verbosity = verbosity, category = category, message = message })
		if verbosity ~= "Error" then
			return
		end
		local signature = "log_error:" .. normalize(message)
		local n = (counts[signature] or 0) + 1
		counts[signature] = n
		if n == 1 or n == 10 or n == 100 or n == 1000 then
			emit({
				kind = "finding",
				t = t(),
				finding = {
					detector = "log_error",
					summary = string.sub(message, 1, 300),
					signature = signature,
					count = n,
					source = "engine",
					t = t(),
				},
			})
		end
	end

	-- A request for the client agent: (ok, result JSON text | error, status).
	local function toClient(op: string, a: { [string]: any }): (boolean, any, number?)
		local player = Players:GetPlayers()[1]
		if remote == nil then
			return false, "the client agent isn't in this play test: " .. tostring(agentProblem or "no remote"), 409
		end
		if player == nil then
			return false, "No player", 409
		end
		local done = Instance.new("BindableEvent")
		local answer = { false, "the client agent didn't answer", 504 } :: { any }
		local timeout = Config.CLIENT_TIMEOUT + (tonumber(a.seconds) or 0)
		task.spawn(function()
			local ok, good, result, status = pcall(function()
				return (remote :: RemoteFunction):InvokeClient(player, op, a)
			end)
			if ok then
				answer = { good, result, status }
			else
				answer = { false, "the client agent failed: " .. tostring(good), 500 }
			end
			done:Fire()
		end)
		task.delay(timeout, function()
			done:Fire()
		end)
		done.Event:Wait()
		done:Destroy()
		return answer[1], answer[2], answer[3]
	end

	local function context(): { [string]: any }
		local character = World.body()
		return {
			world = "/Workspace",
			scene = game.Name,
			character = character and Paths.of(character) or "",
			controller = "",
			input_subsystem = "",
			camera_manager = "",
			camera = "/Workspace/Camera",
			dimension = "3d",
			game_name = game.Name,
			host = "editor",
			engine = "roblox",
			session_running = true,
			paused = false,
			game_time = t(),
			project_dir = "",
			place_id = game.PlaceId,
			client_agent = clientReady,
			controls = Json.array(),
		}
	end

	local function dispatch(op: string, a: { [string]: any }): (boolean, any, number?)
		if op == "context" then
			return true, context()
		elseif op == "ping" then
			return true, { game_time = t(), wall_time = os.time() }
		elseif op == "session" then
			local action = tostring(a.action or "status")
			if action == "stop" then
				task.delay(0.2, function()
					pcall(function()
						StudioTestService:EndTest({ autopilot = "stopped" })
					end)
				end)
				return true, { running = false }
			end
			return true, { running = true }
		elseif op == "rc" then
			return Rc.handle(tostring(a.verb or ""), a.body)
		elseif op == "find" then
			return true, World.find(a)
		elseif op == "transforms" then
			return true, World.transforms(a)
		elseif op == "movement" then
			return World.movement()
		elseif op == "watch" then
			return true, Sight.watch(a, t)
		elseif op == "pause" then
			return true, { paused = false, note = "a Roblox plugin can't pause a running game" }
		elseif op == "timestep" then
			return true, { note = "Roblox runs at its own frame rate: timestep does nothing" }
		elseif op == "detectors" then
			return true, {}
		end
		return false, "unknown op " .. op, 400
	end

	local function onRequest(frame: { [string]: any })
		local id, op = frame.id, tostring(frame.op or "")
		local a = type(frame.args) == "table" and frame.args or {}
		if CLIENT_OPS[op] then
			local ok, result, status = toClient(op, a)
			if ok then
				link:sendText(
					string.format(
						'{"type":"response","id":%s,"ok":true,"result":%s,"t":%s}',
						Json.encode(id),
						result,
						Json.encode(t())
					)
				)
			else
				respond(id, false, result, status)
			end
			return
		end
		local ok, good, result, status = pcall(dispatch, op, a)
		if not ok then
			respond(id, false, "Autopilot: " .. tostring(good), 500)
		else
			respond(id, good, result, status)
		end
	end

	link = Link.new({
		plugin = plugin,
		context = "server",
		session = session,
		onFrame = function(frame)
			if frame.type == "request" then
				onRequest(frame)
			end
		end,
	})
	link:start()

	local function onMessage(message: string, kind: Enum.MessageType)
		if kind == Enum.MessageType.MessageError then
			logged("Error", message, "server")
		elseif kind == Enum.MessageType.MessageWarning then
			logged("Warning", message, "server")
		end
	end
	-- (what the game's scripts said before this instance of the plugin ran: an error as the game starts is often the
	-- one that matters. No yield between the history and the connection: nothing is missed or said twice)
	pcall(function()
		for _, entry in LogService:GetLogHistory() do
			onMessage(entry.message, entry.messageType)
		end
	end)
	LogService.MessageOut:Connect(onMessage)
	if events then
		events.OnServerEvent:Connect(function(_, what, verbosity, message)
			if what == "ready" then
				clientReady = true
			elseif what == "log" and type(message) == "string" then
				logged(verbosity == "Error" and "Error" or "Warning", message, "client")
			end
		end)
	end

	emit({ kind = "session", t = t(), running = true })
	local frames, elapsed = 0, 0
	RunService.Heartbeat:Connect(function(dt)
		frames += 1
		elapsed += dt
		if elapsed >= Config.HEARTBEAT_SECONDS then
			emit({ kind = "heartbeat", t = t(), fps = math.round(frames / elapsed), paused = false })
			frames, elapsed = 0, 0
		end
		if #pending > 0 and link.connected then
			local batch = pending
			pending = {}
			link:send({ type = "events", events = Json.array(batch), t = t() })
		end
	end)
	pcall(function()
		game:BindToClose(function()
			link:close("the play test ended")
		end)
	end)
end

return PlayServer
