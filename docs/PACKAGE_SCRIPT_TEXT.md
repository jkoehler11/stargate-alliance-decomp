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
| `function` declarations inside a block comment | **208** |
| of those, naming a function the same class still exports | **124** |
| ... also declared in the same class's live text | **42** |
| ... naming a function another class of the package exports | **29** |
| ... naming no function the package exports at all | **55** |
| live `function` declarations in the same classes / naming an export | **4890** / **4872** |
| `var` declarations inside a block comment | **51** |
| `native function` declarations inside a block comment | **6** |
| `native function` declarations inside a `//` run | **20** |
| native declarations left live | **681** of 707 |
| `defaultproperties` blocks inside a block comment | **2** |
| `defaultproperties` blocks outside every comment | **0** |
| bytes of text / of it inside a block comment | **5881021** / **278024** |

**The class record agrees, and the base rate is why that alone proves nothing.** All **39** body-in-comment classes are `functions=0, properties=0` in `inventory/classes.csv`, and **0** of the 213 classes that keep a live body are -- but **953 of the corpus's 2319** classes record `0/0` anyway, so a record cannot name one. The text can, and the pair of rates is the control: inside the comment population the split is 39 of 39 against 0 of 213.

**The comment is mostly a revision of the class that shipped, and a third of its declarations name a function the build does not carry.** Joined name by name against the package's own exports, **124 of 208** `function` declarations inside a block comment name a function the same class still exports, **29** name a function another class of the same package exports (the declaration moved) and **55** name nothing the package exports at all. The control says the join is not the weak part: the same matcher over the same classes' live declarations places **4872 of 4890 (99.6%)**, against **124 of 208 (59.6%)** for the commented ones. **42** of the placed ones are also declared in the live text, so those classes carry both revisions of the same function. The absent ones split by population: **42** sit in a class that keeps a live body -- classes this build compiled, where the function is simply not there -- and **13** in the 39 classes whose whole body is the comment, where the class itself exports nothing at all. **3** of the absent names still occur somewhere in the live text, but as a mention rather than a declaration, so they stay counted absent.

The 20 classes that keep a live body and still carry a commented declaration the package does not export, `declared` being every `function` declaration in the comment:

| class | declared | placed | moved | absent | functions in the record |
|---|---:|---:|---:|---:|---:|
| `StargateGame.TeamAI` | 10 | 0 | 0 | 10 | 27 |
| `Engine.xAdminGroup` | 8 | 1 | 1 | 6 | 7 |
| `StargateGame.BotSG` | 5 | 1 | 0 | 4 | 93 |
| `StargateVehicles.SemektetStaffCannonGun` | 16 | 9 | 4 | 3 | 5 |
| `Engine.Screenplay` | 17 | 14 | 1 | 2 | 46 |
| `StargateContent.SuperSoldier` | 4 | 0 | 2 | 2 | 24 |
| `StargateGame.BotEnemy` | 4 | 2 | 0 | 2 | 168 |
| `Engine.AIMsgManager` | 2 | 1 | 0 | 1 | 39 |
| `Engine.MapList` | 1 | 0 | 0 | 1 | 11 |
| `Engine.PlayerController` | 6 | 5 | 0 | 1 | 360 |
| `GamePlay.ScriptedController` | 1 | 0 | 0 | 1 | 38 |
| `StargateContent.ExtreniteWeapon` | 1 | 0 | 0 | 1 | 9 |
| `StargateGame.AlarmPanel` | 1 | 0 | 0 | 1 | 8 |
| `StargateGame.StargateAIMsgManager` | 1 | 0 | 0 | 1 | 4 |
| `StargateGame.StargatePawn` | 41 | 40 | 0 | 1 | 218 |
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
| `Engine.xMaterialTrigger` | 94 | 2814 | 2759 | 5 | 0 | - |
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
direction. Three causes are visible in the data, and one is not yet explained:

* **state-scoped functions** are declared inside `state` blocks and are counted by the source reader but not
  always by the inventory's per-class count (the inventory counts states separately: 553 in total);
* **`event` declarations** parse as functions in some classes;
* truncated tails (`GameInfo.uc` and `WarpZoneMarker.uc` do not balance their braces — the extraction stops
  where the text does).

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


<!-- generated:class-text-bodies -->
**181 of the 208 `function` declarations a block comment disables also carry their body inside that comment, and 22 of them have a live twin of the same name in the same class's text.** The reader is not the weak part: over the same classes' live text it finds a body for **4468 of 4890 declarations (91.4%)**, against **87.0%** inside the comments -- so the shortfall is the comment's own residue, not the pattern's. What the 22 pairs say is that a commented revision is **not** the function the build carries: **1** are byte-identical, **5** keep the operator multiset with a different layout, **11** move at least one operator and **5** leave a live body of 32 bytes or fewer behind a substantial comment -- 10413 commented bytes against 7473 live ones over the population.

| measure | value |
|---|---|
| classes with a `ScriptText` / carrying a block comment | **2319** / **252** |
| `function` declarations inside a block comment | **208** |
| ... with a body in the same comment | **181** |
| ... a declaration without a body | **27** |
| live `function` declarations / whose body the reader finds | **4890** / **4468** |
| commented bodies with a live twin of the same name | **22** |
| ... byte-identical | **1** |
| ... same operator multiset, different layout | **5** |
| ... at least one operator moved | **11** |
| ... whose live body is a trailer | **5** |
| bytes, commented revisions / live revisions of the pairs | **10413** / **7473** |
| operators, the same pair | **101** / **89** |
| distinct call names, the pairs' commented / live revisions | **75** / **48** |
| ... naming no `Function` export of the corpus | **2** / **0** |
| commented bodies with no live twin | **158** |
| bytes / operators / statements of those bodies | **49672** / **500** / **531** |
| distinct call names in those bodies | **320** |
| ... declared by the body's own class / elsewhere in the corpus / by nothing | **80** / **208** / **32** |
| ... of which a cast rather than a call (in the earlier revision's count) | **35** of **355** |
| the same read over every live body of the commented classes (control) | **2763** / **5427** / **99** of **8289** |
| ... of which a cast rather than a call (in the earlier revision's count) | **1028** of **9317** |
| unresolved call names, typed by evidence / their call sites | **25** / **32** |
| ... declared inside a block comment / a member call on a type / nowhere / `#ifdef` | **9** / **12** / **4** / **0** |

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
| `StargateVehicles.SemektetMortarGun.animend` | differs | 1 / 2 / 89 | 6 / 3 / 491 | !=,==,|| |
| `StargateVehicles.SemektetStaffCannonGun.animend` | differs | 0 / 1 / 48 | 4 / 0 / 203 | !=,==,|| |
| `StargateVehicles.SemektetStaffCannonGun.projectilefirebarrels` | differs | 4 / 13 / 988 | 0 / 9 / 660 | *,/ |
| `StargateGame.BotEnemy.findlocalcover` | identical | 0 / 1 / 16 | 0 / 1 / 16 | - |
| `Engine.Weapon.fire` | live-stub | 9 / 1 / 496 | 0 / 0 / 2 | +,-,<,==,>,|| |
| `Engine.Weapon.serveraltfire` | live-stub | 0 / 2 / 88 | 0 / 1 / 28 | - |
| `Engine.Weapon.serverfire` | live-stub | 4 / 2 / 481 | 0 / 1 / 25 | -,==,> |
| `StargateContent.BotScarab.timer` | live-stub | 3 / 2 / 221 | 0 / 1 / 28 | !=,&&,== |
| `StargateGame.StargateWeapon.serverfire` | live-stub | 0 / 2 / 48 | 0 / 1 / 25 | - |
| `StargateContent.HaakenBot.beginstate` | same-operators | 0 / 1 / 36 | 0 / 3 / 117 | - |
| `StargateContent.HaakenBot.notifytakehit` | same-operators | 1 / 3 / 224 | 1 / 3 / 165 | - |
| `StargateGame.BotEnemy.needtoturn` | same-operators | 2 / 8 / 234 | 2 / 9 / 275 | - |
| `StargateGame.StargateWeaponEmplacement.updateaimingrotation` | same-operators | 1 / 17 / 613 | 1 / 15 / 487 | - |
| `StargateVehicles.SemektetStaffCannonGun.getfirestart` | same-operators | 6 / 3 / 243 | 6 / 3 / 243 | - |

**A cast is not a call, and with the conversions out the disabled bodies' residue is not a base rate.** The reader counts every identifier in front of a parenthesis, so a type conversion came in as a call: **35 of the 355** distinct names the disabled bodies make name a `Class`/`Struct`/`Enum` export or a built-in constructor and are no function, and the same filter takes **1028 of 9317** out of the control. What is left is **320** names: **80** declared by the body's own class, **208** by another class of the corpus and **32** by nothing at all (**10.0%**), against **1.2%** over every live body of the classes that carry a comment (8190 of 8289 names resolve). The page's previous revision read **18.9%** against **12.1%** and called the difference the live base rate; its control residue was almost entirely conversions -- **1028 of that read's 1127** unresolved control names were casts -- so the pair it quoted was the reader's, not the corpus's.

**The 25 names behind those 32 sites are typed by evidence the text carries.** 9 are declared as a `function`/`event`/`delegate` inside a block comment themselves -- the caller and the callee are both revisions the comment disabled -- 12 are member calls whose receiver's declared type declares no such function, 4 are declared nowhere in the eighteen packages. The member calls are concentrated: **9 of the 12** are called from one body, `StargateGame.TeamAI.setorders`.

| unresolved call name | sites | kind | called from | evidence |
|---|---:|---|---|---|
| `getpriorityattackobjective` | 3 | commented-declaration | `StargateGame.TeamAI.findnewobjectivefor` | declared inside a block comment at StargateGame.teamai |
| `datetimetomap` | 2 | commented-declaration | `Core.Locale.datetostring` | declared inside a block comment at Core.locale |
| `format` | 2 | member-of | `Core.Locale.datetostring` | called on a `string` receiver and no class declares it |
| `getleastdefendedobjective` | 2 | commented-declaration | `StargateGame.TeamAI.findnewobjectivefor` | declared inside a block comment at StargateGame.teamai |
| `getpriorityfreelanceobjective` | 2 | commented-declaration | `StargateGame.TeamAI.findnewobjectivefor` | declared inside a block comment at StargateGame.teamai |
| `viewport` | 2 | unresolved | `Engine.Pawn.isfirstperson` | no declaration anywhere in the corpus's text |
| `activatefearoveride` | 1 | member-of | `StargateGame.TeamAI.setorders` | called on a `BotEnemy` receiver and no class declares it |
| `activateforcemelee` | 1 | member-of | `StargateGame.TeamAI.setorders` | called on a `BotEnemy` receiver and no class declares it |
| `activatestateoveride` | 1 | member-of | `StargateGame.TeamAI.setorders` | called on a `BotEnemy` receiver and no class declares it |
| `attachdrivertoseat` | 1 | commented-declaration | `StargateVehicles.StargateHover.attachdrivertoseat` | declared inside a block comment at StargateVehicles.stargatehover |
| `deactivatefearoveride` | 1 | member-of | `StargateGame.TeamAI.setorders` | called on a `BotEnemy` receiver and no class declares it |
| `deactivateforcemelee` | 1 | member-of | `StargateGame.TeamAI.setorders` | called on a `BotEnemy` receiver and no class declares it |
| `deactivatestateoveride` | 1 | member-of | `StargateGame.TeamAI.setorders` | called on a `BotEnemy` receiver and no class declares it |
| `docharge` | 1 | member-of | `StargateGame.TeamAI.setorders` | called on a `BotEnemy` receiver and no class declares it |
| `dorangedattack` | 1 | member-of | `StargateGame.TeamAI.setorders` | called on a `BotEnemy` receiver and no class declares it |
| `doretreat` | 1 | member-of | `StargateGame.TeamAI.setorders` | called on a `BotEnemy` receiver and no class declares it |
| `mergewith` | 1 | member-of | `StargateGame.TeamAI.findnewobjectivefor` | called on a `SquadAIBasic` receiver and no class declares it |
| `putondefense` | 1 | commented-declaration | `StargateGame.TeamAI.setorders` | declared inside a block comment at StargateGame.teamai |
| `putonfreelance` | 1 | commented-declaration | `StargateGame.TeamAI.setorders` | declared inside a block comment at StargateGame.teamai |
| `putonoffense` | 1 | commented-declaration | `StargateGame.TeamAI.setorders` | declared inside a block comment at StargateGame.teamai |
| `screenprint` | 1 | unresolved | `Engine.Screenplay.tick` | no declaration anywhere in the corpus's text |
| `setfreelancescriptfor` | 1 | unresolved | `StargateGame.BotSG.findroamdest` | no declaration anywhere in the corpus's text |
| `setobjective` | 1 | member-of | `StargateGame.TeamAI.findnewobjectivefor` | called on a `SquadAIBasic` receiver and no class declares it |
| `setupkeybindings` | 1 | commented-declaration | `Engine.PlayerController.setcurrentprofile` | declared inside a block comment at Engine.playercontroller, under #ifdef |
| `staticview` | 1 | unresolved | `StargateGame.StargatePlayer.changeaim` | no declaration anywhere in the corpus's text |


**The disabled bodies are the weight of the comment, not a residue of it.** 158 commented bodies have no live twin at all: 49672 bytes of source, 500 operators and 531 statements this build never compiled, 20 of them a trailer of 32 bytes or fewer (a declaration a class kept while emptying its body, the shape the pairs show from the other side). Those are the classes and functions `tools/class-text-comments.py` counts by declaration; this is the same population by size, so a cut-content read can rank it instead of counting it.

The largest of the 158 disabled bodies (`ops` / `stmts` at the body's own brace depth, the artifact carrying all of them):

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

Census: 2319 classes read, 252 with a block comment, 181 commented bodies.
<!-- /generated:class-text-bodies -->

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
