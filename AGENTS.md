# Agent instructions

James runs this repository. These rules apply to every person and every bot working here, including bots
James runs. If another file in this repository disagrees with this one, this one wins.

## Where things are

- `README.md`: what the project is, what it is not, and the legal terms. Read it before anything else.
- `PLAN.md`: the task list. It is the authority on what to do next and in what order.
- `AGENTS.md` (this file): the rules.
- `LICENSE`: GPL-3.0-or-later, covering this repository's own code, tooling and documentation only.
- `docs/`: scope, legal, provenance, conventions, VM and native notes, per-format notes under
  `formats/`, and `UNKNOWNS.md` — the tracked gaps. Add to it as the phases in `PLAN.md` reach them.

## The hard rule: no game data, ever

Nothing extracted from the game, derived from the game, or leaked from the studio goes into this repository,
a commit, a branch, a PR, an issue, a comment, a release or a CI artifact. That includes:

- the executable (`default.xbe`) and anything unpacked from it;
- every package and asset: `.u`, `.unr`, `.utx`, `.ukx`, `.usx`, `.uax`, `.sgs`, `.sgp`, `.ka`, `.kaw`, `.lad`,
  `.int`, `.ini` files taken from the release, and exported WAVs, TGA/BMP textures, meshes or T3D dumps;
- decompiled or rebuilt output: `.uc` reconstructed from bytecode, rebuilt packages, exported levels;
- the leaked 2006 studio source (`.uc`, `.ucpp`, `.cpp`) in any form, including as a test fixture, a fixture
  hash input committed by value, or a diff pasted into a PR;
- emulator BIOS files, Xbox SDK libraries, Microsoft symbol files and HDD images.

Work on your own copy, outside git. Keep derived output under `work/` and keep `work/`, `out/`, `dump/` and
`*.xbe` in `.gitignore` — the plan's phases are executed locally against your own dump and the results are
yours alone. What may be committed is our own tooling, documentation, manifests of *hashes and counts*,
and measurement output (sizes, versions, match percentages, symbol names you discovered).

Format documentation is offsets and field layouts, in prose or tables. Do not paste payload bytes.
Script/asset class names are fine: they are names, and the project is unusable without them.

## Attribution

- Commits, PRs, issues and comments carry human names only. Never add an AI tool, model or bot as author,
  co-author or committer, never add "Generated with" lines, AI session links or bot signatures, and turn
  off your tool's attribution setting before committing.
- Keep human authorship when importing someone else's work: cherry-pick with their author, or add a
  `Co-authored-by:` line naming the person.
- Third-party tools we call stay third-party; don't vendor them into this repo, pin versions in
  `tools/versions.lock` and document how to install them.

## How to make changes

- Every change goes through a branch and a pull request into `main`. Never push to `main` directly,
  force-push it, or rewrite its history.
- One concern per pull request. The title says what changed; the body says how it was checked, in plain
  language, with the command and the real output.
- Small, boring commits. Commit messages are imperative, lowercase after the prefix, no attribution.
- Before starting, check open PRs and recent branches so two people don't do the same task from `PLAN.md`.
- Run the checks that fit the change. If a check does not exist yet, the PR that needs it adds it.

## Honesty about results

This project's value is that its claims are checkable, so:

- Every statement about the build must come from a hash-verified dump (SHA-256 in `README.md`) and be
  reproducible by someone else with the same tools. Paste the command you ran and its output.
- Never estimate a match percentage, a function count or a file count. Measure it or say it is not measured.
- Do not describe a phase as done because the code exists. `PLAN.md` defines the pass criterion for each
  phase; quote it and show it was met.
- Record what you do not know in `docs/UNKNOWNS.md` (create it when the first unknown appears) rather than
  filling the gap with a plausible guess.
- When your tool guesses (decompiler inference, auto-generated names, string-based naming), mark it in the
  output with a comment, and never present a guess as a match.

## Reverse-engineering conventions

- Matching tiers, used everywhere a percentage or a claim appears: **L0** decompiled text exists,
  **L1** our source compiles clean, **L2** opcode sequence matches the original (≥98% per function),
  **L3** bytes match. Say which tier you mean.
- Unknown native functions are `FUN_<address>` until named, then take the script class/function name that
  reaches them (`Class__Function`) or the name inferred from a string/assert site. Renaming is its own
  commit, so a diff of names stays reviewable.
- Every reconstructed function carries its address, its evidence and its tier. A function with no evidence
  is not reconstructed.
- Prefer the build's own data as the oracle: script `native()` indices, assert paths, config files, package
  name/import tables. Auto-generated symbol imports beat hand-naming.
- Do not "correct" decompiled code to match a newer source that may not be the source of this build.
  Differences are findings; write them down.

## Tooling and builds

- Pin toolchain versions (`Ghidra`, `ghidra-xbe`, `UELib` commit, `UE Explorer`, `umodel`, `xemu`, `dotnet`,
  plus the UE2 compiler used for rebuilds) and record them in `tools/versions.lock`.
- Scripts in `tools/` are idempotent and take explicit paths. No script may write outside the directory
  passed to it, and none may touch the pristine copy of the dump.
- CI runs the checks from `PLAN.md` phase 7 and must never download the game, a BIOS or an SDK library.
  No secrets in CI; there is nothing here worth protecting with a secret.

## Talking to people on GitHub

- Write as James's account, in the first person, plainly and briefly. If a bot drafts it, it is still his
  reply: say "I", and don't mention bots, agents or models.
- Never help anyone obtain the game, the dump, the leaked source or a BIOS — not with a link, a mirror name,
  a search hint or an offer to share privately. Point them at the public preservation listing and stop there.
- Don't post, push or open issues or PRs on other repositories unless James asks for that specific action.
- Don't promise dates, and don't close an issue about a bug until the reporter confirms it or it is a clear
  duplicate.
- Release nothing that contains game data or game-derived output, under any license. GPL-3.0 covers our
  code and grants no rights in the game.

## Scope guardrails

- This is a decompilation and preservation project. It is not a remaster, a port, a reimplementation or a
  source port, and it is not a mod loader. Renderer or gameplay changes are out of scope.
- Native work follows the script: engine internals are documented and named where the script reaches them,
  game modules get decompiled bodies. Reconstructing all of the engine is not a goal.
- If a task in `PLAN.md` turns out to be the wrong approach, change the plan in its own PR with the reason,
  rather than quietly doing something else.
