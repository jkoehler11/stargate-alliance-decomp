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
