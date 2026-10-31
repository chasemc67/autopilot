--!strict
-- `codex_manifest` and `codex_read` (the edit instance): the place's scripts as text, and a summary of the place.
-- Files are named under AutopilotCodex/ (the brain puts them at its codex's top, autopilot/engines/roblox.py):
--   AutopilotCodex/scripts/<Service>/<path>.server.luau | .client.luau | .luau   (Script, LocalScript, ModuleScript)
--   AutopilotCodex/summary.txt
-- A script's text is the editor's (ScriptEditorService:GetEditorSource: unsaved edits included), else its Source.

local CollectionService = game:GetService("CollectionService")
local ScriptEditorService = game:GetService("ScriptEditorService")

local Config = require(script.Parent.Config)
local Json = require(script.Parent.Json)
local Paths = require(script.Parent.Paths)

local Codex = {}

local PREFIX = "AutopilotCodex/"
local SUMMARY = PREFIX .. "summary.txt"
local SERVICES = {
	"Workspace",
	"ReplicatedFirst",
	"ReplicatedStorage",
	"ServerScriptService",
	"ServerStorage",
	"StarterGui",
	"StarterPack",
	"StarterPlayer",
	"Lighting",
	"SoundService",
	"Teams",
	"TextChatService",
}
local EXT = { Script = ".server.luau", LocalScript = ".client.luau", ModuleScript = ".luau" }
local MAX_FILES = 5000
local MAX_TOTAL = 64 * 1024 * 1024
local MAX_READ = 8 * 1024 * 1024
local MAX_SUMMARY = 256 * 1024

local byPath: { [string]: LuaSourceContainer } = {}
local summaryText = ""

local function service(name: string): Instance?
	local ok, s = pcall(function()
		return game:GetService(name)
	end)
	return ok and s or nil
end

local function ours(inst: Instance): boolean
	return CollectionService:HasTag(inst, Config.TEMP_TAG)
		or inst:FindFirstAncestor(Config.AGENT_NAME) ~= nil
		or inst.Name == Config.AGENT_NAME
end

function Codex.source(s: LuaSourceContainer): string
	local ok, text = pcall(function()
		return ScriptEditorService:GetEditorSource(s)
	end)
	if ok and type(text) == "string" then
		return text
	end
	local ok2, source = pcall(function()
		return (s :: any).Source
	end)
	return ok2 and tostring(source) or ""
end

-- A cheap hash (FNV-1a, 32 bits) and the length: enough for the brain to tell a file changed.
local function hash(text: string): string
	local h = 2166136261
	for i = 1, #text do
		h = bit32.bxor(h, string.byte(text, i))
		h = (bit32.lshift(h, 24) + h * 403) % 4294967296 -- (h * 16777619, exact: 16777619 = 2^24 + 403)
	end
	return string.format("%08x-%d", h, #text)
end

local function scripts(): { LuaSourceContainer }
	local out = {}
	for _, name in SERVICES do
		local s = service(name)
		if s then
			for _, d in s:GetDescendants() do
				if d:IsA("LuaSourceContainer") and EXT[d.ClassName] and not ours(d) then
					table.insert(out, d :: LuaSourceContainer)
				end
			end
		end
	end
	return out
end

function Codex.pathOf(s: LuaSourceContainer): string
	return PREFIX .. "scripts" .. Paths.of(s) .. EXT[s.ClassName]
end

-- The place at a glance, as text.
function Codex.summary(): string
	local lines = {}
	local function add(line: string)
		table.insert(lines, line)
	end
	add(string.format("Roblox place: %s (PlaceId %d, GameId %d)", game.Name, game.PlaceId, game.GameId))
	local ws = service("Workspace") :: Workspace
	add(
		string.format(
			"Workspace: Gravity %s studs/s^2, FallenPartsDestroyHeight %s",
			tostring(ws.Gravity),
			tostring(ws.FallenPartsDestroyHeight)
		)
	)
	local sp = service("StarterPlayer") :: StarterPlayer
	if sp then
		add(
			string.format(
				"StarterPlayer: CharacterWalkSpeed %s, CharacterUseJumpPower %s, CharacterJumpPower %s, CharacterJumpHeight %s, "
					.. "CameraMode %s, CameraMaxZoomDistance %s, AutoJumpEnabled %s",
				tostring(sp.CharacterWalkSpeed),
				tostring(sp.CharacterUseJumpPower),
				tostring(sp.CharacterJumpPower),
				tostring(sp.CharacterJumpHeight),
				tostring(sp.CameraMode),
				tostring(sp.CameraMaxZoomDistance),
				tostring(sp.AutoJumpEnabled)
			)
		)
	end
	local teams = service("Teams")
	if teams and #teams:GetChildren() > 0 then
		local names = {}
		for _, team in teams:GetChildren() do
			table.insert(names, team.Name)
		end
		add("Teams: " .. table.concat(names, ", "))
	end
	local tags = {}
	for _, tag in CollectionService:GetAllTags() do
		if tag ~= Config.TEMP_TAG then
			table.insert(tags, string.format("%s (%d)", tag, #CollectionService:GetTagged(tag)))
		end
	end
	if #tags > 0 then
		add("CollectionService tags: " .. table.concat(tags, ", "))
	end
	local counts = { Script = 0, LocalScript = 0, ModuleScript = 0 }
	for _, s in scripts() do
		counts[s.ClassName] += 1
	end
	add(
		string.format(
			"Scripts: %d Scripts, %d LocalScripts, %d ModuleScripts (in scripts/)",
			counts.Script,
			counts.LocalScript,
			counts.ModuleScript
		)
	)
	add("")
	add("Remotes and bindables (how client and server talk):")
	for _, name in SERVICES do
		local s = service(name)
		for _, d in (s and s:GetDescendants() or {}) do
			if
				(
					d:IsA("RemoteEvent")
					or d:IsA("RemoteFunction")
					or d:IsA("UnreliableRemoteEvent")
					or d:IsA("BindableEvent")
				) and not ours(d)
			then
				add(string.format("  %s (%s)", Paths.of(d), d.ClassName))
			end
		end
	end
	add("")
	add("The place's tree (parts in a model counted, not listed; attributes shown as {name=value}):")
	local function describe(inst: Instance): string
		local bits = { Paths.name(Paths.of(inst)) .. " (" .. inst.ClassName .. ")" }
		local attributes = {}
		for k, v in inst:GetAttributes() do
			table.insert(attributes, k .. "=" .. tostring(v))
		end
		if #attributes > 0 then
			table.insert(bits, "{" .. table.concat(attributes, ", ") .. "}")
		end
		if inst:IsA("ValueBase") then
			table.insert(bits, "= " .. tostring((inst :: any).Value))
		elseif inst:IsA("ProximityPrompt") then
			table.insert(
				bits,
				string.format(
					'"%s" key %s hold %ss',
					inst.ActionText,
					tostring(inst.KeyboardKeyCode),
					tostring(inst.HoldDuration)
				)
			)
		elseif inst:IsA("TextLabel") or inst:IsA("TextButton") or inst:IsA("TextBox") then
			if (inst :: any).Text ~= "" then
				table.insert(bits, string.format('"%s"', string.sub((inst :: any).Text, 1, 80)))
			end
		elseif inst:IsA("BasePart") then
			local p = inst.Position
			table.insert(bits, string.format("at (%.1f, %.1f, %.1f)", p.X, p.Y, p.Z))
		end
		return table.concat(bits, " ")
	end
	local size = 0
	local function walk(inst: Instance, depth: number)
		if size > MAX_SUMMARY or ours(inst) then
			return
		end
		local line = string.rep("  ", depth) .. describe(inst)
		size += #line
		add(line)
		local parts = 0
		for _, c in inst:GetChildren() do
			if c:IsA("BasePart") and (inst:IsA("Model") or depth > 3) and next(c:GetAttributes()) == nil then
				parts += 1
			elseif not c:IsA("LuaSourceContainer") or depth < 1 then
				if depth < 8 then
					walk(c, depth + 1)
				end
			end
		end
		if parts > 0 then
			add(string.rep("  ", depth + 1) .. string.format("(%d parts)", parts))
		end
	end
	for _, name in SERVICES do
		local s = service(name)
		if s and name ~= "TextChatService" then
			walk(s, 0)
		end
	end
	return table.concat(lines, "\n") .. "\n"
end

-- `codex_manifest`: {"export"?: "skip"} -> {"project", "files": [{"path", "size", "hash"}], "truncated", "export"}.
function Codex.manifest(args: { [string]: any }): { [string]: any }
	local started = os.clock()
	table.clear(byPath)
	local files = Json.array()
	local total = 0
	local truncated = false
	for _, s in scripts() do
		if #files >= MAX_FILES then
			truncated = true
			break
		end
		local text = Codex.source(s)
		if total + #text > MAX_TOTAL then
			truncated = true
			continue
		end
		total += #text
		local path = Codex.pathOf(s)
		byPath[path] = s
		table.insert(files, { path = path, size = #text, hash = hash(text) })
	end
	if args.export ~= "skip" or summaryText == "" then
		summaryText = Codex.summary()
	end
	table.insert(files, { path = SUMMARY, size = #summaryText, hash = hash(summaryText) })
	return {
		project = game.Name,
		files = files,
		truncated = truncated,
		export = {
			wrote = args.export ~= "skip",
			seconds = math.round((os.clock() - started) * 100) / 100,
			file = SUMMARY,
		},
	}
end

-- `codex_read`: {"paths", "max_bytes"?} -> {"files": {path: text}, "missing", "too_big"}.
function Codex.read(args: { [string]: any }): { [string]: any }
	local maxBytes = math.clamp(tonumber(args.max_bytes) or 512000, 1, MAX_READ)
	if next(byPath) == nil then
		Codex.manifest({ export = "skip" })
	end
	local out, missing, tooBig = {}, Json.array(), Json.array()
	local used = 0
	for _, asked in (type(args.paths) == "table" and args.paths or {}) do
		local text: string? = nil
		if asked == SUMMARY then
			text = summaryText ~= "" and summaryText or Codex.summary()
		elseif byPath[asked] and byPath[asked].Parent then
			text = Codex.source(byPath[asked])
		end
		if text == nil then
			table.insert(missing, asked)
		elseif #text > maxBytes then
			table.insert(tooBig, asked)
		elseif used + #text > maxBytes then
			break -- (the brain asks again for the rest)
		else
			out[asked] = text
			used += #text
		end
	end
	return { files = out, missing = missing, too_big = tooBig }
end

return Codex
