from autopilot.kit import *


class CoinTouchHarness(Harness):
    goal = "Walk into coins to pick them up; each coin touched should raise the leaderstats Coins count by its value"
    tempo = 0.0           # decide only when a walk to a coin ends: the targets' ranking shifts as the player walks
    stall_seconds = 30

    def setup(self, game):
        self.coins = game.call("/Workspace/Coins", "GetChildren")["ReturnValue"]
        self.where = game.locs(self.coins)
        self.value = {c: (game.get(c, "Value") or 1) for c in self.coins}
        self.stats = "/Players/%s/leaderstats" % game.name_of(game.pawn)
        self.pickups = []          # each coin seen gone: its value and the Coins change at that moment
        self.last = None
        self.gone = set()

    def resume(self, game, progress):
        for c in self.coins:
            if not game.exists(c):
                self.gone.add(c)

    def observe(self, game):
        reads = game.batch([lambda: game.pose(), lambda: game.get(self.stats, "Coins")]
                           + [(lambda c=c: game.exists(c)) for c in self.coins])
        pose, count, alive = reads[0], reads[1], reads[2:]
        left = [c for c, a in zip(self.coins, alive) if a is True]
        if self.last is None:
            self.last = count
        for c, a in zip(self.coins, alive):
            if a is not True and c not in self.gone:
                self.gone.add(c)
                self.pickups.append({"coin": game.name_of(c), "value": self.value[c], "coins_before": self.last,
                                     "coins_after": count, "t": round(game.t, 2)})
        if isinstance(count, (int, float)):
            self.last = count
        return {"_pose": pose, "_left": left, "leaderstats_coins": count, "coins_left": len(left),
                "coins_picked_up": len(self.pickups)}

    def _collect(self, coin):
        def run(game, obs):
            r = yield from walk_to(game, coin, within=20, timeout=12)
            yield from wait(game, 0.4)
            return Result(not game.exists(coin), "coin gone" if not game.exists(coin) else "coin still there: %s" % r.why)
        return run

    def options(self, obs):
        # every remaining coin, so the set only changes when a coin is taken (notes: a nearest-few set interrupts walks)
        gained = lambda a, b: (b["leaderstats_coins"] or 0) > (a["leaderstats_coins"] or 0)
        return [Option("collect_" + game_name(c), "Walk into %s to pick it up" % game_name(c), self._collect(c),
                       facts={"coin": where(obs["_pose"], self.where[c]), "value": self.value[c]},
                       interruptible=False, effect=gained)
                for c in obs["_left"]]

    def status(self, obs):
        ev = {"pickups": list(self.pickups), "leaderstats_coins": obs["leaderstats_coins"]}
        bad = [p for p in self.pickups if (p["coins_after"] or 0) - (p["coins_before"] or 0) != p["value"]]
        if bad:
            return Status("done", "a coin was touched and Coins did not change by its value", ev)
        if not obs["_left"] and self.pickups:
            return Status("done", "every coin touched; Coins rose by each one's value", ev)
        return Status(evidence=ev)


def game_name(path):
    return path.rsplit("/", 1)[-1].replace("@", "_")
