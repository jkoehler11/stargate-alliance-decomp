# The class tables and the classes that own them

Status: **466 of the 492 tables** in `inventory/xbe_class_tables.csv` are named from the build's own
class registrations, and every class's object size comes with the name
(`inventory/xbe_class_table_names.csv`, `tools/class-table-names.py`). The 26 that are not named are
listed at the end. This answers the "two families of table" question that `docs/NATIVE_VM.md` left
open: the split is classes that override two virtuals against classes that keep an empty default.

## The registration every class goes through

Every class object is built by a call to `FUN_0003af60`. There are **493 call sites in 493 distinct
functions** — one registration per class — and `ByteProperty`'s is typical:

```
FUN_0003af60(0, 0x70, 0x8000, 0, 0, 0, 0, L"ByteProperty", uStack_4, L"System",
             0x4084004, &LAB_0007dfe0, &DAT_000624d0);
if (iVar2 == 0) FUN_00058a30("ReturnClass", "…\Core\Src\UnProp.cpp", 0x1af);
```

| argument | what the registrations show |
|---|---|
| 0 | outer: a literal `0`, or the enclosing package when pushed as a register |
| 1 | **object size** in bytes — `0x70` here, and it agrees with every size already measured |
| 2 | class flags (`0x8000`, `0x8001`, `0x18000`, `0x28000`, `0`) |
| 7 | **the class name**, a wide literal |
| 9 | `L"System"`, the package the class object is created in |
| 10 | a second flag word, `0x4084004` in every registration read so far |
| 11 | a **constructor thunk** (or the constructor itself) |
| 12 | `&DAT_000624d0` in every registration read so far — not identified |

## The chain the tool follows

```
registration function
  └─ pushes L"<Class>"                      -> the class name
  └─ pushes &<thunk>                        -> a null-checking thunk:
                                               mov ecx,[esp+4]; test ecx,ecx; je end; jmp <constructor>
        └─ constructor                       -> mov dword ptr [this], <table>   (opcode C7 /0)
              └─ table                       -> inventory/xbe_class_tables.csv
```

Each step is a byte pattern in the image, so the link is a literal the build ships plus the
instruction that uses it — not a name inferred from ordering. `pip install capstone` is the one
dependency, pinned in `tools/versions.lock`.

## What it establishes

- **The 492 tables are per-class vtables**, installed at `[object+0x00]` by the class's own
  constructor — the same offset `ProcessEvent` is dispatched through, which is what makes the
  slot-21 measurement in `docs/NATIVE_VM.md` a statement about instances, not about tables in a file.
- **Object sizes**, independently cross-checked. The registration's size argument reproduces every
  size measured by other means, so the column is trustworthy where nothing else measured it:

  | class | registration | previous measurement |
  |---|---|---|
  | `AlkeshBomber` | 3,788 | 3,788 — `docs/NATIVE_NOTES.md`, from the ucpp registration stub |
  | `StargatePlayer` | 4,344 | 4,344 — same |
  | `StargateVehicle` | 3,344 | 3,344 — same |
  | `ByteProperty` | `0x70` | `0x70` — the class literal in `docs/NATIVE_PROPERTIES.md` |
  | `StructProperty` | `0x70` | `0x70` — same |
  | `ClassProperty` | `0x74` | `0x74` — same |

  Two independent sources agreeing is what lets the rest of the column be used, e.g. `Actor` 908,
  `Pawn` 2,408, `GoauldDrone` 2,532.
- **A name for 466 vtables.** Engine classes (`Actor`, `Pawn`, `Controller`, `Info`,
  `NavigationPoint`, `Triggers`, …) and game classes (`StargatePlayer`, `StargateVehicle`,
  `AlkeshBomber`, `GoauldDrone`, …) now label the tables a decompiled body dispatches through.

## The two families, explained

Slots 19 and 20 (`+0x4c`, `+0x50`):

| slot 19 / 20 hold | tables | classes |
|---|---|---|
| `0x00020f00` — a single `ret`, the empty default | **327** | `SGScreen*`, `Order`, `MatObject`, and the whole property family |
| real bodies (`0x000c4f60`, `0x000c6d00`) | **165** | `Actor`, `Keypoint`, `AIScript`, `Info`, `ReplicationInfo`, `Pawn`, `Vehicle`, `SVehicle`, `Emitter`, `NavigationPoint`, `Triggers`, `Controller`, … |

So the split is not abstract against concrete: it is **classes that override these two virtuals
against classes that keep the `ret` stub**, and the 165 are the actor-side engine hierarchy. The two
slot numbers are now a nameable fact: any object whose table has the stub there leaves those
functions unimplemented.

## Not established

- **Which virtuals slots 19 and 20 are.** The override reads `[this+0x2dc] & 1` and then `[this+0xa4]`,
  `[+0xf4]`; the property classes do not override them, so nothing in the property track calls them.
- **The 26 tables with no registration.** They are `0x004c78c0`, `0x004c7958`, `0x004c7a10`,
  `0x004c7ac8`, `0x004c7c78`, `0x004c9348`, `0x004c93d0`, `0x004c94b0` and 18 more, listed in the CSV
  with the evidence column saying so. Their constructors exist (the `mov [this], table` store is
  there) but the registration that names them does not push both a literal and a followable thunk,
  so the link is missing rather than the table.
- **Arguments 10 and 12.** `0x4084004` and `&DAT_000624d0` are the same in every registration read;
  what they mean is not established, so no column is claimed for them.

## How many classes declare natives of their own

`inventory/native_declarations.csv` is regenerated by `make package-census`, and its rule is measurable: a class in
`StargateGame` that declares a native function **no ancestor declares**. Eleven classes qualify — `BotBase`,
`BotEnemy`, `StargateWeapon`, `StargateHUD`, `SGOrder`, `SGScreenBloom`, `SGFontInfo`, `HitEffectManager`,
`ShadowManager`, `Electrocutor`, `FarClipChange`. The six the ucpp work named are the subset that also carries a
`ucpp_Register_<Class>` stub in `symbols/xbe_overrides.csv`; the other five declare natives the stock engine
already implements, so nothing about them needs ucpp. The artifact used to list only those six, which read as though
six classes added natives at all.

## Reconciling the declarations against the records (PLAN 3.9)

`inventory/natives.csv` lists the native functions the packages carry; the script text declares them. Nothing had
checked that the two agree class by class, and both are inputs to the compile loop: a `native function` declaration
says the body lives in C++, and a `native(1234)` pins its index, so a disagreement would mean either the tree is
missing a native body or the record is describing something the source no longer has.

    python3 tools/native-reconcile.py DUMP/System -o inventory     # inventory/native_reconcile.csv
    python3 tools/native-reconcile.py DUMP/System --class Object --walk

| | count |
|---|---:|
| classes declaring natives (text) or carrying records | 96 |
| `native function` declarations in the shipped text | 681 |
| …of those carrying an explicit index | 256 |
| `native … operator` declarations | 92 |
| operator records the compiler synthesises from them | 112 |
| native function records (`natives.csv`, operators excluded) | 685 |
| **records matched to a declaration** | **672 (98.1%)** |
| declarations with no record | 9 |
| records with no declaration | 13 |
| **index disagreements** | **0** |

**The index check is the part that matters.** Every one of the 256 indexed declarations agrees with its record's
token — the same number `docs/NATIVE_VM.md` reports as the distinct index space. That makes the native list usable as
a cross-check for a rebuild rather than a claim about one.

Three wrinkles, each handled explicitly instead of being smoothed over:

* **headers wrap across lines** (`native function Foo(` / `int A )`), so statements are matched, not lines — the
  line-shaped regex the source-tree extraction uses loses those;
* **`Core.Object` declares most of its natives as operators**, including pre-operators
  (`native(129) static final preoperator bool ! ( bool A )`), which a function-shaped pattern does not see at all;
* **the compiler synthesises a record per operator signature** (`Add_IntInt`, `AddEqual_VectorVector`,
  `Concat_StrStr`, `LessLess_VectorRotator`, …), so those 112 records have no declaration to match by design and are
  counted apart.

**The residual is 22 records over 14 classes**, and it is named rather than explained: `PlayerController` has 4
records with no declaration (the `Client*`/`Server*` pairs, where the shipped text looks truncated), `Core.Object`
has 2 declarations with no record, `Weapon`, `GUIController`, `StargateWeapon` and `HitEffectManager` two each. The
same two classes whose text the extraction flagged as truncated (`GameInfo`, `WarpZoneMarker`) are **not** among
them, so truncation is a candidate cause, not a demonstrated one.

Not measured: which side is right where they disagree. Settling that needs the C++ side (Phase 4), which is why the
residual is a list of names and counts rather than a verdict.
