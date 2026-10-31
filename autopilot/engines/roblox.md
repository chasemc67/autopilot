
## The engine: Roblox Studio

- **Things are instances**, named by their paths in the DataModel, services first: `/Workspace/Map/Door`,
  `/ReplicatedStorage/Remotes/Buy`, `/Players/<Name>/leaderstats/Coins`. A second sibling with the same name gets `@2`
  (`/Workspace/Coins/Coin@2`), a third `@3`. A "component" is a child instance (`/Workspace/Door/Hinge`).
  `game.name_of(path)` is the last part (`Coin@2`).
- **Classes**: `game.actors("BasePart")`, `game.actors("Model")` (`Instance:IsA`: subclasses too), a CollectionService
  tag as `game.actors("tag:Enemy")`, else instances whose `Name` is exactly the class you gave. `game.all_actors()`:
  Workspace's direct children and the Models and parts in Folders under it, not every part inside a model;
  `game.named("door")` searches those.
- **Reads and calls** (`game.get`, `game.props`, `game.call`, `game.describe`) reach an instance's properties by name
  (`game.get(path, "Transparency")`), its **attributes** (`GetAttribute`: `game.get(path, "Health")` falls back to an
  attribute of that name) and the `Value` of a ValueBase child (`game.get("/Players/<Name>/leaderstats", "Coins")`).
  `game.props(path)` gives the usual properties of its class, its attributes and its value children. `game.call` calls
  a method, parameters in order (`game.call(path, "GetChildren")`). Scripts' local variables and module tables can't
  be read: what the game keeps in attributes, Value objects and leaderstats can.
- **Values from `game.get` are Roblox's own**: studs, Roblox's axes (x right, y up, -z forward), a Vector3 as
  `{"x","y","z"}`, a CFrame as `{"position", "orientation"}` (degrees), a Color3 as `{"r","g","b"}`, an instance as its
  path, an EnumItem as its name. `game.to_kit(value)` turns a position or velocity into the kit's frame
  (`kit = 28 * (-z, x, y)`: a stud is 28 cm); `game.to_native(vec)` turns a kit point back for `game.set`. `game.set(model,
  "Pivot", point)` moves a Model (`PivotTo`, keeping its rotation). Everything else the kit gives (`game.loc`,
  `game.locs`, `game.bounds`, `game.pose`, `game.view`, `movement`) is in the kit's frame already.
- **The player**: the local player's Character model (`/Workspace/<Name>`); `game.pose()["at"]` is its
  HumanoidRootPart, the feet `half_height` below. `game.controller` is "": use `game.pose()` / `game.view_rotation()`
  for the camera.
- **Input is semantic** (a Studio plugin can't make real key events): movement keys (W, A, S, D) and `move` walk the
  Humanoid (`Humanoid:Move`, camera-relative), `SpaceBar` jumps, `MouseX`/`MouseY` turn the camera, `E` (or any key a
  ProximityPrompt asks for) holds the nearest prompt in range, `LeftMouseButton` activates the equipped Tool, and any
  key or action the game binds through ContextActionService is called through it (`game.press("Sprint")` presses a
  ContextActionService action by name). A key the game only reads in `UserInputService.InputBegan` does nothing: read
  the code; when it acts through a RemoteEvent, `game.call(remote, "FireServer", ...)` is not a player's action and is
  reported as a direct change.
- **The place**: one place is one level. `game.restart()` ends the play test and starts a new one (a fresh server:
  every script runs again, nothing is kept). `map=` is not supported. Game time (`game.t`) is the server's
  `workspace.DistributedGameTime`, from 0 in each play test. `game.pause()` does nothing (Roblox can't pause a running
  game from a plugin).
- **Physics**: a Humanoid steps up small ledges by itself (`movement(game)["step"]` is None) and climbs TrussParts;
  `max_slope` is its `MaxSlopeAngle`. Walk speed, jump and gravity come from the Humanoid (`WalkSpeed`, `JumpPower` or
  `JumpHeight`) and `Workspace.Gravity`.
- **`game.view`** here tells: visible parts and models (name, path, class, center, size, distance, screen position,
  crosshair_on), the camera (fov horizontal) and the body (on_ground, falling, standing_on). Everything else Unreal's
  view has is in the answer's `omitted`.
- **`game.screen()`** keys are `<ScreenGui>.<Name>` (`HUD.CoinsLabel`): the visible TextLabels, TextButtons and
  TextBoxes in the player's PlayerGui. `game.ui()` lists buttons and text boxes. `ui_act` can `set` a TextBox's text;
  a **click can't be made** (answers 501): to get past a menu, read what its button's `Activated` handler does and do
  that, or say the run is blocked.
- **No pictures** unless the agent service was started with `AUTOPILOT_ROBLOX_SCREENSHOT=1` (then a still is a
  screenshot of the screen); never a recording. Use `game.view`, `game.screen` and reads.
- **In Studio**, the brain starts the play test itself (the session is started before the harness runs). With no play
  test running, only `game.context()` answers.
