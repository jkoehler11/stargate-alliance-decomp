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

## Not measured

* **Where a script starts inside a `Function` payload (U13).** The tail is solved (above), and the header's encoded
  part is now measured **with a control**: a `Class` runs the same `UStruct::Serialize` without `UFunction`'s tail, so
  anything that is the `Children` chain would scale with a class's children and anything fixed would not.
  `python3 tools/uscript-dis.py --header System` aligns the decode by requiring a value to be the export's own
  `FriendlyName` name index and counts the values in front of it:

  | kind | exports aligned | values in front of `FriendlyName` |
  |---|---:|---|
  | `Function` | 12,360 | **five** in 12,355 (four in 4, two in 1) |
  | `Class` | 2,318 | **four** in 2,316 (three in 1, two in 1) |

  **The count does not move with the export's child count.** `Object` carries 250 children and `Actor` 618, and both
  hold four values like `Time` with one — so the per-child `Children`-chain model this document used to propose is
  dead, and the +1/+2/+3 residuals of that prediction were the compact encoding's *widths*, not missing references.
  Classified against each package's own export, import and name tables, the values are:

  | kind | position | zero | import | same-name | other function | child |
  |---|---|---:|---:|---:|---:|---:|
  | Function | v0 | 12,360 | 0 | 0 | 0 | 0 |
  | Function | v1 | 6,728 | 3,226 | 2,406 | 0 | 0 |
  | Function | v2 | 783 | 0 | 0 | 10,529 | 0 |
  | Function | v3 | 12,359 | 0 | 0 | 0 | 0 |
  | Function | v4 | 3,137 | 0 | 0 | 0 | 9,218 |
  | Class | v0 | 1 | 1,275 | 0 | 0 | 1,042 other |
  | Class | v1 | 2,318 | 0 | 0 | 0 | 0 |
  | Class | v2 | 0 | 0 | 0 | 0 | 2,317 |
  | Class | v3 | 942 | 0 | 0 | 0 | 1,374 |

  So a function's header reads `[0][Super][Next][0][Children]` and a class's reads `[Super][0][Children][…]`: in both,
  one position is always zero, the value that resolves to a same-named parent is `Super`, the "other function" is
  `Next` and the child is `Children`. What `v0`/`v3` are attached to is not identified.

  **The fixed run after `FriendlyName` is still open, and the two kinds do not agree.** A dword 16 bytes past the name
  equals the distance to the script's epilogue in **2,141** `Function` payloads and in **zero** `Class` payloads; at 8
  bytes it is 39 functions and no classes. The walk from `name_end + 16` reaches the epilogue in 1,975 functions, and
  the ones it misses fail on the walker's own refusals (bad-token 3,824, unterminated parameter lists 1,617) spread
  across the whole opcode range — the signature of a desynchronised start rather than of a table gap. So the run's
  length is not settled, and until it is, no *long* shipped function is disassembled end to end.
* **Operand semantics.** A pattern says four bytes, not *which* reference; which object a `0x13` cast names, which
  function a `0x1c` calls, and what a `0x05` switch's byte means are all still to read, and each is per-opcode work
  on top of this table.
* **`0x60..0xff`.** The walker has rules for them and the table carries those rules, but nothing names them: UELib
  stops at `0x5f`. Whether they are extended natives, engine-private tokens, or reserved is open.
* **Compressed scripts.** `UStruct::Serialize` appends a `'G'` marker when a script is stored compressed; no shipped
  payload has been found in that state yet, and this table describes the uncompressed walk.
* **Whether every shipped script walks clean.** The engine asserts on a mismatch, so the corpus is presumably
  self-consistent, but that has not been tested here — it follows from U13.
