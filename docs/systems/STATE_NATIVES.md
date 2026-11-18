# The state machine's native half: `GotoState`, `Goto` and the label map

`docs/systems/AI.md` reads the state machine from the outside -- the shipped text's `state` blocks, the payloads'
`GotoState`/`GotoLabel` operands, and the `LabelTable`s those operands resolve against. It ends on an open question:

> the by-name half: `Object.GotoState` is `native(113)`, slot `0x71` ... and `inventory/script_calls.csv` has no
> by-name call to `Goto` or `GotoState` at all, because both compile to opcodes rather than to a script call.

This page is the inside of that question: the four script natives, the state setter they call and the label lookup
the setter's second half is, read out of `default.xbe`. It is a **read**, not a reconstruction: every claim below
carries the instruction it was read at, in `inventory/state_natives.csv`, and `tools/state-natives.py` re-reads them
from the image (`--verify`) or from the committed rows (`--check`).

    python3 tools/state-natives.py --xbe DUMP/default.xbe -o inventory/state_natives.csv \
        --write-doc docs/systems/STATE_NATIVES.md

<!-- generated:state-natives -->
| body | reached through | args evaluated | debug info | class-table slots | log lines |
| --- | --- | ---: | :---: | --- | --- |
| `execGotoState` | native 113 | 2 | eats `0x42` | 0x34 0x38 0x14 | 789, 792 |
| `execIsInState` | native 281 | 1 | eats `0x42` | - | - |
| `execGetStateName` | native 284 | 0 | eats `0x42` | - | - |
| `execGotoLabel` | gnatives[13] (0x0d) | 1 | - | 0x38 | 789 |
| `state_set` | class table 13 (+0x34) | 0 | - | 0x10 0x6c | - |
| `label_lookup` | class table 14 (+0x38) | 0 | - | - | - |

* `execGotoState` -- two name expressions, the label defaulting to FName 100; the state change is class table 13 and the label jump class table 14, and only an explicit label that misses is logged. Fields touched: 0x8 0xc 0x1c 0x20 0x24. Constants compared: 0x1 0x2b2.
* `execIsInState` -- one name expression; the walk follows the state chain, so a state the current one extends answers too. Fields touched: 0x8 0xc 0x1c 0x20 0x28. Constants compared: -.
* `execGetStateName` -- no expression at all: the call takes no argument and only consumes a trailing DebugInfo. Fields touched: 0x8 0xc 0x1c 0x20. Constants compared: -.
* `execGotoLabel` -- one name expression, the same class table 14 the state entry uses -- `Goto('L')` and `GotoState('S','L')` share one lookup. Fields touched: 0x8 0xc. Constants compared: -.
* `state_set` -- resolves the name, fires the end/begin events around the switch, then clears the code pointer and recomputes the frame's probe masks. Fields touched: 0x0 0x4 0xc 0x1c 0x20 0x24 0x28 0x2c 0x3c 0x80 0x84 0x88 0x8c 0x90 0x498. Constants compared: 0x2b2.
* `label_lookup` -- walks the state chain's `LabelTable`s -- 8-byte entries, a zero name ending the table, the offset relative to the state's own code -- and on a miss clears the frame's code pointer. Fields touched: 0x4 0xc 0x1c 0x28 0x48 0x94. Constants compared: -.

Log text, verbatim:

* `execGotoState`: `GotoState (%s %s): Label not found`
* `execGotoState`: `GotoState (%s %s): State not found in class %s!`
* `execGotoState`: `Stack when GotoState failed.`
* `execGotoState`: `GotoState (%s %s): State not found in class %s!\nRefer to Log for Script Stack Trace.`
* `execGotoState`: `GotoState (%s %s): State not found in class %s!`
* `execGotoLabel`: `GotoLabel (%s): Label not found`
<!-- /generated:state-natives -->

## The two call paths, and the one lookup they share

A state change is a script native; a label jump is an interpreter opcode. They meet at the class table's slot 14
(`+0x38`):

* `GotoState(<state>, <label>)` -- `native(113)` -- evaluates **two** names off the byte stream, changes the state
  through class table slot 13 (`+0x34`, the state setter), and then jumps to a label through slot 14. The label
  defaults to FName **100** when the call passes none.
* `Goto('<label>')` -- `EX_GotoLabel`, `gnatives[13]` (`0x0d`), the bytecode token `docs/systems/AI.md` counts 196
  of -- evaluates **one** name and calls the *same* slot 14. Nothing else happens in the handler: the jump is the
  lookup's own effect.

So the label map has **one consumer**, and it is not the state machinery: `0x46f00` is reached from both a
`Goto('Label')` in a state's code and the tail of every `GotoState`. That is why a `State` payload's table entries
and a `GotoLabel` operand's target are the same population (the AI page's 196 of 196 join), and why a `Function`
payload has no table at all: only state code jumps by label.

## The label map, as the lookup reads it

The lookup walks the state chain -- the current state and then whatever it extends -- and for each state with a
table (`LabelTableOffset` not `0xffff`) scans an array of **8-byte entries**:

* the entry is `(FName index, code offset)`, and the table ends on a **zero name**, which is the `None` name and
  not a zero offset (the distinction `docs/UNKNOWNS.md` U19 needed two attempts to make);
* the table's address is the state's own **code base** (`+0x48`) plus a **WORD** field of the state record
  (`+0x94`) -- the same `LabelTableOffset` the payload read measures, and the reason U19's field is a widened-code
  offset: the base is the code buffer the loader rebuilt, not the file's bytes;
* a hit writes the code pointer to `base + offset` **and** stores the state it matched, so a label the current
  state inherits from the state it extends resolves and runs *there*.

**A miss clears the code pointer.** That is the answer to the question this page was opened with: when the state
is found and the label is not, the frame is left pointing at nothing, so the state does not run at all -- it is
entered and immediately ends. `GotoState` reports it (`GotoState (%s %s): Label not found`, source line 789) only
when the caller passed a label; the **default** label's miss is silent, because a bare `GotoState('X')` that finds
no FName 100 entry just stops. `Goto` reports every miss (`GotoLabel (%s): Label not found`, line 789).

## What a state change does around the switch

The setter (`+0x34`) resolves the name to a state, and the shape around it is a state machine in its own right:

* the name `0x2b2` -- the `BadToken` sentinel the compiler emits for an argument it could not build -- does not
  look the name up at all: it walks the chain for the state whose flag byte has bit 2 set, i.e. the `auto` state.
  That is the mechanism behind the 14 `auto` states `docs/systems/AI.md` counts, and it is why a state block can be
  entered with no name from the text.
* an *unresolved* name falls back to the object's current state, and the setter returns 0 -- the call is a no-op
  rather than a move.
* a real change fires a built-in event on the old state (FName index **317**) before the switch and one on the new
  state (**316**) after it; the order is the evidence, the pages' names for those two indices are not measured here.
* then the frame's code pointer is cleared, the frame's probe masks are recomputed from the old and new states'
  `ProbeMask`/`IgnoreMask`, and the object's flags are marked as having begun a state.

## Two queries

`IsInState(name)` takes one name (its second declared parameter is never evaluated) and walks the same chain
comparing the `+0x20` name field, so a state the current one extends answers true as well -- the runtime side of
the text idiom `if (!IsInState('X'))`. `GetStateName()` evaluates **no** expression at all: it eats a trailing
DebugInfo token and reads the name out of the object's class-side state record, which is the field `GotoState`
compares a request against when it turns a request for the state you are already in into `None`.

Both script natives (and every call site that can be followed by one) accept a trailing `0x42` `DebugInfo` token:
the native consumes it itself, which is why `inventory/opcode_table.csv`'s 13-byte `DebugInfo` row appears inside
a call's operand stream rather than before it.

## What the corpus says

`inventory/state_entry.csv` counts **871** `GotoState` sites and **196** `GotoLabel` operands, and `docs/systems/AI.md`'s
generated block resolves the label half: **111** `GotoState` sites pass an entry label, **105** of them name a
literal state, and of those **103** name an entry of the target's table (**4** through the state's own `Super`),
**1** names a state no chain reaches a table for, and **1** names a label the table does not carry. Against the
runtime read above:

* **196 of 196** `GotoLabel` operands name an entry of their own state's table, so **no** `Goto` in the shipped
  build can print `GotoLabel (%s): Label not found`; the message exists for code that is not shipped.
* of the 111, **103** jump on arrival and the 8 that do not (the one absent label, the one state with no table, and
  the six whose label or state the text computes) reach the lookup with no entry to find -- entered and stopped.
* the absent one is `AmbientCreatures.TransientAmbientPawn.Wandering`: the function `Wandering` entering `Wandering`
  with the label `KeepGoing`, re-derived with the same read as the AI page's join. It is the only site in the corpus
  that reaches the `Label not found` line.

## Not measured, and what would settle it

* **Which name FName 100 is.** The default label is the engine's own name index, and the packages' name tables are
  per-package, so the only instrument that answers it is a GNames read in a live guest: slot 100 of the array at
  `0x006be0d8` (`tools/xemu/mem.py --virtual`, the same read `docs/NATIVE_PROPERTIES.md` makes of that global). In
  the corpus, 143 of the 166 tables put `Begin` on their entry byte and 23 put something else there, so the two
  readings disagree for 23 states and the runtime's answer decides which.
* **Which engine field each offset is.** `+0xc` (the state frame), `+0x1c` (the state the lookup walks from),
  `+0x20` (a state's name), `+0x24` (the object's current state), `+0x48` (the state's code base), `+0x94` (the
  WORD label offset) are measured *shapes*, not named members: the engine headers are not in the image, and the
  record built at load is the package-based `UState` whose field order this page's constants match one for one.
* **The names of the two events.** Indices 316 and 317 are fired before and after the switch; that they are
  `EndState`/`BeginState` is an inference from order and from the shipped text, not a measurement.

## Reproduce

    make state-natives SG1_DUMP=$HOME/nfs/sg1-dump      # the read, against the dump
    python3 tools/state-natives.py --verify             # re-read every claim from the image
    python3 tools/state-natives.py --check              # the committed rows, no dump
    make state-entry SG1_DUMP=$HOME/nfs/sg1-dump        # the corpus side this page quotes (`docs/systems/AI.md`)
