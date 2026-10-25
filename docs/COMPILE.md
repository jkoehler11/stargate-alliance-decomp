# What a rebuild would have, per class

PLAN 3.6 asks for a compile loop, and the blocker everyone knows is `ucc`: this project cannot obtain the UE2
compiler, so nothing has been compiled here. What is *not* blocked is measuring whether the inputs are present,
and that is what this page is — no compile happened, and nothing below claims one did.

    make compile-readiness        # tools/compile-readiness.py, reads only committed artifacts

Per class, four things a compiler would need:

| input | source of truth | present |
|---|---|---|
| shipped source | `inventory/package_script_text.csv` | **2,319 of 2,319 classes** |
| a default-property block whose every property the class declares | `inventory/class_defaults_per_class.csv` | **1,552 (66.9%)** |
| a superclass that is itself in the tree | `inventory/classes.csv` | **2,319 of 2,319** (case-insensitive: the inventory spells `GamePlay` and `package_of_super=Gameplay`) |
| function/native counts, so `ucc` knows what has a C++ side | `inventory/classes.csv` | 10,335 functions, 797 of them native |

**1,552 of 2,319 classes (66.9%) have every input.** The other 767 are blocked on exactly one thing — the default
block — which is the measured version of "the source alone is not a rebuild input"
(`docs/PACKAGE_SCRIPT_TEXT.md`).

| package | classes | ready | no block | ambiguous |
|---|---:|---:|---:|---:|
| `StargateContent` | 567 | 77.8% | 26 | 100 |
| `Engine` | 367 | 46.3% | 80 | 117 |
| `StargateGame` | 306 | 49.0% | 58 | 98 |
| `GamePlay` | 262 | 68.7% | 2 | 80 |
| `Sharpeffects` | 158 | 98.7% | 1 | 1 |
| `GUI` | 148 | 86.5% | 9 | 11 |
| `StargateVehicles` | 142 | 82.4% | 9 | 16 |
| `StargateMenus` | 98 | 66.3% | 16 | 17 |
| `UWindow` | 78 | 6.4% | 17 | 56 |
| `Stargateeffects` | 77 | 89.6% | 2 | 6 |
| `UDebugMenu` | 38 | 76.3% | 6 | 3 |
| `AmbientCreatures` | 18 | 77.8% | 1 | 3 |
| `Editor` | 16 | 87.5% | 2 | 0 |
| `IpDrv` | 14 | 35.7% | 1 | 8 |
| `UnrealEd` | 12 | 33.3% | 1 | 7 |
| `Core` | 7 | 42.9% | 3 | 1 |
| `Fire` | 7 | 0.0% | 0 | 7 |
| `UPreview` | 4 | 50.0% | 1 | 1 |

Two shapes are worth reading out of that table. `UWindow` (5 of 78) and `Fire` (0 of 7) are the worst — `UWindow`'s
classes inherit most of their properties from `GUI`/`Core`, and `Fire`'s are fractals with almost no declared
properties, so the "every property declared" test is close to vacuous there. `Sharpeffects` (98.7%) and
`Stargateeffects` (89.6%) are the best.

## What still has to be true before 3.6 can start

1. **`ucc`** — a UE2 build of the compiler matching licensee 4, version 130. Without it nothing here compiles;
   the loop is blocked on toolchain, not on data.
2. **Defaults for 767 classes** — either the block locator gets a second, independent rule, or the defaults come
   from a different serialisation path. Until then a rebuilt class would differ from the shipped one in every
   default property, and every weapon's damage and vehicle's mass lives in those blocks.
3. **A bytecode comparator** (3.7) before any rebuild is *trusted* — matching source text is not matching
   compilation, and `ScriptText` has been re-serialised by the retail compiler already.

## Not measured

* Whether the shipped source actually compiles clean (`L1` in `docs/STATUS.md` stays `--`, not `0%`).
* Whether the 1,552 "ready" classes would produce byte-identical bytecode — that needs `ucc` and 3.7.
* The 532 ambiguous blocks: how many are *wrong* rather than merely ambiguous. The tie-break is a rule of thumb
  (`docs/CLASS_DEFAULTS.md`), and this page counts them as blocked rather than guessing.
