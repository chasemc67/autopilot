--!strict
-- Autopilot for Roblox Studio: the plugin's entry. A plugin runs once in each DataModel: in the edit one it is the
-- Autopilot dock and the link to the agent service (EditHost); in a play test's server it answers the game's requests
-- (PlayServer). In the play test's client the client agent the dock put in the place does the client's part.

local RunService = game:GetService("RunService")

if RunService:IsEdit() then
	require(script.Parent.EditHost).start(plugin)
elseif RunService:IsServer() and RunService:IsRunning() then
	require(script.Parent.PlayServer).start(plugin)
end
