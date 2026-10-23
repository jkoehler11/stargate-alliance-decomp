# Conventions

## Repository layout

```
docs/                 scope, legal, provenance, conventions, formats/, systems/, evidence/
inventory/            generated CSVs + manifest.sha256 (committed; regenerable, never hand-edited)
src/UnrealScript/     our reconstructed .uc tree (git-ignored: game-derived)
src/Native/<Module>/  reconstructed C for native modules
symbols/              address maps consumable by Ghidra/xemu-gdb (structure, not content)
tools/                our tooling (Python 3, C#, shell) — everything here is GPL-3.0
build/                build pipeline output (git-ignored)
```

Rule: **generated files are never edited by hand.** If a number is wrong, fix the generator and
rerun `make inventory`. A hand-edited CSV is a bug report against the tool.

## Naming

- Reconstructed UnrealScript keeps original names, verbatim, including the prototype's typos.
  Never "clean up" an identifier: it must match the bytecode so the diff tools line up.
- Native functions discovered from the script side use the script name: `execTrace` for the
  function `Actor.Trace` declares as a native. Prefix with the owning class when two slots would
  otherwise share a name (`execActor_Sleep` / `execGUIPage_Sleep`) — never number them to break a
  tie, because a renumbering would rename functions that did not change.
- A `GNatives` slot that no shipped package declares is `Native_<index>` and nothing more: the index
  is evidence that the address is dispatch slot N, not evidence of what the function does. Its plate
  comment carries the same fact. Do not promote a slot to a semantic name without evidence from
  behaviour, a string, or the script that reaches it.
- Native functions with no script counterpart: `<Module>_sub_<ADDRESS>` (e.g. `Engine_sub_0014A2C0`),
  where address is the Ghidra address without the `0x`. Never invent a semantic name without evidence;
  put the hypothesis in the comment, not the symbol.
- Library/CRT functions keep their canonical names once matched by signature (`memcpy`, `_malloc`).

## Marking uncertainty

Three tags, used consistently, greppable, and counted by CI:

| Tag | Meaning |
|---|---|
| `// UNVERIFIED:` | reconstructed from one observation; not yet exercised at runtime |
| `// GUESS:` | an inference about intent, with the evidence in the same comment |
| `// TODO(<phase>.<task>):` | deferred work, referencing the `PLAN.md` task id |

A function is only "matched" (L2+) when it is recorded in the matching table by the comparator, not
because it looks right. Source comments cannot promote a grade.

## Matching grades

`L0` present, `L1` compiles, `L2` opcode sequence ≥98% per function, `L3` byte-exact. Grades are
per-function; package-level numbers are always "N of M functions at ≥L2" and never a bare percentage.

## Commits

`<area>: <imperative summary>` — areas: `inventory`, `scripts`, `native`, `assets`, `build`, `ci`,
`docs`, `tools`. Reference the plan task in the body, e.g. `Fixes: PLAN 2.2`.

Body must say what was verified and how, in one or two lines. "Generated X" is not verification;
"regenerated X, spot-checked 3 rows against dump, counts match plan §0" is.

Branches: `main` is always green. Work on `<area>/<short-slug>`; PRs must pass `make check`.

## Determinism

Anything generated must be byte-identical across runs on the same input: sorted keys, `\n` endings,
fixed timestamps, no absolute paths, no hostnames. CI diffs regenerated output against the commit
and fails on drift — that is how the inventory stays honest.

## Dump access

The dump path is never hard-coded. Everything reads `SG1_DUMP` (see `Makefile`); scripts must fail
loudly with a clear message when it is unset rather than silently scanning nothing. No absolute
paths from a contributor's machine may appear in committed files.
