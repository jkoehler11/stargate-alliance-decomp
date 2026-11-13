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

## Where the script starts, anchored on the shipped source

The start is `name_end + 16`, and the two readings that used to support it are weaker than they read. `--start-scan`
walks from **every** delta between `name_end+8` and `name_end+40` and counts the ones whose walk lands exactly on the
epilogue: over `Engine.u` the clean share is **99.3% at +14, 99.2% at +16, 87.1% at +11, 79.3% at +13** and still 73%
at +30 — most opcodes are one byte, so a garbage stream consumes the payload exactly and "the walk reaches the
epilogue" is true almost wherever it starts. That reading therefore counts the opcode table's coverage; it does not
locate the script, and the same is true of any count of payloads that "walk from +16" (3,328 of them).

The anchor that discriminates is content. A `Function` export's class ships its `ScriptText`, so a body whose
first statement is `return true;`, `return false;`, `return;`, `return 0;` or `return 1;` fixes its first opcode
bytes. `--start-anchor` walks no tokens: it asks which `name_end + delta` in `+lo..+hi` carries those bytes, and only
then whether the walk from there reaches the epilogue.

**Five rules decide the bytes, and all are load-bearing (measured 2026-11-12).** The first is *which body* the
payload is: a `Function` export names its owner only through its `Outer`, so the anchor is keyed by
(class, function) — and, when that owner is a `State`, by (class, state, function), because a state's body is its
own and the class-level declaration is not evidence for it. That second case is reachable: the class's `ScriptText`
carries the `state <name> { ... }` block, and reading it anchors **93** state-owned payloads of which **92** sit at
+16 and all 92 walk. Keyed by the function name alone, by contrast, one class's `Destroyed` declaration anchored every
same-named export in the package — `Engine.u` alone exports it from dozens of classes — and those payloads were then
searched for bytes their own source never contained: that mispairing, not the header, is what an earlier pass counted
as 422 unmatched payloads. The third rule is the declaration's **return type**, which chooses the literal: a function
declared with no return type writes `return <expr>;` as a bare `Return Nothing` (`04 0b`, the value dropped — 69 of
the 104 payloads a type-blind table missed), a `float` function writes `04 1e` plus the float (`04 1e 00 00 80 3f`
for `return 1;`), a `byte` function `04 24` plus the byte, and only `bool`/`int` bodies reach `04 25`/`04 26` for 0/1
and `04 27`/`04 28` for true/false. A return type with no measured rule (`vector`, `rotator`, a struct, an array) is
left unanchored rather than guessed, so the denominator stays honest. The fourth is that **a state's body is not part
of the class-level text, and reading it as one is what the last twelve residues were.** The class-level pass ran the
declaration regex over the whole `ScriptText`, which also matches every function declared inside a `state { … }`
block. When the class's own declaration is not an anchor — `Engine.u`'s `Weapon.IsFiring` is `return ((Instigator !=
None) && …)` — the *state's* `return true;` became the class-level expectation, and the class-owned export was then
searched for bytes only the state's body contains. Blanking the state spans before that pass (same length, newlines
kept, so every offset stays valid for `first_statement_kind`) takes the corpus from **534 anchored payloads (514 at
+16, 13 unmatched, 31 declarations dropped as doubled)** to **540 anchored, 536 at +16 and all 536 walking, 4
unmatched, 2 dropped**; the spurious `+21`/`+22`/`+28` matches (7 payloads) go with them, the state-owned half is
unchanged at 93 anchored / 92 at +16, and the twice-declared population falls from 31 to the two that really are
declared twice in one body.

The fifth rule closes the population: **a declaration with no body is not an anchor, and its payload is a prediction.**
82 declarations are `native` or state-implemented stubs — `function bool CanMultiJump();`, whose body lives in a state
or in native code — and since the body finder takes the first `{` after the parameter list, each was paired with the
*next* function's body. That pairing is also what kept the last four residues alive: the borrowed literal never
matched, while the payload's `04 0b 47` is exactly what a bodiless declaration compiles to. Excluding the stub and
checking its payload instead gives **82 of 82 empty**, and it removes 74 payloads that had been counted as anchored on
a borrowed `return;` — the `04 0b` anchored opcodes fall from 81 to 11. A key declared both `native` and with a body
(`Weapon.GetErrorVec`, `Weapon.GetFireStart`) is filed under the bodied declaration, the only one that could have
produced a script; a key declared twice *with* bodies is still dropped rather than guessed.

**An `if (` or `assert (` anchor is invalid and this tool no longer takes one.** This compiler evaluates the
condition first and emits the conditional jump behind it, so `07`/`09` are not the body's first byte; with those two
patterns in the list the anchored population was 2,474 payloads and **597 of the 716 that matched at no delta did not
carry the expected byte anywhere in the payload** — the signature of a wrong anchor, not of a wrong header (a later
run measured 31 payloads with the byte one byte *before* the name's end, i.e. inside the header's own encoded part).

    $ python3 tools/uscript-dis.py --start-anchor System --lists 20
    System: 466 anchored payloads (ScriptText fixes the first opcode) of 466 with a located epilogue
      anchored opcodes: {'0428': 232, '0427': 186, '0425': 19, '040b': 11, '041e00000000': 10,
                         '042400': 6, '041e0000803f': 1, '0426': 1}
      by owner: 386 class function(s), 80 owned by a State
      not anchored: 2 declaration(s) made twice under one key, 0 whose return type has no measured rule,
                    82 with no body at all
      a declaration with no body must compile empty: 82 such payloads, 82 empty, 0 carrying a script
          +16   466 /  466   466 walk   <- the fixed run start_report assumes
          +18    10 /  466    10 walk
      peak delta +16 with 466 of 466 anchored payloads and 466 clean walks
      state-owned only: 80 anchored, 80 at +16 (80 walk), no match at any delta: 0
      no delta matched: 0; the same bytes at payload offset 0: 0
      dword at name_end+12 minus the script's length, over the payloads whose start is +16: {0: 464, 14: 1, 18: 1}
        equal in 464 of 466 (99.6%), and never negative: yes
      dropped, declared twice under one key (2):
        Engine.u     mover.selftriggered  x2  bool/true
        StargateContent.u haakenweaponshield.isready  x2  bool/false

**466 of 466** anchored payloads carry the expected bytes exactly at `name_end+16`, and **all 466** walk from there to
the epilogue; **no** payload carries them at no delta, **none** at payload offset 0, and `+18` is the only other delta
(10 second occurrences of the same bytes, not starts). A wrong start does not survive this test, because the bytes have
to be the ones the source wrote *and* the literal the declaration's own return type predicts.

What is off the population is named rather than smoothed, because a count is not actionable and the names are one read
away: `--lists N` prints each row with the bytes its declaration fixed, the bytes it carries at +16, whether those walk,
the dword at `name_end+12` against the script's length — and the three groups that are excluded instead of anchored.

**2** declarations are made twice with bodies in one class (`Mover.SelfTriggered`, `HaakenWeaponShield.IsReady`) and
are dropped rather than guessed. **82** have no body at all: `native` declarations and `function Foo();` stubs whose
implementation lives in a state, which is why their payload is an empty script — and that is checked, not assumed
(**82 of 82 empty, 0 carrying a script**). The four "empty body" payloads an earlier pass listed as a residue are in
this group, and their `04 0b 47` was the compiler being right. The dword at `name_end+12` is the script's length in
**464 of the 466** rows whose start is +16 (residues 14 and 18) — a statement about the trivial `return` bodies this
population is made of: corpus-wide the same dword is *larger* than the script far more often than it is equal, so
the field is equal here only because these bodies have no operand to widen -- it is identified below, "The dword
before the script is its size in memory".

**Every declaration in the corpus is accounted for, and the missing literal rules are not exercised.** The tool
prints the accounting beside the peak delta: **466 anchored, 2 declared twice under one key, 82 with no body, and 0
unanchored payloads carrying a script** (`--start-anchor System --lists 100`). The last of those lines exists for this
claim — a row there is a declaration whose return type has no rule *and* whose payload holds a script, i.e. a literal
to measure — and it is empty over all 16 packages that ship bytecode. So the `vector`/`rotator`/`struct`/`array`
literal encodings an earlier note named as the next rule are not needed for any shipped body: the declarations that
named those types are bodiless stubs (`Weapon.UpdateLeadLag` `rotator/zero`, `VoiceChatReplicationInfo`'s
`GetChannels`/`GetPublicChannels`/`GetPlayerChannels` `array<voicechatroom>/false`, its
`GetPublicChannelCount`/`GetPlayerChannelCount` `int/false`, `BotEnemy.SquadCoverRequest` `esquadcover/false`), and
the tenth row, `vector/zero`, appears in neither residue. A rule measured from those bodies would have been measured
from nothing, which is why the check to run before measuring one is **count the population that would exercise it**.

## The dword before the script is its size in memory (U13, 2026-11-12)

`python3 tools/uscript-dis.py --sizefield System` closes the fourth word of the fixed run. It is **the script's size
with every compact index operand widened to four bytes**, i.e. `len(script) + sum(4 - width)` over the script's own
index operands:

    System: 11687 payload(s) with a located epilogue, 11608 walked
      dword@name_end+12 == the script with every index widened to 4 bytes: 11565 of 11608 (99.63%)
      dword@name_end+12 == the stored script length: 2141 of 11608 (18.44%)

The reading is not a guess about the field but a consequence of a measured one: the loader widens exactly those
operands on the way into memory (the note above `operand_spans`: `StargateHUD.DrawOrderTarget`'s log call is `b8 2a`
in the package and `a0 1e 26 01` in the guest), so a size the compiler wrote *before* that widening is larger than
the stored script by three bytes per one-byte index and two per two-byte one. That is why the difference tracks the
script's index widths rather than its length — and why the anchored population's trivial `return` bodies, which have
no operands to widen at all, are exactly where the two coincide (464 of the 466).

Three consequences, and two things not to redo:

* **It is not a locator, and there is no length field to find in front of a script.** Only 18.45% of payloads carry
  the stored length there, and the loader reads the payload's `SerialSize` anyway.
* **It is a check on an extent.** Any candidate end for a script must satisfy the identity — a second test beside
  "the walk reaches the epilogue", and the only one that does not depend on the opcode table. It is what a rebuild
  (3.8) can score a compiled script against.
* **The residue is 43 payloads of 11,608** -- it was 117 of 11,602 until the `0x43` row below was corrected, and
  that one correction took the `+4` mode with it. What remains is `-3` (14 rows, one index the loader does not
  widen), small `±1..±10` differences, and three payloads whose dword is in the millions (the header decode is
  ambiguous there, not a different field). **No single operand class explains the rest**: every pattern item's
  count per residue row is tabulated against the difference, and no class times any small coefficient fits.
  `--sizefield --lists N` names each row with the widths of its index operands for the session that wants to
  chase one.
* **`--diverge` then asks each residue row where its difference could start, and 24 of the 43 have no first
  operand.** Every span of every residue row is tried as *the* explanation -- an `index` operand the compiler did not
  widen (it moves the prediction by `-(4 - width)`) or a `fixed` operand whose bytes decode as a compact index of
  exactly its own width (it moves it by `+(4 - width)` without moving the walk). Re-run against the corrected table:
  **9 rows have exactly one such operand, 10 have several, and 24 have none anywhere in the script**
  (`index-not-widened` at ops `01` 92 sites, `00` 31, `1b` 9, `02` 3, `1c` 1; `fixed-is-index` at `39` 19, `2c` 10,
  `05` 4, `06` 2, `0a`/`07` 1 each). For the 24 the difference is accumulated over the row rather than owned by one
  operand: `-6` 4, `+5` 3, `-48`/`-4`/`-1`/`+1`/`+2`/`+4`/`+10` 1 each, `+7`/`+9` 2 each, plus the three
  million-scale rows.
* **A class of feature does not explain the rest either, and `case` values were never the `+4` mode.** The rule
  `dword - widened_length == k * count(feature)` was tested for every feature a row is made of -- 297 opcodes, operand
  widths and pattern items -- at every k 1..8 over all 11,608 walked rows: **2,376 rules, none exact**, the closest
  (`op:0d` x3) still leaving 42 rows. The `+4` rows it could not fit had **no `case` value at all**, while 58 *exact*
  rows do -- a u16 case widened to a u32 was dead rather than merely unlikely -- and the shape it kept pointing at
  (`op 43` x2) was real but counted at the wrong granularity: see the subsection below, which removes the whole mode
  with one table row.
* **The four "ambiguous header" rows are not a shifted read of the same dword.** `--diverge` also searches the
  44 bytes the header occupies for a dword equal to the prediction, at every byte offset: the three rows whose dword
  is above 10^6 (`InitializeParsers` at 11,665,408, `EnableShadows` 3,932,095, `CalcPathPoint` 7,798,660) and the
  fourth with a 261,119 difference carry **no such dword anywhere in the header** (the report prints `-` per row), so
  whatever those four payloads' `name_end+12` is, it is not this field read a few bytes off.

### The `+4` rows are one opcode's operand, measured in a guest (U13, 2026-11-12)

The rows whose dword sat four above the prediction had one thing in common that the feature census could not see,
because it counted *tokens* rather than the bytes inside one: **`0x43` `DelegateFunction`**. The table recorded its
reference as four fixed bytes (`bytes:4`), and in a guest it is **eight** -- two compact indices, each widened to a
four-byte slot. `tools/xemu/script-ram.py` is the read that settled it, on `Engine.u`'s `StreamPlaylist.AddStream`
(script 146, dword 200 against a predicted 196):

    $ python3 tools/xemu/script-ram.py RAMBIN --dump DUMP --pkg Engine.u --class StreamPlaylist --function AddStream
    Engine.u StreamPlaylist.AddStream: script 146 bytes, 25 index operand(s) by width {2: 25}
      model 196 bytes (script + 50 widening(s)); field at name_end+12 = 200, predicted 196, +4
      search: 134217728 byte(s) of RAM, 5-byte anchor; 0 copy(ies) at the model's length, 3 near miss(es)
        copy at 0x00cd84c0: 176 of 196 model byte(s) agree, then fixed of op43; epilogue at +197, so the copy is
          200 bytes (+4 the model)

* **The model matches for 176 bytes and then the copy is four longer.** The copy's first disagreement is the `0x43`
  token's reference: eight bytes in RAM where the file has four (`90 0d 42 01 d7 08 00 00` against `45 1b 7b 06`).
  Every byte after it matches the model again **shifted by exactly +4** and ends on the same epilogue, so the +4 is
  one token's operand and not an accumulation. The same copy is in three independent 128 MiB dumps -- `brk6` and
  `findtext4` at `0x00cd84c0`, `msg1` at `0x00cb84c0` (the heap shifted) -- so it is a load-time allocation, not one
  boot's leftover. **Counting tokens cannot see this**: the token sits in 82 payloads, and the rows that move are the
  ones whose two indices are both two bytes wide.
* **The file's four bytes are two compact indices**, `45 1b` (1733) and `7b 06` (443): each starts a two-byte index,
  and the compiler's own arithmetic is `146 + (4-2) + (4-2) = 200`, which is the field. The two slots in RAM do not
  hold those two numbers -- the first reads `0x01420d90`, above the image end, so a pointer, and the second `0x8d7` --
  so the loader resolves the reference as well as widening it; the **width** is what this question needed, and the
  corpus confirms it.
* **Correcting that one row collapses the mode.** With `0x43` as `index,index,expr`, the System directory gives
  **11,608 walked (was 11,602), 11,565 exact (was 11,485), 43 residue rows (was 117)**: ten of the eleven `+4` rows
  and the single `+8` are gone, six payloads the walker used to refuse now walk
  (`VotingReplicationInfoBase.SendCommand` and `.SendResponse`, `GUINumericEdit.EditOnChange`, and
  `ChapterSelectionPanel`/`GameModeSelectionPanel`/`ProfilePanel`'s `InternalOnChange`), and no payload got worse.
  `Engine.u` alone goes from 24 residue rows to 12.
* **The earlier feature rules were not wrong, they were counted at the wrong granularity.** `op 43` x2 was the
  *closest* of the 2,376 single-feature rules and left 70 rows, because one `DelegateFunction` adds **four** only
  when both its indices are two bytes wide. A rule over a token's *count* cannot express a rule about its *operand
  widths*, which is why the census kept pointing at `op 43` without ever fitting.

### The `-3` and `-6` rows are the same question in the other direction (U13, 2026-11-12)

With the `+4` mode gone the largest residue shape is `-3`: the dword is three *smaller* than
`len(script) + sum(4 - width)`, i.e. the model widens one operand that the guest does not. `script-ram.py` answers
these the same way, and on three rows the copy's own length is **exactly the field's number**:

    Engine.u StreamBase.ConvertToFileExtension         script 205  dword 207  predicted 210   copy at 0x00cd8b00: 208 of 210 bytes agree, epilogue at +204 -> 207 bytes
    Engine.u GameMessage.GetString                     script 400  dword 499  predicted 505   copy at 0x00d63400:  30 of 505 bytes agree, epilogue at +496 -> 499 bytes
    Engine.u TexOscillatorTriggered.Trigger            script 134  dword 174  predicted 180   copy at 0x00c2fc00: 114 of 180 bytes agree, epilogue at +171 -> 174 bytes

* **`Trigger` shows where the six bytes went, and they are two slots of one byte.** Around the disagreement the model
  has four wildcards, then `86 00`, then four more, then `39 3a`; the copy has `07 86 00 9b 39 3a` -- a **one-byte**
  slot on each side of the shared literal, i.e. `-3` twice. So the `-6` rows are two un-widened one-byte indices, and
  `-3` is one, which is what the difference distribution says without a guest (`-3` 14 rows, `-6` 4).
* **The opcode does not decide which indices widen.** In `ConvertToFileExtension` both un-widened candidates are
  `op00` `LocalVariable`, and one of that script's `LocalVariable` operands is four bytes in the copy (`6a59`) while
  the script's last one stays one byte. So "every compact index widens to four" is right for the corpus (99.63%) and
  wrong per slot: the loader's choice is not the token class, and this is the measurement that says so.
* **These rows are not a compiler-versus-loader disagreement at all.** The field counts `205 + 2 = 207`, the copy is
  207 bytes: the compiler and the loader agree, and it is the *model* that over-counts. That is the opposite of the
  `+4` rows, where the model was short and the table row was wrong -- worth keeping apart when either is quoted.

`--sizefield --lists` now names the owning class rather than the metaclass (`pkg.name(owner)`, not
`class_name_of(owner)`, which printed `Class` on all 400 rows) -- without it these three rows could not be looked up
at all, since a function name is not unique in a package.

### The field is exact in every copy found, and the gap is one or two one-byte slots (U13, 2026-11-12)

`--sizefield` left 43 residue rows. Reading them one at a time against six 128 MiB `pmemsave` images of a loaded
`M00` -- `script-ram.py --residue`, one line per row, `--slots` for the per-slot detail -- turns the shape into a
count: **13 rows have a guest copy that aligns span for span, and 10 of those copies are the field's own number.** In
every one of the 10 the difference between the model and the copy is *exactly* the slots the guest left at their file
width, so no row needs a second rule:

| package | class.function | field | narrow slot(s) |
|---|---|---:|---|
| `Engine.u` | `PlayerController.ServerJoinVoiceChannel` | 327 | 212 `op01` |
| `Engine.u` | `TexOscillatorTriggered.Trigger` | 174 | 86 `op00`, 89 `op00` |
| `Engine.u` | `TexPannerTriggered.Trigger` | 174 | 86 `op00`, 89 `op00` |
| `Engine.u` | `StreamBase.ConvertToFileExtension` | 207 | 202 `op00` |
| `Engine.u` | `LadderInfo.GetMatchInfo` | 196 | 110 `op00` |
| `GUI.u` | `GUIComponent.MenuStateChange` | 455 | 281 `op01` |
| `GUI.u` | `GUIMultiComponent.MapControls` | 952 | 507 `op02` |
| `GUI.u` | `GUIHorzList.InternalOnKeyEvent` | 222 | 135 `op00` |
| `StargateGame.u` | `WeaponZoomInteraction.KeyEvent` | 120 | 97 `op00` |
| `StargateGame.u` | `StargateWeapon.PlayFiring` | 464 | 183 `op00`, 186 `op01` |

* **13 narrow slots, every one at file width 1, and all on the three variable tokens**: `op00` `LocalVariable` 9,
  `op01` `InstanceVariable` 3, `op02` `DefaultVariable` 1. The widened slots in those same scripts are both widths
  (`2 -> 4` and `1 -> 4`), so "every compact index widens" is a corpus rule and not the loader's per-slot behaviour.
* **Neither the opcode nor the value decides.** `Trigger` has four one-byte slots: 36 and 41 (`op01`, indices 4 and
  14) are widened in the copy, 86 and 89 (`op00`, indices 7 and -27) are not. A one-byte index of 4 widens at offset
  36 of `WeaponZoomInteraction.KeyEvent` and stays at offset 202 of `ConvertToFileExtension`, so a small value is not
  the discriminator either -- the same negative the `-3` rows gave, now measured on the guest side instead of inferred
  from the field.
* **The million-scale rows are settled from the guest side.** Three of the 13 complete copies are *not* the field:
  `StreamPlaylistManager.InitializeParsers` (copy 183, field 11,665,408), `StargatePlayer.EnableShadows` (124 against
  7,798,784) and `SemektetMortarGun.CalcPathPoint` (65 against 3,932,160) -- and each copy is exactly the model's
  predicted length. So the body loads as the widening model predicts and `name_end+12` on those payloads is not this
  field at all, which is the `--diverge` "no such dword anywhere in the header" negative confirmed without another
  header scan.
* **The 30 rows that do not align are three different negatives.** Rows / complete copies / deepest alignment in
  spans, per package: `Engine.u` 12/6/212, `GUI.u` 5/3/505, `StargateGame.u` 5/3/252, `StargateVehicles.u` 1/1/36,
  `StargateMenus.u` 8/0/9, `UWindow.u` 7/0/10, `UDebugMenu.u` 2/0/5, and `GamePlay.u`, `IpDrv.u`,
  `StargateContent.u` 1/0/**0** each. The three at zero have no body of theirs in any of the six images -- not
  resident, a different negative from a divergence -- while the menu packages peak at 5-10 spans, the size of a wrong
  anchor, and `Engine.u`/`GUI.u` run hundreds of spans before stopping.
* **A seventh image, taken with the front-end menus open, moves neither number -- and the name pools say the reason is
  the anchor, not the package.** The 8 `StargateMenus.u` rows align to 9, 9, 0, 5, 5, 1, 1, 5 spans in it and the 7
  `UWindow.u` rows to 0, 10, 0, 5, 0, 0, 0: no complete copy, the same 5-10 span peak, so a menu-open image is not the
  missing witness. What it does answer is whether those bodies are there at all:
  `tools/xemu/resident-names.py RAMBIN --residue CSV` counts each row's **class name** in the image's `FNameEntry`
  pool (the wide name at `+0x0c`), and **41 of the 43 rows' class names are resident in it** -- the two that are not
  are `UDebugMenu.u`'s `UGraphParticlePage` and `UDebugMenuBar`, a package the shipped build never loads. So
  `GamePlay.u`, `IpDrv.u` and `StargateContent.u` -- the three packets this page called "not resident" -- do have
  their classes in the image, and the zero-span reading means the **anchor** found nothing, not that the body is
  missing.

* **`Actor.DisplayDebug` (+9, the largest row, 1,605 spans) stops at 212 spans, and the copy is longer there, not
  shorter.** The model and the copy agree for 455 bytes; the stop is at file offset `0x167`, an `op 1f` `str` token:

      file : 1f 70 00        1f 1f 20 52 45 4d 4f 54 45 20 52 4f 4c 45 20 00 16
      guest: 80 49 fe 00  70 00  80 49 fe 00  1f 20 52 45 4d 4f 54 45 20 52 ...

  so each of the two `op 1f` bytes of the file is four bytes in the copy -- the same value (`0x00fe4980`) both times --
  and the literal that follows is unchanged. (The literal is ` REMOTE ROLE `, `54 45`; a dump search for
  `REMOVE ROLE` finds nothing, which is how the wrong byte in the first version of this paragraph surfaced.)

  **The four bytes are a constant, and no per-token rule reproduces the copy.** Measured with the second model
  `script-ram.py` now carries (`op1f`: an `op 1f` opcode is four bytes, the literal after it unchanged, tried only
  where the strict model stops):

  * **Eight `StringConst`s sit inside the 455 bytes the strict model reproduces byte for byte** -- `TEST` (file
    0x16), ` DELETED (b` (0x5c), `ROLE ` (0xcf), `None` (0xe5), `DumbProxy` (0xfa), `SimulatedProxy` (0x114),
    `AutonomousProxy` (0x133), `Authority` (0x153) -- **and none of them is expanded.** The first two that are, are the
    pair at 0x167/0x16a, out of **83** `StringConst`s in the script.
  * **A rule keyed on the opcode desynchronises at the first literal that is not expanded**: widening every `op 1f`
    aligns 16 spans, 32 of the model's 3,486 bytes. Keying it on the preceding `0f 00` pair buys one more token
    (214 spans) and stops at the second of the two. The deciding property is **per literal**, and it is not the
    preceding token either.
  * **The four bytes are not that literal's address.** Both slots hold `0x00fe4980` -- two different literals, one
    value -- in **all six** dumps, 125 times in a 128 MiB image; `0x00fe4980` reads as zeros and pointers, not text;
    and the dword does not occur in `default.xbe` at all (0 matches), so it is a load-time heap address whose value is
    deterministic, not something the image names. `script-ram.py --slots` prints the stop and, when the second rule is
    what read the copy, every `op 1f` slot with its four bytes; `--residue` reports complete copies by rule, and the
    rule closes **0 of the 30 unaligned rows** (13 complete / 10 the field's own number, unchanged) because the wedge
    appears in exactly one of them.

  What would settle it is the handler rather than the payload: `GNatives[0x1f]` in a live table (`docs/NATIVE_VM.md`)
  decides whether the four-byte slot is read at all -- or a byte-diff over many more copies that correlates which
  literals expand inside one script.
  **The handler answers the first half of that: the four-byte slot is not read.** `GNatives[0x1f]` is
  `intUObjectexecStringConst` at `0x00047bf0` -- 69 bytes, listed in `docs/NATIVE_VM.md` from
  `tools/gnatives-body.py --slot 31`: it copies the literal at `FFrame::Code` through a `0x400`-capped helper into the
  result `FString` and then walks `Code` **one byte at a time** to the NUL. There is no count and no operand, and the
  executor reads the opcode as **one byte** (`0x48ce5` `movzx edx,[eax]`, `0x48cf5` `call [edx*4+0x6313e8]`), so at
  both stops the copy's `80 49 fe 00` is opcode `0x80` -- `GNatives[128]`, `execLeft` -- with three operand bytes
  behind it, and the literal that follows is not reachable through slot 31 from there. The `op1f` model is the right
  byte count and the wrong token: the copy holds a **replaced opcode** where the file has a one-byte one, which is also
  why the rule desynchronises at the next unexpanded literal and closes no row.



* **A content anchor places no residue row either, and it says why.** `script-ram.py --anchor-literal` derives a
  copy's start from a literal the script carries (`hit - model offset`) instead of from the model's prefix, so a
  rewritten prefix no longer hides a copy. Over the 43 residue rows against the four 128 MiB images still on disk it
  closes **0 of the 30 unaligned rows** (13 complete / 10 the field's own number, unchanged): **21 of the 30 carry no
  literal of 6+ bytes at all**, 6 of the 9 that do carry one that occurs **nowhere** in the four images -- three of the
  eight `StargateMenus.u` rows among them, so those scripts are absent rather than unmatchable -- and the three rows
  that do hit (`Actor.DisplayDebug` 45 candidate starts, `LadderInfo.UpdateLadders` 2,
  `CommandInterface.executeScriptCommand` 1) align 212/0/0 spans, exactly as far as the prefix search did. The *start*
  was never what those rows were missing.

* **Where a copy is placed, the anchor reads the script's first byte back from the guest.** `ServerJoinVoiceChannel`
  (327 bytes at `0x00d72000`) and `StargateWeapon.PlayFiring` (464 at `0x012dc700`) align **complete** from a literal,
  at the same start the prefix search found, and the 16 bytes in front of that start match the payload's header tail in
  **9 and 10 of 16 positions, never more than 2 from the right** -- so the export's header is not copied in front of
  the script, and the copy's first byte is the script's first byte: `name_end + 16`, confirmed from the guest side.
  `--anchor-literal` prints that read-back for whichever copy it places; `--residue --anchor-literal` retries every
  row the prefix could not place and prints each row's literal population and candidate-start count.

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

## The name column is the image's own, not a token list that stops at `0x47`

The table's names came from UELib's UE2 token map, which is a list of *expression* tokens and ends at `0x47`. The
operators and natives above it had a shape and no name, so **126 of the 184 opcodes the shipped corpus uses** printed
as `op77`, `op61`, `opaf` -- and those 126 are exactly the 48,422 native-call tokens, every one of them anonymous in
the disassembly. No external list is needed there: the token *is* the `GNatives` index, and the image already carries
`intUObjectexec<Function>` for every slot it populates (`inventory/gnatives_records.csv`).
`tools/opcode-table.py` joins that record into the CSV's `name`, `source` (`record` / `record-high`) and `note`
columns, and `--check` refuses a row whose name disagrees with the record table.

| band | opcodes | what names it | corpus tokens |
|---|---:|---|---:|
| `0x70..0xff` | 120 used | the token *is* the slot: `0x77` is slot 119 `intUObjectexecNotEqual_ObjectObject` | 44,174 |
| `0x60..0x6f` | 6 used of 16 | `HighNative<n>`; with operand byte `b` the native is `(op-0x60)*256 + b`, so `0x61 0x16` is 278 `Actor.Spawn` | 4,248 |
| `0x48..0x5f` | 0 | **nothing**: the image has no `GNatives` record at any of those 24 slots | 0 |

That third row is a third independent confirmation that the walker's bad-token band is not an opcode band: it rejects
`0x48..0x5f` and the image has no slot there either.

Two checks say the join is the naming rather than a plausible one, and both are by content:

* **Direct band** -- 111 of the 120 spell the function the shipped script declares at that exact index
  (`inventory/natives.csv`), and the nine that do not differ only in spelling: `Concat_StringString` in C++ where
  `Object.uc` declares `Concat_StrStr` (the nine are `Concat`, `Less`, `Greater`, `LessEqual`, `GreaterEqual`,
  `EqualEqual`, `NotEqual`, `ComplementEqual`, `At`). Those rows carry the declaration in their `note`.
* **High band** -- 84 distinct natives, **83 identical** to a declaration, one differing the same way
  (`ConcatEqual_StringString`), and **4,246 of 4,248 tokens** resolve. `python3 tools/uscript-dis.py --highnative
  DUMP/System` prints that comparison; it is `inventory/native_opcodes.csv`'s row set cut by band, so the two
  artifacts corroborate each other rather than duplicating a measurement.

What the disassembler therefore prints is the operator's own name -- `AndAnd_BoolBool`, `Multiply_FloatFloat` -- and
for a `HighNative` token the callee its operand byte names:

    $ python3 tools/uscript-dis.py --hex 611616
    0000  HighNative1 (Spawn)      16                   3 byte(s)

`inventory/opcode_usage.csv` and the walk are otherwise unchanged (184 opcodes, 11,608 scripts walk clean, the same
token counts), so what moved here is names, not measurements.

**Not claimed.** Two tokens name nothing: `0x67 0x08` resolves to native(1800) and `0x6e 0x43` to native(3651), one
token each, with no `GNatives` record and no `native()` declaration anywhere in the shipped script (both rows stay
empty in `inventory/native_opcodes.csv`). Naming those is a question about slots the record table does not populate,
not about the token encoding.

## The calls the scripts make

`--calls` walks the corpus and records every call site, resolving each one as far as its operand rules allow:

    $ python3 tools/uscript-dis.py --calls DUMP/System -o inventory
    11687 scripts with an epilogue, 11608 walked; 18931 call sites
      static (FinalFunction): 5424, of which the target is a Function: 5424
      by name (VirtualFunction, GlobalFunction): 13507
        naming a declaring class: 13433 of 13507 (99.5%)
        on the caller's chain: 8321 -- 5778 in its own class, 2543 on an ancestor (top: Engine.Actor 337, Engine.Pawn 246, StargateGame.BotBase 191, Editor.BrushBuilder 187, StargateGame.BotEnemy 187)
          of those, 223 name a function only a *state* of that class declares (the class owns the state)
        by the corpus alone: 2290 site(s) reach the one class that declares the name (914 names), top: ShouldCrouch -> Engine.Pawn 63, AddMenuItem -> UWindow.UWindowPulldownMenu 58, SetPosImmediate -> StargateGame.UIWidget 51, GetLocalPlayerController -> Engine.LevelInfo 40, PostAIMsg -> Engine.Controller 40
        on the receiver's chain: 2815 (655 names) -- the expression in front of the call types to a class (279 distinct, top: Pawn 274, UWindowEditControl 189, Controller 149, UWindowCheckbox 138, Weapon 111)
          of those, 47 place because the receiver's type names a class in another case, top: pawn 14, playercontroller 12, GameHUD 4, inventory 3, stargateammo 3, pickup 2
          and 11 name a function only a *state* of the receiver's chain declares
        decided by the receiver: 74 site(s) across 30 names, top: ClientMessage 26 over 3 classes, GetPickupItemName 7 over 2 classes, Possess 3 over 15 classes, UnPossess 3 over 11 classes, GetMyPlayer 3 over 2 classes
          an object expression with no recorded type: 5 -- 0 of them a local, 5 not
          a directive line between a declaration and its body: 4 declaration(s) over 173 class text(s): InteractionMaster.AddInteraction, InteractionMaster.AddInteraction, StargatePawn.Talk, StargatePawn.Talk
          of the receiver-typed sites, 610 had a local in front (typed from the class's own ScriptText)
          a local whose declaration the source spells in another case: 159 site(s), top: Msg 30, GUI 20, t 13, i 12, Dir 8, Image 7
          an expression that typed to a class whose chain declares no such name: 54, of which 7 reach exactly one descendant that declares it (descendant:Pkg.Class, 2 names), top: IsUnconscious -> StargateGame.BotEnemy 4, GetAggroLevel -> StargateGame.BotEnemy 3
            the rest stay a dispatch: 42 over several descendants (14 names, top: ClientMessage 25, Possess 3, CallToArms 3, ValidIndex 1), 5 a name only classes outside the subtree declare, 0 a name no class in the corpus declares
            those 54 receivers by class: Object 36, BotBase 14, UnrealScriptedSequence 3, Pawn 1
            names only outside the subtree declare: GetMoveTarget 2, GetMyPlayer 1, TakeOver 1, SetPawnClass 1
            names no class declares: 
          a completed value in front (a self-call, or a receiver the walk did not reach): 22
            the token that produced that value: JumpIfNot 1250, Let 520, Jump 318, Return 133, False 108, True 72, LetBool 69, Log 54
          a receiver that is a call result: 822 site(s), 132 typed from the callee's declared return type (top: playercontroller 80, sound 33, sgformation 6, herdanimal 4, stargateammo 4, vector 3)
            the rest by what the callee's own statement says: call result: the callee returns nothing 609, call result: the callee returns a value that is not a class 73, call result: the imported function's class is not in the corpus 7, call result: the callee PawnDied is declared by 8 classes, none on the caller's chain 1
          why an expression in front names no class: call result: the callee GetInstigator is declared by 2 classes, none on the caller's chain 2, call result: the callee TeamIDLookup is declared by 5 classes, none on the caller's chain 1, call result: the callee SetSprinting is declared by 2 classes, none on the caller's chain 1, call result: the callee returns nothing 1
          a receiver that is an array element: 681 site(s) over 64 (token, class) pairs, top: ArrayElement/UWindowEditControl 168, ArrayElement/UWindowCheckbox 126, ArrayElement/UWindowLabelControl 42, DynamicArrayElement/GUIComponent 31, DynamicArrayElement/MaplistRecord 28, DynamicArrayElement/ScriptEvent 24
          typed by the import's own owner class: 427 site(s) over 16 class(es), top: Pawn 177, Actor 57, GUI 57, LevelInfo 46, GUIListBox 37, BotBase 19
          where every declarer agrees, the owner's own class agrees in 711 site(s) and differs in 0

`--shuffle` runs the same walk with each class's declared types rotated onto a neighbouring name; it writes no CSV
and exists only to measure how much of the reading the declarations decided (see "The check is a shuffle" below).

**The residue is named, not just counted, and two of its reasons were the tool's.** `--lists N` prints every site of
the residue -- package, class, function, call name, the token in front of it, that token's own operand name and the
reason -- because a count says a reading failed and only the name says which. On the first run the 181 sites were five
shapes, and two of them were reading gaps rather than the build's:

* **70 were casts whose class the package imports.** `DynamicCast Pawn` in `StargateContent` names `Engine.Pawn`
  through the *import* table, and the cast branch consulted only the export table, so every cast of a class declared
  in another package answered "names no class". Reading the import (and requiring its kind to be `Class`) is one of
  the two changes below; all 70 place.
* **27 of the 28 untyped locals were declarations the source regex could not see.** `GUI.GUIController` declares
  `protected final event PushMenu(int Index, GUIPage NewMenu, string Param1, string Param2)`; the modifier list
  accepted `final` but not `protected` (nor `exec`), so the function was never keyed and none of its locals was
  declared. Accepting them types **27 more local receivers** (609 sites now read a local in front) and leaves **one**
  untyped local.

Together: by-name sites naming a declaring class **13,258 -> 13,356 of 13,507 (98.9%)**, sites on the receiver's chain
**2,641 -> 2,738**, the untyped-object residue **181 -> 83**, and 238 rows of `inventory/script_calls.csv` moved.

**What the remaining 83 are**, by shape: **40 array elements whose array holds a struct** (`TArray<ScriptDirection>`
in `Engine.ScriptEvent`, `TArray<PlayInfo>` in `Engine.PlayInfo`, `TArray<StreamPlaylist>`) -- an element of a struct
array is not an object, so that is a negative and not a gap; **25 struct members whose declared type is a class**
(`StargateGame.SquadMenu.PostRender`'s `FaceWidget`, `GoauldConsoleHud.DrawHUD`'s `Message`), which needs the struct
declarations the class `ScriptText` already carries -- the next instrument; **17 call results** whose callee is an
import with no return type anywhere in this corpus; and **one local**, `Engine.InteractionMaster.AddInteraction`'s
`NewInteraction`, which the class text does declare, so the body scan still stops short of that line.

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


**A struct member's declared class is a `var` line in the class text, and it was the last typed residue.**
`StructMember` was the one object expression the reading did not know: `FaceWidget.Update()` evaluates the struct
member and then the call, and the member's own record names the member and its Outer the struct (`FaceWidget` ->
`SquadItemStruct`, `Message` -> `HUDLocalizedMessage`) while its class says only `ObjectProperty` -- the declared
class is a `var UIWidget FaceWidget;` line inside the `struct` block of the class that declares the struct.
`source_structs` parses those blocks (a nested struct's members belong to the nested struct, and a member declared
with two types is dropped rather than guessed), the tool indexes them once over the corpus into
`{struct: {member: type}}`, and the 25 sites that read `not an object expression` now name a class: **24 place on
the receiver's chain** -- 15 `UIWidget` (`SquadMenu.PostRender`'s five widget fields: `Fade` 6 with `Initialized`,
`Update` 5, `ChangeColour` 2, `SetEnable` 2), 6 `Engine.LocalMessage` (`Message.GetOffset` in both HUDs), 2
`StargateGame.SquadAIBasic` and 1 `Engine.TeamInfo` from `MindControlCrystal.ReleaseMind` -- and the twenty-fifth
types to `BotBase`, where `Possess` stays the dispatch it always was. Naming them takes by-name placement
**13,356 -> 13,385 of 13,507 (99.1%)**, the receiver's chain **2,738 -> 2,767**, and the untyped-object residue
**83 -> 53**; `inventory/script_calls.csv` now carries 97 call sites whose receiver expression is a struct member.
Five `Engine.MaplistManager` sites go with them, because the array there is a struct member declared `array<...>`
and its element type sits behind the same block.

**Two of the twenty-five needed the import table, and twenty-three rows had read a modifier as a type.** A class
that uses a struct another class declares *imports* its members, so `Message`'s record in `GoauldConsoleHud` is an
import whose own OuterIndex is the struct `HUDLocalizedMessage` -- six sites answered "the operand names no record"
until the branch followed that field instead of the export table. And `var config UIWidget Face;` puts a modifier
where the type belongs: 23 struct members were typed literally `config` in `script_calls.csv` before the modifier
list (`config`, `globalconfig`, `editconst`, `editinline`/`editinlinenotify`, `noexport`, `travel`, `input`, ...)
was accepted. `--check` drives fourteen assertions over synthetic blocks and hand-built expressions, so the parse
and its four negatives are testable without the dump.


**A static call to another package's function is an import too, and eleven sites were waiting on it.** The
`call_result` reading took a `FinalFunction` operand for a `Function` *export* of the package being walked, so the
twelve sites whose expression in front of the call is `PlayerOwner` -- a function `UWindow` declares and this
package imports -- read "names no Function of this package". The import's own OuterIndex names the class that
declares the function, so its return type is a statement in *that* class's text: `class_pairs` names the package
and `class_source` opens it. **131 call-result sites are typed now, against 75** (77 `playercontroller`, which the
UWindow source spells lowercase and the export table does not), **eleven of the twelve place**, by-name placement
goes **13,385 -> 13,396 of 13,507 (99.2%)**, the receiver's chain **2,767 -> 2,778**, and the untyped-object
residue **53 -> 42**.

**What the 42 are, and none of them is a reading the corpus can close.** 35 arrays whose element type is a struct
(an element of a struct array is not an object: a negative), 5 receivers whose name the caller's chain does not
declare, 1 local the class text does not declare, and 1 call result whose callee -- `UnrealScriptedSequence`'s
`Trigger` -- is declared with no return type. Seven further sites name an imported function whose class the
eighteen packages do not declare at all; each of those is still placed on the caller's chain, so none of them is
in the residue.

**The last local sat behind a preprocessor directive, and the declaration was in the text all along.**
`Engine.InteractionMaster.AddInteraction` is declared *twice* in the class text -- once inside `#ifdefXBOX`
(`string InteractionName, optional Player AttachTo, optional bool bInFront`) and once inside `#ifndefXBOX`
(`string InteractionName, optional Player AttachTo`) -- and the one body both branches share starts *after* the
closing `#endif`. The declaration scan collected the parameters of both (the build declares one signature per
branch) but then stepped to the next non-space character, found `#`, and never reached the `{`, so
`local Interaction NewInteraction; local class<Interaction> NewInteractionClass;` were never keyed and the site read
"the class source does not declare it". A directive line carries no braces, so stripping the directive lines before
the parse (`_src_text`, used by the declaration, return-type and struct passes alike) reads the body: the local
types `Interaction`, `NewInteraction.Initialize` places on the receiver's chain, and **the residue goes 42 -> 41 with
no local left in it** -- by-name placement **13,396 -> 13,397 of 13,507 (99.2%)**, the receiver's chain
**2,778 -> 2,779**, "decided by the receiver" 111 -> **110**, and two rows of `inventory/script_calls.csv` move.

The population is small, and the report now names it instead of hiding it: **4 declarations over 173 class texts**
carry a directive line between the declaration and its `{`, and they are two functions each declared twice under a
guard -- `InteractionMaster.AddInteraction` and `StargatePawn.Talk`. `Talk`'s site was already placed by the
corpus-only lookup; the strip fills its `receiver_type` (`StargateHUD`) and so says *why* the call reaches
`SetSubtitle`. A hundred and seventy-three texts carry directives and 4 declarations were split by one, which is
what says this is a parse rule and not a one-off patch for a single class.

**A chained call's callee gets the corpus lookup too, and the four that remain are dispatch, not a gap.** A call
in front of a call is typed from the callee's *declared* return type, and that callee was resolved on the caller's
super chain only -- so a `Context` whose inner expression calls a name the caller does not inherit stayed untyped even
though the call table has a second, receiver-free lookup for exactly that: a name **one class in the corpus declares**
resolves there whatever the receiver is. Giving the chained callee that same last step **places one site**
(`StargatePawn.CreateShield`'s `AskForShieldClass` -> `receiver:Engine.GameInfo`) and fills `receiver_type` on three
more rows without moving their placement, so by-name placement goes **13,397 -> 13,398 of 13,507**, the receiver's
chain **2,779 -> 2,780**, and the residue **41 -> 40**. The four chained callees that stay untyped are named by the
reason string now, with their declarer count: **`GetInstigator` 2 classes, `SetSprinting` 2, `TeamIDLookup` 5**, and
one callee (`UnrealScriptedSequence`'s `Trigger`) declared with no return type. That is a *dispatch* the static text
cannot decide -- several classes declare the name and the site carries no receiver of its own -- not a reading gap,
and it is the first residue shape in the table that a rebuild has to resolve at runtime rather than at parse time.

**What the 40 are, and one shape of them is left to a decoder.** 35 arrays whose element type is a struct (an element
of a struct array is not an object: a negative), 4 chained callees several classes declare, and 1 callee declared with
no return type. `--check` drives seventeen assertions over synthetic blocks, three of them the guarded declaration: a body after an `#endif`, a directive inside a struct block, and the census row that names a split declaration.

**An array element's type carries its modifiers inside the angle brackets, and that was most of the residue.**
`array<editconst editinline Stream>`, `array<export editinline ScriptEvent>`, `array<editconst editinline direction>`:
the declaration puts the `var` modifiers *inside* the array's brackets, and `_class_of_type` unwrapped the brackets and
handed the rest on as a name -- so the element type read as a class literally called `editconst editinline Stream`,
which names nothing, and **35 array-element sites answered "the array's type names no class"** for a class that is
spelled right there. The words `var` already accepts are now stripped from a tagged type before it is unwrapped, and a
type with a space before its closing bracket (`array<class<Info> >`, which the declaration regex captured one character
short, leaving `array<class<Info>` and never unwrapping it) closes properly. **All 35 place**, by-name placement goes
**13,398 -> 13,433 of 13,507 (99.5%)**, the receiver's chain **2,780 -> 2,815**, "decided by the receiver"
109 -> 74, and the untyped-object residue **40 -> 5**. That corrects a negative this page has published twice: the
"35 struct arrays" were **class** arrays whose element type was behind the modifiers (`StreamPlaylistManager`'s
`StreamPlaylist`, `ScriptEvent`'s `direction` and `ScriptEvent`), so their elements are objects and the gap was the
reading, not the build. 154 sites carry that element class as their receiver now (527 -> 681 over 50 -> 64
(token, class) pairs), and 30 of them keep the placement they already had while gaining the `receiver_type` that says
which class the array holds (`GUIComponent` 31, `MaplistRecord` 28, `ScriptEvent` 24, `direction` 9).

**A tag can name a struct, and the tool says so instead of saying nothing.** With the modifiers off,
`array<PlaylistParser>` and `array<editconst editinline direction>` are the same shape -- one identifier -- and only the corpus separates them: `source_structs` already holds every declared struct, so a type that names one is reported as
`declared as a struct (`PlaylistParser`), not a class` rather than as an expression that names nothing, and the branch's
own reason is kept when the type is neither. Nothing places differently (no array element is left in the residue); what
moves is **157 -> 3 rows of `receiver_type` that spelled a struct** (`vector` 99, `Rotator` 41, `IpAddr` 12,
`KeyValuePair` 3, `coords` 1, `MindControlSlot` 1) -- a column that names classes no longer claims a class called
`vector`. `--check` drives **25 assertions**, eight of them new: the three modified tagged types, the nested bracket
with a space, the two struct decisions, and the reason a branch keeps when its type names nothing at all.


**A receiver the class text never shows: `Outer`, which is typed at the root.** The 54 sites whose receiver types
to a class that declares no such name are listed with their names now (`--lists` prints the typed residue, the token
in front, that token's operand and the classes below the receiver that do declare the name), and **36 of the 54 are
one property**: the bytecode's token in front of the call is `InstanceVariable Outer`. `Outer` is `Core.Object`'s own
`var native const object Outer`, so its declared type is the universal base and *every* declaring class in the corpus
is below the receiver -- the descendant lookup cannot narrow it, and the callers (`Engine.CheatManager`,
`Engine.AdminBase`, `Engine.PlaylistParserBase`) extend `Object` rather than `Actor`, so the caller's chain has
nothing to offer either. What the set is, per name, is small and now written down: `ClientMessage` 3 declarers
(`Pawn`, `PlayerController`, `Security`), `ConsoleCommand` 4, `PlayerCalcView` 3, `Possess` 5, `Save` 5,
`TriggerEvent` 3, `ClientReStart` 2, `ValidIndex` 2, and the six SG1 sight toggles 2 each
(`ActivateAstralSight`, `DeactivateAstralSight`, `ActivateScarabPussSight`, `DeactivateScarabPussSight` and their
`StargatePlayer` overrides).

**And the shipped source spells those same calls with no receiver at all, which is a fact about the text, not the
build.** `Engine.CheatManager` calls `ClientMessage(...)` bare in its `ScriptText` and declares no `ClientMessage`;
the eighteen packages declare that name in exactly `Security`, `PlayerController` and `Pawn`, none of which is on
`CheatManager`'s chain -- so the text as shipped could not have compiled, while the bytecode carries `Outer` as the
receiver of every one of those calls. For a call site the bytecode is authoritative and the extracted source is not:
a reader who types the receiver from the source line reads no receiver where the build read `Outer`.

**The 14 `BotBase` receivers split three ways, and only one of them is still open.** Seven place on the single
descendant that declares the name (`IsUnconscious` 4 and `GetAggroLevel` 3 -> `descendant:StargateGame.BotEnemy`),
six stay a dispatch over the classes below `BotBase` that do declare it (`Possess` 2 over 10 bots, `CallToArms` 3 over
`BotEnemy`/`BotLite`/`BotSG`, `UnPossess` 1 over 7), and one (`SetPawnClass`) has no declarer below the receiver at
all. The remaining four rows are names declared outside the receiver's subtree -- `GetMoveTarget` 2 and `TakeOver` 1
on an `UnrealScriptedSequence`-typed field, `GetMyPlayer` 1 on a `Pawn` local. **None of the 54 is a parse gap:** no
name in the residue is absent from the corpus (0 rows), so what is left is a dispatch whose size is known, and the
only thing that would place the rest is a receiver from a running image.

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
     walks to the epilogue. (**Corrected 2026-11-12:** a walk also succeeds from almost *every* other delta — see
     "Where the script starts, anchored on the shipped source" — so this count is the opcode table's coverage and
     not a locator.) Of the payloads whose epilogue is at the 7-byte trailer position, **11,269 walk all the
     way**, 28 stop between 16 and 63 bytes, 36 between 64 and 255, 11 beyond 256 and 6 between 4 and 15 — the
     remainder is the opcode table's coverage, which the `index` correction above cut from 4,022 refusals to 81.
  3. **What the dword 16 bytes past the name is.** It is what a `ScriptStorageSize` field would carry, and it is
     **not that**: against the physical script length it is larger in **9,298** payloads and equal in **2,052**, never
     smaller. **Updated 2026-11-12:** the fourth word is identified — it is the script's size *in memory*, with
     every compact index operand widened to four bytes (`--sizefield`, "The dword before the script is its size in
     memory") — so it is larger than the stored script wherever the script has an index operand. The conclusion
     stands and for the same reason: no length field is needed there, because this one is not the stored length;
     the loader knows the payload's `SerialSize`, which fixes the script's end without reading a length.

  **So the script starts 16 bytes past `FriendlyName`, and every count in the earlier sessions can be read against
  that.** What the four words of the run are is now the open question, together with the table's unnamed opcodes: the
  header's encoded part is a fixed four values for a `Class` and five for a `Function` whatever their child count
  (`--header` reports the per-position classification), which is why the per-child `Children`-chain model this
  document used to propose is dead — and its +1/+2/+3 residuals were the compact encoding's widths. The fourth
  word of that run is no longer open (it is the in-memory size, above); what the first three encode is.
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
