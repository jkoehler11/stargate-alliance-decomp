# What a rebuild would have, per class

PLAN 3.6 asks for a compile loop, and the blocker everyone knows is `ucc`: this project cannot obtain the UE2
compiler, so nothing has been compiled here. What is *not* blocked is measuring whether the inputs are present,
and that is what this page is — no compile happened, and nothing below claims one did.

    make compile-readiness        # tools/compile-readiness.py, reads only committed artifacts
    make compile-readiness-doc    # regenerate the tables below from that run (`--check-doc` in `make ci`)

Per class, four things a compiler would need:

| input | source of truth | present |
|---|---|---|
| shipped source | `inventory/package_script_text.csv` | **2,319 of 2,319 classes** |
| defaults: a block whose every property the class declares, or no properties in its ancestry to set | `inventory/class_defaults_per_class.csv` | **every class accounted for** — the states table below has the split |
| a superclass that is itself in the tree | `inventory/classes.csv` | **2,319 of 2,319** (case-insensitive: the inventory spells `GamePlay` while `package_of_super` says `Gameplay`) |
| function/native counts, so `ucc` knows what has a C++ side | `inventory/classes.csv` | 10,335 functions, 797 of them native |

<!-- generated:compile-readiness -->

**1,823 of 2,319 classes (78.6%) have every input.**

| `defaults_state` | classes | blocks the compile loop? |
|---|---:|---|
| `read` — a block whose every property the class declares | 954 | no |
| `not-applicable` — no properties anywhere in its ancestry | 869 | no |
| `ambiguous` — block found, not every property declared | 247 | yes |
| `none` — declares properties, no block located | 249 | yes |

| package | classes | ready | no block | ambiguous |
|---|---:|---:|---:|---:|
| `StargateContent` | 567 | 92.9% | 10 | 30 |
| `Engine` | 367 | 58.0% | 85 | 69 |
| `StargateGame` | 306 | 74.5% | 43 | 35 |
| `GamePlay` | 262 | 76.7% | 22 | 39 |
| `Sharpeffects` | 158 | 98.7% | 2 | 0 |
| `GUI` | 148 | 91.2% | 5 | 8 |
| `StargateVehicles` | 142 | 90.8% | 5 | 8 |
| `StargateMenus` | 98 | 69.4% | 3 | 27 |
| `UWindow` | 78 | 16.7% | 44 | 21 |
| `Stargateeffects` | 77 | 94.8% | 3 | 1 |
| `UDebugMenu` | 38 | 86.8% | 4 | 1 |
| `AmbientCreatures` | 18 | 94.4% | 1 | 0 |
| `Editor` | 16 | 87.5% | 2 | 0 |
| `IpDrv` | 14 | 42.9% | 3 | 5 |
| `UnrealEd` | 12 | 33.3% | 6 | 2 |
| `Core` | 7 | 42.9% | 4 | 0 |
| `Fire` | 7 | 0.0% | 7 | 0 |
| `UPreview` | 4 | 75.0% | 0 | 1 |

Blockers across the tree: no-defaults-block 249, defaults-ambiguous 247.

<!-- /generated -->

| `Core` | 7 | 42.9% | 3 | 1 |
| `Fire` | 7 | 0.0% | 0 | 7 |
| `UPreview` | 4 | 50.0% | 1 | 1 |

`Sharpeffects` (98.7%) and `Stargateeffects` (89.6%) are the best; `UWindow` (6.4%) and `Fire` (0.0%) the worst, and
the state column says why: `UWindow` inherits most of its properties from `GUI`/`Core`, and `Fire`'s fractals
declare almost nothing, so there the block test is near-vacuous.

## What still has to be true before 3.6 can start

1. **`ucc`** — a UE2 build of the compiler matching licensee 4, version 130. Nothing here compiles without it; the
   loop is blocked on toolchain, not on data.
2. **A block for the 400 + 207 classes** — a second, independent locator rule, or defaults from another
   serialisation path. Until then a rebuilt class differs from the shipped one in every default property, and every
   weapon's damage and vehicle's mass lives in those blocks.
3. **A bytecode comparator** (3.7) before any rebuild is *trusted*: matching source text is not matching
   compilation, and `ScriptText` has already been through the retail compiler.

## Not measured

* Whether the shipped source compiles clean — `L1` stays `--` in `docs/STATUS.md`, not `0%`.
* Whether the classes that are ready would produce byte-identical bytecode.
* How many of the 400 ambiguous blocks are *wrong* rather than tied; the tie-break is a rule of thumb
  (`docs/CLASS_DEFAULTS.md`) and this page counts them as blocked rather than guessing.
