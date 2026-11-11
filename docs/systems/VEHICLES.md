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
## Seat and turret wiring

The wiring the class graph could not show is not behaviour: it is a **default property value**.
`StargateVehicle` declares two arrays of structs, and every class that has seats tiles one tagged struct per
slot in its default property block:

    StargateGame,StargateVehicle,DriverWeapons,UArrayProperty,...,array<DriverWeaponStruct>
    StargateGame,StargateVehicle,PassengerWeapons,UArrayProperty,...,array<PassengerWeaponStruct>

`tools/vehicle-seats.py` reads those blocks out of `tools/defaults-emit.py`'s emitted tree and writes
`inventory/vehicle_seats.csv`; the table below is generated from that artifact (`make vehicle-seats`,
`--check-doc` in `make ci`), so the page cannot drift from the read.

<!-- generated:vehicle-seats -->
**25 slots over 11 classes:** empty 4, seat 10, turret 5, weapon 6.

| class | slot | # | pawn | attach | role |
|---|---|---:|---|---|---|
| `GoaDropTurret` | driver | 0 | `GoaDropTurretGun` | none | weapon |
| `StaffCannon` | driver | 0 | `StaffCannonGun` | named | weapon |
| `APC` | passenger | 0 | `APCM60Turret` | named | turret |
| `APC` | passenger | 1 | `APCInteriorSeat` | named | seat |
| `APC` | passenger | 2 | `APCInteriorSeat` | named | seat |
| `APC` | passenger | 3 | `APCInteriorSeat` | named | seat |
| `GoauldTurretEmplacement` | driver | 0 | `GoauldTurretEmplacementGun` | none | weapon |
| `HoverCraft` | driver | 0 | `HoverCraftFixedGun` | named | weapon |
| `HoverCraft` | passenger | 0 | `HoverCraftTurretGunPawn` | named | turret |
| `HoverCraft` | passenger | 1 | `HoverCraftPortSeat` | named | seat |
| `HoverCraft` | passenger | 2 | `HoverCraftStarboardSeat` | named | seat |
| `HoverTank` | driver | 0 | — | none | empty |
| `HoverTank` | passenger | 0 | `HoverTankTurret` | named | turret |
| `HoverTank` | passenger | 1 | — | none | empty |
| `HoverTank` | passenger | 2 | — | none | empty |
| `Hummvee` | passenger | 0 | `HummveeMachineGun` | named | turret |
| `Hummvee` | passenger | 1 | `HummveeInteriorSeat` | named | seat |
| `Hummvee` | passenger | 2 | `HummveeInteriorSeat` | named | seat |
| `M60TurretEmplacement` | driver | 0 | `M60TurretGun` | none | weapon |
| `QuadBike` | passenger | 0 | `QuadBikeSeat` | named | seat |
| `Semektet` | driver | 0 | — | named | empty |
| `Semektet` | passenger | 0 | `SemektetMortarTurret` | named | turret |
| `Semektet` | passenger | 1 | `HummveeInteriorSeat` | named | seat |
| `Semektet` | passenger | 2 | `HummveeInteriorSeat` | named | seat |
| `TempleTurretEmplacement` | driver | 0 | `TempleTurretGun` | none | weapon |

Concrete vehicles under `StargateVehicle` with no slot row at all — an unset array is an empty array, and the property is declared once on the base: `AlkeshBomber`, `DeathGlider`, `Jeep`, `Teltak`.
<!-- /generated:vehicle-seats -->

Four things the table says that the class graph could not:

* **A driver slot can be a gun, and then there is no seat.** Six slots carry a weapon on the driver: five
  emplacements (`StaffCannon`, `GoaDropTurret`, `GoauldTurretEmplacement`, `M60TurretEmplacement`,
  `TempleTurretEmplacement`), each with no passenger slot at all, and `HoverCraft`'s fixed gun — the only real
  vehicle here with a driver weapon. Every other gun in the table is a passenger slot.
* **The two structs do not spell their pawn field the same way.** A `DriverWeaponStruct` element carries
  `WeaponClass`, a `PassengerWeaponStruct` element carries `WeaponPawnClass`. A reader that looks for one name
  returns half the wiring, which is the failure `--check` drives on purpose.
* **The role comes from the super chain, and a seat pawn does not name its vehicle.** The five turret slots
  reach `VehicleTurret` and the ten seats are five `StargateVehicleSeat` classes — and `Semektet`'s two rear
  seats are `HummveeInteriorSeat`, a pawn shared across vehicles, so the array a slot sits in is what says
  which vehicle it belongs to. Six turret pawns in the tree (`HummveeTurretRear`, `…RearLeft`, `…RearRight`,
  `SemektetStaffTurret`, `…StaffTurretLeft`, `…StaffTurretRight`) are never named by a slot array at all.
* **`attach` is whether a slot names a bone, never which one.** 18 slots name one and 7 do not; the bone's
  name is a property value, i.e. game data, so it stays out of the artifact (AGENTS.md).

