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

## The negation is reconstructed and written in the wrong position

3.5's remaining operator item is not the precedence table but the unary position, and the packed source settles it
without the privately held 2006 tree. `tools/decompiler-operators.py` (`make decompiler-operators`,
`inventory/decompiler_operators.csv`: 2,319 class rows, one row per operator, a census row) counts the logical
negation in both trees and in both positions, the parenthesised groups, and every operator token.

| | source | reconstruction |
|---|---:|---:|
| `!` **before** its operand | 2,064 | 10 |
| `!` **after** its operand | 0 | **2,053** |

**The negation is not lost; its position is.** 2,294 of the 2,319 classes carry the source's count exactly, 12 lose
one and 13 gain one, so the reconstruction holds **2,063 of the source's 2,064 negations (99.95%)** -- and 2,053 of
those 2,063 are written postfix (`bFlag!`, `IsReady()!`, `(Other == none)!`), a form the source's own 2,064
negations never use and UnrealScript's grammar has no production for. The ten that survive as prefix are the ones
whose operand is a parenthesised comparison (`if(!(DemoLevels[i] != ""))`, `Engine.Console`): the renderer knows the
prefix form and chooses it only for a group.

**The fix is mechanical, which is why this is a finding and not a loss.** 1,238 of the 2,053 sites (60.3%) follow a
name and 815 (39.7%) follow a call or a parenthesised group, so every one is recovered by moving the `!` in front of
the operand it follows -- a postfix site names its own operand, so no expression tree has to be rebuilt. As the tree
stands it is not UnrealScript: PLAN 6.1's compile loop would inherit 2,053 syntax errors before a compiler reaches an
expression.

**Binary precedence is not the gap, and the page can say so now.** The reconstruction carries **8.1% more**
parenthesised groups than the source (85,076 against 78,707) and 40,254 operator tokens against 40,762 (−1.2%, all
twenty operators present: `-` +839 from negative literals and unary minus, `==` −921, `&&` −85), and the corpus
contains no ternary at all (`?` counts 0 in both trees). So a `&&`/`||` chain is over-parenthesised rather than
flattened -- a reader who re-parses the text gets the same tree -- and the one operator the text gets wrong is the
unary one.

**The control is the zero case.** 1,890 classes declare no negation and the reconstruction adds one on **2** of them
(`Core.Object`, `Sharpeffects.SE_AnimatedActor`), so the renderer is not emitting a `!` per class or per function.

The tree is written by UELib's `UClass.Decompile()` (`tools/Sg1Tool/DecompileCommand.cs`), so the defect belongs to
the pinned renderer (`tools/uelib`, `8236c39`, git-ignored) rather than to this repository's code -- the case 3.5
says to file upstream. Until that lands the correction is a text pass over `$WORK/UnrealScript`, not a measurement
anyone has to guess at.

## What this does not measure

Statement shapes. This counts three constructs, not statements: no `if`/`while` nesting, call arity or statement
order is compared. The operator census above is the one text-level property the packed source settles without a tree
builder; the 3.3 oracle diff (bucket *matches leak*, *drifted*, *absent*) is the full text-level measure, and it needs
the privately held 2006 source, which this repository does not carry.
