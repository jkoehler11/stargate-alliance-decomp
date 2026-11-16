# Actor properties: read at last

PLAN 5.2 wanted T3D for every map so mission structure could be indexed. The remaining blocker was the
property block — and with the layout confirmed against a public format reference, it parses: **121,394 of
194,190 exports across all 73 maps (62.5%) carry a readable property block**, and for actor classes it is
effectively complete. Those totals are the artifact's (`tools/map-census.py`, `make map-census`, 73 map rows and
389 class rows in `inventory/map_property_coverage.csv` and `inventory/actor_property_coverage.csv`); this page
used to quote 121,225 / 62.4%, which no committed run has produced since.

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

The per-class histograms are in `inventory/actor_prefix_census.csv` — the corpus-wide mode list this page used to
quote here (15 bytes on 49,820 exports, 0 on 41,097, 12 on 24,096) summed to less than the block total, so the
census supersedes it: the offset is what a T3D writer needs in order to sit on top of this, **but it is not a
per-class constant**, and the next section measures why.

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

**The type of the candidate's first tag decides it, and the name does not** (`make actor-prefix-anchor`,
`tools/actor-prefix-anchor.py`, `inventory/actor_prefix_anchor.csv`). The identity anchor this page proposed — require
the start whose first tag is the class's own first property — was measured on all 73 maps and does not hold:
**35,870 of the 121,394 blocks (29.5%) begin with a property other than the one their class usually begins with**, a
class's first property takes a median of 2 different names across its instances and up to 140 on `StaticMeshActor`, so
the stream is a delta and the class's first property is not a constant of it.

What does decide the start is the tag's *type*. A property's info byte carries its type in the low nibble, and the
leading tag a misparse invents decodes as type 0, `Invalid`, which is not a property type: on `M02A` every
`StaticMeshActor` whose extent front is 12 begins with `PrePivot` typed `Invalid` with one byte of value. Rejecting
candidates that contain a type-0 property moves **25,005 of the 121,394 blocks (20.6%)** and takes **200 of the 295
classes that locate a block to a single start** (the extent census: 176), 26 of them from `varies` to `constant`.
`StaticMeshActor` is the case both pages used: 4 starts with 15 on 23,490 of 32,193 by the extent rule, **15 on
32,053 of 32,193 (99.6%)** once type-0 candidates are rejected. `Light` is the sharper one — the census calls it
constant at 12, and 4,661 of its 8,370 blocks begin there with a type-0 tag, which the rule moves to 15 on 8,112.

**The prefix is a record, and its length is twice the class index plus thirteen** (`make actor-prefix-record`,
`tools/actor-prefix-record.py`, `inventory/actor_prefix_record.csv`). The residue above was closed by reading the
bytes in front of a block instead of rejecting candidate starts: **79,925 of the 121,394 blocks (65.8%) begin with
their own class index**, and every one of those 79,925 carries the same record —

    [class index][the same index again][8 x 0xff][4 bytes][0x81]

so the front is **`2*w + 13`**, `w` being the encoded width of the class index (1, 2 or 3 bytes, depending on where
the class sits in that package's import table): `ZoneInfo` is `ad ad ff ff ff ff ff ff ff ff 30 00 30 00 81` at 15,
`LevelInfo` `d9 02 d9 02 ff ff ff ff ff ff ff ff 6b 00 78 00 81` at 17. The length is a property of the *package*,
not of the class, so a per-class constant could not exist — and **148 of the 148 classes that carry a prefix are the
record on every one of their blocks**, with nothing left over.

The record judges both older rules. Its start is a type-0-free candidate on **all 79,925** blocks; it equals the
extent locator's choice on 53,866 (67.4%) and the type-0 rule's on 78,658 (98.4%). The 1,267 blocks (1.6%) in 64
classes where the two disagree are starts the type rule keeps because they parse without an `Invalid` tag and sit
earlier: `Light` 257 of 8,370, `Mover` 143 of 1,268, `StaticMeshActor` 140 of 32,193. So the "93 classes still
locating at more than one start" is not a residue of the prefix's own extent — it is 1.6% of the class-referenced
blocks, and the record names the right start for every one of them.

**The other third has no prefix at all.** 41,254 blocks (34.0%) sit at front 0 — `ReachSpec` 30,539,
`SpriteEmitter` 2,854 — and 215 blocks over 5 classes carry a front whose first byte is not their class index
(`StaticMeshInstance` 211). Those exports are not an actor instance's stream, which is why the earlier histogram of
"prefixes" mixed two populations: 148 of the 295 classes carry the record and 147 never do.

The four bytes between the `0xff` fill and the closing `-1` are left unnamed: they vary per instance, taking 1,011
distinct values over the 79,991 exports that carry the record, the commonest `00 00 00 00` on 24,912 and
`30 00 30 00` on 8,688.

## Not measured

* Property *values* at corpus scale. The reader decodes them on demand (`--values`) and they are game content,
  so nothing here aggregates or stores them.
* Class defaults (see above).
* What the record's four middle bytes hold, and why 215 blocks over 5 classes carry a front without their class
  index first (`StaticMeshInstance` 211 of them): the record names the block's start but not those fields.
