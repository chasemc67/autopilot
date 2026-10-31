--!strict
-- The client agent, put in the game by the plugin's play-server instance while the play test runs: the RemoteFunction
-- the server half calls it through and the RemoteEvent it reports the client's errors on, in ReplicatedStorage, and in
-- each player's PlayerGui a ScreenGui (ResetOnSpawn off, so it outlives a respawn) holding the client agent
-- LocalScript (ClientAgent.luau, with the modules it needs under it). They are made in the play test's DataModel only:
-- the edit DataModel, the place you save, is never touched. (A LocalScript the play server makes and sets the Source
-- of replicates with its source and runs in the client: checked live, docs/roblox-live-test-log.md.)

local CollectionService = game:GetService("CollectionService")
local ReplicatedStorage = game:GetService("ReplicatedStorage")

local Config = require(script.Parent.Config)
local Sources = require(script.Parent.Sources)

local Agent = {}

local function tagged(inst: Instance): Instance
	CollectionService:AddTag(inst, Config.TEMP_TAG)
	return inst
end

-- The remotes (made once per play test).
function Agent.remotes(): (RemoteFunction, RemoteEvent)
	local remote = ReplicatedStorage:FindFirstChild(Config.REMOTE_NAME) :: RemoteFunction?
	if remote == nil then
		local made = tagged(Instance.new("RemoteFunction")) :: RemoteFunction
		made.Name = Config.REMOTE_NAME
		made.Parent = ReplicatedStorage
		remote = made
	end
	local events = ReplicatedStorage:FindFirstChild(Config.EVENTS_NAME) :: RemoteEvent?
	if events == nil then
		local made = tagged(Instance.new("RemoteEvent")) :: RemoteEvent
		made.Name = Config.EVENTS_NAME
		made.Parent = ReplicatedStorage
		events = made
	end
	return remote :: RemoteFunction, events :: RemoteEvent
end

-- Put the client agent in a player's PlayerGui. Returns (ok, why not).
function Agent.inject(player: Player): (boolean, string?)
	local source = Sources.get("ClientAgent")
	if source == nil or source == "" then
		return false, "the plugin carries no client agent (rebuild it)"
	end
	local playerGui = player:FindFirstChildOfClass("PlayerGui") or player:WaitForChild("PlayerGui", 30)
	if playerGui == nil then
		return false, "the player has no PlayerGui"
	end
	local old = playerGui:FindFirstChild(Config.AGENT_NAME)
	if old then
		old:Destroy()
	end
	local holder = tagged(Instance.new("ScreenGui")) :: ScreenGui
	holder.Name = Config.AGENT_NAME
	holder.ResetOnSpawn = false
	local agent = tagged(Instance.new("LocalScript")) :: LocalScript
	agent.Name = Config.AGENT_NAME
	local ok, problem = pcall(function()
		(agent :: any).Source = source
		for _, name in Config.AGENT_MODULES do
			local m = Instance.new("ModuleScript")
			m.Name = name;
			(m :: any).Source = Sources.get(name) or ""
			m.Parent = agent
		end
	end)
	if not ok then
		holder:Destroy()
		agent:Destroy()
		return false, "couldn't write the client agent's source: " .. tostring(problem)
	end
	agent.Parent = holder
	holder.Parent = playerGui
	return true, nil
end

-- Take out what older versions of the plugin put in the edit DataModel (and a run that crashed left behind).
function Agent.remove()
	for _, inst in CollectionService:GetTagged(Config.TEMP_TAG) do
		pcall(function()
			inst:Destroy()
		end)
	end
end

return Agent
