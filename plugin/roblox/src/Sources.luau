--!strict
-- The source text of the plugin's own modules, for the client agent the plugin puts in the place (Agent.luau). In the
-- Rojo build the modules are the plugin's ModuleScripts and their Source is read; the single-file build
-- (build_plugin.py) replaces this module with the texts themselves.

local Sources = {}

function Sources.get(name: string): string?
	local m = script.Parent:FindFirstChild(name)
	if m and m:IsA("LuaSourceContainer") then
		return (m :: any).Source
	end
	return nil
end

return Sources
