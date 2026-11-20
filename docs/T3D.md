
# T3D: values, references, and what a spawner points at

The T3D writer from the previous step produced actor *names* and sizes. This adds the values, and in doing so
answers a question the AI system doc had recorded as unanswerable.

## What changed

* **Property values decode.** Values inside a package's property stream are *compact* — a name or object
  reference is one or two bytes, not a fixed four — and a vector arrives as `Struct(Vector)`, not as a
  `Vector` type. Reading them by assumed width is why `Location` and `Squad` came out empty on the first
  attempt.
* **Object references resolve** to `Class'Name'` form (`SQUAD_JaffaSerpentL_Single'SQUAD_…2'`) instead of
  `ref:<index>`.

Measured over the same 73 maps:

| | |
|---|---:|
| actors written | 121,225 |
| actors carrying a `Location` with coordinates | **111,520** |
| distinct `SuggestedSquadScript` values | **601** |
| maps whose spawners reference a squad script | **61 of 73** |
| references that do not resolve (`?`) | 820 |

`inventory/squad_scripts.csv` holds (map, squad script, spawner count) — 630 rows of identifiers.

## Spawners point at named AI scripts

`IntelligentSpawner` never names a bot class. Its properties are `SpawningRate`, `Squad`,
`SuggestedSquadScript`, `spawnPriority` and `bForceSpawn`, and `SuggestedSquadScript` carries a *script class*
name — `AI_Jaffa_Horus_Med_single`, `AI_Jaffa_Horus_Med_Obs`, `Jaffa_Enc5`, `TSS_7A_B_Jaffa_01`. So the chain
from a mission to its enemies runs:

```
map → IntelligentSpawner → SuggestedSquadScript (a class) → that class's default roster → bot classes
```

The first two links are now measured. The third is a **class default**, which is the one part of the package
format this reader still cannot reach (`docs/ACTOR_PROPERTIES.md`: class objects have no property block). So
"which bots does mission 7 use" is answerable *by encounter script name* today, and by bot class only once
defaults parse.

## Correcting an earlier claim

`docs/systems/AI.md` said "no AI class is placed in any map… so 'which bots does this mission use' is not
answerable from the `.unr` alone". Half of that stands — no bot *class* is placed. But the map does name the
encounter script that will spawn them, 61 of 73 maps carry such names, and the names are explicit about what
they spawn. The doc now says that instead.

## Still not measured

* ~~The 820 unresolved references, and the 18 `?` squad scripts.~~ **Settled** (2026-11-19): the name-table
  fix names all 18 (`inventory/squad_scripts.csv` is 630 rows), and the three references that survived it were
  one locator rule -- the T3D now has none. The old hypothesis, that they point at imports the reader does not
  index, is wrong: both were reads that stopped one test short. See "the three values that fell through".
* Class defaults, above.
* The palette's tail after the colour table: 17,183,118 bytes on all 33,509 payloads, whose first field is a
  dword in 2..10 and whose remainder is not a tagged block (both measured at the end of this page).
* ~~The `Level` export's script sequence.~~ **Settled** (2026-11-19): there is no script in any map
  (`docs/MAP_SCRIPTS.md`), and the export's payload is the level's own actor list (the section at the end of
  this page). The `ACTION_*` ordering the old bullet pointed at is `docs/MAP_ACTION_ORDER.md`.

## Regenerating these numbers

`make map-census` (`tools/map-census.py`) rebuilds `map_property_coverage.csv` and
`actor_property_coverage.csv` from the `.unr` files, and both reproduce **byte for byte**:
73 maps and 389 classes, with the property-block locator in `tools/class-defaults.py` deciding what "has a block"
means (a start in the first 64 bytes whose stream ends exactly at the export's end and whose names all resolve).
That is a strict reading — it counts what can be read, not what might be there.

`squad_scripts.csv` is written by it too, as of 2026-11-19, and the "not yet reproducible" reading above was
the name-table fix rather than a broader rule: the pass finds **630** `(map, script)` pairs where the old file
had **614**, and the whole delta is two maps — `M17D` and `M21A`, the two whose name tables the length rule
truncated (`docs/PACKAGE_READER.md`), which the old file recorded as a single `?` row each. Those two rows are
now the **18 scripts** behind them: `M17D`'s 8 spawners are seven scripts named once each and a `noScript`
shared by nine, `M21A`'s 10 are ten singletons. So `614 - 2 + 18 = 630` rows, and the spawner column moves
with them: **719** properties (`IntelligentSpawner` 706, `HaakenPod` 13) against the old file's 711, because
the `M17D` placeholder held 8 where the map carries 16 — 8 spawners were never counted at all, which is what
the earlier note called a "residue" and left open. Every one of the 612 rows the two enumerations share
agrees on its spawner count (`711 - 18 + 26 = 719` the other way), so this is a replacement and not a second
enumeration.

**It reads the value from the block the T3D writer reads.** The coverage locator takes the *earliest* start
whose block ends at the export's end; the writer takes the candidate with the most properties. On these 73
maps the first names **591** pairs and the second **630**, so the artifact follows the writer — the page and
the file have to be one reading, and the 39 pairs are the cost of the earlier parse. `make map-census` writes
all four map-side artifacts and its `--check` (dump-free) validates this one's shape, positive counts and
unique pairs; the pass is 80 s, 30 s of it the coverage walk and the rest this re-read.

## Every map written, with references resolved (measured 2026-11-06)

`make t3d` now writes a committed coverage table beside the T3D itself (`inventory/t3d_coverage.csv`,
`tools/map-to-t3d.py --stats`). One run over the 73 shipped maps:

| measure | value |
|---|---|
| maps | 73 |
| actors | 121,225 |
| properties | **1,178,141** |
| unresolved object references | **3** |
| T3D bytes | 44,375,952 |

**The `ref:<index>` reading is stale, and the three values that fell through were one rule (2026-11-19).**
`prop-stream.py` resolves an export index to `Class'Package.Name'` and a negative one to the import's
`Class'Package.Object'`; three values in 1.18 million used to fall through, and this page listed them as three
defects -- a struct read as an object in `M02A`, an index past the export table in `M15A`, the same in `M17B`.
They are one defect: the locator's tie-break tested the block's *names* and not its *references*, so in `M02A`
a `Trigger`, in `M15A` a `MissionObjective` and in `M17B` a `StaticMeshActor` the parse began at a start that
spent the front of the next property on a reference landing nowhere -- byte 1 of a 17-byte export in `M02A`
(inside the record in front of the block), byte 19 of `M15A` and byte 18 of `M17B` (four and three bytes into
the block). Reading the same 73 maps with "every object reference in the block resolves" as the first test
moves **3 of 121,394** block starts -- exactly those three -- and leaves every other start where it was, so the
writer now has **0 unresolved references**, **1,180,946** properties (3 fewer: the bytes the misparse invented
were being counted as properties) and 49,212,006 bytes of T3D. The three defects are gone rather than named:
no `ref:` reaches the text, and `inventory/t3d_coverage.csv` counts any that come back.

**The totals above are superseded** by the 2026-11-19 section at the end of this page: the table is regenerated
by `make t3d`, and the two maps whose name tables the length rule had truncated (`M17D`, `M21A`) now locate
169 more actors and 2,808 more properties.

**What this does not claim.** References resolve; *arrays and structs are still emitted by size*, so a property whose
value is a struct array does not round-trip through an editor yet, and the `Level` export's script sequence is still
separate from the actors. The T3D is much closer to editor-loadable than the previous note said, and not yet there.
The array half of that sentence is the next section's, and it is now only half true.

## An array is its elements, and a struct's width is not fixed (measured 2026-11-19)

The writer above still printed an `Array` property's payload as one opaque string, which is the difference
between a T3D an editor can load and one it cannot: `PathList=` followed by byte soup is not a reach-spec array.
The layout had already been measured for one array — `tools/action-wiring.py` reads `Actions` as a compact count
followed by exactly that many compact object indices — and this writer now applies that rule to every array.

**The two tests that make it safe.** A payload decodes as an object array only when it consumes itself exactly
*and* every index lands in the package's own export or import table. The second test is what keeps a struct array
from being read as actors: `SizeScale` is 35 bytes of floats and its first byte is a plausible count. Anything
that fails either test is counted, never printed.

| what the writer emits | before | now |
|---|---|---|
| object array | one string of bytes | `PathList(0)=ReachSpec'ReachSpec2550'`, one line per element |
| struct array | the same string | `// SizeScale: 2 element(s) of 17 byte(s), element type not in the export` |
| struct value | `(X=…,Y=…,Z=…)` for `Vector`/`Rotator`/`Color` only | unchanged, and the census counts the rest |

Two censuses are committed beside the coverage table, both written by `make t3d` and arithmetic on each other
under `--check` (which needs no dump): `tools/t3d_values.csv` per map, `inventory/t3d_structs.csv` per struct name.

<!-- generated:t3d-values -->
| measure | value |
|---|---:|
| maps | 73 |
| `Array` properties | 38930 |
| of those, object arrays | **34218** |
| object references inside them | **127563** |
| arrays refused a (count, width) layout | 4687 (4687 struct arrays holding 31604 records) |
| -- of those, a compact-index array | **0** (0 element(s)) |
| -- of those, a string array | **25** (126 element(s) decoded) |
| -- of the struct arrays, element type declared | **4687** |
| -- of those, elements decoded by a fixed width | **14499** |
| -- of those, elements decoded as tagged blocks | **4541** arrays, **17105** records |
| -- arrays no rule reaches | **0** |
| struct-typed properties | 315462 |
| struct properties decoded | **315453** |
| -- native bodies (`Vector`, `Rotator`, `Color`) | 203340 |
| -- tagged blocks (fields read from the payload) | **112113** |
| struct property names | 12 |
| `StaticMeshInstance` payloads | 33509 |
| palette colours inside them | **7112701** |
| records whose alpha is zero | 5643 |
| payloads the palette rule refuses | 0 |
| native bytes after the palette | 17183118 |
| exports whose block is followed by a native record | 66 |
| tails whose head dword is 2 | **30450** of 33509 |
| records the tails count | **466858** |
| -- records whose flag dword is 0 | 10582 |
| -- records whose first field is an export index | **466858** of 466858 |
| -- of those, naming a light actor | **466435** (423 other) |
| tails whose record list closes exactly | **33509** of 33509 |
| `Level` exports read | **73** |
| actors the `Level` exports name | **79063** |
| strings in the `Level` trailer | **365** over 73 maps |
| bytes after them | 2335 |
| actors a `Level` names that the writer does not write | **0** |
| native terrain record bytes after a `TerrainInfo` block | **12543159** |
| -- exports whose record ends on a vertex-colour array | **66** of 66 |
| -- vertex colours inside them | **313344** |
| terrain records read member by member | **66** of 66 |
| -- sector references, all naming a `TerrainSector` | **765** (765) |
| -- vertex positions (12 bytes each) | **313344** |
| -- per-vertex records (24 bytes each) | **313344** |
| -- records whose tail ints are the sector list's furthest edge | **66** of 66 |
| `TerrainSector` payloads read | **765** |
| -- naming their `TerrainInfo` | **765** |
| -- quads they cover | **194114** |

| struct | properties | decoded | tagged | native | sizes (bytes:instances) | fields |
|---|---:|---:|---:|---:|---|---|
| `Vector` | 152289 | 152289 | 0 | 152289 | 12:152289 | `--` |
| `PointRegion` | 79991 | 79991 | 79991 | 0 | 13:64084 / 14:15907 | `Zone:Object+iLeaf:Int+ZoneNumber:Byte` |
| `Rotator` | 50528 | 50528 | 0 | 50528 | 12:50528 | `--` |
| `Scale` | 17558 | 17558 | 17558 | 0 | 25:17550 / 28:8 | `Scale:Struct<Vector>+SheerRate:Float+SheerAxis:Byte` |
| `RangeVector` | 10078 | 10078 | 10078 | 0 | 52:9410 / 55:614 / 54:30 / 61:13 / 64:8 / 62:3 | `X:Struct<Range>+Y:Struct<Range>+Z:Struct<Range>` |
| `Range` | 4149 | 4149 | 4149 | 0 | 13:4123 / 15:26 | `Min:Float+Max:Float` |
| `Color` | 523 | 523 | 0 | 523 | 4:523 | `--` |
| `TerrainLayer` | 243 | 243 | 243 | 0 | 214:70 / 215:52 / 211:26 / 227:20 / 226:19 / 206:16 / 213:13 / 219:8 / 225:7 / 210:6 / 223:3 / 212:2 / 222:1 | `Texture:Object+AlphaMap:Object+UScale:Float+VScale:Float+UPan:Float+VPan:Float+TextureMapAxis:Byte+TextureRotation:Float+LayerRotation:Struct<Rotator>+TerrainMatrix:Struct<Matrix>+KFriction:Float+KRestitution:Float` |
| `Orientation` | 43 | 43 | 43 | 0 | 106:29 / 122:7 / 123:7 | `CamOrientation:Byte+LookAt:Object+DollyWith:Object+EaseIntime:Float+bReversePitch:Int+bReverseYaw:Int+bReverseRoll:Int+LookAtActor:Object+LookAtActorTag:Name+LookAtBoneName:Name+LookAtOffset_Relative:Struct<Vector>+LookAtOffset_Absolute:Struct<Vector>+PctInStart:Float+PctInEnd:Float+PctInDuration:Float+StartingRotation:Struct<Rotator>` |
| `Plane` | 41 | 41 | 41 | 0 | 26:29 / 29:12 | `W:Float+X:Float+Y:Float+Z:Float` |
| `Matrix` | 10 | 10 | 10 | 0 | 129:10 | `XPlane:Struct<Plane>+YPlane:Struct<Plane>+ZPlane:Struct<Plane>+WPlane:Struct<Plane>` |
| `InterpCurve` | 9 | 0 | 0 | 0 | 5:9 | `Points:Array` |

| array element type | declared on | arrays | records | tagged arrays | element widths (bytes:records) | payload fields |
|---|---|---:|---:|---:|---|---|
| `ParticleColorScale` | `ParticleEmitter` | 2472 | 5746 | 2472 | 14:5095 / 17:411 / 15:213 / 16:27 | `RelativeTime:Float+Color:Struct<Color>` |
| `ParticleTimeScale` | `ParticleEmitter` | 1330 | 2000 | 1330 | 34:877 / 31:434 / 35:429 / 32:147 / 33:113 | `RelativeTime:Float+RelativeSize:Float+UseXYZ:Bool+RelativeSizeXYZ:Struct<Vector>` |
| `SoundEmitter` | `AmbientSound` | 486 | 823 | 486 | 38:551 / 37:233 / 41:31 / 40:8 | `EmitInterval:Float+EmitVariance:Float+Volume:Struct<Range>+EmitSound:Object` |
| `int` | `TerrainInfo` | 132 | 12480 | 0 | 4:132 (arrays) | `--` |
| `IMusicSegment` | `IMusicActor` | 81 | 8220 | 81 | 83:1336 / 63:1336 / 61:1282 / 80:1277 / 87:908 / 66:906 / 59:506 / 77:448 / 68:113 / 84:56 / 155:21 / 281:12 / 41:8 / 81:3 / 39:2 / 40:2 / 70:2 / 47:1 / 49:1 | `SegmentName:Str+State:Byte+TypeStart:Bool+TypeEnd:Bool+TypeLoop:Bool+Transition:Array+TransitionFlags:Int+TestedValue:Int` |
| `ParticleSound` | `ParticleEmitter` | 58 | 73 | 58 | 85:70 / 84:3 | `Sound:Object+Radius:Struct<Range>+Pitch:Struct<Range>+Weight:Int+Volume:Struct<Range>+Probability:Struct<Range>` |
| `ParticleBeamEndPoint` | `BeamEmitter` | 52 | 93 | 52 | 74:77 / 75:8 / 78:5 / 73:2 / 83:1 | `ActorTag:Name+offset:Struct<RangeVector>+Weight:Float+ActorRef:Object` |
| `ParticleVelocityScale` | `ParticleEmitter` | 36 | 114 | 36 | 23:63 / 24:51 | `RelativeTime:Float+RelativeVelocity:Struct<Vector>` |
| `int` | `FluidSurfaceInfo` | 12 | 2017 | 0 | 4:12 (arrays) | `--` |
| `DecorationLayer` | `TerrainInfo` | 8 | 10 | 8 | 653:4 / 657:3 / 659:1 / 655:1 / 684:1 | `ShowOnTerrain:Int+ScaleMap:Object+DensityMap:Object+ColorMap:Object+StaticMesh:Object+FadeoutRadius:Struct<Range>+BillboardFadeinRadius:Struct<Range>+BillboardHardCullDist:Float+BillboardTexture:Object+RedColorVariance:Byte+GreenColorVariance:Byte+BlueColorVariance:Byte+BillboardAlphaFadeEnabled:Bool+BillboardAlphaRef:Byte+BillboardUpdateThreshold:Float+BillboardMorphHorizontal:Float+BillboardMorphVertical:Float+ScaleMultiplier:Struct<RangeVector>+MeshExtents:Struct<Range>+swayRange:Struct<Range>+swaySpeed:Struct<Range>+DensityMultiplier:Struct<Range>+MaxPerQuad:Int+Seed:Int+AlignToTerrain:Int+DrawOrder:Byte+ShowOnInvisibleTerrain:Int+LitDirectional:Int+DisregardTerrainLighting:Int+RandomYaw:Int+EnableCollision:Bool+CollisionRadius:Float+CollisionHeight:Float+swayRotators:Struct<FTreeSwayRotation>+swayRotators:Struct<FTreeSwayRotation>+swayRotators:Struct<FTreeSwayRotation>` |
| `Plane` | `ParticleEmitter` | 8 | 8 | 8 | 26:7 / 29:1 | `W:Float+X:Float+Y:Float+Z:Float` |
| `ParticleRevolutionScale` | `ParticleEmitter` | 4 | 8 | 4 | 23:6 / 24:2 | `RelativeTime:Float+RelativeRevolution:Struct<Vector>` |
<!-- /generated:t3d-values -->

**The refused fifth is a struct array, and its element width is the payload's remainder over its count.** 4,528
of the 4,656 refusals divide exactly into 14,590 records; the other 128 do not divide and stay a commented
one-liner. That is the shape `SizeScale`, `ColorScale`, `SoundEmitters` and `BeamEndPoints` have, and it is where
a struct array stops: the element's type lives in the class's `ArrayProperty` declaration, and this reader carries
a map's values, not its class declarations.

**A struct's width is a property of the instance, not of the name.** `PointRegion` is 13 bytes on 63,992
properties and **14 on 15,875**; `Scale` 25 on 17,550 and 28 on 8; `Orientation` 106, 122 and 123. So no decoder
may key on a struct's name alone, and `prop-stream.py`'s four fixed-size decoders are right only because each
also tests the size. It is also why "decode the structs" is not a one-line change: **111,756 of 314,940** struct
properties sit at a size their name does not predict.

**The coverage table was stale by one reader fix, and regenerating it moved 2 of the 73 rows, not 73.** The
committed `inventory/t3d_coverage.csv` predated the name-table fix in `docs/PACKAGE_READER.md`: `M17D` and `M21A`
now locate **169 more actors** and **2,808 more properties**, so the totals were **121,394 actors / 1,180,946
properties / 3 unresolved references** and the T3D 49,211,838 bytes, because an array is now one line per
element rather than one line of bytes. A second run reproduces the table byte for byte. *(The property total is
superseded by the writer's own locator, measured at the end of this page: 6,511 of those properties were a parse
that started inside a value.)*

## The element type is a declaration, and the declaration is in the class (measured 2026-11-19)

The sentence this page ended on said the element's type "lives in the class's `ArrayProperty` declaration, and
this reader carries a map's values, not its class declarations". The declaration was already committed, in
`inventory/class_properties.csv` (`array<ParticleColorScale>` as the property's friendly type), and a placed
class inherits it: `SpriteEmitter.ColorScale` is declared on `ParticleEmitter`, so the join walks the super
chain in `inventory/classes.csv`. It names the element type of **4,501 of the 4,528** refused struct arrays
(99.4%, 14,563 of their 14,590 records), and the residue is named rather than left as a mystery: **27** refused
arrays whose property their class chain does not declare as an array -- `PathNode.Vector` (13 arrays),
`Light.PrePivot`, `Light.Filename`, `Light.Distance`, `Light.LightSaturation`, `Light.DrawScale3D`,
`Light.State`, `SoundTrigger.ZoneNumber`, `MovementMarker.ColLocation`, `PathNode.Tag` -- which are *not* arrays
at all but tags a mislocated block start decodes as one; and the **128** payloads that divide by nothing at all.

**Typed is not decoded, and the width is why.** The element's *layout* is not in either artifact: a struct
array's element width is the payload's remainder over its count, and it varies within one type
(`ParticleColorScale` 14 bytes in 2,156 arrays and 17 in 203; `ParticleTimeScale` 34 in 656 and 31 in 220),
because compact indices inside the element are one or two bytes. So the writer decodes nothing it cannot fix
the width of: an element type whose width is a fact about the type (`int`, `float`, `byte`, `bool`, `Vector`,
`Rotator`, `Color`) is decoded when the payload's width is that type's, and **2,019 records** are: **2,017
`int`** in 12 `array<int>` properties (`FluidSurfaceInfo`) and 2 `float`. Every other typed array is emitted
as `// SizeScale: 2 element(s) of 17 byte(s) of ParticleTimeScale` -- a comment with a name an editor can look
up, not a name invented for the bytes.

The per-element-type census is `inventory/t3d_arrays.csv` (14 rows, written by `make t3d`, arithmetic against
`t3d_values.csv` under `--check`), and the table below is rendered from it. *(The width question this paragraph
stops on is answered at the end of this page: the element is a tagged block, so there is no width to fix.)*

**Not in this PR.** The struct decoders beyond the three whose layout is established, the element layouts of
the five typed struct arrays, and the `Level` export's script sequence. *(The first of those is settled two
sections down -- a struct's value is a tagged block, so there are no per-struct decoders to write.)*

## The writer takes the reader's locator, and the struct census collapses (measured 2026-11-19)

`locate()` used to carry a locator of its own: the start whose block spends every object reference in the
package's tables, then the most properties. `prop_stream.find_block` now runs a stronger test -- every
checkable value consumes exactly the `Size` its tag declares -- and that is the test a writer needs, because a
parse that begins inside a struct payload still ends at the export's end and still resolves every name. So the
writer delegates: one locator, one answer, and `value-layouts.csv`'s relocation count and this writer's start
are the same rule rather than two that agree by luck.

The prefix is pinned to the reader's 64-byte window, and that is a measurement rather than a preference.
Unpinned, `find_block` will look one window further when nothing inside the first decodes -- right for a class
header, wrong for a map: the wider look locates **490 more exports** than the old rule did, **every one of them
a `StaticMeshInstance`**, at starts 65-95, each a parse of two tag-shaped bytes inside a packed transform
record. No map actor's block starts later than **32** bytes into its export (`inventory/actor_prefix_census.csv`:
`Cubemap` 32, `StaticMeshInstance` 30, `TerrainSector` 21, every other class 17 or less), so the pin costs
nothing and stops the writer from inventing an actor out of a native payload.

| | before | after |
|---|---:|---:|
| actors written | 121,394 | 121,394 |
| properties | 1,180,946 | **1,174,435** |
| block starts that move | -- | **4,133** of 121,394 |
| unresolved references | 0 | 0 |
| T3D bytes | 49,206,276 | 49,538,664 |
| `Array` properties | 37,889 | 38,739 |
| -- object arrays | 32,586 | **34,083** |
| -- references inside them | 117,398 | **126,356** |
| arrays refused a shape | 5,303 | 4,656 |
| struct property names | 79 | **12** |

Tags *fall* by 6,511 because a parse inside a value invents properties that are not there, and the file grows
0.7% anyway: the properties that survive are whole ones, so 1,497 more arrays decode as object arrays and
8,958 more element references are printed.

**The 79 struct names were 67 words that are not types.** Of the twelve names the corpus now finds, **eleven
are `UStructProperty` friendly types** declared in `inventory/class_properties.csv` (`Vector`, `PointRegion`,
`Rotator`, `Scale`, `RangeVector`, `Range`, `Color`, `Orientation`, `Plane`, `Matrix`, `InterpCurve`). The old
census's remaining names were actor instance names (`StaticMeshActor448`, `ACTION_Talk1`), property names
(`ColLocation`), and bare `X`/`Y`/`Z` -- words that had been sitting at the name index the tag's struct field
points at, because the parse had begun inside a value and was reading the value's bytes as a tag.

**The one residue is named, and its export has no property block at all.** The residue row is a single property,
and it is `M07A`'s export **#2149** (`Cubemap`, 57 bytes): two of the starts in its prefix walk to the export's
end -- 32 with two properties, 51 with one -- so the locator takes the longer, and that block reads a 16-byte
`Struct` named `PhysicsVolume` and a 1-byte `FixedArray` named `TypeEnd`. `Engine.Cubemap` declares two
properties, `CubemapRenderInterface` (`UIntProperty`) and `Faces` (`UObjectProperty`), and neither is a struct
-- so those 57 bytes are not a tagged block, and the best parse available still names a type the corpus does not
have. `--check` now **fails** on any struct row that is neither a declared type nor that residue at its measured
property count, so the gate bites on the 79-name reading and on any growth of the residue.

**Every census that reads a block start inherits the fix, so they are all re-run here.** The map-side
artifacts are one story about one locator, and the movement is largest where a misparse had cut an array in
half:

| artifact | before | after |
|---|---|---|
| `action_wiring.csv` orphan sequence references | 4,872 | **36** |
| `action_wiring.csv` actions no array names | 1,624 in 49 maps | **12 in 2 maps** |
| `event_wiring.csv` fires with no declared event | 8 | **0** |
| `event_targets.csv` sequences carrying a tag | 5,384 | 5,874 |
| `action_parameters.csv` refused placements | 24 | **12** |
| `map_name_values.csv` malformed name values | 116 | **2** |
| `squad_scripts.csv` rows / spawners | 630 / 719 | **632 / 721** |

The order the engine runs is the thing the old start was breaking: a sequence's `Actions` array is a compact
count followed by compact indices, and a block located four bytes inside that array read the count as a tag and
lost every index after it. The arrays hold **1,820 more references** and name **1,612 more actions**.

**Not in this PR.** The struct decoders beyond the three whose layout is established; the `StaticMeshInstance`
payload's own record format; and the `Level` export's script sequence. *(The 27 refused arrays this paragraph
called "not fixed" are closed at the end of this page: they were starts inside the prefix record.)*


## The prefix record names the start, and the last residue is a tail byte (measured 2026-11-19)

**The start rule the page kept asking for.** `find_block` located a block by its *values*: a start was right
when every checkable value consumed exactly the tag's `Size` and the block ended at the export's end. That test
has a blind spot -- a block with nothing to check (`seen == 0`) falls through to the property count, and a parse
that begins inside the actor's own prefix record satisfies both -- and the previous section left the 27 arrays
it produced as a start rule, not a value rule. The prefix is a record:
`[class index][the same index again][8 x 0xff][4 bytes][0x81]`, so `2*w + 13` bytes with `w` the class index's
encoded width (`tools/actor-prefix-record.py`), and the locator now reads it first. When the bytes *are* the
record its end is the start; when they are not, the value test decides as before.

**What that moves.** 79,991 of these 194,190 exports carry the record. The old locator's start was the
record's end on 54,348 of them and **inside the record's tail on 25,499**; those are the parses that read one to
three bytes of the record as a property tag. Properties fall **1,174,435 -> 1,148,994** because the property
that goes is the invented one -- each moved block's parse is the old one minus its head -- and no export is
lost (`unresolved refs` stays 0). The height of the movement is per class: `PathNode` (whose chain declares no
properties of its own, so nothing but the start could say a `Vector` tag was a parse), `Light`, `ZoneInfo`,
`PlayerStart`.

**The two `Cubemap`s.** Both 57-byte payloads hold a nine-property block -- `Faces[0..5]`, `Palette`,
`InternalTime[0..1]` -- terminated by `None` at 56, and the export carries **one zero byte after it**. The
exact-end rule refused the only real start, so `M07A` #2149 was written from a parse three properties into the
payload (the `PhysicsVolume`/`TypeEnd` pair, and the `Tag` struct row) and `M07B` #2717 was not written at all.
`prop-stream.padded_block` reads a block at the export's first byte when it ends at `None` with at most one
zero byte after it, and `locate()` passes `pad_tail=1`; it is consulted only when no exact-end block passes
the value test, so it cannot displace one. Two exports of 194,190 have such a tail and both are these, which is
what `t3d_coverage.csv`'s `padded_blocks` column pins (the gate fails on a third, or on either map losing it).

**The residue the struct gate used to allow is gone.** `t3d_structs.csv` loses its `Tag` row -- 12 names to
**11, every one a declared `UStructProperty` type** -- and `t3d_arrays.csv` loses **both** `*(not declared)*`
rows (16 rows to 14). Those two rows were the 27 refused arrays the previous section named and left
(`PathNode.Vector` 13, `Light.PrePivot`, `Light.Filename`, ..., 15 under `Actor` and 12 with no declaring
class): every one was a tag a start inside the prefix read as an array, so the element type they were missing
was never missing. Actors **121,394 -> 121,395** (`M07B`'s `Cubemap` is written for the first time).

**Every census that reads a block start inherits it, so all of them were re-run** (11 targets, ~90 s each, no
boot). The order the engine runs is what a start inside the record was breaking, and the residual wiring
orphans close:

| artifact | before | after |
|---|---|---|
| `action_wiring.csv` orphan sequence references | 36 | **0** |
| `action_wiring.csv` actions no array names | 12 in 2 maps | **0** |
| `event_wiring.csv` waits with no matching fire | 332 | re-measured |
| `action_parameters.csv` runat/immediate split | 60 silent of 455 | re-measured |

`tools/prop-stream.py` (the `preferred`/`record_class`/`pad_tail` start rule and `padded_block`) +
`tools/map-to-t3d.py` (the delegation, `padded_blocks`/`record_starts` in the coverage census, the tail gate
and three self-tests) + the eight map-side tools that now hand the export's class to the locator +
`inventory/t3d_{coverage,values,structs,arrays}.csv` + the regenerated map-side artifacts and their pages.

**Not in this PR.** The `Level` export's script sequence. *(The other three are settled at the end of this
page: the `StaticMeshInstance` payload's record format, the struct decoders beyond `Vector`/`Rotator`/`Color`,
and the arrays that divide by nothing.)*

## A struct's value is a tagged block, and the palette is the last opaque payload (measured 2026-11-19)

**The struct decoders the page kept deferring were never separate work.** `0x000795f0` skips the tagged walk
for exactly three struct names (`Vector`, `Rotator`, `Color`, by the GNames indices it compares), and every
other struct serialises as **a property block of its own fields** -- name index, tag, value, `None` -- so the
field names, their order and their types are in the payload rather than only in the class declaration. Reading
them needs no per-struct decoder: `prop_stream.struct_fields` runs the reader it already had over the value and
requires two things of the parse, the same two the locator's value test uses -- it ends at `None` on the value's
last byte, and `tag-encode` re-encodes the same tags.

| struct | properties | decoded | how | field list (the payload's own) |
|---|---:|---:|---|---|
| `PointRegion` | 79,925 | 79,925 | tagged | `Zone:Object, iLeaf:Int, ZoneNumber:Byte` |
| `Scale` | 17,558 | 17,558 | tagged | `Scale:Vector, SheerRate:Float, SheerAxis:Byte` |
| `RangeVector` | 10,078 | 10,078 | tagged | `X:Range, Y:Range, Z:Range` |
| `Range` | 4,149 | 4,149 | tagged | `Min:Float, Max:Float` |
| `Orientation` | 43 | 43 | tagged | 16 fields, `CamOrientation` through `StartingRotation:Rotator` |
| `Plane` | 41 | 41 | tagged | `W:Float, X:Float, Y:Float, Z:Float` |
| `Matrix` | 10 | 10 | tagged | `XPlane`, `YPlane`, `ZPlane`, `WPlane`, each a `Plane` |
| `InterpCurve` | 9 | **0** | -- | `Points:Array` |

Decoded struct properties go **203,200 -> 315,004 of 315,013** (99.997%), and the nine that do not decode are
one name: `InterpCurve`'s only field is an `Array`, which a tuple cannot carry, so the writer refuses the whole
struct rather than printing a hole. The width is still a property of the instance -- `PointRegion` is 13 bytes
on 64,022 and 14 on 15,903, `Scale` 25 and 28, `RangeVector` six widths -- and that is the field, not the name:
the first field is a compact object index, so its own encoded width moves the record by one byte.

**Two corrections follow from reading the fields.** The writer printed `Int` and `Byte` values quoted
(`iLeaf="-1"`), which a struct made obvious: a number beside a quoted name in the same tuple. It now writes
`iLeaf=-1`. And struct values that no rule reached used to print as the empty string `""` -- 111,804 properties
carried nothing at all -- because `fmt(None)` is `""`; every one of them is now a field tuple.

**The `StaticMeshInstance` payload is a colour table, and it is not an actor.** 33,509 of the 194,190 exports
have that class, it never carries a property block, and the record is
`[0x00][compact count][count x FColor]` followed by the instance's own native tail:

| measure | value |
|---|---:|
| payloads | **33,509** |
| colours inside them | **7,112,701** |
| records whose alpha is zero | 5,643 |
| payloads the rule refuses | **0** |
| native bytes after the palette | 17,183,118 |

The fourth byte of each record is the colour's alpha and in this corpus it is only ever `0xff` or `0x00`:
33,462 payloads are opaque throughout and 47 go clear after exactly **84** records (`CTF-Abydos` #3539,
`M07C` #2392, `M07D` #1459 and `M07B` #3609 all break at the same index), which is why the test is
`{0x00, 0xff}` and not `0xff`. Nothing in a map references one of these exports -- the read finds zero object
references to them in `M07A`'s 445 -- so they are the level's baked per-mesh colours, saved beside the actors.

**211 of them used to be written as actors.** The prefix record's end lands inside a palette, and two
tag-shaped bytes there pass the value test, so the writer placed a two-property actor for each: naming the
palette rule removes them, and the actor total falls **121,395 -> 121,184** with properties
**1,148,994 -> 1,148,526**. The locator still counts those 211 exports as having a block
(`inventory/actor_property_coverage.csv`'s 121,394 is a locator count, not an actor count); the palette rule is
what says they are not actors.

**The arrays that divide by nothing have a declaration, not a width.** `LevelInfo.LoadLipSincWithPrefix` is
`array<string>`, so each element carries its own length prefix (which counts the terminating NUL) and the
payload's remainder over its count is arithmetic on nothing. The declaration is asked **first**, and it has to
be: a payload of two equally long strings divides by its count (`\x02\x04abc\x00\x04def\x00` is 5 bytes per
element), so a divisibility test alone reads an `array<string>` as a struct array. **25 arrays / 126 elements**
decode as strings this way. The rest of the refusal population was **46 arrays** that neither rule reached;
those are decoded at the end of this page (a tagged element has no width to divide), and
the compact-index hypothesis is refuted rather than pending: `ZoneInfo.ManualExcludes` -- the shape the earlier
reading called "eight indices in 14 bytes", which is what a compact element would look like -- is an ordinary
object array once the start is the prefix record's end, and **0** arrays in the corpus are arrays of compact
indices. The writer still carries that rule, with a synthetic test, so the zero is a measurement and not a gap.

**Files.** `tools/prop-stream.py` (`struct_fields`, the `native_width`/`depth` threading, and the struct branch
of `read_value`), `tools/map-to-t3d.py` (`fmt_struct`, `struct_shape`, `decode_compact_array`,
`decode_string_array`, `palette_record`, the two census writers and their gates, the number spelling in `fmt`),
`inventory/t3d_{coverage,values,structs,arrays}.csv` (new columns: `struct_tagged`/`struct_native`,
`compact_*`/`string_*`, `palette_*`, and a `fields` column per struct name), `docs/T3D.md` and this note.

**Not in this PR.** The `Level` export's script sequence; the `StaticMeshInstance` record's **tail** (17,183,118
bytes, 2.4 bytes per colour, not decoded -- the palette boundary is measured, what follows it is not); the
element layouts of the five typed struct arrays; and the 46 arrays neither the width nor the declaration rule
reaches.

## An array element is a tagged block, and the palette's tail begins with a discriminator (measured 2026-11-19)

**The element layouts the page kept deferring do not exist.** The previous section left 4,501 typed struct
arrays as `N element(s) of W byte(s)` comments because a struct's width is not a fact about its name: the same
type is 14 bytes on one array and 17 on the next. That is the *element's* width, and the element is a tagged
property block like every other struct value -- so each record carries its own field names, their order and its
own end. `decode_struct_elements` applies the struct rule (`prop_stream.struct_fields`, which already requires a
block to end at `None` on its last byte and its tags to re-encode) to each element of the array's count, and the
array has to consume itself exactly.

| | before | after |
|---|---:|---:|
| struct arrays decoded | 4,501 of 4,547 (2,019 records by width) | **4,547 of 4,547 (17,095 records as tagged blocks, 2,019 by width)** |
| arrays no rule reaches | 46 | **0** |
| records inside the struct arrays | 14,563 | **19,114** |

**The 46 are the same shape, and they were the widest elements.** They are **36 `array<IMusicSegment>`** (45
arrays -> 81, 3,695 -> 8,220 records) and **10 `array<SoundEmitter>`** (476 -> 486 arrays, 797 -> 823 records):
both types carry a `Str` field or a long name, so no element width divides their payloads per array -- the width
rule could only have read them by accident. Their payload field lists are the definitions: `IMusicSegment` is
`SegmentName:Str, State:Byte, TypeStart/TypeEnd/TypeLoop:Bool, Transition:Array, TransitionFlags:Int,
TestedValue:Int`, and one field is an array, so the writer prints those elements as their shape rather than as a
tuple with a hole (3,695 of the 17,095 records, and the only ones not printable).

**Two censuses agree on a struct that has both shapes.** `Plane` is a struct property 41 times *and* an array
element 8 times, and both rows read the same four fields (`W:Float+X:Float+Y:Float+Z:Float`), from two
independent passes over the payloads -- the struct census at the value, the array census at the element. The
element widths also reproduce the width census they replace (the array counts there were divided per array; here
they are counted per record): `ParticleColorScale` 14 bytes on 5,095 records and 17 on 411, `ParticleTimeScale`
34/31/35/32/33 over 2,000 records, `SoundEmitter` 38/37/41/40 over 823.

**The palette's tail starts with a discriminator, and it is not a tagged block.** *(The last section reads the
serialiser: the "discriminator" is the colour record's last member, written after the colours, and the byte
after it is the first record of the array.)* All 33,509 payloads carry
bytes after the last colour (17,183,118, 2.4 per colour), and the first four of them are a dword in **2..10** on
every one: 30,450 read 2, then 899 read 4, 728 read 10, 620 read 6, 606 read 3, 132 read 7, 41 read 9, 28 read 8
and 5 read 5. The dword is not a count -- it does not divide the remainder -- and reading the tail as a tagged
block refuses on all 33,509, while reading it as a *sequence* of blocks "succeeds" on 896 with every field typed
`Invalid`, i.e. on tags the type table does not have. So the colour table's boundary is measured and the record
after it is a native structure whose first field is a small enum; naming its fields is a read of the class's
serialiser in the XBE, not another parse of these bytes. `inventory/t3d_coverage.csv` carries the two counts per
map (`palette_tails`, `palette_head2`) and `--check` holds them to the payload total.

**Not in this PR.** *(Both are in the next section.)*


## The level's actor list and the palette's record list (measured 2026-11-19)

Both items this page kept deferring are native records, so both were read from the functions that write them
rather than from the bytes. `inventory/class_serialize_slots.csv` carries each class's slot-11 `Serialize`
override; the two that matter here are `Level` `0x00161b70` and `StaticMeshInstance` `0x0024b5f0`.

**The `Level` export is the level's actor list, and there is no script to find.** Its payload is
`[0x00][u32 count][u32 count][count x compact export index][trailer]`: the leading zero is the empty property
block's `None` terminator, the two dwords are the same number, and every index lands in the package's own export
table. Across the 73 maps it names **79,063 actors** and a trailer this page reads at its end: five `FString`s and 32 bytes, not the "41 to 43 compact
values" an earlier reading counted here. The writer
never emitted this export -- it has no tagged block, so `locate()` correctly refuses it -- and it now reads the
list and cross-checks itself against it: of the actors a level names, 65 were not in any T3D and all 65 were
`TerrainInfo`, whose payload the last section of this page shows does carry a property block. Those 65 are
written now, so the residue is **0** and the gate pins that, and the T3D now carries the count in its header (`// Level: N actor(s) ...`).

**The palette's tail is a count of records, not a head plus fields.** `UStaticMeshInstance::Serialize` runs only
on load, writes the class's instance record (the `TArray<FColor>` that is the colour table, then one dword) and,
when the package version is at least `0x6e`, a `TArray` of 20-byte records through the array serialiser at
`0x0024e6e0` -- a compact count, then each member. So the bytes after the colours are
`[dword][compact count][count records]`, each record ending with a dword that is 0 or 1: **466,858 records** over
the 33,509 payloads, and the frame spends the tail exactly on all of them. The record body is pinned at the end of
this page: `[compact export index][compact length][bytes][dword 0 or 1]`, read from the serialiser's three
members, which closes every payload -- the 790 that did not close on the earlier reading were records ending on 0.

**Files.** `tools/map-to-t3d.py` (`level_record`, `smi_tail`, the writer's `Level` branch and its header line,
five columns on the coverage census, `LEVEL_UNWRITTEN`, the gates and six synthetic cases),
`inventory/t3d_coverage.csv` (five new columns, 73 rows regenerated), the writer's own T3D under `$WORK`, and
this page's generated block.

## The records name lights, the trailer is strings, and the terrain actor was written (measured 2026-11-19)

Three residues this page carried were one read each, and none of them was the measurement the page predicted.

**The palette record's body is `[compact export index][compact length][bytes][dword flag]`, and the flag is 0 as
often as 1.** The 790 payloads the earlier reading left over were not a second body shape: their records end on
the dword **0**, not 1. `FUN_0024e6e0` -- the record array `UStaticMeshInstance::Serialize` hands `this+0x28` to
-- preloads `count * 0x14` and writes the element's three members in order: `+0` through the archive's compact
slot at `+0x18` (`FUN_000637e0`, the `FCompactIndex` writer, called with the member's own address), `+4` as a
`TArray<BYTE>` (`FUN_0001aed0`: preload, compact count, then `Ar.Serialize(data, count)` through the raw slot at
`+4`), and `+0x10` as a raw dword (`FUN_00037110`, slot `+4` with length 4). Those three members are the 20 bytes
the preload sizes -- `4 + 12 + 4` -- so the element's field widths are the archive's serialisation overloads and
not a payload convention, which is what U31 asked for. Accepting both flag values closes **33,509 of 33,509**
payloads: **466,858 records**, **10,582** of them carrying 0 (every one of those sits in `DM-Tomb` and its
neighbours, which is why the earlier miss counts clustered there).

**The first field is an actor reference, and the actor is a light.** All **466,858** values land in the map's own
export table and **466,435 (99.91%)** name a light class -- `Light` 404,667, `Sunlight` 35,210, `Spotlight`
25,998, `Emitter` 531, `TriggerLight` 29 -- while 423 name an `InterpolationPoint`, `PathNode` or `LookTarget`.
So a `StaticMeshInstance` export is the level's baked per-light lighting: the colour table above it, then one
record per (light, instance) pair carrying a compressed byte blob and a flag. The gate holds the reference count
to the record count and the non-light share under one percent, and the flag-0 count is published beside them
rather than left as a residue.

**The `Level` trailer is five strings, not a run of compact values.** After the actor list the payload carries
`07 "unreal"`, `00`, `0a "Index.unr"`, `00`, `00` -- five length-prefixed `FString`s whose lengths count the
terminating NUL, the same five in all 73 maps -- and then **32 bytes** (31 in `DM-WorstCaseScenario`, the one
map whose Level payload is a byte shorter), whose first eight are `61 1e 00 00 01 00 00 00` and whose last six
differ per map. The earlier "41 to 43 compact values" was those ASCII bytes read as compact indices. The census
carries `level_strings` (365 = 5 x 73) and `level_tail_bytes` (2,335) and the gate holds every map to five.

**A `TerrainInfo`'s payload is `[17-byte actor prefix][property block][native terrain record]`, so the class the
page called blockless has a block.** `actor-prefix-record.py`'s rule matches on **66 of 66** exports and names
the block's start, and the block parses to `TerrainMap:Object`, `TerrainScale:Struct<Vector>`,
`Layers:Struct<TerrainLayer>` (three or four of them), `QuadVisibilityBitmap:Array`, `EdgeTurnBitmap:Array`,
`Level:Object`, `Region:Struct<PointRegion>`, ... and terminates on `None` 258 to 4,850 bytes in, with 10 KB to
2.6 MB of terrain record after it. `find_block` requires a block to end at the export's end, so no candidate
existed and the class read as "no property block at all". `locate()` now takes the block at the prefix record's
end for the classes in `NATIVE_TAIL` when the value test accepts it, and `native_tails` (**66**) is that rule's
own control. The 65 actors the `Level` exports named and no T3D held are written: **121,184 -> 121,250 actors**
and **1,148,526 -> 1,149,471 properties**, `level_unwritten` is **0** where the gate pinned 65, and the two new
`TerrainInfo` array declarations put `int` (132 arrays, 12,480 elements) and `DecorationLayer` (8 arrays, 10
records) into the element-type census with their payload field lists.

**Files.** `tools/map-to-t3d.py` (`smi_tail`'s flag and reference census, `level_record`'s string trailer,
`locate`'s `NATIVE_TAIL` rule, `NATIVE_TAIL`/`NATIVE_TAIL_PIN`/`LEVEL_STRINGS`/`LIGHT_FAMILY`, ten new census
columns and the six gates and seven synthetic cases that hold them), `inventory/t3d_coverage.csv` (73 rows
regenerated), `inventory/t3d_{values,structs,arrays}.csv` (regenerated by the same run), `docs/T3D.md` and this
note.

**Not in this PR.** The palette tail's head dword (2 to 10, present on 30,450 of the 33,509 tails) and the 32
trailer bytes of the `Level` export are still counted, not named; both are fields of the class's instance record
or of whatever the level writes last, and neither has a serialiser read yet. The 1,415 `TerrainInfo` properties'
`Layers` struct widths (206..227 bytes, tagged) are decoded field by field, but the terrain record after the
block -- heights, sectors, the 10 KB to 2.6 MB -- is untouched.

## The terrain record ends on its vertex colours, and the two unnamed fields are one read each (measured 2026-11-19)

The three residues this page still counted were all fields of a serialiser, so this pass read the serialisers
instead of the payloads. `make ghidra` keeps its project (`tools/ghidra/run-serialize.sh`), and
`tools/ghidra/ghidra_scripts/ExportNew.java` -- added here, because `ExportAt.java` refuses an address that is
not already a function -- disassembles, creates and exports one. Five bodies were read: `UStaticMeshInstance`'s
`Serialize` (`0x0024b5f0`), its colour record (`0x001f7680`), the `TArray<FColor>` writer it shares with the
terrain (`0x001f7df0`), `ATerrainInfo::Serialize` (`0x00273e80`), and `ULevel::Serialize` (`0x00161b70`).

**The palette's "head" dword is the colour record's *last* member, written after the array.** `0x001f7680` is
`Ar << TArray<FColor>` and then `FUN_00037110(Ar, rec+0x18)` -- a `Serialize(ptr, 4)` through the archive's raw
slot. The array writer stores the count in the array itself (`arr+4`), so the dword after the colours is a
second, separate member: it is **not** the colour count on any of the 33,509 payloads, and it equals the record
count on 2,706 of them only where both are 2. It stays a small enum (2..10) with no reader located; what changed
is its position in the frame, which the previous section's "starts with a discriminator" got backwards.

**The `Level` trailer's five strings are the level's URL, and the rest is not written by this image.**
`ULevel::Serialize` (`0x00161b70`) decompiles to `Super::Serialize`, one 12-byte member at `this+0x94`, a
`FString`-shaped local, a dword at `this+0xf8`, sixteen dwords at `this+0x98`, and a `TArray` of 0x1c-byte
elements at `this+0xe0` -- none of which is the `[count][count][indices][five strings][32 bytes]` frame the
export carries. So the export was written by the editor that built the maps (not in `default.xbe`), and the
32 bytes will not fall to a body read; `docs/UNKNOWNS.md` U32 now says so rather than pointing at the wrong
function. What the strings *are* is settled by their values, identical in all 73 maps: `"unreal"`, `""`,
`"Index.unr"`, `""`, `""` -- the five `FString`s of an `FURL` (`Protocol`, `Host`, `Portal`, `Map`, `Op`) --
followed by the dword **7777** and the dword **1** on every map (the URL's port and its valid flag), then 24
bytes: 16 zero bytes on all 73, and six that vary, whose middle four read as a `float` in 0..12,282.

**The terrain record ends with the terrain's own vertex colours, so the class's tail is no longer untouched.**
`ATerrainInfo::Serialize`'s last member (for a package version above `0x74`) is
`FUN_001f7df0(Ar, this+0x1404)` -- **the same `TArray<FColor>` writer** the static-mesh colour table uses. So
the record's last member is `[compact count][4 bytes per vertex]`, and the count is a vertex grid:
**66 of 66** `TerrainInfo` exports close on it, **313,344** colours in total, in five grid sizes -- 16x16
(`256`, 5 exports), 32x32 (`1024`, 21), 64x64 (`4096`, 13), 128x128 (`16384`, 1) and 256x256 (`65536`, 3). The
writer prints the frame under each terrain actor (`// Terrain: 163,991 native byte(s), 16,384 vertex colour(s)`)
and `inventory/t3d_coverage.csv` carries `terrain_bytes`, `terrain_colours`, `terrain_colour_exports` and the
per-map grid list. Four gates hold it: every `TerrainInfo` closes, every count is a square, the set of grids is
exactly the five above, and no record is shorter than the colours it names.

**Files.** `tools/map-to-t3d.py` (`terrain_colours` and its five synthetic cases, the terrain comment line, the
four census columns, `TERRAIN_GRIDS`/`TERRAIN_COLOUR_EXPORTS` and four gates), `inventory/t3d_coverage.csv`,
`docs/T3D.md` (this section, and the palette sentence above corrected in place),
`tools/ghidra/ghidra_scripts/ExportNew.java`.

**Not in this PR.** ~~The terrain record's *body* between the property block and the colour array -- heights,
sectors, layers, the 10 KB to 2.6 MB -- is still counted only as `terrain_bytes`~~ (read member by member in
the next section); the palette dword's reader (what consumes `rec+0x18`) is not located; the `Level`
trailer's 24 bytes stay counted, not named.

## The terrain record's body is a member chain, and its sectors name it back (measured 2026-11-19)

The last section left "the terrain record's body between the property block and the colour array" as counted
bytes. `ATerrainInfo::Serialize` (`0x00273e80`, the same decompilation) writes that body as a short chain of
members, and the exported payloads close on it exactly:

| member | shape | what it is |
|---|---|---|
| sectors | compact count + that many compact references | 765 over 66 exports, **every one a `TerrainSector` export** |
| positions | compact count + 12 bytes each | one `FVector` per vertex: **313,344** |
| sector grid | two int32 | x and y of the sector grid; their product is the sector count on 66 of 66 |
| records | compact count + 24 bytes each | two triangle normals per vertex: **313,344** records |
| frame | 48 bytes | the terrain's grid-to-world frame, twelve floats (`FUN_0021f140`) |
| inverse | 48 bytes | the same frame inverted, twelve floats (`FUN_000541f0`) |
| grid | two int32 | the vertex grid's width and height -- the two ints `FUN_0026bda0` indexes the positions with |
| colours | compact count + 4 bytes each | the `TArray<FColor>` the previous section measured |

**The stretch after the record array is 48 + 48 + 8, not 80 + 24.** The first reading of this chain split it as
"two 40-byte members and a 24-byte tail", and both readings close on 66 of 66 exports, so only the code tells
them apart. It does, three ways: the two members sit at `this+0x1358` and `this+0x1388`, which are 0x30 = **48**
bytes apart; `FUN_0021f140`, the writer called once per member, is twelve four-byte writes (`param+0x00` through
`param+0x2c`), so its decompile reads `[param, param+4, param+8][param+0xc, param+0x10, param+0x14][param+0x18,
param+0x1c, param+0x20][param+0x24, param+0x28, param+0x2c]`; and the dwords the old reading called the tail's
`[0][0][0][float]` are the **fourth vector of the second frame** -- its `(0, 0, 1/scale-z)`. The tail proper is
the two dwords at `this+0x1334`/`this+0x1338`, written after the frames by two plain four-byte serializations,
and they are the terrain's grid: `FUN_0026bda0` addresses the position array with `[+0x1334] * row + col` and
sizes it to `[+0x1334] * [+0x1338]`. So `U33`'s "tail float" is not a tail member at all -- it is the inverse
frame's z scale, and it is `4.571` on `CTF-Abydos` because that map's forward z scale is `0.2188`.

**Three counts that are one number.** The positions, the 24-byte records and the vertex colours are each
**313,344** over the corpus, and `terrain_body` refuses a body whose counts disagree, so the record's three
per-vertex members are the same grid by measurement rather than by resemblance. The positions are the terrain's
vertex grid, not a byte count: they step regularly along both axes.

**A `TerrainSector` names its terrain, and its terrain's size counts its sectors.** The class has no property
block, so its payload is its native instance record: `[0x00][compact index of its TerrainInfo][quads x]
[quads y][origin x][origin y]`. All **765** sector payloads open this way, all 765 name the `TerrainInfo` in
their own map, and their quads are 8, 16 or 32 with the far edge one short (15 for 16, to avoid duplicating the
shared boundary) on origins that are multiples of eight. So the two records read each other: the terrain's
sector list names the sectors, and the terrain's grid dwords equal the furthest `origin + quads` in that list
plus one, on 66 of 66. The sectors cover **194,114** quads.

**Files.** `tools/map-to-t3d.py` (`terrain_body`, `terrain_span`, `sector_record`, `compact_width`, nine new
census columns, six new gates, ten synthetic cases, the T3D comment line under each terrain actor),
`inventory/t3d_coverage.csv` (73 rows regenerated), this section, `docs/UNKNOWNS.md` U33, the execution note in
`PLAN.md`.

**Gates.** `make ci` green; `--check` reads the committed census with no dump; the 73-map write reproduces its
own totals (121,250 actors, 1,149,471 properties, 54,957,213 T3D bytes); `--write-doc`/`--check-doc` agree.

**Not in this PR.** ~~The meaning of the two 40-byte members, of the 24-byte records' two vectors, and of the
tail's float~~ (named in the two sections below); the palette dword's reader and the `Level` trailer's 24 bytes
stay counted (`U32`).

## The two 48-byte members are a frame and its inverse (measured 2026-11-19)

`FUN_00264220` is what builds them, and it is the terrain's own record: it computes twelve floats from
`this+0x154`/`+0x158`/`+0x15c` (three offsets) and `this+0x3ec`/`+0x3f0`/`+0x3f4` (three scales), copies them
into `this+0x1358`, and copies `FUN_000541f0`'s answer -- the same twelve floats inverted -- into `this+5000`,
which is `0x1388`. The layout is one translation followed by three axis vectors carrying one scale each:

    [tx][ty][tz] [sx][0][0] [0][sy][0] [0][0][sz]

Each of the three axis vectors has a single non-zero component on **66 of 66** exports, and the two members are
each other's inverse on 66 of 66 by two products: `inverse[3]*frame[3] == inverse[7]*frame[7] ==
inverse[11]*frame[11] == 1`, and `inverse[0] == -frame[0]*frame[3]`, likewise for the y offset against
`frame[7]` and the z offset against `frame[11]`. `frame_inverse` is that test and it is a gate: a payload whose
second frame is not the first one's inverse fails the census.

**The frame is a grid-to-world map, and it is the one the vertex array is built with.** `FUN_0026bda0` walks the
grid, takes the height sample from `Engine_UnTerrain(col, row)`, subtracts the frame's translation and scales
each axis: `world.x = (grid.x - frame[0]) * frame[3]`, and the same for y and z, so the x and y scales are world
units per cell and the z scale is world units per height sample. The terrain's positions are exactly that
product, which is why the vertex grid's spacing equals `frame[3]`.

**The one map that carries two vertices per cell.** The spacing is the frame's own x scale on **65 of 66**
exports and **half** of it on `CTF-Abydos`'s 16,384-vertex terrain (`frame[3]` 448, spacing 224, `frame[0]`
28.42 -- `FUN_00264220` shifts the translation by `[+0x1334]/2` and `[+0x1338]/2` when `this+0x3e4` is set,
which is the doubled-resolution case). The grid dwords are a second, independent measurement: the vertex grid is
**twice** those dwords on **four** exports (`CTF-Abydos` 217 and 246, and both `LostCity` maps, whose 64x64 and
128x128 grids sit under 128x128 and 256x256 vertex arrays) and equal to them on the other 62,
`DM-WorstCaseScenario` included. So "the four largest terrains carry twice as many positions per sector vertex"
is not one rule: of the four the earlier section named, one halves the cell size (`CTF-Abydos` 217), two carry a
vertex grid twice the grid dwords (both `LostCity`), and `DM-WorstCaseScenario` does neither -- and a fourth
export (`CTF-Abydos` 246, an ordinary 4,096-colour terrain) carries the doubled vertex grid as well.

**Gates on the two.** `terrain_frames` is 66 (the inverse holds), `terrain_half_grid` is 4 (the grid dwords
against the vertex side), and `terrain_steps` is 65 x `1.00` and one `0.50` (the spacing against the frame's x
scale) -- three census columns, all read back by `--check` with no dump.

## The 24-byte record is two triangle normals (measured 2026-11-19)

`FUN_00277ec0` is the array writer: `Preload(count*0x18, max*0x18)`, then the compact count, then `FUN_00263c10`
per element, and `FUN_00263c10` is six four-byte writes at `param+0x00`..`param+0x14`. The element is two
`FVector`s, and `FUN_0026bda0` writes both: for the vertex at `(col, row)` it takes the three neighbouring
positions, takes their cross product (`FUN_001cb420`), normalises it, and stores it at element `index-1`'s first
vector and, from the other pair of neighbours, its second vector. A degenerate cross product is stored as
`(0, 0, 0)` rather than skipped. So each vertex carries the normal of **both triangles it belongs to** -- which
is why a flat terrain's records are two identical `(0, 0, 1)`s (`M02A`), why a rough one's differ (`CTF-Abydos`
has 60 distinct second vectors in its first 64 records), and why the first vector is a unit vector wherever it
is not zero.

**Files.** `tools/map-to-t3d.py` (`frame_inverse`, `vertex_step`, the three census columns, the T3D comment
line's frame scale, four synthetic cases), `tools/ghidra/ghidra_scripts/OffsetReaders.java` (the new script that
found the readers: every instruction whose displacement is a named instance offset, with its function),
`inventory/t3d_coverage.csv`, this section, `docs/UNKNOWNS.md` U33, the execution note in `PLAN.md`.

**Gates.** `make ci` green; `--check` reads the committed census with no dump; `--write-doc`/`--check-doc` agree;
`--check`'s synthetic cases cover a frame pair that is not an inverse, a frame paired with itself, a grid at the
frame's scale and at half of it, a grid whose spacing is uneven, and a zero scale.

**Not in this PR.** The palette dword's reader (what consumes `rec+0x18`, `U32`) and the `Level` trailer's 24
bytes (`U32`) stay counted, not named.
