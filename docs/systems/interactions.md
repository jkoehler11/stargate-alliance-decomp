# Interactions

"Interaction" names two unrelated things in this build, and the game uses both. The first is Epic's
client-side framework for input and rendering hooks; the second is Stargate's own "press use on this"
gameplay data. This note separates them, then documents the gameplay one, which is what PLAN 3.10 means
by the interaction system.

## One: the client framework (`Engine.Interaction` and friends)

`Engine.Interaction` is a per-viewport object with hooks rather than behaviour: `Initialized`,
`NotifyLevelChange`, `KeyType`, `KeyEvent`, `PreRender`, `PostRender`, `SetFocus`, plus
`bNativeEvents`, `bRequiresTick`, `bVisible` and a `Master` back-pointer. `Engine.InteractionMaster`
(373 lines) owns them: it keeps `BaseMenu`, `Console`, `UIX` and a `GlobalInteractions` array, and
re-dispatches `NotifyLevelChange` to the array. `Engine.Interactions` holds the enums the hooks use
(`EInputKey`, `EInputAction`, `EStreamFinishReason`). Subclasses in this build are the console
(`Engine.Console`), the UI stack (`GUI.UIXInteraction`, `Engine.AbstractUIXInteraction`), the streaming
player (`Engine.StreamInteraction`), the menu hook (`StargateGame.SGMenuInteraction`) and the zoom weapon
hook (`StargateGame.WeaponZoomInteraction`).

None of that is game logic, and nothing below depends on it.

## Two: the gameplay interaction (`Engine.SGInteraction`)

`SGInteraction` is **not an actor**. It is an `Object` subclass, `editinlinenew`, five functions, ten
properties, two enums — a small value object that lives inside something else. The carrier is `Actor`:

```
var(Interaction) export editinline array<export editinline SGInteraction> InteractionData;
```

so any actor can own a list of interactions, authored per placement, and they are saved as inline
subobjects of that actor rather than as separate actors in the level.

### What an interaction says

| field | type | role |
|---|---|---|
| `AppliesTo` | `InteractionType` | who may use it: `SG1_PLAYER`, `SG1_AITEAM`, `SG1_ONEILL`, `SG1_TEALC`, `SG1_CARTER`, `SG1_JACKSON` |
| `DisplayText` | `localized string` | the prompt; `Actor.GetInteractionText(Index)` returns exactly this |
| `ScriptExecuteTag` | `name` | the script/event tag the interaction points at |
| `ScriptPriority` | `InteractionScriptPriority` | what an AI on script should do: `SCRIPT_IgnoreAllStimuli`, `SCRIPT_IgnoreEnemies`, `SCRIPT_StayOnScript`, `SCRIPT_LeaveScriptForCombat` |
| `AnimType` | `name` | which hands animation to play |
| `ActiveConeRange`, `ActiveDistance` | `float` | the focus volume, read through `GetActiveConeRange()` |
| `InteractionSound` | `Sound` | played on use |
| `TriggerOnceOnly`, `IsEnabled` | `bool` | one-shot and enabled flags |

### The callbacks

The object has three entry points and two getters: `InteractionUsed(ContextActor, UsingPawn)` for the
use action, `InteractionActive(...)` / `InteractionInactive(...)` when the player's focus enters or
leaves, and `GetActiveConeRange()` / `GetInteractAnimType()` for the driver to query. The active/inactive
pair forwards to the context actor's own `Actor.InteractionActive(Pawn User, bool bActive)`, so the actor
gets told when it becomes the focused thing.

### Who drives it

- `StargateGame.StargatePlayer` walks the focused actor's `InteractionData` and calls
  `InteractionData[i].InteractionUsed(hitObject, Pawn)` on the chosen entry — the player's use button.
- `StargateGame.CommandInterface` (the in-game command/HUD layer, 1,054 lines) draws the prompt:
  `GHP.SetInteractionText(...)` with the text from `GetInteractionText(ContextInteractionIndex)`, calls
  `InteractionActive(...)` when the focus changes and hides it with `InformInteractionActive(none)`.
- `Engine.SGUseTrigger` is the usual carrier: a `Trigger` subclass with its own `InteractionText`,
  `bAutoUsedByAI`, `SelfTriggered()`, `SpecialHandling(Pawn)` and `UsedBy(Pawn)`, i.e. a trigger that is
  authored as "use me" rather than "walk into me". `Engine.SVehicleTrigger`,
  `Engine.Pickup` and `Sharpeffects.SE_AdvExplosiveMeshActor` are the other in-engine carriers.

### Where it is in the content

`inventory/map_actors.csv` counts 621 `SGInteraction` entries across 61 maps, roughly ten per map. They
are not placed actors: because the class is `editinlinenew`, the map parser sees each one as an export
owned by the actor that carries it. That is worth knowing when reading the census — `docs/CONTENT_CENSUS.md`
reports 175,718 "placed actors" across 389 classes, and inline subobjects such as these are included in
that number.

## The visible half: `StargateContent.InteractionHands`

`InteractionHands` (296 lines) is the first-person hands actor that plays the animation an interaction
asked for: `ShowOnlyHands()`, `ShowNeedle()`, `ShowCard()`, `PlayIdleAnim()`, `FakeRaise()`,
`HasAmmo()`, `NeedsToReload()`, `AnimNotify_hit()`, and an `Active` state. The needle and the card are
the two prop-based interactions in the game (a medical injector and a keycard), which is consistent with
`LockedObjective`'s `KeyPickup`/`KeyTag` being the multiplayer equivalent of the same idea.

## Open questions

- **What `ScriptExecuteTag` actually fires.** The field name says "execute a script tag"; the trigger's
  `UsedBy` and the interaction's `InteractionUsed` are the two candidates for the call site. Not traced.
- **Whether `InteractionMaster` is used for gameplay at all.** The framework is present and instantiated
  for the menu/console/UIX; nothing measured shows a gameplay interaction routed through it.
- **Which actors carry which interactions.** That needs the export-owner relationship, which
  `inventory/map_actors.csv` does not record (it aggregates by class). PLAN 5.2's T3D dumps would show it
  per actor.
