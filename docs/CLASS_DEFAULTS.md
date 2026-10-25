# Class defaults: located, and only partly decoded

The last link in the chain `map → spawner → squad script → roster → bots` is a **class default**, and the chain
is what the T3D work (`docs/T3D.md`) and the compile loop (PLAN 3.6) both need. This is where that stands: the
block can be found, and its values decode for some types but not all.

    python3 tools/pkg-defaults.py DUMP/System --class SGOrder

## What works

A class export's default-property block is located the same way an actor's is: try every start, require the
block to terminate with `None` exactly where the export ends, and keep the candidate with the most properties.
For `SGOrder` (229-byte export) the block is at `+0xAC` and holds **10 properties**:

| property | type | value |
|---|---|---|
| `canPerformOneill` | Bool | `True` |
| `canPerformCarter` | Bool | `True` |
| `canPerformTealc` | Bool | `True` |
| `canPerformJackson` | Bool | `True` |
| `maxBotsAssigned` | Int | `-1` |
| `Condition` | Byte | `1` |
| `iconTexture` | Object | `icon_mission'Engine'` |

`Bool`, `Int`, `Byte` and object references decode correctly, and the values are exactly the kind of authoring
data a class default holds — which is the same set of properties `inventory/class_properties.csv` says `SGOrder`
declares.

## What does not

Two problems, both visible in the same dump:

* **String and Str values come out misaligned.** `Description` decodes as `Str BeginState` and `Core` as an
  empty `String`. The block has two competing candidate starts two bytes apart (`0xAC` and `0xAE`) and both pass
  the "ends at the export end" test, so the locator's tie-break can pick the wrong one by the width of a single
  property.
* **The chain does not close yet.** The squad scripts the maps name — `TSS_7A_B_Jaffa_01`,
  `AI_Jaffa_Horus_Med_single` — are not `Class` exports in any of the 18 `System/` packages, and not in the map
  package that references them either. They resolve as object references, so they name something, but this
  reader has not found where the definition lives. Until it does, the roster is unreachable.

## Why this is still worth keeping

It is the difference between "class defaults are unknown" and "class defaults are at `+0xAC` in a 229-byte
export, with Bool/Int/Byte decoded and the string alignment still wrong". The next attempt does not have to
rediscover the block's existence, its location, or the tie-break ambiguity — all three are measured here, with
the two competing starts named.

## Not measured

* Any property's value in bulk: defaults are authoring data for the whole game, and this file reports one class.
* Where the squad-script definitions live. Two hypotheses are excluded by measurement (not in `System/`, not in
  the referencing map); the next step is to resolve the reference index all the way through the import table
  rather than trusting the printed name.
* Whether the two-byte ambiguity is a real alternation (some classes' blocks start at an odd offset) or always a
  tie-break error.
