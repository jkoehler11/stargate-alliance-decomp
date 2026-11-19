
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
* The `Level` export's script sequence: the `ACTION_*` actors' parameters are visible, their order is not.

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
| `Array` properties | 38790 |
| of those, object arrays | **34218** |
| object references inside them | **127563** |
| arrays refused a shape | 4572 (4501 struct arrays holding 14563 records) |
| -- of the struct arrays, element type declared | **4501** |
| -- of those, elements decoded | **2019** |
| struct-typed properties | 315013 |
| struct properties decoded to numbers | **203200** |
| struct property names | 11 |

| struct | properties | decoded | sizes (bytes:instances) |
|---|---:|---:|---|
| `Vector` | 152152 | 152152 | 12:152152 |
| `PointRegion` | 79925 | 0 | 13:64022 / 14:15903 |
| `Rotator` | 50525 | 50525 | 12:50525 |
| `Scale` | 17558 | 0 | 25:17550 / 28:8 |
| `RangeVector` | 10078 | 0 | 52:9410 / 55:614 / 54:30 / 61:13 / 64:8 / 62:3 |
| `Range` | 4149 | 0 | 13:4123 / 15:26 |
| `Color` | 523 | 523 | 4:523 |
| `Orientation` | 43 | 0 | 106:29 / 122:7 / 123:7 |
| `Plane` | 41 | 0 | 26:29 / 29:12 |
| `Matrix` | 10 | 0 | 129:10 |
| `InterpCurve` | 9 | 0 | 5:9 |

| array element type | declared on | arrays | records | widths (bytes:arrays) |
|---|---|---:|---:|---|
| `ParticleColorScale` | `ParticleEmitter` | 2472 | 5746 | 14:2156 / 17:203 / 15:102 / 16:11 |
| `ParticleTimeScale` | `ParticleEmitter` | 1330 | 2000 | 34:656 / 35:281 / 31:220 / 33:102 / 32:71 |
| `SoundEmitter` | `AmbientSound` | 476 | 797 | 38:363 / 37:84 / 41:23 / 40:6 |
| `ParticleSound` | `ParticleEmitter` | 58 | 73 | 85:55 / 84:3 |
| `ParticleBeamEndPoint` | `BeamEmitter` | 52 | 93 | 74:40 / 75:5 / 78:4 / 73:2 / 83:1 |
| `IMusicSegment` | `IMusicActor` | 45 | 3695 | 73:21 / 41:8 / 68:8 / 59:4 / 39:1 / 47:1 / 75:1 / 49:1 |
| `ParticleVelocityScale` | `ParticleEmitter` | 36 | 114 | 23:21 / 24:15 |
| `int` | `FluidSurfaceInfo` | 12 | 2017 | 4:12 |
| `Plane` | `ParticleEmitter` | 8 | 8 | 26:7 / 29:1 |
| `ParticleRevolutionScale` | `ParticleEmitter` | 4 | 8 | 23:3 / 24:1 |
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

The per-element-type census is `inventory/t3d_arrays.csv` (16 rows, written by `make t3d`, arithmetic against
`t3d_values.csv` under `--check`), and the table below is rendered from it.

**Not in this PR.** The struct decoders beyond the three whose layout is established, the element layouts of
the five typed struct arrays, and the `Level` export's script sequence.

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

**Not in this PR.** The `StaticMeshInstance` payload's own record format (33,509 exports whose payload is a
packed colour table, not a tagged block); the `Level` export's script sequence; the struct decoders beyond
`Vector`/`Rotator`/`Color`; and the arrays that divide by nothing at all.
