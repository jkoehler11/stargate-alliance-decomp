# Matching: the L2/L3 scoreboard (PLAN 3.8)

The project grades a function in four tiers: **L0 present** (the class exists), **L1 compiles**, **L2 opcode
sequence matches** the shipped script, **L3 byte-exact**. `tools/uscript-dis.py --compare A B` scores two packages
function by function and keeps L3 and L2 apart, because an operand that moved is not an opcode that moved.

The comparator needs no compiler — only two packages — so it is built and validated while `ucc` is still missing
(`PLAN 3.6`). What it cannot do without one is *score a rebuild*. This page is the other half of that: the
**baseline** a rebuild is scored against, package by package, so the day a compiled package exists the comparison
has a denominator instead of a fresh start.

    python3 tools/uscript-dis.py --compare BUILT.u SHIPPED.u -o "$WORK/match"

## The columns

| column | what it counts | what moves it |
|---|---|---|
| `functions` | every `Function` export the package owns — the comparator's index, keyed by the **class path** (`Actor.Dying.BeginState`, below) | a package's export table |
| `scripted` | payloads the header model places and the walker reads end to end — **the ceiling**: a rebuild can be graded on nothing else | an opcode rule (the corpus walk) |
| `scriptless` | natives with no epilogue: no body exists to grade | — |
| `refused` | an epilogue but a walk that stops (`docs/BYTECODE.md`, `inventory/opcode_refusals.csv`) | the per-opcode tail |
| `operator` | operator declarations, which serialise their names in front of the header (below) | a compiler |
| `unaligned` | a header that does not end on the export's own name and is not an operator — **0 in this build**; a non-zero there is a defect in the header model | — |
| `other` | a payload too short to hold a header, or an epilogue with no room for a script in front of it — **0 in this build** | — |
| `L3 ceiling` | the package compared with itself, so byte-identical and equal to `scripted` by construction | — |
| `control` | where the mutation control flipped its bit (below) | — |

`L3 ceiling` is deliberately the *label* a rebuild fills in, not a match rate: a self-comparison can only say the
comparator read the same bytes twice. The honest claim the baseline makes is the denominator, and the mutation
control is what keeps the two columns from being one tautology.

## The baseline

<!-- generated:matching-baseline -->
**18 package(s), 12472 `Function` export(s): 11687 with a script, 673 scriptless, 0 refused, 112 operator, 0 unaligned.**

| package | functions | scripted | scriptless | refused | operator | unaligned | L3 ceiling | control |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| `AmbientCreatures.u` | 97 | 97 | 0 | 0 | 0 | 0 | 97 | `AmbientCreatureManager.AddCreature+2` L3 96, L2 97 |
| `Core.u` | 214 | 19 | 91 | 0 | 104 | 0 | 19 | `HelloWorldCommandlet.Main+2` L3 18, L2 19 |
| `Editor.u` | 37 | 23 | 14 | 0 | 0 | 0 | 23 | `ConeBuilder.Build+1` L3 22, L2 23 |
| `Engine.u` | 3842 | 3396 | 438 | 0 | 8 | 0 | 3396 | `AIController.AdjustDesireFor+2` L3 3395, L2 3396 |
| `Fire.u` | 0 | 0 | 0 | 0 | 0 | 0 | 0 | — |
| `GUI.u` | 800 | 719 | 81 | 0 | 0 | 0 | 719 | `ControllerButton.InitComponent+2` L3 718, L2 719 |
| `GamePlay.u` | 382 | 382 | 0 | 0 | 0 | 0 | 382 | `ACTION_ChangeLevel.GetActionString+2` L3 381, L2 382 |
| `IpDrv.u` | 111 | 83 | 28 | 0 | 0 | 0 | 83 | `BufferedTCPLink.DoBufferQueueIO+1` L3 82, L2 83 |
| `Sharpeffects.u` | 40 | 40 | 0 | 0 | 0 | 0 | 40 | `SE_AdvExplosiveMeshActor.CheckExplode+1` L3 39, L2 40 |
| `StargateContent.u` | 1834 | 1834 | 0 | 0 | 0 | 0 | 1834 | `ACTION_ActivateAstralSight.InitActionFor+3` L3 1833, L2 1834 |
| `StargateGame.u` | 3273 | 3256 | 17 | 0 | 0 | 0 | 3256 | `ACTION_ActivateHelmet.InitActionFor+1` L3 3255, L2 3256 |
| `StargateMenus.u` | 334 | 334 | 0 | 0 | 0 | 0 | 334 | `ChapterList.GetChapter+2` L3 333, L2 334 |
| `StargateVehicles.u` | 549 | 545 | 4 | 0 | 0 | 0 | 545 | `ACTION_CarpetBombing.InitActionFor+1` L3 544, L2 545 |
| `Stargateeffects.u` | 58 | 58 | 0 | 0 | 0 | 0 | 58 | `AcidBurn.PostBeginPlay+2` L3 57, L2 58 |
| `UDebugMenu.u` | 157 | 157 | 0 | 0 | 0 | 0 | 157 | `UDebugBlueLookAndFeel.Button_DrawSmallButton+1` L3 156, L2 157 |
| `UPreview.u` | 13 | 13 | 0 | 0 | 0 | 0 | 13 | `UPreviewMapListClient.BeforePaint+2` L3 12, L2 13 |
| `UWindow.u` | 731 | 731 | 0 | 0 | 0 | 0 | 731 | `UWindowBase.BuildObjectWithProperties+2` L3 730, L2 731 |
| `UnrealEd.u` | 0 | 0 | 0 | 0 | 0 | 0 | 0 | — |
| **total** | **12472** | **11687** | **673** | **0** | **112** | **0** | **11687** | 16 of 18 package(s) |
<!-- /generated:matching-baseline -->

## What a function is keyed by

`--compare` matches two packages' functions by a key, and the key has to name the *class* a function belongs to.
The export an inner function sits under is not always its class: **2,137 of this build's 12,472 `Function` exports
are declared inside a `state`**, whose outer is the state, not the class. Keyed by `(immediate outer, name)` — which
is what this tool did until now — those functions are keyed by a name like `Dead.BeginState`, and that name is
ambiguous in exactly two ways this build exercises:

- **inside one package** two classes that both have a `Dead` state write the same slot, so the second export
  overwrites the first: **319 of 12,472 functions were missing from the index** (`Engine.u` 21, `StargateContent.u`
  165, `StargateGame.u` 76, `StargateVehicles.u` 47, the rest scattered), and the baseline's denominator and `L3`
  ceiling were both 319 short of the corpus;
- **between two packages** a state whose name recurs in a different class reads as one shared function:
  `--pairs` over the 18 packages' **153 pairs** finds the old key claiming **201 shared functions in 21 pairs**, and
  every one of them is a name like `Dead.BeginState`, `Flying.BeginState` or `NormalFire.Fire` that two unrelated
  classes happen to spell the same way.

`export_path()` walks up to the outermost export instead, so a function is `Class.Function` or
`Class.State.Function`. The state stays in the key because `Actor.Dying.BeginState` and `Actor.BeginState` are
different bodies. Under the qualified path the same 153 pairs share **2 functions in one pair** — and those two are
real: **`ACTION_ChangeVehicleStatus` is declared in both `StargateContent.u` and `StargateGame.u`**, the only class
in this build compiled into two packages. `--check` drives `export_path` on a synthetic tree of two classes that both
declare a `Dead` state, and asserts that the *old* key collides on that same tree — a self-test the previous shape
would also have passed proves nothing.

Two consequences for the scoreboard. The denominator is now the export table itself (**12,472**, not 12,153) and the
ceiling **11,687** (not 11,368); and `--compare` across two different packages is a *negative* result by
construction, because the index holds **declarations**. A function a package inherits from another package is not in
its index, so `--compare Engine.u StargateGame.u` answers 0 shared and prints why: cross-package comparison can grade
a relocated class, never an inherited function. That is the honest shape of the oracle anyway — a rebuild is graded
against the package it rebuilds.

## The mutation control

Every package gets one: the tool flips a single bit inside an operand of the first function it can find one in — a
byte position no token starts at, so the opcode sequence cannot move — re-reads the payload and re-compares. The
expected result is exact (L3 loses that one function, L2 does not move) and the site is printed, so the claim can be
re-run. **16 of 18 packages are controlled**: `Fire.u` and `UnrealEd.u` hold no `Function` export at all (110 and
143 exports, none of them a function), so neither column has anything to grade there. That is also a correction to
this PLAN item's own text, which lists both among the small packages to chase first.

## The opcode control, which is what makes L2 a claim

The operand control says L3 is sensitive and L2 is not — but the mutation it makes is one the opcode sequence cannot
see **by construction**, so L2 had no mutation that could ever fail it. A column like that is a label. The other
mutation is an **opcode swap**: take a token whose opcode the table gives a same-shape partner (two rows with one
`pattern` differ only in what they do, so the stream keeps its length and every following token's offset), write the
partner over the opcode byte, re-read the payload and re-grade. `tools/uscript-dis.py --opcode-control SYSTEM
-o inventory` walks the packages in that order and stops at the first site that parses:

| what the swap must move | expected | measured |
|---|---|---|
| L3 | the function is no longer byte-identical | **0 on all 16** |
| L2 | the opcode sequence no longer matches | **0 on all 16** |
| token count | unchanged — the operand shape is the same | **unchanged on all 16** (5 to 230 tokens) |
| similarity | exactly `(n-1)/n`, one token of n | **exactly `(n-1)/n` on all 16**, to six places |

**16 of 18 packages are controlled, and none of the 16 by a refused swap**; `Fire.u` and `UnrealEd.u` hold no
`Function` export at all, so neither has a site — the same two the baseline table leaves blank. Five distinct pairs
carried the 16 rows, and every one is a swap a rebuild could really make: `JumpIfNot` → `Assert` (`0x07` → `0x09`,
7 packages), `Let` → `DynamicArrayElement` (`0x0f` → `0x10`, 5), `Return` → `GotoLabel` (`0x04` → `0x0d`, 2),
`LetDelegate` → `Let` (`0x45` → `0x0f`) and `Context` → `0x12`.

And **the two controls land on the same function in all 16 packages** — both take the first body with a candidate
site, and in this build that body has both an operand byte and a same-shape opcode. So the scoreboard's two columns
are now bracketed on one body, graded two ways: flip a bit of an operand there and L3 falls while L2 holds; swap its
opcode and both fall, with the ratio landing on `(n-1)/n`. `--check` reports that agreement rather than asserting it
(it is a property of this build, not of the method) and *does* assert the four numbers above on every row of
`inventory/opcode_compare.csv`.

## What the opcode control can reach

The swap above proves L2 is sensitive, on 16 packages and one body each. It does not say **how much of the build the
swap can reach**, and that is the question a rebuild's grade rests on: a body the control cannot mutate is graded by
a column nobody has ever made fail on it. The control's rule is `opcode_partners` -- two rows with one `pattern` --
and it read that rule off the **map** rows only, with a truthiness test on `pattern`:

    if row.get("source") == "map" and row.get("pattern"):    # the rule the package control shipped with

Two things are invisible to that test, and both are large. **The empty pattern is a shape**: `EndFunctionParms`
(`0x16`), `Nothing` (`0x0b`), `EndOfScript` (`0x47`), `True`/`False`/`IntZero`/`NoObject`/`BoolVariable` take no
operand at all, and those 13 rows carry **128,171** tokens -- `EndFunctionParms` alone is 68,548, one per argument
list. And **the `record` rows are opcodes too**: `0x70..0xff` and the `HighNative` band are named from the image's
own `GNatives` records with pattern `parms`, 151 rows carrying **49,100** tokens. Under the shipped rule the control
reached **293,276 of the corpus's 484,332 opcode tokens (60.55%)**.

`tools/uscript-dis.py --opcode-pairs SYSTEM -o inventory` widens the rule to every row whose pattern the walker can
read and grades **every pair**, not the first one a package offers. The swap is the same mutation; what changes is
that a row now exists for each of the 221 opcodes the table describes, with a verdict:

- `graded` -- the walk kept its token count, exactly one token moved, L3 and L2 both fell, similarity `(n-1)/n`;
- `refused` -- the stream no longer fits the new opcode (one row: `EndOfScript`);
- `extends` -- the walk is a different length, which is what a terminator swapped for a non-terminator does;
- `unused` -- the corpus never writes that opcode, so it can be neither graded nor missed;
- `unpaired` -- no row in the table has this operand shape, so **this build cannot swap it at all**.

That last verdict is the answer to the question the previous note left open, and the answer is a negative: the four
opcodes it named as having "no same-shape partner" -- `ArrayElement`, `LetBool`, `DynamicArrayElement` and the `Let`
beside them -- all sit in one five-member `expr,expr` group and all grade. Nothing in this build is unswappable for
want of a *partner* except the patterns that have one member: `Jump` (`bytes:2`), `Case`, `Iterator`, `New`,
`StringConst`, `UnicodeStringConst`, `DelegateFunction`, `LabelTable` and `DebugInfo`. Six of those nine are written
by the corpus (13,785 tokens), and each is alone in its structural class by construction -- a code offset, a
NUL-terminated string, a case table -- so no length-preserving partner exists to be named. **`EndOfScript` is the
other negative, and it is the interesting one**: it pairs with `Stop` (both operand-free), and every one of the
11,687 attempts refuses, because `0x47` is what ends the walk and `0x08` is not.

<!-- generated:opcode-pairs -->
**The L2 control reaches 458860 of the build's 484332 opcode tokens (94.74%): 174 of its 221 row(s) grade, and 47 do not.**
**182 of these rows are outside the rule `--opcode-control` shipped with** -- that rule pairs a `map` row with a non-empty pattern, so the operand-free opcodes (`0x16` `EndFunctionParms` alone is 68,548 tokens) and the `record` natives above `0x6f` were invisible to it, and it reached only 293276 of the 484332 tokens (60.55%). The shape is the same measurement in both cases; only the rule's reach differed.

| operand shape | opcodes | with a partner | graded | tokens | under a graded swap |
|---|---:|---:|---:|---:|---:|
| `index` | 7 | 7 | 7 | 133715 | 133715 |
| (operand-free) | 13 | 13 | 11 | 128171 | 116484 |
| `parms` | 144 | 144 | 121 | 44811 | 44811 |
| `expr,bytes:3,expr` | 2 | 2 | 2 | 31622 | 31622 |
| `expr,expr` | 5 | 5 | 5 | 29782 | 29782 |
| `bytes:2,expr` | 3 | 3 | 3 | 22421 | 22421 |
| `expr` | 4 | 4 | 3 | 21156 | 21156 |
| `index,parms` | 3 | 3 | 3 | 19349 | 19349 |
| `bytes:1,expr` | 2 | 2 | 2 | 10729 | 10729 |
| `index,expr` | 3 | 3 | 3 | 9466 | 9466 |
| `bytes:1` | 2 | 2 | 2 | 7669 | 7669 |
| `bytes:4` | 2 | 2 | 2 | 6546 | 6546 |
| `bytes:2` | 1 | 0 | 0 | 6315 | 0 |
| `str` | 1 | 0 | 0 | 5830 | 0 |
| `bytes:1,parms` | 16 | 16 | 4 | 4289 | 4289 |
| `case` | 1 | 0 | 0 | 1044 | 0 |
| `bytes:12` | 2 | 2 | 2 | 599 | 599 |
| `expr,bytes:2` | 1 | 0 | 0 | 358 | 0 |
| `expr,expr,expr` | 2 | 2 | 2 | 216 | 216 |
| `expr,expr,expr,expr` | 1 | 0 | 0 | 139 | 0 |
| `index,index,expr` | 1 | 0 | 0 | 99 | 0 |
| `index,expr,expr` | 2 | 2 | 2 | 6 | 6 |
| `bytes:13` | 1 | 0 | 0 | 0 | 0 |
| `labeltable` | 1 | 0 | 0 | 0 | 0 |
| `utf16` | 1 | 0 | 0 | 0 | 0 |
| **total** | **221** | **212** | **174** | **484332** | **458860** |

The rows that keep no grade, in the order of their population: `0x06 Jump` (unpaired), `0x0a Case` (unpaired), `0x11 New` (unpaired), `0x1f StringConst` (unpaired), `0x2f Iterator` (unpaired), `0x43 DelegateFunction` (unpaired), `0x47 EndOfScript` (refused) -- and 40 more the corpus never writes, so they can be neither graded nor missed.
<!-- /generated:opcode-pairs -->

## The cast axis

A `PrimitiveCast` (`0x39`) is the opcode a rebuild can get wrong without changing the shape of the code: its row in
the opcode table is `bytes:1,expr`, so the conversion's slot is a byte **operand** and the value it converts is a
child. `--compare` reads both and grades them apart -- the CSV it writes carries `casts`, `cast_slots_identical`,
`cast_exprs_identical` and `first_cast_diff` per function, so a rebuild that picks the wrong conversion (an L3 loss
with the opcode sequence intact) is not scored as a structural difference, and one that converts the wrong value
(an L2 loss) is.

The axis is validated on the shipped bytes, because a rebuild does not exist yet: `tools/uscript-dis.py
--cast-control` moves one conversion's slot, re-reads the payload and re-grades it, then does the same to the first
byte of the expression it converts. In **16 of the 18 packages** the slot mutation costs L3, keeps L2 and moves
**exactly one token -- the cast's own**, so a mutation that shifted a length instead of a slot could not pass; the
expression mutation costs L2 as well in 15 of the 16 and stops the walk in the sixteenth
(`Stargateeffects.u`, `AcidBurn.Timer+10`), which is the other honest answer. `Fire.u` and `UnrealEd.u` hold no
`Function` export at all, so there is nothing to control there -- the same two the baseline table leaves blank.

The census is the denominator a rebuild's cast column needs: **10,529 cast tokens in 2,618 of 11,687 walkable
payloads**, and the token total is not a new claim -- it is the sum `inventory/cast_operands.csv` already records,
counted by the same walker generator, and `--check` fails when the two disagree. Per package the count is in
`inventory/cast_compare.csv`, one control row each; the rarity end of it is `ByteToBool` (slot 59) with one token in
the whole build (`docs/NATIVE_VM.md`, *The cast index is one bytecode operand*).

## The 112 operator declarations

Before the scoreboard, 112 payloads read as a header-model defect: their payload does not start with the five-value
run that ends on the export's name. They are the **operator declarations** (`EqualEqual_KeyValuePair…`,
`Multiply_FloatColor`, `AddEqual_FloatFloat`). A `Function` serialises its `PreOperator`, `Operator` and
`PostOperator` names in front of the header when its flags say so: all 112 carry `Operator|PreOperator`
(`0x1000|0x2000`) in the `FunctionFlags` dword at `payload_end-4`, and 107 of the 112 are already rows in
`inventory/native_tokens.csv`. They carry no script for this project to match — the compiler expands the operator
at the call site — so they belong in a column of their own rather than in the negative. The flag test is applied
only to a payload the header refused; a payload that walks is a script whatever its flags say.

## Reading it

- **`Core.u` is 19 of 214, and that is not a defect**: 91 of its functions are natives with no body and 104 are
  operators. The engine's base classes are declared there and implemented in C++.
- **The script-bearing packages already sit at the top of their own export tables**: `StargateContent.u` 1,834 of
  1,834, `StargateGame.u` 3,256 of 3,273, `AmbientCreatures.u` 97 of 97. `Engine.u`'s 3,396 of 3,842 is the package
  `--compare` validated on.
- **`refused` is empty**: the walk closes every scripted payload (11,687 of 11,687), so `inventory/opcode_refusals.csv`
  is a header and `scripted` is the whole walkable set. The two negatives left are structural — 673 natives with no
  body and 112 operator declarations — not gaps in the reader.

## Reproducing

    make matching-baseline SG1_DUMP=~/nfs/sg1-dump
    python3 tools/uscript-dis.py --pairs ~/nfs/sg1-dump/Stargate_SG-1_-_The_Alliance-2005-12-15/System
    python3 tools/uscript-dis.py --opcode-control ~/nfs/sg1-dump/Stargate_SG-1_-_The_Alliance-2005-12-15/System -o inventory
    python3 tools/uscript-dis.py --opcode-pairs ~/nfs/sg1-dump/Stargate_SG-1_-_The_Alliance-2005-12-15/System -o inventory
    python3 tools/uscript-dis.py --check-doc docs/MATCHING.md     # in `make ci`; reads the artifact only

`inventory/matching_baseline.csv` is the artifact and the table above is generated from it (`--write-doc`);
`--check-doc` compares the page against the committed CSV, so the numbers here cannot drift from the row that
carries them.
