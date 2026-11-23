# The front end, written here: the lexer is done and every byte is classified

`docs/COMPILER.md` measures why the compile loop cannot be bought: the only public UE2 Runtime (build 2226, three
archived installers all the same build) emits **126 / licensee 0**, this build is **130 / licensee 4**, and pointing it
at these packages stops on `Bad expr token 03` — a grammar difference, so neither a version constant nor a newer download
closes it. `U17`'s second option is therefore the one left: a front end written here.

This page is that project's plan, and its first stage is already measured.

## Stage 1 — the lexer (measured 2026-11-21)

`tools/uscript-lex.py` classifies the shipped source, and its gate is *coverage*: the token spans must tile the input
exactly, with no gap and no overlap, or the tool refuses and names the byte.

| | |
|---|---|
| files | **4,637** |
| bytes | **12,227,040** |
| tokens | **2,686,850** |
| unclassified bytes | **0** |

The distribution, from `inventory/uscript_lex.csv`:

| kind | tokens | share |
|---|---|---|
| space | 917,864 | 34.16% |
| operators and punctuation | 882,268 | 32.84% |
| identifiers | 529,339 | 19.70% |
| keywords | 231,150 | 8.60% |
| comments | 61,456 | 2.29% |
| numbers | 39,780 | 1.48% |
| strings | 12,121 | 0.45% |
| quoted names (`'Actor'`) | 9,492 | 0.35% |
| preprocessor directives | 3,376 | 0.13% |
| backtick macros | **0** | 0% |
| non-ASCII runs | **4** | 0.00% |

Three things that distribution settles for the next stages:

1. **The preprocessor is `#`, and backtick macros do not occur** in this corpus — so the parser needs `#ifdef`/`#else`
   handling (`tools/text-branches.py` already evaluates the guard set) and no macro expander.
2. **Only four files carry a non-ASCII byte**, and all four are the same two files (`GUISectionBackground.uc`,
   `MenuItemOverlay.uc`) reached through two directories, one `©` each. They are prose: those files' shipped text opens
   with a bare class comment whose `//` is not in the buffer, so the prose is in the token stream with no marker.
3. **The corpus is not uniformly marker-clean**, and the lexer is where that has to surface rather than later. A parser
   must refuse `high` in code position instead of guessing — the same rule the rest of this repository follows.

## Stage 2 — the parser (next)

An AST per file, with two gates:

* **round-trip**: rendering the AST back to text, with comments and whitespace re-attached from the token stream, must
  reproduce the input byte for byte on every file whose tokens are marker-clean (4,633 of 4,637 by the count above);
* **declaration agreement**: every class, `var`, `function` and `state` the AST finds must match the shipped package's
  export table for that class — the packages already carry it (`inventory/classes.csv`, `inventory/natives.csv`), so a
  parser that invents or drops a declaration fails against data rather than against opinion.

## Stage 2a — declarations, scored against the packages (measured 2026-11-22)

`tools/uscript-decls.py` parses each file's class header and its `var` / `function` / `event` / `delegate` / `state` /
`struct` / `enum` / `const` declarations, and scores them against `inventory/classes.csv` — a super name and six counts per
class, each side measured independently. **2,317 classes are scored, one row each.** The read is at *name* level: the
parser returns the declared names per kind and the counts are their lengths, so a column and a name list can never
disagree — the two computed separately is how a rename scores as agreement.

| column | agree | share | package rows non-zero |
|---|---|---|---|
| class name | 2,317 / 2,317 | **100.0%** | 2,318 |
| `super` | 2,317 / 2,317 | **100.0%** | 2,317 |
| `var` (properties) | 2,317 | **100.0%** | **1,103** |
| `state` | 2,317 | **100.0%** | 184 |
| `enum` | 2,317 | **100.0%** | 114 |
| `const` | 2,316 | 100.0% | 98 |
| `native function` | 2,313 | **99.8%** | 92 |
| `function` (non-native) | 2,310 | **99.7%** | **1,188** |
| `struct` | 2,230 | *void* | **0** |

### Eight things the scoring settled, and one it retracted

1. **`event` counts as a function.** Counting only `function` gave 82.4% at the time, with the package higher in 794
   classes and lower in 6 — a one-sided gap is what a missing declaration form looks like.
2. **State functions do *not* count.** The follow-up guess was tested and **refuted**: counting them drops agreement
   (82.4% → 79.3%) by overshooting classes that were already right.
3. **UnrealScript is case-insensitive, and the source uses it.** Three costumes, each measured: `super` on case alone
   (`KeyPoint` vs `Keypoint`); a capitalised keyword (`class ClientMover Extends Mover`); and the lexer reading
   `Var config int FearRadius;` as an identifier, which made one class read 5 properties against the table's 10.
4. **A `delegate` is a property.** The table names it `__<name>__delegate`, the source writes `delegate OnClick(...)`.
5. **The build guards must be evaluated, not counted.** This corpus is the Xbox image, so `XBox` and `DEBUG` are defined
   and the `PC`/`PS2` branches were never compiled — the rule `tools/text-branches.py` measured for keywords, with
   case-folded macro names and `#ifdefPS2` read as a whole word. Counting the unbuilt branches over-counted `Actor` by
   **173** functions.
6. **`functions` is the total, with natives inside it**, so the non-native count is `functions − native_functions`.
7. **A native declaration can carry an index and modifiers**: `native(881) final simulated static function …`. Six tokens
   separate the two words, so a check at `i-1` misses them — which is why the native column read 97.5% and the non-native
   column absorbed the difference. Walking back to the statement boundary takes `native function` to **99.8%** and
   `function` to **98.9%**.
8. **Retracted: the `struct` column proves nothing.** Its 96.3% is two zeroes matching — `structs = 0` in **all 2,319**
   rows while `Actor` declares eleven. The comparison is marked void, and the table's non-zero column shows the same
   caution applies to `const` (98), `state` (185) and `enum` (114).

### The residue, named at name level (measured 2026-11-22)

A count comparison cannot see a rename. `var HaakenPodDoor Doors[NUM_DOORS];` read as a property called `NUM_DOORS` has
the same *count* as `Doors`, and `delegate bool OnClick(...)` read as a property called `bool` has the same count as
`__OnClick__delegate` — the old score called those classes agreement. Joined against the package's own export names,
**126 (class, column) pairs disagreed while their counts agreed** (88 `var`, 28 `function`, 9 `state`, 1 `const`).
Seven rules closed all but ten of them, and every one came from reading a sample rather than from adding a rule and
watching an average:

| the text says | what it was read as | cost |
|---|---|---|
| `class` inside a comment whose `//` is not in the buffer: `class is used to indicate the selected menu item` | a class named `is` | 6 classes mis-named; the file's stem is what names the declaration |
| `ACTION_KillInstigator` in the text, `ACTION_Killinstigator` in the table | no join at all | **40** of the table's classes reported as "no source file" |
| `state() LoopMove` — a state with an empty label | no state | 12 of Mover's 19 |
| `out byte State` — a parameter named after a keyword | a state | 19 classes, one each |
| `var(Display) enum EPhysics { … } Physics;` | no enum, and `EPhysics` as the property | 7 of Actor's 23 enums, 35 classes |
| `var HaakenPodDoor Doors[NUM_DOORS];` | a property called `NUM_DOORS` | most of the 88 `var` pairs |
| `delegate bool OnClick(...)` | a property called `bool` | 12 in `GUIComponent` alone |
| `operator(20) color + (color A, color B)` | no function | 111 names: a delegate is *also* a Function in the package, and an operator is a native Function named `Add_ColorColor` |

Two of the eight were not parser bugs but reporting bugs: `BotScarab`'s shipped `ScriptText` carries **no class header at
all** (it opens inside a function comment, so the text is a fragment of the file), and the 47 classes the earlier note
called "no source file in the corpus" were 40 spellings and 6 prose headers — the corpus has a file for every one.

Ten classes still disagree, and they are named here rather than left as a percentage: `Object` (13 native functions the
text does not declare), `MaplistRecord` (2 more operators whose operand types are structs declared in another class),
`Mover` (`detach`, declared in the text and not in the package), `PlayerController`, `StargatePawn` and `UIWidget` (one
function each, same shape), `UPreviewMapListClient` and `UPreviewSkinnedButton` (a `native` declaration the walk-back
does not reach), and `ObjectivesPanel`'s `const COLUMN_INDENT`, which is declared **inside a function body** and hoisted
to the class by the compiler. `inventory/uscript_decls_residue.csv` carries every one of them, with the cause, so the
list is reproducible and a fix has a row to close.

## Stage 2b — signatures, scored against the package records (measured 2026-11-21)

A signature is finer than a count: a name, a return type and an ordered parameter list. `inventory/natives.csv` carries
one per native — **797** rows of class, function and `parameter_count` read from the package's function records, not from
the text — so `tools/uscript-signatures.py` parses every `function`/`event` declaration and joins on (class, function).

**10,147 declarations parsed; 687 join to a package record; the parameter slots agree on 687 of 687 — 100.0%.**

The corpus is **two copies of the same 2,319 files** — the extracted tree holds a flat `<Class>.uc` and a
`<Package>/<Class>.uc` for every class — so the earlier figures here (20,292 declarations, 1,358 joined) walked each
class twice; the populations are half of that, and the join gained 8 rows from stage 2a's class-name fix.

Two facts had to be measured to get there, and both looked like parser bugs until the source was read:

1. **The return value occupies a parameter slot.** The first run agreed on 51.3%, and every miss was *exactly one* low —
   `ShouldBeHidden` read 0 against 1, `Move` 1 against 2. A uniform off-by-one is a modelling error, not a parsing one:
   the package counts the return value as a parameter. Counting slots as `params + (returns ? 1 : 0)` took it to 99.1%.
2. **A parameter can be *named* with a type word.** `StrLen( coerce string String, out float XL, out float YL )` names its
   first parameter `String`, which the lexer calls a **keyword** now that keywords are case-insensitive — so an
   identifiers-only scan dropped that parameter and read two slots instead of three. Reading the last *word* of each
   comma-separated segment, and removing an identifiers-only guard that skipped the segment before it could be named,
   took the last twelve cases to **100.0%**.

The self-test also caught an error of mine: it required an `event` to carry three slots *and* to have no return slot, two
things that cannot both hold. The parser was right — an `event` has no return type, so its slots are its parameters —
and the expectation was corrected rather than the code, which is the only direction that leaves the gate meaningful.

## Stage 2c — call sites, the first expression-level measure (measured 2026-11-21)

`inventory/script_calls.csv` records **19,350** call sites read out of the packages: the class, the function the call sits
in, and the target it resolves to. That is enough to score expression-level parsing without a full grammar —
`tools/uscript-calls.py` parses every function body (19,142 of them) and asks whether the recorded target appears as a
call in that function's source.

**14,428 of 14,442 recorded call targets are found: 99.90%.** Four things had to be measured first, and the first of them
looked like a total failure:

1. **The join is case-insensitive.** Recorded targets are lower-cased and the source keeps its case, so intersecting the
   raw sets matched only names the source happens to spell in lower case (`initialise`) — a correct extractor scored
   **0.6%**. Lower-casing one side took it to 98.2%.
2. **State-block functions are class code.** Six of the misses were self-calls in AI classes (`BotEnemy.checkifshouldcrouch`
   was "missing" its own recursive call) because those functions live inside `state` bodies, which the first pass skipped.
   Parsing them took recall to 99.9% and raised the joined pairs from 13,144 to 14,408.
3. **A call can be named with a keyword.** `Travel("Map")` and `Import(...)` are recorded calls whose names the lexer
   classifies as keywords, so an identifiers-only test never saw them.
4. **An operator is a call the text may never name.** `==` compiles to `EqualEqual_MapItemMapItem`, and `ClockwiseFrom`
   is spelled *as an infix word* — `if ( FireYaw ClockwiseFrom Rotation.Yaw )`. Scoring an operator target on the symbol
   *or* on its spelled name recovered **34** more, to 99.90%.

**The residual is seven, and it is *not* a parser gap — it is the oracle's attribution.** Six are recorded against
`Mover.Trigger`, `Mover.Reset` and `Mover.Untrigger`, and reading those bodies shows what they actually call:
`Trigger` holds `BeginEvent`, `Disable`, `Enable`, `GotoState` and `PlaySound`. The targets that read as missing
(`InterpolateTo`, `SetStoppedPosition`) are **virtual-dispatch resolutions** the package records on that site; `InterpolateTo`
is spelled in `DoOpen`/`DoClose`, not in `Trigger`. A first draft of this section blamed state-scope code, and checking the
body is what refuted it. The seventh is the placeholder row for a file that does not parse.

So the call measure is closed at **99.90%**, and the next piece of stage 2d is statement and expression structure — the
conditions, assignments and operator expressions inside those bodies — not more call extraction.

## Stage 3 — the emitter, first family: one constant return (measured 2026-11-22)

Bytecode out, compared against the shipped function bodies at the tiers `AGENTS.md` defines. `tools/uscript-emit.py`
emits a body from its parsed source and compares the bytes with that function's own script in the package, so this is
the first stage with no recall knob: a body either equals the shipped bytes or it does not, and there is no percentage
to hide a rename behind.

**1,724 bodies are modelled and 1,724 are byte-identical: 0 differ.** The family is the smallest one that is still
real — a body that is one constant return (`return true;`, `return -1;`, `return "";`, `return None;`, `return self;`,
`return vect(0,0,0);`), `return;`, an empty body, or a declaration whose text carries no body at all (**801** of the
1,724). Of the 11,687 shipped bodies that walk cleanly, **11,337 join to a declaration** in the extracted text (the
join's own residue is 80 class-scope names and 270 state names the text does not carry) and **9,613 are refused by
name**: 3,919 with a nested block, 1,429 with two statements, 1,412 whose one statement is not a return, 758 with
three, 445 whose expression is compound. That residue is the statement walk's work, and it is named rather than left
as a percentage.

**All 11,687 bodies end on the same three bytes, `04 0b 47`** (Return Nothing; EndOfScript), and **1,469 are those
three bytes alone**: 801 declarations with no body, 352 whose source body is comments only, 263 state functions whose
text names another state, and **53 whose text declares code the package did not compile** — `Mover.EnableTrigger` is
one statement in the source and the trailer in the package. The stage-2 counts could not see that population, because
both sides agreed on the count. (`Mover.EnableTrigger` is the same collision named below: the class-level declaration
carries no body and the statement is a state function's. 1,096 bodies are now modelled as that stub and the trailer
alone, every one identical.)

Six rules had to be measured before the comparison was exact, and every one came from a body that differed:

| the source | the bytes | what it settled |
|---|---|---|
| `return 0;` in an `int` function | `04 25` | `IntZero` is a token of its own |
| `return 1;` / `return 2;` | `04 26` / `04 2c 02` | `IntOne`, then `IntConstByte` up to 255 |
| `return -1;` / `return 666;` | `04 1d ff ff ff ff` / `04 1d 9a 02 00 00` | everything else is `IntConst` + int32 |
| `return 0;` in a `float` function | `04 1e 00 00 00 00` | the **declared return type** picks the token, not the value: `return 1` in a float function is `1e 00 00 80 3f` |
| `return 0;` in a `byte` function | `04 24 00` | `ByteConst` — and an enum constant is the same token with its ordinal |
| `return "";` / `return 'weapon_bone';` | `04 1f 00` / `04 21 4f 20` | a string is NUL-terminated; a `name` literal is a **name-table index** in the archive's compact form (2,063 → `4f 20`), so the emitter has to write that index and read the package's own name table |

`return None;` is `04 2a`, `return self;` is `04 17`, and `return vect(0,0,0);` is `04 23` + twelve zero bytes.

Two refusals came out of the first run's residue, and both are facts about the build rather than about the parser:

* **A `native` declaration with no body still ships a script**: it relays its parameters — `Object.Clamp` is three
  `29 <index>` `NativeParm` tokens and then the trailer, one per argument. Emitting the plain stub would be wrong, so
  the emitter refuses a native declaration, and the 12 bodies of that shape stay in the residue by name.
* **A class whose extracted text carries two declarations of one name** (an `#if` branch, or a stub beside its body)
  compiled the longer one: preferring the declaration that has a body turned four `Pawn`/`UnrealPawn` bodies from
  differ to identical. That is the measurement, not a rule assumed in advance.

`inventory/uscript_emit.csv` is one row per modelled body — what was emitted, what the package holds, and the verdict
— with a census row that `--check` re-derives from the rows. The gate is dump-free: 19 synthetic bodies, one per
shape plus the two refusals, and the compact-index writer is round-tripped against `uscript-dis.read_index` for the
positive and negative forms, because writing that index wrong would score as a whole family of mismatches.

## Stage 3, continued -- the join closes and two statement families land (measured 2026-11-22)

**2,200 bodies are modelled and 2,196 are byte-identical.** The four that differed were read at the time as "the
text declares a call the package did not compile" (`Weapon.ForceReload` calls `ServerForceReload()`, and the shipped
script for that body is the bare trailer). Measured later, below: the class-level `ForceReload` is a *bodyless
declaration* and the call belongs to a function of the same name inside a state, so the comparison was reading the
wrong declaration.

### The join's residue was 350, and it was three declaration forms the walk did not read

Stage 3's first run left 350 shipped bodies with no declaration to join (80 class-scope + 270 state names). None of
them was a missing source file -- every one was a form the walk did not recognise:

| the form | what it cost | the rule |
|---|---|---|
| `state() LoopMove` -- a state whose label is empty | 194 of the 270 state names, all of them Mover's, Trigger's and KHinge's, because the walk took the `(` for the state's name | step over an empty `()` group and take the name after it (the same form stage 2a measured) |
| `ignores SeePlayer, HearNoise, KilledBy;` | the rest: the compiler emits one bodyless `Function` per ignored name, in the scope that writes the clause | read the clause as a declaration, one stub per name |
| `delegate bool OnClick(Actor Sender);` | the 80 class-scope names, nearly all GUI's `OnDraw`/`OnPress` family: a delegate is one property **and** one Function named after it | read `delegate` as a declaration with no body |

**11,678 of 11,687 walked bodies now join; 9 do not**, and each is named in `inventory/uscript_emit_join.csv`
with its cause rather than left as a rate: five `TeamVoicePack`/`Mover` names the text spells but never declares in
that scope, three `Mover.TriggerToggle` names declared only in another state, and `BotScarab.DoBury`, in the one
class whose shipped `ScriptText` is a fragment with no header.

**The walk's brace depth was a counter that skipped bodies, and it drifted.** A declaration's body was consumed whole
(`i = end + 1`), so the body's braces were never counted; one state later the counter read one level high and every
class-scope function after it was read as *nested* -- `Mover`'s `SetStoppedPosition`, `MoverLooped`,
`UpdatePrecacheStaticMeshes` and `DisplayDebug` were simply absent from a file whose braces balance. The depth is now
precomputed per token, which cannot drift.

### The property index is the record's own index, so nothing had to be rebuilt

`X = value;` compiles to `Let` (`0x0f`) + the *property token* + the record's index + the value, and the index is the
package's own: an export index when the declaring class is in this package (`PlayerProfile.DirtyMask` is export
**1096**), a **negative import index** when the property comes from another package (`InteractionHands.EndState`
writes `fc 07`, -508). A local is the same shape with `LocalVariable` (`0x00`) and the export whose outer is the
function. `bFlag = true;` is `LetBool` (`0x14`), then `BoolVariable` (`0x2d`), then the same property token and index.

The resolver therefore reads names and owners out of the export table and writes the index it finds -- own class,
then the nearest ancestor in the same package, then a property import. That is 76 bodies: 424 bool assignments and
their int/float/byte/object/string siblings, all byte-identical.

### A call's token is decided by its declaration, not by the call site

| the callee | the bytes | why |
|---|---|---|
| a native with a token (`Destroy` 279) | `61 17 16` | `HighNative`: opcode `0x60 + token//256`, operand `token % 256`
| a `final` function (`myOnDeactivate`) | `1c <index> 16` | `FinalFunction` names the Function record itself
| anything else by name (`Died`) | `1b <name index> 16` | `VirtualFunction` names it through the name table
| native with token **0** and not final (`DetachActor`) | the `VirtualFunction` form | there is no opcode to emit, so the call goes by name

The native token and the `Final` flag come from `inventory/natives.csv` (read only); `final` on a *scripted* callee
comes from its own declaration in the text. Two of those four rows cost a run each: `DestroyPlayerStatCache` and
`myOnDeactivate` are scripted and `final`, which no native table can say. 114 one-call bodies are modelled, all
byte-identical.

## Stage 3, continued -- the argument list lands, and one differing body is left (measured 2026-11-22)

**2,819 bodies are modelled and 2,818 are byte-identical: 1 differs**, from 2,510 / 2,417 / 93 as this thread first
read the argument list. A modelled body is one stub (1,096), an empty body (373), one constant return (780), one
assignment (162) or one call (408), and the join is unchanged at 11,678 of 11,687.

| the source | the bytes | what it settled |
|---|---|---|
| a call to a native whose token is `0x70`..`0xFF` (`GotoState('FadeOut')`) | `71 21 <idx> 16` | the token **is** the opcode: one byte, no operand, not a `HighNative` pair (`0x60 + token>>8`). **135** declarers in this build carry such a token -- `71` GotoState, `75` Enable, `76` Disable |
| a `bool` parameter read | `2d 00 <index>` | a parameter is a **local**. The score loop once handed `_scope` the index left by the name-table sweep instead of the body's own export index, so no local was built and every parameter read fell back to the property table (`2d 01` where the package wrote `2d 00`) |
| `Mix(false)` for `Mix(bool Flag, optional float Time)` | `1b 2e 28 16` | an `optional` parameter may be left off: the package writes no placeholder for it, and a call short of a *required* parameter is refused by name |
| `OnChange(Self)`, where `__OnChange__delegate` is a property | `43 33 1a 17 16` | a `delegate` is two records; `DelegateFunction` names the DelegateProperty's export and then the function's name index |
| `return Count;` in an `int` function whose `Count` is a `byte` | `04 39 3a 00 64 09` | a read whose declared type is not the slot's is wrapped in `PrimitiveCast` (`39 <slot>`), the slot read from the image's own cast table (`inventory/cast_operands.csv`) |
| `GetFocusedControl(...)`, `SetFocusedControl(...)` | `39 3d 2c ff` | a numeric literal is typed `int` even at a `byte` parameter: `IntToByte`, then `IntConstByte` -- not `ByteConst` |

### A class-level stub and a state body of the same name are two exports

`Mover` declares `function DisableTrigger();` at class scope and a `Disable('Trigger')` inside a state; `Weapon`
declares `function ForceReload();` and a state function of that name carries the call. The source index filled the
class-level key from *any* declaration of the name and kept the longer body, so the stub was scored against the
state's bytes and read as "differ": 14 shipped bodies. The key now takes class-level declarations only, and a state
body stays under its own state name; a one-file synthetic tree gates both halves.

### The residue's small families were three rules, not new shapes

* `_split_commas` counted a comma inside a nested call as a separator, so `Explode(HitLocation, vect(0,0,0), none)`
  was five arguments to a three-parameter callee: the family (10 bodies) is gone, and the split is shared with
  `params_of` and `enums_of`.
* `return '';` is **name index 0** (`04 21 00`), not a name the package lacks: 8 bodies, all identical.
* a native that an ancestor **in another package** declares is still in this class's chain (`Object` lives in
  `Core.u`): 1 body, `burnProjector.UnTouch`. The 51 that still refuse that way name a **cast** at the call site --
  `AIController(Controller)`, `string(class)`, `GetItemName(string(self))` -- or `assert(...)`, which is the next
  family and not a resolution failure.

### The one body that still differs

`PlayerInput.ChangeSnapView` (`bSnapToLevel = B;`): the package writes six bytes (`19 01 8e 05 00 04`) between the
`LetBool` and the target's `BoolVariable` that the model does not write, and the rest of the body matches byte for
byte. Named, not explained.

## What the statement walk has left

Its own residue, named by count and not by rate -- **superseded** by the statement-walk section below, which
models 731 of these bodies and names what is left by cause: **3,893** bodies with a nested block and **3,690** with two or
more statements (control flow, the next stage), 462 whose one expression is **compound**, 322 whose one statement is
control flow, 283 whose call has a **receiver** (`Pawn.SwitchWeapon(F)`, a `Context` chain), 58 whose assignment
target is a receiver chain or an index (`H.LastPickupTime`, `Skins[0]`), 51 that call a **cast**
(`AIController(Controller)`) or `assert(...)`, 32 names that are neither a local, a property nor an enum constant, 28
assignment targets of that kind, 16 bool assignments whose value is not a literal, 12 native declarations that relay
their parameters, and 7 assignments to a struct property.

## What this project does not need

A stock compiler. The corpus supplies the source, the packages supply the compiled result, and the comparator between
them already exists; the front end is the only missing piece, and the lexer now shows it can start from measured ground
rather than from a guess about the dialect.

## Stage 3, continued -- the cast family and the member node (measured 2026-11-22)

**2,856 bodies are modelled and 2,855 are byte-identical: 1 differs**, from 2,819 / 2,818 / 1. The one that differs is
`PlayerInput.ChangeSnapView`, and its six bytes are now named rather than left as a remainder (below). Seven rules came
out of the bodies that refused before; `inventory/uscript_emit.csv` carries the shape of every one of them.

| the source | the bytes | what it settled |
|---|---|---|
| `return AIController(Controller);` | `2e 59 01 01 09` | `DynamicCast` names the class as a **compact object reference**: 89 is `AIController`'s export in `Engine.u`, `2e 13` is `Vehicle` (export 19), `2e c8 01` is `Material`'s import (-72). Not a name index -- the name table holds `AIController` at 799 |
| `return string(GetValue());` | `39 55 ...` | an explicit cast is `PrimitiveCast` + the slot of its (from, to) pair from `inventory/cast_operands.csv`: FloatToString `55`, StringToInt `4a`, StringToBool `4b`, VectorToRotator `50`, ObjectToString `56`, BoolToInt `41`. A pair the table holds with **0 tokens** has no slot to write and is refused |
| `return string(class);` | `39 56 17` | **bare `class` is `Self`**: `GetItemName(string(class))` and `GetItemName(string(self))` are the same seven bytes (`1b 31 39 56 17 16`) |
| `return Target.Health;` | `19 <obj> 05 00 04 <member>` | `Context` = object, skip word, member's value size, member. The size byte is the **member's** own value size: object/name/class/bool 4, byte 1, vector 12, string 0 |
| `PathName.FullPath` | `36 <member> <struct read>` | `StructMember` names the **field first** and the value after it, the opposite order to `Context` |
| `Recipients.Length;` | `37 <array read>` | `.Length` is the length operator, not a property of any class; 14 bodies |
| `return w.Default.X;` in a `float` function | `39 3f 19 ...` | the conversion wraps the **node**, it is not written inside it |

### The receiver's own class is the join, not the body's

`List.Index` in `GUIComboBox.GetIndex` is `GUIListBase.Index`: `inventory/class_properties.csv` names the class of
every object property, and resolving the member through the *body's* chain wrote export 8263 where the package writes
13. The fallback was removed rather than tuned: two `InitFor` bodies resolve a same-named **own export** where the
package names an **import** (-20), i.e. bytes that look right and are not. A receiver whose class the artifact does not
name (a parameter, a local) is refused by name -- 100 bodies -- instead of joined by the first record of the name.

### The skip word is 5 for an `InstanceVariable` member, and it is not a length

All **17,710** `Context` nodes whose member is an `InstanceVariable` write 5, whatever width that member's index is
written with (`01 18`, `01 69 04` and `2d 01 6c 28` all write 5). It is therefore not the member's byte length, and a
`VirtualFunction` member of one shape ranges 1..6 -- so a call member is refused by name and not given a number the
corpus does not support.

### The six bytes in `PlayerInput.ChangeSnapView` are `Outer.bSnapToLevel`

The package writes `14 | 19 01 8e 05 00 04 2d 01 6c 28 | 2d 00 79 56 | 04 0b 47`: a `Context` whose object is the
imported **`Outer`** property (import 14) wrapped around the BoolVariable target `2d 01 <export 2604>`. Export 2604 is
`bSnapToLevel`, declared on **`PlayerController`**; `PlayerInput` extends `Object` and has no such record on its chain,
so the compiled statement names the property through `Outer` while the shipped text spells it bare
(`bSnapToLevel = B;`). The difference is in the text, not in the walk -- and a bool member's word is not settled (the
corpus writes 6 for that shape), so the emitter refuses that member and the body stays in the residue by name.

### What the walk still refuses, by name

**22 of the 51 cast/`assert` call sites this page named are emitted now** (`call to a name the class chain does not
declare` 51 -> 29), and the 29 are no longer one family: the `assert(...)` bodies, casts whose operand is an operator
expression, 4 bool members, 2 pairs the cast table does not install, 2 casts of a struct. The
`assert` statement (`Actor.Crash` writes `09 26 08 28` -- opcode, **source line 2086**, expression) is its own next
item, because the line number is the only operand the emitter would have to read from the text.

## Stage 3, continued -- the operator node, the assert, and the types a read did not name (measured 2026-11-22)

**2,902 bodies are modelled and 2,901 are byte-identical: the one that differs is still
`PlayerInput.ChangeSnapView`.** 46 bodies were gained and none lost.

| the source | the bytes | what it settled |
|---|---|---|
| `DirtyMask = DirtyMask \| 1;` | `0f 01 48 11 / 39 3d / 9c 39 3a 01 48 11 26 16` | an operator's token comes **first**: `9c` (`And_IntInt`), left, right, `16`; the node is then cast by the slot the assignment wants (`39 3d` IntToByte) and a `byte` operand is promoted (`39 3a` ByteToInt) |
| `return bool(DirtyMask & 2);` | `04 39 3e 9c 39 3a 01 48 11 2c 02 16` | the cast wraps the node and chooses its slot from the operator's **result** type (`IntToBool`), which is the operator declaration's return type |
| `AddVelocity(Momentum / Mass);` | `1b 76 / d6 00 4a 2c 01 e9 07 16 / 16` | `d6` is `Divide_VectorFloat` 214: the pair of operand types, not the symbol, picks the token |
| `assert(false);` | `09 26 08 28` | `Assert`, the **source line as uint16**, the expression; line 2086 of `Actor.uc` is `0x0826`, so the extracted text keeps the compiler's numbering |
| `return PlayerInput.bInvertMouse;` | `04 19 01 49 02 06 00 04 2d 01 49 09` | a bool member writes skip **6** (2,212 of 2,272 nodes) and `2d 01 <index>` as the member |
| `Outer.bSnapToLevel` | `19 01 8e 05 00 04 2d 01 6c 28` | the 60 nodes that write skip **5** are all this shape: the object is a property *import* (`01 8e`, import 14) |
| `return rotator(SquadDir);` | `04 39 50 01 48 21` | `SquadDir` is a `Vector` **property** (`inventory/class_properties.csv`), so the slot is `VectorToRotator` 80, not "struct" |
| `return int(MyNumericEdit.Value);` | `04 39 4a 19 01 69 04 05 00 00 01 4a 07` | the member's type is read through the **receiver's** class (`GUINumericEdit.Value` is a `StringProperty`) |
| `return PathName.Directory;` | `04 36 7e 2e 01 70 27` | the member *node* is chosen by the record kind (`PathName` is a `FilePath` struct -> `StructMember`), not by the type word a cast wants |

Three refusals came out of the residue, each named rather than guessed:

* **an operator on two literals** is folded by the compiler (`Foo(1 + 2)`), so the unfolded node is not written;
* **`&&` / `||` / `^^`** wrap their right operand in `Skip` (`18 <uint16> <expr>`) and the word is not the operand's
  length: `GUIListBase.IsValidIndex` writes `0x0e` for a 6- and a 7-byte operand while their sum is 13, and
  `xAdminGroup.HasPrivilege` writes `0x28` for a nested pair that sums to less. 8 bodies;
* **`@` and `$`** are prefixed by `0e` (`EatString`) -- `ConsoleCommand("LoadSaveGame"@Filename)` is
  `0e 1b 11 a8 ...` and the mark is not modelled. 2 bodies.

The `Context` skip word is now settled for every member kind the walk emits: **5** for an `InstanceVariable` member
(17,710 of 17,710) and for a bool member behind a property import, **6** for a bool member behind an export or a
local. The 4 bool-member bodies this page named are emitted and identical; the member walk reads a property's declared
type from `inventory/class_properties.csv` once per run instead of once per body, which is what kept a corpus walk at
8 minutes and now runs it in 2.

## Stage 3, continued -- the record table the compiler read (measured 2026-11-22)

**2,986 bodies are modelled and 2,986 are byte-identical** -- 45 gained, none lost, and **no body differs** for the
first time (`PlayerInput.ChangeSnapView`, the last one, now reproduces).

The skip word is not a per-shape constant: it is the member's width in the compiler's **uncompacted** code array,
where every compact index occupies four bytes. Measured over the 63 `Context` nodes whose member is a call:

| the member | compact | uncompacted | the word |
|---|---|---|---|
| `1b 0a 16` (`List.ItemCount()`) | 3 | 6 | **6** |
| `1c 72 10 16` (a `final` member, `MyList.GetExtra()`) | 4 | 6 | **6** |
| `1b 61 05 01 05 16` (`P.BotDesireability(Pawn)`) | 6 | 11 | **11** |
| `1b 43 08 00 7f 7e 1e 00000000 16` (`P.BeyondViewDistance(SpawnLocation, 0)`) | 12 | 16 | **16** |

60 of the 63 agree. The same arithmetic gives the property constants this page already recorded: `01 <idx>` is 5
and `2d 01 <idx>` is 6, because an index is four bytes wide before the archive compacts it. Three nodes do not agree
and stay refused by name: a member whose **argument list carries a `Context` of its own**
(`Squad.GetSGFormation().GetLocationFor(...)` writes 18 where the wide length is 24, twice in `BotEnemy`/`BotRep`)
and `UWindowLayoutControl.AddRow` (11 against 9). The one shape where the rule gives the wrong number is
`Outer.<bool>`: it writes 5 where `2d 01 <idx>` accounts for 6, so that word is measured, not derived.

Three record-table rules came out of the same walk:

| the source | the bytes | what it settled |
|---|---|---|
| `bSnapToLevel = B;` (`PlayerInput`, export 2604 on **another** class) | `14 19 01 8e 05 00 04 2d 01 6c 28 2d 00 ...` | a bool the package exports once on a class outside the body's chain is read through **`Outer`** (`01 8e` is import 14); the `Context` wraps the read, the `LetBool` stays outside it |
| `return Default.PawnDamageEffect;` (`Corroded`, `Drowned`) | `04 02 d8 02` | a property the package only **imports** is a record the compiler named, index `-152`: the import table, not the class chain, is the authority. 27 assignments and 11 reads this page had no record for are modelled from it |
| `return Default.DrawColor;` (`LocalMessage.GetColor`, `GetConsoleColor`) | `04 02 44 44` | a **struct** default is the record alone -- no `Context`, no size byte, because the value is copied straight out of the default object |

An off-chain record is only read through `Outer` when it is a **bool**: the 27 non-bool names that land in the same
fallback are read *bare* in this corpus (`GamerTag` writes `0f 01 77 39` -- export 3703 -- not a `Context`), so the
mark is per type, and a non-bool name in that position is refused rather than given a node the package does not write.

## Stage 3, continued -- the receiver's class, and the `Skip` word measured (measured 2026-11-22)

**3,056 bodies are modelled and 3,056 are byte-identical: 0 differ** -- 70 gained, none lost. The thread is the
receiver: a `Context`'s member can be a call, and the callee it names belongs to the *receiver's* class, which the
body's own chain usually cannot name.

| the source | the bytes | what it settled |
|---|---|---|
| `return List.ItemCount();` | `04 19 01 2b 06 00 04 1b 0a 16` | a **call member** is a `Context` like a property member; its word is the call's width in the uncompacted array, and the size byte is the *value* the call yields -- 4 for an object/int/bool, 0 for a string, 12 for a rotator |
| `return AmmoType.HasAmmo();` | `04 19 01 38 06 00 04 1b 41 01 16` | the receiver is a property whose class `Inventory`/`Weapon` is declared in the artifact; the callee resolves on **that** chain (`HasAmmo` is a `Weapon` function, not a `Pawn` one) |
| `return w.GetAIRating();` (`Controller.RateWeapon`) | `04 19 00 56 68 06 00 04 1b 62 14 16` | a receiver that is a **parameter** is typed by its own declaration (`Weapon w`), which is where the class comes from |
| `return ProfileList.ItemCount();` | `1c d9 03 16` | `ItemCount` is an **import** (a negative index) and `final` on the chain's GUI ancestor, so the call is a `FinalFunction` naming the import -- a `VirtualFunction` here is a *different* function of the same name on the body's own class |
| `GetPlayerController(ViewportIndex)` (`coerce string`) | `39 53 00 47 1d` | a `coerce` argument is a `PrimitiveCast` from the image's table (`IntToString` is slot 83); the conversion also sits outside the node |
| `return (Controller != None) && (Controller.bGodMode \|\| Controller.bCheatGodMode);` | `82 ... 18 24 00 ... 16` | the **`Skip` word** is a distance in the uncompacted array: from the right operand's first byte to the end of the enclosing node, so `wide(right) + 1` for the node's own `16` |

### The `Skip` word is measured on every node the corpus carries

`tools/uscript-emit.py` now computes the word instead of refusing it. A probe over all 18 packages read every `Skip`
node in every walked body and compared the shipped word with `wide(parent end) - wide(right operand start)`:
**4,857 of 4,857 agree, 0 differ**. `Pawn.InGodMode` is the worked example: the outer `&&` writes 36 for a 25-byte
right operand whose four indices widen by 10, and the inner `||` writes 16 for 10 bytes plus 5.

The same reading settles the `Context` word for call members: of 134 such nodes, the computed width agrees on every
one whose object is not a property import, and the `Outer` shape (`01 8e`) writes **5 whatever the member is** --
`Outer.Log("...")` is `19 01 8e 05 00 04 1b 0f ...` and `Outer.MaplistCommand(...)` is 5 too. That is a measurement,
not a derivation, so the emitter keeps the constant for that shape.

### The receiver's class is the join, and it has three sources

`_class_of()` resolves a receiver one link at a time from artifacts only: a parameter's or local's declared type
(`Weapon w`), a property's declared type (`class_properties.csv`, and the source's own `var`/`Automated` line when the
package table has no row -- `ProfilePanel` has none for `Automated ProfileList ProfileList`), and a call's declared
return type (`w.GetSGFormation()` is an `SGFormation`, so its `.GetLocationFor(...)` resolves). Falling back to the
body's chain is now **refused**: before that rule, `ProfileList.ItemCount()` was emitted as a `VirtualFunction`
resolved to `ProfilePanel.ItemCount` -- plausible bytes for a different function. Five bodies are refused by name for
this reason and stay in the residue.

### What moved, by reason

| reason | before | after |
|---|---|---|
| a receiver chain whose member is not one name | 69 | **3** |
| a short-circuit operator, whose right operand is a `Skip` node | 13 | **0** |
| a member that is not a property of the receiver's class | 97 | **91** |
| an operator the image's table does not install | 176 | 173 |
| a call member whose receiver's class the artifacts do not name | -- | 5 (new, by name) |

The 70 gained bodies are 43 call members, 14 short-circuits and 13 that only became emittable once their expression
resolved (4 calls, 4 assignments, 2 contexts, 1 bool context, 1 call value, 1 operator). The 3 remaining "member is
not one name" are `class'X'.static.F()`, an array-element receiver and a `default` chain -- each its own next item.

## Stage 3, continued -- the statement walk: a sequence, an `if` and a `while` (measured 2026-11-22)

**4,022 bodies are modelled and 4,008 are byte-identical: 14 differ**, from 3,056 / 3,056 / 0. The 966 gained bodies
were every one of them refused before this change, and no previously scored body lost a byte -- the 3,056 rows that
were identical are still identical, to the byte (`git show <before>:inventory/uscript_emit.csv` against the new file:
0 rows lost, 0 rows moved). The join is unchanged at 11,678 of 11,687 walked bodies.

A body is now a **list of statements**: each statement emits on its own and the body appends its trailer once. 731 of
the modelled bodies carry two or more statements, 574 carry an `if`, 133 open with a `local` declaration and 19 carry
a `while`.

| the source | the bytes | what it settled |
|---|---|---|
| `SetPhysics(PHYS_None); SetTimer(0.2 + FRand(), false); SetPhysics(PHYS_Falling);` | the three calls back to back, then `04 0b 47` **once** | statements concatenate with no separator, and the trailer belongs to the body, not to a statement |
| `local rotator newRotation; newRotation = Rotation; newRotation.Pitch = 0;` | `0f 00 4e01 01a7 0f 36 b9 00 4e01 25 …` | a `local` declaration emits **nothing**: it is a record the package declares, so the first bytes are the first *assignment* and the two-byte `00 4e01` read of the local follows it |
| `if ( c ) S ;` | `07 <end> c S` | `JumpIfNot` names the position just past `S` |
| `if ( c ) S ; else T ;` | `07 <else> c S 06 <end> T` | the `JumpIfNot` names the **else branch's** position and a `Jump` carries the end |
| `while ( c ) S ;` | `07 <end> c S 06 <start>` | the condition sits at the loop's top, the back-`Jump` names the loop's own start, and the `JumpIfNot` names the position **after** that `Jump` |

### A branch operand is an absolute position in the uncompacted array

`Skip` is a *distance* -- from the right operand's first byte to the end of the enclosing node -- but a branch operand
is the **absolute** offset of its target in the compiler's uncompacted code array, measured from the body's first byte.
Three sites agree with that reading and with nothing else:

* `TransientAmbientPawn.PhysicsVolumeChange` writes **59** for an `if` that starts at 0, whose condition is 28 wide and
  whose braced body is 28 more (46 compact + 13 of index widening = 59); read as a distance from the instruction's end
  the same target would be 56.
* `FishBase.PhysicsVolumeChange`'s `if/else` writes **32** for the else branch at compact `0x19` (25 compact + 7
  widening) and **53** for the end of the whole statement at `0x2c`; the `Jump` in between sits at wide 29, so a
  distance reading would be **21**.
* `Object.GetItemName`'s `while` writes **0x47** for the position of the `return` that follows the loop; its back-`Jump`
  is at wide 0x44, i.e. three bytes earlier. The first draft of the rule named the `Jump` itself, and every body whose
  loop was followed by a statement came back three bytes short -- the target is the loop's *end*, not its back edge.

A `for` header is measured and **not** emitted: `for (i = 0; i < n; i++) S;` is the init, a `JumpIfNot` naming the
back-jump's position, the condition, the body, the increment, then a `Jump` to the condition's position.
`Actor.UpdatePrecacheMaterials` writes `06 1300` for a condition at wide 19. The increment compiles to an expression
(`a5`, `AddAdd_Int`) that the statement surface does not model, so `for` (467 bodies), `switch` (173) and `do`/`until`
(8) stay refused by name.

### The 14 that differ were all refused before this change

None of them is caused by the new control flow: every branch operand in all 14 agrees with the shipped bytes, and each
difference is a gap in the *statement* emitter that the single-expression surface never reached.

| the shape | bodies | what the package writes that the walk does not |
|---|---|---|
| an operand the compiler wraps in a conversion | 5: `Canvas.DrawBracket`, `Canvas.MakeColor`, `PlayerController.CompressAccel`, `UWindowHTMLTextArea.ParseColor`, `UWindowWindow.GetMouseXY` | `39 3f` / `39 44` / `39 3a` in front of an operand the walk's type join leaves alone |
| a `Context` skip word that is neither 5 nor 6 | 4: `Scarab.Surfaced` (3), `DMMutator.AlwaysKeep` (11), `StargateWeapon.GetRating` (16), `ScriptedController.NeedToTurn` (11) | the member's own width, measured per shape, not the constant the walk writes |
| a call statement the compiler reaches through `Outer` | 2: `CheatManager.astral`, `CheatManager.puss` | `19 01 8e 05 00 00 <call>` -- the callee is declared on `Outer`'s class and the source spells it bare |
| one member read the walk does not emit after a cast | 2: `GameInfo.InitGameReplicationInfo`, `Inventory.HandlePickupQuery` | one extra `01 9e` |
| a byte literal at a `byte` parameter | 1: `PuzzleTrigger.Trigger` | `24 00` (`ByteConst`) where the walk writes `39 3d 25` (`IntToByte`, `IntZero`) |

The gate carries **64 synthetic bodies**: one per new form, `for` and `do`/`until` as refusals, and `WhileThen` (a loop
followed by a statement), which is the case the first draft of the `while` rule got wrong by three bytes.

## Stage 3, continued -- the loop surface: `for`, `do`/`until` and the four increment opcodes (measured 2026-11-22)

**4,096 bodies are modelled and 4,079 are byte-identical: 17 differ**, from 4,022 / 4,008 / 14. The 74 gained bodies
were every one of them refused before this change, and no previously scored body lost a byte (the 4,008 identical rows
are still identical, and the join stays at 11,678 of 11,687 walked bodies). A statement can now be an *expression*:
`i ++` and `x += y` are statements in their own right, not assignments, and the `for` header's three clauses are
expressions the same emitter writes.

| the source | the bytes | what it settled |
|---|---|---|
| `for (i = 0; i < n; i ++) S;` | init, `07 <end>`, cond, S, step, `06 <top>` | the init comes first, then a `JumpIfNot` whose target is the position **after** the back-jump, the condition, the body, the step, and a `Jump` naming the `JumpIfNot`'s own position |
| `do S until (c);` | S, `07 <body's first byte>`, c | the condition is written *after* the jump that reads it, and the jump names the body's start |
| `i ++` / `++ i` | `a5 <read> 16` / `a3 <read> 16` | postfix and prefix are two natives, not one with a flag |
| `b ++` / `++ b` | `8b <read> 16` / `89 <read> 16` | the `byte` pair, from the operand's declared type |
| `Slot += 2` | `a1 <read> 2c 02 16` | `AddEqual_IntInt` (161) is a different native from `Add_IntInt` (146) |
| `YPos += YL` | `b8 <read> <read> 16` | `AddEqual_FloatFloat` (184), chosen by the declared `(float, float)` pair like any operator |

### The `for` end operand is the position after the back-jump

`Actor.UpdatePrecacheMaterials` writes `07 3700` for a loop whose `06 0700` sits at wide 0x34 and ends at 0x37;
`SGOrder.PostBeginPlay` writes `07 2100` for a `Jump` at 0x1e. Both name the position *past the jump*, which is the
same rule the `while` shape follows (`Object.GetItemName`'s 0x47) -- and reading it as the jump's own position, the
first draft, left **14 bodies exactly three bytes short** (the whole of `AIMsgManager`, `xAdminGroup.ValidName`,
`UWindowEditBox.InsertText`). The back-`Jump` names the `JumpIfNot`'s own position, not the condition's first byte:
`Actor.UpdatePrecacheMaterials` writes `06 0700` for a `JumpIfNot` at wide 7 whose condition starts at 10.

### `do`/`until` is measured, and no corpus body models it end to end yet

`Engine.TestInfo.TestContinueDoUntil` writes `07 1000` for a body that begins at wide 0x10, and
`Engine.StreamBase.RevInStr` writes `07 3d00` for one that begins at 0x3d; in both the condition follows the jump. The
shape emits, but every one of the corpus's 8 `do`/`until` bodies still refuses further in -- `continue` (which compiles
to a `Jump` naming the position after the loop's `JumpIfNot`, and is a statement the walk does not model), a call it
cannot bound, or a member it cannot name -- so the rule is carried by the two shipped disassemblies and the gate's
synthetic body rather than by a scored row. That is the honest state of it: measured, emitted, unexercised.

### The 17 that differ are 14 plus three the loop surface reached for the first time

`Controller.AwardAdrenaline`, `Pawn.Tool_AngleLinearInterpolate` and `UWindowEditBox.InsertText` are the
**conversion on an operand** family the page already names (a `39 3f` / `39 44` the package writes in front of an
operand the walk's type join leaves alone), reached now because their bodies contain a `for` or a `+=`. That family is
5 bodies before this change and 8 after it, and it is the thread's next item -- one rule, and every one of the 17
becomes identical or stays refused. Nothing else differs: no branch operand moved.

`switch` (173 bodies), `continue`/`break` (52) and a statement whose call is a member of a receiver
(`Pawn.SwitchWeapon(F)`, 2,250) are **not in this PR**. The receiver-call statement was written and measured: it
gained 52 bodies and wrote 8 wrong (the skip word and the `EatString` prefix of a *member* call the statement path
does not yet distinguish), so it went back to the residue rather than ship plausible bytes.

The gate carries **71 synthetic bodies**: `ForLoop`, `ForCompound` (a compound assignment for its step), `DoLoop`,
`IncSlot`, `PreIncSlot`, `AddToSlot`, `SubFromSlot` and `IncFloat` (refused: no `float` increment is installed).

## Stage 3, continued -- the value converts at its site, the two spellings of `Class`, and a byte literal's form (measured 2026-11-22)

**4,096 bodies are modelled and 4,093 are byte-identical: 3 differ**, from 4,096 / 4,079 / 17. Fourteen of the
seventeen fixed and no previously scored body lost a byte (the 4,079 identical rows are still identical, and the
walk and the join are unchanged at 11,687 and 11,678). The residue is unchanged at 7,582: every body here was
already modelled, the bytes were wrong.

| the source | the bytes | what it settled |
|---|---|---|
| `Adrenaline = Clamp(Adrenaline, 0, AdrenalineMax);` in a `float` property | `0f <read> 39 3f fb ... 16` | the **call's result** is converted at its own site: `Clamp` returns an `int`, the property is a `float`, so `IntToFloat` wraps the call. The argument conversions (`39 44` on both int parameters) were already right |
| `X = Int(Root.MouseX);` in a `float` local | `39 3f 39 44 19 ...` | a **cast's result** is converted like any other value: `Int(...)` is the inner `FloatToInt`, the site's own `IntToFloat` wraps it |
| `Insert(Asc(Mid(Text, i, 1)));` at a `byte` parameter | `1b 3b 39 3d 5a ...` | an argument that is a **call** is converted by the parameter's type: `Asc` returns `int`, `Insert` takes a `byte`, and the `39 3d` sits around the `Asc`, not around its argument |
| `Min(Abs(C), 127)` | `7e 39 44 <Abs> ...` | a nested call's result is converted for *its* site as well (the `39 44` was missing inside `Min`, not outside it) |

The rule is one sentence: **a node's value is converted to the type its site wants, and only a leaf read was doing
that before.** Six bodies: `Canvas.DrawBracket`, `Controller.AwardAdrenaline`, `Pawn.Tool_AngleLinearInterpolate`,
`PlayerController.CompressAccel`, `UWindowEditBox.InsertText`, `UWindowWindow.GetMouseXY`.

### The `Context` word of a call member is the call's own width, whatever the receiver is

`Pawn.NeedToTurn(targ)` writes `19 01 8d 0b 00 04 1b 59 02 00 48 0a 16` -- the word is `0x0b`, and `_wide` of
`1b 59 02 00 48 0a 16` is 7 compact bytes plus 2 for each of its two widened indices: 11. The page carried a measured
constant (5) for a property **import** receiver, from `Outer.Log("...")`; that shape's call *is* five wide
(`1b 0f <string> 16`, no index to widen), so the constant was the observation, not the rule. Three more bodies agree
with the computed word and nothing else moved: `Scarab.Surfaced.Tick` `0x03` (a `HighNative` with no index),
`DMMutator.AlwaysKeep` `0x0b`, `StargateWeapon.GetRating` `0x10`.

### `class` and `Class` are two nodes, and the spelling is the compiler's input

Lowercase `class` compiles to `Self` -- `Actor.GetItemName`, `Pickup` and `Pawn` all write `1b 31 39 56 17 16` for
`GetItemName(string(class))`. Capital `Class` is the property `Object` declares (`var native const editconst class
Class;`) and compiles to its record: `GameReplicationInfo.GameClass = string(Class);` is `39 56 01 9e`
(`ObjectToString` of the import), and `Item.InventoryType == Class` is `01 9e 16`. Two bodies each way, and the text
the emitter reads is the package's own `ScriptText`, so this is a rule rather than a reconstruction artefact.

### A byte literal's form is decided by its value, not by its slot

`A = 255;` where `A` is a `byte` writes `39 3d 2c ff` -- `IntToByte` and then the **int** constant form -- and
`FindControllerPortIndex(255)` (also a `byte` parameter) writes the same three bytes. Below 128 the form is
`ByteConst` itself: `_SetKeyFrameToPuzzleMovers(0)` is `24 00`, `SetTimer`-style assignments of 1 and 3 are `24 01`
and `24 03`, and ParseColor's `16` is `24 10`. Six sites, one threshold (127), and five bodies that were already
identical -- `PlayerProfile.NewProfile`, `DeathMatch.BeginState`, `SGScreenBlur.BlurView`, `StoryModeGame.BeginState`
and `UWindowURLTextArea.ProcessText` -- are what pinned the low side when the cast rule was tried on them.

### What is left

Three bodies. `CheatManager.astral` and `CheatManager.puss` write a **receiver-less call as a member of `Outer`**:
`DeactivateAstralSight();` compiles to `19 01 8e 05 00 00 1b 5b 15 16` -- a `Context` whose value size is 0 (the call
returns nothing) -- where the walk writes the `VirtualFunction` bare. `UWindowHTMLTextArea.ParseColor`'s
`16 * GetHexDigit(...)` writes the literal as `39 3a 24 10`: the literal adopts its byte neighbour, then is promoted
for the `int` operator, and the byte-returning call beside it is not converted at all. Neither is a site rule, so
neither is in this change.
