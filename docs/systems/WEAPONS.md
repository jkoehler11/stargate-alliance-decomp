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

## What a weapon names

`tools/weapon-links.py` reads every class reference a class in the four trees sets in its default properties
and classifies the target by **its own super chain**, never by the property's name:

    python3 tools/weapon-links.py --system DUMP/System -o inventory/weapon_links.csv
    python3 tools/weapon-links.py --tree $WORK/defwep --write-doc docs/systems/WEAPONS.md

Output: `inventory/weapon_links.csv`, one row per reference. The population is **231 classes including the four
roots themselves** -- the table above counts the 227 descendants, which is why its rows are one short of each
root's count here. Class names are vocabulary this repository already publishes; the numbers a weapon sets are
still game data and stay in `$WORK`, so the artifact is the reference graph only.

<!-- generated:weapon-links -->
**315 class references over 176 of the 231 classes in the four trees** (ammo 69, damage 74, emitter 47, other 85, projectile 40). Every one names a class this dump carries.

| property | declared | edges | targets |
|---|---|---:|---|
| `MyDamageType` | `Class<DamageType>` | 78 | damage 74, other 4 |
| `AmmoName` | `Class<Ammunition>` | 53 | ammo 53 |
| `ProjectileClass` | `Class<Projectile>` | 36 | projectile 36 |
| `LowDetailEffect` | `Class<Effects>` | 30 | other 30 |
| `AttachmentClass` | `Class<InventoryAttachment>` | 28 | other 28 |
| `PickupClass` | `Class<Pickup>` | 19 | other 19 |
| `MeleeClipType` | `Class<StargateAmmo>` | 16 | ammo 16 |
| `MuzzleEmitter` | `Class<Emitter>` | 16 | emitter 16 |
| `ImpactClass` | `Class<Emitter>` | 15 | emitter 15 |
| `EmitterClass` | `Class<Emitter>` | 5 | emitter 5 |
| `ProjectileEmitterClass` | `Class<Emitter>` | 3 | emitter 3 |
| `ChargingEmitterClass` | `Class<Emitter>` | 2 | emitter 2 |
| `FullyChargedEmitterClass` | `Class<Emitter>` | 2 | emitter 2 |
| `FullyChargedProjectileClass` | `Class<Projectile>` | 2 | projectile 2 |
| `UnchargedProjectileClass` | `Class<Projectile>` | 2 | projectile 2 |
| `ChargedDecal` | `Class<Projector>` | 1 | other 1 |
| `ChargingEmitterType` | `Class<Emitter>` | 1 | emitter 1 |
| `ControllerClass` | `Class<Controller>` | 1 | other 1 |
| `ExplosionEmitterClass` | `Class<Emitter>` | 1 | emitter 1 |
| `MeleeChargingEmitterClass` | `Class<Emitter>` | 1 | emitter 1 |
| `MeleeFullyChargedEmitterClass` | `Class<Emitter>` | 1 | emitter 1 |
| `ReetouClass` | `Class<Reetou>` | 1 | other 1 |
| `UnChargedDecal` | `Class<Projector>` | 1 | other 1 |

A reference here is a **setting, not the value a class carries**: 53 weapons set an ammo class in their own block, but **62 of the 69 weapons in the tree end up with one** (9 inherit theirs) and 7 carry none (`Binoculars`, `EmptyHands`, `GRENADE`, `InteractionHands`, `ScopedWeapon`, `StargateRechargeWeapons`, `StargateWeapon`). `ProjectileClass` is not a `Weapon` property on this build at all -- a weapon names a bullet and the bullet names the shot -- so the resolved chain is the next block's measurement, and this table is the settings it starts from.

**Effects wiring, the item this page listed as not measured:** 35 distinct emitter classes are named, 16 weapons name a muzzle emitter and 15 a projectile impact emitter; the damage side's `LowDetailEffect` names one class (`BloodHit`), from 30 row(s).
<!-- /generated:weapon-links -->

Three things the table says that a property name cannot:

* **The chain is joined at the ammunition, not at the weapon.** `AmmoName` is declared on `Engine.Weapon`, but
  `ProjectileClass` and `MyDamageType` are declared on `Engine.Ammunition` -- a weapon names a bullet and the
  bullet names the shot. That is why this graph and the `X`/`XAmmo`/`XDamage` naming test disagree: a weapon
  that shares an ammo class inherits whichever projectile that class names.
* **`MeleeClipType` is an ammo class and `ChargingEmitterType` is an emitter class**, while `ImpactClass` is
  declared on the projectile rather than the weapon. Two properties whose names say "type" carry a class
  reference, and the one whose name says "class" is not on the weapon at all.
* **The damage vocabulary is not pure data after all.** The 37 `StargateDamageType` classes carry zero
  functions, but 30 of them set `Engine.DamageType`'s `LowDetailEffect`, and all 30 name the *same* class -- an
  `Effects` sprite (`Blood < UT_SpriteSmokePuff < AnimSpriteEffect < Effects`), not an emitter. So the damage
  tree reaches into `Stargateeffects` through one shared class, while the emitter references are per weapon
  (35 distinct classes over the 47 emitter references).

## What a weapon fires

The table above is *settings*; this is the value. Each hop is resolved through the class chain -- a weapon's
`AmmoName` is the nearest one it or an ancestor sets, the ammo's `ProjectileClass` likewise, and the damage type
falls back from the projectile to the ammo, because `Engine.Ammunition.MyDamageType` is what the engine stamps on
the projectile that ammo spawns:

    python3 tools/weapon-links.py --system DUMP/System \
        --effective-out inventory/weapon_effective.csv --write-doc docs/systems/WEAPONS.md

Output: `inventory/weapon_effective.csv`, one row for every weapon in the tree
(`package,class,ammo,ammo_set_by,projectile,projectile_set_by,damage,damage_via,damage_set_by`). The `*_set_by`
column names the class whose **own** block sets the value, so "this class sets it" and "it inherited it" are two
different rows; `damage_via` says whether the damage type came from the projectile or from the ammo. Class names
only -- no range, speed or damage number is read, and the values stay in `$WORK`.

<!-- generated:weapon-effective -->
**69 weapons, resolved through their class chains** (`inventory/weapon_effective.csv`, one row per weapon, names only): **62 carry an ammo class** -- 53 set it in their own block and 9 inherit it -- and **7 carry none** (`Binoculars`, `EmptyHands`, `GRENADE`, `InteractionHands`, `ScopedWeapon`, `StargateRechargeWeapons`, `StargateWeapon`). 42 of the 69 reach a projectile class and 60 reach a damage type.

| hop | weapons |
|---|---:|
| sets an ammo class in its own block | 53 |
| carries an ammo class (set or inherited) | 62 |
| carries a projectile class | 42 |
| damage type from the projectile's `MyDamageType` | 39 |
| damage type from the ammo's `MyDamageType` | 21 |
| resolves a damage type | 60 |

The 62 weapons that carry a bullet share **52 ammo classes** (the most shared, `APCM60GunAmmo`, is named by 4 weapons) and reach **29 damage classes**.

9 weapon(s) reach no damage type at all: `Binoculars`, `EmptyHands`, `GRENADE`, `GoauldKillerDroidWeapon`, `InteractionHands`, `ReetouDevice`, `ScopedWeapon`, `StargateRechargeWeapons`, `StargateWeapon`.
<!-- /generated:weapon-effective -->

Four things the resolved chain says that the settings table cannot:

* **The nine weapons that never set a bullet are variants, and the inheritance is the pattern.** `_Bot`,
  `_Enemy` and `_Weak`/`_JaffaCaptain` subclasses, plus `P90Torch` (inheriting `P90Prototype`'s `P90Ammo`),
  `GatlingGun` and `HummveeM60Gun` (inheriting the APC gun's `APCM60GunAmmo`). A rebuild that writes `AmmoName` into
  all 62 of these classes would add nine settings the original does not have -- the same trap as generating
  classes from the `X`/`XAmmo`/`XDamage` naming.
* **The projectile's `MyDamageType` is not always where the damage is.** Three weapons name a projectile that
  carries no damage type -- `FragGrenade`, `ShockGrenade` and `SemektetMortarGun`, the hand-grenade and mortar
  ammo -- and resolve `Exploded` from the **ammo** instead, so the fallback hop is load-bearing, not defensive.
* **Nine weapons reach no damage type, and only seven of those are ammo-less.** `GoauldKillerDroidWeapon` and
  `ReetouDevice` carry a melee ammo class and resolve nothing from it, so a damage number has to be in the
  weapon's script; the seven with no ammo class at all (`Binoculars`, `EmptyHands`, `GRENADE`,
  `InteractionHands`, `ScopedWeapon`, `StargateRechargeWeapons`, `StargateWeapon`) spawn their shot in script for
  the same reason. `GRENADE` -- 22 functions, 8 states -- is the clearest case: a weapon class whose whole
  firing path is code.
* **The armoury is a small vocabulary reused heavily, which the naming hides.** 52 ammo classes over 62 weapons
  and 29 damage classes over 60 weapons; one ammo class (`APCM60GunAmmo`) serves four weapons (the APC gun, the
  gatling gun, the Hummvee's M60 and the M60 turret), and 12 damage classes are reached by more than one weapon
  (`SGDamageMelee` alone by 7). Read as the one-to-one triple the naming implies, the 68 weapon classes would
  invent 16 ammo classes and 39 damage classes that do not exist.

## Not measured

* **Damage numbers.** `Damage`, `DamageRadius`, `MomentumTransfer` and `MaxSpeed` on `Engine.Projectile` are
  the values a shot uses, and they are game data: `weapon_links.csv` says which property carries one and which
  class declares it, and the numbers stay in `$WORK`.
* Which state of `GRENADE`'s 8 handles throwing versus detonation.
* **Arrays of class references.** `tools/defaults-emit.py` leaves arrays unrendered unless they tile, so an
  array-typed class property is absent from `weapon_links.csv` -- unmeasured here, not unset. Every property in
  the table above is a scalar, and a class-reference array would need the element tiling 5.2/4.7 is blocked on.
