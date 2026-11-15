# The packages carry their own source

The most consequential thing found so far, and it came out of the export table: **every class in the build
ships its original UnrealScript source as a text export inside its own package.** Not a decompilation — the
text the compiler consumed.

    python3 tools/pkg-exports.py PKG.u --class SGOrder

```
SGOrder: export #94, 34 children
  ScriptText             TextBuffer     size=5857   offset=0x001aba9b
  assignBots             Function       size=268    offset=0x0007fefd
  Initialise             Function       size=221    offset=0x0008134a
  ...
  kinds: {'Function': 10, 'ArrayProperty': 2, 'ObjectProperty': 2, 'TextBuffer': 1, 'StrProperty': 1, ...}
```

Every class has exactly one `ScriptText` child, and it is a `TextBuffer` export — the engine's own container
for UnrealScript source.

## The count is exact

| | |
|---|---:|
| classes in the build | **2,319** |
| `Class` exports | **2,319** |
| `ScriptText` exports | **2,319** |
| source bytes | **6,149,951** (5.9 MiB) |

Per package, the three numbers agree everywhere — `StargateGame` 306/306/306 with 1,840,507 bytes of source,
`Engine` 367/367/367 with 1,769,955, `Core` 7/7/7. `inventory/package_script_text.csv` holds all 18 rows.

Source volume is small enough to be plausible and large enough to be real: 5.9 MiB over 2,319 classes is
2.6 KB per class, which is what a class declaration plus a few functions looks like — not a codebase, and not
nothing. `SGOrder`'s 5,857 bytes against 10 functions and 22 properties matches that ratio.

## What this changes

1. **The oracle problem gets much better.** `docs/ORACLE_DIFF.md` compares the decompiler's output against
   the privately held 2006 `Classes` tree, and could only check 291 classes. The packages themselves contain
   the text for all 2,319, including the 204 that agree and the 56 that drift — so drift can now be checked
   against what shipped, not against a tree from a different date.
2. **3.7's bytecode comparison stops being a guess.** If the exact source that produced the shipped bytecode
   is available, then compile-it-and-compare is a real experiment: same input, same compiler generation,
   compare function bytecode. Without the text, any recompilation differs by unknown amounts of source drift.
3. **The decompilation track's value changes.** UELib's decompiler output is no longer the only readable
   source; it is a cross-check on the shipped text. That is the reverse of the project's current framing.
4. **`ScriptText` explains a pile of things** in `docs/DECOMPILER_GAPS.md`: cases where the decompiler
   produced something odd are now checkable against the original line.


## What the text contains

`tools/pkg-script-text.py` extracts a class's `ScriptText` and reduces it to countable facts. Extraction
writes to `$WORK`; nothing here prints or commits source.

| measure | value |
|---|---:|
| classes with text | **2,319 of 2,319** |
| source bytes recovered | 6,113,909 |
| classes whose first line is `class X extends Y` | 2,319 |
| `native` function declarations found | 707 |
| `native` declarations recorded in `inventory/classes.csv` | 797 |

The text is the real thing: `SGOrder`'s first line is `class SGOrder extends Order editinlinenew native;`,
it parses as 219 lines with 44 comment lines, and its declarations match the export table's function list.

### Native functions can be named from the source

The declarations carry no indices in these packages, so they cannot be handed to a debugger directly — but
they do say **which** functions are C++, which is the half of PLAN 4.7 the class table could not answer
(`docs/systems/UCPP_NATIVES.md`).

| class | natives | functions |
|---|---:|---|
| `SGOrder` | 4 | `isValid`, `updateCondition`, `DrawDesc`, `DrawIcon` |
| `Electrocutor` | 2 | `StartArcing`, `WankTick` |
| `FarClipChange` | 2 | `GetFarClipDistance`, `SetFarClipDistance` |
| `ShadowManager` | 1 | `GetShadowLights` |
| `SGFontInfo` | 1 | `bendover` |
| `StargateHUD` | 1 | `DrawOrdersHud` |

All six counts equal `native_functions` in `inventory/classes.csv`, so the two independent measurements
agree. `inventory/native_declarations.csv` holds the rows.

### Where counting is incomplete, and why

Eight of eighteen packages match `classes.csv` exactly; five do not, and the shortfall is `Core` (101 found
vs 203 recorded). The cause is visible in Core's own source: it declares native **operators** — `native(208)
static final operator(20) float + ( float A, float B )` — which the counter does not treat as functions.
`StargateContent`'s three found against zero recorded is the mirror case: declarations the inventory does not
attribute to that package's classes. Both are recorded in `inventory/script_text_natives.csv` rather than
smoothed over; the six-class table above is the part that is validated.


## Extracting the whole tree, and what it is not

`python3 tools/pkg-script-text.py DUMP/System --tree --write $WORK/source-tree` writes one `.uc` per class.
Measured on 2026-10-24:

| | |
|---|---:|
| files written | **2,319** |
| bytes of source | 6,113,909 |
| files whose braces balance | 2,317 |
| files containing `defaultproperties` | **15** |
| files declaring functions | 1,182 |
| function declarations in the tree | 10,608 |
| function counts recorded in `inventory/classes.csv` | 10,335 |
| classes whose function count matches exactly | **1,789 of 2,319** |

`inventory/source_tree_summary.csv` holds the per-package file counts.

### The defaults are not in the text

**Only 15 of 2,319 classes have a `defaultproperties` block.** This is the important correction to the
enthusiasm of the previous section: `ScriptText` is the class declaration and its functions, and the class
**defaults live in the package's default-properties data, not in the source text**. So:

* The tree is not a drop-in replacement for the decompiled tree for rebuilding: a class compiled without its
  defaults is a different class, and every weapon's damage, every vehicle's mass, every emitter's colour
  lives there.
* The 3.6/3.7 plan does not get simpler, it gets a second input: the text for structure, the defaults export
  for values. The compile loop needs both.
* PR #45's claim that the exact text behind the shipped bytecode is available stands, but "exact" now means
  "for the script body" — the values that shape that bytecode's constants are elsewhere.

### The text carries a commented-out revision of some classes

The counts above count the whole buffer, and part of the buffer is commented out. Measured over the packages
rather than over the extracted tree (`tools/class-text-comments.py`, `make class-text-comments`,
`inventory/class_text_comments.csv`): **252 of the 2,319 classes** carry a `/* ... */` block, and in **39** of
them the remainder outside every comment is the class declaration alone. Those 39 are classes whose body --
natives, vars, functions, and in two cases a `defaultproperties` block -- sits *inside* the comment, which is
what a disabled revision of a class looks like in this build's own source. The other 213 carry a live body
beside the comment, so a block comment is not the finding by itself; what separates the two populations is what
the live remainder declares.

<!-- generated:class-text-comments -->
**252 of 2319 classes ship a commented-out block, and 39 of them have nothing live but the declaration.** The other 213 carry a live body beside the comment, so a block comment is not itself the finding -- what separates the two is what the live remainder declares.

| measure | value |
|---|---|
| classes with a `ScriptText` | **2319** |
| carrying a `/* ... */` block | **252** |
| live part declares no `function` and no `var` | **39** |
| live part declares a body | **213** |
| `function` declarations inside a block comment | **208** |
| `var` declarations inside a block comment | **51** |
| `native function` declarations inside a block comment | **6** |
| `defaultproperties` blocks inside a block comment | **2** |
| `defaultproperties` blocks outside every comment | **0** |
| bytes of text / of it inside a block comment | **5881021** / **278024** |

**The class record agrees, and the base rate is why that alone proves nothing.** All **39** body-in-comment classes are `functions=0, properties=0` in `inventory/classes.csv`, and **0** of the 213 classes that keep a live body are -- but **953 of the corpus's 2319** classes record `0/0` anyway, so a record cannot name one. The text can, and the pair of rates is the control: inside the comment population the split is 39 of 39 against 0 of 213.

Per package (`classes` / `with a comment` / `body in the comment`):

| package | classes | with a block comment | body in the comment |
|---|---:|---:|---:|
| `AmbientCreatures` | 18 | 2 | 0 |
| `Core` | 7 | 3 | 2 |
| `Engine` | 367 | 85 | 10 |
| `GUI` | 148 | 40 | 21 |
| `GamePlay` | 262 | 8 | 2 |
| `IpDrv` | 14 | 1 | 0 |
| `StargateContent` | 567 | 26 | 1 |
| `StargateGame` | 306 | 62 | 2 |
| `StargateMenus` | 98 | 4 | 1 |
| `StargateVehicles` | 142 | 10 | 0 |
| `Stargateeffects` | 77 | 1 | 0 |
| `UDebugMenu` | 38 | 3 | 0 |
| `UWindow` | 78 | 7 | 0 |

The 39 body-in-comment classes, `payload` being the class export's serial size:

| class | payload | text bytes | commented | `defaultproperties` in the comment |
|---|---:|---:|---:|---:|
| `Core.Time` | 91 | 3123 | 1701 | - |
| `Core.Locale` | 91 | 6355 | 6024 | 1 |
| `Engine.WaterSceneManager` | 99 | 390 | 267 | - |
| `Engine.xMaterialTrigger` | 94 | 2814 | 2759 | - |
| `Engine.PSLuminanceTexIndexMaterial` | 101 | 529 | 390 | - |
| `Engine.PSBumpBSP` | 109 | 537 | 365 | - |
| `Engine.PSBumpSpecularBSP` | 107 | 974 | 274 | - |
| `Engine.PSModulatedDistortionMaterial` | 103 | 423 | 340 | - |
| `Engine.PSAdditiveDistortion` | 103 | 409 | 335 | - |
| `Engine.PSSubtractiveDistortion` | 103 | 419 | 342 | - |
| `Engine.PSVDM` | 125 | 825 | 311 | - |
| `Engine.PSReplicatorVision` | 128 | 746 | 267 | - |
| `GUI.STY_OnScreenKeyboardButton` | 300 | 302 | 248 | - |
| `GUI.STY_Sui20pt_C8C8C87F` | 256 | 345 | 273 | - |
| `GUI.SGVertScrollBar` | 106 | 808 | 594 | 1 |
| `GUI.STY_CharacterButton` | 131 | 296 | 248 | - |
| `GUI.fntXboxSymbols` | 244 | 273 | 215 | - |
| `GUI.STY_Sui28pt_C8C8C8FF` | 221 | 347 | 275 | - |
| `GUI.STY_Sui24pt_C8C8C8FF` | 265 | 347 | 275 | - |
| `GUI.STY_Sui18pt_FFFFFFFF` | 234 | 339 | 267 | - |
| `GUI.STY_Sui18pt_FF9C00FF` | 265 | 339 | 267 | - |
| `GUI.STY_Sui18pt_C8C8C8FF` | 226 | 345 | 273 | - |
| `GUI.STY_Sui12pt_C8C8C8FF` | 226 | 345 | 273 | - |
| `GUI.STY_ScrollBox` | 195 | 267 | 224 | - |
| `GUI.STY_OnScreenKeyboardButtonPressed` | 147 | 375 | 248 | - |
| `GUI.STY_MessageBoxButton` | 255 | 297 | 249 | - |
| `GUI.STY_MessageBox` | 301 | 259 | 196 | - |
| `GUI.STY_ListBoxHeader` | 157 | 281 | 225 | - |
| `GUI.STY_ImageStyle_13816BFF` | 268 | 353 | 275 | - |
| `GUI.STY_ImageListItem` | 144 | 293 | 225 | - |
| `GUI.STY_ImageListCursor` | 127 | 290 | 220 | - |
| `GUI.STY_GroupingBox` | 186 | 286 | 223 | - |
| `GUI.STY_ArrowScrollPanel` | 140 | 313 | 240 | - |
| `GamePlay.BumpSpecularShader` | 105 | 368 | 316 | - |
| `GamePlay.BumpShader` | 103 | 352 | 316 | - |
| `StargateContent.CaptainStaffWeaponAttachment` | 100 | 206 | 134 | - |
| `StargateGame.GoauldConsoleFillVessel` | 118 | 411 | 75 | - |
| `StargateGame.SGScreenComboFlash` | 97 | 487 | 398 | - |
| `StargateMenus.XBoxLiveServerSelect` | 128 | 522 | 459 | - |

**Every `defaultproperties` block the build ships as text is in that population** -- `Core.Locale`, `GUI.SGVertScrollBar` -- so the text cannot serve as an oracle for this build's compiled defaults: the block it carries belongs to a revision of the class that the package did not compile. `tools/defaults-emit.py --verify` now prints the same status per block.

Census: 2319 classes read, 252 with a block comment.
<!-- /generated:class-text-comments -->

Three consequences, each measured rather than argued:

* **A declaration count over the whole buffer counts disabled declarations.** 208 `function` and 51 `var`
  declarations, and 6 `native function` declarations, sit inside a block comment, so a per-class count that
  scans the whole text is an upper bound on what the class compiles.
* **The tree row above counts mentions.** Of the 16 classes whose text contains the word `defaultproperties`,
  only **2** carry a block, and both are commented out -- the rest are prose in a comment (`Engine.Actor`'s
  clipboard help text, a `// defaultproperties` banner in eight classes, and two classes whose disabled
  revision keeps a commented block: `StargateContent.P90Torch`, `StargateGame.Constructions`).
* **The two blocks are not this build's defaults.** `Core.Locale` (50 properties) and `GUI.SGVertScrollBar`
  (13) are both in the 39, so `tools/defaults-emit.py --verify` has been comparing the renderer against a
  revision the package did not compile. That closes the three candidate explanations `docs/CLASS_DEFAULTS.md`
  left open for the `WinWidth` mismatch (`0.02` in the text, `20.0` in the package): the text is a different
  revision, and the instrument now prints the comment status of each block it verifies.

### Where the function count drifts

1,789 of 2,319 classes match the inventory's function count exactly; the rest differ by one or two in either
direction. Three causes are visible in the data, and one is not yet explained:

* **state-scoped functions** are declared inside `state` blocks and are counted by the source reader but not
  always by the inventory's per-class count (the inventory counts states separately: 553 in total);
* **`event` declarations** parse as functions in some classes;
* truncated tails (`GameInfo.uc` and `WarpZoneMarker.uc` do not balance their braces — the extraction stops
  where the text does).

The unexplained residue is small but real: 530 classes' counts disagree and this file does not claim to know
which side is right. Both numbers are recorded; the disagreement is the finding.

## Handling: measured here, extracted elsewhere

The text is game-derived. Per `AGENTS.md` and `docs/LEGAL.md` it must not be committed: what is committed is
the **counts and sizes** above. Extraction writes to `$WORK` on the machine that owns the dump, and the
repository only ever cites byte sizes and the tool that reads them.

That is not a technicality — it is the difference between an inventory and a copy of the game's source.

## Not measured

* The text's *completeness*: it parses as source for all 2,319 classes, but whether every body is present
  (rather than every declaration) is not measured — that needs a compile attempt, i.e. 3.6.
* Native **indices**: the declarations here say `native function`, not `native(1234)`, so the index still
  lives in the function's record inside the package. The names are now known; the numbers are not.
* Whether the text is byte-identical to what the compiler consumed. The package stores what the build wrote;
  a compiler may have normalised line endings or comments.
* Function bodies. The export table gives each function's name, size and byte range, but no `UFunction`
  record or bytecode is decoded yet, so "native vs script" is still not separable from the table alone —
  `docs/systems/UCPP_NATIVES.md` records the same gap from the other side.
