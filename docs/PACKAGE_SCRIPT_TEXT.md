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
| `native` function declarations found | 707 (26 disabled — 6 in a `/* */`, 20 in a `//` — so **681 live**) |
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

This table is a comparison of two *different* things — declarations in the text against function records in
the class — so a difference between them is not an error to explain away. `Core` (101 found vs 203 recorded) is
the largest: its source declares native **operators** — `native(208) static final operator(20) float + ( float
A, float B )` — and the counter does not treat those as functions. `StargateContent`'s three found against zero
recorded is the mirror case, and it is **closed below**: the three are inside a block comment in
`StargateContent.SuperSoldier`, so no record could carry them.

**The whole-buffer number is not the live one, and the difference is 26 declarations.** 6 of the 707 sit in a
`/* ... */` and 20 in a `//` run, so the text declares **681** native functions. The line-comment half is the
larger one and it lands in packages this table calls a *surplus*: `Engine` is 16 disabled declarations (which is
why 451 found against 444 recorded is not 7 extra functions) and `GUI` 4. So a per-class attribution has to
start from the live count and from the record side, not from this table.

**The record side closes it per record, not per count.** `inventory/native_reconcile.csv` matches the two by
name: `Core`'s 203 records are 97 matched declarations plus 106 records that its 86 live `operator` statements
expand into, and 2 declarations (`Core.Object`'s `Divide` and `Round`) have no record at all; `Engine`'s 444 are
438 matched plus 6 operator records with 3 declarations left over. Corpus-wide that is 797 records = 683 matched
+ 112 operator-expanded + 2 with no declaration, against 692 live declarations (681 functions + 11 events) = 683
matched + 9 with no record. Quoting either count alone cannot say that, which is why both stay in
`inventory/script_text_natives.csv` rather than being smoothed over.


### The two counts, closed per package

`tools/native-count-closure.py` reads the artifacts above (`inventory/native_reconcile.csv`,
`inventory/classes.csv`, `inventory/native_residual.csv`, this page's comment census) and checks two identities
per package: `records = matched + operator-expanded + record-only`, and
`declarations = matched + no-record + operator statements`. It needs no dump, so it is a per-commit gate rather
than a nightly stage.

<!-- generated:native-count-closure -->
**The two counts close per record, not by arithmetic: 18 packages of 18.** A package records more natives than its text declares when a live `operator` statement expands into several typed records, and fewer when a declaration has no record -- so `records = matched + operator-expanded + record-only` and `declarations = matched + no-record + operator statements` are the two identities, and both hold on every package here.

| measure | value |
|---|---:|
| `native function` declarations in the whole buffer | **707** |
| ... inside a `/* ... */` / inside a `//` run | **6** / **20** |
| live declarations (functions + events + operators) | **681** + **11** + **92** |
| declarations that name a record | **683** |
| records those `operator` statements expand into | **112** |
| declarations with no record | **9** |
| records no declaration explains | **2** |
| native function records | **797** |

| package | whole | `/* */` | `//` | live fn | events | operators | matched | expanded records | no record | no declaration | records |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `Core` | 101 | 3 | 0 | 98 | 1 | 86 | 97 | 106 | 2 | 0 | 203 |
| `Editor` | 14 | 0 | 0 | 14 | 0 | 0 | 14 | 0 | 0 | 0 | 14 |
| `Engine` | 451 | 0 | 16 | 435 | 6 | 6 | 438 | 6 | 3 | 0 | 444 |
| `GUI` | 82 | 0 | 4 | 78 | 3 | 0 | 81 | 0 | 0 | 0 | 81 |
| `IpDrv` | 31 | 0 | 0 | 31 | 1 | 0 | 32 | 0 | 0 | 0 | 32 |
| `StargateContent` | 3 | 3 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 |
| `StargateGame` | 21 | 0 | 0 | 21 | 0 | 0 | 17 | 0 | 4 | 0 | 17 |
| `StargateVehicles` | 4 | 0 | 0 | 4 | 0 | 0 | 4 | 0 | 0 | 0 | 4 |
| `UPreview` | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 0 | 2 | 2 |

**Every package closes both identities** -- `inventory/native_count_closure.csv` carries the per-package row and its verdict, so a package that stops closing fails this block rather than being averaged into a total.
<!-- /generated:native-count-closure -->


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
| `function` declarations inside a block comment | **226** |
| of those, naming a function the same class still exports | **134** |
| ... also declared in the same class's live text | **45** |
| ... naming a function another class of the package exports | **33** |
| ... naming no function the package exports at all | **59** |
| live `function` declarations in the same classes / naming an export | **5927** / **5890** |
| `var` declarations inside a block comment | **51** |
| `native function` declarations inside a block comment | **6** |
| `native function` declarations inside a `//` run | **20** |
| native declarations left live | **681** of 707 |
| `defaultproperties` blocks inside a block comment | **2** |
| `defaultproperties` blocks outside every comment | **0** |
| bytes of text / of it inside a block comment | **5881021** / **278024** |

**The class record agrees, and the base rate is why that alone proves nothing.** All **39** body-in-comment classes are `functions=0, properties=0` in `inventory/classes.csv`, and **0** of the 213 classes that keep a live body are -- but **953 of the corpus's 2319** classes record `0/0` anyway, so a record cannot name one. The text can, and the pair of rates is the control: inside the comment population the split is 39 of 39 against 0 of 213.

**The comment is mostly a revision of the class that shipped, and a third of its declarations name a function the build does not carry.** Joined name by name against the package's own exports, **134 of 226** `function` declarations inside a block comment name a function the same class still exports, **33** name a function another class of the same package exports (the declaration moved) and **59** name nothing the package exports at all. The control says the join is not the weak part: the same matcher over the same classes' live declarations places **5890 of 5927 (99.4%)**, against **134 of 226 (59.3%)** for the commented ones. **45** of the placed ones are also declared in the live text, so those classes carry both revisions of the same function. The absent ones split by population: **46** sit in a class that keeps a live body -- classes this build compiled, where the function is simply not there -- and **13** in the 39 classes whose whole body is the comment, where the class itself exports nothing at all. **4** of the absent names still occur somewhere in the live text, but as a mention rather than a declaration, so they stay counted absent.

The 22 classes that keep a live body and still carry a commented declaration the package does not export, `declared` being every `function` declaration in the comment:

| class | declared | placed | moved | absent | functions in the record |
|---|---:|---:|---:|---:|---:|
| `StargateGame.TeamAI` | 10 | 0 | 0 | 10 | 27 |
| `Engine.xAdminGroup` | 8 | 1 | 1 | 6 | 7 |
| `StargateGame.BotSG` | 5 | 1 | 0 | 4 | 93 |
| `StargateVehicles.SemektetStaffCannonGun` | 16 | 9 | 4 | 3 | 5 |
| `Engine.Screenplay` | 17 | 14 | 1 | 2 | 46 |
| `GamePlay.ScriptedController` | 2 | 0 | 0 | 2 | 38 |
| `StargateContent.SuperSoldier` | 4 | 0 | 2 | 2 | 24 |
| `StargateGame.BotEnemy` | 5 | 3 | 0 | 2 | 168 |
| `StargateGame.StargatePawn` | 42 | 40 | 0 | 2 | 218 |
| `Engine.AIMsgManager` | 2 | 1 | 0 | 1 | 39 |
| `Engine.MapList` | 1 | 0 | 0 | 1 | 11 |
| `Engine.MaplistManager` | 1 | 0 | 0 | 1 | 55 |
| `Engine.PlayerController` | 13 | 12 | 0 | 1 | 360 |
| `GamePlay.TournamentConsole` | 1 | 0 | 0 | 1 | 3 |
| `StargateContent.ExtreniteWeapon` | 1 | 0 | 0 | 1 | 9 |
| `StargateGame.AlarmPanel` | 1 | 0 | 0 | 1 | 8 |
| `StargateGame.StargateAIMsgManager` | 1 | 0 | 0 | 1 | 4 |
| `StargateGame.StargatePlayer` | 1 | 0 | 0 | 1 | 134 |
| `StargateGame.StargateWeapon` | 3 | 1 | 1 | 1 | 88 |
| `StargateMenus.ServerSelectionPage` | 1 | 0 | 0 | 1 | 3 |
| `StargateVehicles.DeathGliderBot` | 1 | 0 | 0 | 1 | 20 |
| `StargateVehicles.StargateHover` | 1 | 0 | 0 | 1 | 11 |

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

| class | payload | text bytes | commented | `function` declarations | absent | `defaultproperties` in the comment |
|---|---:|---:|---:|---:|---:|---:|
| `Core.Time` | 91 | 3123 | 1701 | 3 | 3 | - |
| `Core.Locale` | 91 | 6355 | 6024 | 9 | 9 | 1 |
| `Engine.WaterSceneManager` | 99 | 390 | 267 | 0 | 0 | - |
| `Engine.xMaterialTrigger` | 94 | 2814 | 2759 | 6 | 0 | - |
| `Engine.PSLuminanceTexIndexMaterial` | 101 | 529 | 390 | 0 | 0 | - |
| `Engine.PSBumpBSP` | 109 | 537 | 365 | 0 | 0 | - |
| `Engine.PSBumpSpecularBSP` | 107 | 974 | 274 | 0 | 0 | - |
| `Engine.PSModulatedDistortionMaterial` | 103 | 423 | 340 | 0 | 0 | - |
| `Engine.PSAdditiveDistortion` | 103 | 409 | 335 | 0 | 0 | - |
| `Engine.PSSubtractiveDistortion` | 103 | 419 | 342 | 0 | 0 | - |
| `Engine.PSVDM` | 125 | 825 | 311 | 0 | 0 | - |
| `Engine.PSReplicatorVision` | 128 | 746 | 267 | 0 | 0 | - |
| `GUI.STY_OnScreenKeyboardButton` | 300 | 302 | 248 | 0 | 0 | - |
| `GUI.STY_Sui20pt_C8C8C87F` | 256 | 345 | 273 | 0 | 0 | - |
| `GUI.SGVertScrollBar` | 106 | 808 | 594 | 0 | 0 | 1 |
| `GUI.STY_CharacterButton` | 131 | 296 | 248 | 0 | 0 | - |
| `GUI.fntXboxSymbols` | 244 | 273 | 215 | 0 | 0 | - |
| `GUI.STY_Sui28pt_C8C8C8FF` | 221 | 347 | 275 | 0 | 0 | - |
| `GUI.STY_Sui24pt_C8C8C8FF` | 265 | 347 | 275 | 0 | 0 | - |
| `GUI.STY_Sui18pt_FFFFFFFF` | 234 | 339 | 267 | 0 | 0 | - |
| `GUI.STY_Sui18pt_FF9C00FF` | 265 | 339 | 267 | 0 | 0 | - |
| `GUI.STY_Sui18pt_C8C8C8FF` | 226 | 345 | 273 | 0 | 0 | - |
| `GUI.STY_Sui12pt_C8C8C8FF` | 226 | 345 | 273 | 0 | 0 | - |
| `GUI.STY_ScrollBox` | 195 | 267 | 224 | 0 | 0 | - |
| `GUI.STY_OnScreenKeyboardButtonPressed` | 147 | 375 | 248 | 0 | 0 | - |
| `GUI.STY_MessageBoxButton` | 255 | 297 | 249 | 0 | 0 | - |
| `GUI.STY_MessageBox` | 301 | 259 | 196 | 0 | 0 | - |
| `GUI.STY_ListBoxHeader` | 157 | 281 | 225 | 0 | 0 | - |
| `GUI.STY_ImageStyle_13816BFF` | 268 | 353 | 275 | 0 | 0 | - |
| `GUI.STY_ImageListItem` | 144 | 293 | 225 | 0 | 0 | - |
| `GUI.STY_ImageListCursor` | 127 | 290 | 220 | 0 | 0 | - |
| `GUI.STY_GroupingBox` | 186 | 286 | 223 | 0 | 0 | - |
| `GUI.STY_ArrowScrollPanel` | 140 | 313 | 240 | 0 | 0 | - |
| `GamePlay.BumpSpecularShader` | 105 | 368 | 316 | 0 | 0 | - |
| `GamePlay.BumpShader` | 103 | 352 | 316 | 0 | 0 | - |
| `StargateContent.CaptainStaffWeaponAttachment` | 100 | 206 | 134 | 0 | 0 | - |
| `StargateGame.GoauldConsoleFillVessel` | 118 | 411 | 75 | 0 | 0 | - |
| `StargateGame.SGScreenComboFlash` | 97 | 487 | 398 | 0 | 0 | - |
| `StargateMenus.XBoxLiveServerSelect` | 128 | 522 | 459 | 1 | 1 | - |

**Every `defaultproperties` block the build ships as text is in that population** -- `Core.Locale`, `GUI.SGVertScrollBar` -- so the text cannot serve as an oracle for this build's compiled defaults: the block it carries belongs to a revision of the class that the package did not compile. `tools/defaults-emit.py --verify` now prints the same status per block.


**6 of the `native function` declarations a whole-buffer count credits are inside a block comment**, and they sit in `Core.Locale`, `StargateContent.SuperSoldier` -- classes whose live part declares none of them, so the source-side native total is 701 against the 707 such a count reports. `StargateContent`'s 3-against-0 in `inventory/script_text_natives.csv` is one of those classes and not a package the inventory mis-attributes.

**The other comment form disables more of them: 20 `native function` declarations sit inside a `//` run**, in 9 classes of which 2 carry no block at all (`Engine.Pawn`, `Engine.Controller`, `Engine.PlayerController`, `Engine.GameInfo`, `Engine.PlayerStats`, `Engine.AIMsgManager`, `Engine.KConeLimit`, `GUI.GUIController`, `GUI.UIXInteraction`). A block-only count is what called this corpus 701 live; with both forms subtracted the source declares **681** live native functions, so any per-class attribution that starts from the whole-buffer number inherits the 26 disabled ones.

Census: 2319 classes read, 252 with a block comment.
<!-- /generated:class-text-comments -->

Three consequences, each measured rather than argued:

* **A declaration count over the whole buffer counts disabled declarations.** 208 `function` and 51 `var`
  declarations, and 6 `native function` declarations, sit inside a block comment, so a per-class count that
  scans the whole text is an upper bound on what the class compiles.
* **The comment is a revision of the class, and what it declares that the build does not is the residue.** Joined
  name by name against the package's own `Function` exports, **124 of the 208** commented declarations name a
  function the same class still exports, **29** name one another class of the package exports, and **55** name
  no export of the package at all. The control is the same matcher over the same classes' live declarations:
  **4,872 of 4,890** place, against 59.6% for the commented ones, so the join is not the weak part. The 55 are
  concentrated: `StargateGame.TeamAI` alone declares ten functions the package never compiles and
  `Engine.xAdminGroup` six, each beside a live class that carries a different set of functions -- a replaced
  revision rather than a renamed one. The generated block above carries the per-class table.
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
direction. Four causes are visible in the data, and one is not yet explained:

* **state-scoped functions** are declared inside `state` blocks and are counted by the source reader but not
  always by the inventory's per-class count (the inventory counts states separately: 553 in total);
* **`event` declarations** parse as functions in some classes;
* truncated tails (`GameInfo.uc` and `WarpZoneMarker.uc` do not balance their braces — the extraction stops
  where the text does).
* front-truncated buffers: **22** of the 2,319 `ScriptText`s carry more `/* ... */` closes than opens, i.e. the
  export begins inside a doc comment, so the leading prose parses as declarations and code. `GUI.MenuItemOverlay`
  is one, and it is where the corpus's only keyword outside every function and every block sits
  (`inventory/text_branches.csv`, `tools/text-branches.py` counts the population).

The unexplained residue is small but real: 530 classes' counts disagree and this file does not claim to know
which side is right. Both numbers are recorded; the disagreement is the finding.

## What a disabled body weighs

`tools/class-text-comments.py` counts the `function` declarations a block comment disables and joins them to
the package's exports by name; it stops at the declaration. `tools/class-text-bodies.py` reads the body behind
each one -- its brace span, its operator multiset (through `tools/decompiler-operators.py`'s own token table,
so a change to that table moves both pages at once), the statements at the body's own brace depth and its byte
length -- and splits the population three ways: a commented body whose name the live text of the same class
also declares with a body, a commented body with no live twin, and the live declarations themselves, which are
the reader's control.

Most of what a comment disables it disables whole: the body sits inside the same `/* ... */`, so the comment
is readable per function and not only per declaration. A live twin of the same name is usually **not** the
same function -- the operator multiset alone separates most pairs, and a group of them keeps the class's
declaration while the body beside it is a trailer. And the commented bodies with no live twin at all are the
weight of the comment: bytes, operators and statements this build never compiled, which is a cut-content
ranking rather than another declaration count.

A reader that found fewer bodies in the live text than the text declares would not be measuring the comment,
so the same reader runs over the live declarations too and the block reports the pair of rates; the residue
in the comments is the comment's own, not the pattern's.

The bodies also say what the cut code *used*: every call name a body makes is joined against the
corpus's own `Function` exports, so a body that calls functions the build still carries is a revision of this
build rather than a fragment of some other tree. A cast is not a call -- `PlayerController(Owner)` and
`vect(0,0,0)` are type conversions that the call pattern counts, and the type table has to take them out before
the join means anything: with them out the disabled bodies leave **10.0%** of their call names unresolved
against **1.2%** for the live bodies of the same classes, and the residue is a typed population rather than a
base rate -- nine names declared inside a block comment themselves, twelve member calls on a class that
declares no such function (nine of them the AI override API `StargateGame.TeamAI.setorders` calls on a
`BotEnemy`), four declared nowhere at all.

Those twelve are then read against the build's *other* vocabularies, because a name missing from the export
table is not a name missing from the build: the `state` blocks the class texts declare, the labels at a state
body's own statement level, and each package's own name table, which the compiler fills with every name any of
its objects referenced. **The AI override API's names are not this build's.** Eight of the nine names
`StargateGame.TeamAI.setorders` calls on a `BotEnemy` -- `ActivateStateOveride`, `ActivateFearOveride`,
`ActivateForceMelee` and their three `DeActivate` counterparts, `DoRangedAttack`, `DoRetreat` -- appear in no
name table of the eighteen packages, so nothing in this build ever referenced them. The ninth, `DoCharge`, is
the **entry label of a state**: `StargateContent.JaffaBotHeavy` jumps into its `MelleeCharge` state with
`GotoState('MelleeCharge', 'DoCharge')`, which is a jump target and not something a call site can reach. What
the bots present instead is a state vocabulary -- `Mellee`, `MelleeCharge`, `Ranged`, `Flee`, `CoveredAttack`
(`inventory/ai_states.csv`, and `docs/systems/AI.md` for the machine) -- entered with `GotoState`.

That is a statement about the *names*, not about the feature: the same bodies call the behaviours under the
family's own spellings, and the word table the generator renders below puts a live declaration beside each
commented name (`DoRangedAttack` beside `DoRanged`, `DoRetreat` beside `PickRetreatDestination`, the
`Activate`/`DeActivate` pairs beside `ShouldSquadOverideAI` and `ShouldRunAwayFromFearSpot`). So the commented
`setorders` revision is an older or parallel naming of functions this build has, not a feature it removed --
and the two names it leaves unmatched (`ActivateForceMelee`, `DeActivateForceMelee`) carry only words
(`force`, `melee`) the corpus uses too widely for a word match to be evidence either way, which is a gap the
bodies would have to close, not the names.

The two base rates are what make that a finding rather than a reader artefact: **13 of the 266** state names
and **7 of the 54** state labels the corpus declares are also `Function` exports, so a name this build ships is
normally visible in the export table, and an API that had merely been renamed would leave its names in the name
tables if nowhere else.


<!-- generated:class-text-bodies -->
**198 of the 226 declarations a block comment disables also carry their body inside that comment, and 25 of them have a live twin of the same name in the same class's text.** The reader is not the weak part: over the same classes' live text it finds a body for **5318 of 5927 declarations (89.7%)**, against **87.6%** inside the comments -- so the shortfall is the comment's own residue, not the pattern's. What the 25 pairs say is that a commented revision is **not** the function the build carries: **1** are byte-identical, **6** keep the operator multiset with a different layout, **12** move at least one operator and **6** leave a live body of 32 bytes or fewer behind a substantial comment -- 10684 commented bytes against 7672 live ones over the population.

| measure | value |
|---|---|
| classes with a `ScriptText` / carrying a block comment | **2319** / **252** |
| declarations inside a block comment | **226** |
| ... with a body in the same comment | **198** |
| ... a declaration without a body | **28** |
| live declarations / whose body the reader finds | **5927** / **5318** |
| commented bodies with a live twin of the same name | **25** |
| ... byte-identical | **1** |
| ... same operator multiset, different layout | **6** |
| ... at least one operator moved | **12** |
| ... whose live body is a trailer | **6** |
| bytes, commented revisions / live revisions of the pairs | **10684** / **7672** |
| operators, the same pair | **103** / **91** |
| distinct call names, the pairs' commented / live revisions | **80** / **51** |
| ... naming no `Function` export of the corpus | **2** / **0** |
| commented bodies with no live twin | **172** |
| bytes / operators / statements of those bodies | **54105** / **534** / **574** |
| distinct call names in those bodies | **361** |
| ... declared by the body's own class / elsewhere in the corpus / by nothing | **88** / **238** / **35** |
| ... of which a cast rather than a call (in the earlier revision's count) | **39** of **400** |
| the same read over every live body of the commented classes (control) | **3522** / **6503** / **119** of **10144** |
| ... of which a cast rather than a call (in the earlier revision's count) | **1272** of **11416** |
| unresolved call names, typed by evidence / their call sites | **28** / **35** |
| ... declared inside a block comment / a member call on a type / nowhere / `#ifdef` | **9** / **12** / **7** / **0** |
| ... a `state` name / a state label / only a name in a table / in no name table | **0** / **1** / **3** / **24** |
| ... called on a `BotEnemy` receiver, of which the build carries | **9** / **1** |
| distinct `state` names / also a `Function` export | **313** / **14** |
| distinct state-label names / also a `Function` export | **63** / **7** |


**The rule needed three shapes the grammar's first form did not have, and each is its own number.** A declaration is introduced by `event`, `delegate` or the operator family as well as by `function`, carries any of the grammar's modifiers, may put a modifier after the keyword (`function static`) and may start its parameter list on the next line -- which is how the engine's own `PlayerController` and `GameProfile` write theirs. Counted with the four-modifier `function`-only rule the corpus reads **5098** declarations over the classes that carry a comment; with the grammar's modifiers and keywords but the parameter list required on the same line, **6112**; with the parameter list allowed on its own line, **6153** -- of which **6115** are same-line and **3** carry a trailing modifier. What that does to this census is the three readings side by side: commented declarations **208 -> 226 -> 226**, live declarations **4890 -> 5886 -> 5927**, pairs **22 -> 25 -> 25** and disabled bodies **158 -> 172 -> 172** (first rule, same-line rule, this rule), so every figure the page quoted before was a floor under a rule that could not see the declarations it was counting.

**The state read was case-sensitive and the text is not.** UnrealScript folds keywords, and **35** of the **261** `state` declarations this read finds over the same classes are spelled `State` -- lost to the case-sensitive pattern that a previous revision counted with, which is why the tree once recorded five states (`GetInFormation`, `MoveInFormation`, `TakePoint`, `WaitingForFormation`, `Broken`) as ones no text declares: the text declares them, in a capitalised block.
Every pair, `ops` being the operator multiset and `stmts` the statements at the body's own brace depth:

| class.function | verdict | commented ops / stmts / bytes | live ops / stmts / bytes | operators that moved |
|---|---|---:|---:|---|
| `Engine.AIMsgManager.issubtypeof` | differs | 23 / 15 / 1454 | 2 / 4 / 118 | !=,&,+,-,<,==,> |
| `Engine.xAdminGroup.removeuser` | differs | 0 / 1 / 23 | 1 / 0 / 137 | != |
| `StargateContent.BotScarab.beginstate` | differs | 0 / 4 / 126 | 2 / 10 / 321 | !=,== |
| `StargateContent.BotScarab.pickdestination` | differs | 2 / 7 / 326 | 2 / 7 / 437 | !=,- |
| `StargateContent.HaakenBot.timer` | differs | 0 / 1 / 21 | 3 / 1 / 339 | !=,-,>= |
| `StargateContent.ReetouBot.pickdestinationtododgephasetorch` | differs | 37 / 39 / 3111 | 58 / 50 / 3159 | !=,*,+,-,<,==,> |
| `StargateContent.ReetouWeapon.botfire` | differs | 0 / 4 / 105 | 1 / 2 / 101 | == |
| `StargateContent.SuperSoldier.playfiring` | differs | 8 / 4 / 1422 | 0 / 2 / 96 | !=,&&,*,-,==,> |
| `StargateGame.BotEnemy.enemynotvisible` | differs | 0 / 1 / 76 | 1 / 1 / 52 | != |
| `StargateVehicles.SemektetMortarGun.animend` | differs | 1 / 2 / 89 | 6 / 3 / 491 | !=,==,|| |
| `StargateVehicles.SemektetStaffCannonGun.animend` | differs | 0 / 1 / 48 | 4 / 0 / 203 | !=,==,|| |
| `StargateVehicles.SemektetStaffCannonGun.projectilefirebarrels` | differs | 4 / 13 / 988 | 0 / 9 / 660 | *,/ |
| `StargateGame.BotEnemy.findlocalcover` | identical | 0 / 1 / 16 | 0 / 1 / 16 | - |
| `Engine.PlayerController.jump` | live-stub | 1 / 2 / 115 | 0 / 0 / 7 | == |
| `Engine.Weapon.fire` | live-stub | 9 / 1 / 496 | 0 / 0 / 2 | +,-,<,==,>,|| |
| `Engine.Weapon.serveraltfire` | live-stub | 0 / 2 / 88 | 0 / 1 / 28 | - |
| `Engine.Weapon.serverfire` | live-stub | 4 / 2 / 481 | 0 / 1 / 25 | -,==,> |
| `StargateContent.BotScarab.timer` | live-stub | 3 / 2 / 221 | 0 / 1 / 28 | !=,&&,== |
| `StargateGame.StargateWeapon.serverfire` | live-stub | 0 / 2 / 48 | 0 / 1 / 25 | - |
| `Engine.Controller.postloadsavedgame` | same-operators | 1 / 2 / 80 | 1 / 2 / 140 | - |
| `StargateContent.HaakenBot.beginstate` | same-operators | 0 / 1 / 36 | 0 / 3 / 117 | - |
| `StargateContent.HaakenBot.notifytakehit` | same-operators | 1 / 3 / 224 | 1 / 3 / 165 | - |
| `StargateGame.BotEnemy.needtoturn` | same-operators | 2 / 8 / 234 | 2 / 9 / 275 | - |
| `StargateGame.StargateWeaponEmplacement.updateaimingrotation` | same-operators | 1 / 17 / 613 | 1 / 15 / 487 | - |
| `StargateVehicles.SemektetStaffCannonGun.getfirestart` | same-operators | 6 / 3 / 243 | 6 / 3 / 243 | - |

**A cast is not a call, and with the conversions out the disabled bodies' residue is not a base rate.** The reader counts every identifier in front of a parenthesis, so a type conversion came in as a call: **39 of the 400** distinct names the disabled bodies make name a `Class`/`Struct`/`Enum` export or a built-in constructor and are no function, and the same filter takes **1272 of 11416** out of the control. What is left is **361** names: **88** declared by the body's own class, **238** by another class of the corpus and **35** by nothing at all (**9.7%**), against **1.2%** over every live body of the classes that carry a comment (10025 of 10144 names resolve). The page's previous revision read **18.5%** against **12.2%** and called the difference the live base rate; its control residue was almost entirely conversions -- **1272 of that read's 1391** unresolved control names were casts -- so the pair it quoted was the reader's, not the corpus's.

**The 28 names behind those 35 sites are typed by evidence the text carries.** 9 are declared as a `function`/`event`/`delegate` inside a block comment themselves -- the caller and the callee are both revisions the comment disabled -- 12 are member calls whose receiver's declared type declares no such function, 7 are declared nowhere in the eighteen packages. The member calls are concentrated: **9 of the 12** are called from one body, `StargateGame.TeamAI.setorders`.

**Those names are missing from the build, not only from its export table.** Each of the 28 is read against three further vocabularies -- the `state` blocks the class texts declare, the labels at a state body's own statement level, and every package's own name table, which the compiler fills with every name it saw: **0** is a state, **1** is a state label (`docharge`, the entry label of `melleecharge` in `StargateContent.JaffaBotHeavy`), **3** only a name in a package's table and **24** in none of the three. The base rates are what make the last column a finding rather than a reader artefact: **14 of the 313** state names and **7 of the 63** state labels the corpus declares are also `Function` exports, so a name this build ships is normally visible in the table, and an API that had merely been renamed would leave its names there.

That is the whole answer for the AI override API's **names**: `StargateGame.TeamAI.setorders` calls **9** names on a `BotEnemy` receiver and the build carries **1** of them -- `docharge`, the entry label of `melleecharge` in `StargateContent.JaffaBotHeavy` -- while the remaining **8** sit in no name table of the eighteen packages, so no object of any package ever referenced them. A call site cannot reach a label, and the other eight were never compiled: the revision is written against a spelling this build does not use.

**A name is not a behaviour, and most of the behaviours are still here.** Each commented name's own words -- `DoRangedAttack` carries `ranged`, `DeActivateStateOveride` carries `overide`; the verb prefixes and the generic `state` are not words a declaration is distinctive by -- are matched, as words, against every `Function` the eighteen packages export: **24 of the 28** residue names and **7 of the 9** `BotEnemy` ones share one with a declaration the build carries. A word more than 25 declarations carry is not counted as evidence, so a name whose words are all that common reads 0 (`activateforcemelee`, `deactivateforcemelee`, words the corpus uses everywhere). The matches are the build's own spellings of the same vocabulary (`shouldsquadoverideai` beside the commented `activatestateoveride`; `shouldsquadoverideai` beside the commented `deactivatestateoveride`; `pickretreatdestination` beside the commented `doretreat`; `fearthisspot` beside the commented `activatefearoveride`) -- a naming correspondence, not an identity: a low count says where to look, and only the bodies can say the two do the same thing.

| commented name | its words | declarations of this build that share one |
|---|---|---:|
| `attachdrivertoseat` | attach driver seat | `adjustdriverdamage`, `animnotify_driveralighted`, `animnotify_driverignition`, `attach` (31) |
| `staticview` | static view | `addprecachestaticmesh`, `fillprecachestaticmeshesarray`, `getstaticbigfont`, `getstatichugefont` (21) |
| `setfreelancescriptfor` | freelance script for | `abortscript`, `clearscript`, `continueformationscript`, `docombatscript` (20) |
| `getpriorityattackobjective` | priority attack objective | `addobjective`, `addvisibleobjective`, `armorpriority`, `assessenemypriority` (17) |
| `getpriorityfreelanceobjective` | priority freelance objective | `addobjective`, `addvisibleobjective`, `armorpriority`, `assessenemypriority` (17) |
| `mergewith` | merge with | `addsquadwithleader`, `buildobjectwithproperties`, `comparewithuser`, `connectwithpassword` (15) |
| `docharge` | charge | `animnotify_melleecharge_hold`, `cancharge`, `canmellecharge`, `charge` (14) |
| `getleastdefendedobjective` | least defended objective | `addobjective`, `addvisibleobjective`, `botnearobjective`, `disablenextobjective` (10) |
| `netconnection` | net connection | `connectionfailed`, `displaybadconnectionalert`, `ispendingconnection`, `neednetnotify` (10) |
| `setobjective` | objective | `addobjective`, `addvisibleobjective`, `botnearobjective`, `disablenextobjective` (10) |
| `viewport` | viewport | `addchildviewport`, `findviewportindex`, `getviewport`, `getviewportcount` (10) |
| `utpasswordcw` | password | `connectwithpassword`, `findchannelpassword`, `getpassword`, `requirespassword` (9) |
| `utpasswordwindow` | password window | `connectwithpassword`, `findchannelpassword`, `getpassword`, `requirespassword` (9) |
| `dorangedattack` | ranged attack | `doranged`, `dovehicleranged`, `rangedattacktime`, `recommendlongrangedattack` (5) |
| `putondefense` | put defense | `adddefensepoint`, `ownsdefensescript`, `putbotonsquadledby`, `putdown` (5) |
| `activatefearoveride` | fear overide | `fearthisspot`, `shouldrunawayfromfearspot`, `shouldsquadoverideai`, `updatefearspotstatus` (4) |
| `deactivatefearoveride` | fear overide | `fearthisspot`, `shouldrunawayfromfearspot`, `shouldsquadoverideai`, `updatefearspotstatus` (4) |
| `format` | format | `formatquat`, `formattime`, `formattimedisplay` (3) |
| `datetimetomap` | date time map | `fulltimedate`, `getprettydate` (2) |
| `putonfreelance` | put freelance | `putbotonsquadledby`, `putdown` (2) |
| `putonoffense` | put offense | `putbotonsquadledby`, `putdown` (2) |
| `activatestateoveride` | overide | `shouldsquadoverideai` (1) |
| `deactivatestateoveride` | overide | `shouldsquadoverideai` (1) |
| `doretreat` | retreat | `pickretreatdestination` (1) |
| `activateforcemelee` | force melee | - (0) |
| `deactivateforcemelee` | force melee | - (0) |
| `screenprint` | screenprint | - (0) |
| `setupkeybindings` | setup key bindings | - (0) |

| unresolved call name | sites | kind | called from | in the build | where | evidence |
|---|---:|---|---|---|---|---|
| `getpriorityattackobjective` | 3 | commented-declaration | `StargateGame.TeamAI.findnewobjectivefor` | absent | - | declared inside a block comment at StargateGame.teamai |
| `datetimetomap` | 2 | commented-declaration | `Core.Locale.datetostring` | absent | - | declared inside a block comment at Core.locale |
| `format` | 2 | member-of | `Core.Locale.datetostring` | name-only | Engine,UWindow | called on a `string` receiver and no class declares it |
| `getleastdefendedobjective` | 2 | commented-declaration | `StargateGame.TeamAI.findnewobjectivefor` | absent | - | declared inside a block comment at StargateGame.teamai |
| `getpriorityfreelanceobjective` | 2 | commented-declaration | `StargateGame.TeamAI.findnewobjectivefor` | absent | - | declared inside a block comment at StargateGame.teamai |
| `viewport` | 2 | unresolved | `Engine.Pawn.isfirstperson` | name-only | AmbientCreatures,Engine,GUI,GamePlay,UWindow | no declaration anywhere in the corpus's text |
| `activatefearoveride` | 1 | member-of | `StargateGame.TeamAI.setorders` | absent | - | called on a `BotEnemy` receiver and no class declares it |
| `activateforcemelee` | 1 | member-of | `StargateGame.TeamAI.setorders` | absent | - | called on a `BotEnemy` receiver and no class declares it |
| `activatestateoveride` | 1 | member-of | `StargateGame.TeamAI.setorders` | absent | - | called on a `BotEnemy` receiver and no class declares it |
| `attachdrivertoseat` | 1 | commented-declaration | `StargateVehicles.StargateHover.attachdrivertoseat` | absent | - | declared inside a block comment at StargateVehicles.stargatehover |
| `deactivatefearoveride` | 1 | member-of | `StargateGame.TeamAI.setorders` | absent | - | called on a `BotEnemy` receiver and no class declares it |
| `deactivateforcemelee` | 1 | member-of | `StargateGame.TeamAI.setorders` | absent | - | called on a `BotEnemy` receiver and no class declares it |
| `deactivatestateoveride` | 1 | member-of | `StargateGame.TeamAI.setorders` | absent | - | called on a `BotEnemy` receiver and no class declares it |
| `docharge` | 1 | member-of | `StargateGame.TeamAI.setorders` | state-label | StargateContent.JaffaBotHeavy.melleecharge | called on a `BotEnemy` receiver and no class declares it |
| `dorangedattack` | 1 | member-of | `StargateGame.TeamAI.setorders` | absent | - | called on a `BotEnemy` receiver and no class declares it |
| `doretreat` | 1 | member-of | `StargateGame.TeamAI.setorders` | absent | - | called on a `BotEnemy` receiver and no class declares it |
| `mergewith` | 1 | member-of | `StargateGame.TeamAI.findnewobjectivefor` | absent | - | called on a `SquadAIBasic` receiver and no class declares it |
| `netconnection` | 1 | unresolved | `Engine.PlayerController.restartlevel` | name-only | Engine,StargateGame | no declaration anywhere in the corpus's text |
| `putondefense` | 1 | commented-declaration | `StargateGame.TeamAI.setorders` | absent | - | declared inside a block comment at StargateGame.teamai |
| `putonfreelance` | 1 | commented-declaration | `StargateGame.TeamAI.setorders` | absent | - | declared inside a block comment at StargateGame.teamai |
| `putonoffense` | 1 | commented-declaration | `StargateGame.TeamAI.setorders` | absent | - | declared inside a block comment at StargateGame.teamai |
| `screenprint` | 1 | unresolved | `Engine.Screenplay.tick` | absent | - | no declaration anywhere in the corpus's text |
| `setfreelancescriptfor` | 1 | unresolved | `StargateGame.BotSG.findroamdest` | absent | - | no declaration anywhere in the corpus's text |
| `setobjective` | 1 | member-of | `StargateGame.TeamAI.findnewobjectivefor` | absent | - | called on a `SquadAIBasic` receiver and no class declares it |
| `setupkeybindings` | 1 | commented-declaration | `Engine.PlayerController.setcurrentprofile` | absent | - | declared inside a block comment at Engine.playercontroller, under #ifdef |
| `staticview` | 1 | unresolved | `StargateGame.StargatePlayer.changeaim` | absent | - | no declaration anywhere in the corpus's text |
| `utpasswordcw` | 1 | unresolved | `GamePlay.TournamentConsole.connectfailure` | absent | - | no declaration anywhere in the corpus's text |
| `utpasswordwindow` | 1 | unresolved | `GamePlay.TournamentConsole.connectfailure` | absent | - | no declaration anywhere in the corpus's text |


**The operator family is a declaration kind the rule could not name, and it is bodies, not a residue.** An operator declares its name as a symbol (`operator(24) bool == ( .. )`) and writes its priority in parentheses where a parameter list would start, so the grammar above read none of them: **2** operator-family declarations over the eighteen packages, **2** of them carrying a body (396 bytes over the population) and **0** declaration-only, beside **97** `native` operators, which are `tools/native-operators.py`'s population and never a body. A `break` inside one of the bodies was counted outside every declaration before this rule -- `inventory/break_sites.csv`'s `outside_body` column, not a state's label code. A body's row is keyed by the name the build gives it, the composed record name (`==` over two `MapItem` operands is `EqualEqual_MapItemMapItem`), which **2** of them need, because every other census keys a function by its export name and the symbol is not one.

| class.operator | declared | bytes | ops | stmts |
|---|---|---|---:|---:|---:|
| `Engine.MaplistRecord.EqualEqual_KeyValuePairKeyValuePair` | `==` | 49 | 3 | 1 |
| `Engine.MaplistRecord.EqualEqual_MapItemMapItem` | `==` | 347 | 10 | 6 |

**The disabled bodies are the weight of the comment, not a residue of it.** 172 commented bodies have no live twin at all: 54105 bytes of source, 534 operators and 574 statements this build never compiled, 20 of them a trailer of 32 bytes or fewer (a declaration a class kept while emptying its body, the shape the pairs show from the other side). Those are the classes and functions `tools/class-text-comments.py` counts by declaration; this is the same population by size, so a cut-content read can rank it instead of counting it.

The largest of the 172 disabled bodies (`ops` / `stmts` at the body's own brace depth, the artifact carrying all of them):

| class.function | bytes | ops | stmts |
|---|---:|---:|---:|
| `StargateGame.TeamAI.setorders` | 2669 | 9 | 8 |
| `StargateMenus.MainMenu.buttonclick` | 1482 | 2 | 3 |
| `StargateGame.BotSG.findroamdest` | 1386 | 17 | 14 |
| `StargateGame.StargatePawn.endduststormanims` | 1350 | 1 | 0 |
| `StargateVehicles.SemektetStaffCannonGun.tick` | 1318 | 10 | 7 |
| `StargateGame.StargatePawn.startduststormanims` | 1294 | 1 | 0 |
| `StargateGame.StargatePawn.getweaponclass` | 1273 | 4 | 4 |
| `StargateGame.StargateAIMsgManager.checkmsgtype` | 1241 | 26 | 27 |

Census: 2319 classes read, 252 with a block comment, 198 commented bodies.
<!-- /generated:class-text-bodies -->

## The operator family is a declaration kind, not a residue

An operator declaration names its function with a **symbol** and writes its priority in parentheses where a
parameter list would start: `static final operator(24) bool == ( MapItem A, MapItem B )`, `native(129) static final
preoperator bool ! ( bool A )`. Both parts defeated the grammar's rule, which expects an identifier in the name's slot
and a `(` after the return type, so the rule saw **no declaration at all** -- not a declaration whose body it could
not reach. That is why one `break` statement inside `Engine.MaplistRecord`'s `==` body was counted outside every
declaration (`inventory/break_sites.csv`'s `outside_body` column, `docs/BYTECODE.md`).

Read over all 2,319 class texts, **114** lines spell an operator and this rule now names the ones a class *writes*:
**2**, both `Engine.MaplistRecord`'s `==` overloads, and both carry a body (**49** and **347** bytes, **3** / **10**
operators, **1** / **6** statements). The other **97** are `native` operators -- `Core.Object`'s 91-row table
(`==`, `**`, `>>>`, the `+=` family, the `vector`/`rotator`/`color` specialisations), `Engine.Actor`'s four `color`
operators and `Engine.StreamBase`'s two string ones -- which are `tools/native-operators.py`'s population and have a
`;` instead of a body, and which this reader deliberately does not count as functions (`native` is absent from its
modifier list, so the natives census owns them). The remaining **15** lines spell the word inside a block comment --
**14** of them in `Core.Object`'s own banner and **1** prose line in `StargateGame.GoauldConsoleTrigger` -- and this
reader masks comments before it looks, which is why the 114 lines answer 99 declarations and not 114.

**One rule, five artifacts.** The vocabulary is `tools/native-operators.py`'s -- its `DECL` already parsed
`operator(24)` and a symbol name, and its `--selftest` holds the hard spellings -- and every text census in the tree
calls `class-text-bodies.py`'s `FUNC`, so the correction moves all of them together and each delta is a number rather
than a claim:

| artifact | before | after | what moved |
|---|---:|---:|---|
| `inventory/class_text_bodies.csv` `live_declarations` | 5925 | **5927** | the control's denominator, over the comment-carrying classes |
| `inventory/class_text_bodies.csv` `live_with_body` | 5316 | **5318** | both operator bodies are body-resolved, so the rate is unchanged at 89.7% |
| `inventory/class_text_comments.csv` `live_functions` | 5925 | **5927** | the comment census's control |
| `inventory/text_stubs.csv` `declarations` / `bodies` | 11411 / 10505 | **11413 / 10507** | stubs stay **906**: neither operator declaration is a stub |
| `inventory/text_branches.csv` `text_functions` | 11523 | **11525** | and the `agree` class split 50 fewer / 732 exact -> **49 / 733** |
| `inventory/break_sites.csv` `outside every declaration` | 4 | **3** | the site is a `break` inside `MaplistRecord.==`, and 985 in-body -> **986** |
| `inventory/text_branch_functions.csv` `text_only` | 34 | **35** | the operator body joins as text-only -- see below |

**The class-set verdict is the one that was a reader artifact.** `Engine.MaplistRecord` was one of the **50** classes
that "declare fewer functions than their record has" in `docs/BYTECODE.md`; it declares exactly as many now, so that
population is **49** and the exact-match one **733**, and a count disagreement this page has carried since the census
was written was the reader, not the corpus.

**What the operator row still cannot do is join the stream, and the build says why.** `Engine` exports the same two
functions as `EqualEqual_KeyValuePairKeyValuePair` (69-byte payload) and `EqualEqual_MapItemMapItem` (182) -- the
composed names `tools/native-operators.py`'s `compose()` builds from the symbol and its operand types, and the names
`inventory/native_operators.csv` already carries for the `native` half. A name-keyed join therefore reads the
operator body as `no-record` and one more text-only function; composing the text declaration's name from its own
operand types is the route, and it is U23's.

## A declaration without a body

`tools/class-text-bodies.py` reads a declaration's body with a brace walk, and the walk answering `None` is not
one thing. The grammar lets a class declare a function and never write it -- UE2's own `Engine.Actor` does that
for the events native code calls -- and the exported `ScriptText` can also lose the body while keeping the
declaration, or stop in the middle of one. The other side of the question is what the build did with the
declaration, and every declaration has an export: a bodyless declaration is a `Function` object like any other,
so its payload says whether the compiler also thought there was nothing to compile.

`tools/text-stubs.py` asks both, per declaration, over the same 18 packages: the window between a declaration
and the next one gives the shape, the declaration's **scope** decides which record answers it -- a `Function`'s
Outer is a `State` as often as a `Class`, and the compiler binds a declaration written inside a `state` block to
that state's own `Function` record, so asking the class for it credits the state with the class's script -- and
the payload's own length field gives the compiled script. The empty script is seven bytes before the trailer
(`03 00 00 00 04 0b 47` -- a `Return` with no operand), so a stub that carries it is a declaration the build
agreed about, and a stub that carries anything longer is a body the package kept. The tool also joins the bodies
`tools/class-text-bodies.py` measured inside block comments by `(package, class, name)`, which is the only way
to say whether a body the text does not spell *moved into a comment* rather than being dropped. The artifact is
`inventory/text_stubs.csv` (one row per class, one per stub, and the census), and `make text-stubs` regenerates
it and this block.

<!-- generated:text-stubs -->
**Of the 11,413 declarations the grammar's rule reads, 10,507 carry a body the brace walk resolves and 906 are declaration-only** (7.9%) -- and every one of them has an export, because a declaration is a `Function` object whether or not it has statements. Read against that export, a stub is one of two things: **903 carry the compiler's empty script** (`04 0b 47`, `Return` and nothing else), so the text and the build agree there is no body, and **3 carry a longer script**, a body the package kept. Of those **2** are forward declarations -- the same text declares the name again, with a body, further down -- and **0** are the mirror case, a body the class spells *above* the declaration (a bodyless re-declaration of a name the class already defines), so the residue is **1 declaration** whose script no live body in the `ScriptText` spells**. The control is what keeps that from being a stub test: **370 of the 10,507 bodied declarations compile to the same empty script too** -- **369** an empty `{}` body and **1** a body carrying only locals, comments or structure -- so the test separates *no code* from *code* and not a stub from a body. 0 of the 369 empty-`{}` bodies carry a longer payload than it.

| shape | stubs | what it is |
|---|---:|---|
| `;` before any `{` | 903 | a bodyless declaration -- the parameter list may sit on the next line |
| no `{` in the window | 2 | the body sits after an `#endif`, outside the declaration's region |
| braces never close | 1 | the exported `ScriptText` ends inside a body |

| owner | empty script | compiled body |
|---|---:|---:|
| the class itself | 649 | 3 |
| a `State` of the class | 254 | 0 |

**Every stub's name is a record the class binds**: **652 name a `Function` the class itself exports** and **254 one a `State` of the class exports** -- the names a class-over-state reading left unowned were state members whose block a plain-header reader missed, which is the state-scope paragraph below. **2** are declared a second time in the same text with a body, **37** sit in a window that holds a block comment and **0** carry `FUNC_Native` -- so the population is a language shape and not a reader's miss. 647 distinct names are behind the 906 stubs.

| package | stubs |
|---|---:|
| `Engine` | 428 |
| `StargateGame` | 164 |
| `StargateContent` | 133 |
| `GUI` | 78 |
| `UWindow` | 38 |
| `StargateVehicles` | 22 |
| `IpDrv` | 18 |
| `GamePlay` | 15 |
| `AmbientCreatures` | 3 |
| `Core` | 3 |
| `Editor` | 2 |
| `StargateMenus` | 2 |

The concentration is a handful of classes, not a spread: **15 classes are all stubs** and **10 hold at least half**. The eight with the most:

| class | stubs / declarations | empty | bodied |
|---|---:|---:|---:|
| `Engine.actor` | 71 / 111 | 71 | 0 |
| `Engine.controller` | 49 / 136 | 49 | 0 |
| `Engine.pawn` | 46 / 212 | 46 | 0 |
| `StargateGame.botenemy` | 38 / 403 | 38 | 0 |
| `UWindow.uwindowlookandfeel` | 37 / 38 | 37 | 0 |
| `GUI.guicomponent` | 30 / 104 | 30 | 0 |
| `StargateGame.stargateplayer` | 28 / 214 | 28 | 0 |
| `Engine.gameinfo` | 22 / 162 | 22 | 0 |

**A `state`'s declaration belongs to the state.** **254 of the 906 stubs are written inside a `state` block**, and each one resolves to that state's own `Function` export rather than the class's: **254** of them compile to the empty script and **0** carry one, against **649** empty and **3** bodied at the class's own level. Asking the class for the name -- a class-over-state preference -- credits a state's bodyless declaration with the class's script, which is how a census can read a body the text does not spell where the text spells nothing missing at all.

**A bodied declaration that names no export is a scope question, and the header's spelling answers most of it.** 38 of the 10,507 bodied declarations resolve to nothing against their class, and **1,695** of them are written inside a `state` block: a state's `Function` record is outered to the `UState`, not to the class, so a class-keyed walk cannot answer the name, and the header pattern this module carried (`state Name {`) missed four of the five spellings the shipped text uses -- `state() Name` (every state in `Engine.Mover`), `state(0) Name`, `simulated state X extends Y` and a capitalised `State X`: over the whole corpus the plain header finds **424** of the **554** blocks the class texts declare. Read through `class-text-bodies.state_blocks` instead, **1,695** bodied declarations are placed in a state and **8,812** are not; of the residue, **38** are written inside a preprocessor block this build does not compile (`pc` and `ps2` guard whole functions, **5** of those inside a state) and **0** sit in a live region. Those rows are named below -- the count is the artifact's, not a reader's claim.

| declaration | line | scope | why no export |
|---|---:|---|---|
| `Engine.ismoviepaused` | 1,060 | class | inside `ifdef pc` |
| `Engine.ismovieplaying` | 1,047 | class | inside `ifdef pc` |
| `Engine.pausemovie` | 1,053 | class | inside `ifdef pc` |
| `Engine.playmoviedirect` | 1,000 | class | inside `ifdef pc` |
| `Engine.playmoviescaled` | 1,010 | class | inside `ifdef pc` |
| `Engine.stopmovie` | 1,072 | class | inside `ifdef pc` |
| `Engine.gsif` | 8,748 | class | inside `ifdef pc` |
| `Engine.initgsinterface` | 8,742 | class | inside `ifdef pc` |
| `Engine.pausem` | 8,804 | class | inside `ifdef pc` |
| `Engine.playm` | 8,770 | class | inside `ifdef pc` |
| `Engine.stopm` | 8,809 | class | inside `ifdef pc` |
| `Engine.setcontrolstyleindex` | 424 | class | inside `ifdef ps2` |
| `Engine.setviewsensitivityindex` | 406 | class | inside `ifdef ps2` |
| `Engine.postbeginplay` | 371 | class | inside `ifdef ps2` |
| `Engine.sethighestswichpriority` | 463 | class | inside `ifdef ps2` |
| `StargateContent.gettargetfov` | 56 | class | inside `ifdef pc` |
| `StargateContent.playfiring` | 189 | class | inside `ifdef ps2` |
| `StargateContent.altfire` | 738 | `idle` | inside `ifdef ps2` |
| `StargateContent.fire` | 646 | `idle` | inside `ifdef ps2` |
| `StargateContent.forcereload` | 840 | `idle` | inside `ifdef ps2` |
| `StargateContent.getbloom` | 414 | class | inside `ifdef ps2` |
| `StargateContent.renderoverlays` | 442 | class | inside `ifdef ps2` |
| `StargateContent.setbloom` | 401 | class | inside `ifdef ps2` |
| `StargateContent.setfog` | 429 | class | inside `ifdef ps2` |
| `StargateContent.zoomin` | 847 | `idle` | inside `ifdef ps2` |
| `StargateContent.zoomtoggle` | 614 | `idle` | inside `ifdef ps2` |
| `StargateGame.resolvename` | 1,689 | class | inside `ifdef ps2` |
| `StargateGame.createdofoverlay` | 598 | class | inside `ifdef pc` |
| `StargateGame.debugdof` | 611 | class | inside `ifdef pc` |
| `StargateGame.setdist` | 626 | class | inside `ifdef pc` |
| `StargateGame.setfar` | 621 | class | inside `ifdef pc` |
| `StargateGame.setnear` | 616 | class | inside `ifdef pc` |
| `StargateGame.setradius` | 632 | class | inside `ifdef pc` |
| `StargateGame.showmovie` | 364 | class | inside `ifdef pc` |
| `StargateGame.toggledof` | 638 | class | inside `ifdef pc` |
| `StargateGame.createfirstpersonhands` | 325 | class | inside `ifdef ps2` |
| `StargateGame.setuphandanimlinks` | 342 | class | inside `ifdef ps2` |
| `StargateGame.beginsubaction` | 47 | class | inside `ifdef pc` |

**The residue is not in the class's comment.** `inventory/class_text_bodies.csv` already measures the bodies a block comment disables, and joining that artifact by `(package, class, function)` against them puts **0** in a class whose own comment carries a body of the same name -- a body the text moved into a comment rather than dropped -- **0** in a class that ships a block comment without this name, and **1** in a class that ships no comment at all, so **1 of the 3** bodied stubs is a body the `ScriptText` does not spell anywhere. The name-level rate is the control: **23 of the 647** distinct stub names also occur as a commented body somewhere in the corpus (3.6%), against **0 of 1** (0.0%) for the lost bodies' own names -- the join is not finding less than a plain name census would.

**The declaration lookups stay inside their own scope.** A forward declaration is one the same text declares again *below* with a body (**2** bodied stubs) and the mirror case is a body *above* (**0**); both are counted on the same scope's declarations only, so a class-level declaration is never answered with a state's body or the reverse.

**A repeated payload size is not a repeated script.** The 3 bodied stubs carry **3** distinct payloads and **0** rows share one, and those are one name declared twice in a class (`StargatePlayer.fire`, `StargateWeapon.fire`) -- equal sizes across *different* names are distinct scripts, not one lost body counted several times.

The stubs whose payload carries code are the ones where the text and the build disagree, and almost every one of them is a *shape*: 3 sit in an `#endif` window or a truncated text, 2 re-declare a name the class's text spells a body for (2 forward, 12 backward) and the remainder is the one body the text does not spell. The largest:

| function | payload | owner |
|---|---:|---|
| `UWindow.uwindowurltextarea.textareacliptext` | 734 | class |
| `StargateGame.stargatepawn.talk` | 498 | class |
| `Engine.interactionmaster.addinteraction` | 314 | class |

<!-- /generated:text-stubs -->

## One state header, five spellings

A class's `ScriptText` writes its states as `state Idle { ... }`, and the corpus does not stop there: `auto state
Idle`, `state(0) Idle`, `state() LeadInOutLooper` (every state in `Engine.Mover`), `simulated state ZoomingIn
extends Zooming`, a capitalised `State FadeOut` and a parent named through another class (`extends Pawn.Waiting`).
Four readers of that header live in `tools/`, and over the same 2,319 class texts they read **424** (`text-stubs`'
own old pattern), **457** (`ai-states`, and with it `ai-state-body` and `state-exports`), **518** (`uscript-dis`)
and **554** blocks. The last is `class-text-bodies.state_blocks`, the one rule the declaration scopes are keyed
on: a declaration written inside a block a reader misses is keyed to the class's own level, where no `Function`
export of that name exists -- a state's record is outered to the `UState` -- so the miss reads as a body with no
export anywhere (UNKNOWNS U24). The test that this rule reads headers rather than statements is the gap: all 554
blocks have their `{` on the header's own line or the next one, while a prefix-only pattern also matched
`stateTime += DeltaTime` and then swallowed the next brace it found.


## Handling: measured here, extracted elsewhere

The text is game-derived. Per `AGENTS.md` and `docs/LEGAL.md` it must not be committed: what is committed is
the **counts and sizes** above. Extraction writes to `$WORK` on the machine that owns the dump, and the
repository only ever cites byte sizes and the tool that reads them.

That is not a technicality — it is the difference between an inventory and a copy of the game's source.

## Not measured

* The text's *completeness* at statement level: it parses as source for all 2,319 classes, and the payload
  test above says the text spells a body for every bodied declaration except one truncated `ScriptText` -- but
  whether those statements are the ones the compiler consumed still needs a compile attempt, and 3.6 measured
  that a stock `ucc` cannot read these packages (`references/compiler-gate.md`).
* Native **indices**: the declarations here say `native function`, not `native(1234)`, so the index still
  lives in the function's record inside the package. The names are now known; the numbers are not.
* Whether the text is byte-identical to what the compiler consumed. The package stores what the build wrote;
  a compiler may have normalised line endings or comments.
* Function bodies. They are decoded now: `tools/uscript-dis.py` walks every scripted payload (11,687 of
  11,687, `docs/BYTECODE.md`), and native-versus-script is separated per record in `inventory/native_reconcile.csv`
  (797 records = 683 matched + 112 an `operator` statement expands into + 2 with no declaration, against 692 live
  declarations = 683 matched + 9 with no record). `docs/systems/UCPP_NATIVES.md` has the native side.
