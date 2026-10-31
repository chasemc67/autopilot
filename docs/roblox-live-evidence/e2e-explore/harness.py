from autopilot.kit import *


class CoinExploreHarness(Harness):
    goal = ("Explore the coin system for bugs: pick coins up in different orders and ways (walking in, jumping over "
            "them), walk back over spots where coins were taken (does anything count twice?), and watch the count, "
            "the door (should open once the count reaches 5; Coin3 is worth 2) and the HUD. Prefer what hasn't been "
            "tried yet (see 'tried'): every coin has been walked into in earlier episodes, so this "
            "episode: once a coin or two is held, walk off the world edge to die, then check the count after "
            "respawning and collect the rest.")
    tempo = 0.0
    stall_seconds = 0
    step_seconds = 0

    def setup(self, game):
        self.coins = game.call("/Workspace/Coins", "GetChildren")["ReturnValue"]
        self.where = game.locs(self.coins)
        self.value = {c: (game.get(c, "Value") or 1) for c in self.coins}
        self.player = "/Players/%s" % game.name_of(game.pawn)
        self.stats = self.player + "/leaderstats"
        self.tried = {}
        self.taken_spots = {}
        self.prev = None
        self.log = []          # every change of count / coins / door, with its time
        self.anomalies = []
        self.jumps = []
        self.fell, self.respawns, self.last_pose, self.death, self.fall_note = False, 0, None, None, None

    def observe(self, game):
        reads = game.batch([
            lambda: game.pose(), lambda: game.get(self.stats, "Coins"),
            lambda: game.get("/Workspace/Door", "CanCollide"), lambda: game.screen(),
        ] + [(lambda c=c: game.exists(c)) for c in self.coins])
        pose, count, door_solid, screen = reads[:4]
        left = [c for c, a in zip(self.coins, reads[4:]) if a is True]
        screen = screen if isinstance(screen, dict) else {}
        count = count if isinstance(count, int) else None
        if isinstance(pose, dict):
            z = pose["at"].z
            if z < -3000 and not self.fell:
                self.fell = True
                self.log.append({"t": round(game.t, 1), "event": "falling out of the world", "count": count})
            elif self.fell and abs(z) < 400:
                self.fell = False
                self.respawns += 1
                self.log.append({"t": round(game.t, 1), "event": "respawned", "count": count,
                                 "at": pose["at"].rounded(), "door_open": door_solid is False})
        else:
            pose = self.last_pose
        self.last_pose = pose
        state = (count, tuple(left), door_solid is False)
        if self.prev is not None and state != self.prev and count is not None and self.prev[0] is not None:
            gone = [c for c in self.prev[1] if c not in left]
            for c in gone:
                self.taken_spots[c] = self.where[c]
            expect = sum(self.value[c] for c in gone)
            delta = count - self.prev[0]
            entry = {"t": round(game.t, 1), "count": count, "delta": delta, "gone": [game.name_of(c) for c in gone],
                     "door_open": state[2]}
            self.log.append(entry)
            if delta != expect:
                self.anomalies.append(dict(entry, why="count changed by %s, vanished coins worth %s" % (delta, expect)))
            if state[2] != (count >= 5):
                self.anomalies.append(dict(entry, why="door open=%s with count %s" % (state[2], count)))
        if count is not None:
            self.prev = state
        return {"_pose": pose, "_left": left, "coins": count, "coins_left": len(left),
                "coins_left_worth": sum(self.value[c] for c in left),
                "door_open": door_solid is False, "hud_status": screen.get("Hud.Status"),
                "tried": dict(self.tried), "anomalies_seen": len(self.anomalies)}

    def _count(self, name):
        self.tried[name] = self.tried.get(name, 0) + 1

    def _collect(self, coin):
        def run(game, obs):
            self._count("collect_" + game.name_of(coin))
            r = yield from walk_to(game, coin, within=20, timeout=12)
            yield from wait(game, 0.4)
            gone = not game.exists(coin)
            return Result(gone, "coin gone" if gone else "coin still there: %s" % r.why)
        return run

    def jump_over(self, coin, takeoff, kind):
        def run(game, obs):
            self._count(kind)
            at = self.where[coin]
            me = game.pose()["at"]
            d = (at - me).flat.unit
            start = at - d * 450
            yield from walk_to(game, Vec(start.x, start.y, me.z), within=40, timeout=12)
            yield from look_at(game, Vec(at.x, at.y, me.z), timeout=1.0)
            game.hold("W")
            jumped, t0, took = False, game.t, None
            ground_z = game.pose()["at"].z
            while game.t - t0 < 2.5:
                p = game.pose()["at"]
                if took is None and not game.exists(coin):
                    took = {"feet_above_ground": round(p.z - ground_z), "dist_to_coin": round(p.dist2d(at))}
                if not jumped and p.dist2d(at) < takeoff:
                    game.press("SpaceBar")
                    jumped, t0 = True, game.t
                if jumped and game.t - t0 > 1.0:
                    break
                yield
            game.release("W")
            yield from wait(game, 0.4)
            if took is None and not game.exists(coin):
                took = "after landing"
            r = {"coin": game.name_of(coin), "kind": kind, "jumped": jumped, "picked_up": took}
            self.jumps.append(r)
            return Result(True, str(r))
        return run

    def fall_off(self, game, obs):
        self._count("walk_off_world_edge")
        me = game.pose()["at"]
        before = game.get(self.stats, "Coins")
        try:
            yield from walk_to(game, Vec(me.x, -30500, me.z), within=100, timeout=80, avoid_drops=False)
            game.hold("W")
            yield from wait(game, 1.5)
            game.release("W")
        except Exception as e:
            self.fall_note = repr(e)[:200]
        t0 = game.t
        while game.t - t0 < 15 and self.respawns == 0:
            yield
        yield from wait(game, 1.0)
        after = game.get(self.stats, "Coins")
        r = {"coins_before": before, "coins_after_respawn": after, "respawned": self.respawns,
             "at": game.pose()["at"].rounded()}
        self.death = r
        return Result(self.respawns > 0, str(r))

    def revisit(self, game, obs):
        self._count("walk_over_taken_spots")
        for c, at in list(self.taken_spots.items()):
            yield from walk_to(game, at, within=20, timeout=10)
            yield from wait(game, 0.3)
        return Result(True, "walked over %d taken spots" % len(self.taken_spots))

    def options(self, obs):
        at = obs["_pose"]["at"]
        gained = lambda a, b: (b["coins"] or 0) > (a["coins"] or 0)
        opts = []
        if self.respawns == 0 and (obs["coins"] or 0) >= 1 and obs["_left"]:
            opts.append(Option("walk_off_world_edge", "Walk off the edge of the baseplate and fall out of the world "
                               "(does the count survive dying and respawning?)", self.fall_off, effect=lambda a, b: True,
                               interruptible=False, max_seconds=110, facts={"coins_now": obs["coins"]}))
        for c in obs["_left"]:
            n = c.rsplit("/", 1)[-1]
            opts.append(Option("collect_" + n, "Walk into %s to pick it up" % n, self._collect(c), effect=gained,
                               facts={"coin": where(obs["_pose"], self.where[c]), "value": self.value[c],
                                      "tried": self.tried.get("collect_" + n, 0)}, interruptible=False))
        if False:   # jumps settled in episode 3
            c = min(obs["_left"], key=lambda c: self.where[c].dist(at))
            for kind, takeoff, words in (("jump_late", 120, "taking off just before it, so the jump's top is over it"),
                                         ("jump_early", 260, "taking off well before it, so it lands on it")):
                opts.append(Option(kind + "_over_nearest_coin", "Run at the nearest coin and jump, " + words +
                                   " (does it pick the coin up?)", self.jump_over(c, takeoff, kind),
                                   facts={"coin": where(obs["_pose"], self.where[c]),
                                          "tried": self.tried.get(kind, 0)},
                                   effect=None, interruptible=False))
        if self.taken_spots:
            opts.append(Option("walk_over_taken_spots", "Walk back over every spot a coin was taken from "
                               "(does the count change again?)", self.revisit, effect=lambda a, b: True,
                               facts={"spots": len(self.taken_spots)}))
        return opts

    def status(self, obs):
        ev = {"coins": obs["coins"], "coins_left": obs["coins_left"], "door_open": obs["door_open"],
              "hud": obs["hud_status"], "log": self.log[-12:], "anomalies": self.anomalies, "death": self.death,
              "fall_note": self.fall_note, "respawns": self.respawns}
        if not obs["_left"] and self.respawns > 0:
            return Status("done", "all coins taken and their spots revisited", ev)
        return Status(evidence=ev)
