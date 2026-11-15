# Whether the placement that fires a name is reached, and the start closure it closes

`docs/MAP_ACTION_PATHS.md` measured the set of paths an `Actions` array allows and named what it would not claim:
"whether the firing placement is itself reached is still a runtime question." It is not one. The firing placement
is a *placed action*, and the three readings this project already has decide whether an action runs:

* an action runs only if an `Actions` array names it — `docs/MAP_ACTION_WIRING.md`, 16,886 of 18,510 placements;
* its slot runs only if some path reaches it — `docs/MAP_ACTION_PATHS.md`, 16,888 of 16,907 slots;
* the sequence holding that slot runs only if it *starts* — `docs/MAP_EVENT_TARGETS.md`, 1,374 wired actions in a
  sequence whose own `Tag` some placed property fires.

Chaining those three is not a multiplication, because the third edge feeds back into the first: a sequence starts
because a *reached* carrier fires its tag, and a carrier is reached because a *started* sequence names it at a slot
some path reaches. The answer is therefore a **fixed point**, and `tools/action-reach.py` grows it outward from the
sequences that need no name at all — the ones whose own class starts them:

* `ScriptedTrigger.PostBeginPlay` spawns a `ScriptedTriggerController` and calls `InitializeFor(self)`, so a
  `ScriptedTrigger` runs the moment the level begins, with no name and no touch;
* `TriggeredScript` does the same from its own `Touch`, so it runs when a pawn walks into it.

Both are read out of the class's shipped text by the tool (`function` bodies that call `InitializeFor(` or
`ScriptedTriggerPostBeginPlay(` from `PostBeginPlay`, `Touch` or `BeginPlay`), not assumed from a class name. The
rest of the family — `ScriptedSequence`, `UnrealScriptedSequence`, `TeamScriptedSequence`, `ScriptedSpawner`,
`HoldSpot`, `Sequence_GotoActor` — starts only from a `Trigger()` call, which is what a fired name does.

    python3 tools/action-reach.py "$SG1_DUMP/Stargate_SG-1_-_The_Alliance-2005-12-15" --sweep -o inventory
    python3 tools/action-reach.py --report
    python3 tools/action-reach.py --write-doc docs/MAP_ACTION_REACH.md
    python3 tools/action-reach.py --check          # synthetic maps, no dump, writes nothing

`inventory/action_reach.csv` holds one row per map, one per fire-carrying class, one per sequence class and a
`census` row. Two committed artifacts are read as controls in the same sweep: the holder population
`event-targets` counted (2,694 exports carry an `Actions` array, 497 of them whose `Tag` is fired) and its wired
totals (16,886 wired, 1,374 in an armed sequence), plus `action-paths`' array and element counts.

<!-- generated:action-reach -->
**Of the 16886 actions the maps wire, 7790 sit in a sequence that starts and at a slot some path reaches; the closure that says so begins at 1266 sequences that need no name at all.**

`docs/MAP_ACTION_PATHS.md` left one question open -- "whether the firing placement is itself reached is still a runtime question". The firing placement is a placed action, so the three readings this project already has decide it: an action runs only if an `Actions` array names it (`docs/MAP_ACTION_WIRING.md`), only if its slot is on some path (`docs/MAP_ACTION_PATHS.md`), and only if the sequence holding that slot starts (`docs/MAP_EVENT_TARGETS.md`). Chaining them is a closure, not a count -- a sequence starts because a reached carrier fires its tag, and a carrier is reached because a started sequence names it -- so this walk grows the start set outward from the sequences that start by themselves and stops when nothing new starts.

| measure | answer |
|---|---|
| maps read | **73** |
| sequence classes carrying an `Actions` array | **6** |
| of those, classes that start with no name | **2** |
| sequences carrying an array (the six scripted classes) | **2437** |
| exports carrying an array at all (every class: `event-targets`' population) | **2694** |
| -- of those, a `Tag` some placed property fires | **497** (239 scene-manager camera arrays) |
| slots wired | **16907** |
| fire sites | **5953** over 5584 carrier actors |
| -- carrier is a placed scripted action | **2916** (2668 carriers of other classes) |
| -- carrier reached (a started sequence names it, on a path) | **2313** |
| -- carrier wired but on no path a started sequence reaches | **344** |
| -- carrier no array names (volume, mover, native start) | **2927** |
| fire-to-actor landings | **7047** |
| sequences whose tag some placed property fires (armed) | **258** |
| -- in the start closure by their own class | **1266** |
| -- in the start closure through a chain of fires | **0** |
| sequences in the closure altogether | **1266** |
| sequences no chain reaches (stalled) | **1171** |
| rounds the closure took (deepest chain) | **1** |
| actions wired by an array | **16886** |
| -- in a sequence whose tag a fire names (armed) | **1374** |
| -- in a started sequence | **7803** |
| -- there and on a reached slot (the full chain) | **7790** |
| -- behind nothing that starts, or off the paths | **9096** |

A fire site's carrier is classified by where placed data puts *it*. `reached` means a started sequence's `Actions` array names it at a slot on some path; `dead` means an array names it but only at slots the paths do not reach, or inside a sequence nothing starts; `outside` means no array names it at all -- a door `Mover`, an `Emitter`, a spawner, which the engine starts and no property in the map records. The self-starting classes are read from their own shipped text, not assumed: `ScriptedTrigger`'s `PostBeginPlay` spawns its controller and calls `InitializeFor(self)`, and `TriggeredScript` does the same from `Touch`.

**What the closure adds over the two-hop count.** `docs/MAP_EVENT_TARGETS.md` reported 1374 wired actions in a sequence whose tag is fired -- an arm, not a run, because the fire that arms it may itself sit in a sequence nothing starts. The closure answers both directions at once: 7803 wired actions are in a started sequence, 7790 of those on a reached slot, and 9096 lie behind no start at all. A cycle of sequences that fire each other and take no seed never enters the set, which is why the count is a fixed point rather than two hops.

### Which classes fire, and from where

| class of the actor carrying the fire property | fire sites | carrier reached | carrier dead | carrier outside | landings |
|---|---:|---:|---:|---:|---:|
| `ACTION_TriggerEvent` | 2657 | 2125 | 285 | 247 | 4159 |
| `Trigger` | 547 | 0 | 0 | 547 | 600 |
| `SubActionTrigger` | 431 | 0 | 0 | 431 | 283 |
| `Mover` | 308 | 0 | 0 | 220 | 381 |
| `ACTION_Talk` | 256 | 188 | 58 | 10 | 21 |
| `SGUseTrigger` | 219 | 0 | 0 | 219 | 311 |
| `SceneManager` | 145 | 0 | 0 | 123 | 45 |
| `PlayerStart` | 122 | 0 | 0 | 122 | 99 |
| `SQUAD_JaffaHorusL_Single` | 112 | 0 | 0 | 99 | 53 |
| `SQUAD_JaffaSerpentL_Single` | 110 | 0 | 0 | 83 | 96 |
| `SQUAD_JaffaHorusM_Single` | 105 | 0 | 0 | 67 | 90 |
| `counter` | 94 | 0 | 0 | 94 | 62 |
| `MissionObjective` | 77 | 0 | 0 | 77 | 64 |
| `HackingPawn` | 64 | 0 | 0 | 28 | 104 |
| `SQUAD_JaffaJackalM_Single` | 63 | 0 | 0 | 52 | 24 |

### Which classes carry the arrays, and how much of each is in the closure

| sequence class | arrays | armed | started | stalled | wired | in a started sequence | there and on a path |
|---|---:|---:|---:|---:|---:|---:|---:|
| `TeamScriptedSequence` | 1141 | 0 | 0 | 1141 | 8716 | 0 | 0 |
| `ScriptedTrigger` (starts with no name) | 1073 | 247 | 1073 | 0 | 7065 | 7065 | 7056 |
| `TriggeredScript` (starts with no name) | 193 | 6 | 193 | 0 | 738 | 738 | 734 |
| `UnrealScriptedSequence` | 25 | 0 | 0 | 25 | 349 | 0 | 0 |
| `ScriptedSpawner` | 4 | 4 | 0 | 4 | 15 | 0 | 0 |
| `ScriptedSequence` | 1 | 1 | 0 | 1 | 3 | 0 | 0 |

Census: maps=73;fires=5953;reached=2313;dead=344;outside=2927;holders=2437;started=1266;stalled=1171;rounds=1;wired=16886;wired_started=7803;wired_started_reach=7790.
<!-- /generated:action-reach -->

## What the closure changes

Two numbers move, and both are consequences of the same omission: an arm is not a start.

* **The armed quarter is not the running quarter.** `docs/MAP_EVENT_TARGETS.md` closed at 8.1% of wired actions in
  a sequence a fire arms, and read that as the reachable minority. But a `ScriptedTrigger` needs no arm: its own
  `PostBeginPlay` starts it. Measured from the closure, the running population is six times larger, and the
  armed subset is a *narrowing* of it rather than the whole.
* **Nothing enters the start set through a fire.** Every sequence a placed fire arms is already in the closure by
  its own class, so the chain adds no sequence at all — the closure closes in one pass. The fired names a map
  carries are what flip `TriggeredCondition`s and end `ACTION_*` waits; they do not start a sequence that would
  otherwise be idle. That is the outer edge of the flow graph, and it is not an edge.

The remaining population is `TeamScriptedSequence` and `UnrealScriptedSequence` — pure sequences whose start is a
`TakeOver(Pawn)` that only an AI order or a native call issues. `docs/MAP_ACTION_ORDER.md` already said their start
is outside placed data; this walk says how much sits behind it.

## What this does not claim

* **That a `TriggeredScript` is touched.** Its start is a collision, and a level's geometry and a pawn's path are
  not in the package. The walk counts it as started because its class starts it, which is the same treatment
  `ScriptedTrigger` gets; a map where no pawn can reach one is indistinguishable here.
* **That a slot's path is the path a play takes.** `FRand()`'s draw and a `TriggeredCondition` that inverts
  mid-play still move a run between the paths `docs/MAP_ACTION_PATHS.md` measured, and the closure uses the union.
* **That the engine stops where the classes say.** A `bValidForTrigger` gate is applied as `action-paths` applies
  it; a native override of `ProceedToNextAction` would not be visible here.
