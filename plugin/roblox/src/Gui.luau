--!strict
-- `screen`, `ui`, `ui_act` in the client agent: the player's PlayerGui. Keys are `<ScreenGui>.<Name>` (a second one of
-- the same key gets `@2`). A GuiButton can't be clicked from Luau (no Activate method; its events can't be fired), so
-- `ui_act` click answers 501; `set` changes a TextBox's text.

local Players = game:GetService("Players")

local Json = require(script.Parent.Json)
local Paths = require(script.Parent.Paths)

local Gui = {}

local function playerGui(): PlayerGui?
	local p = Players.LocalPlayer
	return p and p:FindFirstChildOfClass("PlayerGui") or nil
end

local function shown(g: GuiObject): boolean
	local cur: Instance? = g
	while cur do
		if cur:IsA("GuiObject") and not cur.Visible then
			return false
		end
		if cur:IsA("LayerCollector") then
			return cur.Enabled
		end
		cur = cur.Parent
	end
	return true
end

local function owner(g: Instance): string
	local layer = g:FindFirstAncestorWhichIsA("LayerCollector")
	return layer and layer.Name or "Screen"
end

local function textOf(g: Instance): string
	if g:IsA("TextLabel") or g:IsA("TextButton") or g:IsA("TextBox") then
		return (g :: any).ContentText ~= "" and (g :: any).ContentText or (g :: any).Text
	end
	return ""
end

-- The shown GUI objects that pass `keep`, each with its key: { {key, object} }.
local function keyed(keep: (GuiObject) -> boolean): { { any } }
	local root = playerGui()
	local out = {}
	if root == nil then
		return out
	end
	local counts = {}
	for _, g in root:GetDescendants() do
		if g:IsA("GuiObject") and keep(g) and shown(g) then
			local key = owner(g) .. "." .. g.Name
			counts[key] = (counts[key] or 0) + 1
			if counts[key] > 1 then
				key = key .. "@" .. counts[key]
			end
			table.insert(out, { key, g })
		end
	end
	return out
end

-- `screen`: {"texts": {key: text}} (and "ui" with `ui: true`).
function Gui.screen(args: { [string]: any }): { [string]: any }
	local texts = {}
	for _, kg in
		keyed(function(g)
			return textOf(g) ~= ""
		end)
	do
		texts[kg[1]] = textOf(kg[2])
	end
	local out: { [string]: any } = { texts = texts }
	if args.ui then
		out.ui = Gui.list()
	end
	return out
end

local function kindOf(g: GuiObject): string
	if g:IsA("TextBox") then
		return "text_input"
	elseif g:IsA("GuiButton") then
		return "button"
	end
	return ""
end

local last: { [string]: GuiObject } = {}

-- `ui`: the buttons and text boxes on screen.
function Gui.list(): { any }
	table.clear(last)
	local out = Json.array()
	for _, kg in
		keyed(function(g)
			return kindOf(g) ~= ""
		end)
	do
		local key, g = kg[1], kg[2] :: GuiObject
		last[key] = g
		local center = g.AbsolutePosition + g.AbsoluteSize / 2
		local label = textOf(g)
		if label == "" then
			local inner = g:FindFirstChildWhichIsA("TextLabel", true)
			label = inner and inner.Text or ""
		end
		local item: { [string]: any } = {
			id = key,
			kind = kindOf(g),
			name = g.Name,
			owner = owner(g),
			label = label,
			enabled = g.Active and (not g:IsA("GuiButton") or (g :: any).Interactable ~= false),
			path = Paths.of(g),
			class = g.ClassName,
			screen = { x = math.round(center.X), y = math.round(center.Y) },
		}
		if g:IsA("TextBox") and g.PlaceholderText ~= "" then
			item.placeholder = g.PlaceholderText
		end
		table.insert(out, item)
	end
	return out
end

-- `ui_act`: {"id" (a key from `ui`, or a path), "action": "click" | "set" | ..., "value"? / "text"?}.
function Gui.act(args: { [string]: any }): (boolean, any, number?)
	local id = tostring(args.id or "")
	local g: Instance? = last[id]
	if g == nil or g.Parent == nil then
		Gui.list()
		g = last[id] or Paths.resolve(id)
	end
	if g == nil then
		return false, "no element " .. id .. " on screen", 400
	end
	local action = tostring(args.action or "click")
	if action == "set" then
		if g:IsA("TextBox") then
			g.Text = tostring(args.text or args.value or "")
			return true, {}
		end
		return false, id .. " has no value to set (only text boxes here)", 400
	end
	return false,
		"a Roblox plugin can't click GUI buttons (GuiButton has no Activate, and its events can't be fired from "
			.. "Luau): do what the button's Activated handler does, or report the run as blocked",
		501
end

return Gui
