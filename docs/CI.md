# What CI checks, and what it deliberately does not

`make ci` (`.github/workflows/ci.yml`, every push and pull request) runs `tools/ci.py`. It is small on
purpose: `AGENTS.md` forbids CI from downloading the game, a BIOS or an SDK library, so anything that
needs the dump cannot be a per-commit check, and a job that pretended otherwise would be theatre.

## What runs

| check | what it proves |
|---|---|
| `csv-shape` | every committed CSV parses and every row has as many fields as its header — the way a hand-appended row with an unquoted comma gets caught |
| `function-map` | `symbols/xbox_functions.csv` is sorted, has no duplicate address, every size is positive and every name is present |
| `symbol-map` | `symbols/xbox.map` is sorted and unique, and every row agrees with the function map or the class tables it was built from, in both directions |
| `symbol-map-regenerates` | `tools/make-symbols.py` reproduces the committed `symbols/xbox.map` byte for byte |
| `docs-counts` | `tools/native-summary.py` reproduces `docs/NATIVE.md` and `inventory/xbe_functions_by_module.csv` |
| `addresses-in-image` | every symbol address falls inside a section of the image — **skipped** unless `SG1_DUMP` holds one |

The last check is the only one that touches the dump. It skips with a printed reason on a clean checkout,
and CI reports that skip in the summary rather than passing silently.

## What does not run, and where it lives instead

- `make verify-dump` — the manifest against the dump (PLAN 1.3). Needs all 4,006 files.
- `make inventory` / `make decompile` — package parse and decompile coverage (PLAN 2.x, 3.2). Need the
  dump and the pinned UELib build.
- `make ghidra` — the native pass (PLAN 4.x). Needs the dump, Ghidra 12.2 and JDK 25.
- `make check` — manifest plus inventory drift. Needs the dump by definition.

Those are run by hand or by whoever runs the nightly (PLAN 7.5), on a machine that has the dump. What CI
protects is the half that goes stale silently: the committed generated artifacts and the numbers the docs
quote from them.

## Running it locally

```
make ci
```

Exit status is non-zero if any check fails, and each failure names the file and the first line that
differs. Regenerate whatever drifted rather than editing the generated file:

```
make symbols SG1_DUMP=~/sg1/dump
python3 tools/native-summary.py --symbols symbols/xbox_functions.csv -o inventory
```
