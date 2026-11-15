# Actor properties: read at last

PLAN 5.2 wanted T3D for every map so mission structure could be indexed. The remaining blocker was the
property block — and with the layout confirmed against a public format reference, it parses: **121,225 of
194,190 exports across all 73 maps (62.4%) carry a readable property block**, and for actor classes it is
effectively complete.

    python3 tools/prop-stream.py DUMP/Maps/M02A.unr --class StaticMeshActor --scan
    python3 tools/prop-stream.py DUMP/Maps/M02A.unr --export 50 --values

## Coverage

`inventory/actor_property_coverage.csv` (per class) and `inventory/map_property_coverage.csv` (per map) hold
the numbers; the shape is what matters:

| class | exports | with a property block |
|---|---:|---:|
| `StaticMeshActor` | 32,193 | **32,191** |
| `ReachSpec` | 29,665 | **29,665** |
| `PathNode` | 8,600 | **8,600** |
| `StaticMeshInstance` | 33,509 | 211 |
| `Model` / `Polys` | 8,781 each | **0** |

The 38% without blocks are not failures: they are non-actor objects — models, polygons, mesh instances —
which have no UnrealScript properties. Every actor class lands at or within a handful of 100%.

## The properties are the actor baseline, independently

Across the corpus, by frequency:

| property | actors carrying it |
|---|---:|
| `Tag` | 80,679 |
| `PhysicsVolume` | 80,497 |
| `Location` | 78,971 |
| `Region` | 77,641 |
| `Level` | 77,374 |
| `Rotation` | 47,724 |
| `CollisionRadius` | 38,899 |
| `CollisionHeight` | 36,759 |
| `StaticMesh` | 32,934 |

`Tag`, `PhysicsVolume`, `Region`, `Level`, `Location`, `Rotation` — the same six that
`docs/systems/CHECKPOINTS.md` found in every saved actor, arriving here from a completely different direction
(actor properties in maps rather than properties in `.sgs` files). Two independent measurements of the same
engine baseline agreeing to the property is the strongest confirmation either has had.

## Why the block is not always at offset zero

A UE2 export does not necessarily start with its property block — a class can serialise native data first.
A `StaticMeshActor` carries 12 bytes of instance data ahead of its properties; other classes 15, 16 or 17. So
the reader **locates** the block instead of assuming: it requires the block to terminate exactly where the
export ends and to contain only resolving names, and takes the candidate with the most properties. A
mis-parse that starts a few bytes late loses properties rather than inventing them, which is why the test is
"ends at the end" and not "parses".

Measured prefix lengths across the corpus: **15 bytes** (49,820 exports), **0** (41,097), **12** (24,096), then
16 and 17 in small numbers. That distribution is what a T3D writer needs in order to sit on top of this — but it
is *not* per class, and the next section measures why.

## What this unblocks

* **T3D is now a formatting problem, not a parsing one.** Location, Rotation and the actor's property set are
  readable; writing them out as text is the remaining step.
* **5.3's SFX mapping** needs the same reader on sound objects, which is the identical shape.
* **3.6's defaults** are the one place this does *not* yet reach: a class object's header is empty by design,
  so class defaults live in the class's own serialised data after its fields, which is a separate problem and
  still open.

## The prefix is not a per-class constant

`inventory/actor_prefix_census.csv` (`make map-census`, `tools/map-census.py`) asks the question this page used to
leave open — whether the offset in front of a class's property block is the same on every instance — and answers it
on the whole corpus: **194,190 exports, 121,394 with a located block, 389 classes — 94 never locate one, 176 have
one prefix on every instance and 119 have more than one**. The 119 are not a tail: they hold **80,064 of the
121,394 blocks (66%)**, and the largest class in the dump is among them — `StaticMeshActor` locates at prefix 15 on
23,490 of its 32,193 instances, at 12 or 10 or a fourth offset on the rest.

**The export flags do not separate them.** 279 of the 295 classes that locate a block carry a single `flags` value
across every instance, so the question cannot be asked of them at all; of the 16 that carry more than one, 13 map
each value to exactly one prefix and 3 put one value at two prefixes. "Varies with the object's flags" is not what
this is.

**The start is ambiguous inside the 64-byte window, and the locator's tie-break decides it.** Every property
boundary is itself a valid start — a suffix of the property list also ends at the export's end — so over four maps
(4,358 exports that locate a block) **4,354 admit more than one valid start**, median 9, up to 32. `find_block`
takes the earliest parse that reaches the export end, which is the right choice for reading properties, but the
leading native bytes can also parse as a tag on some instances: three `M02A` `StaticMeshActor` exports whose
located prefixes are 10, 12 and 15 share their first ten bytes byte for byte (`82 82 ff ff ff ff ff ff ff ff`) and
differ only in where the parse begins. So a class's modal prefix is **not** an offset a T3D writer may assume; a
writer locates per object, exactly as this reader does.

**What would settle the last of it** is a class whose native prefix the code fixes: require the start whose first
property name is the one the class's own field table puts first, and see whether it reproduces the modal prefix on
every instance. That is a reader-side read, not another corpus pass.

## Not measured

* Property *values* at corpus scale. The reader decodes them on demand (`--values`) and they are game content,
  so nothing here aggregates or stores them.
* Class defaults (see above).
