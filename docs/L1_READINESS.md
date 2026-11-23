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
