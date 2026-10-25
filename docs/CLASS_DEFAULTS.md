# Class defaults: the class's own property list settles the block, but not the values

`docs/T3D.md` left the roster link open: a spawner names a squad script, and that script's roster is a class
default. This is where reading defaults stands — the block selection problem is solved, the value decoding is
not.

    python3 tools/class-defaults.py DUMP/System --class SGOrder
    python3 tools/class-defaults.py DUMP/System --sweep -o inventory

## The ambiguity is settled by what the class declares

The first attempt located a class's default block by the boundary rule alone and produced two candidate starts
two bytes apart, one of which invented properties (`Core`, `Range`). `inventory/class_properties.csv` already
knows what each class declares, and its superclass chain supplies inherited names, so the candidates can be
scored by how many of their names the class actually declares.

`SGOrder` now resolves at **`+0xB3` with 8 properties, 100% of them declared by the class** — `canPerformOneill`,
`canPerformCarter`, `canPerformTealc`, `canPerformJackson`, `Description`, `maxBotsAssigned`, `iconTexture`,
`Condition` — where the boundary-only rule picked a 10-property block containing `Core` and `Range`.

## But it is not reliable across the package

Sweeping all 306 classes in `StargateGame`:

| | |
|---|---:|
| classes with a candidate block | 233 |
| classes with no candidate at all | 73 |
| candidates whose names are **all** declared | **18** |
| partial matches with more than one candidate | 188 |

Eighteen of 306 is not a reader that works — it is a reader that works on the clean cases and is ambiguous on
the rest. The honest reading: property names are enough to *reject* a wrong block but not to *choose* between
two plausible ones, because a class's inherited property set is large enough that a shifted block still
matches.

## Values are still partly wrong

`Bool`, `Int`, `Byte` and object references decode correctly and self-consistently:

```
canPerformOneill   Bool    True
maxBotsAssigned    Int     -1
iconTexture        Object  icon_mission'Engine'
Condition          Byte    1
```

`Str` values do not: `Description` decodes as `Str BeginState`, which is a *name* from the package's name
table, not a string. So for `UPT_Str` the value is being read at the wrong offset or with the wrong width —
one type, not a general failure, and the next thing to fix. Until it is fixed, this tool is trustworthy for
numeric and object defaults and untrustworthy for string ones.

## What is not measured

* The 73 classes with no candidate: whether their block is absent, or present and failing the boundary rule.
* String values, above.
* The roster itself. The squad scripts the maps name (`TSS_7A_B_Jaffa_01` in `M07A.unr`,
  `AI_Jaffa_Horus_Med_single` in `M15B`/`M15C`) appear in those packages' **name tables** but are neither
  imports nor exports of them — so they are referenced from data inside the `Level` export rather than by an
  object reference this reader follows. That is measured, and it is where the next search goes.
