--!strict
-- The Autopilot dock: a DockWidgetPluginGui with the connection's state, the prompt box, Start and Stop, the run's
-- progress lines and its result, and (folded under Settings) the agent service's address and token. Plain Studio
-- widgets made in code; no assets.

local Dock = {}
Dock.__index = Dock

export type Handlers = {
	start: (text: string) -> (),
	stop: () -> (),
	steer: (text: string) -> (),
	saveSettings: (url: string, token: string) -> (),
	connect: () -> (),
}

local PAD = 8

local function label(parent: Instance, text: string, height: number, order: number): TextLabel
	local l = Instance.new("TextLabel")
	l.BackgroundTransparency = 1
	l.Size = UDim2.new(1, 0, 0, height)
	l.TextXAlignment = Enum.TextXAlignment.Left
	l.TextYAlignment = Enum.TextYAlignment.Top
	l.TextWrapped = true
	l.Font = Enum.Font.SourceSans
	l.TextSize = 15
	l.TextColor3 = Color3.fromRGB(220, 220, 220)
	l.Text = text
	l.LayoutOrder = order
	l.Parent = parent
	return l
end

local function box(parent: Instance, placeholder: string, height: number, order: number): TextBox
	local b = Instance.new("TextBox")
	b.Size = UDim2.new(1, 0, 0, height)
	b.BackgroundColor3 = Color3.fromRGB(30, 30, 30)
	b.BorderSizePixel = 0
	b.TextColor3 = Color3.fromRGB(235, 235, 235)
	b.PlaceholderText = placeholder
	b.PlaceholderColor3 = Color3.fromRGB(130, 130, 130)
	b.Text = ""
	b.ClearTextOnFocus = false
	b.MultiLine = height > 30
	b.TextWrapped = true
	b.TextXAlignment = Enum.TextXAlignment.Left
	b.TextYAlignment = Enum.TextYAlignment.Top
	b.Font = Enum.Font.SourceSans
	b.TextSize = 15
	b.LayoutOrder = order
	b.Parent = parent
	return b
end

local function button(parent: Instance, text: string, order: number, accent: boolean?): TextButton
	local b = Instance.new("TextButton")
	b.Size = UDim2.fromOffset(96, 28)
	b.BackgroundColor3 = accent and Color3.fromRGB(240, 135, 58) or Color3.fromRGB(60, 60, 60)
	b.BorderSizePixel = 0
	b.TextColor3 = accent and Color3.fromRGB(20, 20, 20) or Color3.fromRGB(230, 230, 230)
	b.Font = Enum.Font.SourceSansSemibold
	b.TextSize = 15
	b.Text = text
	b.LayoutOrder = order
	b.Parent = parent
	return b
end

function Dock.new(plugin: Plugin, handlers: Handlers, url: string, token: string)
	local self = setmetatable({}, Dock)
	local info = DockWidgetPluginGuiInfo.new(Enum.InitialDockState.Right, false, false, 360, 520, 280, 320)
	local widget = plugin:CreateDockWidgetPluginGui("AutopilotDock", info)
	widget.Title = "Autopilot"
	self.widget = widget

	local root = Instance.new("ScrollingFrame")
	root.Size = UDim2.fromScale(1, 1)
	root.BackgroundColor3 = Color3.fromRGB(22, 22, 22)
	root.BorderSizePixel = 0
	root.AutomaticCanvasSize = Enum.AutomaticSize.Y
	root.CanvasSize = UDim2.new()
	root.ScrollBarThickness = 6
	root.Parent = widget
	local padding = Instance.new("UIPadding")
	for _, side in { "PaddingTop", "PaddingBottom", "PaddingLeft", "PaddingRight" } do
		(padding :: any)[side] = UDim.new(0, PAD)
	end
	padding.Parent = root
	local layout = Instance.new("UIListLayout")
	layout.Padding = UDim.new(0, PAD)
	layout.SortOrder = Enum.SortOrder.LayoutOrder
	layout.Parent = root

	self.connection = label(root, "Not connected", 20, 1)
	self.prompt = box(root, 'A prompt, e.g. "Verify that touching the lava part kills the player"', 90, 2)
	local row = Instance.new("Frame")
	row.BackgroundTransparency = 1
	row.Size = UDim2.new(1, 0, 0, 28)
	row.LayoutOrder = 3
	row.Parent = root
	local rowLayout = Instance.new("UIListLayout")
	rowLayout.FillDirection = Enum.FillDirection.Horizontal
	rowLayout.Padding = UDim.new(0, PAD)
	rowLayout.SortOrder = Enum.SortOrder.LayoutOrder
	rowLayout.Parent = row
	self.startButton = button(row, "Start", 1, true)
	self.stopButton = button(row, "Stop", 2)
	self.steerButton = button(row, "Steer", 3)
	self.status = label(root, "", 40, 4)
	self.result = label(root, "", 20, 5)
	self.result.AutomaticSize = Enum.AutomaticSize.Y
	self.result.Font = Enum.Font.Code
	self.result.TextSize = 13

	local settings = label(root, "Settings: the agent service (autopilot serve) and its token", 20, 6)
	settings.TextColor3 = Color3.fromRGB(150, 150, 150)
	self.url = box(root, "Address", 24, 7)
	self.url.Text = url
	self.token = box(root, "Token (autopilot roblox status --show-token)", 24, 8)
	self.token.Text = token
	local settingsRow = Instance.new("Frame")
	settingsRow.BackgroundTransparency = 1
	settingsRow.Size = UDim2.new(1, 0, 0, 28)
	settingsRow.LayoutOrder = 9
	settingsRow.Parent = root
	local settingsLayout = rowLayout:Clone()
	settingsLayout.Parent = settingsRow
	self.saveButton = button(settingsRow, "Save", 1)
	self.connectButton = button(settingsRow, "Connect", 2)

	self.startButton.Activated:Connect(function()
		local text = self.prompt.Text
		if string.match(text, "%S") then
			handlers.start(text)
		else
			self:setStatus("Type what Autopilot should do first.")
		end
	end)
	self.stopButton.Activated:Connect(handlers.stop)
	self.steerButton.Activated:Connect(function()
		if string.match(self.prompt.Text, "%S") then
			handlers.steer(self.prompt.Text)
		end
	end)
	self.saveButton.Activated:Connect(function()
		handlers.saveSettings(self.url.Text, self.token.Text)
	end)
	self.connectButton.Activated:Connect(handlers.connect)
	return self
end

function Dock:setConnection(text: string)
	self.connection.Text = text
end

function Dock:setStatus(text: string)
	self.status.Text = text
end

function Dock:setResult(text: string)
	self.result.Text = text
end

return Dock
