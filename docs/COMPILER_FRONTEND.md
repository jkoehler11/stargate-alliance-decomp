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

`tools/uscript-decls.py` parses each file's class header and its `var` / `function` / `event` / `state` / `struct` /
`enum` / `const` declarations, and scores them against `inventory/classes.csv` — a super name and six counts per class,
each side measured independently. **4,545 files join to a package row.**

Agreement, with the number of package rows that carry a non-zero value — a column of zeros agrees with a parser that
finds nothing, and proves nothing:

| column | agree | share | package rows non-zero |
|---|---|---|---|
| class name | 4,545 / 4,545 | **100.0%** | 2,319 |
| `super` | 4,545 / 4,545 | **100.0%** | 2,318 |
| `const` | 4,541 | 99.9% | 98 |
| `state` | 4,507 | 99.2% | 185 |
| `enum` | 4,471 | 98.4% | 114 |
| `var` (properties) | 4,471 | 98.4% | **1,104** |
| `native function` | 4,427 | 97.4% | 92 |
| `function` | 4,397 | 96.7% | **1,204** |
| `struct` | 4,371 | *void* | **0** |

### What the scoring settled, and what it retracted

1. **`event` counts as a function.** With only `function` counted, agreement was **82.4%**, and the package had more
   functions than the source in **794** classes against **6** the other way — a one-sided gap, which is what a missing
   declaration form looks like. Counting `event` raises it to **96.6%**.
2. **State functions do *not* count.** The follow-up guess — that a `function` inside a `state` body also lands in the
   table — was tested and **refuted**: counting them drops agreement from 82.4% to **79.3%**.
3. **UnrealScript is case-insensitive, and the source uses it.** This one bug wore three costumes and each was measured:
   `super` agreement was 97.8% on case alone (`KeyPoint` vs `Keypoint`, `STAFFWEAPON`); the header scan missed
   `class ClientMover Extends Mover`; and the lexer classified `Var config int FearRadius;` as an *identifier*, which
   made `AlkeshBombProjectile` read 5 properties against the table's 10. Keywords are now matched case-insensitively
   everywhere, and `super` is **100.0%** while that class reads 10 of 10.
4. **Retracted: the `struct` column proves nothing.** Its 96.2% was a both-sides-zero artefact — `inventory/classes.csv`
   carries `structs = 0` in **all 2,319** rows, while `Actor` alone declares eleven in its source. The comparison is marked
   void rather than left as a number. `const`, `state` and `enum` carry the same caution at lower strength: 98, 185 and
   114 of 2,319 rows are non-zero, so most of their agreement is two zeroes matching.

### The residue, sampled rather than averaged

The substantive disagreements are `var` (116) and `function` (148). Sampling the `var` classes against
`inventory/class_properties.csv` — which lists properties by name — gives the cause for the first block of them:
**38 of the 116 are `delegate` properties**. The source writes `delegate OnClick(...)`, the export table carries a
property named `__onclick__delegate`. Counting each `delegate` as one property takes `var` from 97.4% to **98.4%**
(4,429 → 4,471 agreeing, 42 classes flipped) and leaves 74 disagreements. Those are a spread, and one shape is visible:
`GUIHorzScrollZone` declares no `var` at all in its source while the table carries one, so part of the residue is
properties the shipped text does not spell. That is the next thing to price, one cause at a time.

## Stage 3 — the emitter

Bytecode out, compared against the shipped function bodies at the tiers `AGENTS.md` defines: the oracle is
`inventory/matching_baseline.csv`, and `tools/uscript-dis.py --compare` already scores two packages against each other.
The first target is a class whose source is short and whose body is already disassembled, so a mismatch has one plausible
cause rather than six.

## What this project does not need

A stock compiler. The corpus supplies the source, the packages supply the compiled result, and the comparator between
them already exists; the front end is the only missing piece, and the lexer now shows it can start from measured ground
rather than from a guess about the dialect.
