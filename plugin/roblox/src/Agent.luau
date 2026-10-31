--!strict
-- What the edit instance puts in the place for a run, just before it starts the play test (so the play test's copy of
-- the place has them), and takes out when the test ends: the client agent LocalScript (ClientAgent.luau, with the
-- modules it needs under it) in StarterPlayer.StarterPlayerScripts, the RemoteFunction the server half calls it
-- through and the RemoteEvent it reports the client's errors on, in ReplicatedStorage. All tagged AutopilotTemporary
-- (Studio's undo history sees them come and go).

local CollectionService = game:GetService("CollectionService")
local ReplicatedStorage = game:GetService("ReplicatedStorage")
local StarterPlayer = game:GetService("StarterPlayer")

local Config = require(script.Parent.Config)
local Sources = require(script.Parent.Sources)

local Agent = {}

local function tagged(inst: Instance): Instance
	CollectionService:AddTag(inst, Config.TEMP_TAG)
	inst.Archivable = true
	return inst
end

-- Put the agent in the place. Returns (ok, why not).
function Agent.insert(): (boolean, string?)
	Agent.remove()
	local source = Sources.get("ClientAgent")
	if source == nil or source == "" then
		return false, "the plugin carries no client agent (rebuild it)"
	end
	local remote = tagged(Instance.new("RemoteFunction"))
	remote.Name = Config.REMOTE_NAME
	local events = tagged(Instance.new("RemoteEvent"))
	events.Name = Config.EVENTS_NAME
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
		agent:Destroy()
		remote:Destroy()
		events:Destroy()
		return false, "couldn't write the client agent's source: " .. tostring(problem)
	end
	remote.Parent = ReplicatedStorage
	events.Parent = ReplicatedStorage
	agent.Parent = StarterPlayer:FindFirstChildOfClass("StarterPlayerScripts") or StarterPlayer
	return true, nil
end

-- Take out everything Agent.insert put in (also what a run that crashed left behind).
function Agent.remove()
	for _, inst in CollectionService:GetTagged(Config.TEMP_TAG) do
		pcall(function()
			inst:Destroy()
		end)
	end
end

return Agent
