# T3D: every map's actors, written out

PLAN 5.2's first half is done. With the property block readable (`docs/ACTOR_PROPERTIES.md`), writing T3D is
formatting, and all 73 maps have been written:

| | |
|---|---:|
| `.t3d` files | **73** |
| actors written | **121,225** |
| actors carrying a `Location` | **112,000** |
| size | 27 MB |
| location | `$WORK/extracted/maps/` — game content, never the repository |

    python3 tools/map-to-t3d.py DUMP/Maps --out $WORK/extracted/maps

## The writer is checked by its own arithmetic

The actor count written equals the property-block count measured independently in the previous step —
**121,225 in both** — so no actor is being silently skipped between "has a block" and "appears in the file".
One map in detail: `M02A` yields 2,246 actors and 527,030 bytes, with 1,858 of those actors carrying a
`Location`, and the first section is the level's `LevelInfo` (`TimeSeconds`, `Summary`, `VisibleGroups`, the
editor camera positions). That is the right object in the right place.

## Shape

```
Begin Actor Class=StaticMeshActor Name=StaticMeshActor_25
   Tag="..."
   Location=(X=...,Y=...,Z=...)
   Rotation=(Pitch=...,Yaw=...,Roll=...)
   StaticMesh="..."
   ...
End Actor
```

Float, int, bool, name, string, vector and rotator values are decoded; arrays and structs are written by size
rather than expanded member by member.

## What this makes answerable

Anything that is a per-map, per-actor property question is now a text query over `$WORK/extracted/maps` —
which maps place which actor classes, where the spawn points, path nodes, triggers, movers and objective
actors are, and what each one's parameters are. That covers the class-level census (already in
`inventory/map_export_classes.csv`) and goes past it into configuration.

## Not measured, and why

* **Object references are not resolved.** A value that points at another object is written as `ref:<index>`
  rather than the object's name. Resolving requires a name-lookup walk over the export and import tables for
  every reference, which is the next improvement and is why the tool does not claim to produce
  editor-loadable T3D.
* **The level script itself** is not extracted: the `Level` export's bytecode/action list is separate from the
  actors' properties, and the `ACTION_*` actors' parameters are visible while the *sequence* is not.
* **Arrays and structs** are emitted by size, not expanded.
* **Round-tripping** is untested and out of scope: this is an index, not a re-importable map.
