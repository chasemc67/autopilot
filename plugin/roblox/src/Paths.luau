--!strict
-- Instance paths as the bridge names things: services first, slashes between names, a second sibling of one name
-- `@2` (a third `@3`): /Workspace/Coins/Coin@2. In a name, "%", "/" and "@" are written %25, %2F and %40.

local Paths = {}

local function escape(name: string): string
	return (name:gsub("%%", "%%25"):gsub("/", "%%2F"):gsub("@", "%%40"))
end

local function unescape(part: string): string
	return (part:gsub("%%2[Ff]", "/"):gsub("%%40", "@"):gsub("%%25", "%%"))
end

-- The segment of one instance under its parent: its escaped name, with @k when it is the k-th child of that name.
local function segment(inst: Instance): string
	local name = inst.Name
	local parent = inst.Parent
	if parent == nil then
		return escape(name)
	end
	local k = 0
	for _, child in parent:GetChildren() do
		if child.Name == name then
			k += 1
			if child == inst then
				break
			end
		end
	end
	if k > 1 then
		return escape(name) .. "@" .. k
	end
	return escape(name)
end

-- The path of an instance ("" for one outside the DataModel, "/" for the DataModel itself).
function Paths.of(inst: Instance?): string
	if inst == nil then
		return ""
	end
	if inst == game then
		return "/"
	end
	local parts = {}
	local cur: Instance? = inst
	while cur and cur ~= game do
		table.insert(parts, 1, segment(cur))
		cur = cur.Parent
	end
	if cur ~= game then
		return ""
	end
	return "/" .. table.concat(parts, "/")
end

-- The last part of a path ("/Workspace/Coins/Coin@2" -> "Coin@2").
function Paths.name(path: string): string
	return string.match(path, "([^/]*)$") or path
end

local function child(parent: Instance, part: string): Instance?
	local name, nth = string.match(part, "^(.*)@(%d+)$")
	local want = unescape(name or part)
	local k = tonumber(nth) or 1
	local seen = 0
	for _, c in parent:GetChildren() do
		if c.Name == want then
			seen += 1
			if seen == k then
				return c
			end
		end
	end
	if parent == game and k == 1 then -- (a service not created yet: GetService makes it)
		local ok, service = pcall(function()
			return game:GetService(want)
		end)
		if ok then
			return service
		end
	end
	return nil
end

-- The instance at a path (also: "workspace" or "game" as the first part, and dotted "Workspace.Map.Door" when it has
-- no slash), or nil.
function Paths.resolve(path: string?): Instance?
	if type(path) ~= "string" or path == "" then
		return nil
	end
	if path == "/" or path == "game" then
		return game
	end
	local parts = {}
	if string.find(path, "/", 1, true) then
		for part in string.gmatch(path, "[^/]+") do
			table.insert(parts, part)
		end
	else
		for part in string.gmatch(path, "[^%.]+") do
			table.insert(parts, part)
		end
	end
	if parts[1] == "game" then
		table.remove(parts, 1)
	end
	if parts[1] == "workspace" then
		parts[1] = "Workspace"
	end
	local cur: Instance = game
	for _, part in parts do
		local nxt = child(cur, part)
		if nxt == nil then
			return nil
		end
		cur = nxt
	end
	return cur
end

return Paths
