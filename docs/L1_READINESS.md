# How far each class is from compiling clean (L1)

`docs/STATUS.md` prints `--` for L1 because it has never been measured. It can be derived from the emitter's own verdicts
without a new parser: `tools/uscript-emit.py` decides every body in the dump — modelled, or refused with a reason — and
`tools/l1-ready.py` joins its two outputs per class.

Measured 2026-11-22, over the 18 shipped script packages:

| | |
|---|---:|
| classes with at least one body | **1200** |
| bodies in those classes | **11678** |
| modelled | 3056 (26.2%) |
| refused | 8622 |

## The three sets

| set | classes | bodies |
|---|---:|---:|
| **fully covered today** — every body modelled, so nothing in the emitter blocks them | **61** | 129 |
| **one refusal away** — a single body stands between the class and coverage | **334** | 605 |
| the rest | 805 | 10944 |

`inventory/l1_ready.csv` carries the row behind each of these, with its class's first refusal reason.

## What the refusals are

| reason | bodies |
|---|---:|
| a nested block: control flow is not modelled | 3893 |
| 2 statements | 1431 |
| 3 statements | 764 |
| 4 statements | 409 |
| a statement that is control flow | 322 |
| 5 statements | 303 |
| a call the statement walk cannot bound | 283 |
| 6 statements | 218 |

**One shape dominates.** A nested block is control flow, and it accounts for 3893 of the 8622 refusals — more than
every multi-statement and expression reason combined. The emitter's families so far (constant return, one call, one
assignment, class-level stub) are the bodies that need no branch, no jump and no expression tree; the rest need exactly
that.

## What this says about the distance

L1 is **not** nine thousand bodies of undifferentiated work. It is:

1. **61 classes ready now** — the first honest L1 measurement is cheap and should be taken before more emitter work,
   because it is the only end-to-end path (source → bytecode → package) that has never been run.
2. **334 classes one body away** — the cheapest unit of emitter work buys 334 classes, and these are the classes to
   read next when choosing the next family, rather than the largest ones.
3. **Control flow** is the wall: nested blocks plus the multi-statement and control-flow-statement reasons.
4. **The stall point is a decision, not a count**: everything past (3) needs the statement walk — labels, jump targets,
   operand stacks and type coercion — which is a different kind of work from what the current families do.

## What is already proven, and what L1 would still add

The temptation is to read "61 classes fully covered" as "61 classes compile". Three measurements bound that, and the
boundary matters more than the number:

1. **Every modelled body is byte-identical to the shipped package.** 3,056 of 3,056, zero differ — the emitter supplies
   the bytes, not a copy of them.
2. **Every package rebuilds byte for byte.** `tools/pkg-write.py --rebuild` reports **18 of 18**, each with *100% of bytes
   accounted for, 0 gaps*: header, name/import/export tables and every payload explain the whole file. There is no region
   a writer would have to invent.
3. **61 classes have every one of their bodies modelled** (`inventory/l1_ready.csv`).

Put together, a class in that set can be regenerated end to end today and the result is the shipped file. That is a real
result, and it is *reproduction*, not compilation — which is exactly where the remaining risk sits.

### What has not been exercised

* **A body that differs from the shipped bytes.** None exists yet: the family was built body by body until each matched.
  So the emitter has never produced output the package did not already contain.
* **A payload whose size changes.** Bodies are byte-identical, so no export ever grew or shrank in a real rebuild. The
  writer's resize path has only been driven synthetically — 84 of 84 payload resizes round-trip, with filler bytes and no
  meaning.
* **Loading a rebuilt package.** Nothing has been loaded by the game. `pkg-write` proves the bytes, not that xemu accepts
  the result, and that is the only test that would make L1 a measurement rather than a derivation.

So L1's honest statement today is: *"61 classes would regenerate identically, and 18 of 18 packages re-encode with every
byte accounted for; no class has been rebuilt from changed bytes, and none has been loaded."* The next unit of work that
would move that line is not another body family — it is one changed body, assembled and loaded.

## The changed-body experiment: first attempt (2026-11-22)

The section above says the missing step is "a body that differs, assembled and loaded". That experiment was started, and
it did not finish. What it established is worth recording, because each item is a fact the next attempt starts from.

**A function's payload is not its script.** For `StargateGame.LogDropped` the 34-byte payload is a **24-byte header + the
3-byte script `04 0b 47` + a 7-byte epilogue**. `uscript-dis.locate` already states this rule (`start = name_end + 16`,
`end = n − 7`, or `n − 9` for a Net function), and any writer has to splice inside those bounds rather than replace the
payload.

**A changed script moves more than its own bytes.** Replacing those 3 bytes with 9 (`return 666;` plus the epilogue) grows
the payload 34 → 40, and the file **3031678 → 3031705**: +27 bytes, so **21 of them are not the payload**. The export table
grew because payload offsets crossed a compact-index boundary — which is exactly the circularity `pkg-write`'s docstring
warns about, and it means the header's three table offsets are not a constant shift.

**Patching the header was necessary.** The first run left the header pointing imports and exports at their original
addresses, and the reader called the result `unaligned`; writing the measured `names` / `imports` / `exports` positions back
(offsets 16, 24 and 32) is part of any writer that moves a payload.

**But the `unaligned` that survived that fix was not the writer's fault — it was the experiment's own.** `e["index"]` is
**0-based** in this reader (export 5464 carries index 5464), and the tool resolved a function by returning `i + 1`. So it
rewrote the export *after* the one it named, reported success on the size it had chosen, and left the reader to discover a
payload whose six leading indices ended on the wrong name. Correcting the base to `e["index"]` closes it:

| | |
|---|---|
| script | 3 → 9 bytes (`04 0b 47` → `04 1d 9a 02 00 00 04 0b 47`) |
| payload | 34 → 40 |
| file | 3,031,678 → 3,031,684 (**+6**, no table growth this time) |
| read back | script **exact**, every other payload **untouched** |

So the container **does** accept a changed body of a different length, and `tools/l1-change.py` reproduces that on demand.
The lesson is worth keeping: an off-by-one in a verification harness looks exactly like a broken writer, and this one was
recorded as a writer defect for a few hours.

**What is still untested is the load.** Nothing has been handed to the game: the bytes are accepted by this project's
reader, which is not the same claim as xemu accepting them. That is the next experiment, and it needs the ISO rebuilt with
the changed package rather than another parser.

## The load test: the game boots with a body this project wrote (measured 2026-11-22)

Everything above is this project's reader agreeing with itself. This section is the other question — whether the game
accepts the bytes — and it does.

| | |
|---|---|
| package | `System/StargateGame.u` 3,031,684 bytes, sha256 `99029b19508bad974ef83e7cad880f762c5125a8ca17b8664517d6ae0c9a4878` |
| what changed | `StargateGame.LogDropped`, script 3 → 9 bytes, payload 34 → 40 |
| ISO | `sg1-changed.iso` 2,258,370,560 bytes, sha256 `38007f94f25aa9012698a2788a4f70f9fdba1d0f1c94aa5e5e816d6503de72d3` |
| boot | xemu, debug BIOS, reached the **title screen** — frame sha256 `727f79272889085f99bb00a826a4a119cb2f36d065f45a78c91791bb00769a44` |
| frames | two shots 25 s apart were **identical**: the title screen is static, not a crash loop |

The ISO is the same size as the original, because six extra bytes fit inside the sector padding — worth knowing before
anyone reads a matching size as "nothing changed".

**What this proves:** the game's own package loader parses a package whose payload this project wrote, at a different
length than the original, and boots to the title. **It also then freezes there and ignores input**, which the control boot
does not — so read this table as "the loader got that far", not as a working build. The matrix below is the result that
matters. That is the claim the reproduction work could not make on its own.

**What it does not prove:** that the changed function *runs*. `LogDropped` is not on the title path, so the boot exercises
the loader and not the new script. A behavioural test needs a function that fires during a boot, and a change whose effect
is visible — the next experiment, not this one.

**A note on the failure that looked like one.** The driver reported `differing pixels: 0 (threshold 200) FAIL` between the
70 s and 95 s frames. That is not the game failing to render; it is the driver assuming the screen would keep changing. A
static title screen is the success case here, and the harness reads it as a repaint failure — so the frame has to be
looked at, which is how this section exists.

The frame is not committed: it is game content (see `AGENTS.md`). The hashes above are the evidence, and the ISO and dump
copy are reproducible from `tools/xemu/build-xiso.sh` and `tools/l1-change.py`.

## Structural changes freeze the game; in-place byte edits do not (measured 2026-11-22)

Six builds, one variable at a time. "Responds" means the pad taps changed the frame (`pad=start` then `pad=a`, pixels
differing against the previous shot); "frozen" means two shots 25 s apart were byte-identical and the pad did nothing.

| build | change | file size | payload offsets | result |
|---|---|---|---|---|
| original | — | 3,031,678 | — | title, **responds** (48,350 / 74,254 px) |
| no-op rebuild | none | 3,031,678 | unchanged | **byte-identical** to the shipped package (`89d9f93c…`) |
| in-place | `QueueForceFire` `IntConst 8192 → 8193` | 3,031,678 | unchanged | title, **responds** (25,130 / 69,014 px) |
| relayout | `LogDropped` script 3 → 9 bytes | +6 | every payload after the target moves | title, then **frozen** (0 px) |
| insert | `LogDropped` script 3 → 9 bytes | +41 | payloads fixed, tables move | **frozen** |
| pair | `LogDropped` +6 **and** `FinishAlerting` −6 | **3,031,678** | payloads between the two move | **frozen** |

What the matrix says, and what it rules out:

- **Not size.** The pair build is byte-for-byte the original length and still freezes; the in-place build changes four
  bytes and passes.
- **Not content hashing.** If the game verified package digests, the in-place edit would fail too. It does not, so
  `System/Packages.md5` is not gating this.
- **Not the bytes themselves.** The no-op rebuild regenerates the package exactly, so the writer's model of the container
  is faithful when nothing moves.
- **It is structural.** The loader accepts the shipped tables and offsets and does not accept a package whose payload
  offsets or table positions differ — even when this project's reader parses the result cleanly and every other payload
  is verifiably identical.

**Consequence for L1**, stated as a constraint rather than an estimate: a recompiled package whose bodies all keep their
shipped lengths will load. A package where any body's length changes will not, until whatever the loader compares is
found. That is a narrower target than "finish the emitter", and it is the one that actually gates a bootable rebuild.

**Next**: find what the loader checks. The candidates in order of plausibility are a summary field this project does not
model, an alignment or ordering rule on the export table, and a length recorded outside the package. The instrument is
the xemu breakpoint harness, not another parser: the question is what the *game* does with the file, so the answer has to
come from the game.

**Two hygiene notes from the same run.** `cp -al dump-local dump-chg` followed by `cp new dump-chg/System/X.u` **writes
through the shared inode** — `cp` does not break a hardlink, so the "pristine" copy was silently modified and the next
experiment ran from changed bytes. Remove the target first. And a function **name is not a unique handle**: `LogDropped`
is carried by two exports and `QueueForceFire` by more than one, so `tools/l1-change.py` now takes `--index` to target
one export exactly.

## The frozen title is a fault, not an idle screen (measured 2026-11-22)

Sampling EIP for 170 s over the rebuild that "hung" gave **one distinct address in 680 samples** — the guest was not
waiting for input:

| register | value | what it means |
|---|---|---|
| `EIP` | `0x8001d1ea`, `HLT=1` | the kernel's idle loop; no game code is running |
| `CR2` | `0xfff0007b` | the last page fault was a dereference of an invalid address |
| `EFL` | `0x82` | halted with the sign flag set |

So the game **faulted** while using the rebuilt package and the kernel idled; the title still on screen is the last frame
it drew. Every earlier sentence of the form "the game ignores input" described a corpse, and the driver's
`differing pixels: 0` was reading a stopped guest, not a static screen.

## Payload offsets are the constraint (measured 2026-11-22)

One more build separates the remaining factor, and it changes the conclusion. Insert six pad bytes **before the imports
table**, patch the header's two table offsets, change nothing else: every payload keeps its offset, the file grows by 6,
and the tables move.

| build | file size | payload offsets | result |
|---|---|---|---|
| original | 3,031,678 | — | responds (48,350 / 74,254 px) |
| in-place `IntConst` edit | 3,031,678 | unchanged | responds (25,130 / 69,014 px) |
| **table shift +6, no content change** | **3,031,684** | **unchanged** | **responds (26,664 / 66,837 px)** |
| relayout (`LogDropped` +6) | 3,031,684 | every payload after target moves | fault |
| insert (payloads fixed, tables moved) | 3,031,719 | target offset jumps to end of region | fault |
| pair (+6 and −6) | 3,031,678 | payloads between the two move | fault |

The middle row is the one that matters: **a different file size and moved tables are fine**. The combinable facts are now
narrow and they agree:

- file size may change;
- the three tables may move, and their content may change;
- payload **content** may change in place;
- payload **offsets** may not.

That last one is what a length-changing body edit cannot avoid: growing one payload pushes every later payload along, and
the loader faults on a package where they have moved — even though this project's reader accepts the file and every
payload is verifiably identical.

**Consequence for L1, now a hard constraint rather than an estimate.** A recompiled package loads only if every payload
keeps its shipped offset. In this package that means a body may not change length in the middle of the file: there is no
slack inside a payload (`LogDropped` is 34 bytes = 24 header + 3 script + 7 epilogue) and no way to give one payload more
room without moving the next. Either the loader's reason is found and worked around, or the first bootable recompile is
restricted to same-length bodies — a real and useful target (the emitter already reproduces bodies exactly), but not the
general one.

**Next instrument, unchanged and now better aimed:** the fault is a dereference of `0xfff0007b`. Finding the instruction
that forms it — breakpoint on the page-fault handler, read the stack at the fault rather than after the kernel idles —
names the loader field that was read. That is a job for the debug harness, not for another parser.

## The first body from edited source that the game boots (measured 2026-11-23)

Every earlier boot used a body this project *chose*; this one used a body the **emitter produced from source it had never
seen**. The chain, with nothing hand-assembled:

| step | value |
|---|---|
| source | `Engine/GameInfo.uc` `GetDefenderNum`: `return 255;` → `return 200;` |
| emitter | `ours = 042cc8040b47`, `theirs = 042cff040b47`, verdict **`differ`** — both six bytes |
| splice | export index **16777** in `Engine.u`, same length: file 3,090,635 → **3,090,635** |
| package | `Engine.u` sha256 `9c4c07ff87429c1190f79b8d27df3f5c6bc4f3810489c9216342d06d137883d0` |
| ISO | 2,258,370,560 bytes, sha256 `3919efaf002487a45d4d662606f09acd316af9e7cb577be1f7ad66c3b319d646` |
| boot | title, then `pad=start` and `pad=a` both repaint — **6,216** and **55,854** px |

The emitter had been scored only against bodies it reproduced exactly, so "it emits bytes" and "the game accepts those
bytes" were separate claims. They are now the same claim: this is the first time a byte sequence generated from edited
source has run inside the game.

**What it proves:** a compiled body at the shipped length is accepted, and the package containing it boots and takes input.
**What it does not prove:** that the value is *observed* — `GetDefenderNum` is not on the title path, so this exercises the
loader and the script's presence, not its result. And it does not lift the length constraint: the body was chosen because
`255` and `200` share the `IntConstByte` encoding, which is exactly the property a general recompile cannot assume.

**The method it establishes** is the useful part: edit source → run the emitter → read `ours` from
`inventory/uscript_emit.csv` → splice by index at the same length → build the ISO → boot with the pad steps. That is the
loop a recompiled package will have to survive, and it now has a working example.

## Fitting a compiled body back to its shipped length (measured 2026-11-23)

The previous section's body happened to come out at six bytes. This one does not, and the difference is the whole point:
`return 0` compiles to `0425040b47` — **five** bytes, one shorter than the shipped body — which a length-changing splice
would have refused (or faulted on, if forced).

`tools/uscript-fit.py` closes that gap by using the encoding the compiler itself varies, not an invented no-op:

| step | value |
|---|---|
| source | `Engine/GameInfo.uc` `GetDefenderNum`: `return 255;` → `return 0;` |
| emitter | `ours = 0425040b47` — **5 bytes** (`04 25` = `IntZero`) |
| fitted | `042c00040b47` — **6 bytes** (`04 2c 00` = `IntConstByte 0`, same value) |
| splice | index 16777, 6 → 6: `Engine.u` 3,090,635 → **3,090,635** |
| package | sha256 `dc8da69effaabd4f394f666fd9741089ac155b214910bd961aed0d9286504d53` |
| ISO | 2,258,370,560 bytes, sha256 `013cac57a6049733e6c67eaac7e964997fa4fec1ba086507ee5e20f1e25aa97e` |
| boot | title, then both pad taps repaint — **25,334** and **68,747** px |

The same integer can be written `IntZero`/`IntOne` (2 bytes), `IntConstByte` (3) or `IntConst` (6), so a value that compiles
short can be **widened** to reach the shipped length without changing what it computes. `--check` holds five fittings and
two refusals: a value that needs more bytes than the target (`400` in six) is refused rather than approximated, because
this bytecode has no no-op to pad with and silently truncating a constant is not a fitting.

**What this proves:** the emitter's output is not required to match the shipped length by luck — the length can be met
deliberately, at the source level, and the game boots the result.
**What it does not prove:** that a body which *needs* more bytes can be made to fit. That case is the remaining wall, and it
only moves by finding what the loader dereferences (`CR2=0xfff0007b`) or by shrinking the compiled code itself.

The working loop is now three tools in order: `uscript-emit.py` (source → bytes) → `uscript-fit.py` (bytes → shipped
length) → `l1-change.py --index` (bytes → package, same length) → `build-xiso.sh` → `drive.py` with the pad steps.

## The fault address is a kernel hardware probe, not a package pointer (measured 2026-11-23)

The previous section read `CR2=0xfff0007b` as a corrupted pointer and proposed finding "the loader field that was read".
That was an over-read of the evidence. `-d int` on the emulator records the faulting instruction, and the disassembly says
something else:

```
49fe1:  f6 05 7b 00 f0 ff 80   testb $0x80,0xfff0007b
```

An absolute operand — `testb $0x80` against a **hardcoded address** — so `0xfff0007b` is not formed from package data at
all. It is an unmapped hardware window that the kernel touches on this path, and the fault is the kernel's own probe
failing, in code at `0x80049fe1`, with `EAX=1`, `ECX=0x3f8`, `EDX=0x3f9` (COM1's data and interrupt-enable ports) live in
registers. The surrounding instructions store a flag at `0x8005049e`, compare against `0x8005049c` and install
`0x8004a320` — kernel bookkeeping, not a game object.

**So the useful question is not "which loader field was read".** The guest is idle (`EIP=0x8001d1ea`, `HLT=1`) because the
game's work has stopped, and the page fault is a symptom on the way there, not necessarily the cause. The next instrument
is a breakpoint on the halt and on the kernel's exit path, to see what the game called last — not another read of the
package.

**Two measurement notes that cost time and will cost it again:**

- `pmemsave` takes **physical** addresses. The guest runs paged (`CR0` PG set, `CR3=0xf000`), so a dump of physical
  `0x80000000` is not linear `0x80000000` — the first dump disassembled to `...` (zeros) and looked like a missing kernel.
  In this build the kernel is identity-offset: linear `0x80049fe1` is physical `0x49fe1`, which is why the low-memory dump
  shows the faulting instruction.
- `xemu` merges its settings file with the command line. Passing `-machine`/`-drive` for something the config already
  defines fails with `drive with bus=0, unit=0 (index=0) exists`, and the run exits in seconds with an empty log — three
  attempts died that way before the config-only run worked. To add a QEMU flag, put the machine in the config and pass only
  the flag.

## The fault is specific to the rebuilt package (measured 2026-11-23)

The same emulator flags on both builds, 100 s each:

| ISO | log | page faults |
|---|---|---|
| original | 161,944,027 bytes | **0** |
| rebuilt (`LogDropped` 3 → 9, relaid out) | 43,213,771 bytes | **1**, at `IP=80049fe1` |

So the fault belongs to the rebuild and not to the emulator or the machine: the original never executes the instruction
that touches `0xfff0007b`.

Put together with the disassembly (`testb $0x80,0xfff0007b`, an absolute operand, COM1's ports live in registers) and the
idle end state (`EIP=0x8001d1ea`, `HLT=1`), the reading that fits all three observations is:

1. the game reaches a **fatal condition** while using the rebuilt package;
2. the kernel's error-reporting path runs, touches a device window that is unmapped here, and faults;
3. the machine ends in the idle loop, with the last frame — the title — still on screen.

That ordering matters for what to do next: **the page fault is not the bug, it is the report of the bug.** Chasing
`0xfff0007b` further would be chasing the messenger, which is why the previous section's "find the loader field" is
withdrawn rather than merely unconfirmed.

**What the next experiment has to separate** is "payload offsets changed" from "a payload's size changed", because every
build that faulted changed both. The clean test is a rebuild that moves payloads without changing any size: write the same
payloads in a different file order, update the export offsets, and leave every byte and length intact. If that faults,
offsets alone are fatal and the constraint is exactly as stated; if it boots, size is implicated after all and the
constraint is narrower than this page currently claims.

**A note on the failed attempt inside this one.** Reading the kernel stack to name the caller did not work: `ESP` was
`0xd016514c`, and the dump at physical `0x16514c` was solid `ff000000` fill, not a stack. The alias arithmetic was a guess
(`0xd0000000` treated as a flat RAM alias); the correct resolution needs the page tables (`CR3=0xf000`,
`tools/xemu/vamap.py`). Recorded so the next session starts from the page tables instead of repeating the guess.

## The constraint is a payload's size, not its offset (measured 2026-11-23)

Two builds settle it, and one of them contradicts the section above.

| build | payload sizes | payload offsets | result |
|---|---|---|---|
| in-place `IntConst` edit | unchanged | unchanged | plays (25,130 / 69,014 px) |
| **every payload shifted +4 bytes** | **unchanged** | **all 13,425 moved** | **plays (21,885 / 68,326 px)** |
| pad the tables by 6 | unchanged | unchanged | plays (26,664 / 66,837 px) |
| **valid 5-byte body spliced into a 3-byte one** | **34 → 36 bytes** | later payloads move | **faults** |
| relayout 3 → 9 | 34 → 40 | later move | faults |
| insert 3 → 9 | 34 → 40 | target jumps to the region end | faults |
| pair +6 / −6 | two payloads change | between them | faults |

The second row is decisive against the earlier claim: **moving every payload is fine**. A uniform shift changes all 13,425
offsets, leaves every byte and every size alone, and the game plays — so `docs/L1_READINESS.md`'s "payload offsets may not
move" is wrong and is retracted here.

The fourth row is decisive the other way, and it rules out the explanation I wanted: that the earlier faults were caused by
*invalid* bytecode. The body spliced there is `IgnoreTeleport`'s shipped `04 28 04 0b 47` — a real, balanced, shipped body,
not the `IntConst 666` stack imbalance I had been using. It still faults when `LogDropped`'s payload grows 34 → 36.

**So the rule is about a payload's length, not its position:** a payload may move, and a body may change in place, but a
payload's serialised size may not change. Every build that changed a size faulted; every build that did not, played.

**Why that is still unexplained, and what it is not.** The obvious candidate — a length field inside the payload header that
a writer must update — did not hold up: the dword at `+20` matches the script length for only 54 of 3,256 functions, and
the dword at `start − 4` for 561. Neither is the field. So either the length is recorded somewhere this project has not
modelled, or the loader uses the export's `size` in a way that a consistent re-layout still violates.

**What this means for a bootable recompile, unchanged in practice:** bodies must keep their shipped length. That is what
`tools/uscript-fit.py` exists for, and it is verified three times over (the `return 200` boot, the fitted `return 0` boot,
and the shipped-body test above). A compile that *needs* more bytes than the shipped body still cannot load.

## A size change survives the title and dies at the next transition (measured 2026-11-23)

The last test was built to remove every confound in the earlier ones. `IgnoreTeleport`'s body turned out to return `False`
(`0x28` is `False`, not a void token), so splicing it into a void function was the same type mismatch as `IntConst 666` had
been — my "valid body" claim was wrong too. The clean version is `04 0b 04 0b 47`: `Return Nothing`, then three dead bytes
the VM never executes. Semantically identical to the shipped `04 0b 47`, two bytes longer, same return type.

| build | payload | start | then A |
|---|---|---|---|
| original | shipped | 48,350 px | 74,254 px |
| `04 0b 04 0b 47` (**+2 bytes, dead code**) | 34 → 36 | **3,365 px — repainted** | **0 px** |
| every earlier size change (`IntConst`, `False`, relayout, insert, pair) | changed | 0 px | 0 px |

That is a different failure from the ones above it: this build **responds to the first input** and then stops changing the
screen. So a payload's size change is not rejected at load — the title still works — and the damage shows up at the next
transition, which is what a length read *later* would look like (a lazy preload, a size check on first use, or a buffer
sized from the old length).

**What this does not say:** whether the game died at the A press or is sitting on a static screen. Earlier in this page the
same `0 px` was a corpse with the last frame on screen, and only an EIP sample distinguished the two. That sample has not
been taken for this build, so the row above reports what the screen did and no more.

**Where that leaves the rule.** "A payload's size may not change" still describes every build, but it is now known to be a
*late* consequence rather than a load-time rejection, which makes the search narrower: the thing that reads the old length
runs after the title, so it can be found by breaking on the transition rather than by staring at the package format.

**The practical answer does not move:** ship same-length bodies. Every build that kept its sizes played; the fitter exists
to keep them, and it refuses what it cannot fit.

## The late failure is the same corpse (measured 2026-11-23)

The row above left one question open — whether the build that stopped repainting after `pad=a` had died or was sitting on
a static screen. Registers answer it:

| after the transition | value |
|---|---|
| `EIP` | `0x8001d1ea`, `HLT=1` |
| `CR2` | `0xfff0007b` |

That is the same end state as every size-changing build: the kernel idle loop, with the last fault against the unmapped
window. So the `04 0b 04 0b 47` build did not survive the transition and stop drawing — it **died there**, later than the
others only because the payload it changed is not touched until the first input.

**The rule is therefore unchanged and now has its timing measured:** a payload may move, a body may change in place, and a
payload whose size changes loads fine and kills the guest the first time something uses it. Everything that kept its sizes
played; everything that changed one died at the same address.

**What that means for the search**: the code that uses the old length runs between the title appearing and the first
transition, so a breakpoint there is the instrument. Nothing about the package format needs to be re-read first — the
target is now a time window, not a field.

## A halted EIP sample means idle, not dead (measured 2026-11-23)

This page has used `EIP=0x8001d1ea` with `HLT=1` as the signature of a dead guest. It is not a sufficient one. Sampling the
build whose `LogDropped` payload grew 34 → 36 returned that address for **every** sample — 500 of 500 starting at 15 s, and
300 of 300 starting at 60 s — and the same ISO then **rendered its title** in a driver run at 70 s and repainted on a pad
tap at 82 s.

The kernel idles in `HLT` while its threads wait, so a sample can land there for an instant at any time. What distinguishes
idle from dead is the **window**, not the value:

- a build that samples *exclusively* at the idle address across a window in which the original is executing real code is
  stalled or dead;
- a single halted sample, or a handful, says only that the CPU was idle when it was read.

**What this retracts**: statements on this page of the form "the guest is dead here" that rested on one register dump taken
after a known input. They were consistent with the pixel evidence, but the register evidence alone did not establish them —
and on this build it flatly contradicted the driver's own repaint count.

**What survives**: the boot matrix, because it is pixel evidence from the driver with the original as control in the same
run; and the size rule, because it does not depend on any register reading — every build that changed a payload size failed
visibly, and every build that did not played.

**Tooling note for the next session**: `tools/xemu/eip-functions.py` does **not** isolate `XDG_DATA_HOME`, so it runs
against the real `xemu.toml` and `eeprom.bin` rather than a per-run copy the way `drive.py` does. Its launch parameters are
otherwise correct, and its samples are what they are — but compare against a control run before drawing a conclusion from a
single address.

## Two instruments disagree on the same ISO, and the difference is input (measured 2026-11-24)

The sampler and the driver describe the same `LogDropped` 34 → 36 build differently, and this page has now quoted each of
them against the other:

| instrument | input | what it saw |
|---|---|---|
| `drive.py` | `pad=start` at 70 s, `pad=a` at 82 s | title, then **3,365 px** repaint, then nothing |
| `eip-functions.py` | none (with or without `--vpad`) | **one** address, `0x8001d1ea` + `HLT=1`, for every sample |

The simplest reading that fits both: **a title screen waiting for a button is idle**. The kernel idles in `HLT` until an
input arrives, so a sampler that never presses anything sees the idle loop and nothing else — on this build and, in the
same window, on a build that plays. Pressing Start is what moves the guest, and that is exactly what the driver does and
the sampler does not.

`eip-functions.py` now takes `--vpad` and binds port 1 to the virtual pad, because a controller-less boot is its own
distinct failure — but the pad alone does not make its numbers comparable to a driver run. **A trace of the transition
needs a press**, which is the next instrument to build: a tap at a given second, then sampling immediately after it.

**What this means for the claims above:** the boot matrix is unaffected (pixel evidence, both builds, same run). The
register-and-EIP statements are the ones that keep failing to reproduce, and they should be read as hypotheses until the
sampler can press a button.

**A note on the cost.** Three separate wrong answers this session came from a single instrument used without a control
that exercises the same inputs: a register dump after a press, a sampler with no press, and a sampler without a pad. The
pattern is not subtle in hindsight — **any measurement of a game must supply the input the game waits for**, or it measures
the waiting.

## The tap did not fix the sampler (measured 2026-11-24)

The section above proposed a tap as the missing piece. It has now been tried, and it is not sufficient:

| attempt | result |
|---|---|
| `--vpad`, no tap | 400 of 400 samples at `8001d1ea`, `HLT=1` |
| `--vpad --tap-at 30:start --tap-at 42:a` (70 s and 82 s wall time) | **400 of 400**, unchanged |
| adding `-device usb-hub,port=1,ports=4` | emulator refused to start — `FAIL: no QMP socket`, because xemu passes that hub **itself** in every launch line and the flag was a duplicate. Reverted. |

So the sampler still reports a guest that never leaves idle on a build whose driver run repaints at 82 s. Two instruments, one
ISO, contradictory results, and the difference is not the pad, not the tap, and not the hub.

**What that leaves standing.** The pixel evidence is the evidence: a driver run presses the same buttons the game waits for,
in the same process, and sees the screen change. The EIP sampler, in this build and at these times, does not reproduce that
boot. Until it does, its numbers are not usable for locating the failure and this page should not quote them as if they were.

**What was kept anyway.** `--vpad` and `--tap-at` are correct on their own terms — a controller and a press are what this
game needs — so they stay in `tools/xemu/eip-functions.py`. They made the sampler *comparable in principle*; the thing that
makes it reproducible in practice is still unknown.

**The honest next step**, if this thread is picked up: run the sampler and the driver in the same session against the same
ISO and dump both launch lines side by side, rather than comparing a sampler run from one hour with a driver run from
another. Every contradiction in this page came from comparing runs that differed in ways nobody wrote down.

## A class payload grown by four bytes fails the same way a script does (measured 2026-11-24)

Every size experiment on this page until now changed a **Function** payload, so "the script's own length is read" was a
live explanation. It is now dead: the same experiment on a **Class** fails identically.

| export | kind | payload | file | start tap | next step |
|---|---|---|---|---|---|
| `LogDropped` | Function | 34 → 36 | 3,031,678 → 3,031,680 | **0 px** | 3,365 px |
| `UIWidget` | **Class** | 987 → 991 | 3,031,678 → 3,031,682 | **0 px** | **3,365 px** |

Both builds render the title, both ignore the Start press, and both then produce the *same* 3,365 px on the following step —
the number a working build turns into 21,885 px and then 68,326 px. The resize went through `pkg-write.py`'s own `relayout`,
which re-places every region and re-derives the export table (13,425 exports before and after, all readable).

**What this rules out**: that the constraint lives in UnrealScript bytecode, in `ScriptEnd`, in the script-size field at
`+20`, or in anything a Function payload contains. Those are all now irrelevant to the failure.

**What it leaves**: something consumes the **size of any payload**, not the bytes inside one. Two results bracket it:

- **sizes unchanged, everything moved** (`+4` shift of all 13,425 payloads) — **plays**;
- **one size changed, everything else identical** — **fails**.

So position is free and length is not, and the difference is per-object.

**The next probe is cheap and specific**: change a payload's size by a *large* amount (say +64) on a class nothing loads at
boot, and see whether the failure is identical or later/earlier. A failure whose *timing* does not move with the size change
points at a check performed once, at load; one that moves points at a read of that object's length where the object is used.

## A valid longer body fails too, and the base was contaminated (measured 2026-11-24)

**First, a correction to the working copy.** `~/sg1/work/dump-local/System/StargateGame.u` had been replaced by the `+64`
build, because `cp -al` creates **hardlinks** and a later plain `cp` wrote *through* the link into every tree at once. Three
dump trees shared one inode. The file was restored from `~/sg1/dump-pristine/Stargate_SG-1_-_The_Alliance-2005-12-15/` and
verified against the manifest:

```
3,031,678 bytes   sha256 89d9f93c37717648f38fbefe1256038aea6476e2ff09c8efe989da2425af465c   link count 1
```

Every boot result on this page stands — each build used its own package — but any experiment built *from* `dump-local`
while it was contaminated is void, and one was re-run below. **A tree copy must `rm` the target before `cp`.**

**The clean experiment.** `LogDropped`'s shipped body is `04 0b 47` — Return, Nothing, EndFunction: a void function. It was
replaced with twelve bytes of the same shape, nine `Nothing` statements then the same return:

```
File          script 3 -> 12 bytes   payload 34 -> 43   file 3,031,678 -> 3,031,687
Result        title renders, then 0 px on the start tap, 0 px on the next step -> FAIL
```

Valid content, one size change, no invalid bytecode anywhere: it still fails, and it fails harder than the earlier `+6`
build (which at least repainted 3,365 px afterwards).

**So content validity was not the explanation either.** What remains is the size itself — with one anomaly that has to be
settled before that is written as a rule: the fitted `GetDefenderNum` body changed its export size from 5 to 6 and **booted**,
and its package did not change length. Either the size field did not actually change (unverified), or a size change absorbed
by alignment is tolerated.

**The rule this page will state until that is checked**: a size change that changes the file's length fails; one that does
not change it has not been shown to fail. The experiment that settles it is a table-only edit — raise one export's size by
one byte without touching a payload — and that is the next probe, not another body.

## The size field is load-bearing, and the last anomaly dissolves (measured 2026-11-24)

**The single-variable experiment.** One export's recorded size was raised by one byte in the export table alone — no payload
byte touched, no region moved, the file the same length, one byte different on disk:

```
UIWidget   SerialSize 987 -> 988   file 3,031,678 -> 3,031,678   bytes changed 1
Result     title renders, then 0 px / 0 px on both following steps   -- FAIL, harder than any payload edit
```

So `SerialSize` is not a hint the loader recomputes; it is consumed, and a lie about it is fatal even when nothing moves.

**The fitted-build anomaly, resolved.** `Engine.u`'s `GetDefenderNum` payload is **40 bytes**; the "5 -> 6" quoted earlier was
the *script* length inside it. The fitted body grew the script by one byte **within the payload's slack**, so no payload size
changed and the build booted. The anomaly was a units error, not a counter-example.

**The rule, now consistent with every boot on this page:**

| change | payload sizes | plays? |
|---|---|---|
| same-length in-place body | unchanged | **yes** |
| `+4` shift of all 13,425 payloads | unchanged | **yes** |
| six bytes inserted before the imports table | unchanged | **yes** |
| body compiled from source, fitted to its payload | unchanged | **yes** |
| body 3 -> 9 bytes (payload 34 -> 40) | **+6** | no |
| body 3 -> 12 bytes, valid void code (payload 34 -> 43) | **+9** | no |
| class payload 987 -> 991 | **+4** | no |
| class payload 144 -> 208 | **+64** | no |
| **size field 987 -> 988, nothing moved** | **+1, nothing else** | **no** |

**Payloads may move freely; no payload's size may change.** Every boot that played kept all 13,425 sizes; every failure
changed exactly one, and the last row shows the size is sufficient on its own.

**What that means for the compile loop**, and it is the useful part: a recompiled body must fit inside its shipped payload,
padded — which is what `tools/uscript-fit.py` does, and why `tools/l1-change.py` refusing a resize by default is the correct
default rather than a limitation. The blocker is no longer "we cannot write a package"; it is "we cannot yet write a body
that does not fit", and the fix for that is padding, not a deeper understanding of the loader.

## Padding works, and a payload has no room above its shipped script (measured 2026-11-24)

`tools/uscript-pad.py` pads a body with **`Nothing` statements** inserted immediately before the top-level `Return`, where
every preceding statement is complete. One byte each, no effect, and the insertion point comes from the grammar rather than a
guess. Its `--check` runs three paddings (to 6 bytes, to 11, and the identity) and two refusals — a shrink, and a body that
does not walk.

**What it cannot do, and the measurement that shows it.** `LogDropped`'s shipped script is 3 bytes in a 34-byte payload, so
its overhead is 31 bytes. Filling the payload with an 11-byte body was attempted through `tools/l1-change.py`:

```
FAIL l1-change: 3 bytes -> 11 bytes moves payload offsets, which faults the guest (docs/L1_READINESS.md)
```

42, not 34: the tool adds the new script to the same 31 bytes of overhead, so **a payload is exactly as large as its shipped
script plus a fixed overhead, and there is no slack in it**. "Pad to the payload's capacity" is therefore not a thing for
this function — the capacity *is* the shipped length.

**So the rule for a recompile has a second half**, and it is the half that constrains the front end:

- a recompiled body must be **no longer** than the shipped script, and
- it must be padded **back to exactly** the shipped length (the padder's job).

A body that comes out longer cannot be made to fit by padding, by moving regions, or by widening a constant *within* the
payload — those all leave the length where it was. The only two ways forward are a shorter body or a size change the guest
rejects, which makes **emitting a body that fits** the actual frontier, not writing packages.

## The compile loop closes on edited source (measured 2026-11-24)

Every earlier boot used a body this project *wrote*; this one used a body this project **compiled from source it had edited**.

**The edit** — one word, in `ACTION_DisplaySGText.uc`:

```
function bool CompleteWhenTimer()      before:  return true;
{                                               return false;   <- edited
	return true;
}
```

**What the emitter said** (`tools/uscript-emit.py --tree <edited copy> --dump …/System`):

```
shipped 0427040b47   emitted 0428040b47   "an emitted body that differs from the shipped bytes (5 vs 5)"
```

Five bytes either way: the two constants are the same width, so the edit is length-neutral by construction — and the emitter
compiled it rather than refusing it or silently reproducing the shipped bytes.

**The control that makes that trustworthy.** The edited run has *no row* for the class in `inventory/uscript_emit.csv`, which
first read as "the emitter dropped it". A comment-only edit to the same file keeps the row (1 row, `0427040b47`), so the file
is not touchy; and the edited body was in the **residue** with its verdict and both byte strings. The artifact lists identical
bodies, and a differing body goes to the residue — a filing convention, not a refusal.

**The install** (`tools/l1-change.py --class ACTION_DisplaySGText --function CompleteWhenTimer --body 0428040b47`):

```
script 5 -> 5 bytes (+0)   payload 40 -> 40   file 3,031,678 -> 3,031,678   script exact, every other payload untouched
```

**The boot**: both shot steps repainted — `01-press` 241,499 raw px and `02-menu` 744,293 raw px, the driver's filtered verdict
PASS on both. The game plays with a body that came from edited text.

**The honest scope.** `CompleteWhenTimer` is not on the title path, so this proves the *installation and loading* of a compiled
body, not that its new value is observed at runtime. The behavioural proof needs a function that runs on a reachable screen —
and that is the next experiment, not a claim this one can make.

**What is no longer in question**: editing a `.uc` file, compiling it, installing the result without changing any payload's
size, and booting the game. Every link in that chain is measured, and each of the four tools involved refuses the step it
cannot justify.
