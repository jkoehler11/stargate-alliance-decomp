# Single-player objectives

Two systems in this build are called "objective" and they do not touch each other. Everything below is
about the single-player one, `Engine.MissionObjective`; the other family is described at the end so it is
not mistaken for it.

Names, files and counts here come from the reconstructed tree (`make decompile`) and from the committed
inventories (`inventory/classes.csv`, `inventory/map_actors.csv`, `inventory/sgs_schema.csv`). Behaviour
is described from the decompiled bodies — the code itself is game-derived and stays out of this
repository.

## The data model

`Engine.MissionObjective` extends `Actor` and is small: 243 lines, eight functions and two events. Its
state is authoring data plus one runtime flag.

| field | type | role |
|---|---|---|
| `ObjectiveName` | `var() string` | the line the HUD and the panel print |
| `bIsActive` | `var() bool` | set once at runtime; an objective that is not active is not shown |
| `ObjectiveComplete` | `var() bool` | the only piece of progress state; replicates to clients |
| `Priority` | `var() int` | sort key for the panel, lower sorts first |
| `ParentObjectiveName` | `var() name` | the parent's `Tag`, resolved at runtime |
| `ParentObjective` / `ParentDescription` / `Children` | runtime | the resolved graph, never authored by hand |
| `bDisplayIconWhenComplete` / `bDisplayIconWhenInComplete` | `var() bool` | per-state map visibility |
| `MapDisplayIconComplete` / `MapDisplayIconIncomplete` | `var() Material` | the two map icons |
| `ObjectiveCompleteEvent` | `var() name` | fired with `TriggerEvent` when the objective completes |
| `ObjectiveCompleteSound` | `config Sound` | played with the completion notification |

Replication is declared explicitly at the top of the class: the icons, the name and the priority are
reliable and authority-only, which is why the client panel can draw objectives it was never told about
individually.

## Lifecycle

1. **Link.** `PostNetBeginPlay` calls `findParent()` then `findKids()`. Parent lookup iterates
   `DynamicActors` for another `MissionObjective` whose `Tag` equals this one's `ParentObjectiveName`;
   children are found the other way round — every objective whose `ParentObjectiveName` equals *this*
   objective's `Tag`. Both are tag-matched by scanning actors, so the graph exists only once the level is
   running; nothing references it before `PostNetBeginPlay`.
2. **Activate or complete — one event, two states.** `Trigger(Actor Other, Pawn EventInstigator)` is the
   whole state machine. An inactive objective becomes active and every `PlayerController` with a HUD gets
   `DisplayNotification(ObjectiveName, "New Objective")`; an already-active objective becomes complete,
   swaps to the complete icon, notifies with `"Objective Complete"` and the configured sound, and fires
   `ObjectiveCompleteEvent` if one was set. The same trigger is therefore wired to different volumes or
   events for "objective appears" and "objective done".
3. **Aggregation.** `checkKidsStatus()` walks `Children`: an active parent that is not itself complete
   becomes complete once every child is complete. A parent with no children is never auto-completed — it
   needs its own trigger, so leaf objectives are the ones level script marks.
4. **Display.** `DisplayObjective()` returns false unless the objective is active, then picks the icon for
   its current state; `PreBeginPlay` copies that into `MapDisplay`. `isCurrent()` is active-and-not-done,
   which is what the HUD uses for the current-objective line.

## Consumers

Only four classes reference `MissionObjective` in the reconstructed tree: `StargateGame.GameHud` (current
objective line), `StargateGame.Waypoint` (map marker), `StargateMenus.InGameInfoPanel` and
`StargateMenus.ObjectivesPanel`. Nothing spawns one — there is no `Spawn(class'Engine.MissionObjective')`
anywhere in the script, so every objective is placed in a map.

`ObjectivesPanel` is the list UI and it is the only interesting consumer: it keeps two arrays of
`ObjectiveInfo` (visible and complete), adds a heading row `"Active Objectives"` with `Priority` 100, then
walks parents in priority order with `GetNextActiveParent`, adding each followed by its children, and
gives completed entries menu state 4 instead of 0. Sub-objectives are indented by nesting depth rather
than by a flat sort, which is why `Priority` alone does not explain the order on screen.

## Where they are in the content

`inventory/map_actors.csv` counts placed actors per map. `MissionObjective` is placed **61 times across 61
of the 73 maps, exactly once in each** — one objective actor per mission map, no map with two. That is the
whole authored graph in this build: a single root per mission, with children only where a map's own script
or a designer added them.

The nine checkpoint packages (`inventory/sgs_schema.csv`, 1,739 distinct property tags) contain **no**
`ObjectiveComplete`, `ObjectiveName`, `bIsActive` or `ParentObjectiveName` tag. So objective progress is
not part of a checkpoint, which is consistent with the release's "single-player campaign won't start, use
Continue" state: a load restarts the map's objective logic rather than restoring it.

## The other "objective" family (multiplayer)

`StargateGame.GameObjective` extends `NavigationPoint`, not `MissionObjective`, and is the UT2004 assault
objective: score, defending team, `NextObjective` chain, `DefenseSquad`/`DefenseScripts`, bot hints
(`TellBotHowToDisable`). Its subclasses are the interesting ones and they are all about *how* an objective
can be taken:

| class | how it is disabled |
|---|---|
| `LockedObjective` | needs a `KeyPickup` matched by `KeyTag`; `MyKey` is resolved at start |
| `TriggeredObjective` | a named `MyTrigger` actor fires `Trigger` |
| `DestroyableObjective` | two damage stages (`DamageMesh[2]`, `DamageThreshold[2]`), then `DestroyedMesh` |
| `StargateLockedObjective`, `StargateDestroyableObjective`, `StargateTriggeredObjective` | Stargate-flavoured variants of the three |
| `SGC_SecurityDoorObjective` | a StargateContent door objective |

`StargateGame.CTFBase` and `StargateGame.SGEtherealObjective` also match a search for "objective" but are
not part of either graph: `SGEtherealObjective` is a six-line `Actor` subclass (`config placeable`) with no
body of its own, and `CTFBase` is the CTF flag base — it extends `GameObjective`, which is why it matched.

## Open questions

- **Does a checkpoint really reset objectives?** The absence of the tags in nine checkpoints is measured;
  what it *means* for the player is not. Loading a checkpoint and watching whether completed objectives
  reappear needs the boot test (`make` + xemu, `docs/XEMU.md`), which is still blocked on input automation.
- **Who fires the triggers?** No script class triggers a `MissionObjective` by name; the maps' own level
  script and placed triggers must. That is checkable from the T3D dumps that PLAN 5.2 still owes: the
  actor list would show which volume or event references each objective's tag.
- **Are children ever authored?** The panel handles them, `checkKidsStatus` aggregates them, but with one
  objective per map in this build the nesting may be dead code carried from the fuller campaign. A T3D dump
  would settle whether any map sets `ParentObjectiveName`.
