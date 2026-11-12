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
        naming a declaring class: 13258 of 13507 (98.2%)
        on the caller's chain: 8321 -- 5778 in its own class, 2543 on an ancestor (top: Engine.Actor 337, ...)
          of those, 223 name a function only a *state* of that class declares (the class owns the state)
        by the corpus alone: 2290 site(s) reach the one class that declares the name (914 names), top:
          ShouldCrouch -> Engine.Pawn 63, AddMenuItem -> UWindow.UWindowPulldownMenu 58, ...
        on the receiver's chain: 2641 (624 names) -- the expression in front of the call types to a class
          (267 distinct; 582 of the 2,641 had a local in front, typed from the class's own ScriptText)
          of those, 33 place because the receiver's type names a class in another case (pawn 14, GameHUD 4, ...)
        below the receiver's chain: 6 reach exactly one descendant that declares the name, top:
          IsUnconscious -> StargateGame.BotEnemy 3, GetAggroLevel -> StargateGame.BotEnemy 3
        decided by the receiver: 249 site(s) across 111 names, top: ClientMessage 27 over 3 classes, ...
          an object expression with no recorded type: 181 -- 28 of them a local, 153 not
          an expression that typed to a class whose chain declares no such name: 52, of which 6 reach exactly
            one descendant that declares it; the other 46 are a dispatch the static type cannot decide
          a completed value in front (a self-call, or a receiver the walk did not reach): 22
          why an expression in front names no class: a literal or cast that names no class 70, ...
          a local whose declaration the source spells in another case: 149 site(s), top: Msg 30, t 13, ...
          a receiver that is a call result: 822 site(s), 72 typed from the callee's declared return type
          a receiver that is an array element: 501 site(s) over 50 (token, class) pairs, top:
            ArrayElement/UWindowEditControl 168, ArrayElement/UWindowCheckbox 126, ...
          typed by the import's own owner class: 423 site(s) over 15 class(es), top: Pawn 177, Actor 57, ...
          where every declarer agrees, the owner's own class agrees in 700 site(s) and differs in 0

`--shuffle` runs the same walk with each class's declared types rotated onto a neighbouring name; it writes no CSV
and exists only to measure how much of the reading the declarations decided (see "The check is a shuffle" below).

**5,424 static edges, every one of them naming a `Function` export or import**, plus **13,507 dynamic call sites**
whose target is a name rather than an object. A by-name target is first resolved along the **caller's super chain, in
the package that declares each ancestor** -- the earlier census only asked its own package, which is why a parent's
function looked unresolved. **8,321 of the 13,507 (62%) name a function declared on the chain: 5,778 on the caller's
own class and 2,543 on an ancestor, 337 of those `Engine.Actor`, 246 `Engine.Pawn`, 191 `StargateGame.BotBase`, 187
`Editor.BrushBuilder`, 187 `StargateGame.BotEnemy`** -- so a by-name call is not necessarily to a script function of
this build at all, and a rebuild has to carry the ancestor's declaration too.

**The chain index was keyed on the wrong name: a state's function is the class's.** The index a chain walks was built
by a function's *outer* export, which is the `State` for anything declared inside one -- so `IsUnconscious`, declared in
`state BotUnconscious` of `StargateGame.BotEnemy`, was indexed under `BotUnconscious`, a name no super chain holds, and
the sites that call it resolved nowhere. Keying the same index by the class that owns the state (the rule this page
already states for attribution) places **223** of the chain group's sites: the own-class share rises by 197 and the
ancestor share falls by 127 -- 127 sites that named an ancestor now name the caller's own class, because the nearer
declarer is the state the caller's class owns -- for a net **+70**, and it places 8 more on the receiver's chain. The
corpus-alone group shrinks 2,332 -> **2,290** for the same reason seen from the other side (a name that could only be
placed "somewhere in the corpus" now names a class on the caller's chain). Examples: `ChooseAttackMode` and
`WhatToDoNext`, both declared in that state, resolve 6 and 11 sites to `StargateGame.BotEnemy`.

**The property imports were invisible: the tagged stream names the property class with its C++ `U`.** An import that
names a variable records the property's class as `ObjectProperty`, while `inventory/class_properties.csv` writes the
same class `UObjectProperty` (its C++ name), so an equality test between the two rejected **every** property import in
the corpus -- and inside that dead branch a `c[3]` indexed a three-field tuple, which nothing noticed because the
branch never ran. Comparing the names with the prefix stripped types **233 more receivers**, all of them imported
variables: the receiver's chain goes 1,757 -> **1,954** (569 names, 249 distinct receiver types, `Pawn` 260) and the
untyped-object-expression residue 654 -> **421** (340 not a local). What was left of the imports was a different
question -- **240 sites naming a property several packages declare with different types** (`UIWidget` 61,
`HaakenMK2Weapon` 50, `Controller` 48, `InitComponent` 20), owned by `Pawn` 33, `GUI` 30, `GUIListBox` 16,
`ScriptedController` 9 -- which the next round closed, because the import's own outer already names the owner.

**The owner settles the vote: a disagreeing import is typed by the class it names, not by the corpus.** An import's
`OuterIndex` is a negative import index, and for a property it names the class that declares the variable -- the
declaration the compiler resolved against. A corpus-wide vote between same-named properties of unrelated classes is
not a tie to break by count (`UIWidget.Type` and `GUIText.Type` are different variables), so `_receiver_reading`
prefers the owner's own record: **423 receiver readings** in the corpus come from a disagreeing import and are typed
that way (`Pawn` 177, `Actor` 57, `GUI` 57, `LevelInfo` 46, `GUIListBox` 37, `BotBase` 19, over 15 owner classes), of
which **240 were sites the chain had left unplaced** and all 240 now place -- 1,954 -> **2,194** on the receiver's
chain, by-name placement **12,571 -> 12,811 of 13,507 (94.8%)**, the unplaced residue 936 -> **696**. They land on
`Engine.Weapon` 67, `Engine.Controller` 47, `GUI.GUIListBase` 35, `Engine.GameInfo` 32, `GUI.GUIController` 30,
`StargateGame.SquadAIBasic` 8 and `GamePlay.LatentScriptedAction` 8; `CloseMenu` 17, `Add` 17, `GetAggroLevel` 10,
`TeamIDLookup` 9 and `GetPlayerController` 9 are the names they carry. **The rule is controlled where the corpus does
agree: 700 sites declare the name with one type in every package, and the owner's own class agrees in all 700 and
differs in 0** -- so preferring the owner is not a way to disagree with a consensus, it is the vote a tie was
missing. No disagreeing import remains in the residue.

**An array element is an object expression, and the caller had never asked for one.** 430 of the 469 sites with a
"completed value in front" were `A[i].Foo()`: the token in front of the call is `ArrayElement` (349) or
`DynamicArrayElement` (81), its two children are the **index and the array** (in that order, measured), and the
element's class is the array's declared element type -- `array<Pawn>` unwrapped by the same `_class_of_type` that
already accepted `array<>`. **The reading branch alone changed nothing** (the residue stayed 469): `--calls` only
asked `_receiver_reading` about a token in `OBJECT_TOKENS`, and an array element was not one, so the new branch was
never reached -- a site that types to nothing and a site nobody asked about look identical in every count. Adding the
two tokens to that set types **501 sites over 49 (token, class) pairs** (`ArrayElement/UWindowEditControl` 168,
`ArrayElement/UWindowCheckbox` 126, `UWindowLabelControl` 42) and places **390** of them: the receiver's chain
2,194 -> **2,584**, by-name placement **12,811 -> 13,201 of 13,507 (97.7%)**, the unplaced residue 696 -> **306**, and
the "completed value in front" bucket 469 -> **39**.

**What the array case exposed: a declared type carrying a struct's inlining flags is not a class.** The rule's first
run typed receivers as `export editinline Order`, `editinlinenotify GUIComponent`, `editinline StreamPlaylist` -- the
tagged type fields carry a struct's flags and `_class_of_type` took the whole string for a class name, so 21 sites were
placed on a "class" no table has. A class is one identifier, so a type with a space or an angle bracket left after the
`class<>`/`array<>` unwrapping now answers "not a class": those sites move to "declared, but not as a class" (the
untyped residue 187 -> 221 on the run before the sanitisation, 306 unplaced either way) and no placement moves.

**And a class name is matched without regard to case, which the source does not respect.** UnrealScript is
case-insensitive; the `ScriptText` writes `local pawn P;` and `Cast<inventory>(...)` while `classes.csv` says `Pawn`
and `Inventory`, so the receiver's type reached the class index as a name no key held and placed nothing -- the same
shape of bug as `class<LocalMessage>` was for the type syntax. Folding the case **places 26 more sites on the receiver's
chain** (`pawn` 14, `GameHUD` 4, `inventory` 2, `pickup` 2, `controller` 1, `vehicle` 1) and types 25 more local
receivers, so the local share of that group is 529 of 1,757.

**The caller of a site inside a `State` is the class that owns the state, and saying so moved 869 sites onto the
chain.** A state compiles into its class, so a function declared in `state Active` is `Pawn`'s function and its
self-calls resolve there; the census attributed those functions to the state's own name, where nothing declares
anything. With the class as the owner the chain group went from 7,382 to 8,251, and the number of sites with **no
expression in front of them** fell from 145 to **17** -- a self-call always has a declarer on the chain, so that
collapse is the check that the attribution is the right one rather than a lucky one.

**The chain is only the first of two lookups, and the second one does not need the receiver.** A by-name operand is an
`FName` and `ProcessEvent` finds the function by walking the *receiver's* class, so a site inside a class that does
not declare the name is not a broken site -- it is a call on another object. The corpus answers most of the rest
without knowing which object: of the **5,186** sites the chain cannot place, **2,290 (44%) across 914 names reach a
function exactly one class in the corpus declares** -- `ShouldCrouch` -> only `Engine.Pawn` 63, `AddMenuItem` ->
only `UWindow.UWindowPulldownMenu` 58, `SetPosImmediate` -> only `StargateGame.UIWidget` 51,
`GetLocalPlayerController` -> only `Engine.LevelInfo` 40, `PostAIMsg` -> only `Engine.Controller` 40 -- so whatever
the receiver is, the call can reach exactly one implementation.

**The site does carry its receiver; it is just not an operand.** `VirtualFunction` names its target and nothing else,
because the compiler has already emitted the object expression and the VM dispatches on what is on the stack: the
**expression in front of the call token** is the receiver, and in the token tree that is the previous sibling. When
that expression is a variable, a cast, a `Context` or an object literal, its **declared type** names a class -- a
class property's from `inventory/class_properties.csv`, a cast's from its target -- and the call is placed on *that*
class's chain: **1,757 sites (13%) over 547 names**, 247 distinct receiver classes, `Pawn` 138 sites,
`Controller` 101, `SquadAIBasic` 82, `UIWidget` 78, `BotBase` 64. Examples: `Engine.Touch ClientMessage()` has a
`Pawn` in front, so it lands on `Engine.Pawn`; `Engine.PlayerTick PlayerInput()` has a `PlayerInput` and lands
on `Engine.PlayerInput`; `UWindow.AddText AppendItem()` has a `UWindowDynamicTextRow`.

**A local is not a table index: it is a property record the function owns, and its type is in the source the
class ships.** The `LocalVariable` operand resolves to an export in every one of the 540 local receiver sites
in `Engine.u` (389 `ObjectProperty`, 62 `IntProperty`, 37 `StrProperty`, 23 `ClassProperty`, 9
`StructProperty`, 7 `ArrayProperty`, 6 `BoolProperty`, 4 `FloatProperty`, 3 `ByteProperty`), and that export's
**outer is the calling function** -- so the record's *name* is table fact. Its *type* is not readable from the
record: no payload offset of the 281 `ObjectProperty` locals whose source type is known names that class for
more than 30 of them (offset 4 hits 30, offset 5 hits 16, offset 3 hits 8), so the payload is not a plain
`[ArrayDim][class reference]` in this cook. What does bind the name is the `ScriptText` the class carries:
`local <Type> <Name>;` and the parameter list name the same variable, so `--calls` parses each class's source
once per package (`source_declarations`) and answers a local's type from it. That takes the untyped locals
from **628 to 81** and places **504 of the 628**, 80% of them.

**The check is a shuffle, and it is a flag: `--calls --shuffle`.** Before the walk, each class's declared types --
its `local <Type> <Name>;` lines and its declared return types, one pool -- are rotated onto the next name in
sorted order (`shuffled_source`), so the source keeps the same functions and the same set of type spellings and
loses only the binding that placed a site. The same walk then reports what survives: **13,258 of 13,507 by-name
sites name a declaring class as shipped, 12,753 (94.4%) do so shuffled** -- and the part that moves is the part
the declarations placed. `Engine.u` alone: the receiver group **357 -> 264** and the sites whose receiver is a
typed local **117 -> 25** (-79%); the whole corpus **2,641 -> 2,123** and its typed-local front **582 -> 80**
(-86%), with the case-folded placements **33 -> 10** and the call-result placements **72 -> 18** (the 54 lost are
the ones the callee's declared return type decided). **So a local placement is the declaration doing work about
six times in seven, and 2,123 receiver placements survive a rotation because the permuted type still names
*some* class whose chain declares that name** (`ClientMessage`, `GetItemName` and `GetTitle` are on dozens of
chains) -- the control measures the evidence a declaration adds, not the whole of the reading. A local stays
untyped by construction when its class ships no `ScriptText`, when neither its class nor any ancestor has a
declaration for that name in that function, when the class declares the name with two different types, or when
the declared type is not a class at all. The names from the text are matched **without case**, because the
compiler does (`local pawn P;` is the export's `Pawn`) -- 149 sites were untyped for nothing but that.

**So 13,258 of the 13,507 (98.2%) by-name sites now name a declaring class, and the 249 that do not are a
typing gap, not a dispatch gap.** `ClientMessage` 27 sites over 3 classes, `WeaponFireAgain` 11 over 9,
`GetUIXInteraction` 10 over 2, `SetPause` 9 over 4, `GetPickupItemName` 7 over 2. Of the 249: **181 have an object
expression in front of them that names no class at all** (28 a local neither its class nor an ancestor declares,
70 a literal or cast, 40 an array element whose array's type is not a class, 25 not a variable at all, 18 a call
result the callee's own statement could not type -- **no import is among them**), **46 name a class whose chain and subtree
both declare nothing of that name**, and **22 have a completed value in front**, which is a call's return or a
receiver the walk did not reach.

**Three readings took the residue from 306 to 249, and the third one is a spelling bug.** A receiver that is a
**call result** is typed from the callee's *declared return type*, read off the class's own `ScriptText`
(`function Actor GetOwner()`), and the reading is asked at all 822 sites that front a by-name call with another
call: **72 type** (`sound` 33, `PlayerController` 22), **534 name a callee that returns nothing**, 131 one whose
Function is imported rather than in the package (its declaration is in another package), 72 a return value that is
not a class, 11 a callee whose class declares no return type for it and 2 a callee outside the caller's chain. A
local the caller's own source does not declare is looked for in an **ancestor's** `ScriptText`, which types 7. And
**a declared name is matched without case**, the way the compiler matches it: the source spells `msg`, `t`, `dir`
and the export table `Msg`, `T`, `Dir`, so `_folded` types **149 sites** whose declaration was there all along
(`Msg` 30, `t` 13, `i` 12, `Dir` 8, `Image` 7) -- the same defect that `pawn`, `class<LocalMessage>` and
`GameHUD` had. Altogether **57 sites place off the receiver** (53 with a local in front, 4 with a call result):
the receiver's chain 2,584 -> **2,641** over 624 names and 267 distinct receiver classes, the receiver-typed local
share 529 -> **582**, and the untyped local bucket 81 -> **28**. The check on the call-result rule is that only
**4** of the 57 are call results: most of those 822 sites were already placed on the caller's chain, and this
residue is nearly dry -- a fourth reading of the same packages is not where the rest of it is.

**The last of the typed residue is a dispatch, and looking below the receiver settles most of it.** A receiver whose
static type is a base can hold any subclass, so after the chain the tool looks *below* it: of the **52** sites whose
expression typed to a class whose chain declares no such name (`Object` 36, `BotBase` 12, `UnrealScriptedSequence` 3,
`Pawn` 1), **6 name a function exactly one descendant declares** and place as `descendant:Pkg.Class` --
`IsUnconscious` and `GetAggroLevel`, each 3 sites, both to `StargateGame.BotEnemy`, where the first is declared inside
`state BotUnconscious`. The other **46 stay a dispatch**: `ClientMessage` 25 -- an `Object`-typed receiver is a
dispatch over every class in the build, not a lookup -- then `CallToArms` 3, `Possess` 2 and `ValidIndex` 1 over
several descendants, and `GetMoveTarget` 2, `GetMyPlayer` 1, `TakeOver` 1 and `SetPawnClass` 1 naming only classes
*outside* the receiver's subtree. **No name in that residue is missing from the corpus (0)**, which is the useful
negative: the gap is which object, not which build.

**The 654 have names now, and most of them are not a script gap.** `--calls` counts a residue site by *why*
its expression typed to nothing: **473 of the 654 (before the kind fix below, which places 233 of them) reference a property no package in the corpus declares**
(`UIWidget` 80, `HaakenMK2Weapon` 59, `Instigator` 56, `Controller` 48, `Pawn` 41, `Outer` 36 -- several of
them the `Actor`/`Pawn` properties a rebuild inherits from C++), **81 a local whose class's own source has no
declaration for the name**, **70 a literal or cast that names no class**, and **30 a token that is not a
variable at all**. An import names an object in *another* package by definition, so the largest bucket is a
property declared outside the eighteen packages in `System` or declared natively: no reading of these
packages can type it, and the answer would have to come from the native property table, not from a better
walk.

**The split paid for itself by finding a spelling bug.** `_class_of_type` knew only `Class<>` and `array<>`;
UnrealScript's own source spells them lowercase (`local class<LocalMessage> M;`), so a class-typed local came
back as a class literally named `class<LocalMessage>` and placed nothing. Accepting both spellings places
**14 more sites** (the receiver group 1,718 -> 1,732, the local share 490 -> 504; the group is **1,757** after the
class-keying round above) and is why the earlier round's `typed class<LocalMessage> ...` line is gone.

**One caveat, measured: an object expression in front is evidence, not proof.** 820 of the 8,251 chain-resolved
sites (10%, measured against that group; the group is 8,321 now) would *also* be placed by the expression in front of them, because a statement that ends in an object
(`A = SomePawn; Foo();`) leaves an object-valued token next to the next call. The tool prefers the chain reading --
the conservative one for a rebuild, because it never invents a target class the caller cannot reach -- and the
ambiguity is why a `receiver:` placement is written with its receiver class in the same row: a reader can re-judge
any row individually.

`inventory/script_calls.csv` is the table: package, class, function, token, dispatch, target, target class,
`resolved_in` (`package`, an ancestor's `Pkg.Class`, `corpus:Pkg.Class` for the one-class lookup,
`receiver:Pkg.Class` for the receiver's chain, or `descendant:Pkg.Class` for the one class below it that declares the
name), `declaring_classes` (how many classes in the corpus declare that
name), `only_class` (that class when there is exactly one), `receiver` (`self`, `value`, or the token in front when it
can leave an object on the stack) and `receiver_type` (the class that token's declared type names) -- regenerated by
`make script-calls`. The static column is the part a reader can rely on; the dynamic column says where to look, not
what runs.

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
* **Operand semantics -- measured, and the receiver is read from the expression in front of the call.** What the
  reference operands *name* is measured per opcode (`--resolve`, above): objects for the variable, cast and
  final-call tokens, the name table for the virtual and global calls and `NameConst`. The `0x05` switch's byte is
  settled above (the compared value's width). By-name sites are now placed four ways: 8,321 on the caller's chain,
  2,290 by the one class in the corpus that declares the name, 2,641 on the **receiver's** chain and 6 on the one
  descendant below the receiver that declares it -- the receiver is the expression the emitter evaluated in front of
  the call, and its declared type names a class (a property's from `class_properties.csv`, an import's from the owner
  class its outer names, an array element's from its array, a local's from the class's own `ScriptText` or an
  ancestor's, and a call result's from the callee's declared return type). What is left
  is **249 sites over 111 names**: 181 with an expression that names no class (28 a local neither its class nor an
  ancestor declares, 70 a literal or cast, 40 an array element of a non-class array, 25 not a variable, 18 a call
  result with nothing to type), 46 typed to a class whose chain
  lacks the name, and 22 with a completed value in front; see "The calls the scripts make" for the counts, the reason
  split, the shuffle control and the 10% reading ambiguity.
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
