# Squad rosters: the `SQUAD_*` classes read, the member list does not

`docs/systems/AI.md` ends its chain at "the roster is a class default". The defaults reader
(`tools/class-defaults.py`) can now resolve those blocks, so this is the roster data itself.

    python3 tools/squad-rosters.py DUMP/System -o inventory   # 65 rows, inventory/squad_rosters.csv
    python3 tools/squad-rosters.py DUMP/System --class SQUAD_Anubis --walk

## What a roster class contains

`SquadRoster` is a `StargateGame` class with 65 subclasses in `StargateContent`, and its declared properties are
the AI vocabulary a squad is authored with: `RosterString`, `PawnTag`, `SquadTag`, `Members`, the three order
presets (`MovePreset`, `ShootPreset`, `EngagementPreset`), `CombatEvent`, `DeathEvent`,
`bAllowMoraleFailure`, `bCanLeaveTurrets`, `bCanUseTurrets`.

**58 of 65 read**, all at the same block offset (`+0x5D`) — a uniform result that is itself the check: a
locator gone wrong would scatter the offsets. Each reads `RosterString` (authoring prose, longest 39 characters),
`PawnTag` and `Members`.

| | |
|---|---:|
| `SQUAD_*` classes | 65 |
| defaults block read | **58** |
| with a `PawnTag` | 58 |
| distinct `PawnTag` values | 53 |
| total members across all rosters | **129** |
| no `Class` export | 7 |

Members per roster: 44 rosters hold **one** member, then 3, 4, 5, 6 and 7 members (three rosters each) and one
outlier, `SQUAD_Jaffa_Full_Patrol`, with **16**. Most "squads" in this build are single-character rosters —
`SQUAD_Thor`, `SQUAD_GeneralHammond`, `SQUAD_SuperSoldier`, `SQUAD_Tokra_Single` — and the multi-member ones are
Jaffa and Extrenite patrols.

`PawnTag` is authoring metadata, not a unique key: `Dropturret` is shared by three rosters,
`SQUAD_Extrenite_Quad` by two, and both `JaffaSerpentL_Patrol` and `JaffaSerpentL_Single` by two each. The class
name is the key; the tag is what the designer called it.

## The member list is a run of serial numbers, not references

`Members` is declared `array<export editinline RosterEntry>`, and the payload is **one count byte and then two
bytes per member** — measured across seven distinct payload sizes (3 bytes → 1 member, 33 bytes → 16, every case
`2n+1`). The two-byte values are consecutive and increase by one per member:

| roster | members | values |
|---|---:|---|
| `SQUAD_Anubis` | 1 | 30309 |
| `SQUAD_ExtreniteChild` | 4 | 30317 … 30320 |
| `SQUAD_Jaffa_Full_Patrol` | 16 | 30573 … 30588 |

So they are **not** name-table indices (checked against that package's 6,970 names — all out of range) and not
object references (a reference to an inline `RosterEntry` would carry the entry's own data, not two bytes). They
are allocated identifiers, monotonic inside a roster, and what table they index is not established here. That is
the one link still missing between "this squad has 16 members" and "these are the 16 pawns".

## The seven rosters with no `Class` export

`SQUAD_Alkesh_Bomber_Single`, `SQUAD_DeathGlider_Single`, `SQUAD_Goauld_Drone_Single`,
`SQUAD_Goauld_Killer_Droid_Single`, `SQUAD_HoverTankPilots`, `SQUAD_HoverTank_Single`, `SQUAD_Teltak_Single`.

Read the list: every one is a vehicle or single-minion roster, and every one ends `_Single` except the two
HoverTank entries. They are inventoried as classes in `inventory/classes.csv` but have no `Class` export in
`StargateContent.u`, so the roster data exists only somewhere this reader does not follow — the same shape of
gap as the map-side references. Whether they are `Object` exports under a different class name, or defined in a
map, is open; the names are recorded so the next search is a lookup rather than a scan.

## Not measured

* The identifier table the member values index, above. That is what would turn a count into a roster.
* The order presets' *values* — `MovePreset`/`ShootPreset`/`EngagementPreset` resolve to `MoveOrder`/
  `ShootOrder`/`EngagementOrder` classes, and reading their defaults (`docs/CLASS_DEFAULTS.md`) is the next
  layer of the same question.
* The `TSS_*` names the spawners carry (`inventory/squad_scripts.csv`) do not match `SQUAD_*` class names, and
  neither appeared as an export in the referencing map. The spawner-side name and the roster class are still
  two different name spaces; how they meet at runtime is not measured.
