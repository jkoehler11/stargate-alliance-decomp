# Vehicles: a big package, a small vehicle tree, and most of it is mounted weapons

`StargateVehicles` is 498,874 bytes and 142 classes — but only about a tenth of those classes are
vehicles. Measured from the class graph, the tree under the engine's `Vehicle` base is 44 classes with
**17 states and 260 functions between them**, and 13 of those 44 are empty shells.

    python3 tools/class-graph.py --root Vehicle -o inventory

Output: `inventory/class_graph.csv` (44 `Vehicle`-rooted classes with states, functions, natives,
properties, map placement and checkpoint instances). Reads committed CSVs only.

## The tree

| level | classes | notes |
|---|---:|---|
| `Vehicle` (Engine) | 1 | base |
| `SVehicle` (Engine) | 1 | Karma-physics sibling, the fork's actual base |
| `StargateVehicle` (StargateGame) | 1 | 33 functions, 33 properties — the fork's vehicle layer |
| `StargateCar` (StargateVehicles) | 1 | 14 functions, 77 properties |
| `StargateHover` | 1 | 11 functions, 46 properties |
| `StargateCopter` | 1 | 12 functions, 36 properties |
| concrete vehicles | 10 | below those four |
| seats, turret mounts, emplacements, consoles | 13 | **zero** states, zero functions |

Totals: 44 classes, 17 states, 260 functions, 7 native.

Concretely: `Hummvee` and `APC` and `Jeep` and `QuadBike` under `StargateCar`; `HoverCraft` → `HoverTank`
and `Semektet` under `StargateHover`; `AlkeshBomber` and `Teltak` under `StargateCopter`; and `DeathGlider`
directly under `StargateVehicle`.

## Behaviour is a rounding error

17 states across the whole tree. The only classes with a real state machine are `HoverCraft` (5 states,
6 functions) and `Semektet` (5 states, 5 functions); everything else is 0–2 states. The most functions sit
in the two fork bases — `StargateVehicle` 33, `StargateWeaponEmplacement` 24 — and in `DeathGlider` 19 and
`AlkeshBomber` 18.

That is the opposite shape from the AI (`docs/systems/AI.md`: 165 states, 758 functions). Vehicles are
engine-physics-driven: the script sets up a `SVehicle` and its properties (`StargateCar` alone carries 77)
and the behaviour lives in the Karma/physics path, which is native. **7 natives in the tree**, and the fork
bases hold them.

## What the package is actually made of

| super | classes in `StargateVehicles` |
|---|---:|
| `StargateWeapon` | 13 |
| `StargateAmmo` | 12 |
| `StargateDamageType` | 10 |
| `StargateProjectile` | 10 |
| `RosterEntry` | 7 |
| `SquadRoster` | 7 |
| `VehicleTurret` | 6 |
| `StargateVehicleSeat` | 5 |

142 classes, 400 functions, 62 states. So the package named "Vehicles" is mostly **vehicle-mounted
weaponry**: guns, ammo, damage types and projectiles for the turrets, plus rosters. Anyone estimating
vehicle work from the package size would be off by a factor of ten.

## Vehicles are placed; enemies are not

This is the sharpest contrast with the AI:

| class | placed in maps | instances in checkpoints |
|---|---:|---:|
| `StaffCannon` | 21 | 139 |
| `DeathGlider` | 12 | 0 |
| `Hummvee` | 9 | 134 |
| `M60TurretEmplacement` | 8 | 0 |
| `AlkeshBomber` | 6 | 38 |
| `Semektet` | 1 | 0 |

Six vehicle classes appear in map files, and two of them (`Hummvee`, `AlkeshBomber`) also appear in
checkpoints. `HummveeInteriorSeat` is saved 246 times and `HummveeMachineGun` 136 — seats and mounted guns
are persisted per vehicle instance, which is what a mid-mission save of an occupied vehicle looks like.

So the T3D dump (5.2) *will* answer "which vehicles does this mission have" — unlike "which bots", which
lives in the spawner.

## Not measured

* How the vehicles drive. The physics is `SVehicle`/Karma in native code; nothing here says how a hover
  tank's parameters are set beyond the property names.
* Which of the 7 natives belong to which virtual — `docs/systems/UCPP_NATIVES.md` measures why the class
  table does not answer that.
* Seat and turret wiring: `VehicleTurret` (6 classes) and `StargateVehicleSeat` (5) exist and are saved,
  but which seat index maps to which turret is configuration, not visible in the class graph.
