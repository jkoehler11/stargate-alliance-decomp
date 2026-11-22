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

## Stage 2a — declarations, scored against the packages (measured 2026-11-21)

`tools/uscript-decls.py` parses each file's class header and its `var` / `function` / `event` / `delegate` / `state` /
`struct` / `enum` / `const` declarations, and scores them against `inventory/classes.csv` — a super name and six counts per
class, each side measured independently. **2,272 classes are scored, one row each.**

| column | agree | share | package rows non-zero |
|---|---|---|---|
| class name | 2,272 / 2,272 | **100.0%** | 2,319 |
| `super` | 2,272 / 2,272 | **100.0%** | 2,318 |
| `const` | 2,271 | 100.0% | 98 |
| `var` (properties) | 2,272 | **100.0%** | **1,104** |
| `native function` | 2,270 | **99.9%** | 92 |
| `state` | 2,272 | **100.0%** | 185 |
| `function` (non-native) | 2,246 | 98.9% | **1,204** |
| `enum` | 2,272 | **100.0%** | 114 |
| `struct` | — | *void* | **0** |

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

### The residue, read one row at a time (measured 2026-11-22)

The percentages above were the only view of the disagreements, so this pass started by naming them:
`inventory/uscript_decls_residue.csv` carries every row that disagrees (`column, class, ours, theirs, file`) — **116
rows** where the page had counts. Reading it family by family closed 60 of them, and each closure is a lexer fact:

1. **A declared name can be a keyword.** `var String Name;` names the property `Name`, which lexes as the `name` type,
   so a rule that wants an identifier after the type reads 4 properties in `SavedGameInfo` where the package has 5. All
   five `var` disagreements were this family; accepting a keyword as the last word of a declaration takes `var` to
   **2,272 / 2,272**.
2. **A `var` statement can declare its names after an inline enum body.**
   `var(Texture) enum ETexClampMode { … } UClampMode, VClampMode;` declares two properties *after* the brace group —
   `BitmapMaterial` read 8 against 9 — and the inline enum is a declaration of its own (a `UEnum` whose outer is the
   class). Counting both takes the `enum` column from 2,237 to **2,272 / 2,272** in one rule.
3. **`state` is used as an identifier.** 19 GUI classes read one state they do not have because a native parameter is
   named `state` (`out byte Key, out byte state`) or a member is (`Segment.state`); `TextGameInfo` reads one for
   `Key == 0x7A && state == 1`. A state header is `[auto] state [()] Name`, so the token after the optional empty
   parameter list decides — `state` goes to **2,272 / 2,272**.
4. **A state's name can be a keyword too.** The first cut of that rule required an identifier, and then under-counted
   `KHinge` (4 against 5, `state Default`) and `PteroBossBot` (6 against 7, `state Protected`).
5. **A native operator never says `function`.** `native(129) static final preoperator bool !( bool A );` is a
   `UFunction` with the native flag: `Object` carries **103** of them and `Actor` **4**, which is the whole difference
   between those two classes' text and package counts. Counting a class-scope `operator`/`preoperator`/`postoperator`
   whose statement carries `native` takes the column from 2,267 to **2,270 / 2,272**.
6. **The `struct` column is void in the tool, not only in this page.** The packages export no `UScriptStruct` at all
   (0 in all 2,319 rows), so a class declaring eleven `struct`s scored as a disagreement against a zero. The tool now
   reports the column as `void` and never scores it; its 87 rows stay in the residue, labelled, because the count of
   declarations the package cannot confirm is itself a measurement.

### What is left

**26 `function` rows, 2 `native function` and one `const`** — 116 residue rows in all, 87 of them the void `struct`
column. The function residue is one-sided for 23 of the 26 (the package carries 1 to 29 more non-native functions than
the text declares; `GUIComponent` is 75 against 104) and three classes declare one more than the package (`Mover` 31
against 30, `UPreviewMapListClient` 4 against 3, `UPreviewSkinnedButton` 2 against 1). Both native rows
(`UPreviewMapListClient`, `UPreviewSkinnedButton`) have no `native` word in their text at all, so the package marks a
function native that the source does not — the one place that question has been read before is `U3` on the same page.
`const` is one class (`ObjectivesPanel`), whose only `const` sits inside a function body: counting body consts fixed
that row and broke `CacheManager` (53 body consts, 0 in the package), so the column stays at class scope. Plus 47 of
the table's 2,319 classes with no source file in the corpus.

## Stage 2b — signatures, scored against the package records (measured 2026-11-21)

A signature is finer than a count: a name, a return type and an ordered parameter list. `inventory/natives.csv` carries
one per native — **797** rows of class, function and `parameter_count` read from the package's function records, not from
the text — so `tools/uscript-signatures.py` parses every `function`/`event` declaration and joins on (class, function).

**20,292 declarations parsed; 1,358 join to a package record; the parameter slots agree on 1,358 of 1,358 — 100.0%.**

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

## Stage 3 — the emitter

Bytecode out, compared against the shipped function bodies at the tiers `AGENTS.md` defines: the oracle is
`inventory/matching_baseline.csv`, and `tools/uscript-dis.py --compare` already scores two packages against each other.
The first target is a class whose source is short and whose body is already disassembled, so a mismatch has one plausible
cause rather than six.

## What this project does not need

A stock compiler. The corpus supplies the source, the packages supply the compiled result, and the comparator between
them already exists; the front end is the only missing piece, and the lexer now shows it can start from measured ground
rather than from a guess about the dialect.
