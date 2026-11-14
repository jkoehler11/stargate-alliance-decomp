# Matching: the L2/L3 scoreboard (PLAN 3.8)

The project grades a function in four tiers: **L0 present** (the class exists), **L1 compiles**, **L2 opcode
sequence matches** the shipped script, **L3 byte-exact**. `tools/uscript-dis.py --compare A B` scores two packages
function by function and keeps L3 and L2 apart, because an operand that moved is not an opcode that moved.

The comparator needs no compiler — only two packages — so it is built and validated while `ucc` is still missing
(`PLAN 3.6`). What it cannot do without one is *score a rebuild*. This page is the other half of that: the
**baseline** a rebuild is scored against, package by package, so the day a compiled package exists the comparison
has a denominator instead of a fresh start.

    python3 tools/uscript-dis.py --compare BUILT.u SHIPPED.u -o "$WORK/match"

## The columns

| column | what it counts | what moves it |
|---|---|---|
| `functions` | every `Function` export the package owns — the comparator's index | a package's export table |
| `scripted` | payloads the header model places and the walker reads end to end — **the ceiling**: a rebuild can be graded on nothing else | an opcode rule (the corpus walk) |
| `scriptless` | natives with no epilogue: no body exists to grade | — |
| `refused` | an epilogue but a walk that stops (`docs/BYTECODE.md`, `inventory/opcode_refusals.csv`) | the per-opcode tail |
| `operator` | operator declarations, which serialise their names in front of the header (below) | a compiler |
| `unaligned` | a header that does not end on the export's own name and is not an operator — **0 in this build**; a non-zero there is a defect in the header model | — |
| `other` | a payload too short to hold a header, or an epilogue with no room for a script in front of it — **0 in this build** | — |
| `L3 ceiling` | the package compared with itself, so byte-identical and equal to `scripted` by construction | — |
| `control` | where the mutation control flipped its bit (below) | — |

`L3 ceiling` is deliberately the *label* a rebuild fills in, not a match rate: a self-comparison can only say the
comparator read the same bytes twice. The honest claim the baseline makes is the denominator, and the mutation
control is what keeps the two columns from being one tautology.

## The baseline

<!-- generated:matching-baseline -->
**18 package(s), 12153 `Function` export(s): 11368 with a script, 673 scriptless, 0 refused, 112 operator, 0 unaligned.**

| package | functions | scripted | scriptless | refused | operator | unaligned | L3 ceiling | control |
|---|---:|---:|---:|---:|---:|---:|---:|---|
| `AmbientCreatures.u` | 95 | 95 | 0 | 0 | 0 | 0 | 95 | `AmbientCreatureManager.AddCreature+2` L3 94, L2 95 |
| `Core.u` | 214 | 19 | 91 | 0 | 104 | 0 | 19 | `HelloWorldCommandlet.Main+2` L3 18, L2 19 |
| `Editor.u` | 37 | 23 | 14 | 0 | 0 | 0 | 23 | `ConeBuilder.Build+1` L3 22, L2 23 |
| `Engine.u` | 3821 | 3375 | 438 | 0 | 8 | 0 | 3375 | `AIController.AdjustDesireFor+2` L3 3374, L2 3375 |
| `Fire.u` | 0 | 0 | 0 | 0 | 0 | 0 | 0 | — |
| `GUI.u` | 800 | 719 | 81 | 0 | 0 | 0 | 719 | `ConsoleVisible.BeginState+2` L3 718, L2 719 |
| `GamePlay.u` | 378 | 378 | 0 | 0 | 0 | 0 | 378 | `ACTION_ChangeLevel.GetActionString+2` L3 377, L2 378 |
| `IpDrv.u` | 111 | 83 | 28 | 0 | 0 | 0 | 83 | `BufferedTCPLink.DoBufferQueueIO+1` L3 82, L2 83 |
| `Sharpeffects.u` | 37 | 37 | 0 | 0 | 0 | 0 | 37 | `Dead.BeginState+1` L3 36, L2 37 |
| `StargateContent.u` | 1669 | 1669 | 0 | 0 | 0 | 0 | 1669 | `ACTION_ActivateAstralSight.InitActionFor+3` L3 1668, L2 1669 |
| `StargateGame.u` | 3197 | 3180 | 17 | 0 | 0 | 0 | 3180 | `ACTION_ActivateHelmet.InitActionFor+1` L3 3179, L2 3180 |
| `StargateMenus.u` | 334 | 334 | 0 | 0 | 0 | 0 | 334 | `ChapterList.GetChapter+2` L3 333, L2 334 |
| `StargateVehicles.u` | 502 | 498 | 4 | 0 | 0 | 0 | 498 | `ACTION_CarpetBombing.InitActionFor+1` L3 497, L2 498 |
| `Stargateeffects.u` | 57 | 57 | 0 | 0 | 0 | 0 | 57 | `AcidBurn.PostBeginPlay+2` L3 56, L2 57 |
| `UDebugMenu.u` | 157 | 157 | 0 | 0 | 0 | 0 | 157 | `UDebugBlueLookAndFeel.Button_DrawSmallButton+1` L3 156, L2 157 |
| `UPreview.u` | 13 | 13 | 0 | 0 | 0 | 0 | 13 | `UPreviewMapListClient.BeforePaint+2` L3 12, L2 13 |
| `UWindow.u` | 731 | 731 | 0 | 0 | 0 | 0 | 731 | `UWindow.KeyEvent+2` L3 730, L2 731 |
| `UnrealEd.u` | 0 | 0 | 0 | 0 | 0 | 0 | 0 | — |
| **total** | **12153** | **11368** | **673** | **0** | **112** | **0** | **11368** | 16 of 18 package(s) |
<!-- /generated:matching-baseline -->

## The mutation control

Every package gets one: the tool flips a single bit inside an operand of the first function it can find one in — a
byte position no token starts at, so the opcode sequence cannot move — re-reads the payload and re-compares. The
expected result is exact (L3 loses that one function, L2 does not move) and the site is printed, so the claim can be
re-run. **16 of 18 packages are controlled**: `Fire.u` and `UnrealEd.u` hold no `Function` export at all (110 and
143 exports, none of them a function), so neither column has anything to grade there. That is also a correction to
this PLAN item's own text, which lists both among the small packages to chase first.

## The 112 operator declarations

Before the scoreboard, 112 payloads read as a header-model defect: their payload does not start with the five-value
run that ends on the export's name. They are the **operator declarations** (`EqualEqual_KeyValuePair…`,
`Multiply_FloatColor`, `AddEqual_FloatFloat`). A `Function` serialises its `PreOperator`, `Operator` and
`PostOperator` names in front of the header when its flags say so: all 112 carry `Operator|PreOperator`
(`0x1000|0x2000`) in the `FunctionFlags` dword at `payload_end-4`, and 107 of the 112 are already rows in
`inventory/native_tokens.csv`. They carry no script for this project to match — the compiler expands the operator
at the call site — so they belong in a column of their own rather than in the negative. The flag test is applied
only to a payload the header refused; a payload that walks is a script whatever its flags say.

## Reading it

- **`Core.u` is 19 of 214, and that is not a defect**: 91 of its functions are natives with no body and 104 are
  operators. The engine's base classes are declared there and implemented in C++.
- **The script-bearing packages already sit high**: `StargateContent.u` 1,666 of 1,669, `StargateGame.u` 3,169 of
  3,197, `AmbientCreatures.u` 95 of 95. `Engine.u`'s 3,354 of 3,821 is the package `--compare` validated on.
- **`refused` is the walker's remaining tail** — 79 payloads, concentrated in `StargateMenus.u` (24) and
  `Engine.u` (21). Each is a row of `inventory/opcode_refusals.csv` carrying the opcode it stopped on; until one
  walks, the comparator cannot grade it and no rebuild can raise that row above `scripted`.

## Reproducing

    make matching-baseline SG1_DUMP=~/nfs/sg1-dump
    python3 tools/uscript-dis.py --check-doc docs/MATCHING.md     # in `make ci`; reads the artifact only

`inventory/matching_baseline.csv` is the artifact and the table above is generated from it (`--write-doc`);
`--check-doc` compares the page against the committed CSV, so the numbers here cannot drift from the row that
carries them.
