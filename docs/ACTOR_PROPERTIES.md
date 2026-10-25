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
16 and 17 in small numbers. That distribution is per class, and it is what a T3D writer needs in order to sit
on top of this.

## What this unblocks

* **T3D is now a formatting problem, not a parsing one.** Location, Rotation and the actor's property set are
  readable; writing them out as text is the remaining step.
* **5.3's SFX mapping** needs the same reader on sound objects, which is the identical shape.
* **3.6's defaults** are the one place this does *not* yet reach: a class object's header is empty by design,
  so class defaults live in the class's own serialised data after its fields, which is a separate problem and
  still open.

## Not measured

* Property *values* at corpus scale. The reader decodes them on demand (`--values`) and they are game content,
  so nothing here aggregates or stores them.
* Whether every class's prefix length is stable, or varies with the object's flags. The distribution is
  concentrated but not proven constant.
* Class defaults (see above).
