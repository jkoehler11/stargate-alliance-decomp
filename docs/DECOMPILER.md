# Where the reconstruction is thinner than the source

Every package ships its own `ScriptText`, which means the reconstructed tree has an oracle for **structure** even
where it has none for text: a class whose shipped source declares a `replication` block and whose reconstruction
declares none is a measured gap, not an impression. PLAN 3.5 lists three such features — `replication` blocks, state
code and latent calls — and this page counts them, class by class.

    python3 tools/decompiler-gaps.py $WORK/source-tree $WORK/UnrealScript -o inventory

`inventory/decompiler_gaps.csv` is the per-class table (2,319 rows); the two trees are game-derived and stay under
`$WORK`. Comments are stripped before counting, because a commented-out `replication` is not a block.

## What the census says

| feature | blocks in the source | blocks in ours | classes that lose all of them |
|---|---:|---:|---:|
| `replication` | 142 | 63 | **79** |
| `state` | 457 | 512 | 0 |
| `latent` | 10 | 13 | 0 |

**State code and latent calls are not gaps** — every class that declares them keeps them, and the reconstruction emits
slightly more of both (it is a decompiler, so it can also produce a state where the source has one written by a macro).

**`replication` is the gap**, and it is not total: 63 classes keep a block and the block counts match the source
exactly, so what the decompiler emits it emits correctly. The 79 that lose every block are concentrated in the fork's
own packages — `StargateContent` 39, `StargateGame` 15, `StargateVehicles` 15, `Engine` 9, `GamePlay` 1 — and only 4 of
them declare themselves `native`, so "the body is in C++" explains a twentieth of it.

## What the missing blocks look like

Shape of the first line inside the block, over the 79:

| shape | classes |
|---|---:|
| a bare `persistent` followed by a list, with no condition | 48 |
| `persistent bField, bOther` (one line) | 3 |
| other | 28 |

So the dominant shape is a `persistent` group with no `if (...)`, which is legal script and not the `reliable if (...)`
form the decompiler's replication parser appears to expect. That is the lead for a fix, and the fix is in UELib's
decompiler (`tools/uelib`, pinned at `8236c39`), not in this repository's own code.

## What this does not measure

Text fidelity generally: this counts three constructs, not statements. The 3.3 oracle diff (bucket *matches leak*,
*drifted*, *absent*) is the text-level measure, and it needs the privately held 2006 source, which this repository does
not carry.
