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
`enum` / `const` declarations, and scores them against `inventory/classes.csv` — six counts and a super name per class,
each side measured independently. **4,543 files join to a package row**, and agreement is:

| column | agree | share |
|---|---|---|
| class name | 4,543 / 4,543 | **100.0%** |
| `const` | 4,539 | 99.9% |
| `state` | 4,473 | 98.5% |
| `enum` | 4,467 | 98.3% |
| `super` | 4,443 | 97.8% |
| `var` (properties) | 4,425 | 97.4% |
| `native function` | 4,425 | 97.4% |
| `function` | 4,389 | 96.6% |
| `struct` | 4,369 | 96.2% |

**Two counting conventions were settled by measuring, not by assuming, and both came from the *direction* of the
disagreement rather than its size:**

1. **`event` counts as a function.** With only `function` counted, agreement sat at **82.4%**, and the package had more
   functions than the source in **794** classes against only **6** the other way — a one-sided gap, which is what a missing
   declaration form looks like. Adding `event` raises it to **96.6%**, and the asymmetry nearly disappears.
2. **State functions do *not* count.** The obvious next guess — that a `function` inside a `state` body also lands in the
   table — was tested and **refuted**: counting them drops agreement from 82.4% to **79.3%**, because it overshoots the
   classes that were already right.

The residual 2–4% per column is the next question, and it now has a shape: it is a spread of small disagreements rather
than one missing form, so the next step is to sample the disagreeing classes rather than to add another rule and watch the
average move.

## Stage 3 — the emitter

Bytecode out, compared against the shipped function bodies at the tiers `AGENTS.md` defines: the oracle is
`inventory/matching_baseline.csv`, and `tools/uscript-dis.py --compare` already scores two packages against each other.
The first target is a class whose source is short and whose body is already disassembled, so a mismatch has one plausible
cause rather than six.

## What this project does not need

A stock compiler. The corpus supplies the source, the packages supply the compiled result, and the comparator between
them already exists; the front end is the only missing piece, and the lexer now shows it can start from measured ground
rather than from a guess about the dialect.
