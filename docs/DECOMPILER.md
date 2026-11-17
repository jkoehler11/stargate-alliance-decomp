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
says to file upstream. **It is filed and fixed**: the next section measures the cause and ships the patch, and the
correction pass below stays as the regression check on the patched renderer, where it moves nothing.

### The correction, run over the whole tree

`tools/decompiler-notfix.py` (`make decompiler-notfix`, `inventory/decompiler_notfix.csv`) is that pass. It reads a
reconstructed tree, writes a corrected one, and reports the counts:

    python3 tools/decompiler-notfix.py $WORK/UnrealScript $WORK/UnrealScript-fixed -o inventory

* **2,319 files, 2,056 sites moved, 0 refused, byte delta 0.** A move is one character, so every file keeps its size
  and a `diff` of the two trees is exactly the sites the manifest counts.
* **The `!` is placed at the end of the receiver, not of the expression**, so the pass walks back over groups, name
  literals and dotted links to the front of the chain: `PlayerController(C)!.IsSpectating()` becomes
  `!PlayerController(C).IsSpectating()`, `Class'X'!.static.F()` becomes `!Class'X'.static.F()`, and
  `Array[i]!.Field` becomes `!Array[i].Field` -- the same tree a reader of the bytecode would draw, since a postfix
  selector binds tighter than the unary operator.
* **Re-reading the fixed tree with the census returns 2,063 prefix and 0 postfix**, the same 2,063 negations the
  section above counts as recovered, and a second pass over the corrected tree moves **0** sites.
* **A site it cannot delimit is refused and named**, never guessed: the `--check` covers the six shapes the scanner
  must separate (prefix left alone, `!=`, a `!` inside a string, one inside a line comment and one inside a block
  comment, a class the tree does not have) plus a refusal (`"x"!`, a string that is not a bool), by exact text
  comparison, and asserts the pass is its own fixpoint.
* **With the patch installed the pass moves 0** -- `inventory/decompiler_notfix.csv` is regenerated from the patched
  tree -- so what it measures from now on is a renderer regression: a non-zero count means the postfix form came
  back.

### The renderer patch: a pre operator is not a post operator

`tools/patches/uelib-negation-prefix.patch` is the whole fix, and the cause is one condition in UELib:

    public bool IsPost()            // src/Core/Classes/UFunction.cs
    {
        return IsOperator() && OperPrecedence == 0;
    }

In this build a *pre* operator carries the `PreOperator` flag and **no precedence byte**. Measured from the dump's
own `Core.u` -- `Object.Not_PreBool` (native 129), `Object.Subtract_PreVector` (211) and `Object.Subtract_PreFloat`
(169) -- every one reads `prec=0, flags=0x00023411`, i.e. `Final|PreOperator|Native|Operator|Static|Public`. So
`OperPrecedence == 0` is true of a pre operator, `IsPost()` answered **true** where `IsPre()` answered true as well,
and both `NativeTableItem(UFunction)` and the two token renderers test `IsPost()` first: the postfix branch won every
native pre operator. That is not only `!` (`bFlag!`): unary minus went the same way (`float(StepHeight-)`,
`direction-`, `(dz / float(2))-`).

The fix adds the flag test, so a function the build declares `PreOperator` can never be classified post:

    return IsOperator() && OperPrecedence == 0 && !FunctionFlags.HasFlag(FunctionFlag.PreOperator);

`tools/fetch-uelib.sh` applies every `tools/patches/uelib-*.patch` in name order after checking out `8236c39`, and it
is idempotent both ways: a patch that already applies in reverse is reported as applied, and one that neither
applies nor reverses stops the fetch with exit 3 rather than leaving a checkout patched halfway.

**Two fresh `make decompile` runs over the same dump, one per renderer** (the committed artifact was one tree older
than today's build, so both columns were measured now):

| | prefix `!` | postfix `!` | postfix unary `-` |
|---|---:|---:|---:|
| shipped source | 2,064 | 0 | 0 |
| pinned renderer (`8236c39`) | 10 | 2,057 | 145 |
| patched renderer | **2,067** | **0** | **1** |

The negation total does not move (2,067 in both runs): the patch is a position fix, 2,057 sites, and the ten prefix
survivors stay prefix. `tools/decompiler-notfix.py` over the patched tree moves **0 sites** (2,056 over the pinned
one), so the correction pass is now a fixpoint check rather than a step, and `inventory/decompiler_operators.csv` and
`inventory/decompiler_notfix.csv` are regenerated from the patched tree. The `-` column is a text scan for
`[A-Za-z0-9_)]-` followed by `)`, `,`, `;` or a space: 145 sites in the pinned tree, 1 in the patched one.

## The operator deficit is two text artifacts, not a renderer gap

The census above compares two texts whole, and it reports the reconstruction 386 tokens short of the source in all
(`40,762` against `40,376`), `==` short by 854 and `-` long by 839. `tools/decompiler-operator-regions.py`
(`make decompiler-operator-regions`, `inventory/decompiler_operator_regions.csv`) cuts every class's two texts into
four regions and counts the operators in each, so the difference is attributed instead of described:

| region | what it is | source | reconstruction |
|---|---|---:|---:|
| `head` | the fragment before the first `*/` when the extracted `ScriptText` starts **inside** the banner comment | 778 | 0 |
| `defaults` | the `defaultproperties { ... }` block | 0 | 883 |
| `dead` | the `#ifdef` branches this build does not compile | 853 | 0 |
| `code` | everything else | 39,131 | 39,493 |

**The source column counts 724 `==` that are not comparisons.** `pkg-script-text.script_text` locates a class's text
by its first `class ` or `//` token, and the class banner is a `/* ... */` block whose middle lines often begin with
`//` -- so on **22 of the 2,319 classes** the extracted text starts *inside* that comment and the banner's closing
`====...` run is left outside any comment, where `clean()` reads every `=` pair as `==`. Those 22 classes carry
**724 of the source's 6,433 `==`**, plus 29 `*`, 22 `/` and 3 `-`. The reconstruction has **0** such class: the
reconstruction is written whole, so the fragment is the extractor's, not a property of the shipped text.

**Another 177 `==` are branches this build never compiles.** The shipped text carries `#ifdefXBOX` /
`#ifndefXBOX` / `#ifdefPC` / `#ifdefPS2` / `#ifndefPS2` / `#ifdefDEBUG` conditionals and the census counts both
sides of each; collapsing them to the branch the Xbox build compiles removes **177 `==`, 147 `!=`, 113 `+`,
106 `<`, 75 `>`, 71 `&&`** and 164 more -- 853 tokens in all.

**The reconstruction's 839 extra `-` are defaults values.** 883 of its 5,047 `-` sit inside a `defaultproperties`
block, which the reconstruction re-renders from the class *record*: 277 classes carry one, and the shipped
`ScriptText` carries the word in **15** classes and not one operator inside those blocks (0 `-` in the corpus). So
the surplus is not unary minus; it is the defaults block the source text does not ship.

**On code alone there is no deficit, and the reconstruction is slightly long.** `==` 5,532 source against 5,579,
`!=` 6,870 against 6,952, `&&` 3,608 against 3,643, `<` 3,615 against 3,663: the reconstruction carries **more**
operator tokens on **14 of the 20** operators and fewer on none (`**` and five others are equal). The 386-token gap
is then exactly `+778 head + 853 dead - 883 defaults - 362 code`, and per operator the four regions sum to
`inventory/decompiler_operators.csv`'s own rows to the unit, on both trees -- the independent claim that the split
covers the text the census counted and not a subset of it.

The split also names the 74 classes whose code columns disagree (`code_delta`), largest first: `StargatePawn`
(`!=` -17, `*` -11, `+` -10), `StoryModeGame` (`!=` -16, `==` -14, `&&` -8) and `Engine.Weapon` (`+` -10, `*` -9).
Every one of those is the reconstruction carrying *more* than the source, which is what a renderer that spells an
expression out rather than reusing a macro would do; the one class where the source is ahead is
`UPreview.UPreviewMapListClient` (`/` +7, `-` +6, `+` +4).

## What this does not measure

Statement shapes. This counts three constructs, not statements: no `if`/`while` nesting, call arity or statement
order is compared. The operator census above is the one text-level property the packed source settles without a tree
builder; the 3.3 oracle diff (bucket *matches leak*, *drifted*, *absent*) is the full text-level measure, and it needs
the privately held 2006 source, which this repository does not carry.

The operator census above counts both trees whole; `inventory/decompiler_operator_regions.csv` splits
that same count by text region, and the source column of the section above is not code-only.
