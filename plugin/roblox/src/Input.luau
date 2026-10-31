--!strict
-- The player's hands, in the client agent. A Studio plugin can't make real key events (VirtualInputManager isn't
-- open to plugins; UserInputService's events can't be faked), so each key does what it usually means in a Roblox game:
--   W A S D / arrows -> Humanoid:Move (camera-relative), SpaceBar -> Humanoid.Jump, MouseX / MouseY -> the camera turns,
--   One..Nine -> equip the backpack's n-th Tool, LeftMouseButton -> the equipped Tool's Activate,
--   a key a ProximityPrompt in reach asks for -> InputHoldBegin / InputHoldEnd,
--   any key a ContextActionService action is bound to -> ContextActionService:CallFunction.
-- `input` presses a ContextActionService action, or a ProximityPrompt by its ActionText or name.
-- Holds are leased (let go when not renewed) or timed (`seconds` of game time), as the other engines' plugins do.

local ContextActionService = game:GetService("ContextActionService")
local Players = game:GetService("Players")
local RunService = game:GetService("RunService")
local Workspace = game:GetService("Workspace")

local Frame = require(script.Parent.Frame)
local World = require(script.Parent.World)

local Input = {}

local TAP_SECONDS = 0.05
local DEGREES_PER_COUNT = 0.3 -- (a mouse count turns the view this much; the brain measures it: calibrate_look)
local MOVE_KEYS = {
	W = Vector3.new(0, 0, -1),
	Up = Vector3.new(0, 0, -1),
	S = Vector3.new(0, 0, 1),
	Down = Vector3.new(0, 0, 1),
	A = Vector3.new(-1, 0, 0),
	Left = Vector3.new(-1, 0, 0),
	D = Vector3.new(1, 0, 0),
	Right = Vector3.new(1, 0, 0),
}
local DIGITS = { One = 1, Two = 2, Three = 3, Four = 4, Five = 5, Six = 6, Seven = 7, Eight = 8, Nine = 9 }
-- Unreal key names that aren't Roblox KeyCode names
local KEYCODES = {
	SpaceBar = "Space",
	Enter = "Return",
	LeftControl = "LeftControl",
	LeftShift = "LeftShift",
	LeftAlt = "LeftAlt",
	BackSpace = "Backspace",
	Zero = "Zero",
	LeftMouseButton = "MouseButton1",
	RightMouseButton = "MouseButton2",
	MiddleMouseButton = "MouseButton3",
}

type Held = { key: string, value: number, renewed: number, untilT: number, rate: boolean }

local held: { [string]: Held } = {}
local lease = 1.0
local moving: { dir: Vector3, scale: number }? = nil
local holdingPrompts: { [string]: ProximityPrompt } = {}

local function now(): number
	return os.clock()
end

local function keyCode(name: string): Enum.KeyCode?
	local wanted = KEYCODES[name] or name
	for _, item in Enum.KeyCode:GetEnumItems() do
		if item.Name == wanted then
			return item
		end
	end
	return nil
end

local function userInputType(name: string): Enum.UserInputType?
	local wanted = KEYCODES[name]
	if wanted and string.sub(wanted, 1, 11) == "MouseButton" then
		return (Enum.UserInputType :: any)[wanted]
	end
	return nil
end

-- The ContextActionService actions bound to a key (by Unreal name), or to an action name.
local function casActions(key: string?, action: string?): { string }
	local out = {}
	local ok, bound = pcall(function()
		return ContextActionService:GetAllBoundActionInfo()
	end)
	if not ok or type(bound) ~= "table" then
		return out
	end
	local code = key and keyCode(key)
	local uit = key and userInputType(key)
	for name, info in bound do
		if action and name == action then
			table.insert(out, name)
		elseif key and type(info) == "table" and type(info.inputTypes) == "table" then
			for _, t in info.inputTypes do
				if (code and t == code) or (uit and t == uit) then
					table.insert(out, name)
					break
				end
			end
		end
	end
	return out
end

local function callAction(name: string, down: boolean): boolean
	local ok = pcall(function()
		ContextActionService:CallFunction(name, down and Enum.UserInputState.Begin or Enum.UserInputState.End, nil)
	end)
	return ok
end

-- The prompts in reach of the player that a key (or an action text / name) triggers, nearest first.
local function prompts(key: string?, action: string?): { ProximityPrompt }
	local _, _, root = World.body()
	if root == nil then
		return {}
	end
	local code = key and keyCode(key)
	local found = {}
	for _, p in Workspace:GetDescendants() do
		if not p:IsA("ProximityPrompt") or not p.Enabled then
			continue
		end
		local prompt = p :: ProximityPrompt
		local matches = (code ~= nil and prompt.KeyboardKeyCode == code)
			or (action ~= nil and (prompt.ActionText == action or prompt.Name == action))
		if not matches then
			continue
		end
		local parent = prompt.Parent
		local at: Vector3? = nil
		if parent and parent:IsA("BasePart") then
			at = parent.Position
		elseif parent and parent:IsA("Attachment") then
			at = parent.WorldPosition
		elseif parent and parent:IsA("Model") then
			at = parent:GetPivot().Position
		end
		if at and (at - root.Position).Magnitude <= prompt.MaxActivationDistance + 2 then
			table.insert(found, { prompt = prompt, d = (at - root.Position).Magnitude })
		end
	end
	table.sort(found, function(a, b)
		return a.d < b.d
	end)
	local out = {}
	for _, f in found do
		table.insert(out, f.prompt)
	end
	return out
end

local function equippedTool(): Tool?
	local character = World.body()
	return character and character:FindFirstChildOfClass("Tool") or nil
end

local function turn(right: number, up: number)
	local camera = Workspace.CurrentCamera
	if camera == nil then
		return
	end
	local yaw = -math.rad(right * DEGREES_PER_COUNT)
	local pitch = math.rad(up * DEGREES_PER_COUNT)
	local cf = camera.CFrame
	local focus = camera.Focus.Position
	local offset = cf.Position - focus
	if offset.Magnitude < 0.5 then -- (first person: turn in place)
		local rx, ry = cf:ToOrientation()
		local newPitch = math.clamp(rx + pitch, math.rad(-80), math.rad(80))
		camera.CFrame = CFrame.new(cf.Position) * CFrame.fromOrientation(newPitch, ry + yaw, 0)
		return
	end
	local rotated = CFrame.fromAxisAngle(Vector3.yAxis, yaw):VectorToWorldSpace(offset)
	local tilted = CFrame.fromAxisAngle(cf.RightVector, -pitch):VectorToWorldSpace(rotated)
	local elevation = math.asin(math.clamp(tilted.Unit.Y, -1, 1))
	if math.abs(elevation) < math.rad(80) then
		rotated = tilted
	end
	camera.CFrame = CFrame.lookAt(focus + rotated, focus)
end

-- What a key does when it goes down (down = true) or up. Returns whether anything took it, and what.
local function apply(key: string, down: boolean, value: number): (boolean, string?)
	local _, humanoid = World.body()
	if MOVE_KEYS[key] then
		return true, "move"
	end
	if key == "SpaceBar" then
		if humanoid and down then
			humanoid.Jump = true
		end
		return true, "jump"
	end
	if key == "MouseX" or key == "MouseY" then
		if down then
			if key == "MouseX" then
				turn(value, 0)
			else
				turn(0, value)
			end
		end
		return true, "camera"
	end
	local took = false
	local what = nil
	for _, name in casActions(key, nil) do
		took = callAction(name, down) or took
		what = "action " .. name
	end
	if DIGITS[key] and down and humanoid then
		local player = Players.LocalPlayer
		local backpack = player and player:FindFirstChildOfClass("Backpack")
		local tools = backpack and backpack:GetChildren() or {}
		local tool = tools[DIGITS[key]]
		if tool and tool:IsA("Tool") then
			humanoid:EquipTool(tool)
			took, what = true, "equip " .. tool.Name
		end
	end
	if key == "LeftMouseButton" then
		local tool = equippedTool()
		if tool then
			if down then
				tool:Activate()
			else
				tool:Deactivate()
			end
			took, what = true, "tool " .. tool.Name
		end
	end
	if down then
		for _, p in prompts(key, nil) do
			p:InputHoldBegin()
			holdingPrompts[key] = p
			took, what = true, "prompt " .. p.ActionText
			break
		end
	elseif holdingPrompts[key] then
		holdingPrompts[key]:InputHoldEnd()
		holdingPrompts[key] = nil
		took = true
	end
	return took, what
end

local function known(key: string): boolean
	return MOVE_KEYS[key] ~= nil
		or key == "SpaceBar"
		or key == "MouseX"
		or key == "MouseY"
		or DIGITS[key] ~= nil
		or keyCode(key) ~= nil
		or userInputType(key) ~= nil
end

local function waitTap()
	local started, frame = now(), 0
	while now() - started < TAP_SECONDS or frame < 2 do
		RunService.RenderStepped:Wait()
		frame += 1
	end
end

local function release(key: string)
	local h = held[key]
	if h then
		held[key] = nil
		apply(key, false, 0)
	end
end

function Input.releaseAll()
	for key in held do
		release(key)
	end
	moving = nil
	for key, p in holdingPrompts do
		pcall(function()
			p:InputHoldEnd()
		end)
		holdingPrompts[key] = nil
	end
end

-- `key`: {"key", "mode": "press" | "hold" | "release" | "release_all", "value"?, "lease"?, "seconds"?, "rate"?}.
-- Yields for a press (down, then up a moment later).
function Input.key(args: { [string]: any }): (boolean, any)
	local mode = tostring(args.mode or "press")
	if tonumber(args.lease) and tonumber(args.lease) > 0 then
		lease = math.clamp(tonumber(args.lease) :: number, 0.2, 10)
	end
	if mode == "release_all" then
		Input.releaseAll()
		return true, {}
	end
	local key = tostring(args.key or "")
	if key == "" then
		return true, {} -- (the brain's probe: an empty key changes nothing)
	end
	if not known(key) then
		return false, "Unknown key " .. key
	end
	local value = tonumber(args.value) or 1
	if mode == "release" then
		release(key)
		return true, {}
	end
	if mode == "hold" then
		local seconds = tonumber(args.seconds) or 0
		local h = held[key]
		if h == nil then
			held[key] = { key = key, value = value, renewed = now(), untilT = 0, rate = args.rate == true }
			apply(key, true, value)
			h = held[key]
		end
		h.value, h.renewed, h.rate = value, now(), args.rate == true
		h.untilT = seconds > 0 and (time() + seconds) or 0
		return true, {}
	end
	local took, what = apply(key, true, value)
	if key == "MouseX" or key == "MouseY" then
		return true, {}
	end
	waitTap()
	if not held[key] then
		apply(key, false, value)
	end
	if not took then
		return true, { note = key .. " isn't bound to anything Autopilot can press in this game" }
	end
	return true, { did = what }
end

-- `input`: {"action", "mode": "press" | "hold" | "release"}: a ContextActionService action or a ProximityPrompt.
function Input.input(args: { [string]: any }): (boolean, any)
	local action = tostring(args.action or "")
	local mode = tostring(args.mode or "press")
	local names = casActions(nil, action)
	local ps = #names == 0 and prompts(nil, action) or {}
	if #names == 0 and #ps == 0 then
		return false, "No input action named " .. action
	end
	local function set(down: boolean)
		for _, n in names do
			callAction(n, down)
		end
		for _, p in ps do
			if down then
				p:InputHoldBegin()
			else
				p:InputHoldEnd()
			end
			break
		end
	end
	if mode == "release" then
		set(false)
		return true, {}
	end
	set(true)
	if mode == "hold" then
		return true, {}
	end
	local hold = 0
	if ps[1] then
		hold = ps[1].HoldDuration
	end
	local started = now()
	waitTap()
	while now() - started < hold + 0.05 do
		RunService.RenderStepped:Wait()
	end
	set(false)
	return true, {}
end

-- `move`: {"direction" (kit), "seconds", "scale"?} -> {"moved": kit cm across the floor}. Yields for `seconds`.
function Input.move(args: { [string]: any }): (boolean, any)
	local _, humanoid, root = World.body()
	if humanoid == nil or root == nil then
		return false, "No player"
	end
	local dir = Frame.nativeDir(args.direction)
	dir = Vector3.new(dir.X, 0, dir.Z)
	if dir.Magnitude > 1e-4 then
		dir = dir.Unit
	end
	local scale = math.clamp(tonumber(args.scale) or 1, 0, 1)
	local seconds = math.clamp(tonumber(args.seconds) or 0.35, 0, 30)
	local start = root.Position
	moving = { dir = dir, scale = scale }
	local started = now()
	while now() - started < seconds do
		RunService.RenderStepped:Wait()
	end
	moving = nil
	humanoid:Move(Vector3.zero, false)
	local stop = root.Position
	local moved = Vector3.new(stop.X - start.X, 0, stop.Z - start.Z).Magnitude * Frame.STUD_CM
	return true, { moved = Frame.r2(moved) }
end

-- Every frame, after the game's own controls (so a held key wins over the PlayerModule's zero input) and before the
-- camera: walk, jump, turn, and let go of what wasn't renewed.
local function step(dt: number)
	local t = now()
	local gameT = time()
	for key, h in held do
		local expired
		if h.untilT > 0 then
			expired = gameT >= h.untilT
		else
			expired = t - h.renewed > lease
		end
		if expired then
			release(key)
		elseif key == "MouseX" or key == "MouseY" then
			local v = h.rate and h.value * dt or h.value
			apply(key, true, v)
		elseif key == "SpaceBar" then
			apply(key, true, h.value)
		end
	end
	local _, humanoid = World.body()
	if humanoid == nil then
		return
	end
	if moving then
		humanoid:Move(moving.dir * moving.scale, false)
		return
	end
	local local_ = Vector3.zero
	for key, h in held do
		local v = MOVE_KEYS[key]
		if v then
			local_ += v * math.clamp(h.value, -1, 1)
		end
	end
	if local_.Magnitude > 1e-4 then
		humanoid:Move(local_.Magnitude > 1 and local_.Unit or local_, true)
	end
end

function Input.start()
	RunService:BindToRenderStep("AutopilotInput", Enum.RenderPriority.Input.Value + 1, step)
end

function Input.stop()
	Input.releaseAll()
	pcall(function()
		RunService:UnbindFromRenderStep("AutopilotInput")
	end)
end

return Input
