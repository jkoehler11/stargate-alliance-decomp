# What a rebuild would have, per class

PLAN 3.6 asks for a compile loop, and the blocker everyone knows is `ucc`: this project cannot obtain the UE2
compiler, so nothing has been compiled here. What is *not* blocked is measuring whether the inputs are present,
and that is what this page is — no compile happened, and nothing below claims one did.

    make compile-readiness        # tools/compile-readiness.py, reads only committed artifacts

Per class, four things a compiler would need:

| input | source of truth | present |
|---|---|---|
| shipped source | `inventory/package_script_text.csv` | **2,319 of 2,319 classes** |
| defaults: a block whose every property the class declares, or no properties in its ancestry to set | `inventory/class_defaults_per_class.csv` | **843 blocks read + 869 with nothing to configure** |
| a superclass that is itself in the tree | `inventory/classes.csv` | **2,319 of 2,319** (case-insensitive: the inventory spells `GamePlay` while `package_of_super` says `Gameplay`) |
| function/native counts, so `ucc` knows what has a C++ side | `inventory/classes.csv` | 10,335 functions, 797 of them native |

**1,712 of 2,319 classes (73.8%) have every input.**

## Four states, and only two of them block

The artifact carries a `defaults_state` column:

| `defaults_state` | classes | blocks the compile loop? |
|---|---:|---|
| `read` — a block whose every property the class declares | 843 | no |
| `not-applicable` — no properties anywhere in its ancestry | 869 | no |
| `ambiguous` — block found, not every property declared | 400 | yes |
| `none` — declares properties, no block located | 207 | yes |

`not-applicable` is the distinction that moved this number, and it had to be the right one. The first attempt tested
the class's **own** property list: 1,215 classes declare no properties of their own, which looked like an easy
869-class win — but a class that declares nothing still inherits everything its ancestors declare, and those
inherited properties *do* have defaults to read. Testing the own list would have called 1,215 classes "nothing to
configure" when most of them have dozens. The artifact therefore counts the **inherited closure**, and only
869 classes genuinely have nothing.

Getting that wrong would have inflated readiness to 79.2%. The measured number is **73.8%**.

| package | classes | ready | no block | ambiguous |
|---|---:|---:|---:|---:|
| `StargateContent` | 567 | 90.1% | 19 | 37 |
| `Engine` | 367 | 51.5% | 70 | 108 |
| `StargateGame` | 306 | 59.8% | 52 | 71 |
| `GamePlay` | 262 | 76.0% | 1 | 62 |
| `Sharpeffects` | 158 | 98.7% | 1 | 1 |
| `GUI` | 148 | 88.5% | 8 | 9 |
| `StargateVehicles` | 142 | 85.9% | 9 | 11 |
| `StargateMenus` | 98 | 68.4% | 15 | 16 |
| `UWindow` | 78 | 7.7% | 17 | 55 |
| `Stargateeffects` | 77 | 94.8% | 1 | 3 |
| `UDebugMenu` | 38 | 81.6% | 5 | 2 |
| `AmbientCreatures` | 18 | 88.9% | 1 | 1 |
| `Editor` | 16 | 87.5% | 2 | 0 |
| `IpDrv` | 14 | 35.7% | 1 | 8 |
| `UnrealEd` | 12 | 33.3% | 1 | 7 |
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
* Whether the 1,712 ready classes would produce byte-identical bytecode.
* How many of the 400 ambiguous blocks are *wrong* rather than tied; the tie-break is a rule of thumb
  (`docs/CLASS_DEFAULTS.md`) and this page counts them as blocked rather than guessing.
