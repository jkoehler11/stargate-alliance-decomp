# What a map contains, straight from its export table

PLAN 5.2 wants a T3D dump of every map so mission structure can be indexed. The export table turns out to
answer the most-asked part of that question without T3D, without a property reader, and without UELib: a
`.unr` is a package, and its level actors are exports, so "which classes does this map place, and how many"
is a histogram.

    python3 tools/map-actors.py DUMP/Maps              # every map
    python3 tools/map-actors.py DUMP/Maps/M02A.unr     # one

Output: `inventory/map_export_classes.csv` — 6,478 rows of (map, class, count).

## All 73 maps parse

| | |
|---|---:|
| maps parsed | **73 of 73** |
| distinct export classes across all maps | **389** |
| rows in `map_export_classes.csv` | 6,478 |

Largest classes across the corpus: `StaticMeshInstance` 33,509, `StaticMeshActor` 32,193, `ReachSpec` 29,665,
`Model` 8,781, `PathNode` 8,600, `Light` 8,370, `Brush` 7,383. A single campaign map is 2,000–5,500 exports;
the multiplayer maps sit at the top (`SGCTF-AbydosVsSGC` 5,430 exports, 1,440 `StaticMeshInstance`).

## This is an independent check on the existing map inventory

`inventory/map_actors.csv` was produced through UELib. Comparing the two readings of the same 73 maps:

| | |
|---|---:|
| class vocabulary | **389 vs 389 — identical set**, one class unique to each side |
| classes with identical instance totals | **256 of 389** |
| `PathNode` | 8,600 vs 8,600 |
| `StaticMeshActor` | 32,193 vs 32,193 |
| `Light` | 8,370 vs 8,370 |
| `Brush` | 7,383 vs 7,383 |

Two independent parsers agreeing on the class vocabulary and on two thirds of the totals is the strongest
validation either has had. The disagreements are systematic rather than random — this reader is consistently a
little *low*: `ReachSpec` 29,665 vs 30,539, `SpriteEmitter` 2,711 vs 2,854, `Mover` 1,255 vs 1,268,
`IntelligentSpawner` 785 vs 808 — which is what a reader that skips archetype/subobject exports would show.

One map contributes a visible chunk of that: `M21A` has **1,540 exports whose class index does not resolve**
(reported as `?`), the only map where the class resolution fails at that scale. That map's classes are
under-counted here, and saying so is more useful than averaging it away.

## What this does and does not give 5.2

**Gives:** per-map actor-class presence and counts, for all 73 maps, today. That is enough to answer "does
this mission use `JaffaBotLight`", "how many `PathNode`s", "which maps place vehicles" — the questions the
content and cut-content documents keep asking.

**Does not give:** actor *properties*. Position, rotation, tag, spawner configuration, level-script actions —
all of that is in each actor's serialised property stream, which is the next reader. So the T3D dump is still
the goal for mission structure; this just means the class-level census no longer waits for it.

## Not measured

* Actor properties, and therefore anything the level script does.
* Why `M21A`'s class indices fail to resolve. It is one map out of 73 and the failure is concentrated, so it
  looks like a local table anomaly rather than a reader bug — but that is a hypothesis, not a finding.
* The 133 classes whose totals differ between the two readers, beyond the pattern that this one is lower.
