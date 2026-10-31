--!strict
-- `rc` in Roblox's terms: {"verb": "probe" | "property" | "call" | "describe", "body": {"objectPath", ...}}.
-- Properties by name, else the instance's attribute of that name, else the Value of a ValueBase child of that name.
-- Values are Roblox's own (Values.luau). "Pivot" is a Model's or part's pivot position; writing it moves the thing
-- (PivotTo, keeping its rotation).

local Json = require(script.Parent.Json)
local Paths = require(script.Parent.Paths)
local Values = require(script.Parent.Values)

local Rc = {}

-- The properties `props` and `describe` list, by class (Roblox has no property reflection at run time).
local PROPS: { { string | { string } } } = {
	{ "Instance", { "Name", "ClassName", "Parent" } },
	{
		"BasePart",
		{
			"Position",
			"Orientation",
			"Size",
			"Anchored",
			"CanCollide",
			"CanTouch",
			"Transparency",
			"Color",
			"Material",
			"AssemblyLinearVelocity",
			"Locked",
		},
	},
	{ "Model", { "PrimaryPart", "WorldPivot" } },
	{
		"Humanoid",
		{
			"Health",
			"MaxHealth",
			"WalkSpeed",
			"JumpPower",
			"JumpHeight",
			"UseJumpPower",
			"HipHeight",
			"MoveDirection",
			"FloorMaterial",
			"Sit",
			"PlatformStand",
			"MaxSlopeAngle",
			"RigType",
		},
	},
	{ "Player", { "UserId", "DisplayName", "Team", "Character" } },
	{ "ValueBase", { "Value" } },
	{ "GuiObject", { "Visible", "Position", "Size", "AbsolutePosition", "AbsoluteSize", "BackgroundTransparency" } },
	{ "TextLabel", { "Text" } },
	{ "TextButton", { "Text" } },
	{ "TextBox", { "Text", "PlaceholderText" } },
	{ "LayerCollector", { "Enabled" } },
	{
		"ProximityPrompt",
		{ "ActionText", "ObjectText", "Enabled", "KeyboardKeyCode", "HoldDuration", "MaxActivationDistance" },
	},
	{ "ClickDetector", { "MaxActivationDistance" } },
	{ "Tool", { "Enabled", "RequiresHandle", "ToolTip" } },
	{ "Sound", { "Playing", "SoundId", "Volume", "TimePosition" } },
	{ "Light", { "Enabled", "Brightness", "Range" } },
	{ "Camera", { "CFrame", "FieldOfView", "CameraType", "CameraSubject" } },
	{ "Team", { "TeamColor" } },
	{ "BaseScript", { "Enabled" } },
	{ "Attachment", { "WorldPosition" } },
	{ "Constraint", { "Enabled" } },
	{ "SpawnLocation", { "Enabled", "Neutral" } },
}

local FUNCTIONS: { { string | { string } } } = {
	{
		"Instance",
		{
			"GetChildren",
			"GetDescendants",
			"FindFirstChild",
			"GetAttribute",
			"GetAttributes",
			"SetAttribute",
			"IsA",
			"Destroy",
			"Clone",
			"GetFullName",
		},
	},
	{ "PVInstance", { "GetPivot", "PivotTo" } },
	{ "Model", { "GetBoundingBox", "GetExtentsSize" } },
	{ "BasePart", { "GetMass", "GetTouchingParts", "ApplyImpulse" } },
	{ "Humanoid", { "TakeDamage", "Move", "MoveTo", "EquipTool", "UnequipTools", "GetState", "ChangeState" } },
	{ "RemoteEvent", { "FireServer", "FireClient", "FireAllClients" } },
	{ "RemoteFunction", { "InvokeServer" } },
	{ "BindableEvent", { "Fire" } },
	{ "BindableFunction", { "Invoke" } },
	{ "ProximityPrompt", { "InputHoldBegin", "InputHoldEnd" } },
	{ "Tool", { "Activate", "Deactivate" } },
	{ "Sound", { "Play", "Stop" } },
	{ "Player", { "LoadCharacter", "Kick" } },
}

local function classList(inst: Instance, table_: { { string | { string } } }): { string }
	local out = {}
	for _, entry in table_ do
		local cls, names = entry[1] :: string, entry[2] :: { string }
		local isA = false
		pcall(function()
			isA = inst:IsA(cls)
		end)
		if isA then
			for _, n in names do
				table.insert(out, n)
			end
		end
	end
	return out
end

local function err(message: string, status: number?): (boolean, string, number)
	return false, message, status or 400
end

-- (found, value) of a name on an instance: its property, else its attribute, else a ValueBase child's Value.
function Rc.read(inst: Instance, name: string): (boolean, any)
	if name == "Pivot" and inst:IsA("PVInstance") then
		return true, (inst :: PVInstance):GetPivot().Position
	end
	local ok, value = pcall(function()
		return (inst :: any)[name]
	end)
	if ok then
		if typeof(value) == "Instance" and value.Parent == inst and value.Name == name then
			-- (a child, not a property: a value object stands for its value)
			if value:IsA("ValueBase") then
				return true, (value :: any).Value
			end
			if inst:GetAttribute(name) ~= nil then
				return true, inst:GetAttribute(name)
			end
			return true, value
		end
		if typeof(value) ~= "function" then
			return true, value
		end
	end
	local attribute = inst:GetAttribute(name)
	if attribute ~= nil then
		return true, attribute
	end
	return false, nil
end

local function readAll(inst: Instance): { [string]: any }
	local out = {}
	for _, n in classList(inst, PROPS) do
		local found, value = Rc.read(inst, n)
		if found then
			out[n] = Values.toJson(value)
		end
	end
	if inst:IsA("PVInstance") then
		out.Pivot = Values.toJson((inst :: PVInstance):GetPivot().Position)
	end
	for k, v in inst:GetAttributes() do
		out[k] = Values.toJson(v)
	end
	for _, c in inst:GetChildren() do
		if c:IsA("ValueBase") and out[c.Name] == nil then
			out[c.Name] = Values.toJson((c :: any).Value)
		end
	end
	return out
end

local function write(inst: Instance, name: string, value: any): (boolean, any, number?)
	if name == "Pivot" and inst:IsA("PVInstance") then
		local pv = inst :: PVInstance
		local at = Values.fromJson(value, Vector3.zero)
		local pivot = pv:GetPivot()
		pv:PivotTo(CFrame.new(at) * pivot.Rotation)
		return true, {}
	end
	local isProp, current = pcall(function()
		return (inst :: any)[name]
	end)
	local child = inst:FindFirstChild(name)
	if isProp and not (child ~= nil and current == child) and typeof(current) ~= "function" then
		local ok, problem = pcall(function()
			(inst :: any)[name] = Values.fromJson(value, current)
		end)
		if not ok then
			return err(string.format("Couldn't set %s on %s: %s", name, Paths.of(inst), tostring(problem)))
		end
		return true, {}
	end
	if inst:GetAttribute(name) ~= nil then
		inst:SetAttribute(name, Values.fromJson(value, inst:GetAttribute(name)))
		return true, {}
	end
	if child and child:IsA("ValueBase") then
		local ok, problem = pcall(function()
			(child :: any).Value = Values.fromJson(value, (child :: any).Value)
		end)
		if not ok then
			return err(string.format("Couldn't set %s: %s", Paths.of(child), tostring(problem)))
		end
		return true, {}
	end
	return err(string.format("Property %s does not exist on %s", name, Paths.of(inst)))
end

local function call(inst: Instance, name: string, parameters: any): (boolean, any, number?)
	local fn
	local ok = pcall(function()
		fn = (inst :: any)[name]
	end)
	if not ok or typeof(fn) ~= "function" then
		return err(string.format("Function %s does not exist on %s", name, Paths.of(inst)))
	end
	-- Roblox methods take arguments in order: `args` (a list), or `parameters` itself when it is one
	local list = {}
	if type(parameters) == "table" then
		if type(parameters.args) == "table" then
			list = parameters.args
		elseif #parameters > 0 then
			list = parameters
		elseif next(parameters) ~= nil then
			return err("Roblox methods take their arguments in order: pass them as args=[...]")
		end
	end
	local converted = {}
	for i, a in list do
		converted[i] = Values.fromJson(a, nil)
	end
	local results = table.pack(pcall(fn, inst, table.unpack(converted, 1, #list)))
	if not results[1] then
		return err(string.format("%s:%s failed: %s", Paths.of(inst), name, tostring(results[2])), 500)
	end
	local returns = Json.array()
	for i = 2, results.n do
		table.insert(returns, Values.toJson(results[i]))
	end
	return true, { ReturnValue = results.n >= 2 and Values.toJson(results[2]) or Json.null, Returns = returns }
end

local function describe(inst: Instance): { [string]: any }
	local props = Json.array()
	for _, n in classList(inst, PROPS) do
		local found, value = Rc.read(inst, n)
		if found then
			table.insert(props, { Name = n, Type = typeof(value), Value = Values.toJson(value) })
		end
	end
	for k, v in inst:GetAttributes() do
		table.insert(props, { Name = k, Type = typeof(v), Value = Values.toJson(v), Description = "attribute" })
	end
	for _, c in inst:GetChildren() do
		if c:IsA("ValueBase") then
			table.insert(props, {
				Name = c.Name,
				Type = c.ClassName,
				Value = Values.toJson((c :: any).Value),
				Description = "value object",
			})
		end
	end
	local fns = Json.array()
	for _, n in classList(inst, FUNCTIONS) do
		table.insert(fns, { Name = n })
	end
	local children = Json.array()
	for i, c in inst:GetChildren() do
		if i > 200 then
			break
		end
		table.insert(children, Paths.name(Paths.of(c)))
	end
	return {
		Name = inst.Name,
		Class = inst.ClassName,
		Path = Paths.of(inst),
		Properties = props,
		Functions = fns,
		Children = children,
	}
end

-- [ok, result] or [false, error, status], as the plugin's other ops answer.
function Rc.handle(verb: string, body: any): (boolean, any, number?)
	if verb == "probe" then
		return true, { ok = true }
	end
	if type(body) ~= "table" then
		return err("rc wants a body")
	end
	local path = body.objectPath
	local inst = Paths.resolve(path)
	if inst == nil then
		return err(string.format("Object %s does not exist", tostring(path)))
	end
	if verb == "property" then
		local name = body.propertyName
		if body.access == "WRITE_ACCESS" and type(name) == "string" then
			local values = type(body.propertyValue) == "table" and body.propertyValue or {}
			return write(inst, name, values[name])
		end
		if type(name) == "string" and name ~= "" then
			local found, value = Rc.read(inst, name)
			if not found then
				return err(string.format("Property %s does not exist on %s", name, Paths.of(inst)))
			end
			return true, { [name] = Values.toJson(value) }
		end
		return true, readAll(inst)
	elseif verb == "call" then
		return call(inst, tostring(body.functionName), body.parameters)
	elseif verb == "describe" then
		return true, describe(inst)
	end
	return err("unknown rc verb " .. tostring(verb))
end

return Rc
