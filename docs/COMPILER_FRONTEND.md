# The front end, written here: the lexer is done and every byte is classified

`docs/COMPILER.md` measures why the compile loop cannot be bought: the only public UE2 Runtime (build 2226, three
archived installers all the same build) emits **126 / licensee 0**, this build is **130 / licensee 4**, and pointing it
at these packages stops on `Bad expr token 03` — a grammar difference, so neither a version constant nor a newer download
closes it. `U17`'s second option is therefore the one left: a front end written here.

This page is that project's plan, and its first stage is already measured.

## Stage 1 — the lexer (measured 2026-11-21)

`tools/uscript-lex.py` classifies the shipped source, and its gate is *coverage*: the token spans must tile the input
exactly, with no gap and no overlap, or the tool refuses and names the byte.

| | |
|---|---|
| files | **4,637** |
| bytes | **12,227,040** |
| tokens | **2,686,850** |
| unclassified bytes | **0** |

The distribution, from `inventory/uscript_lex.csv`:

| kind | tokens | share |
|---|---|---|
| space | 917,864 | 34.16% |
| operators and punctuation | 882,268 | 32.84% |
| identifiers | 529,339 | 19.70% |
| keywords | 231,150 | 8.60% |
| comments | 61,456 | 2.29% |
| numbers | 39,780 | 1.48% |
| strings | 12,121 | 0.45% |
| quoted names (`'Actor'`) | 9,492 | 0.35% |
| preprocessor directives | 3,376 | 0.13% |
| backtick macros | **0** | 0% |
| non-ASCII runs | **4** | 0.00% |

Three things that distribution settles for the next stages:

1. **The preprocessor is `#`, and backtick macros do not occur** in this corpus — so the parser needs `#ifdef`/`#else`
   handling (`tools/text-branches.py` already evaluates the guard set) and no macro expander.
2. **Only four files carry a non-ASCII byte**, and all four are the same two files (`GUISectionBackground.uc`,
   `MenuItemOverlay.uc`) reached through two directories, one `©` each. They are prose: those files' shipped text opens
   with a bare class comment whose `//` is not in the buffer, so the prose is in the token stream with no marker.
3. **The corpus is not uniformly marker-clean**, and the lexer is where that has to surface rather than later. A parser
   must refuse `high` in code position instead of guessing — the same rule the rest of this repository follows.

## Stage 2 — the parser (next)

An AST per file, with two gates:

* **round-trip**: rendering the AST back to text, with comments and whitespace re-attached from the token stream, must
  reproduce the input byte for byte on every file whose tokens are marker-clean (4,633 of 4,637 by the count above);
* **declaration agreement**: every class, `var`, `function` and `state` the AST finds must match the shipped package's
  export table for that class — the packages already carry it (`inventory/classes.csv`, `inventory/natives.csv`), so a
  parser that invents or drops a declaration fails against data rather than against opinion.

## Stage 2a — declarations, scored against the packages (measured 2026-11-22)

`tools/uscript-decls.py` parses each file's class header and its `var` / `function` / `event` / `delegate` / `state` /
`struct` / `enum` / `const` declarations, and scores them against `inventory/classes.csv` — a super name and six counts per
class, each side measured independently. **2,317 classes are scored, one row each.** The read is at *name* level: the
parser returns the declared names per kind and the counts are their lengths, so a column and a name list can never
disagree — the two computed separately is how a rename scores as agreement.

| column | agree | share | package rows non-zero |
|---|---|---|---|
| class name | 2,317 / 2,317 | **100.0%** | 2,318 |
| `super` | 2,317 / 2,317 | **100.0%** | 2,317 |
| `var` (properties) | 2,317 | **100.0%** | **1,103** |
| `state` | 2,317 | **100.0%** | 184 |
| `enum` | 2,317 | **100.0%** | 114 |
| `const` | 2,316 | 100.0% | 98 |
| `native function` | 2,313 | **99.8%** | 92 |
| `function` (non-native) | 2,310 | **99.7%** | **1,188** |
| `struct` | 2,230 | *void* | **0** |

### Eight things the scoring settled, and one it retracted

1. **`event` counts as a function.** Counting only `function` gave 82.4% at the time, with the package higher in 794
   classes and lower in 6 — a one-sided gap is what a missing declaration form looks like.
2. **State functions do *not* count.** The follow-up guess was tested and **refuted**: counting them drops agreement
   (82.4% → 79.3%) by overshooting classes that were already right.
3. **UnrealScript is case-insensitive, and the source uses it.** Three costumes, each measured: `super` on case alone
   (`KeyPoint` vs `Keypoint`); a capitalised keyword (`class ClientMover Extends Mover`); and the lexer reading
   `Var config int FearRadius;` as an identifier, which made one class read 5 properties against the table's 10.
4. **A `delegate` is a property.** The table names it `__<name>__delegate`, the source writes `delegate OnClick(...)`.
5. **The build guards must be evaluated, not counted.** This corpus is the Xbox image, so `XBox` and `DEBUG` are defined
   and the `PC`/`PS2` branches were never compiled — the rule `tools/text-branches.py` measured for keywords, with
   case-folded macro names and `#ifdefPS2` read as a whole word. Counting the unbuilt branches over-counted `Actor` by
   **173** functions.
6. **`functions` is the total, with natives inside it**, so the non-native count is `functions − native_functions`.
7. **A native declaration can carry an index and modifiers**: `native(881) final simulated static function …`. Six tokens
   separate the two words, so a check at `i-1` misses them — which is why the native column read 97.5% and the non-native
   column absorbed the difference. Walking back to the statement boundary takes `native function` to **99.8%** and
   `function` to **98.9%**.
8. **Retracted: the `struct` column proves nothing.** Its 96.3% is two zeroes matching — `structs = 0` in **all 2,319**
   rows while `Actor` declares eleven. The comparison is marked void, and the table's non-zero column shows the same
   caution applies to `const` (98), `state` (185) and `enum` (114).

### The residue, named at name level (measured 2026-11-22)

A count comparison cannot see a rename. `var HaakenPodDoor Doors[NUM_DOORS];` read as a property called `NUM_DOORS` has
the same *count* as `Doors`, and `delegate bool OnClick(...)` read as a property called `bool` has the same count as
`__OnClick__delegate` — the old score called those classes agreement. Joined against the package's own export names,
**126 (class, column) pairs disagreed while their counts agreed** (88 `var`, 28 `function`, 9 `state`, 1 `const`).
Seven rules closed all but ten of them, and every one came from reading a sample rather than from adding a rule and
watching an average:

| the text says | what it was read as | cost |
|---|---|---|
| `class` inside a comment whose `//` is not in the buffer: `class is used to indicate the selected menu item` | a class named `is` | 6 classes mis-named; the file's stem is what names the declaration |
| `ACTION_KillInstigator` in the text, `ACTION_Killinstigator` in the table | no join at all | **40** of the table's classes reported as "no source file" |
| `state() LoopMove` — a state with an empty label | no state | 12 of Mover's 19 |
| `out byte State` — a parameter named after a keyword | a state | 19 classes, one each |
| `var(Display) enum EPhysics { … } Physics;` | no enum, and `EPhysics` as the property | 7 of Actor's 23 enums, 35 classes |
| `var HaakenPodDoor Doors[NUM_DOORS];` | a property called `NUM_DOORS` | most of the 88 `var` pairs |
| `delegate bool OnClick(...)` | a property called `bool` | 12 in `GUIComponent` alone |
| `operator(20) color + (color A, color B)` | no function | 111 names: a delegate is *also* a Function in the package, and an operator is a native Function named `Add_ColorColor` |

Two of the eight were not parser bugs but reporting bugs: `BotScarab`'s shipped `ScriptText` carries **no class header at
all** (it opens inside a function comment, so the text is a fragment of the file), and the 47 classes the earlier note
called "no source file in the corpus" were 40 spellings and 6 prose headers — the corpus has a file for every one.

Ten classes still disagree, and they are named here rather than left as a percentage: `Object` (13 native functions the
text does not declare), `MaplistRecord` (2 more operators whose operand types are structs declared in another class),
`Mover` (`detach`, declared in the text and not in the package), `PlayerController`, `StargatePawn` and `UIWidget` (one
function each, same shape), `UPreviewMapListClient` and `UPreviewSkinnedButton` (a `native` declaration the walk-back
does not reach), and `ObjectivesPanel`'s `const COLUMN_INDENT`, which is declared **inside a function body** and hoisted
to the class by the compiler. `inventory/uscript_decls_residue.csv` carries every one of them, with the cause, so the
list is reproducible and a fix has a row to close.

## Stage 2b — signatures, scored against the package records (measured 2026-11-21)

A signature is finer than a count: a name, a return type and an ordered parameter list. `inventory/natives.csv` carries
one per native — **797** rows of class, function and `parameter_count` read from the package's function records, not from
the text — so `tools/uscript-signatures.py` parses every `function`/`event` declaration and joins on (class, function).

**10,147 declarations parsed; 687 join to a package record; the parameter slots agree on 687 of 687 — 100.0%.**

The corpus is **two copies of the same 2,319 files** — the extracted tree holds a flat `<Class>.uc` and a
`<Package>/<Class>.uc` for every class — so the earlier figures here (20,292 declarations, 1,358 joined) walked each
class twice; the populations are half of that, and the join gained 8 rows from stage 2a's class-name fix.

Two facts had to be measured to get there, and both looked like parser bugs until the source was read:

1. **The return value occupies a parameter slot.** The first run agreed on 51.3%, and every miss was *exactly one* low —
   `ShouldBeHidden` read 0 against 1, `Move` 1 against 2. A uniform off-by-one is a modelling error, not a parsing one:
   the package counts the return value as a parameter. Counting slots as `params + (returns ? 1 : 0)` took it to 99.1%.
2. **A parameter can be *named* with a type word.** `StrLen( coerce string String, out float XL, out float YL )` names its
   first parameter `String`, which the lexer calls a **keyword** now that keywords are case-insensitive — so an
   identifiers-only scan dropped that parameter and read two slots instead of three. Reading the last *word* of each
   comma-separated segment, and removing an identifiers-only guard that skipped the segment before it could be named,
   took the last twelve cases to **100.0%**.

The self-test also caught an error of mine: it required an `event` to carry three slots *and* to have no return slot, two
things that cannot both hold. The parser was right — an `event` has no return type, so its slots are its parameters —
and the expectation was corrected rather than the code, which is the only direction that leaves the gate meaningful.

## Stage 2c — call sites, the first expression-level measure (measured 2026-11-21)

`inventory/script_calls.csv` records **19,350** call sites read out of the packages: the class, the function the call sits
in, and the target it resolves to. That is enough to score expression-level parsing without a full grammar —
`tools/uscript-calls.py` parses every function body (19,142 of them) and asks whether the recorded target appears as a
call in that function's source.

**14,428 of 14,442 recorded call targets are found: 99.90%.** Four things had to be measured first, and the first of them
looked like a total failure:

1. **The join is case-insensitive.** Recorded targets are lower-cased and the source keeps its case, so intersecting the
   raw sets matched only names the source happens to spell in lower case (`initialise`) — a correct extractor scored
   **0.6%**. Lower-casing one side took it to 98.2%.
2. **State-block functions are class code.** Six of the misses were self-calls in AI classes (`BotEnemy.checkifshouldcrouch`
   was "missing" its own recursive call) because those functions live inside `state` bodies, which the first pass skipped.
   Parsing them took recall to 99.9% and raised the joined pairs from 13,144 to 14,408.
3. **A call can be named with a keyword.** `Travel("Map")` and `Import(...)` are recorded calls whose names the lexer
   classifies as keywords, so an identifiers-only test never saw them.
4. **An operator is a call the text may never name.** `==` compiles to `EqualEqual_MapItemMapItem`, and `ClockwiseFrom`
   is spelled *as an infix word* — `if ( FireYaw ClockwiseFrom Rotation.Yaw )`. Scoring an operator target on the symbol
   *or* on its spelled name recovered **34** more, to 99.90%.

**The residual is seven, and it is one shape:** six are `Mover` calls (`InterpolateTo`, `SetStoppedPosition`,
`BeginEvent`, and a `Reset`/`Untrigger` pair) that the source writes **at state scope** — statements directly inside a
`state` body rather than inside a function — so the body walk has no function to attribute them to. The seventh is the
placeholder row for a file that does not parse. Modelling state-scope code is the next piece of stage 2d.

## Stage 3 — the emitter, first family: one constant return (measured 2026-11-22)

Bytecode out, compared against the shipped function bodies at the tiers `AGENTS.md` defines. `tools/uscript-emit.py`
emits a body from its parsed source and compares the bytes with that function's own script in the package, so this is
the first stage with no recall knob: a body either equals the shipped bytes or it does not, and there is no percentage
to hide a rename behind.

**1,724 bodies are modelled and 1,724 are byte-identical: 0 differ.** The family is the smallest one that is still
real — a body that is one constant return (`return true;`, `return -1;`, `return "";`, `return None;`, `return self;`,
`return vect(0,0,0);`), `return;`, an empty body, or a declaration whose text carries no body at all (**801** of the
1,724). Of the 11,687 shipped bodies that walk cleanly, **11,337 join to a declaration** in the extracted text (the
join's own residue is 80 class-scope names and 270 state names the text does not carry) and **9,613 are refused by
name**: 3,919 with a nested block, 1,429 with two statements, 1,412 whose one statement is not a return, 758 with
three, 445 whose expression is compound. That residue is the statement walk's work, and it is named rather than left
as a percentage.

**All 11,687 bodies end on the same three bytes, `04 0b 47`** (Return Nothing; EndOfScript), and **1,469 are those
three bytes alone**: 801 declarations with no body, 352 whose source body is comments only, 263 state functions whose
text names another state, and **53 whose text declares code the package did not compile** — `Mover.EnableTrigger` is
one statement in the source and the trailer in the package. The stage-2 counts could not see that population, because
both sides agreed on the count.

Six rules had to be measured before the comparison was exact, and every one came from a body that differed:

| the source | the bytes | what it settled |
|---|---|---|
| `return 0;` in an `int` function | `04 25` | `IntZero` is a token of its own |
| `return 1;` / `return 2;` | `04 26` / `04 2c 02` | `IntOne`, then `IntConstByte` up to 255 |
| `return -1;` / `return 666;` | `04 1d ff ff ff ff` / `04 1d 9a 02 00 00` | everything else is `IntConst` + int32 |
| `return 0;` in a `float` function | `04 1e 00 00 00 00` | the **declared return type** picks the token, not the value: `return 1` in a float function is `1e 00 00 80 3f` |
| `return 0;` in a `byte` function | `04 24 00` | `ByteConst` — and an enum constant is the same token with its ordinal |
| `return "";` / `return 'weapon_bone';` | `04 1f 00` / `04 21 4f 20` | a string is NUL-terminated; a `name` literal is a **name-table index** in the archive's compact form (2,063 → `4f 20`), so the emitter has to write that index and read the package's own name table |

`return None;` is `04 2a`, `return self;` is `04 17`, and `return vect(0,0,0);` is `04 23` + twelve zero bytes.

Two refusals came out of the first run's residue, and both are facts about the build rather than about the parser:

* **A `native` declaration with no body still ships a script**: it relays its parameters — `Object.Clamp` is three
  `29 <index>` `NativeParm` tokens and then the trailer, one per argument. Emitting the plain stub would be wrong, so
  the emitter refuses a native declaration, and the 12 bodies of that shape stay in the residue by name.
* **A class whose extracted text carries two declarations of one name** (an `#if` branch, or a stub beside its body)
  compiled the longer one: preferring the declaration that has a body turned four `Pawn`/`UnrealPawn` bodies from
  differ to identical. That is the measurement, not a rule assumed in advance.

`inventory/uscript_emit.csv` is one row per modelled body — what was emitted, what the package holds, and the verdict
— with a census row that `--check` re-derives from the rows. The gate is dump-free: 19 synthetic bodies, one per
shape plus the two refusals, and the compact-index writer is round-tripped against `uscript-dis.read_index` for the
positive and negative forms, because writing that index wrong would score as a whole family of mismatches.

The next stage is the statement walk: a body of one assignment or one call, with the property and local indices the
emitter does not read yet.

## What this project does not need

A stock compiler. The corpus supplies the source, the packages supply the compiled result, and the comparator between
them already exists; the front end is the only missing piece, and the lexer now shows it can start from measured ground
rather than from a guess about the dialect.
