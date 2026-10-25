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
with. A minimal install:

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
| 13 | `make ci` | nothing | the dump-free gate (see §5) |

Order matters in two places: **step 5 before 6/10/11** (the symbol map comes out of the Ghidra run),
and **step 7 before 8** (the class names are looked up in the tables step 7 measured).

Content-side tools are not `make` targets; they run where the dump or the CSVs are:

```sh
python3 tools/dialogue-index.py "$SG1_DUMP" -o inventory      # speech: docs/DIALOGUE.md
python3 tools/music-index.py    "$SG1_DUMP" -o inventory      # music:  docs/MUSIC.md
python3 tools/campaign-matrix.py -o inventory                 # CSVs only, no dump: docs/CUT_CONTENT.md
python3 tools/native-summary.py -o inventory                  # rewrites docs/NATIVE.md from the symbol map
```

## 4. What each stage is expected to say

Do not trust a count in prose; every number lives in a generated document that states its own command:

| question | document |
|---|---|
| how many packages, classes, natives, modules | `docs/INVENTORY.md` |
| how complete the script tree is, and what the decompiler cannot name | `docs/DECOMPILER_GAPS.md`, `docs/TOKEN_MAP.md` |
| how the decompilation compares with the 2006 source | `docs/ORACLE_DIFF.md` |
| native coverage: functions found, named, attributed per module | `docs/NATIVE.md` |
| the VM: dispatch table, `ProcessEvent`, `FFrame`, seeding | `docs/NATIVE_VM.md` |
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
ok   symbol-map-regenerates: symbols/xbox.map
ok   docs-counts: docs/NATIVE.md
ok   docs-counts: inventory/xbe_functions_by_module.csv
ok   addresses-in-image: 15422 symbols inside the image's sections
all checks passed
``` Everything dump-dependent — manifest verification,
inventory drift, the Ghidra run, the package parse — is a local step.

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
