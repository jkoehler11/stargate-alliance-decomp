
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

* The 820 unresolved references, and the 18 `?` squad scripts. They cluster rather than scatter, which points
  at references to imports this reader does not index, not at random corruption — but that is a hypothesis.
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

**The `ref:<index>` reading is stale.** `prop-stream.py` resolves an export index to `Class'Package.Name'` and a
negative one to the import's `Class'Package.Object'`; only three values in 1.18 million fall through, and each is
its own defect rather than a missing feature:

| map | property | value | what it suggests |
|---|---|---|---|
| `M02A` | `Vector` | `ref:-17179869183` | a struct-typed tag decoded as an object reference |
| `M15A` | `Max` | `ref:7470` | an export index past the map's export table |
| `M17B` | `StaticMesh` | `ref:5758` | same shape, different map |

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
| `Array` properties | 37889 |
| of those, object arrays | **32586** |
| object references inside them | **117398** |
| arrays refused a shape | 5303 (4510 struct arrays holding 14576 records) |
| struct-typed properties | 312295 |
| struct properties decoded to numbers | **203147** |
| struct property names | 79 |

| struct | properties | decoded | sizes (bytes:instances) |
|---|---:|---:|---|
| `Vector` | 152140 | 152108 | 12:152108 / 16:19 / 9:7 / 6:5 / 2:1 |
| `PointRegion` | 77025 | 0 | 13:61298 / 14:15726 / 16:1 |
| `Rotator` | 50518 | 50518 | 12:50518 |
| `Scale` | 17561 | 0 | 25:17550 / 28:8 / 16:1 / 1:1 / 4:1 |
| `RangeVector` | 10078 | 0 | 52:9410 / 55:614 / 54:30 / 61:13 / 64:8 / 62:3 |
| `Range` | 4138 | 0 | 13:4111 / 15:26 / 1:1 |
| `Color` | 521 | 521 | 4:521 |
| `None` | 57 | 0 | 2:26 / 0:18 / 4:8 / 1:4 / 12:1 |
| `Orientation` | 43 | 0 | 106:29 / 122:7 / 123:7 |
| `Plane` | 42 | 0 | 26:29 / 29:12 / 1:1 |
| `SheerRate` | 21 | 0 | 4:21 |
| `?` | 14 | 0 | 1:12 / 0:1 / 4:1 |
<!-- /generated:t3d-values -->

**The refused fifth is a struct array, and its element width is the payload's remainder over its count.** 4,510
of the 5,303 refusals divide exactly into 14,576 records; the other 793 do not divide and stay a commented
one-liner. That is the shape `SizeScale`, `ColorScale`, `SoundEmitters` and `BeamEndPoints` have, and it is where
a struct array stops: the element's type lives in the class's `ArrayProperty` declaration, and this reader carries
a map's values, not its class declarations.

**A struct's width is a property of the instance, not of the name.** `Vector` is 12 bytes on 152,108 properties
and **16 on 19**; `PointRegion` 13 on 61,298 and **14 on 15,726**; `Scale` 25 on 17,550 and 28 on 8;
`Orientation` 106, 122 and 123. So no decoder may key on a struct's name alone, and `prop-stream.py`'s four
fixed-size decoders are right only because each also tests the size. It is also why "decode the structs" is not a
one-line change: **109,148 of 312,295** struct properties sit at a size their name does not predict.

**The coverage table was stale by one reader fix, and regenerating it moved 2 of the 73 rows, not 73.** The
committed `inventory/t3d_coverage.csv` predated the name-table fix in `docs/PACKAGE_READER.md`: `M17D` and `M21A`
now locate **169 more actors** and **2,808 more properties**, so the totals are **121,394 actors / 1,180,949
properties / 3 unresolved references** and the T3D is 49,211,838 bytes, because an array is now one line per
element rather than one line of bytes. A second run reproduces the table byte for byte.

**Not in this PR.** The array element type (a class-declaration read), the struct decoders beyond the three whose
layout is established, and the `Level` export's script sequence.
