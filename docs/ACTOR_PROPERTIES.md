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

**The tie-break has since been priced on the action side.** `docs/MAP_ACTION_PARAMETERS.md` reads 18,510 placed
actions with this same reader and the same locator, and finds "most properties" picking a start *inside* the block
on 12 of them — caught because the block then names a property its class chain does not declare. Re-scoring the
candidates by the fraction of declared names recovers 21 of those 26 statements on 8 placements, and picks exactly
this locator's start on **all 15,429** placements whose block names only declared properties
(`inventory/action_block_relocation.csv`). So the ambiguity is real, and on the action side the class's own
declaration list is the discriminator — the actor side's version of the same test is the type-0 rejection
`tools/actor-prefix-anchor.py` measures, and the `StaticMeshActor` blocks there whose front begins with `PrePivot`
typed `Invalid` are the same defect seen from the other end.

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

**The record marks an actor, and the class's super chain says which third gets one** (`make actor-prefix-kind`,
`tools/actor-prefix-kind.py`, `inventory/actor_prefix_kind.csv`). The two populations above are not a residue and its
remainder: they are the level's actor stream and everything else. Joining each block's class to
`inventory/classes.csv`'s `super` column and walking the chain gives **148 classes and 79,925 blocks that carry the
record, all of them reaching `Actor`, and 142 classes and 41,254 blocks with no front at all, none of which reach it** —
no class on both sides, and no exception either way.

The bare names are the ones that looked surprising: `ReachSpec` extends `Object` directly, `ActionMoveCamera` extends
`MatAction`, `ACTION_Talk` extends `LatentScriptedAction`, `ACTION_SetBotScript` extends `ScriptedAction`, and
`SpriteEmitter` is not the actor it looks like — its parent `ParticleEmitter` **is itself an `Object` in this build**,
which is why a particle sprite is saved without a front and its owning `Emitter` (an `Actor`) is not.

The remaining 215 blocks are not a front at all: `StaticMeshInstance` (211), `KMeshProps` (1) and `TerrainSector` (1)
are the three classes no `.u` package in the corpus defines, and their exports are mesh and terrain bytes that the
locator's "parses cleanly to the payload's end" test accepts as a block — a `StaticMeshInstance` export reads back as
`Scale`, `Vector`, which are name-table hits and not properties. Two single blocks (`ACTION_SaveGame`, `Cubemap`) have
bytes in front that are not their class index either. So the record, its absence and this small false-positive set
account for **121,394 of 121,394 blocks**.

The record is also not the class's own serialiser: this build's `AActor::Serialize` (`0x000c7fa0`, 110 bytes) calls
`UObject::Serialize` (`0x00063f70`) first and then conditionally writes two arrays at `+0x244`/`+0x250`; nothing in it
stores a class reference twice, so the front is written by whatever serialises the level's actor stream around it.

The four bytes between the `0xff` fill and the closing `-1` are read below: they are a code, not a reference --
**1,010 distinct values over the 79,925 blocks that carry the record**, the commonest `00 00 00 00` on 24,907 and
`30 00 30 00` (the halves 48,48) on 8,677. The 1,011-over-79,991 this page used to quote was counted over every block
that *begins* with its class index rather than over the blocks whose record matched, and no committed run produces
that pair.

**The four middle bytes are a code, not a reference, and the corpus says so four ways** (`make actor-prefix-middle`,
`tools/actor-prefix-middle.py`, `inventory/actor_prefix_middle.csv`). Read as the two little-endian `u16` such a field
would be written as, they are a *vocabulary*: **1,010 distinct values over the 79,925 blocks**, and **165 of those
values appear in ten or more of the 73 maps**. The cross-map count is what refuses the one reading a serialiser's
reader would try first. A name index is a fact about *one* package's name table, so a value that recurs in seventy
maps cannot be one: `48,48` is 8,677 blocks in **70 maps** and its first half resolves to **42 different names** across
them, `110,0` is 7,071 blocks in 71 maps as **61 names**.

| reading of the four bytes | blocks it fires on | control |
|---|---:|---:|
| a half names a property the same block declares | 2,572 (3.2%) | 6,597 (8.3%) |
| a half names the block's own class | 294 (0.4%) | -- |
| a half names the block's `Tag` value | 243 (0.3%) | -- |
| the dword is the export's offset or size, or the block's length | 549 (0.7%) | -- |

The control is the same test with a name index of the same *band* (a fixed scramble of the export's ordinal taken
modulo `0x80`), and it is what makes the first row a negative rather than a reading: early name-table entries are the
names every class references, so a control drawn from the whole table hits only 0.3% and would have made the field's
3.2% look like evidence. At the same magnitude, an index the block never mentions hits **more** often than the value
does.

Two more properties fit a flag or enum word rather than a pointer: the halves are **equal on 39,165 blocks (49.0%)**
and `00 00 00 00` is the single commonest state (24,907, 31.2%), so "nothing" is what this field usually says; and
**5,571 blocks (7.0%)** carry a half at or above `0x80`, which no one-byte name index can (`65278,65278` is 4,912
blocks in 18 maps and resolves to nothing at all). Nor is the value an identity or a class constant: `StaticMeshActor`
carries **607** distinct values over its 32,193 blocks, while `48,48` alone covers 448 `StaticMeshActor`s, 53 `Mover`s
and 39 `Light`s in a single map -- per instance, and small. The tool's `verdict` column sorts the classes: **110 carry
more than one value, 21 carry exactly one, and 17 give every block its own**.

So what is left is the writer, not the payload: the field's *meaning* is fixed by whatever serialises the level's actor
stream (and that is not `AActor::Serialize`, above), and the corpus can price every reading without naming the code it
carries.

## Not measured

* Property *values* at corpus scale. The reader decodes them on demand (`--values`) and they are game content,
  so nothing here aggregates or stores them.
* Class defaults (see above).
* What the code in the record's four middle bytes *means*. The previous section measures it as a vocabulary of 1,010
  values that no reference reading survives; which enum it indexes is the writer's answer, not the payload's.
