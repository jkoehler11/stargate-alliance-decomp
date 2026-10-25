# Weapons: the one fully-scripted system

Four class trees cover the whole weapon system — **227 classes, and not a single native function among
them**. That makes weapons the most tractable system in the build for the rebuild: there is no C++ seam to
reconstruct, only script.

    python3 tools/class-graph.py --root StargateWeapon --root StargateAmmo \
        --root StargateDamageType --root StargateProjectile -o inventory

Output: `inventory/class_graph.csv` (227 rows). Reads committed CSVs only.

## Four vocabularies

| root | classes | states | functions | native |
|---|---:|---:|---:|---:|
| `StargateWeapon` (StargateGame) | 68 | 83 | 352 | **0** |
| `StargateAmmo` | 71 | 0 | 57 | 0 |
| `StargateDamageType` | 36 | **0** | **0** | 0 |
| `StargateProjectile` | 52 | 4 | 92 | 0 |

`StargateDamageType` is pure data: 36 classes, zero functions, zero states — a damage vocabulary, nothing
more. `StargateAmmo` is nearly the same (57 functions across 71 classes). The behaviour is in the weapon
(83 states) and the projectile (92 functions).

## Most weapons are named variants of a few bases

29 of the 68 weapons carry two functions or fewer, and some carry none: `GoauldDroneWeapon`,
`GoauldKillerDroidWeapon`, `P90Torch`, `P90Prototype`, `PistolPrototype`, `KULLDISRUPTOR`. The real logic
sits in bases and a handful of primaries:

| class | functions | properties | states |
|---|---:|---:|---:|
| `StaffCannonGun` | 23 | 22 | 3 |
| `GRENADE` | 22 | 24 | 8 |
| `C4Weapon` | 17 | 11 | 3 |
| `Binoculars` | 16 | 12 | 2 |
| `STINGER` | 15 | 20 | 4 |
| `ScopedWeapon` | 14 | 12 | 4 |
| `STAFFWEAPON` | 11 | 23 | 4 |

The naming is the original author's, not an artefact of the decompiler: `STAFFWEAPON`, `ZATGUN`,
`SNIPERRIFLE`, `HANDOFLIGHT` are capitalised in the package, next to mixed-case `C4Weapon` and
`ExtreniteWeapon`. That inconsistency is preserved here because renaming it would hide which classes were
written late.

## The 1:1 naming convention is only partly real

The naming suggests a triple per weapon — `X` / `XAmmo` / `XDamage` — and it holds for **17 of 68 weapons**:

* 30 weapons have an `XAmmo` class by name,
* 18 have an `XDamage` class,
* 17 have both (`APCM60Gun`, `AlkeshBomb`, `AlkeshCannon`, `CaptainStaffWeapon`, `DeathGliderGun`,
  `ExtreniteAutoTurretGun`, `ExtreniteWeapon`, `GoaAutoTurretGun`, …).

So a third of the armoury reuses a shared ammo or damage class instead of declaring its own. Anyone
generating a rebuild from the naming pattern would create classes that never existed.

One `_Enemy` suffix exists (`ExtreniteWeaponAmmo_Enemy`) — a duplicated ammo class for the AI side of the
same weapon, the only such case in the tree.

## Weapons are inventory, not scenery

No weapon class is placed in any of the 73 maps. They are given to pawns and dropped, and the checkpoints
say so:

| class | instances saved |
|---|---:|
| `FragGrenade` | 447 |
| `P90Prototype` | 432 |
| `PistolPrototype` | 328 |
| `STAFFWEAPON` | 279 |
| `Binoculars` | 218 |
| `StaffCannonGun` | 114 |
| `SNIPERRIFLE` | 113 |
| `GatlingGun` | 84 |

That is the player's inventory and the dropped-weapon population mid-mission, which is why the checkpoint
schema is dominated by weapon-sized object counts (`docs/systems/CHECKPOINTS.md`). A rebuild that gets
weapon state wrong will fail the checkpoint comparison immediately, which makes this system a good early
verification target.

## Not measured

* Ballistics and damage numbers. Those are class defaults inside the script bodies
  (`$WORK/UnrealScript`), which this repository does not carry; the class graph gives structure, not values.
* Which state of `GRENADE`'s 8 handles throwing versus detonation.
* Effects wiring. `Sharpeffects` holds muzzle/impact emitters named after weapons (`SE_3rd_P90MuzzleEmitter`,
  `SE_CaptainStaffWeaponImpactEmitter`), but which emitter a weapon selects is a default property, not a
  class relationship, so the graph cannot link them.
