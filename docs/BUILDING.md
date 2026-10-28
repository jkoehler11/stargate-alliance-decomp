# Building this project

Everything here reproduces from an owned copy of the dump. There is no build that produces a game
executable: the four artifacts are a recompilable script tree, a symbolized native map, an asset/level
inventory, and eventually a bootable rebuild (PLAN §1). This file is the pipeline that produces them,
in the order they depend on each other, with the trap that breaks each step.

## 1. What you must supply

| input | where it goes | notes |
|---|---|---|
| your own copy of the dump (`Stargate_SG-1_-_The_Alliance-2005-12-15.7z`, SHA-256 in `README.md`) | `$SG1_DUMP` (default `~/sg1/dump`) | not in this repository and never will be (AGENTS.md) |
| the console's MCPX boot ROM and a flash ROM | `$SG1_DUMP/bios/` | only for the emulator stages (PLAN 7); firmware you dumped yourself |
| the privately held 2006 `Classes` tree (optional) | `$ORACLE_DIR` | only for `make oracle-diff`; never committed |

Everything else — Python packages, the .NET SDK, Ghidra, binutils — is public and installable.

## 2. Toolchain

`tools/versions.lock` is the authority; the pins in it are what the committed artifacts were produced
with. Each line is a **constraint, not prose** — `>=3.11`, `==12.2`, `~=5.0` — so it can be checked against a machine
instead of being trusted:

```
$ make versions                      # on the dump-free analysis host
tool               constraint   found      status      source
python             >=3.11       3.11.15    match       /usr/local/bin/python3
py7zr              >=1.0        -          absent      pypi
dotnet-sdk         >=10.0       -          absent      dot.net
ghidra             ==12.2       -          absent      ghidra_12.2_PUBLIC
capstone           >=5.0        -          absent      pypi
as                 >=2.38       2.44       match       /usr/bin/as
umodel             UNPINNED     -          n/a (UNPINNED) --
```

`make versions --strict` exits non-zero when an **installed** tool violates its constraint; an absent tool is not a
failure, because that is a stage this machine has not run. Three entries say so out loud rather than pretending:
`xbox-sigs` is `none` (Phase 4.2 is blocked on a signature database the repo may not carry), and `umodel`/`xemu` are
`UNPINNED` with the reason — Phase 5.1 has not been run and the xemu build that booted is not recorded, which is
exactly why its captures are not comparable yet. `make ci` checks the lock's shape: it must parse, nothing may be
listed twice, every constraint must be a version, and an `UNPINNED`/`none` entry must say why.

A minimal install:

```sh
# Python side (3.12): the inventory and analysis tools
pip install py7zr capstone

# .NET 10 SDK: builds tools/Sg1Tool (the UELib front end) for the package/decompile stages
#   https://dot.net  (see tools/versions.lock for the pinned major version)

# Ghidra 12.2 + a JDK 25 for the native track (Ghidra refuses JDK < 25)
mkdir -p ~/tools && cd ~/tools
curl -sL -o jdk25.tar.gz "https://api.adoptium.net/v3/binary/latest/25/ga/linux/x64/jdk/hotspot/normal/eclipse"
tar xzf jdk25.tar.gz                                    # -> jdk-25.0.5+8
curl -sL -o ghidra.zip https://github.com/NationalSecurityAgency/ghidra/releases/download/Ghidra_12.2_build/ghidra_12.2_PUBLIC_20261016.zip
unzip -q ghidra.zip                                     # -> ghidra_12.2_PUBLIC

# `as` (binutils) is used by `make symbols` to assemble the symbol file
```

Two things that cost an hour if missed: **Ghidra's headless launcher rejects any JDK older than 25**
(set `JAVA_HOME`), and **`/tmp` is often a noexec tmpfs** — extract and build on real disk, not there.

## 3. The pipeline

Each row is one `make` target. "Needs" is what has to exist first; "produces" is what gets committed
(the rest lands under `$WORK` and never enters git).

| step | command | needs | produces |
|---|---|---|---|
| 1 | `make verify-dump SG1_DUMP=…` | dump | checks `inventory/manifest.sha256` (4,006 files) |
| 2 | `make inventory SG1_DUMP=…` | dump, dotnet | `inventory/*.csv` (packages, classes, natives, modules, formats), `docs/INVENTORY.md` |
| 3 | `make decompile SG1_DUMP=…` | dump, dotnet | `$WORK/UnrealScript/` (game-derived, never committed) |
| 4 | `make oracle-diff ORACLE_DIR=…` | step 3 + private tree | `inventory/oracle_diff.csv`, `docs/ORACLE_DIFF.md` |
| 5 | `make ghidra SG1_DUMP=… GHIDRA_HOME=…` | dump, Ghidra, JDK 25 | `symbols/xbox_functions.csv`, `inventory/xbe_block_seeds.csv`, `build/ghidra/` |
| 6 | `make natives SG1_DUMP=…` | a `$WORK/gnatives/runtime.bin` dump from the emulator | `inventory/gnatives_slots.csv`, `symbols/xbe_dispatch.csv` |
| 7 | `make class-tables SG1_DUMP=…` | image only | `inventory/xbe_class_tables.csv`, `…_slots.csv` |
| 8 | `make class-names SG1_DUMP=…` | step 7, `capstone` | `inventory/xbe_class_table_names.csv` |
| 9 | `make libraries SG1_DUMP=…` | image only | `inventory/xbe_libraries.csv` |
| 10 | `make symbols SG1_DUMP=…` | step 5, `as` | `symbols/xbox.map` (+ `xbox.sym.s`, `.o` not committed) |
| 11 | `make assert-residual SG1_DUMP=…` | steps 5, `build/ghidra/asserts.tsv` | `inventory/xbe_assert_residual.csv` |
| 12 | `make formats SG1_DUMP=…` | dump, dotnet | `inventory/formats.csv`, `inventory/sgs_schema.csv` |
| 13 | `make ci` | nothing | the dump-free gate (see §5), including the one census step that needs no dump |

Order matters in two places: **step 5 before 6/10/11** (the symbol map comes out of the Ghidra run),
and **step 7 before 8** (the class names are looked up in the tables step 7 measured).

The census and derived stages. **Every committed artifact is reachable from one of these** — that is what
`docs/ARTIFACTS.md` audits, and it reported nine artifacts whose tool no target ran before these existed:

| command | needs | produces | gate |
|---|---|---|---|
| `make campaign-matrix` | committed CSVs only | `inventory/campaign_matrix.csv` | **`make ci`** (reproduced byte for byte) |
| `make dialogue-index SG1_DUMP=…` | the dump's `Speech/` | `inventory/dialogue_by_mission.csv`, `docs/DIALOGUE.md` | nightly |
| `make package-check SG1_DUMP=…` | the dump's `System/Packages.md5` | `inventory/package_check.csv` | nightly |
| `make content-census SG1_DUMP=… WORK=…` | the dump + `$WORK/UnrealScript` | `inventory/content_census.csv`, `docs/CONTENT_CENSUS.md` | nightly |
| `make property-types WORK=…` | `$WORK/native` ExportAt output (step 5) | `inventory/property_types.csv` | nightly |
| `make token-map ORACLE_DIR=…` | the private tree + `$WORK/UnrealScript` | `inventory/token_map.csv`, `docs/TOKEN_MAP.md` | nightly |
| `make game-modules WORK=…` | `$WORK/native/functions.csv` (a native index) | `inventory/xbe_game_modules.csv` | nightly |
| `make maps-parse SG1_DUMP=…` | the dump, Sg1Tool | `inventory/map_parse.csv` | nightly |
| `python3 tools/native-summary.py -o inventory` | the symbol map | rewrites `docs/NATIVE.md` | `make ci` |

`token-map` refuses to run without `ORACLE_DIR` rather than publishing a guess, and `game-modules` without its index
produces different per-module counts (measured: `inventory/xbe_game_modules.csv` differs at byte 104 without
`--index`), which is why it is a nightly stage rather than a CI one.

Two of these were run against the real dump and reproduced their committed artifacts byte for byte —
`dialogue-index` (1,364 WAVs, 14 missions, 29 speakers) and `package-check`. The other four need an input this
repository does not carry (`$WORK/native`, the private tree) and stay listed as gates rather than claims.

## 4. What each stage is expected to say

Do not trust a count in prose; every number lives in a generated document that states its own command:

| question | document |
|---|---|
| how many packages, classes, natives, modules | `docs/INVENTORY.md` |
| how complete the script tree is, and what the decompiler cannot name | `docs/DECOMPILER_GAPS.md`, `docs/TOKEN_MAP.md` |
| how the decompilation compares with the 2006 source | `docs/ORACLE_DIFF.md` |
| native coverage: functions found, named, attributed per module | `docs/NATIVE.md` |
| the VM: dispatch table, `ProcessEvent`, `FFrame`, seeding | `docs/NATIVE_VM.md` |
| the bytecode: opcode groups, operand widths, the disassembler | `docs/BYTECODE.md` |
| the class tables, their classes and object sizes | `docs/NATIVE_CLASSES.md` |
| the property system and the tagged save format | `docs/NATIVE_PROPERTIES.md` |
| what the image was linked against, and what that costs | `docs/IMPORTS.md` |
| how to load the symbol map in a debugger | `docs/SYMBOLS.md` |
| maps, chapters, cut content | `docs/CONTENT_CENSUS.md`, `docs/CUT_CONTENT.md` |

## 5. What CI can and cannot check

`make ci` is the per-commit gate and it deliberately does **not** touch the game: `AGENTS.md` forbids
CI from downloading it. It checks that the committed CSVs and symbol map have the shape the tools
expect and that the generated docs reproduce. Its output on the current tree:

```
ok   csv-shape: 73 files
ok   symbol-map: 15423 symbols (14957 functions + 466 class tables)
ok   symbol-map-regenerates: symbols/xbox.map
ok   docs-counts: docs/NATIVE.md
ok   campaign-matrix: inventory/campaign_matrix.csv
ok   artifact-hashes: 76 artifacts, 5.4 MiB, manifest matches
ok   opcode-table: 256 opcodes, 31 groups, 11 streams split, 4 refusals honoured
ok   lipsinc-runtime: 19 functions, 9 with a self-evident name
ok   inventory-index: docs/ARTIFACTS.md (74 artifacts)
ok   status-table: docs/STATUS.md and README.md
... 13 more (23 checks)
all checks passed
```

Everything dump-dependent — manifest verification, inventory drift, the Ghidra run, the package parse — is a local
step.

### The `--check` contract

Every tool that advertises `--check` must answer it **without the dump and without writing to the tree** — that is what
makes it usable as a per-commit gate. `make ci` enforces it (`tool-check-contract`): it discovers the tools from their
own source, runs each one, and diffs `git status` around the whole sweep, so a `--check` that mutates the tree fails the
build. Twelve tools comply; four (`defaults-emit`, `make-manifest`, `map-census`, `package-census`) still require dump
arguments and are **reported by name** rather than silently skipped — they are the next thing to fix, not a licence to
skip.

That contract is not decorative. `compile-readiness.py --check` used to ignore its flag and rewrite two artifacts on
every run, which is how `inventory/compile_readiness.csv` sat **142 rows stale**: later defaults work improved what the
tool computes (`HerdAnimal` went from `defaults 0` to `2`, 142 classes moved out of `defaults-ambiguous`), and nothing
noticed because the check that should have compared never compared. It compares now, the artifact is regenerated in the
same commit, and `--check` reports the drift by file and row count.

Consequence worth stating plainly: **a green CI does not prove the dump-side artifacts are current.**
Run `make inventory`, `make ghidra`, then `make natives` and `make symbols`, and commit any drift in
its own commit so a reviewer can see which numbers moved.

## 6. Reproducing a release

PLAN 8.4 lists the milestones. For any of them:

1. `make verify-dump` on the exact archive named in `README.md` — a different dump is a different result.
2. Run the stages the milestone covers, in the order of §3.
3. `make ci` and commit the regenerated artifacts.
4. Record the toolchain actually used if it differs from `tools/versions.lock`; that difference is a
   finding, not a detail.
5. `python3 tools/artifact-hashes.py --check` — every artifact must match its expected hash.

### Expected hashes

`inventory/artifact_hashes.csv` is the manifest: the path, size and SHA-256 of all 76 committed artifacts under
`inventory/` and `symbols/`. It answers the question a fresh checkout asks before trusting any number — *is this file
the one the tools produce?* — without re-running the tools, which for half of them means a Ghidra run and the dump.

```
$ python3 tools/artifact-hashes.py --check
ok   artifact-hashes: 76 artifacts, 5.4 MiB, manifest matches
```

`make ci` runs that check, so three things fail a build: an artifact whose hash changed but whose manifest was not
rewritten in the same commit, an artifact listed in the manifest that is gone, and a file under `inventory/` or
`symbols/` that the manifest does not mention at all. The remedy is always the same and is deliberately manual — run
the artifact's stage, then `make artifact-hashes`, and commit the two together, so the diff shows which numbers moved.

**Before blaming the tree, check the toolchain.** `make versions --strict` names an installed tool that violates its
constraint; that — not a hand edit — is the usual reason a hash differs on a second machine.

A mismatch is only a bug when the tree is otherwise unchanged. Against *your own* dump it usually means something
else, and both explanations are worth naming: a different dump (the archive is identified in `README.md` and its hash
is pinned in `tools/versions.lock`), or a different toolchain (`tools/versions.lock` again — a JDK or Ghidra change
can move a body size). Hand-written prose in `docs/` is out of scope on purpose; the counts in it are checked by
regenerating the document (`docs-counts` above).

## 7. Which of these were actually run

Written and checked on 2026-10-24 against `main`:

- **run here**: `make ci`, `make libraries SG1_DUMP=…`, `make class-tables`/`make class-names` (their
  outputs are committed), `make symbols`, `python3 tools/campaign-matrix.py`.
- **transcribed, not re-run here** (no .NET SDK or Ghidra in this environment): the `dotnet` stages
  (`make inventory`, `make decompile`, `make formats`) and the Ghidra run (`make ghidra`,
  `make natives`). Their commands and expected outputs are the ones in the targets and in the documents
  listed in §4, both of which are themselves generator output.

If you run a stage and get a different count than the document it feeds, the document is stale — that
is the signal to regenerate and commit, not to edit the doc by hand.

## Indexing what has been measured

`make inventory-index` writes `docs/ARTIFACTS.md`: every committed CSV with its row and column count and the
target that regenerates it. `tools/inventory-index.py --check` runs in `make ci`, so the page cannot age, and the
artifacts it cannot resolve are listed explicitly — an artifact nothing rebuilds is a measurement that will drift.
