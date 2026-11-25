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

## Stage 3, continued -- the call statement through a receiver, the whole parent chain, and two type rules (measured 2026-11-23)

**5,057 bodies are modelled and all 5,057 are byte-identical**, from 4,096 / 4,093 / 3. No previously scored body
lost a byte: the walk and the join are unchanged at 11,687 and 11,678, every one of the 4,093 identical rows is still
identical, and the residue falls 6,742 -> **6,621** of 11,687.

**The artifact's contract is now enforced by the tool, not by this page.** A body whose emitted bytes differ from the
shipped ones is *refused* -- reason `an emitted body that differs from the shipped bytes (n vs m)`, with both hex
strings in the residue row -- instead of being written as a differing row: a family here is built until it matches, so
a diff is a rule that is wrong for that shape, and `inventory/uscript_emit.csv` must never carry a body the next run
would read as evidence. `--check` holds `modelled` to the row count and fails on any `differ` row. Two flags make the
loop cheap: `--residue-out` writes `theirs` and `ours` beside every refusal, and `--only Package.u` scores one package
in seconds (its artifact is partial and must not be committed).

| the source | the bytes | what it settled |
|---|---|---|
| `Pawn.SwitchWeapon(F);` | `19 01 05 0b 00 00 1b 61 03 00 4b 6e 16` | a **call statement through a receiver** is a `Context` whose skip word is the call's **own width** in the uncompacted array (`_wide`, 11 here), not a constant |
| `Outer.Log("...")`, `CheatManager.astral` | `19 01 8e 05 00 00 ...` | the 5 belongs to a **property import** receiver (`Outer`), whose call is five wide in those bodies; reading every import as `Outer` wrote 5 for `Pawn.SetSprinting(true)` (7 wide) and nine bodies came out one word short |
| `TeamDeathMatch.RestartPlayer`'s `Super.RestartPlayer()` | `1c 50 49` (export 4688, `DeathMatch.RestartPlayer`) | a `Super` call walks the **whole parent chain** before it falls back to the `Function` import; the fallback taken at the first ancestor that declared nothing wrote the import for two bodies |
| `BotSG.Tick` in state `BotUnconscious` | export 4129 (`BotEnemy.BotUnconscious.Tick`) | inside a state the parent's **same-state** override is the record, and a function's owner has to be walked past a state's own `Outer` (`func_owners`) |
| `TriggerEvent(EventName, Pawn, Pawn)` in `CheatManager` | `19 01 8e 05 00 04 01 05` per `Pawn` | a `within` call's **arguments** are compiled in `Outer`'s scope: a property the package declares outside the body's chain is read through `Outer` even when its type is not `bool` |
| `Instigator.AttachToBone(...)` in `StargateContent` | -- | a **receiver's class** is re-homed to the package that declares it when exactly one does, so the walk starts there; a name two packages carry keeps the body's package |

### The byte context reaches a literal through the operator tree, and stops at a call

`PlayerProfile.SetDirty` writes `DirtyMask = DirtyMask | 1;` as `0f 01 48 11 39 3d 9c 39 3a 01 48 11 26 16`: the `1`
is **`IntOne`** (`26`), not a widened `ByteConst`. `UWindowHTMLTextArea.ParseColor` writes
`C.R = 16 * GetHexDigit(...) + GetHexDigit(...)` as `39 3d 92 90 39 3a 24 10 ...`: that `16` **is** a `ByteConst`
behind `ByteToInt` (`39 3a`), two operators inside a byte-typed assignment. Three bounds settle the difference, each
on a body this tool scores:

1. **0 and 1 keep the compiler's dedicated opcodes** (`IntZero` `25`, `IntOne` `26`) whatever the context -- SetDirty's
   `1` is `26`.
2. **The byte type propagates through operators**, from the assignment's own type down the tree: ParseColor's `16` is
   a byte because the assignment is.
3. **A call's arguments are typed by the callee's parameters**, so the context stops at a call: the same ParseColor
   expression writes `Mid(S, 2, 1)` as `2c 02 26`, the int forms.

A direct `A = 255;` on a byte is not this shape at all -- `39 3d 2c ff`, the int constant under `IntToByte` -- and
neither is a byte parameter's `0` (`_SetKeyFrameToPuzzleMovers(0)` is `24 00`).

### A `class` operand compares as an `object`

`inventory/native_operators.csv` installs `!=` for `(Object, Object)` and for no class-typed pair, and the compiler
uses that row: `Inventory.Destroyed`'s `if (Pawn(Owner) != None)`, `Mover.Detach`'s and
`PlayerReplicationInfo.SetPlayerName`'s alike. Substituting `class` -> `object` in the lookup only (the operand's own
bytes are unchanged) gained **121 bodies** -- 4,936 -> 5,057, none lost -- the `(class != object)` family leaves the
histogram, and the operator-pair refusals fall 2,806 -> 2,528.

### What is left

**The unary `!` is unmodelled, and it owns the histogram.** 1,059 residue bodies name a `!`, and the operator-pair
refusals are dominated by the unknown type it leaves: `bool && an unknown type` 309, `int < an unknown type` 306,
`an unknown && an unknown` 180, `bool || an unknown type` 136. `Not_PreBool` is `0x81` in the shipped bytes
(`bIsOn = ! bIsOn;` is `14 2d 01 03 81 2d 01 03 16 04 0b 47`) but `inventory/native_operators.csv` carries only
two-operand declarations (97 rows), so the token has no row to come from: the census tool has to read
`operator(N) T sym ( T A )` as well, and then `_type_of` and `emit_expr` need the unary node. After it: `switch`
(173), a member-call statement whose receiver's class the artifacts do not name (174), `continue`/`break` (52).

## Stage 3, continued -- the one-operand node, and the census that never read a unary declaration (measured 2026-11-23)

**5,289 bodies are modelled and all 5,289 are byte-identical**, from 5,057 / 5,057 / 0. No previously scored body lost
a byte and no row's bytes moved: 232 bodies gained, and the residue falls 6,621 -> **6,389** of 11,687 (the walk and
the join are unchanged at 11,687 and 11,678).

### `Not_PreBool` had no row because the census never read a `preoperator`

The previous section recorded the token as unreachable: "`inventory/native_operators.csv` carries only two-operand
declarations". It carries **110** declarations now, from 97, and the missing 13 are `Core.Object`'s unary family --
`native(129) static final preoperator bool ! ( bool A )`, `++`/`--` in both pre and post form, `Complement_PreInt`
(141), `Subtract_PreInt` (143), `Subtract_PreFloat` (169) and `Subtract_PreVector` (211). The declaration regex matched
`\boperator\b`, and `preoperator` has no word boundary in front of `operator`, so every unary statement was skipped;
the records they explain (`AddAdd_PreByte`, `Subtract_PreInt`, ...) then read as if the compiler had synthesised them
from other operators, and that sentence sat in the tool's own docstring. **`--check` now holds 110 declarations, all 110
bound to a registration record, and 0 operator-named records without a declaration (from 13)** -- the orphan line the
tool prints is the gate.

The composed name follows the declaration's own form: a `preoperator` is `<stem>_Pre<Type>` and a `postoperator` is
`<stem>_<Type>` (`AddAdd_Byte` is the postfix increment, `AddAdd_PreByte` the prefix one), so the four increment forms
are four records and not one. The never-matched shape is now a self-test case, three declarations deep.

### A one-operand operator is `<token> <operand> 16`

| the source | the bytes | what it settled |
|---|---|---|
| `bIsOn = ! bIsOn;` (`scaleWall.Trigger`) | `14 2d 01 03 81 2d 01 03 16 04 0b 47` | the token is the native's **own opcode** (129 = `0x81`), the operand is written exactly as a read of the same variable, and the node closes on `16` like any other call |
| `return ( ! Pawn.bJustLanded );` (`PlayerController.WantsSmoothedView`) | `04 81 19 01 05 06 00 04 2d 01 5c 12 16 04 0b 47` | the operand may be a `Context` member, so the node wraps an already-emitted node -- no extra skip word of its own |
| `bSquadBeingAlerted && ! bSquadAlertComplete` (`SquadAIBasic.IsBeingAlerted`) | `82 2d 01 68 21 18 09 00 81 2d 01 47 3c 16 16` | as a short-circuit operand the node's width is what the `Skip` counts: 8 wide bytes of operand plus the `16`, the same `_wide(node) + 1` rule the binary nodes use |
| `- MyList.ItemsPerPage` (`GUIVertScrollBar.WheelUp`) | `1b 2d 8f 19 01 30 05 00 04 01 19 16 16` | the same shape carries `Subtract_PreInt` (`0x8f`) around a member read, so it is one rule for the family, not a `!` special case |

`_type_of` needed the node as much as `emit_expr` did: the operator-pair histogram's `bool && an unknown type` was the
unary node's own result type leaking unknown into the pair lookup, so a one-operand expression now answers its
declaration's return type. A bool assignment admits any value that types as a bool (`bIsOn = ! bIsOn;`), where before
only a literal, a read or a call was written.

### What the node does not reach, and the three bounds on it

- **The operand's type has to be known.** 72 bodies still refuse `an unknown type !` -- `! Level.bDropDetail` reads
  through a member the artifacts do not type. The pair families that survive are the same gap seen from the other
  side: `int < an unknown type` 317, `bool && an unknown type` 289, `an unknown type && an unknown type` 137.
- **`~` is emitted and unexercised.** `Complement_PreInt` (141) takes the same rule as `-`, but no body in the corpus
  writes a unary complement (all 104 `~` hits in the source are `~=` comparisons), so its bytes are unmeasured; a body
  that used it and compiled differently would be refused by the artifact's own contract, not shipped.
- **A folded operand is refused by name** (`a unary operator whose operand is a literal, which the compiler folds`),
  as with the two-operand case: `-1` is an `IntConst`, never `8f 25 16`.

Still refused after this: `switch` (173), a member-call statement whose receiver's class the artifacts do not name
(198), `continue`/`break` (65 "a statement that is control flow"), and the unknown-type operand families above, which
are now the histogram's owners and the next instrument.

## The types the pair histogram was missing: five rules, 5,289 → 5,931 bodies

The unary node left **1,556 operand sites** whose type `_type_of` could not name, and every one of them read as
`an operator the image's table does not install`. Naming them is one thread: four rules in `_type_of` and one in the
operator lookup. Measured on the whole corpus, **5,931 modelled / 5,931 byte-identical / 0 differ** (from 5,289 /
5,289 / 0; 642 bodies gained, none lost, the walk and the join unchanged at 11,687 and 11,678), and the operator
refusals fall 2,523 → **1,279**.

1. **`.Length` of an array or a string is an `int`.** It is the `37` length operator, not a property record, so the
   chain read as unknown and `i < PathList.Length` (157 bodies), `Skins.Length > 0` (51) never reached
   `Less_IntInt`. The test is the receiver's *record* kind, as in `_emit_member`, with the declared type as the
   fallback for a chain.
2. **An enum constant is a `byte`, not its enum's name.** `_type_of` returned `enetrole`/`ephysics`, so the pair
   table was asked for `byte == enetrole` (24) and `byte < enetrole` (21) and refused; the constant is a `ByteConst`
   and the installed row is against `byte`. (`NM_Client`-style constants whose enum the body's chain does not
   declare stay refused as "an enum constant passed as int": see the bound below.)
3. **`int` against `float` promotes to the `(float, float)` row, and the int operand takes the compiler's cast.**
   `Pickup.BotDesireability` writes `RespawnTime < 10` as `b0 01 72 09 39 3f 2c 0a 16` — `Less_FloatFloat` with an
   `IntConstByte 10` behind `IntToFloat` (slot 0x3f). The conversion is written **around** the operand's own bytes,
   never by retyping it: a float site would take `1e 00002041`, and the cast is what the corpus carries. One
   direction only — nothing narrows a float without a cast in the source.
4. **`$` and `@` are declared `coerce string` on both sides**, so a non-string operand is converted with the image's
   own cast table: `" msg DecRefCount = : " $ RefCount` is `1f <string> 00 39 53 01 6e 08 16` (`IntToString` 83) and
   `"STAT QUERY" @ EntryName` is `39 57 00 56 20` (`NameToString` 87). Only a pair whose conversion the build
   installs is taken; the key is the **numeric** type, so a class-typed operand goes through `ObjectToString`.
5. **A member of a class this package only imports resolves through the import table.** `props_all` is this
   package's own exports, so `Pawn.Acceleration` inside `AmbientCreatures` had no record at all (`rec=None`) even
   though the receiver's class was named. The compiler still wrote an index — a **negative import** — and the import
   table names it; a single candidate of that name is used, the same rule `_scope` already applies to a property on
   another chain. `_type_of` then returns the member's **declared class** (`Controller.Pawn` is a `Pawn`), which is
   what lets a second link resolve, and `_numeric` looks a class-typed value up as `object` (`Pawn(Owner) != None`
   is `EqualEqual_ObjectObject`). `FishBase.PhysicsVolumeChange`'s `NewVolume.bWaterVolume` now writes
   `19 00 59 02 06 00 04 2d 01 ae 0f`.

**Bounds, named, not guessed:**

- An enum constant in an `int` site widens, and the **site's own type decides**: `Pawn.FaceRotation`'s
  `Physics == PHYS_Walking` is `9a 39 3a 01 28 39 3a 24 0b 16` (`ByteToInt`, slot 58, in front of the `24 <ordinal>`)
  while `Pawn.DropToGround`'s `SetPhysics(PHYS_Falling)` — a `byte` parameter — writes the bare `24 02`. Both are
  emitted now; the note's "42 bodies do not widen" were the byte-parameter sites. See the next section.
- `ArrayCount(FixedArray)` is **folded to the array's literal dimension** by the compiler
  (`NumClasses < ArrayCount(PawnTypes)` is `Less_IntInt` with `IntConstByte 08`); modelling it needs the export's
  `ArrayDim`, which `ue2pkg` does not read. 33 bodies.
- A struct member's value size is the **declared** struct, and the three the corpus reaches are 12 bytes; a struct
  this table does not carry is still refused, and the residue names it (`color` 9, `region` 7, `htmlstyle` 1).
  See the next section.
- `switch` (173), a member-call statement whose receiver's class the artifacts do not name (198), `continue`/`break`
  (65) are untouched.

## The operand's declared type picks the cast, and two sizes that were constants (measured 2026-11-23)

**6,194 modelled / 6,194 byte-identical / 0 differ** (from 5,931 / 5,931 / 0; **263 bodies gained, none lost**, the
walk and the join unchanged at 11,687 and 11,678). The residue falls 5,747 → **5,484**, and the two families it was
counted by are gone: `an enum constant passed as int` 243 → **0** and `a struct member, whose value size this walk
does not carry` 196 → **0**. The bodies that *emit and differ* fall 61 → **26**. The operator refusal rises
1,279 → 1,327, which is the same advance seen from below: those bodies now reach the operator stage instead of dying
at the operand.

| the source | the bytes | what it settled |
|---|---|---|
| `Physics != PHYS_Falling` (`FishBase.PhysicsVolumeChange`), an enum constant where the pair is `(int, int)` | `9b 39 3a 01 da 01 39 3a 24 02 16` | the constant is a **byte widened**, not retyped: `ByteToInt` (slot 58) around `24 <ordinal>`, the same wrapper a `byte` property gets from the same pair lookup. `_type_of` already answers `byte` for it, so only the site's `int` was missing |
| `SetPhysics(PHYS_Falling)` (`Pawn.DropToGround`) | `6f 82 24 02 16` | the *same* constant at a `byte` site is the bare `24 02` -- the site's own type decides, which is why the previous note's "42 bodies do not widen" and this one's 243 are one rule, not a dispute |
| `return Pawn.Location;` (`TransientAmbientCreature.PickDestination`) | `04 19 01 86 05 00 0c 01 8a` | a struct-valued member is a `Context` like any other and its size byte is the **declared** struct: `vector`/`rotator`/`coords` are 12, and the record's own kind (`StructProperty`) names none of them. The size comes from the artifact's `friendly_type`, so a struct it does not name keeps the refusal -- the residue now carries `color` 9, `region` 7, `htmlstyle` 1 instead of one anonymous 196 |
| `if (Pawn != None) ... Pawn.bPhysicsAnimUp...` (`BotEnemy.EndState`) | `14 19 01 8a 06 00 04 2d 01 e7 02 28` | a bool member's skip word is the member's width in the uncompacted code array (`_wide`), not a constant chosen by whether the object is an import: it is **6** here whatever made the read, and the emitter wrote 5 because the property is a negative import. The 60 `Outer.<bool>` nodes (`01 8e`, import 14) keep their measured **5** |
| `SoundVolume / 255.0` (`Mover.FinishedClosing`) | `39 3c 01 55` | the cast's slot is keyed on the operand's **declared** type, not on the type the pair was looked up under: a `byte` promoted to `float` is `ByteToFloat` (slot 60), not the `IntToFloat` its promoted `int` takes. Same rule at `$`: `Role` is a `byte`, so `Pawn.Fire` writes `39 52` (`ByteToString`) and not `39 53` |
| `self $ " fire weapon " $ weapon` (`Pawn.Fire`) | `39 56 01 17` | ...and a class-typed operand still converts as an **object** (`ObjectToString`, 86): `class` is not the word `object`, so the promoted type is the fallback key when the declared one has no row |

**One shape is a negative, and it is named so the next session does not re-guess it.** An `int` literal promoted to
`float` is **folded into a constant** by the compiler in some sites and **cast** in others, and neither the operator
nor the site decides which:

| the source | the bytes |
|---|---|
| `return (10 + 5 * FRand());` (`TransientAmbientCreature.MaxHiddenTime`) | `ae 1e 00002041` -- the `10` is a `FloatConst` |
| `return (15 + 5 * FRand())`, `(5 + 5 * FRand())` (three classes) | `1e 00007041`, `1e 0000a040` -- folded |
| the `5` of the *same* `5 * FRand()` | `39 3f 2c 05` -- cast |
| `RespawnTime < 10` (`Pickup.BotDesireability`) | `39 3f 2c 0a` -- cast |
| `SetTimer(1 + FRand(), True)` (`Fragment.BeginState`) | `ae 1e 0000803f` -- folded |

Folding is not decided by `+` against `*` (the same body folds one literal and casts the other), by the literal's
position, or by the site's type. Writing it as "arithmetic folds" was tried: it turned 5 bodies identical and broke
**69** that were already identical, which is how the negative was measured rather than reasoned. The 5 folded sites
stay in the residue with their bytes, and the emitter keeps the cast.

**Not in this PR**: `switch` (173), `ArrayCount(FixedArray)` (33 -- needs the export's `ArrayDim`), a member-call
statement whose receiver's class the artifacts do not name, `continue`/`break` (65), and the 26 bodies that still
emit and differ: the six folded literals above, three `SetDrawColor(160 * (IdentifyFadeTime / 3.0))` bodies whose
`160` is a `ByteConst` widened by `ByteToFloat` (a byte the site did not ask for), four `UWindowGridColumn` bodies
whose first difference is a `Skip` word two bytes short, and thirteen singles -- a `within` call, a call token and
index, a cast, and length differences whose cause is not their first differing byte.

## The literal's context and the pair's site (measured 2026-11-23)

**6,202 modelled / 6,202 byte-identical / 0 differ** (from 6,194 / 6,194 / 0; **13 bodies gained, 5 lost**, the walk
and the join unchanged at 11,687 and 11,678, residue 5,484 -> **5,476**), and the bodies that *emit and differ* fall
26 -> **18**. Three rules and one corrected constant, all read off the 26 bodies the previous note left behind.

| the source | the bytes | what it settled |
|---|---|---|
| `return (10 + 5 * FRand());` (`TransientAmbientCreature.MaxHiddenTime`) | `04 ae 1e 00002041 ab 39 3f 2c 05 c3` | the int-literal fold is **positional**: the literal at the **root** of an expression is a `FloatConst` (`1e`), while the `5` of the nested `5 * FRand()` is `IntConstByte` behind `IntToFloat`. The right-hand position casts too (`RespawnTime < 10`), and a parenthesised operand is a root again -- `Beacons.DrawBeacon` folds the `640` of `(640 / RangeToTarget)` and `StargatePawn.DrawClassIcon` the `96` of `96 * scale`. The previous note recorded folding as a negative ("neither the operator, the literal's position nor the site decides it") because it folded *every* arithmetic literal, which fixed 5 bodies and broke 69: it is the position, and only inside a root |
| `Canvas.SetDrawColor(0, 160 * (IdentifyFadeTime / 3.0), 0)` (`StargateHUD.SetIDColor`, three classes) | `... 39 3c 24 a0 ac ...` | an argument's **byte parameter types the literal**: `160` is a `ByteConst` and the mixed pair widens it with `ByteToFloat` (slot 60), not the `IntConstByte` behind `IntToFloat` (slot 63) an int literal takes. The `255` of the `else` branch beside it keeps the int form, which is the next row |
| `Canvas.SetDrawColor(0, 128, 0)` (`SayMessagePlus.RenderComplexMessage`) | `1c dc 01 24 00 24 80 24 00 16` | the byte form's boundary is **255**, not 127. A census of all 18 packages: **3,357** `ByteConst` nodes, **30** of them at 128 or above (128, 137, 150, 160, 192, 200..254), and **389** nodes at exactly 255 in the int form (`A = 255;`, `FindControllerPortIndex(255)`). The earlier "127 is the last value" was measured on six sites that all sat below 128 except the 255 ones |
| `Items.FindEntry((Y - VBorder) / ItemHeight)` (`UWindowPulldownMenu.SetSelected`) | `1b 60 02 91 39 44 af 00 51 19 39 3f 01 7b 0a 16 01 45 0b 16 16` | at the **root** of an expression whose own site is an `int`, a `(float, int)` pair takes the `(int, int)` row and narrows the float operand with `FloatToInt` (slot 68): `Divide_IntInt(FloatToInt(Y - IntToFloat(VBorder)), ItemHeight)`. The **nested** subtraction keeps the float row, and a comparison at a `bool` site promotes the int side instead (`AmbientCreatureManager.InSpawnRange`, `ShieldBelt.Tick`) |

`(a) . _byte_literal`'s widening is why the four `UWindowGridColumn` bodies and `UWindowGrid.Resized` read as a
`Context` skip word two bytes long: the extra bytes were a `39 44`/`39 3f` pair inside the context, not a wrong skip
word. The skip word was the symptom the previous note named; the pair is the cause.

**13 bodies gained:** the five `MaxHiddenTime`/`GetSleepTime` folds, `Fragment.BeginState`,
`Replicator.PlayWaiting`, `Beacons.DrawBeacon`, `StargatePawn.DrawClassIcon`, `SayMessagePlus.RenderComplexMessage`,
`UWindowGrid.Resized`, `UWindowPulldownMenu.SetSelected`, and `PlayerController.BlendRot`.

**5 bodies lost, named and not explained:** `PlayerController.ServerUpdatePing`, `Beacons.VisibleForABit.Tick`,
`BotEnemy.DefendMelee`, `UWindowHScrollbar.CheckRange`, `UWindowVScrollbar.CheckRange`. Each is a mixed pair whose
narrowing sits at a *nested* node, or a `Skip` word whose right operand now differs; the counts are net +8 and the
artifact's contract still holds (a row is byte-identical or the body is refused), but the five are the price of the
root rule and the next session's sample.

**Not in this PR** (the 18 that still emit and differ): the three `SetIDColor` bodies' remaining skip word, three
folded literals the positional rule does not reach, `GUIComponent.ParseOption`'s `final` callee written as a
`VirtualFunction` through a receiver chain, `CheatManager.SetCameraDist`'s write through a `Context`, and singles in
`LinearStairBuilder.Build`, `DeathMatch.AdjustSkill`, `GatlingGunAttachment.Tick`, `UWindowGridColumn` (4) -- plus
`switch` (173), `ArrayCount(FixedArray)` (33) and `continue`/`break` (65) as before.

## The literal's form: a leading position, a float site, and the byte context's own spelling (measured 2026-11-23)

**6,210 modelled / 6,210 byte-identical / 0 differ** (from 6,202 / 6,202 / 0; **8 bodies gained, none lost**, the
walk and the join unchanged at 11,687 and 11,678, residue 5,476 -> **5,468**, the bodies that *emit and differ*
18 -> **10**). Two rules account for all eight, and the five bodies the previous note recorded as lost are back.

| the source | the bytes | what it settled |
|---|---|---|
| `Alpha -= (255 * VisibleTime) * Delta;` (`Beacons.VisibleForABit.Tick`) | `b9 016b3e ab` then `ab 393f2cff 015937 16 007c6f 16` | the int-literal **fold is the expression's leading position**, not a parenthesised one. `255` starts a group that is itself an *operand* of the outer `*`, and the compiler **casts** it (`39 3f 2c ff`). The literal folds only where it is the first operand the compiler reads -- a statement's value, a return value, a call argument -- and it keeps that position down the chain of left operands: `DistanceScale = (640 / RangeToTarget) * 85 / ...` folds the `640` two nodes in, while the `85` to the right of `*` casts |
| `PlayerReplicationInfo.Ping = Min(0.25 * NewPing, 250);` (`PlayerController.ServerUpdatePing`) | `39 3d f9 39 44 ab 1e0000803e 39 3f 00776f 16 2cfa 16` | the mixed `(float, int)` pair is narrowed to `(int, int)` at a root `int` site only when **neither operand is a literal**. Here the float operand is the literal `0.25`: the compiler keeps the `(float, float)` row, promotes `NewPing` (`39 3f`) and converts the *product* at the parameter (`39 44`). `UWindowPulldownMenu.SetSelected`'s `(Y - VBorder) / ItemHeight` has no literal on either side and still narrows |
| `Canvas.SetDrawColor(0, 160 * (IdentifyFadeTime / 3.0), 0)` (`StargateHUD`/`GoauldConsoleHud`/`ReplicatorHUD.SetIDColor`) | `... 39 3c 24 a0 ac 015c0d 1e00004040 16 16 24 00 16` | the **byte context types an *int* literal, not a float one**. `_byte_literal` fired on the `3.0` and wrote `39 3c 24 03` (`ByteToFloat(ByteConst 3)`), two bytes that had been read as a `Context` skip word; the source spells a float, so the compiler writes `1e 00004040` and the byte context stops there. `160` in the same argument is an int literal and stays a `ByteConst` (`24 a0`) |

The previous section's "five bodies lost, named and not explained" are explained: each is one of the two positions
above. `PlayerController.ServerUpdatePing` (the literal pair), `Beacons.VisibleForABit.Tick` (a compound
assignment's value), `BotEnemy.DefendMelee` (`5 * Enemy.Weapon.MaxRange` inside a comparison), and
`UWindowHScrollbar`/`UWindowVScrollbar.CheckRange` (`2 * LookAndFeel.Size...`, a right-hand operand). The four
`UWindowGridColumn` bodies the earlier note blamed on a `Context` skip word are the same rule (`(Y - 12)` narrows
the literal `12` where the group is an operand), and they are still in the ten -- their first difference is that
narrowing, not the skip word.

**Not in this PR** (the 10 that still emit and differ): `LinearStairBuilder.Build`, `PlayerController.PlayerMove`
(`250.0` at an `int` site is `IntToFloat(IntConstByte 250)` -- a *float* literal retyped through the int form),
`CheatManager.SetCameraDist` (a write through a `Context`), `GUIComponent.ParseOption` (`final` callee through a
receiver chain), `DeathMatch.AdjustSkill`, `GatlingGunAttachment.Tick`, and the four `UWindowGridColumn`; plus
`switch` (173), `ArrayCount(FixedArray)` (33) and `continue`/`break` (65) as before.

## Stage 3, continued -- the compiled array size, and the `const` behind it (measured 2026-11-24)

**6212 modelled / 6212 byte-identical / 0 differ** (from 6,210 / 6,210 / 0; **2 bodies gained, none lost**, the walk and
the join unchanged at 11,687 and 11,678, residue 5,468 -> **5466**, the bodies that emit and differ stay **10**).

| the source | the bytes | what it settled |
|---|---|---|
| `return ArrayCount(BombingBay);` (`AlkeshBomber.GetNumBombingBays`) | `042c02040b47` | `ArrayCount(X)` is **not a call**: the compiler writes X's *declared dimension* as an int literal -- `var BombingBay BombingBay[2];` on the class, so the body is the one-statement `Return` of a `2c` constant. The token is the ordinary int form (`25` zero, `26` one, `2c <byte>`, `1d <int32>`), and it converts at its site like any int, so a float site would carry a `39 3f` in front of it |
| `if (EventCacheIdx >= ArrayCount(EventCache))` (`LevelInfo.AddEventToCache`) | `?` | the dimension can be **large**: `var name EventCache[64];` folds to `2c 40`, not to a byte-only or a zero form. The **type** the call answers is `int`, which is what made 41 of the residue's `ArrayCount` rows read `int < an unknown type` -- the pair table never saw a left type for the comparison, so the body stopped at its first `if` |

The declaration is read from the **source text**, not from the package: `tools/uscript-emit.py` scans every `var` and
`local` statement for a declarator whose `]` is followed by `;` or `,` (`var string A[4], B[2];` reads both), which is
what separates a declaration from an index in an initializer. A dimension spelled as a `const` is followed to its
value -- `GUIController`'s `LiveRungData[TOTAL_LADDER_RUNGS]` is 17 from `const TOTAL_LADDER_RUNGS=17;` -- and a name
whose declarations disagree, or whose const is an expression rather than a number, is refused by name
(**0** rows: "an ArrayCount of a name whose declared dimension the source does not spell") instead of being given a
guessed size, because a wrong dimension is a wrong body that still scores.

**Not in this PR:** `switch` (173), `continue`/`break` (65), and the 10 bodies that still emit and differ.

## The `switch` chain and the two loop exits (measured 2026-11-24)

`switch`, `break` and `continue` were the statement walk's three named refusals. They are now emitted, and the score
moved **6,212 -> 6,269 modelled, 6,269 byte-identical, 0 differ** (57 bodies gained, none lost; the residue
5,466 -> 5,409).

**The `switch` is a chain, and its terminator is unconditional.** A `switch (e)` writes `05 <width>` and then the
expression; each `case` writes `0a <word>` and then its value expression; the list ends on `0a ff ff`. The word is the
**absolute position of the next `Case` in the compiler's uncompacted array**, the same reading `tools/switch-join.py`
measured from the handler at `0x0004ca20`, so the emitter knows every link only after the arms are laid out and patches
them at the end. The opcode is `0a` -- reading it as `Return`'s `04` stops the walk at the case value, which is what the
first draft did.

**A `switch` with no `default:` still writes the terminator.** `PlayerProfile.GetCategoryVolume` is three cases and no
`default:`, and its shipped body ends `... 0a 5e 00 21 75 06 07 58 00 ... 0a ff ff 04 1e 00 00 80 bf`: the last case
links to the terminator, the terminator has an empty body, and execution runs from it straight into the statement after
the switch. Emitting no terminator left twelve bodies exactly **three** bytes short, which is the tell that the missing
bytes were one token and not a rule.

**The width is the compared value's declared type, not the case list's.** `0` is a wide string, `1` a byte or a bool,
`4` an int, a name or a float -- `case 'Music':` under a `name` switch is `21 <index>` compared as four bytes, `case 0:`
under an `int` is `25`, and `case "":` under a `string` is `1f 00`. That is the same split `inventory/switch_operands.csv`
carries for the 200 sites a clean walk reaches (19 at width 0, 118 at 1, 63 at 4).

**`break` and `continue` are bare `Jump`s to a position the enclosing construct publishes.** A `break` names the end of
the nearest loop or `switch`; a `continue` names the loop's next iteration -- the step clause of a `for`, the condition
code after a `do`/`until`'s `JumpIfNot`, the loop's own start for a `while` -- and a `switch` passes `continue` through
to whatever loop encloses it. Neither target is known when the body is emitted, because each is the body's own end
(`break`) or the tail it precedes (`continue`), so a construct whose exit depends on its own length is emitted twice:
the first pass writes a three-byte placeholder, which is the width of the real jump, so the extent the second pass
measures is the first pass's. That is the same shape the `Case` words need, and it is what makes a `break` inside an
`if` inside an arm (the two interior breaks `inventory/switch_arms.csv` counts) ordinary rather than special.

## The `foreach`, the class literal, and a guard that read an argument as a keyword (measured 2026-11-24)

**6,269 -> 6,392 modelled, 6,392 byte-identical, 0 differ** (123 bodies gained, none lost; the residue 5,409 -> 5,286;
the walk and the join unchanged at 11,687 and 11,678). Three rules and one defect, one thread.

**A `foreach` is an `Iterator` token whose operand comes *after* its own call.** `foreach F(a) S;` writes
`2f <the call to F> <u16>` + S + `31` (`IteratorNext`) + `30` (`IteratorPop`), and the word is the **wide position of
the `IteratorPop`** -- `Pickup.CheckTouching` is `2f 61 33 20 01 00 70 3d 16 1c 00 1b 2d 00 70 3d 16 31 30 04 0b 47`
with the pop at wide 0x1c. The loop has **no back edge**: the step is the `IteratorNext` the body ends on, so a
`break` is a bare `Jump` to that word (`HUD.PostBeginPlay`'s `... 26 00 06 26 00 31 30`, the `Iterator` and the `Jump`
naming one position) and a `continue` is the **step plus** that `Jump` -- `TestInfo.TestContinueForEach` writes
`31 06 4f 00` for the arm of an `if` whose body is `continue;`. Both targets follow the body's extent, so the body is
emitted twice with a two-byte placeholder, the same shape `switch` and the loops use.

**A class name in single quotes is an object reference, not a name.** `class'Pawn'` compiles to
`20 <compact index>` (`ObjectConst`), the same table a `DynamicCast` reads: `foreach TouchingActors(class'Pawn', P)`
writes `20 01`, Engine.u's export 1, which is `Pawn`. Reading it as a `NameConst` would have been a body that scores.
A name the package's tables do not carry is refused by name (**2** rows corpus-wide).

**The guard that refused a control-flow keyword scanned the whole statement.** `emit_stmt`'s first line asked whether
*any* token of a simple statement was `if`/`for`/`while`/`do`/`switch`/`case`/`else`/`foreach`, so a statement that
merely **passed** one -- `Switch` is a `LocalMessage` parameter, and `GameInfo.BroadcastLocalized` passes it bare --
was refused as unparsed control flow. It reads the statement's first token now, which is the only place a form the parse
did not take can show up.

**What the 77 was.** The number named every residue row whose text carries a `foreach`, not the bodies the form blocked:
of the **309** declarations whose tree holds a `foreach`, the rule now emits **1** byte-identical
(`DemoRecSpectator.ViewClass`) that carries one at statement level... and the 12 rows that differ are the shape below.
The rest were already refused deeper -- **190** of the **246** `foreach` residue rows name "a compound expression",
which the class literal above is the first half of.

**Not in this PR:** the `foreach` body that **returns**. 12 bodies now emit and differ, all one byte short or long, and
their shipped bytes carry an `IteratorPop` in front of the `return` (`Pawn.TouchingWaterVolume` ships
`... 2d 01 6d 02 30 04 27 31 30 04 28 04 0b 47` where the walk writes `... 2d 01 6d 02 04 27 31 30 ...`) -- a cleanup
the compiler inserts when a `return` leaves the iterator, and the next rule this thread reads.

## The `foreach` that returns, the object `switch`, and the empty `switch` (measured 2026-11-24)

**6,392 -> 6,404 modelled, 6,404 byte-identical, 0 differ** (12 bodies gained, none lost; the residue 5,286 -> 5,274;
the walk and the join unchanged at 11,687 and 11,678). Three rules from one thread, and the first is the cleanup the
`foreach` note left as "not in this PR".

**A `return` inside a `foreach` pops the iterator first.** `Pawn.TouchingWaterVolume` ships
`... 6d 02 | 30 04 27 | 31 30 | 04 28 | 04 0b 47` -- `IteratorPop`, `Return True`, then the loop's own
`IteratorNext`/`IteratorPop` (dead code, because the return left), then the next statement's `Return False`. The pop
therefore belongs to the **`return`**, not to the loop: `LadderVolume.InUse` writes it for a `return true` inside a
nested `if` whose body is the whole arm. The body is emitted twice for the `Iterator` operand, so the extra byte is
counted in the measuring pass and every following position still lands on the word the shipped bytes name. Two
**nested** iterators would need two pops each; the corpus carries no such body, so the shape is refused by name
instead of doubled.

**A `switch` on an object compares at width 4.** `InGameMenu.ButtonClick`'s `switch (Sender)` writes `05 04` in front
of a `LocalVariable` that holds a `GUIButton`, and its `case ExitBN:` is an `InstanceVariable` read of an
`ObjectProperty` -- the same `4` an int takes, because a pointer is four bytes. The walk had refused these by name
("a switch on a object, whose comparison width the corpus does not pin", **16** rows); the width table carries
`object: 4` now.

**A `switch` with no `case` at all writes the terminator and nothing else.** `ExtrasPage.ButtonClick`'s
`switch (Sender) { }` ships the head, then `Case 0xffff`: there is no case word to patch, and control runs from the
terminator into the statement after the switch. The walk refused an empty group list as "a switch whose first label is
not a `case`"; it now emits the terminator alone, and `CreditsPage.ButtonClick` is the second of the two rows.

**`--check`** carries four new synthetic bodies -- a `return` in a `foreach`, the same inside an `if`, an object
`switch` and an empty one -- plus the nested-`foreach` return that must refuse: 89 bodies, no dump, no writes.

## Stage 3, continued -- the array element read, and the index that comes first (measured 2026-11-24)

**6,096 -> 6,662 modelled, 6,662 byte-identical, 0 differ** (566 bodies gained, none lost; the residue
5,274 -> 5,016, and the compound-expression bucket 420 -> 129). It was the statement walk's largest single refusal:
an expression that reads `A[i]`.

**The token is the record's own kind, not the syntax's.** A dynamic array is a `UArrayProperty` and writes `10`
(`DynamicArrayElement`); a static dimension on any other record writes `1A` (`ArrayElement`) -- `XUID` is a
`UByteProperty` whose `XUID[12]` lives in the class record, and `PlayerController.ServerSetXUID` writes
`0F 1A 25 01 58 05 ...` for `XUID[1] = ...`. Both tokens take the same two children.

**The children are the index first and the array second, and the node writes no skip word of its own.**
`AIMsgManager.GetReceivedMsg`'s `return msgReceived[Idx];` is `04 10 00 7a 57 01 01 7e 03 04 0b 47`: `10`, then the
local `Idx`, then the property read. The enclosing `Return`/`Let` carries on directly after the second child --
there is no `16` belonging to the element, which is why a first draft that wrote one overshot every body by a byte.

**The element is its declared element type.** `array<T>`'s `T` for a dynamic array; the record's own kind for a
static one (a `UByteProperty` with a dimension is `byte` elements); a struct element keeps the artifact's friendly
name, because that is the key the cast table and the member walk use; a nested `A[B[i]][j]` reads its element type
from the innermost array. That type is what three consumers need -- a cast around the read and an operator pair at
its site (`_type_of`), the receiver of a member call (`_class_of`: `GlobalEvents[size - 1].AddCondition(con)`, where
the element type is the class the call resolves against), and the legality of the target in `emit_assign`
(`Skins[0] = ...` emits an ordinary `Let`).

**The element shape is recognised only when the whole run is one.** The closing `]` has to be the run's last token
and its partner the depth-0 `[`, so `A[i].B` and `A[i] + 1` are not elements -- the member chain and the operator
are tried first, exactly as the compiler nests them.

**Not in this PR:** the element of an array whose declared element type the artifacts do not spell (94 rows still
name it), and the remaining compound expressions (129).

## Stage 3, continued -- a `Super` call names the nearest ancestor's import (measured 2026-11-24)

**6,662 -> 6,777 modelled, 6,777 byte-identical, 0 differ** (115 bodies gained, none lost; the residue 5,016 ->
4,901, and the `Super`-call bucket 483 -> 368).

**One rule, measured.** `Super.F()` writes `1c <record>` where the record can be an **import**, and a package
imports one entry per ancestor that declares the name: `AmbientCreatures.u` carries `PostBeginPlay` three times
(import 60 on `Controller`, 66 on `Actor`, 80 on `Pawn`). The walk refused every one of them because it only
accepted a *unique* import. The compiler's choice is the import whose **owner** -- the import's own `package`
field, which names the declaring class -- is the nearest class of the parent's chain, and the shipped bytes agree
on **351 of the 380** bodies that open with `1c`:

| body | chain (nearest first after the parent) | shipped record | owner |
|---|---|---|---|
| `Bird.PostBeginPlay` | `TransientAmbientPawn, Pawn, Actor` | `1c d0 01` = import 80 | `Pawn` |
| `TransientAmbientCreature.PostBeginPlay` | `AIController, Controller, Actor` | `1c bc` = import 60 | `Controller` |
| `HerdAnimal.Destroyed` | `AmbientCreature, AIController, Controller, Actor` | `1c ac` = import 44 | `Controller` |
| `AmbientCreatureManager.PostBeginPlay` | `Info, Actor` | `1c c2 01` = import 66 | `Actor` |

**The 29 that do not agree are `BeginState`/`EndState`**, which name a **state** rather than a function and stay
refused (they are the next shape this thread reads, not a wrong rule).

**The tool change.** `_class_tables` now keeps each import's declaring class (its `package` field resolved through
the import or export table) in `import_owners`, threaded into the body env; `_record_on` walks the parent's chain
nearest-first and takes the first ancestor that owns a same-named `Function` import, falling back to the unique
import as before. `--check` stays green with no dump (89 synthetic bodies, all four `Super` controls unchanged).

**Not in this PR:** the `BeginState`/`EndState` state record (29 bodies) and the struct-property assignment (539
residue rows).

## Stage 3, continued -- a struct assign is a plain `Let`, and a compound assign picks by its target (measured 2026-11-24)

**6,777 -> 6,966 modelled, 6,966 byte-identical, 0 differ** (189 bodies gained, none lost; the residue 4,901 ->
4,712). Three rules from one refusal family, and the walk now models **6,966 of the 11,687 bodies it reads**
(4,712 not modelled), regenerating `inventory/uscript_emit.csv` byte for byte over the whole corpus.

**A struct-typed target is a plain `Let`.** `Velocity = 200 * VRand();` is `0f 01 98 <operator>` and
`newRotation = Rotation;` is `0f <LocalVariable> <InstanceVariable>`: the copy is by value and the *token* carries
the extent, so there is no member node and no size byte to write. That refusal was the walk's second largest after
the element read -- 539 residue rows named it -- and both spellings (`emit_assign`'s member branch and its property
branch) needed it, because a local struct and a property struct take the same token and differ only in the write
index.

**A compound assignment's row is chosen by its first (`out`) parameter -- the target's declared type -- and only
then by the value.** A `+=` is not a `Let` around a `+`: its token is a separate native (`AddEqual_IntInt` 161
against `Add_IntInt` 146). `NewRot.Yaw += Delta * SpinRate` targets an `int` and the table installs no
`+=(int, float)`, so the compiler takes `AddEqual_IntInt` and narrows the value (`39 44`, a `FloatToInt` in front of
the product); `a *= 2` has no `*=(int, int)` row at all and takes `MultiplyEqual_IntFloat` with the literal widened
(`39 3f 2c 02`); `Velocity *= -1` takes `MultiplyEqual_VectorFloat` and widens (`dd 01 98 39 3f 1d ffffffff 16`).
The value is widened to `float` before it is narrowed to the target, because that is the order all three show.

**A binary operator promotes an `int` operand to `float` when the exact pair is not installed and the pair with
that side widened is** -- right side first, then left, then both, because a row that exists on the other side is a
different native: `200 * VRand()` is `Multiply_FloatVector` with the literal cast (`d5 39 3f 2c c8 fc 16 16`).

**What is left.** The residue's head is now **1,414** rows of *an operator the image's table does not install*,
then 581 *a member that is not a property of the receiver's class* and 485 *a call to a name the class chain does
not declare*; the shapes histogram carries 10 `element` bodies and 45 `super-call`, and the element read's own
refusal is down to 94 rows.

**Not in this PR:** the 1,414 operator rows (the next instrument), the `BeginState`/`EndState` record and the 135
bodies that still emit and differ.

## Stage 3, continued -- a bare name is an enum constant, a const, or a local's declared word (measured 2026-11-24)

**6,966 -> 7,250 modelled, all 7,250 byte-identical, none of them differing** (284 bodies gained, **none lost**; the
residue 4,712 -> 4,428 rows and the operator bucket 1,414 -> 1,125). Four rules from one family: a **bare name** in an
expression, which the walk had resolved only along the body's own class chain and otherwise refused.

**An enum constant is a global name to this compiler, not a name resolved along the chain.** Of the 1,414 rows of *an
operator the image's table does not install*, **104** were operands that are `NM_DedicatedServer` (41), `NM_Client`
(23), `NM_Standalone` (21), `MSGP_None`… -- `LevelInfo.ENetMode`'s constants, spelled in bodies whose chain never
reaches `LevelInfo` and compiled as the ordinal alone (`24 <ordinal>`). The chain is still tried **first**, because a
declaration the body's own chain carries is the one the compiler read; the corpus table (`enum_by_name`, built in
`score`) answers only a name whose every declarer in the corpus agrees on one `(ordinal, enum)` pair, which is what
keeps a name two enums spell differently out of the guess. Callers that must not widen pass `corpus=False`.

**A `const NAME = <int>;` is a compile-time literal.** `_scope` already built the table for `ArrayCount`; it is now
`scope["consts"]` and `emit_expr` re-enters the literal path at the **same site type and the same root position**,
because the compiler substitutes the value and encodes the site exactly as the written number would be. **73** rows were
`NUMTEAMS`, `NUM_PIECES`, `MAX_CORRECT_MOVER`, `NUM_RINGS`… A const at the root of a `float` expression folds like a
written literal does; one passed where a byte is wanted is a `ByteConst`.

**The lookup order between the two is measured, not chosen: chain enum -> chain `const` -> corpus enum.** **24** names
are both a `const` and an enum constant (`xc_*`), so an order that put the corpus table first would read those from the
wrong table; a chain `const` outranks a corpus enum because the body's own declaration is the one the compiler read.

**A local's declared word, and the control that caught the wrong rule it enabled.** `_declared_type` reads a
**local's** type from `env["localtypes"]` (`local Vector Dir;` -> `vector`) instead of leaving a `StructProperty` local
as `struct`: **78** bare operands over **46** names (`StartTrace`, `Enemydir`, `TargetVector`, `HitLocation`). That is
unambiguous only because **no class declares one local name with two types -- 0 collisions over 4,124 `local`
declarations**, which is why the table is first-wins.

With the word now `vector`, `_type_of`'s dot branch took `_member_record` where it had taken `_prop_by_name`, and
**42 bodies the walk already modelled were refused** (`Canvas.SetDrawColor` writes `local Color C; C.R = R;`). The
member's *record* is chosen by the receiver's **record kind**, exactly as `_emit_member` chooses its node -- the same
convention, one function away -- and with both doing it, all 42 come back: the four rules gain **284** and lose none.
A version of this branch that stopped at the typing change was measured first and shipped nothing (7,174 with 42 lost),
which is the number to distrust if anyone re-derives the rule.

**The differ bucket grew 135 -> 148, and not one of the new rows was modelled before.** Every body in it is *refused*
-- the artifact's verdict is byte equality, so nothing wrong enters `modelled`. They are bodies the old rule stopped
early and that now reach a **later** node whose rule is wrong: **seven** appear only once the member read follows the
record kind (`BlueFlag.PostBeginPlay`'s `SimAnim.bAnimLoop = true;`, `Mover.KeyFrameReached`, `UDebugBlueLookAndFeel.
FW_GetClientArea`), **six** already with the two bare-name tables (`HandOfLight_AstralSenseParticle`,
`ZatPulse`, `StingerProjectile` 57 vs 57, `ReplicatorQueen`, `StargateGrenadeProjectile`, `ExtreniteWeapon`'s
`FullChargeTime / MAX_CHARGE`). Named like this they are the next instrument's work list, not a fraction.

**What is left.** The residue's head is 604 *a member that is not a property of the receiver's class*, 502 *a call to a
name the class chain does not declare*, 372 *a member call statement…*, 232 *an operator … (bool && an unknown type)*,
232 *a call statement whose receiver's class the artifacts do not name*, 170 *a numeric literal in a typeless
function*, 164 *a name neither a local, a property nor an enum constant of the chain*.

## Stage 3, continued -- the Super record's walk, and a member call's return type (measured 2026-11-24)

**7,250 -> 7,648 modelled, all byte-identical, none of them differing** (398 bodies gained, **none lost**; the residue
4,428 -> 4,030 and the differ bucket 148 -> 66). Two rules, each a join that was reading the wrong table.

**A `Super.F()` record's import walk starts at the parent, because it is handed the parent.** `_record_on(parent, ...)`
opens `_ancestors(chain, pack, parent)` -- whose **first** entry is the parent itself -- for the export half, but the
import fallback sliced `[1:]`, so a body whose parent imports the name itself wrote the **grandparent's** import.
`EmptyHands.PostBeginPlay` is `1c c6 02 16 04 0b 47`: `c6` sets the sign bit and the continuation bit, `02` continues
the value, so the record is **-134** (`StargateContent` imports `PostBeginPlay` for `StargateWeapon`, `Actor`, `Pawn`,
`Controller` and eleven more), and the walk wrote -127 (`Actor`). **70 of the 148** bodies that emitted and differed
differed at that one byte and nowhere else -- `ZATGUN.DisplayDebug` is -226 (`StargateWeapon`) against the walk's -236
(`Actor`), and every `super.PostBeginPlay();` in `StargateContent` moved. The 29 that still disagree are
`BeginState`/`EndState`, which name a state rather than a function, and they stay refused.

**A member call's return type is declared on the receiver's class, not on the body's chain.** `_type_of`'s call branch
walked `env["order"]`, the body's own ancestors, so `CurrentAction.WaitForPlayer()` -- a `bool` declared on
`ScriptedAction`, a class the body does not extend -- was an unknown type, and every `A && B`, `A || B` and `!A`
around such a call was refused as a pair the table does not install. That is **518 of the 1,125** operator rows (311
where the unknown side was on the right, 207 on the left). `_rettype_on(cls, ...)` already refuses the body-chain
fallback for exactly this reason -- a same-named function on the body is a different record with its own return type
-- and `_emit_member_call` joins through `_class_of`, so the type now comes from that same join. The pair histogram
moves where the note predicted: `bool && an unknown type` 232 -> 28 rows, `an unknown type && bool` 81 -> 6.

**The residue carries a debug spelling.** `SG1_DBG_OP=1` appends both operand texts to the operator refusal
(`... <<Pawn . DrawScale | Pawn . Default . DrawScale>>`), because a pair histogram names a shape while a token run
names the rule. Every count in this section was taken that way, and it is the instrument the next family starts from.

**What is left.** The operator bucket is **759** rows, headed by **353** whose unknown side is a **struct member**
(`Dest.Z`, `Rotation.Pitch`, `Viewer.Region.Zone.bDistanceFog`) -- a `vector`'s fields are not in
`inventory/class_properties.csv`, which is per class -- then 49 `X.Default.Y` chains and 56 names that are a `const`
on an ancestor class (`MAXSTEPHEIGHT`, `pi`). The differ bucket is 66, three of them new: bodies the two rules carried
far enough to reach a later node whose rule is wrong, all refused and none written.

## Stage 3, continued -- a struct member's declared type, and a float const (measured 2026-11-24)

**7,648 -> 7,786 modelled, all byte-identical, none differing** (138 bodies gained, none lost; the residue
4,030 -> 3,892 and the differ bucket 148 -> 89). Three rules, each a table that was missing rather than a walk that
was wrong.

**A struct member's type is a `var` line in a `struct` block, and nothing in the package carries it.** A struct's
member *is* a record -- `PathName.FullPath` is Engine export 3007 and `Directory` 3006, both property exports whose
Outer is the class-`Struct` export `FilePath` -- but the record's class only gives its **size** (`FloatProperty` is
four bytes). The word a cast or an operator needs is in the source: `Coords.Origin` is a `vector`,
`FilePath.DirectoryParts` an `array<string>`, `Vector.Z` a `float`. `tools/struct-members.py` reads every class text
in the dump's 18 packages (2,319 texts) with `uscript-dis.source_structs()` and writes
`inventory/struct_members.csv`: **164 structs, 800 members, 799 typed**; the one untyped member
(`ParticleProjectorInfo.BitmapMaterial`) is declared with two types in the corpus and is written empty rather than
guessed. The census is what shows the engine's own `Region` struct is **not in any class text** -- the `region` the
corpus declares has `X`,`Y`,`W`,`H` and no `Zone`, which is why `Viewer.Region.Zone.bDistanceFog` is still refused.

Two joins, and the pair is what makes them safe. The emitter keys a struct member's *record* by
**(struct, member)** -- `Dest.Z` is `Z` on `Vector`, an import of AmbientCreatures (`36 95` = -21), while `z` alone
names a function in six Engine classes -- and it keys the member's *declared type* by the same pair in the census.
Both are fallbacks: `_prop_by_name` still answers first, so every body that modelled before emits the same bytes
(the artifact regenerated with 0 new differ rows). The operator bucket falls **759 -> 693** and *a member that is not
a property of the receiver's class* **631 -> 536**.

**A `const` may be a float, and the table kept only integers.** `const Pi = 3.1415926535897932;` on `Object` and
`const MAXSTEPHEIGHT = 35.0;` on `Actor` (with `SUPERPUNCH_RANGE = 100.0` on `SuperSoldierBot`) were absent from every
body's const table, so *a name neither a local, a property nor an enum constant of the class chain* held 56 operator
rows that were float consts. `_consts_of` now keeps the **literal text** rather than the number it parses to -- a
const is substituted at its site, so the spelling is what picks `FloatConst` from `IntConst`, and
`str(3.1415926535897932)` is not the text the source wrote. `ArrayCount` still folds only a whole-number const
(`_const_int`), because a dimension cannot be a float. That bucket falls **164 -> 130**.

**`X.Default.Y` is a `ClassContext` (`12`), and the walk does not reach it yet.** Measured from the shipped bytes of
three refused bodies: `return AmmoName.Default.MaxAmmo;` is `12 01 75 0a 05 00 04 02 5b 0f` -- the object expression
is the receiver, the word is the member's value size, and the `DefaultVariable` (`02`) carries **`Y`'s own record on
X's class**, so there is no separate member read. `_emit_member` now builds that node after resolving the record
through `_member_record(X, Y)`, and `_type_of` types the chain the same way, but the branch is **not reached**: the
statement walk hands `_emit_member` an expression without the chain (3 rows still refuse, and no body models it).
The shape is measured; the routing is the next read, and it should start at `emit_stmt`'s `return` arm rather than at
the member walk.

**Not in this change.** The 693 remaining operator rows, the 89 differ rows (headed by bodies the two rules now carry
to a later node), the `BeginState`/`EndState` state record, and the `X.Default.Y` routing above.

## Stage 3, continued -- `X.Default.Y` is two nodes, and both branches were dead (measured 2026-11-24)

**7,786 -> 7,809 modelled, all byte-identical, none differing** (23 bodies gained, none lost; the residue 3,892 ->
3,869 and the differ bucket 119 -> 96).

**The rule the page had already measured never ran: a double index made the guard read a character.** Both
`_emit_member` and `_type_of` tested `obj[chain_at + 1][0][0] in ("ident", "keyword")` -- but `obj[chain_at + 1]`
*is* the token, so `[0]` is already its kind and `[0][0]` is the kind's first character. `'k' in ("ident",
"keyword")` is False for every token, so `X.Default.Y` was unreachable: the expression split at its trailing dot
(`obj` = `X.Default`), the chain guard failed, and the walk fell through to *a member that is not a property of the
receiver's class*. `Message.Default.bBeep`, `P.Default.AirSpeed` and `Driver.default.bOwnerNoSee` all refused
there. Corrected to `obj[chain_at + 1][0]` / `[1]`, 23 bodies model; **34** rows still refuse one step further on
(*a default chain whose member the receiver's class does not declare*), and that is the next read.

**The node is a `Context` (`19`) or a `ClassContext` (`12`), and the receiver's own type picks it.** The page
carried `12` from three bodies. With the branch alive, every default chain over a *variable* writes `19` and only a
receiver that holds a **class** writes `12`: `Weapon.AmmoName` is declared `var class<Ammunition> AmmoName`, so
`return AmmoName.Default.MaxAmmo;` is `12 01 75 0a 05 00 04 02 5b 0f`, while `P.Default.AirSpeed` is
`19 00 21 05 00 04 02 a9` (a local) and `Driver.default.bOwnerNoSee` is `19 01 1b 06 00 04 2d 02 59`-shaped (a bool
member writes `2d 02 <idx>` and a 6-word). `_is_class_ref` reads the answer from the record's free-text type
(`Class<...>` in `inventory/class_properties.csv`), from a `class'X'` literal, or from a bare class name. Emitting
`12` for an object was the whole of the 30 body differences this branch produced when it first came alive, and all 30
went identical once the token was chosen this way. The artifact regenerated byte for byte over the whole corpus.

**What is left.** The 96 differ rows -- the `Super.BeginState()`/`EndState` state record, the `2d 36` bool operand in
`Weapon.AdjustAim` / `Controller.InstantWarnTarget`, two `continue` jump targets in `TestInfo` -- the 34 rows the
default chain now reaches, and the 680 operator rows (headed by the struct members whose struct the corpus never
declares).

**Not in this change.** The 17 *a receiver chain whose member is not one name* rows (`Groups[GameIndex].Records.
Length` and its kin: a member of a member), and the struct sizes no class text carries (`Region`, `Zone`,
`MouseCursor`, `HTMLStyle`).

## Stage 3, continued -- a `Super` in a state, a struct member's bool, and `continue` (measured 2026-11-24)

**7,809 -> 7,853 modelled, all 7,853 byte-identical, none lost and none changed** (44 bodies gained; every one of
the 7,809 rows that was identical before is still identical, to the byte, and the differ bucket 96 -> 52 with **no
new row in it**). The residue 3,869 -> 3,826. Three rules, all read off bodies the walk already reached and then
wrote wrong -- the differ bucket is the only place a rule shows up without a refusal to hide behind.

**A `Super.F()` inside a state names the state's own super: first the state it `extends`, then the parent
class's same-state override -- and the same-state half is a *pair*, not a class.** Two spellings, one idea:

| the source | the record | why the walk wrote something else |
|---|---|---|
| `GRENADE.ClientPreparingToFire extends PreparingToFire` calls `Super.BeginState()` | export **2073** = `GRENADE.PreparingToFire.BeginState` | the walk looked on the class parent (`StargateWeapon`) and wrote its import, -177 |
| `UnrealPawn.Dying` calls `Super.BaseChange()` | import **-1044** = `BaseChange <- Dying <- Pawn` | the import table names the *state* as the record's owner, and the walk compared `dying` against class names, so it fell through to the class-scope import -1116 |

`Import` entries carry their owner as a third field that points at another entry, and a function owned by a state
spells the chain out: `BeginState <- Idle <- Weapon` is -1263, `BeginState <- Activated <- Armor` is -523. Reading
only the immediate name (`idle`) can never match a class, which is why **35 bodies** wrote the class-scope import.
`_class_tables` now keeps that owner as a `(state, class)` pair beside the bare name (`import_pairs`, because the
bare name is what the struct-member join reads), `_record_on` resolves the record through the pair, and
`uscript-dis.state_parents()` reads the `extends` clause from the one state-header rule (`class-text-bodies`).
The resolution order is the measurement: state-extends -> parent's same-state (exports **and** imports) -> parent's
class scope. 38 bodies.

**A bool member of a struct is read behind `BoolVariable`.** `Weapon.AdjustAim`'s `!SavedFireProperties.bInitialized`
is `81 2d 36 52 43 01 5a 08 16` and `BlueFlag.PostBeginPlay`'s `SimAnim.bAnimLoop = true;` is `14 2d 36 ...`: the
`StructMember` node reads the record and the `2d` around it types the value, exactly as it does for a bool
*property*. The walk wrote the bare `36`, which is the whole of four differences (`Weapon.AdjustAim`,
`Controller.InstantWarnTarget`, `BlueFlag.PostBeginPlay`, `RedFlag.PostBeginPlay`). 4 bodies.

**A `continue` names the loop's test node, not the loop's first byte.** `TestInfo.TestContinueWhile` writes
`06 59 00` inside a `while` whose back-`Jump` sits at wide 0x59, and `TestInfo.TestContinueDoUntil` writes
`06 60 00` for the `do`/`until`'s trailing `JumpIfNot` at wide 0x60. Both are the node that decides whether another
iteration runs -- in a `while` that is the back edge (which jumps to the condition at the top), in a `do`/`until`
the test itself -- and both spellings place it at the loop's *end*. The walk wrote the loop's start (`while`) and
the condition's first byte (`do`/`until`), three bytes late. 2 bodies.

**The gate** carries the three as controls: `whilecontinue`/`docontinue` checked structurally (the emitted loop's
last branch node is the position the `continue` names, so the check cannot agree with a wrong constant), a bool
struct member required to be `2d 36 <record> <read>` while its float sibling is not, and `Super.Ping()` in a state
that extends another `(1c <export>)`, in a state whose override is an import (`(state, class)` pair), and with no
same-state record at all (class scope). Both new body gates were corrupted deliberately and each named its row.

**What is left.** The differ bucket is 52: the `2d 36` bool operand is gone, `continue` is settled, and what remains
is the UWindow `region`/`mousecursor`/`htmlstyle` struct members (**its struct is declared in no class text**, so its
member indices and sizes are unknown), the Editor builders' `39 3f` operand casts, `B4SParser`, `Mover`'s two
keyframe bodies, and `TextGameInfo.InitComponent`'s one non-ASCII byte. The 34 *default chain whose member the
receiver's class does not declare* rows are `X.Default.Y` where `X` is a `class<Z>` variable -- `PickupClass`,
`DamageType`, `WeaponClass` -- so the member lives on `Z`, which is the next join to read.

## Stage 3, continued -- a `class<Z>` receiver, a bool member of the default object, and a cast that is not a reference (measured 2026-11-24)

**7,853 -> 7,884 modelled, all 7,884 byte-identical, none changed** (32 bodies gained; the residue 3,825 -> 3,794;
the *default chain whose member the receiver's class does not declare* bucket **34 -> 5**; the differ bucket stays
at **52** with **6 rows fixed and 6 new**, both named below). The three rules are one join each, and each was a word
the walk had already read and then compared against the wrong table.

**A receiver declared `class<Z>` reads its member on `Z`, and no table held `Z`.** `AmmoName.Default.MaxAmmo` is
`Ammunition.MaxAmmo`'s record, but the receiver's *record* is a `UClassProperty` whose kind word is `class`, the
class table (`class_properties.csv`) spells the declared type `Class<Ammunition>`, and the source's own
`var class<Ammunition> AmmoName;` / `local class < StargateWeapon > WeaponClass;` fell through both declaration
regexes in `_source_index`, which capture one type word and stop at the `<`. So `_class_of` answered `""`, the
member lookup never ran, and **34 bodies** refused. Three fixes, one idea:

| source | the record | why the walk wrote something else |
|---|---|---|
| `AmmoName.Default.MaxAmmo` (`var class<Ammunition>`) | `12 01 75 0a 05 00 04 02 5b 0f` | the class table's `Class<Ammunition>` is not a class *name* |
| `WeaponClass.Default.SnipingIdleAnim` (`local class < StargateWeapon >`) | `12 00 67 29 ...` | neither declaration regex reached past the `<` |
| `class 'Localizer' . default . sSelect` | `12 20 cc 01 05 00 00 02 d5 03` | a class literal names its class in the literal |

`_class_ref_class(word)` unwraps `Class<Z>` / `class < Z >` / `class.Z` to `Z`; `_class_of` consults it for a
parameter, a local, a property's artifact spelling, a **chain link** and a `class'X'` literal (package prefix
dropped); and a new source-side table `classreftypes` (`local`/`var class<Z> N`) carries the target for the two
declarations the old regexes could not see. `_member_record` unwraps its `target` on the *class-table* path too,
because a property's declared word arrives there as `class<ammunition>` and the records live on `Ammunition`.
`_is_class_ref` reads `classreftypes` first: `12 00 67 29` for `WeaponClass.Default.SnipIdleAnim` is a
`ClassContext`, and asking the record kind instead answered `19`.

**A bool member of the default object is read behind `BoolVariable`.** `Driver.default.bOwnerNoSee` is
`19 01 1b 06 00 04 2d 02 59 0a` (`Vehicle.ClientKDriverLeave`) and `InputClass.default.bEnableDodging` is
`12 01 53 1a 06 00 04 2d 02 7b 16` (`PlayerController.SetDodging`): the `Context`'s member is `2d`
(`BoolVariable`) and the `DefaultVariable` (`02`) *follows* it, so the skip word is the member read's wide length
(6) and the value size stays 4. The chain emitted `02 <idx>` alone with skip 5 -- the whole of six differences
(`Vehicle`/`KVehicle.ClientKDriverLeave`, `KVehicle.KDriverLeave`, `WeaponTurret.GunnerLeave`,
`PlayerController.PlayerSpidering.EndState`, and the bool half of `ShieldBelt.StopInvisible`).

**A cast is an object, not a reference.** `_is_class_ref`'s last test asked `_type_of(expr) == "class"`, and
`_type_of` answers `class` for *every* object cast (`Pawn(Owner)`), because the word covers both "a class" and "an
object of a class type". `Pawn(Owner).Default.Visibility` is `19 2e 83 01` -- a plain `Context` -- and writing `12`
there was all of `ShieldBelt.StopInvisible`. The test now skips a call expression; a bare class name, a class
literal and a `class<Z>` declaration still answer `class`.

**The gate** carries the three as controls: `classrefdefault` (`12 <local> 06 00 04 2d 02 <idx>` for a
`local class<Probe>` bool chain, taken from `SetDodging`), `castchain` (`19 2e <cast> 05 00 04 02 <idx>`, from
`StopInvisible`'s token) and `litdefaulttext` (`12 20 <idx> 05 00 00 02 <idx>`, from `GUIMessageBox.InitComponent`)
-- 92 synthetic bodies, all three corrupted deliberately and each named its row. The synthetic record table also
gained the `StrProperty` kind for `Caption` (a folded `IntProperty` added a `39 53` cast the shipped bytes do not
carry), and both member refusals gained a debug spelling (`SG1_DBG_MEMBER=1` names the receiver and the class it
resolved to), which is how the last five rows were counted by shape.

**What is left.** Five default chains refuse still: `FiredAmmunition.ProjectileClass` in `Controller.WarnTarget`,
`PlayerController.AdjustAim` and `BotEnemy.AdjustAim` (a member of a member inside the chain), `Chosen.PawnClass`
in `DeathMatch`/`StoryModeGame.SpawnBot` (same shape), and one more. The differ bucket's six **new** rows are
`class<Z>` chains whose member is now found and whose *shape* is one more rule:
`AirborneTurretController.GetAimingTargetSpeedOffset`, `AmbientCreatureManager.Timer`,
`GRENADE.CalcGrenadeLanding`, `Powerups.HandlePickupQuery`, `SemektetMortarGun.CalcGrenadeLanding`,
`WeaponPickup.BotDesireability`. **One body was lost**: `StargateHUD.ToggleGameHUD` (`local GameHUD G; ...
G.HideHud(!G.bHideHud);`) is now refused as *a member call statement to a name the receiver's chain does not
declare* -- the local's class resolves, the callee does not -- which is the next read on this chain, not a
measurement of it. The 17 *a receiver chain whose member is not one name* rows and the UWindow `Region` members
(a struct no class text declares) are untouched by this change.
## Stage 3, continued -- a local's type belongs to its function, and four struct widths the corpus pins (measured 2026-11-24)

**7,884 -> 7,977 modelled, all 7,977 byte-identical, 0 differing, 0 bodies changed** (93 gained; the residue
3,794 -> 3,701). Two joins, and both were tables keyed one scope too wide.

**A `local` declaration's type is its *function's*, and the class-wide table kept the first one.** `_source_index`
filled `localtypes`/`classrefs` with `setdefault((class, name), ...)`, so the first `local` of a name in a class
typed every other function that declared the same name: **43 classes, 66 colliding names** over the 4,637-file
corpus. The body this cost is named: `StargateHUD.ToggleGameHUD` declares `local GameHUD G;`, a later function of
the same class declares `local class<GameInfo> G;`, and `classreftypes` is what `_class_of` consults *first* -- so
`G.HideHud(!G.bHideHud)` joined on `GameInfo`, found no `HideHud`, and the body was refused as a member call to a
name the receiver's chain does not declare. `_local_decls(text)` reads the token stream once per class and
attributes every declaration to the function header above it, including the type written once for a whole
statement (`local vector LookDir, AimDir;` types both names; reading only the first segment's words typed the second
name as the empty string and cost 484 bodies, which is the rule's own control). The env then carries the class-wide
table **extended by** the function's own, so a name the class declares once is unchanged and only a real collision
moves. **73 bodies** gained, none lost: `Actor.IsInPain`, `Controller.AdjustToss`, `Pawn.AddInventory`,
`Pawn.NeedToTurn`, `PlayerController.PlayerMove` and the rest of the operator/member families the wrong word had
blocked.

**Four struct widths, each pinned by the bodies that refused for want of it.** `STRUCT_SIZE` carried `vector`,
`rotator` and `coords` (all 12); a struct-valued member whose artifact names any other struct stayed refused, which
was 24 `color` rows, 10 `region`, 8 `mousecursor` and 1 `plane`. The width is not a guess from the field list: the
emitted node writes the size byte *inside* the member read, so a wrong width puts everything behind it out of step
with the shipped body and the body can only match at the true width. `Color` is `4` (12 bodies: `ShieldEffect.
SetColour`, `StargatePlayer.SetBloomColour`, `UIProgressBar.ResetSkinColor`, `UWindowComboControl.Initialized`),
`Region` is `16` (`UWindowBase`'s four ints `X,Y,W,H`; 8 bodies: `UWindowWin95LookAndFeel.FW_SetupFrameButtons`,
`SB_SetupUpButton`, `Tab_SetupRightButton`), `Plane` is `16` and `MouseCursor` is `13` (`UWindowWindow`'s
`Texture tex; int HotX, HotY; byte WindowsCursor`) -- the last two are carried on the same evidence but no body
reaches them yet. **20 bodies** gained, none lost.

**A struct member's declared type is a record too.** `FiredAmmunition.ProjectileClass.Default.Speed` has a struct
receiver (`FireProperties`), and `_class_of`'s chain branch asked the class table for `projectileclass` on
`fireproperties`, got `""`, and stopped: `_struct_member` answers the same pair from the struct's own records and
`inventory/struct_members.csv` names the class (`class<Projectile>`). That fallback is in this bundle and reached
**no body yet** -- the five default chains still refuse (`Controller.WarnTarget`, `PlayerController.AdjustAim`,
`BotEnemy.AdjustAim`, `DeathMatch.SpawnBot`, `StoryModeGame.SpawnBot`), because `_struct_of` asks `_declared_type`
for the parameter's struct and `_struct_member` then finds no `(fireproperties, projectileclass)` pair in the
package scope. It is named here as the next read, not counted as a gain.

**What is left.** Those five default chains; the 43 `class<Z>`/chain rows; the six `class<Z>` differ rows from the
previous bundle; the 17 *a receiver chain whose member is not one name* rows; `HTMLStyle`'s width (no body pins it:
7 bools, an int, two colors and a string, and a string member has no fixed size); the 680 operator rows.

## Stage 3, continued -- two operator spellings the lexer split, and a parameter's struct (measured 2026-11-25)

**7,977 -> 8,136 modelled, all 8,136 byte-identical, 0 differing** (159 gained; the residue 3,701 -> 3,542).
Three joins, and two of them were a name that was never resolved rather than a shape the walk could not emit.

**`~=` and `^^` are two-character operators, and the lexer's list carried neither.** The engine's own table
declares them -- `native(124) static final operator(24) bool ~= ( string A, string B )` (`ComplementEqual_StrStr`)
and `Xor_BoolBool` -- and `tools/uscript-lex.py` listed `~~`, `@=`, `$=` but not `~=`, so `A ~= B` lexed as `~`
then `=`. The member scan met the `=` first and every body whose condition compares a member read
(`StoredChatPasswords[i].ChatRoomName ~= ChannelName`) refused as *a receiver chain whose member is not one name*
-- the residue's whole 17 rows: `PlayerController.SetChannelPassword`/`FindChannelPassword`,
`StreamPlaylistManager.FindNameIndex`, the four `StreamPlaylist.Find*`, `MaplistManager`'s three, `GUITabControl`'s
two, `ServerList.RemoveServer`/`UpdateServer`, `DeathMatch.ChangeName`, `CharacterSelectionList.SelectCharacter`,
`UDebugMapList.FindMap`. All 17 are modelled now, and the operator family the split hid came with them. The
lexer's `--check` now holds both spellings as one token each, because a gate that only tiles the input cannot see
a two-character operator that came out as two.

**A parameter's struct is in its own signature, and `_declared_type` only asked the property tables.** A body's
parameters are in neither `scope["locals"]` nor `scope["props"]`, so `FiredAmmunition` -- declared
`FireProperties FiredAmmunition` on `Controller.WarnTarget`, `PlayerController.AdjustAim` and
`BotEnemy.AdjustAim` -- read as the empty type, `_struct_of` answered `""`, and
`FiredAmmunition.ProjectileClass.Default.Speed` refused as *an unnamed class*. `paramtypes` (already in the env,
`{name: type}` for the body's own `decl_params`) is now the fallback when neither table has the name. It reaches
past the five chains: a parameter's declared type was also the missing operand type in the operator table, which
is why this one rule moved 100+ bodies.

**A cross-package member's import record is decided by its owner.** `Chosen.PawnClass.Default.ControllerClass`
(`DeathMatch.SpawnBot`, `StoryModeGame.SpawnBot`) resolves `PawnClass` to `class<Pawn>` and then wants
`Pawn.ControllerClass`: `Pawn` is Engine's, so in `StargateGame.u` it is an **import**, and that package imports
`ControllerClass` from more than one class -- the single-candidate rule answered none. The receiver's ancestor
chain is now the filter (`_import_owners` names each import's owner), and both bodies are byte-identical.

**`HTMLStyle` is 28 bytes wide, and no field sum gives that.** `UWindowBase` declares it as `int BulletLevel`,
`string LinkDestination`, two `Color`s and seven `bool`s. Every read of an `HTMLStyle` property in the shipped
packages is `Context 05 00 1c`: `UWindowHTMLTextRow(L).StartStyle` in `UWindowHTMLTextArea.DrawTextLine` at offset
`000d`, and seven more nodes across `GetWrapPos`, `RemoveWrap` and `WrapRow`. So the width is the compiler's own
static size for the struct and it is read from the bytes, not composed from the fields -- the same evidence rule as
`Color`'s 4 and `MouseCursor`'s 13. The four bodies still refuse (they read other members of the row first) and the
width is **not** in this bundle.

**What is left.** The three `FireProperties` chains: `_struct_member` maps a `ClassProperty` to `object` in
`PROP_TYPE` and then drops the declared word, so `FireProperties.ProjectileClass`'s `class<Projectile>` never
reaches `_class_of` -- one clause, and the width of the struct chain is already measured. Then the `class<Z>`
differ rows, `HTMLStyle`'s 28, and the 510 rows the operator table still refuses by the pair it cannot type.

## Stage 3, continued -- a record kind in a struct lookup, a chain link that is a class, and `HTMLStyle`'s 28 (measured 2026-11-25)

**8,136 -> 8,148 modelled, all 8,148 byte-identical, 0 differing** (12 gained, 0 lost, no new residue; the residue
3,542 -> 3,530). Three rules, each one comparison that asked the wrong table.

**A struct member's declared word is carried by its *record kind*, not by the mapped type.** `PROP_TYPE` answers
`object` for a `ClassProperty` -- a class reference is a pointer -- and `_struct_member` tested the mapped word
(`if ptype in ("struct", "array", "class")`) before returning the census's declared type, so
`FireProperties.ProjectileClass` (`class<Projectile>`) came back with an empty word and
`FiredAmmunition.ProjectileClass.Default.Speed` refused as *a default chain whose member the receiver's class does
not declare*. The test is the record kind now, and the two bodies it settles are `Controller.WarnTarget` and
`Weapon.CanAttack`; `PlayerController.AdjustAim` and `BotEnemy.AdjustAim` read the same chain and refuse further
on, which is the next read on this thread.

**A chain link can be the class reference.** `_is_class_ref` asked a bare name, a `class'X'` literal, a `class<Z>`
declaration and `_type_of(...) == "class"` -- and a **member** is none of those: `Chosen.PawnClass` is `Pawn`'s
`var class<Pawn> PawnClass`, so `Chosen.PawnClass.Default.ControllerClass` is a `ClassContext` (`12`) and the walk
wrote `19`. Seven bodies were *differing* on exactly that byte and none of them was refused, which is why the
promise "a wrong rule shows up as residue, never as a wrong body" holds only for shapes the walk can emit at all:
`DeathMatch.SpawnBot`, `AmbientCreatureManager.Timer`, `Powerups.HandlePickupQuery`,
`WeaponPickup.BotDesireability`, `GRENADE.CalcGrenadeLanding`, `SemektetMortarGun.CalcGrenadeLanding`,
`AirborneTurretController.GetAimingTargetSpeedOffset`. All seven are byte-identical now.

**`HTMLStyle` is 28 bytes wide and reads as `Context 05 00 1c`.** The width was measured in the previous bundle from
eight nodes (`UWindowHTMLTextRow(L).StartStyle` at `000d` in `UWindowHTMLTextArea.DrawTextLine`, and seven more
across `GetWrapPos`, `RemoveWrap` and `WrapRow`); it is in `STRUCT_SIZE` now, and `GetWrapPos`, `RemoveWrap` and
`WrapRow` are modelled. `DrawTextLine` still refuses earlier in its body, so the width is in the table and not yet on
every body that reads it.

All three are single comparisons; the measured artifact is regenerated and its census row carries the new totals.

## Stage 3, continued -- a class cast, a hole in an argument list, and a struct compare (measured 2026-11-25)

**8,148 -> 8,278 modelled, all 8,278 byte-identical, 0 differing** (130 gained, none lost; residue 3,530 -> 3,400).
Three rules, each a node the walk had no shape for; each carries a control in `--check` taken from a shipped body,
and each control was checked by mutating the emitted byte and watching `--check` fail.

**A cast to a class type is `13 <object ref> <expr>`, and its `<`/`>` are the cast's spelling, not comparisons.**
`class<Z>(expr)` is the group-13 node `--resolve` calls `MetaClassCast`, and its operand is an **object reference**
in the same table `2E` (`DynamicCast`) reads: `LocalMessage.GetString`'s `class<Actor>(OptionalObject)` is
`13 02 00 41 44` and `Controller.SetPawnClass`'s `class<Pawn>(DynamicLoadObject(...))` is `13 01 1c 9f ...`, where
`02` and `01` are `Actor` and `Pawn` -- exports 2 and 1 of Engine.u, in that order. That settles the old "which object
a `0x13` cast names" question for this token: the package's export/import tables, exactly as a `DynamicCast` names
its class (`--resolve` already reported all 129 operands naming a `Class`). The rule matters because `<` and `>` are
*live operators* in the same expression: before it, `Controller.SetPawnClass` refused as
`an operator ... (an unknown type > object)` and `LocalMessage.GetString` as `... (an unknown type != object)`. The
parse is tried before the operator table, `_operator_types` skips the span, and the node *types* as a class
reference, so `!=` resolves to the `(object, object)` row `_numeric` answers for a `class` -- `77 13 02 00 41 44 2a 16`.
**62 residue bodies spelled the cast and 20 of them are modelled now**; 42 still refuse, 11 of them on a compound
expression, and the shape behind most of those is the call it feeds (below).

**A hole in an argument list is written, not skipped: the compiler's `Nothing` (`0b`).**
`Controller.PostBeginPlay`'s `Spawn(PlayerReplicationInfoClass, Self, , vect(0,0,0), rot(0,0,0))` is
`... 16 | 0b 23 <vector> 22 <rotator> 16` -- one `Nothing` in the `SpawnTag` slot, so the callee's parameter count is
unchanged and the slot still holds a byte. **101 of the 130 gained bodies were refused on this reason alone**, and it
is the whole of `Controller.FindPostedAIMsg`'s `FindPostedMsg(, msgID)`. 102 residue rows still spell `, ,` and now
refuse further on (27 of them on *a call to a name the class chain does not declare*), which is what a rule that is
right looks like: the first refusal moves down the body instead of vanishing.

**A struct comparison is its own node: `32`/`33` + the struct's object reference, and no `16`.**
`UWindowHTMLTextArea.DrawTextLine`'s `(CurrentStyle.TextColor != OldColor)` is `33 8c 36 7c 02 ... 01 55 06`, where
`8c` is import 12 -- the `Color` struct -- of UWindow.u. The corpus carries **six** such nodes and all six name a
`Struct`: `Color` (one), `IpAddr` (one, `ClientBeaconReceiver.ReceivedText`) and `VoiceRef` (four, at export 160, in
`VoiceSelector.DiscardRecentlyUsedVoices`). The rule fires only where the pair table has nothing, and the table's four
color rows are `-`, `*`, `+` and `*` -- so `color != color` is the fallback, and the struct's own record is what the
node carries. It flips **no body yet**: the three bodies that hold one of the six nodes refuse earlier (DrawTextLine
on the `HTMLStyle` row it reads before the compare, the other two on their array elements), so this rule is gated by
a control rather than by a gained body, and the doc says so rather than counting it as work done.

**A `.static` call is the class object inside a `ClassContext` (`12`), and its skip word is the same wide length a
`Context` writes.** `class'AIMessage'.static.Assign(msg, NewMsg)` is `12 20 19 | 10 00 | 04 | 1c 56 07 01 7c 36 00 72 51
16` -- `ClassContext`, the class expression (`ObjectConst`, `20 19`), the skip word, the value size, then the call as
the node's second expression. Two things had made the word look unexplainable, and both are the `Context` rule the
emitter already carried: the word is **`_wide` of the member** (the compact length plus three bytes for every
one-byte index), not the compact length -- 10 -> 16 at `Speech.SetAIMsg`, 14 -> 22 at `AIMsgManager.AddReceivedMsg`,
whose inner call is one argument longer -- and the member is the *whole* call node including its arguments and its
`16`, not the callee alone. A census of the shipped packages settles it: **417 `ClassContext` nodes**, 113 of them
calling a function through an `ObjectConst` receiver (61 `VirtualFunction`, 52 `FinalFunction`), and the skip word
equals `_wide` of the member on every one of the 113 -- and on every `ClassContext` whose member is a property record
or a `default` read (skip 5, the `Context` constant). The four other receiver kinds the corpus writes (`LocalVariable`
36 nodes, `InstanceVariable` 24, `Context` 11, `StructMember` 12) follow the same measure, so the word was never a
different quantity: it was read against the compact length. **98 residue bodies spell the `static` keyword and 22 of
them are modelled now** -- `Speech.SetAIMsg`, `LocalMessage.GetString`, `PlayerController.Speak`,
`AIMessage.IsSubTypeOf`, `GameProfile.RegisterGame`, `HUD.ConnectFailure`, sixteen more -- and the 76 that still
refuse refuse on a *different* reason inside the same body (`a member that is not a property of the receiver's class`
18, `a call to a name the class chain does not declare` 18, `a call statement whose receiver's class the artifacts do
not name` 8), which is what a rule that is right looks like. `--check` carries the node as `classcall` (`12 20 04 09 00 00 1b 35 1f 78 00 16`) and
the refusal as `badstatic` (a class literal the package does not name), and the positive control was verified by
flipping its `12` to `19` and watching `--check` fail.

**A call's value size covers the kinds the corpus writes, and a `class<Z>` return is a class reference, not an
unknown.** `RETURN_SIZE` was the property table plus a rotator, so a member call whose callee returns a struct or a
class refused with `whose value size this walk does not carry`. The added pairs are read off the shipped node that
refused for want of each, one pair per body with a single call node in it: **`coords` -> 48** (10 bodies, e.g.
`StargateWeapon.GetAimingBoneCoords`, `Pawn.GetBoneCoords` -- four vectors, `Origin`/`XAxis`/`YAxis`/`ZAxis`; this is
*not* the 12 `STRUCT_SIZE` carries for a coords **property**, which is a different shape and stays as measured),
`color` -> 4, `font` -> 4 (a class, so a pointer: two bodies), `framehittest` -> 1 (a byte-sized UWindow struct).
Separately, the artifact spells a class-typed return three ways and only one of them reached the table:
`Level.GetGameClass()` is declared `class<GameInfo>`, so `PlayerController.Fire`'s `G = Level.GetGameClass()`
refused as `class.gameinfo` even though a class reference is pointer-sized (`PROP_SIZE`'s `class` is 4, measured over
31,622 `Context` nodes). Reading the word through `_class_ref_class` first settles `class<Z>`, `Class.Z` and `class`
alike. **15 bodies gained**: `class.weapon` 3 (`GameInfo.AddDefaultInventory`, `Mutator.GetDefaultWeapon`), `color` 4
(`GameInfo.AdminSay`, `PlayerController.ServerSay`, both `DrawHUD`s), `font` 3 (`ScoreBoard.GetLargeFontFor` and
`GetMediumFontFor`, `StargateTeamHUD.DrawTeam`), `class.gameinfo` 2 (`PlayerController.Fire`), `coords` 1,
`class.effects` 1 (`Pawn.PlayHit`), `framehittest` 1 (`UWindowFramedWindow.LMouseDown`).

Together: **8,314 modelled / 8,314 byte-identical / 0 differ** (from 8,278 / 8,278 / 0; 36 bodies gained, none lost;
residue 3,400 -> 3,364).

## Stage 3, continued -- one-row operators, the receiver's parameter list, and four return sizes (measured 2026-11-25)

**8,314 -> 8,342 modelled, all 8,342 byte-identical, 0 differing** (28 gained, none lost; residue 3,364 -> 3,336).
Three rules, each of them a lookup that asked a table where the language had already fixed the answer.

**A symbol the operator table installs one row for is typed by its symbol.** The table holds exactly one `&&` (130),
one `^^` (131), one `||` (132) and one `!` (129) declaration across all 18 packages, and every one of them is `bool`
on both sides -- so a side the walk cannot type cannot change the row. The refusal families that were waiting on it
were the head of the operator residue: 48 `bool && an unknown type`, 25 `an unknown type && an unknown type`, 15
`bool || unknown`, 12 `unknown && bool`, 9 `unknown || unknown`, 7 `unknown || bool` and 25 `an unknown type !`.
`_sole_rows()`/`_sole_unary()` read that census off `inventory/native_operators.csv` (a symbol with one row, not a
hand-written list), and the rule fires **only where a side is unknown**: a pair the table lacks for two *known*
types is still refused, because there the type is the measurement and not the symbol. `--check` drives
`_operator_types`/`_unary_types` directly for all four symbols, because the probe class has no operand that is
emittable and untyped at once.

**A member call's parameter list is the receiver's declaration, with no fallback to the body's chain.** `_params_on`
walked the receiver's chain and then, when the name was not declared there, the *body's* chain -- the fallback
`_rettype_on` had already lost for the return type. `HaakenWeaponShield.BeginState`'s `ShieldFX.Lower()` is a
no-argument `Lower()` on `HaakenWeaponShieldEffect` while the body's own chain declares `Lower(float)`, so the call
was refused as `0 argument(s) to a 1-parameter callee (0 required)`; that family carries 20 rows, and two more
(`0 argument(s) to a 2-parameter callee`, `... a 6-parameter callee`) are the same fallback. All 22 model now, and
`--check` holds the rule with a second class in the probe: `TargetH.Pong()` is a no-argument `Pong` on the class
`TargetH` holds while `Probe.Pong(int A)` is the body's -- a fallback refuses it.

**Four return sizes are read off the `Context` node that refused.** `plane` is **16** (`ShadowProjector.InitShadow`'s
`ShadowActor.GetRenderBoundingSphere()` is `19 01 62 16 06 00 10 1c 61 41 16`), `region` **16**
(`UWindowFramedWindow.Resized` and `UWindowMessageBox.BeforePaint` both write `19 01 06 07 00 10 1b 73 01 17 16` for
`LookAndFeel.FW_GetClientArea(Self)`), `box` **28** -- not the 25 a field sum of `box` gives
(`BotEnemy.SetNewHideNode` writes `19 01 14 06 00 1c 1c e7 0e 16`) -- and `array.string` **0**
(`GameInfo.GetMapRotation`'s `MaplistHandler.GetCurrentMapRotation()` is `19 01 68 1f 06 00 00 1b 50 0d 16`). Each
node checks itself: the skip word beside the size is `_wide` of the call (6, 7, 6, 6), so the byte read is the size
and not a neighbouring field. The earlier census could not pin these because it demanded a body with a *single* call
node in it; a `Context` writes the size for the call it holds regardless of what else the body does. Three bodies
gained; `box`'s moves on to an operator rule (`unknown - unknown`), and `plane`/`region` *member* records are still
open -- see below. `--check` holds `plane`/`region` (`takebox`, `takeregion`).

**What is left on this thread.** `UWindowFramedWindow.Resized` is now a **differ** row rather than a refusal: with
`region` at 16 its node matches `BeforePaint`'s byte for byte, but the body models 163 bytes against the shipped
165, because `R.X`/`R.Y` are read as a record (`36 de 01`) where the package writes `36 1f`, and the `39 3f`
`IntToFloat` in front of each is missing -- the gap is region's *member* records, not its size. `plane` has the same
member table ahead of it. `BotEnemy.SetNewHideNode` now refuses at `an unknown type - an unknown type` instead of at
the box return. Then the 42 `class<Z>(` rows that fail on their operands and the 246 operator rows whose pair the
table cannot type with *both* sides known.

