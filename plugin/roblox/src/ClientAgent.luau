--!strict
-- AutopilotClientAgent: the plugin's hands and eyes in the play test's client. This file is not run by the plugin:
-- the plugin copies its source into a LocalScript (with Json, Frame, Paths, Values, World, Sight, Input and Gui as
-- ModuleScripts under it) in StarterPlayer.StarterPlayerScripts just before it starts a play test, and removes it
-- when the test ends. The plugin's server half calls it through ReplicatedStorage.AutopilotAgentRemote
-- (op, args) -> (ok, result as JSON text | error, status), and it passes the client's errors on through
-- ReplicatedStorage.AutopilotAgentEvents.

local LogService = game:GetService("LogService")
local ReplicatedStorage = game:GetService("ReplicatedStorage")
local Workspace = game:GetService("Workspace")

local Gui = require(script:WaitForChild("Gui"))
local Input = require(script:WaitForChild("Input"))
local Json = require(script:WaitForChild("Json"))
local Sight = require(script:WaitForChild("Sight"))
local World = require(script:WaitForChild("World"))

local remote = ReplicatedStorage:WaitForChild("AutopilotAgentRemote", 30) :: RemoteFunction?
local events = ReplicatedStorage:WaitForChild("AutopilotAgentEvents", 30) :: RemoteEvent?
if remote == nil then
	warn("Autopilot: the client agent found no AutopilotAgentRemote; it does nothing")
	return
end

local function handle(op: string, args: { [string]: any }): (boolean, any, number?)
	if op == "pose" then
		return World.pose(Workspace.CurrentCamera)
	elseif op == "view" then
		return Sight.view(args)
	elseif op == "screen" then
		return true, Gui.screen(args)
	elseif op == "ui" then
		return true, { elements = Gui.list() }
	elseif op == "ui_act" then
		return Gui.act(args)
	elseif op == "key" then
		return Input.key(args)
	elseif op == "input" then
		return Input.input(args)
	elseif op == "move" then
		return Input.move(args)
	elseif op == "release_all" then
		Input.releaseAll()
		return true, {}
	elseif op == "ping" then
		return true, { client = true }
	end
	return false, "unknown op " .. tostring(op), 400
end

remote.OnClientInvoke = function(op: string, args: any)
	local ok, good, result, status = pcall(handle, op, type(args) == "table" and args or {})
	if not ok then
		return false, "the client agent failed: " .. tostring(good), 500
	end
	if good then
		return true, Json.encode(result)
	end
	return false, tostring(result), status or 400
end

Input.start()

if events then
	LogService.MessageOut:Connect(function(message: string, kind: Enum.MessageType)
		if string.sub(message, 1, 10) == "Autopilot:" then
			return
		end
		if kind == Enum.MessageType.MessageError then
			events:FireServer("log", "Error", message)
		elseif kind == Enum.MessageType.MessageWarning then
			events:FireServer("log", "Warning", message)
		end
	end)
	events:FireServer("ready")
end
