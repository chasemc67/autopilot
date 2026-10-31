--!strict
-- The plugin's link to the brain over HTTP (autopilot/roblox_bridge.py): a Studio plugin can't hold a WebSocket or
-- listen, so it long-polls. Studio holds a play test's server to Roblox's HttpService budget (about 500 requests a
-- minute: checked live, docs/roblox-live-test-log.md), so each request does as much as it can: the poll that brings
-- the brain's requests is followed, once their answers are ready (MAX_ANSWER_WAIT), by the next poll carrying them,
-- which then waits at the bridge for the next request. One request a question. An answer that comes later (a walk
-- that takes seconds) goes on its own; events (heartbeats, logs) ride the next poll. Requests are spent from a token
-- bucket (REQUESTS_PER_SECOND on average, bursts of REQUEST_BURST). Every request carries the token
-- (X-Autopilot-Token). The edit instance says hello; a play test's server attaches to its session.

local HttpService = game:GetService("HttpService")

local Config = require(script.Parent.Config)
local Json = require(script.Parent.Json)

local Link = {}
Link.__index = Link

export type Options = {
	plugin: Plugin?,
	context: string, -- "edit" | "server" | "client"
	session: string?, -- (server, client: the session to attach to; nil for the newest)
	hello: (() -> { [string]: any })?, -- (edit: the hello frame)
	onFrame: (frame: { [string]: any }) -> (),
	onState: ((state: string, detail: string?) -> ())?, -- "connecting" | "connected" | "rejected" | "lost" | "closed"
}

function Link.new(options: Options)
	local self = setmetatable({}, Link)
	self.options = options
	self.context = options.context
	self.session = options.session
	self.outbox = {} :: { string } -- frames as JSON text
	self.urgent = 0 -- (how many of them are answers: they don't wait for the next poll)
	self.connected = false
	self.closed = false
	self.problem = nil :: string?
	self._rate = Config.REQUESTS_PER_SECOND
	self._tokens = Config.REQUEST_BURST
	self._filled = os.clock()
	self._polling = false -- (a poll is waiting at the bridge)
	self._inflight = 0 -- (requests of the brain's being answered)
	self._wake = Instance.new("BindableEvent")
	return self
end

function Link:_state(state: string, detail: string?)
	if self.options.onState then
		task.spawn(self.options.onState, state, detail)
	end
end

-- POST to an endpoint: (ok, status code, decoded body or nil).
function Link:_post(name: string, bodyText: string): (boolean, number, any)
	while true do
		local t = os.clock()
		self._tokens = math.min(Config.REQUEST_BURST, self._tokens + (t - self._filled) * self._rate)
		self._filled = t
		if self._tokens >= 1 then
			self._tokens -= 1
			break
		end
		task.wait((1 - self._tokens) / self._rate)
	end
	local ok, res = pcall(function()
		return HttpService:RequestAsync({
			Url = Config.url(self.options.plugin) .. "/" .. name,
			Method = "POST",
			Headers = { ["X-Autopilot-Token"] = Config.token(self.options.plugin) },
			Body = bodyText,
		})
	end)
	if not ok then
		local why = tostring(res)
		local low = string.lower(why)
		if string.find(low, "exceeded", 1, true) or string.find(low, "too many", 1, true) then
			-- (Studio's HttpService request limit: slow down, for good)
			self._rate = math.max(Config.MIN_REQUESTS_PER_SECOND, self._rate / 2)
			self._tokens = 0
			warn(string.format("Autopilot: HTTP request limit reached, now at most %g a second", self._rate))
		end
		return false, 0, why
	end
	local decoded = nil
	pcall(function()
		decoded = HttpService:JSONDecode(res.Body)
	end)
	return res.Success, res.StatusCode, decoded
end

-- Say hello (edit) or attach (play): whether the bridge took it.
function Link:_join(): boolean
	local ok, status, body
	if self.context == "edit" then
		local hello = (self.options.hello :: () -> { [string]: any })()
		if self.session then
			hello.resume = self.session
		end
		ok, status, body = self:_post("hello", Json.encode(hello))
		if ok and type(body) == "table" and body.session then
			self.session = body.session
		end
	else
		ok, status, body = self:_post("attach", Json.encode({ session = self.session, context = self.context }))
		if ok and type(body) == "table" and body.session then
			self.session = body.session
		end
	end
	if ok then
		self.connected, self.problem = true, nil
		self:_state("connected", self.session)
		return true
	end
	local why = (type(body) == "table" and (body.error or body.message)) or (type(body) == "string" and body) or ""
	self.problem = string.format("%s (%s)", tostring(why), tostring(status))
	if status == 401 or status == 403 then
		self:_state("rejected", tostring(why))
	else
		self:_state("lost", self.problem)
	end
	return false
end

-- Exchange frames with the bridge: hand over `frames` (JSON texts), get the brain's, waiting up to `wait` seconds.
function Link:_exchange(frames: { string }, wait: number): boolean
	local body = string.format(
		'{"session":%s,"context":%s,"wait":%s,"frames":[%s]}',
		Json.encode(self.session),
		Json.encode(self.context),
		Json.encode(wait),
		table.concat(frames, ",")
	)
	local ok, status, answer = self:_post("exchange", body)
	if not ok then
		if status == 404 or status == 409 then -- (the bridge forgot this session or channel: join again)
			self.connected = false
		end
		self.problem = string.format("exchange failed (%s)", tostring(status))
		return false
	end
	if type(answer) == "table" and type(answer.frames) == "table" then
		for _, frame in answer.frames do
			self._inflight += 1
			task.spawn(function()
				pcall(self.options.onFrame, frame)
				self._inflight -= 1
			end)
		end
	end
	return true
end

-- What the outbox holds now, emptied.
function Link:_takeOutbox(): { string }
	local batch = self.outbox
	self.outbox, self.urgent = {}, 0
	return batch
end

function Link:_putBack(batch: { string })
	for _, text in self.outbox do
		table.insert(batch, text)
	end
	self.outbox = batch
	self.urgent = #batch
end

-- Queue a frame for the brain (anything but events goes at once; events ride the next poll).
function Link:send(frame: { [string]: any })
	self:sendText(Json.encode(frame), frame.type ~= "events")
end

-- Queue a frame already written as JSON (urgent: an answer, which goes at once).
function Link:sendText(text: string, urgent: boolean?)
	if self.closed then
		return
	end
	table.insert(self.outbox, text)
	if urgent ~= false then
		self.urgent += 1
		self._wake:Fire()
	end
end

function Link:_pollLoop()
	local backoff = 1
	while not self.closed do
		if not self.connected then
			if not self:_join() then
				task.wait(backoff)
				backoff = math.min(backoff * 2, 15)
				continue
			end
			backoff = 1
		end
		-- (the answers to what the last poll brought ride this one, when they're ready soon)
		local started = os.clock()
		while self._inflight > 0 and os.clock() - started < Config.MAX_ANSWER_WAIT do
			task.wait()
		end
		local batch = self:_takeOutbox()
		self._polling = true
		local ok = self:_exchange(batch, Config.POLL_WAIT)
		self._polling = false
		if not ok then
			self:_putBack(batch)
			self:_state("lost", self.problem)
			task.wait(backoff)
			backoff = math.min(backoff * 2, 15)
		else
			backoff = 1
		end
	end
end

-- Answers that come while a poll waits at the bridge go on their own.
function Link:_sendLoop()
	while not self.closed do
		if self.urgent == 0 then
			self._wake.Event:Wait()
			continue
		end
		if not self.connected or not self._polling then
			task.wait() -- (no poll waiting: the poll loop takes the outbox with its next poll)
			continue
		end
		local batch = self:_takeOutbox()
		if not self:_exchange(batch, 0) then
			self:_putBack(batch) -- (answers must not be lost to a dropped request)
			task.wait(0.5)
		end
	end
end

function Link:start()
	self:_state("connecting")
	task.spawn(function()
		self:_pollLoop()
	end)
	task.spawn(function()
		self:_sendLoop()
	end)
end

function Link:close(why: string?)
	if self.closed then
		return
	end
	local outbox = self.outbox
	self.outbox = {}
	if self.session and #outbox > 0 then
		pcall(function()
			self:_exchange(outbox, 0)
		end)
	end
	self.closed = true
	self._wake:Fire()
	if self.session then
		pcall(function()
			self:_post("bye", Json.encode({ session = self.session, context = self.context, why = why or "closed" }))
		end)
	end
	self:_state("closed", why)
end

return Link
