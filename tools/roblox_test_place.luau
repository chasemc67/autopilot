--!nocheck
-- A tiny test game for live-testing the Autopilot Roblox plugin (docs/roblox-live-test-log.md). Run it in the EDIT
-- DataModel of a NEW, throwaway Baseplate (Studio MCP execute_luau, or the command bar). Never save the place.
-- It builds: 5 coins (Workspace.Coins) that add to leaderstats.Coins, a wall with a door that opens at 5 coins, a goal
-- pad behind it that shows "You win!", a lever with a ProximityPrompt, an F-key ContextActionService action, and two
-- deliberate bugs for the "tell me everything that looks wrong" prompt: Coin3 adds 2 coins, and a sign whose script
-- errors.

for _, name in { "Coins", "Wall", "Door", "GoalPad", "Lever", "Sign", "AutopilotTestPlace" } do
	local old = workspace:FindFirstChild(name)
	if old then
		old:Destroy()
	end
end
for _, service in { game:GetService("ServerScriptService"), game:GetService("StarterPlayer").StarterPlayerScripts } do
	for _, s in service:GetChildren() do
		if s:GetAttribute("TestPlace") then
			s:Destroy()
		end
	end
end

local function part(props)
	local p = Instance.new("Part")
	p.Anchored = true
	p.TopSurface = Enum.SurfaceType.Smooth
	p.BottomSurface = Enum.SurfaceType.Smooth
	for k, v in props do
		p[k] = v
	end
	return p
end

local root = Instance.new("Folder")
root.Name = "AutopilotTestPlace"
root.Parent = workspace

local coins = Instance.new("Folder")
coins.Name = "Coins"
coins.Parent = workspace
local spots = {
	Vector3.new(20, 3, 0),
	Vector3.new(35, 3, 15),
	Vector3.new(35, 3, -15),
	Vector3.new(55, 3, 0),
	Vector3.new(20, 3, 25),
}
for i, at in spots do
	local c = part({
		Name = "Coin" .. i,
		Shape = Enum.PartType.Cylinder,
		Size = Vector3.new(0.6, 3, 3),
		CFrame = CFrame.new(at) * CFrame.Angles(0, 0, math.rad(90)),
		Color = Color3.fromRGB(255, 200, 40),
		Material = Enum.Material.Neon,
		CanCollide = false,
	})
	c:SetAttribute("Value", i == 3 and 2 or 1) -- (the bug: Coin3 is worth 2)
	c.Parent = coins
end

local wall = Instance.new("Model")
wall.Name = "Wall"
wall.Parent = workspace
part({ Name = "WallLeft", Size = Vector3.new(2, 12, 36), Position = Vector3.new(80, 6, -22), Color = Color3.fromRGB(90, 90, 110) }).Parent =
	wall
part({ Name = "WallRight", Size = Vector3.new(2, 12, 36), Position = Vector3.new(80, 6, 22), Color = Color3.fromRGB(90, 90, 110) }).Parent =
	wall
part({ Name = "Door", Size = Vector3.new(2, 12, 8), Position = Vector3.new(80, 6, 0), Color = Color3.fromRGB(200, 40, 40) }).Parent =
	workspace

local goal = part({
	Name = "GoalPad",
	Size = Vector3.new(10, 1, 10),
	Position = Vector3.new(100, 0.5, 0),
	Color = Color3.fromRGB(40, 220, 80),
	Material = Enum.Material.Neon,
})
goal.Parent = workspace

local lever = part({ Name = "Lever", Size = Vector3.new(1, 4, 1), Position = Vector3.new(10, 2, -12), Color = Color3.fromRGB(60, 60, 60) })
local prompt = Instance.new("ProximityPrompt")
prompt.ActionText = "Pull"
prompt.ObjectText = "Lever"
prompt.KeyboardKeyCode = Enum.KeyCode.E
prompt.MaxActivationDistance = 12
prompt.Parent = lever
lever:SetAttribute("Pulled", false)
lever.Parent = workspace

local sign = part({ Name = "Sign", Size = Vector3.new(6, 4, 0.5), Position = Vector3.new(10, 3, 12), Color = Color3.fromRGB(240, 240, 240) })
sign.Parent = workspace

local game_ = Instance.new("Script")
game_.Name = "CoinGame"
game_:SetAttribute("TestPlace", true)
game_.Source = [[
local Players = game:GetService("Players")
local ReplicatedStorage = game:GetService("ReplicatedStorage")
local NEEDED = 5

local won = Instance.new("RemoteEvent")
won.Name = "TestPlaceWon"
won.Parent = ReplicatedStorage

Players.PlayerAdded:Connect(function(player)
	local stats = Instance.new("Folder")
	stats.Name = "leaderstats"
	local coins = Instance.new("IntValue")
	coins.Name = "Coins"
	coins.Parent = stats
	stats.Parent = player
end)

local door = workspace:WaitForChild("Door")
local function openDoor()
	door.CanCollide = false
	door.Transparency = 0.8
	print("The door opens")
end

for _, coin in workspace:WaitForChild("Coins"):GetChildren() do
	local taken = false
	coin.Touched:Connect(function(hit)
		if taken then return end
		local player = Players:GetPlayerFromCharacter(hit.Parent)
		if not player then return end
		taken = true
		local c = player.leaderstats.Coins
		c.Value += coin:GetAttribute("Value") or 1
		print(player.Name .. " picked up " .. coin.Name .. ": " .. c.Value .. " coins")
		coin:Destroy()
		if c.Value >= NEEDED and door.CanCollide then
			openDoor()
		end
	end)
end

local goal = workspace:WaitForChild("GoalPad")
local reached = {}
goal.Touched:Connect(function(hit)
	local player = Players:GetPlayerFromCharacter(hit.Parent)
	if player and not reached[player] then
		reached[player] = true
		player:SetAttribute("ReachedGoal", true)
		print(player.Name .. " reached the goal pad")
		won:FireClient(player)
	end
end)

local lever = workspace:WaitForChild("Lever")
lever.ProximityPrompt.Triggered:Connect(function(player)
	local pulled = not lever:GetAttribute("Pulled")
	lever:SetAttribute("Pulled", pulled)
	lever.Color = pulled and Color3.fromRGB(40, 200, 40) or Color3.fromRGB(60, 60, 60)
	print(player.Name .. " pulled the lever: " .. tostring(pulled))
end)
]]
game_.Parent = game:GetService("ServerScriptService")

local signScript = Instance.new("Script")
signScript.Name = "SignText"
signScript:SetAttribute("TestPlace", true)
signScript.Source = [[
-- (the bug: the sign's label was never made)
local label = script.Parent:FindFirstChild("SurfaceGui")
label.TextLabel.Text = "Collect 5 coins to open the door"
]]
signScript.Parent = sign

local client = Instance.new("LocalScript")
client.Name = "TestPlaceClient"
client:SetAttribute("TestPlace", true)
client.Source = [[
local ContextActionService = game:GetService("ContextActionService")
local Players = game:GetService("Players")
local ReplicatedStorage = game:GetService("ReplicatedStorage")
local player = Players.LocalPlayer

local gui = Instance.new("ScreenGui")
gui.Name = "Hud"
gui.ResetOnSpawn = false
local status = Instance.new("TextLabel")
status.Name = "Status"
status.Size = UDim2.fromOffset(320, 40)
status.Position = UDim2.new(0.5, -160, 0, 60)
status.BackgroundTransparency = 0.3
status.TextScaled = true
status.Text = "Collect the coins"
status.Parent = gui
local shop = Instance.new("TextButton")
shop.Name = "ShopButton"
shop.Size = UDim2.fromOffset(120, 40)
shop.Position = UDim2.new(0, 20, 0.5, 0)
shop.Text = "Shop"
shop.Parent = gui
shop.Activated:Connect(function()
	player:SetAttribute("ShopClicks", (player:GetAttribute("ShopClicks") or 0) + 1)
	status.Text = "The shop is closed today"
end)
gui.Parent = player:WaitForChild("PlayerGui")

ContextActionService:BindAction("Wave", function(_, state)
	if state == Enum.UserInputState.Begin then
		player:SetAttribute("Waved", (player:GetAttribute("Waved") or 0) + 1)
		print("Wave!")
	end
	return Enum.ContextActionResult.Sink
end, false, Enum.KeyCode.F)

ReplicatedStorage:WaitForChild("TestPlaceWon").OnClientEvent:Connect(function()
	status.Text = "You win!"
end)
]]
client.Parent = game:GetService("StarterPlayer").StarterPlayerScripts

return "test place built"
