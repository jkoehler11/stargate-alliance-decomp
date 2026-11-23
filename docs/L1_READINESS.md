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
