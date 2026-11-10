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

**The residual is 22 records over 14 classes, and the image now decides 15 of them** — see *The 22 residual rows,
each with a verdict* below: `PlayerController` has 4 records with no declaration (the `Client*`/`Server*` pairs),
`Core.Object` has 2 declarations with no record, `Weapon`, `GUIController`, `StargateWeapon` and `HitEffectManager`
two each. The binder pool in the image settles which side is right where one exists and leaves 7 declarations the
build does not bind at all.

Not measured: whether the 7 unbound declarations were compiled in some other build of the class — nothing in the
image binds them, so there is no third witness to ask; everything else in the residual is decided.

## Every token has a basis (per record, UNKNOWNS U1/U3)

The table above answers "do the two halves agree class by class". The finer question — **for every record that
carries a token, which declaration in the class's own text put it there?** — is the one U1 left open ("the 523 claim
no slot") and U3 guessed at ("the compiler assigns an index to a declaration that does not spell one out").
`tools/native-reconcile.py` now classifies all 274 token-carrying records and writes
`inventory/native_tokens.csv` (one row per record: `package, class, function, native_token, basis`):

    python3 tools/native-reconcile.py DUMP/System -o inventory     # both CSVs, and the summary below
    python3 tools/native-reconcile.py --selftest                   # the classifier on fixtures; in `make ci`

| basis | records | what it is |
|---|---:|---|
| `text-index` | **166** | the class's text spells this index on a declaration of that name |
| `operator-expansion` | **107** | no declaration names it: one `operator` statement expanded into typed signatures |
| `unindexed-declaration` | **0** | a declaration that named the function and spelled no index |
| `index-mismatch` | **0** | text and record both spell an index and disagree |
| `unresolved` | **1** | `UPreview.UPreviewMapListClient.Created` — see below |

**The guess in U3 is falsified, corpus-wide.** Not one of the 274 records belongs to a declaration that named a
function without spelling its index, so on this build the compiler never invents an index for an index-less
`native`; a token-carrying record always has a declaration that spelled it out or an `operator` behind it. The
practical rule that survives is narrower than the one U3 stated: a rebuild reads indices from the package records
because the *text* of the runtime packages is incomplete (9 declarations without a record, 13 records without a
declaration), not because the text omits indices.

**`Core.Object`'s "17" is the operator expansion, exactly.** The class carries 160 token-carrying records and its
text spells 143 indices — 57 on `native function` declarations and 86 on `operator` statements — and
57 + 103 = 160 while 57 + 86 = 143, so the surplus is 103 − 86 = **17**: per-signature operator records such as
`Add_IntInt` (146), `Concat_StrStr` (112) and `EqualEqual_ObjectObject` (114), none of which any declaration names.
So the 17 that U1 counted as "mostly … operator records, the compiler synthesises" are *all* operator records; there
is no hand-declared index-less function among them.

**The single unresolved row is in the one package the game never loads.** `UPreview`'s class text ships `Created` as
a **script function with a body** (`function Created()` … `Super.Created()`), so no declaration of that class is
native at all. The package carries two `Function` exports named `Created` (indices 59 and 78) whose outers resolve to
a `FloatProperty` (`XL`, export 7) and a `TextBuffer` (`ScriptText`, export 81) — neither can be a function's outer,
and every function export in the file carries the same flag word `0x70004` — while both "payloads" end in the same
eight bytes `47 00 00 00 02 00 02 00`. So the token 2 is the second dword of a word that belongs to a script
function's end, not a declaration: the row is an artefact of an editor package's export table, and `UPreview`,
`Editor` and `UnrealEd` are excluded from native naming anyway.

The consequence for 3.9/6.1: `inventory/native_tokens.csv` is the per-record form of the rule the compile loop
needs — an index is trustworthy when its basis is `text-index`, and a record whose basis is not is either a
synthesised operator body (no script to compile) or a defect worth reading.

## The 22 residual rows, each with a verdict

The table's residual — 13 records with no declaration, 9 declarations with no record — was left as "which side is
right needs the C++ side". The C++ side is already committed: `tools/native-binder.py` extracts the build's own
`int<prefix><Class>exec<Function>` literals (`inventory/native_binder_names.csv`), which is exactly the name the
engine's binder looks up for a native. So the same run now writes `inventory/native_residual.csv`
(`package, class, function, direction, native_token, binder, verdict`), and every row gets one of three:

| verdict | rows | what the image says |
|---|---:|---|
| `text-missing` | **13** | the record **is right**: the image carries `int⋯exec<Function>` for that class and name, and the record's token, so only the class's shipped text lacks the declaration |
| `declaration-unbound` | **7** | no record and no binder name: the `native function` declaration has no counterpart in the build at all |
| `binder-only` | **2** | `Core.Object.Divide` and `Core.Object.Round` are declared *and* bound in the image, so the **record** table is what is missing |

So the residual is not 22 undecided disagreements: 15 of the 22 are decided by the image (13 text gaps, 2 record
gaps — both `Core.Object`), and the remaining 7 are declarations (`Weapon.GetErrorVec`, `Weapon.GetFireStart`,
`ScoreBoard.SortPRIArrayFast`, `StargateWeapon.DrawReticule`, `StargateWeapon.updateLeadLag`,
`HitEffectManager.Update`, `HitEffectManager.SetupFade`) that nothing in the build binds. The truncation hypothesis
is only partly right: 3 of the 13 owning classes' texts end unbalanced (`Actor`, `GUIFont`, `UPreviewMapListClient`),
while `PlayerController`'s 245 KB text is balanced and still declares none of its four `Client*`/`Server*` records.



## Every body, marked (PLAN 3.9)

The declarations are reconciled above; this is the other half — a reader opening `Pawn.uc` should not have to
guess that a `native function` has no script to compile. `tools/native-mark.py` writes one line above every native
declaration in the extracted tree — `native function` and `operator` alike — and one row to
`inventory/native_bodies.csv`:

```
// native - see src/Native/Engine/Pawn.cpp  token 0 binder intAPawnexecSetViewPitch body 0x00195340
simulated native function SetViewPitch( int NewPitch );
```

The tree itself is game-derived source and stays under `$WORK` (`make native-bodies` writes
`$(WORK)/UnrealScript-marked`, 2,319 class files); what is committed is the table, and its `marker` column holds the
exact line written above each declaration — so the tree's provenance is auditable from git without the text. The
row key `(package, class, function)` is the key `inventory/natives.csv` uses, so `token`, `params` and the flags are
one join away; `binder`/`binder_va` are the image's own `int<prefix><Class>exec<Function>` literal
(`inventory/native_binder_names.csv`) and `body_va` the body pointer the native registration table gives
(`inventory/native_targets.csv`).

    python3 tools/native-mark.py DUMP/System -o inventory --tree $WORK/UnrealScript-marked
    python3 tools/native-mark.py --check        # the committed table, no dump; in `make ci`
    python3 tools/native-mark.py --selftest     # the parser and the marker pass on fixtures; in `make ci`

| what | declarations |
|---|---:|
| `native function` declarations in the shipped text | **681**, in 92 classes |
| … with a record in `inventory/natives.csv` | **674** |
| … with the image's binder literal | **660** |
| … with a body address | **660** |
| … whose body is a function in `symbols/xbox_functions.csv` | **389** |
| … with no body anywhere in the image | **21** |
| `operator` declarations in the shipped text | **97**, in 3 classes |
| … composing a record of their own class | **97** |
| … with the image's binder literal | **85** |
| … with a body address | **97** |
| marker lines written | **778** (681 functions + 97 operators, 0 unmarked) |

**The 21 without a body are two known sets, not a new mystery.** Fourteen are **editor-only** — `BrushBuilder`'s 13
and `MaterialFactory.ConsoleCommand`, classes whose natives are compiled into the editor build rather than into the
shipped XBE — and the other seven are exactly the `declaration-unbound` rows the residual table above already names
(`Weapon.GetErrorVec`, `Weapon.GetFireStart`, `ScoreBoard.SortPRIArrayFast`, `StargateWeapon.DrawReticule`,
`StargateWeapon.updateLeadLag`, `HitEffectManager.Update`, `HitEffectManager.SetupFade`). The two tools agreeing on
the same seven is the useful part: a declaration nothing binds gets no marker target, rather than a guessed address.

**271 of the 660 bodies are not functions in the census at all.** `function_modules.csv` attributes 389 of them
(217 `(unnamed)`, 166 `(dispatch: declared)`, 1 `(dispatch: slot only)`, 5 named) and has no row for the rest. That
is not a defect in the join: the function census is call-graph-driven, and a native's only reference *is* the
registration table — nothing calls it directly. **Measured 2026-11-10: all 274 are function starts, so the census denominator is 15,231, not 14,957.** A byte
pattern is not a verdict — this project's own `SeedGaps` opening-byte list once hid 47 real entries — so
`tools/native-census.py` writes `inventory/native_census.csv`, one row per distinct body address, carrying three
kinds of evidence: the address's relation to `symbols/xbox_functions.csv` (an entry point, inside a function's
`[address, address+size)` range, or outside every range), the image's byte before it and its first bytes, and the
answer the disassembler gives when `tools/ghidra/ghidra_scripts/NativeCensus.java` *creates* a function there.

| evidence | result |
|---|---:|
| of the 883 distinct body addresses, function entry points in the census | 609 |
| … inside a census function's range (`symbols/xbox_functions.csv` having folded a native into a neighbour) | **0** |
| … outside every function's range | 274 |
| … preceded by a function boundary (`0xCC` padding 251, `0x00` padding 15, a `ret` 8) | 274 |
| … opening on an MSVC prologue shape (`arg-load` 117, `push-reg` 115, `seh` 21, `alloc` 15, `empty-return` 4, `push-imm` 2) | 274, 0 unclassified |
| … **accepted by the disassembler as a function start** | **274**, 0 rejected, 0 inside another body |

These are bodies, not pointers into data: median 106 bytes / 39 instructions, the smallest 3 bytes (four `UObject`
log-filter natives whose whole body is `ret 8`: `ListLogFilters`, `SetLogFilter`, `ClearLogFilter`,
`EmptyLogFilters`), the largest 2,016 bytes / 571 instructions (`Actor.GetClosestBone`), and all 274 end on a flow
terminator. The script creates functions in address order, so a false start would be reported as `inside` the body
created just before it; none was. `docs/PATH.md` therefore states **15,231** functions (14,957 + 274) and a named
share of 1,988/15,231 = **13.05%**. Of the 274, 271 are the body of a `native function` declaration and 3 carry no
declaration at all (`Object.ClockwiseFrom_IntInt`, one of the operator-expansion records; `SVehicle.UpdateVehicle`
and `GUIFont.GetFont`, whose declarer the text does not spell as a binding).

**The `src/Native` path segment is the package, and that is safe.** Only 5 of the 660 bodies carry a module name in
`function_modules.csv`, and every one agrees with its declaring package (`Core.Object.StaticClearConfig`→`Core`,
`Engine.Actor.KRemoveLifterFromBone`/`KMakeRagdollAvailable`→`Engine`, `Canvas.WrapText`→`Engine`,
`GUI.GUIMultiColumnList.SortList`→`Gui`, modulo case). One file per class is also the shape the engine's own source
uses, so the target of a reconstruction is `src/Native/<Package>/<Class>.cpp` and a function is a symbol in it.

**The 97 `operator` declarations, and what the "92" was.** The marker pass counts operators with
`native-reconcile`'s `NATIVE_OP`, whose symbol is one or two punctuation characters, so it cannot spell `$`, `$=`,
`>>>` or a named operator (`ClockwiseFrom`, `Dot`, `Cross`): it reports 86 declarations for `Core.Object`, whose
text holds 91, and 92 in total where the statements hold 97. `tools/native-operators.py` reads the statements and
composes each record name by UE2's own rule — stem from the symbol (`==`→`EqualEqual`, `$`→`Concat`,
`@=`→`AtEqual`, `>>>`→`GreaterGreaterGreater`), then one type suffix per operand in declaration order
(`vector * ( float A, vector B )`→`Multiply_VectorFloat`), with `out`/`coerce`/`skip` not part of the name — and
`inventory/native_operators.csv` records the result: **97 of 97 declarations bind to a distinct record of their own
class**, so a declaration is one-to-one with a record and a marker can name it.

| class | declarations | records composed | operator records no declaration composes |
|---|---:|---:|---:|
| `Core.Object` | 91 | 91 | 13 synthesised, 12 of them carrying a token |
| `Engine.Actor` | 4 | 4 | 0 |
| `Engine.StreamBase` | 2 | 2 | 0 |

The thirteen are the compiler's: `++`/`--` and unary `-`/`!`/`~` are synthesised from the operators the class does
declare (`AddAdd_{Int,Byte,PreInt,PreByte}`, `SubtractSubtract_{Int,Byte,PreInt,PreByte}`,
`Subtract_Pre{Int,Float,Vector}`, `Not_PreBool`, `Complement_PreInt`), which is why `Core.Object` carries 104
`operator` records for 91 statements and `inventory/native_tokens.csv` counts 107 token-carrying operator records
across the 97 declarations. Five more names only *begin* with an operator stem and are ordinary natives (`Atan`,
`Round`, `Divide`, `Nothing`, `OrthoRotation`), so a prefix test is not the classification. The string operators are
the one place the two spellings differ, and both are measured: the script writes `Str` (`At_StrStr`), the
registration pool writes `String` (`At_StringString`, 12 records), and the alias is kept in the `composed` column
rather than silently corrected.

Marking an operator is therefore the same shape as marking a function — one line per declaration naming its record
and token — and it is part of the same pass (**measured 2026-11-10**):

```
// native - see src/Native/Core/Object.cpp  token 242 record EqualEqual_BoolBool binder intUObjectexecEqualEqual_BoolBool body 0x00041000
native(242) static final operator(24) bool == ( bool A, bool B );
```

The operator line adds one field, `record`, because the declaration's own name is a symbol: the symbol alone cannot
identify the row — `Core.Object` declares `==` **eight** times (`bool`, `int`, `float`, `vector`, `rotator`,
`string`, `Object`, `name`) and each composes a different record. So the pass joins the ordered declaration list to
`inventory/native_operators.csv` by declaration text; a text the table does not carry raises and the tree is not
written, rather than a marker landing on the wrong declaration. All 97 compose a record, all 97 carry a body
address, and 85 carry the image's binder literal — the twelve without one are the string operators, which the
registration pool spells `_StringString` where the script writes `_StrStr`. Ninety-six of the 97 bodies are in the
function census; the exception is `Object.ClockwiseFrom_IntInt`, the operator-expansion record the census note above
already singles out. The thirteen records the compiler synthesises have no declaration, so there is nothing to mark
for them — that is `inventory/native_tokens.csv`'s `operator-expansion` basis, not a gap in the tree.

The marker pass is **idempotent by construction** — every line carrying the prefix is removed before the fresh
markers are inserted, so it is a pure function of the package text: a second run over the 2,319 files hashes
identically, and a marker whose module or address changed is replaced rather than duplicated.

## `tools/native-mark.py`'s `--check`

`--check` answers from `inventory/native_bodies.csv` alone (no dump, no write): every `(package, class, kind,
function, record)` appears once, an operator row names a record and a function row does not, `src_native` follows
the row's own package and class, and `marker` is the render of its own row — so
a hand-edited row, or a row regenerated after the marker format changed, fails the gate instead of shipping a table
the tree does not match. `--selftest` covers the parser (a wrapped declaration, a commented-out one, native *data*
`var native const int`, an operator) and the pass itself (marker text, offsets, idempotency, a stale marker).
