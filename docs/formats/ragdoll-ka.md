# `.ka` ragdolls and `.kaw` workspaces (Karma XML)

**Container:** XML, CRLF line endings, UTF-8. Root `<KARMA ka_file_version="1.0">`. Not a package,
not compressed: a text asset that ships as-is.

Measured over the three shipped files (78,845 / 101,839 / 110,079 bytes):

| file | assets | parts | joints | primitives | models |
|---|---|---|---|---|---|
| `Resources/HaakenRagdolls.ka` | 2 | 38 | 36 | 75 | 38 |
| `Resources/SG1CrewRagdolls.ka` | 3 | 51 | 48 | 111 | 51 |
| `Resources/StargateRagdolls.ka` | 3 | 52 | 49 | 70 | 52 |
| **total** | **8** | **141** | **133** | **256** | **141** |

## Element and attribute schema

| element | attributes | appears | meaning |
|---|---|---|---|
| `ASSET` | `id`, `graphic`, `scale`, `mass_scale`, `length_scale` | 8 | one ragdoll; `graphic` is the `.PSK` skeletal mesh it wraps, `scale` is `0.02` in every asset |
| `GEOMETRY` | `id` | 251 | one collidable body (named after the bone, e.g. `bip01 l calf`) |
| `PRIMITIVE` | `id`, `type` | 256 | shape inside a geometry: `sphyl` (56) or `sphere` (14) in `StargateRagdolls`, both used across files |
| `RADIUS`, `HEIGHT` | — | 256 / 166 | primitive dimensions, Unreal units |
| `TM` | — | 397 | 16 comma-separated floats, one per primitive and joint: a 4×4 transform in row-major order with the translation in the last row |
| `MODEL` | `id`, `type`, `geometry` | 141 | a body's dynamic model; `type` is `dynamics_and_geometry` (38) or `dynamics_only` (14) in `StargateRagdolls` |
| `DYNAMICS` | — | 141 | mass properties: `MASS`, `DENSITY`, `MASS_OFFSET`, `INERTIA` (3 floats), `LIN_DAMP`, `ANG_DAMP`, `FAST_SPIN`, `USE_FAST_SPIN` |
| `PART` | `id`, `model`, `parent` | 141 | body in the hierarchy; `parent` links to another `PART` (absent on the root) |
| `JOINT` | `id`, `part1`, `part2`, `type` | 133 | constraint between two parts |
| `POS1`, `POS2` | — | 133 each | joint anchor in each body's space |
| `PRIMARY_AXIS1/2`, `ORTHOGONAL_AXIS1/2` | — | 133 each | joint frame (two vectors per side) |
| `CONE_TYPE`, `CONE_HALF_ANGLE_X/Y`, `CONE_STIFFNESS`, `CONE_DAMPING` | — | 90 | cone limit of a joint |
| `TWIST_TYPE`, `TWIST_HALF_ANGLE`, `TWIST_STIFFNESS`, `TWIST_DAMPING` | — | 90 | twist limit |
| `HIGH_LIMIT`, `LOW_LIMIT`, `HIGH_STIFFNESS`, `LOW_STIFFNESS`, `LIMITED` | — | 43 | hinge-style limit block |
| `MOTORIZED`, `DES_VEL`, `MAX_FORCE` | — | 43 | motorised joint (used by the walkers, not by the human ragdolls) |
| `NO_COLLISION` | `part1`, `part2` | 232 | pairs of parts whose collision is disabled; the human rigs are dominated by these |

The mesh reference is a plain `.PSK` filename, so the ragdoll and the skeletal mesh are matched by
the asset's `graphic` attribute, not by file name: `SG1Crew` uses `sg1_crew_oneill.PSK`, and the same
mesh appears under several asset ids.

## `.kaw` — authoring workspace

`Resources/StargateRagdolls.kaw` (519 bytes) is a `KAT_WORKSPACE version="1.0"` document holding
three `FILE` entries, each with `NAME` and `RESOURCE_PATH`. It is the Karma *authoring tool's* list
of the `.ka` files in a project, not something the game loads at runtime — the XBE has no reader for
it, and the three entries correspond to the three `.ka` files in `Resources/`.

## Where the runtime side lives

The engine's Karma integration is native: the `K*` natives on `Engine.Actor`
(`KGetRigidBodyState`, `KSetMass`, `KAddImpulse`, `KMakeRagdollAvailable`, …) are declared by the
script and dispatch through `GNatives` (`inventory/natives.csv`). The loader that turns a `.ka`
into bodies is engine-side and undocumented until PLAN 4.8; the format itself needs no work.

## The runtime side, located

The `.ka`/`.kaw` files are XML, so they need no reverse engineering — but the *code that consumes ragdolls* is in the
image, and it announces itself the same way LIPSinc does. `tools/image-strings.py --topic karma`
(`make karma-runtime`, `inventory/karma_runtime.csv`, `--check` in `make ci`) attributes every `push` of a `Karma*` or
`KarmaSupport.cpp` string to the function containing it: **8 functions**.

| address | bytes | name | evidence | called by |
|---|---:|---|---|---|
| `0x000a4820` | 176 | `Engine_KScript` | `KarmaParamsCollision` | `Core_UnObjBas` (`0x12620`) and four class registrations |
| `0x000a4c90` | 179 | `Engine_KScript` | `KarmaParams` | `Core_UnObjBas` and five more |
| `0x000a4d50` | 179 | `Engine_KScript` | `KarmaParamsRBFull` | `Core_UnObjBas` and three more |
| `0x000a4e10` | 179 | `Engine_KScript` | `KarmaParamsSkel` | `Core_UnObjBas` and four more |
| `0x0008ded0` | 860 | `FUN_0008ded0` | `KarmaGlobals` | **`Engine_UnGame`** (`0x139030`) |
| `0x000ae030` | 872 | `FUN_000ae030` | `Karma Safe-Time Disabled.` / `Enabled.` / `Karma debugging option recognized` | `0x15ef40` |
| `0x000d8110` | 950 | `FUN_000d8110` | `KarmaGravScale` / `KarmaTimeScale` | — |
| `0x00253950` | 10,539 | `FUN_00253950` | `Karma` | **`0x001226a0`** |

Three things follow from where they sit, rather than from reading them:

* **The four `UKarmaParams` subclasses are already named** (`Engine_KScript` from their asserts) and all four are
  constructed through `Core_UnObjBas` at `0x12620` — the same static-constructor dispatcher every class registration
  goes through, so that is registration, not physics.
* **`KarmaGlobals` is initialised from `Engine_UnGame`**, which is what the `XLevel->Ragdolls` field in
  `docs/FORK.md` implies: ragdolls are level state, set up when the game class starts.
* **The biggest body (10,539 bytes) is called from `0x001226a0`** — the same function the save/load work located as the
  `LoadSaveGameInfo` neighbourhood (`docs/UNKNOWNS.md` U5). So ragdoll setup sits in the level-load path, next to the
  package check and the save loader, in one region of the image.

Not measured: which function parses the XML. The `.ka` files are the *authoring* format; what ships in the packages are
cooked `KarmaParams` properties, so the parser may never exist in this build. That distinction is why the ragdolls are
"fully documented" without any of the code above being read.
