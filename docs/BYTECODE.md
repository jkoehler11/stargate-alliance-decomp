# The bytecode tables, read from the engine's own script walker

`PLAN.md` 3.7 asks for a disassembler and a comparator. The comparator needs a compiler (3.6); the disassembler
needs one thing: how many operand bytes each opcode has. That answer is in the image, not in a UE2 document — the
engine validates a script by walking it token by token and asserting `"Script serialization mismatch: Got %i,
expected %i"`, so the walk is the format. This page is that walk, transcribed, and it is generated from
`inventory/opcode_table.csv`.

    make opcode-table                                  # needs the dump; rewrites the CSV
    python3 tools/opcode-table.py --show               # the group table, printed
    python3 tools/opcode-table.py --check              # what `make ci` runs, no image needed
    python3 tools/uscript-dis.py --hex 040b47          # the disassembler that consumes it

## Two tables, not one

The walker is `0x000398e0` (1,373 bytes, `UnClass.cpp`) — the vtable `+0xa0` slot of `Struct`, `Function` and
`Class` (`docs/NATIVE_VM.md`). After reading one opcode byte it dispatches through **both** of these:

    0x000399f7  movzx ebp,BYTE PTR [ebp+0x39ec0]     ; opcode -> group   (72 bytes, opcodes 0x00..0x47)
    0x000399fe  jmp   DWORD PTR [ebp*4+0x39e44]      ; group  -> handler (31 entries)

* the **jump table** at `0x39e44` has **31 entries**, every target inside the walker (`0x399a7..0x39e38`);
* the **byte map** at `0x39ec0` covers **opcodes `0x00..0x47`** and its 72 values are all `< 31` — every opcode
  names a group, every group is named at least once. The two tables are adjacent: `0x39e44 + 31*4 == 0x39ec0`.

**This corrects the previous reading.** `inventory/opcode_dispatch.csv` (PR #88) reported "31 opcodes, `0x00..0x1e`,
one handler each", found by reading the jump table and stopping at the first entry that left the walker. The count of
31 was right — it is the count of *groups* — but the indices in that file are group numbers, not opcodes: opcode
`0x47` is group 5, and group 30 alone covers eleven opcodes, all of which the walker rejects. Reading the table as
opcode-indexed makes `0x1f..0x47` look unmapped when they are exactly what the byte map is for.

Above `0x47` there is no table entry, so the walker's two range checks handle everything else:

| opcodes | rule |
|---|---|
| `0x00..0x47` | through the byte map and the jump table |
| `0x48..0x5f` | the bad-token group (`cmp ebp,0x47` / `ja` before the table lookup) |
| `0x60..0x6f` | one operand byte, then a nested walk until `0x16` (EX_EndFunctionParms) |
| `0x70..0xff` | no operand byte, then the same nested walk |

## The groups

| group | handler | opcodes | operand stream | UELib token |
|---|---|---|---|---|
| 0 | `0x00039c0e` | 00 01 02 20 29 | `bytes:4` | LocalVariable, InstanceVariable, DefaultVariable, ObjectConst, NativeParm |
| 1 | `0x00039dec` | 04 | `expr` | Return |
| 2 | `0x00039d12` | 05 | `bytes:1,expr` | Switch |
| 3 | `0x00039a14` | 06 | `bytes:2` | Jump |
| 4 | `0x00039cce` | 07 | `bytes:2,expr` | JumpIfNot |
| 5 | `0x00039e38` | 08 0b 16 17 25 26 27 28 2a 2d 30 31 47 | `—` | Stop, Nothing, EndFunctionParms, Self, IntZero, IntOne, True, False, NoObject, BoolVariable, IteratorPop, IteratorNext, EndOfScript |
| 6 | `0x00039d98` | 09 18 | `bytes:2,expr` | Assert, Skip |
| 7 | `0x00039d26` | 0a | `bytes:2,case` | Case |
| 8 | `0x00039d4e` | 0c | `labeltable` | LabelTable |
| 9 | `0x000399a7` | 0d 0e 37 | `expr` | GotoLabel, EatString, DynamicArrayLength |
| 10 | `0x00039b55` | 0f 10 14 1a 45 | `expr,expr` | Let, DynamicArrayElement, LetBool, ArrayElement, LetDelegate |
| 11 | `0x00039b3d` | 11 | `expr,expr,expr,expr` | New |
| 12 | `0x00039af0` | 12 19 | `expr,bytes:3,expr` | ClassContext, Context |
| 13 | `0x00039de1` | 13 2e 36 | `bytes:4,expr` | MetaClassCast, DynamicCast, StructMember |
| 14 | `0x00039a9b` | 1b 38 | `bytes:4,parms` | VirtualFunction, GlobalFunction |
| 15 | `0x00039a70` | 1c | `bytes:4,parms` | FinalFunction |
| 16 | `0x00039b79` | 1d | `bytes:4` | IntConst |
| 17 | `0x00039b96` | 1e | `bytes:4` | FloatConst |
| 18 | `0x00039bb3` | 1f | `str` | StringConst |
| 19 | `0x00039e04` | 21 44 | `bytes:4` | NameConst, DelegateProperty |
| 20 | `0x00039c29` | 22 | `bytes:12` | RotationConst |
| 21 | `0x00039c6e` | 23 | `bytes:12` | VectorConst |
| 22 | `0x00039cb3` | 24 2c | `bytes:1` | ByteConst, IntConstByte |
| 23 | `0x00039ce4` | 2f | `expr,bytes:2` | Iterator |
| 24 | `0x00039dc1` | 32 33 | `bytes:4,expr,expr` | StructCmpEq, StructCmpNe |
| 25 | `0x00039be0` | 34 | `utf16` | UnicodeStringConst |
| 26 | `0x00039a05` | 39 | `bytes:1,expr` | PrimitiveCast |
| 27 | `0x00039b49` | 40 41 | `expr,expr,expr` | DynamicArrayInsert, DynamicArrayRemove |
| 28 | `0x00039a1a` | 42 | `bytes:13` | DebugInfo |
| 29 | `0x00039ac1` | 43 | `bytes:4,expr` | DelegateFunction |
| 30 | `0x00039e1f` | 03 15 2b 35 3a 3b 3c 3d 3e 3f 46 | `bad-token` | BadToken, LineNumber, RangeConst, ReturnNothing, DelegateCmpEq, DelegateCmpNe, DelegateFunctionCmpEq, DelegateFunctionCmpNe, EmptyDelegate |

Eleven of the 72 mapped opcodes land on group 30, the handler that logs `"Bad expr token %02x"` and returns, and 24
more (`0x48..0x5f`) reach it by range: **35 of 256 opcodes are refused**. Group 5 is the other extreme: thirteen
opcodes with no operands at all, handled by a bare `return` of the opcode.

## How a pattern was read

Each group's pattern is transcribed from its own handler body, and the handler address is in the CSV so the
instructions can be re-read (`ExportAt.java` or any disassembler on the image). Two things carry the widths:

* the **archive primitives** the handler calls — `0x00037040` writes one byte, `0x00037080` two, `0x00037110` four
  (`docs/NATIVE_VM.md`), `0x00037170` a float32; references go through archive vtable `+0x18` (object) and `+0x1c`
  (name), each consuming four bytes of the script;
* the **position increments** on the walk's own cursor (`[esi]`/`esi`), which is what the disassembler copies.

The cursor walks the object's script buffer — `+0x48` bytes, `+0x4c` length — which is the same pair the interpreter
executes, so these advances are the stored bytecode's operand widths and not a property of some other encoding.

Three terminators are part of the format and are encoded as pattern items rather than plain counts: a parameter list
ends at `0x16`, a case list ends at a `0xffff` value, and a label table ends at a zero dword. Strings are
NUL-terminated (ANSI for `0x1f`, UTF-16 for `0x34`), so their length is data, not a field.

## Cross-check against the reference reader

`tools/uelib` (pinned `8236c39`) states the same mapping independently, for the whole UE2 family
(`src/Branch/DefaultEngineBranch.cs::BuildTokenMap`), and the two agree on most of the table. The agreements worth
naming, because they were not used to build it: `0x11 New` has **four** children (UELib's `NewToken` reads Outer,
Name, Flags, Class), `0x2f Iterator` is an expression then a code offset, `0x42 DebugInfo` is three int32s then a
byte, `0x40`/`0x41` are three children each, and `0x47 EndOfScript` has none — which is why every payload's epilogue
ends with it.

The disagreements are the interesting part, and they are all the same shape: tokens UELib knows and this build's
walker refuses.

| opcode | UELib (UE2 path) | this image |
|---|---|---|
| `0x15` | `LineNumber` — "attested in UE2 builds … but not in any UE1 or UE2.5 builds" | rejected: no line-number token |
| `0x35` | `RangeConst`, only when `Build == UE2` | rejected, so this build is not that generation |
| `0x3a` | `ReturnNothing` | rejected — and its payloads end `04 0b 47`, not `3a` |
| `0x3b..0x3f` | `DelegateCmpEq`, `DelegateCmpNe`, `DelegateFunctionCmpEq`, `DelegateFunctionCmpNe`, `EmptyDelegate` | all rejected: the walker has no delegate tokens |
| `0x46` | `BadToken` | rejected too |

So the table describes a UE2.5-era build without delegates and without per-line debug tokens — which is consistent
with the payload epilogue below and is the first *format-level* statement about which branch of the compiler
produced these packages.

## The disassembler

`tools/uscript-dis.py` applies the CSV and prints the token tree. It refuses rather than guessing: an opcode the
walk would reject, a string that is not NUL-terminated, a parameter list that never reaches `0x16`, or an operand
that runs off the end all end the run with the offset and the reason, the same way the walker's own assert would.

    $ python3 tools/uscript-dis.py --hex 040b47
    0000  Return                                        2 byte(s)
      0001  Nothing                                       1 byte(s)
    0002  EndOfScript                                   1 byte(s)
    2 token(s), 3 byte(s)

That three-byte stream is not a contrived example: it is the tail of most shipped functions (see U13 below), which is
why it is the first self-test. `make ci` runs `--selftest` — eleven streams that must split and four that must be
refused — so a change to the table that breaks a width fails the gate instead of silently disagreeing with the
engine.

## The payload's tail, from `UFunction::Serialize`

`UFunction::Serialize` is `0x00039f10` (283 bytes), and its whole serialized tail is readable from the body. It calls
`UStruct::Serialize` first (`0x000392b0`), then writes, **version-gated on the archive version field**:

| in the payload | field | gate |
|---|---|---|
| `u16` | `+0x84` (the native token) | always |
| `u8` | `+0x88` (operator precedence) | always |
| `u32` | `+0x80` — `FunctionFlags` | always |
| `u16` | `+0x86` — `RepOffset` | only when `FunctionFlags & 0x40` (Net) |

and the build's version is **130**, so the three gates written as `if (Arch.Version < 0x40) write …` — `paramsSize`
(`+0x8a`), `paramsCount` (`+0x89`) and `returnValueOffset` (`+0x8c`) — **never fire**: those fields are *computed at
load* by walking the `Children` chain (the loop at the end of the body), not stored. That is why a `UFunction`
payload here ends in **7 bytes**, or **9** for a network function, and it is the same gate UELib's reader applies
(`PackageObjectLegacyVersion.Release64 = 64`), so its field list agrees with the image for this build.

Measured over `Engine.u`'s 3,842 `Function` payloads (`python3 tools/uscript-dis.py --payloads System/Engine.u`):

* the script's epilogue — `04 0b` (`Return`, `Nothing`) plus `47`, the `'G'` marker `UStruct::Serialize` appends to
  a script it stores uncompressed — sits at `payload_end-10..end-8` in **3,197** payloads, and in **0** of those is
  the `Net` flag set;
* it sits at `payload_end-12..end-10` in **201**, and in **all 201** the `Net` flag *is* set, i.e. the two extra
  bytes are exactly the `RepOffset` the body writes;
* **444** payloads carry the epilogue at neither position (next section);
* `FunctionFlags` at `payload_end-4` equals `inventory/natives.csv`'s `function_flags` for **439 of the 444**
  exports joinable by (class, function) — the field is identified by content, not by position alone.

**And every field in the tail is now checked by content, not just by position.** `--payloads` takes a dump
directory and compares the `u16` at `payload_end-7` (`payload_end-9` when `Net`) with the index the *script*
declares (`native(1234)`), from `inventory/natives.csv`'s `native_token`: over all 18 packages (12,472 `Function`
payloads, 797 joinable) the token equals the declared index in **791**, 5 carry a token where the script declares
none and 1 the other way — **0 disagree in both**. `FunctionFlags` matches in **790 of 797**. That is what makes
`u16` `NativeToken` + `u8` `OperPrecedence` a measurement rather than a reading of the body: the value is the same
number in two unrelated places.

    $ python3 tools/uscript-dis.py --payloads System      # per package, then the corpus totals
    package                fn   join  flags  token   tr7   tr9  none    hdr  size16 walk16  sized
    ...
    TOTAL               12472    797    790    791 11379   337   756  11687     672   1975    674

The epilogue counts move slightly with the corpus: **11,379** payloads carry it 7 bytes from the end, **337** 9
because they are Net functions, and **756** carry it at neither position — the native/scriptless functions, which
is why `natives.csv`'s 797 joinable are almost exactly that set. What the tail does *not* give is where the script
starts, because the header in front of it serialises object references and a name whose widths differ per payload.

## The shipped scripts, as disassembled

With the start at `name_end + 16` the disassembler runs on real functions rather than on hex literals:

    $ python3 tools/uscript-dis.py --script DUMP/System/Core.u Object BeginState
    Object.BeginState: payload 34 bytes, script 24..27 (3 bytes), header 24 bytes
      0000  Return                                        2 byte(s)
        0001  Nothing                                       1 byte(s)
      0002  EndOfScript                                   1 byte(s)

`--script PKG CLASS FUNCTION` prints the token tree with offsets, and `--usage` runs the whole corpus:

    $ python3 tools/uscript-dis.py --usage DUMP/System -o inventory
    11687 scripts with an epilogue, 11602 walk clean end to end
    op     name                         tokens  payloads
    0x47   EndOfScript                   11602     11602
    0x0b   Nothing                       14011     11562
    0x04   Return                        19124     11550
    0x16   EndFunctionParms              67459      8783
    0x01   InstanceVariable              67052      8215
    0x07   JumpIfNot                     17220      6038
    ...

**11,602 of the 11,687 scripted payloads walk clean end to end (99.3%)**, and the census over them covers **153
opcodes** — `inventory/opcode_usage.csv`, regenerated by `make opcode-usage`, sorted by opcode so a reader can look one
up while the console listing is sorted by use.

**What the 85 that still refuse are made of** (they were 4,022 before the operand correction): `LabelTable` (`0x0c`)
whose table does not terminate 16, then single opcodes hitting the walker's bad-token case — `0x52` 6, `0x54` 5,
`0x49` 5, `0x03` 4, `0x4c` 3, `0x2b` 3 — and 4 parameter lists ending on `0xff`. That is a per-opcode tail, not a rule,
and it is 0.7% of the corpus.

**Getting there was one table correction, not per-opcode work.** Five groups' handlers read an `int` and the table
recorded that as four fixed operand bytes; the payloads say the operand is the archive's **compact index** (bit 7 sign,
bit 6 continuation, six bits then seven per byte). With groups 0 (`LocalVariable`, `InstanceVariable`,
`DefaultVariable`, `ObjectConst`, `NativeParm`), 13 (`MetaClassCast`, `DynamicCast`, `StructMember`), 14
(`VirtualFunction`, `GlobalFunction`), 15 (`FinalFunction`) and 19 (`NameConst`, `DelegateProperty`) reading an index,
the corpus went from **3,444 to 11,602** scripts walking — +237%. The measurement that found it was per-opcode: hold
the table fixed, change one group to `index`, and count. Changing `0x03` alone, by contrast, rescues at most 75 of its
690 payloads, which is what a wrong width *there* would have looked like if the desync had been that token's fault.

## What the operands name

`--resolve` decodes every operand a rule calls an index and asks the package what the value lands on — an object (an
export or an import) or a row of the name table:

    $ python3 tools/uscript-dis.py --resolve DUMP/System
    11602 scripts walked clean; reference operands resolved against the package tables
      FinalFunction        5424 operands: Function 5424
      DynamicCast          4153 operands: Class 4153
      MetaClassCast         129 operands: Class 129
      ObjectConst          1449 operands: Class 1305, Texture 123
      InstanceVariable    67052 operands: ObjectProperty 29226, FloatProperty 9021, StructProperty 6889
      LocalVariable       58813 operands: ObjectProperty 22045, IntProperty 12373, StructProperty 7917
      StructMember         5039 operands: FloatProperty 1870, IntProperty 1556
      VirtualFunction     13398 operands: NAMES:a function in the package 10405, ObjectProperty 4370
      GlobalFunction        109 operands: NAMES:a function in the package 109
      NameConst            2849 operands: NAMES:a name 2094, ObjectProperty 936

**The same compact encoding indexes two different tables**, and only the opcode says which: the variable, cast and
final-call tokens carry an **object reference** (a `Function` 5,424 times out of 5,424, a `Class` 4,153 of 4,153, a
property for every variable token), while `VirtualFunction`, `GlobalFunction` and `NameConst` carry a **name-table
index** — 10,405 of the 13,398 virtual-call operands name a function the package declares, and all 109 global calls do.
A byte stream cannot make that distinction by width, which is why "which object a `0x13` cast names" sat unmeasured:
it is a different question per opcode, and the answer is a table.

## The opcodes above `0x5f` carry the native index itself

The table names nothing above `0x5f` (UELib stops there), so the walker only had a shape for them: `0x60..0x6f` take
one byte and then a parameter list, everything above `0x6f` a parameter list alone. That shape is a native call, and
the token *is* the native's index -- the number `GNatives` is indexed by, with no arithmetic on it:

    python3 tools/uscript-dis.py --native-calls DUMP/System -o inventory

over the same 11,602 scripts that walk clean writes `inventory/native_opcodes.csv` (206 rows:
`opcode,operand,index,sites,resolved,param_fit,body_name,body_match,native,declared_in,declared_params,args`), where
the index is the opcode itself below `0x100` and two bytes at or above it (`0x60 + (n >> 8)`, then the low byte), and
`body_name` is what the image's own `GNatives` record spells for the slot's body.

* **48,422 native-call tokens**: 44,174 in the direct form over **120 opcodes**, and **4,248 in the extended form**,
  every extended site naming an index of 256 or more -- exactly where the one-byte form cannot reach (it spans
  `112..255`, which is where the declared band sits).
* **The token is the index, and the image says so twice.** For the body at the token's own `GNatives` slot, the
  image's `.data` record (`{slot, intUObjectexec<Function>}`) already carries the name: **111 of the 120 direct opcodes
  spell `exec<Function>` for the declaration at that exact index, 38,950 sites**, and the other nine differ only in
  spelling (`execConcat_StringString` where the script declares `Concat_StrStr`). Under this rule **42,056 of the
  48,422 sites name a `native()` declaration on the caller's own super chain**, against **5,864** under the
  `op - 0x70` reading this tool shipped with first.
* **Arity agrees once the index is the token.** `0xe7` is slot 231 = `Object.Log` and 1,072 of its 1,186 sites pass
  **one** argument; `0x77` is slot 119 = `Object.NotEqual_ObjectObject`, 5,368 sites, every one with two; `0xe1` slot
  225 `Object.VSize` 372/372 with one; `0xc3` slot 195 `Object.FRand` 324/324 with none. The earlier reading had the
  one-argument `Log` and the two-object `NotEqual` swapped, which is why its arity never fitted.
* **There is no operator band below 96.** The first census read these tokens as slots `0..95` and reported 38,143
  sites that "name nothing": as slots `112..207` they are all declared. `op - 0x70` subtracts the same constant the
  two-byte form adds, and nothing in the build numbers natives from 0.
* **`0xfe` is `Name` equality, not rotator.** Slot 254 = `Object.EqualEqual_NameName`, 414/414 with two arguments --
  the construct is `Tag == 'Foo'`, which is why it outnumbers every other comparison in the shipped script (the
  earlier reading called it `EqualEqual_RotatorRotator`, slot 142, by the same offset).
* **What does not resolve is the classes, not the numbering.** 6,366 sites (13%) name no declaration on the caller's
  chain: 1,480 are extended calls into engine classes the script half never declares (`intUCanvasexecDrawText` 352,
  `execStrLen` 120, `execDrawActor` 8 -- Canvas draws through its own natives), and the rest are callers whose class
  the super map does not carry. The body is still named by the image's record, so the index holds; the declaration is
  what is missing.

**What it rules out.** Both readings this page carried -- an operator token whose operand stream is not a parameter
list, and two numbering schemes meeting at 96 -- are wrong. The stream is a parameter list, and the build has one
numbering: the token is `GNatives`' own index from 112 (`Object.Concat_StrStr`) upward, and the two-byte form exists
only because one byte cannot spell 256.

## The calls the scripts make

`--calls` walks the corpus and records every call site, resolving each one as far as its operand rules allow:

    $ python3 tools/uscript-dis.py --calls DUMP/System -o inventory
    11687 scripts with an epilogue, 11602 walked; 18931 call sites
      static (FinalFunction): 5424, of which the target is a Function: 5424
      by name (VirtualFunction, GlobalFunction): 13507
        on the caller's chain: 7382 -- 5136 in its own class, 2246 on an ancestor (top: Engine.Actor 305, ...)
        by the corpus alone: 2712 site(s) reach the one class that declares the name (1080 names), top:
          ShouldCrouch -> Engine.Pawn 63, AddMenuItem -> UWindow.UWindowPulldownMenu 58, ...
        decided by the receiver: 3413 site(s) across 736 names, top: SetText 156 over 5 classes, ...

**5,424 static edges, every one of them naming a `Function` export or import**, plus **13,507 dynamic call sites**
whose target is a name rather than an object. A by-name target is first resolved along the **caller's super chain, in
the package that declares each ancestor** -- the earlier census only asked its own package, which is why a parent's
function looked unresolved. **7,382 of the 13,507 (55%) name a function declared on the chain: 5,136 on the caller's
own class and 2,246 on an ancestor, 305 of those `Engine.Actor`, 244 `Engine.Pawn`, 187 `Editor.BrushBuilder`** --
so a by-name call is not necessarily to a script function of this build at all, and a rebuild has to carry the
ancestor's declaration too. (This page wrote **9,628** for that group until the corpus-wide pass below; its own two
numbers, 5,136 + 2,246, are 7,382, and 13,507 - 6,125 is the same 7,382.)

**The chain is only the first of two lookups, and the second one does not need the receiver.** A by-name operand is an
`FName` and `ProcessEvent` finds the function by walking the *receiver's* class, so a site inside a class that does
not declare the name is not a broken site -- it is a call on another object. The corpus answers most of the rest
without knowing which object: of the **6,125** sites the chain cannot place, **2,712 (44%) across 1,080 names reach a
function exactly one class in the corpus declares** -- `ShouldCrouch` -> only `Engine.Pawn` 63, `AddMenuItem` ->
only `UWindow.UWindowPulldownMenu` 58, `SetPosImmediate` -> only `StargateGame.UIWidget` 51,
`GetLocalPlayerController` -> only `Engine.LevelInfo` 40, `PostAIMsg` -> only `Engine.Controller` 40 -- so whatever
the receiver is, the call can reach exactly one implementation. A declaration inside a `State` counts against the
class that owns the state, because the state compiles into that class's chain (6 of the 2,712 resolve that way).

**The other 3,413 sites over 736 names are declared by more than one class, and only the receiver decides.** `SetText`
156 sites over 5 classes (GUI's, UWindow's, StargateGame's), `SetFont` 139 over 4, `GetValue` 56 over 10 (sliders,
edit boxes, a playlist parser), `WhatToDoNext` 52 over 16, `ClientMessage` 48 over 3 (Pawn, PlayerController,
Security). That residue is not a naming gap: every one of the 1,816 distinct names is declared somewhere in the
corpus, and a rebuild has to keep every declaration a name has, because the call site cannot say which one runs.

`inventory/script_calls.csv` is the table: package, class, function, token, dispatch, target, target class,
`resolved_in` (`package`, an ancestor's `Pkg.Class`, or `corpus:Pkg.Class` for the one-class lookup),
`declaring_classes` (how many classes in the corpus declare that name) and `only_class` (that class when there is
exactly one) -- regenerated by `make script-calls`. The static column is the part a reader can rely on; the dynamic
column says where to look, not what runs.

## Comparing two packages: the L2/L3 comparator

`--compare` is the comparator half of PLAN 3.8, and it needs no compiler: it takes two packages and reports, per
function, whether a rebuild is the same bytes (**L3**), the same opcode sequence (**L2**), or neither.

    $ python3 tools/uscript-dis.py --compare DUMP/System/Engine.u DUMP/System/Engine.u
    Engine.u vs Engine.u
      3821 functions in both, 0 only in the first, 0 only in the second, 469 not disassembled
      byte-identical (L3): 3352; opcode-identical (L2): 3352; mean opcode similarity: 100.00%

Comparing a package with itself is the wiring test: 3,352 of the 3,821 shared functions compare identical and the 469
that do not are the ones with no script to compare (the natives and the refusals). A one-byte mutation inside an
operand is then caught as what it is — **byte-identical 3,351, opcode-identical 3,352**, i.e. the opcode sequence still
matches while the bytes do not, which is exactly the distinction the three tiers exist for.

**What this leaves of 3.8** is the oracle side: to score a rebuild, something has to produce one, and that is `ucc`.
The comparator is now the part that does not wait.

## The switch operand is a width, and zero is a wide string

The `0x05` byte was the last unnamed operand on this page, and it is not a case count: it is **the number of bytes
the case comparison reads**, with **zero meaning the value is a wide string**. Two independent readings agree.

**The image's interpreter.** `GNatives` slot 5 holds the switch's own body (`0x0004ca20`, 348 bytes, `Native_5` in
the symbol map) and it reads the operand itself instead of letting the walker hide it: one byte, then the switch
expression evaluated into a 12-byte result buffer, then per case a 16-bit offset, a case value expression into a
second buffer, and a comparison. That comparison is `FUN_00036bd0` -- `repz cmpsb` over its third argument, no
locals, a bare `ret` -- so the byte's value is a byte count. At zero the body takes the other arm, calls `__wcsicmp`
(`0x00036d00`) on the same two buffers, and only then destroys them (the two teardown calls before `ret` run **only**
when the byte is zero, which says the same thing a second way: the buffers are `FString`s exactly when the width is
zero).

**The corpus.** `make switch-operands` (`tools/uscript-dis.py --switches`; rows in `inventory/switch_operands.csv`)
walks every script that disassembles -- 11,602 of them -- and finds **105 switch sites carrying three widths**:

| width | sites | case values are built from | cases per switch |
|---|---:|---|---|
| 0 | 7 | `StringConst` 16 (one site, `UWindowHTMLTextArea.GetHexDigit`) | 0..16 |
| 1 | 78 | `ByteConst` 273 | 1..14 |
| 4 | 20 | `IntConstByte` 40, `NameConst` 8, `InstanceVariable` 8 | 0..17 |

The split is the check. A one-byte value's cases are **all** `ByteConst` (273 of 273) and `ByteConst` appears under
no other width, while an int's cases are the compact int tokens (`IntConstByte`, `IntZero`, `IntOne`). The width is
therefore a property of the value's type as the compiler saw it, not of the case list -- which is why the
"case-count byte" reading the opcode table carried until now could not be right: `Engine.IMusicActor.
ConvertStateToTransitionType` switches on a byte with four cases and its operand is `1`, and
`UWindowHTMLTextArea.GetHexDigit` switches on a string with sixteen `StringConst` cases and its operand is `0`.

**Residue, named rather than explained.** Six of the seven zero-width sites (`UDebugMenu` and `UWindow`
`Combo_SetupSizes`, `Checkbox_SetupSizes`, `Editbox_SetupSizes`) are followed by **no case list at all** -- no
`0xffff` terminator, no case, which a switch cannot be -- and `StargateMenus.ButtonClick`'s four-width site ends the
same way. They are rows in the artifact with `ended=end`: the token the table calls `0x05` is doing something this
page does not yet describe, and the census records that instead of guessing at it.

## Not measured

* **Where a script starts inside a `Function` payload (U13).** The tail is solved (above), the header's encoded part
  is a fixed size, and the offset is now measured **three ways** by `python3 tools/uscript-dis.py --start System`:

  1. **The payload's floor.** An empty body still costs the epilogue and the trailer, so the smallest payload after
     `FriendlyName` is the fixed run plus 3 plus 7 — or plus 9 for a Net function. Over 12,360 payloads that floor is
     **25 bytes**, with the two spikes at **26** (1,380 payloads) and **28** (801): `16 + 3 + 7` and `16 + 3 + 9`.
     A 12-byte run would put the floor at 22, where nothing is ever seen.
  2. **How deep the walk from `name_end + 16` gets.** A wrong start desynchronises within a token or two; this one
     walks to the epilogue. Of the payloads whose epilogue is at the 7-byte trailer position, **11,269 walk all the
     way**, 28 stop between 16 and 63 bytes, 36 between 64 and 255, 11 beyond 256 and 6 between 4 and 15 — the
     remainder is the opcode table's coverage, which the `index` correction above cut from 4,022 refusals to 81.
  3. **What the dword 16 bytes past the name is.** It is what a `ScriptStorageSize` field would carry, and it is
     **not that**: against the physical script length it is larger in **9,298** payloads and equal in **2,052**, never
     smaller. So the fourth word of the run is some other field, and a size field is not needed anyway — the loader
     knows the payload's `SerialSize`, which fixes the script's end without reading a length.

  **So the script starts 16 bytes past `FriendlyName`, and every count in the earlier sessions can be read against
  that.** What the four words of the run are is now the open question, together with the table's unnamed opcodes: the
  header's encoded part is a fixed four values for a `Class` and five for a `Function` whatever their child count
  (`--header` reports the per-position classification), which is why the per-child `Children`-chain model this
  document used to propose is dead — and its +1/+2/+3 residuals were the compact encoding's widths.
* **Operand semantics -- measured, except where the receiver decides.** What the reference operands *name* is
  measured per opcode (`--resolve`, above): objects for the variable, cast and final-call tokens, the name table for
  the virtual and global calls and `NameConst`. The `0x05` switch's byte is settled above (the compared value's
  width), and the by-name operands are now placed twice over: 7,382 on the caller's chain and 2,712 by the one class
  in the corpus that declares the name. What is left is **3,413 sites over 736 names that more than one class
  declares** -- placing one of those needs the *static type* of the receiver, which the operand stream does not carry
  (a variable token carries its declared class, but the receiving expression is not recorded beside the call).
* **`0x60..0xff` -- measured, and not reserved.** The walker's rules for them are native calls and the token is the
  slot itself, one byte below `0x100` and two bytes at or above it: 48,422 sites, 42,056 of them naming a `native()`
  declaration on the caller's chain, and the body at each direct opcode's `GNatives` slot spelled `exec<Function>` by
  the image's own record for 111 of the 120 opcodes. The residue is 6,366 sites whose caller's chain or class the
  inventory does not carry, and the extended calls into classes the script half never declares (Canvas). Arity is
  constant per opcode and agrees with the declaration. `--native-calls`, above.
* **Compressed scripts — measured negative.** `UStruct::Serialize` appends a `'G'` marker when a script is stored
  compressed. Every one of the **2,319 `Class` payloads** was tried at five offsets after `FriendlyName`, with four
  leading-byte skips and both zlib framings, looking for an inflate that starts with a `Return` opcode or ends on the
  marker: **none decodes**. A class carries no epilogue either (2,319 of 2,319), so a class's script is not stored the
  way a function's is — this table describes the uncompressed walk, and that is the whole of it.
* ~~**Whether every shipped script walks clean.**~~ **Measured** (`make opcode-refusals`): 11,602 of 11,687 payloads
  walk clean and the **85 that do not are now rows** — package, class, function, offset, opcode and case — in
  `inventory/opcode_refusals.csv`. The engine does not assert on these, so they are the table's gaps and not the
  corpus's damage; two families account for 16 of them (below).
## The 85 refusals, one row each (measured 2026-11-09)

The section above counts the corpus and names the tail of failures in a sentence. `tools/uscript-refusals.py`
(`make opcode-refusals`, `inventory/opcode_refusals.csv`, `--check` in `make ci`) holds every payload with an
epilogue against the table and writes the row a fix needs: package, class, function, script length, the offset the
walk stopped at, the opcode there, the refusal case, and the walker's own sentence.

**11,602 scripts walk clean and 85 refuse**, which settles the "not measured" item below — it is the same 11,602/85
the payload census reported, now with rows.

| case | refusals | what stops |
|---|---:|---|
| `bad-token` | 46 | an opcode the table calls the walker's bad-token case |
| `parameter-list` | 20 | a `0x16`-terminated list that never reaches its terminator |
| `label-table` | 16 | a `0x0c` label table whose entries do not reach a zero entry |
| `operand-bytes` | 2 | an opcode with fewer operand bytes left than the table says |
| `string` | 1 | a string operand with no NUL |

| package | refusals |
|---|---:|
| `StargateMenus.u` | 27 |
| `Engine.u` | 23 |
| `StargateGame.u` | 11 |
| `UWindow.u` | 11 |
| `GUI.u` | 5 |
| `StargateContent.u`, `UDebugMenu.u` | 3 each |
| `GamePlay.u`, `IpDrv.u` | 1 each |

**They are not spread evenly through the scripts.** The median script is 168 bytes and the median refusal is at
**71% of its own script**; 40 of the 85 stop inside the last 16 bytes, none inside the last 2, and the smallest
remainder is 4. So the walk consumes most of a body and dies on a token near the end. The obvious hypothesis is
therefore that the walker's range is one token too long — **and that is wrong**: `--tail-cut` re-walks each refused
script with its last 3, 7 and 11 bytes removed and rescues **0, 0 and 4**, the four being the rows whose failing
token *is* the bytes cut. The offsets stand.

**Two families account for 16 of the 85, and one of them is a single function.** All 13 label-table refusals that
stop with exactly **11 bytes left** are `StargateMenus.u`'s `InternalOnXControllerEvent` — 13 scripts, one function,
one token shape, one fix. Its other two refusals are at different offsets, so the function is the corpus's worst
single case (15 of 85). The other family is `InternalOnChange`, three 11-byte scripts that stop at offset 5 with 6
bytes left, and there the failing byte differs between them — `0x42`, `0x49`, `0x54` — which is what a bad operand
width in the token *before* offset 5 looks like, not three separate opcode gaps.

The remaining 69 are a long tail: 45 distinct function names, no offset repeated more than three times, and the
`bad-token` rows alone spread over 37 distinct opcodes with 1–6 rows each. That is the honest shape of the residue —
a per-function tail, which is why the table correction that took the corpus from 3,444 to 11,602 was worth the
session and the last 85 are not one more such correction.
