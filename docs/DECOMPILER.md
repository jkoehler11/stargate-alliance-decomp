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

**`replication` is the gap**, and the first reading of it — "the decompiler drops the block" — is wrong. Two
measurements corrected it:

* **The blocks we do emit are exactly the blocks the class record can support.** 63 classes carry a `replication`
  block in both trees and the block counts match the source *exactly*, so nothing the data can express is lost.
* **The 79 classes that lose their block have no replication data to reconstruct.** Instrumenting
  `UClassDecompiler.FormatReplication` (a local build of `tools/uelib`, never committed) and running the decompile over
  all 2,319 classes shows **`net=0`** for every one of the 79: not one of their properties carries `PropertyFlag.Net`,
  so the class record holds no group, no `RepOffset` and no condition. All 15 of `StargateGame`'s are in that set —
  `BotBase` 77 properties, 0 replicated; `BotEnemy` 92, 0; `SquadRoster` 14, 0.

So what the 79 declare is a **`persistent` group**, which is a different construct from replication: it is
compile-time-only and leaves no trace in the serialized class, unlike `defaultproperties`, which the reader can put
back together from the tag stream. The class record is not thinner than it should be; the source is simply saying
something the record does not carry.

Two candidate causes were tested and are out: the parser is not failing on a `persistent` group (no exception is
raised — the 79 never reach the condition decoder), and the `DataScriptSize <= 0` gate is not dropping them either
(**0 classes in the corpus have a Net-flagged property with no class script**, so the gate never fires on a
reconstructable block).

What that means for 3.5: the replication feature is closed for everything the data supports, and the 79 are not a
decompiler gap but a limit of the format. A rebuild that wants those blocks has to take them from the source text,
which is game-derived and stays out of this repository.

## What this does not measure

Text fidelity generally: this counts three constructs, not statements. The 3.3 oracle diff (bucket *matches leak*,
*drifted*, *absent*) is the text-level measure, and it needs the privately held 2006 source, which this repository does
not carry.
