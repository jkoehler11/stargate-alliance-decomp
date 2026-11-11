# Class defaults: read from the packages, emitted back as `defaultproperties`

The roster link `map → spawner → squad script → roster` ends in a class default, and the compile loop (3.6)
needs defaults as an input. Two halves were needed: a reader that locates the block and names its properties,
and a renderer that turns each value back into the UnrealScript a compiler consumes.

    python3 tools/class-defaults.py DUMP/System --class SGOrder           # reader: names, types, values
    python3 tools/defaults-emit.py  DUMP/System --sweep -o inventory      # renderer: coverage artifact
    python3 tools/defaults-emit.py  DUMP/System --emit $WORK/defaults     # one .uc snippet per class
    python3 tools/defaults-emit.py  DUMP/System --merge $WORK/source-tree # or into the extracted tree
    python3 tools/defaults-emit.py  DUMP/System --verify                  # against the shipped text blocks

Values are game content. Neither tool prints a value unless asked (`--show`, `--walk`), `--emit`/`--merge` write
under `$WORK`, and what this repository carries is the count of what rendered: `inventory/class_defaults_emit.csv`.

## Reading: three defects, all fixed

| defect | symptom | fix |
|---|---|---|
| `Str` (type 13) read as a name index | `Description` decoded as the *name* `BeginState` | read it as an FString: `size` bytes of text |
| block required to end exactly at the export end | 73 of 306 `StargateGame` classes had "no block" | allow a bounded trailer (≤ 64 bytes) after the terminator |
| super chain followed within the package only | inherited properties invisible, so correct blocks looked undeclared (18 of 306 "fully declared") | follow `inventory/classes.csv`'s super map, which is cross-package |

Two more came out of rendering the values, and both were invisible while only names were read:

* **The FString's length prefix counts the terminating NUL**, and the NUL itself is only stored when the declared
  size has room for it. Decoding from the length byte made `Description` print as `\x0cEmpty Order` — the value
  was right, one byte too early. `tools/prop-stream.py` now has one `read_fstring` for it, which the T3D path uses
  too.
* **An imported object reference was rendered with its two names swapped.** The import entry stores
  `ClassPackage`, `Class` and `ObjectName`; `resolve_ref` printed the class package where the class belongs, so a
  texture came out as `icon_mission'Engine'`. It resolved, so it looked decoded. Now
  `Texture'Engine.icon_mission'` — the form the game's own T3D writer emits and the shipped source uses.

## Coverage, all 18 packages

`inventory/class_defaults_coverage.csv` has the per-package rows (`make defaults-coverage`; `--coverage` in
`tools/class-defaults.py` reproduces the committed file byte for byte, which it did not have a generator for
before this change):

| | classes | blocks found | every property declared |
|---|---:|---:|---:|
| `StargateContent` | 567 | 557 | **457** |
| `Engine` | 367 | 310 | 195 |
| `StargateGame` | 306 | 293 | 195 |
| `GamePlay` | 262 | 261 | 182 |
| `Sharpeffects` | 158 | 157 | **156** |
| `GUI` | 148 | 144 | 132 |
| `StargateVehicles` | 142 | 141 | 124 |
| `StargateMenus` | 98 | 86 | 66 |
| `UWindow` | 78 | 78 | 12 |
| others (9 packages) | 193 | 182 | 145 |
| **total** | **2,319** | **2,209** | **1,664 (71.8%)** |

### The scan window was the biggest single defect

The locator used to search the first **512 bytes** of an export for the block. A class export carries its record
(script tables, super, package dependencies) *before* its defaults, and the record of every base class is longer
than that, so the classes with the most defaults were exactly the ones reported as having none:
`Actor` (record 2,451 bytes), `Pawn` (3,992), `PlayerController` (13,665), `GameInfo` (5,682), `Weapon` (2,580).
Scanning the whole payload recovers **114 classes**, takes blocks found from 2,084 to **2,209**, fully-declared
blocks from 1,552 to **1,666**, and classes with no block from 235 down to **110** — and the recovered set is the
inheritance spine of the whole tree, which is why the emitted property count jumps from 13,307 to 15,491. (Those are
the values of that change; the current ones are 1,664 and 15,517, because two blocks were later withheld for
contradicting their own property record — see "The locator scores names first and tag types second".)

Where it still fails:

* `UWindow` (12 of 78) and `Fire` (0 of 7) are the worst. `UWindow`'s classes are engine-heavy GUI types whose
  declared properties mostly come from `GUI`/`Core` parents; `Fire`'s are fractals with almost no declared
  properties, so "every property declared" is the wrong test there — they may well be correct blocks scored
  harshly.
* **110 classes have no usable block, and the trailer bound is not why.** `make defaults-misses`
  (`tools/class-defaults.py --misses`, `inventory/class_defaults_misses.csv`, `--check-misses` in `make ci`) classifies
  every one of the 655 classes that are not fully declared, against the committed per-class artifact:

  | reason | classes | what it means |
  |---|---:|---|
  | `single-declared` | 263 | the payload's tail is a one-property block whose name the class declares, and no parse holds two declared names — the floor-of-one measurement below |
  | `incomplete` | 252 | a block was chosen, but it holds names the class does not declare — the tie below |
  | `type-contradiction` | 30 | a block was chosen whose names are declared, but at least one tag's type the declared kind cannot carry (below) |
  | `empty-block` | 109 | the payload parses a valid **empty** block, and no offset anywhere yields a parse holding even two of the class's properties |
  | `single-property` | 1 | only one-property blocks parse |
  | `trailer-over-64` | **0** | no class is rejected by the 64-byte bound |
  | `no-block-parses` | **0** | every class parses *something* |

  `single-declared` is new and it is a *reason*, not a located block: the `property` column names the tag a
  floor of one would have promoted the class on, and the tag is usually the payload's own tail rather than its
  block. Nine of the 263 name `VoiceSets` — the nine packs that set the `array<VoiceSet>` — and
  `tools/voice-sets.py` reads that same array independently, so *those* rows are a real one-property block. `ActionString` is the tag for **53** of them, which
  is a menu-table tail shared across classes, and `VoicePack` itself would be "fully declared" on `LifeSpan` out of
  a 108-byte payload that holds no `defaultproperties` at all. The 24 `empty-block` and 24 `type-contradiction`
  rows that also end in a declared one-tag parse keep their reasons: an empty block parses at offset 0, so those
  payloads do hold a block-shaped structure the single tag does not explain, and a contradicting block is a
  different finding.

  This corrects an earlier reading of the same data. The first pass labelled 48 of these `trailer-over-64`, because it
  asked only "what was the best parse and how far did it end from EOF" — and a 1–2 property parse 90 bytes from the end
  looks like a block cut short by the bound. Asking the stronger question, "does any parse contain a property this class
  *declares*", gives 0 or 1 for all 110: there is no block being truncated, so raising the bound would recover nothing.
  The `< 2` and `≤ 64` guards are innocent here.

  What the 109 actually look like, measured: an empty block parses at offset 0, followed by a **uniform 80–95-byte
  remainder** (`Object` 81 bytes, `Locale` 91, `Subsystem` 92) that is not decoded. Two readings survive and this page
  does not choose between them: the classes set none of their declared properties (so a rebuild emits an empty
  `defaultproperties` and loses nothing), or their block has a shape `read_block` does not parse and the remainder is
  where it lives. Settling it needs that remainder decoded, or a stock `ucc` build of one of these classes to compare
  against — neither of which this project has.
* **Dropping the two-property floor was measured, and it is why the guard is two.** A floor of one promotes **320**
  classes to "fully declared" (1,664 → **1,984** of 2,319), and every one of those verdicts rests on a single tag.
  That tag is not evidence that a block was found: **1,477 of the 1,664** classes whose block is *already* located
  carry the same shape — a one-tag parse of a declared property ending exactly on the payload's last byte
  (**1,778 of 2,319** payloads do), because the last tag of any real block, read from its own start, is that. The
  promotions are also wrong where the payload is known: `ActionString` is the single tag for **53** of the 320,
  `VoicePack` would be "fully declared" on `LifeSpan` out of a 108-byte payload, and for **19** classes the promoted
  tag *replaces* a two-property candidate that names **two** declared names, because the score's declared fraction
  (1/1 against 2/3) outranks the count. A false block is worse than a miss — it would emit wrong defaults where the
  compiler would otherwise leave the class alone — so the floor stays at two and `single-declared` names the residue.
  Reproduce with `python3 tools/class-defaults.py --packages DUMP/System --floor 1 -o $WORK/floor1`, which
  reports 1,984 fully declared against the committed 1,664.
* 430 classes are ambiguous — ties between blocks with equally declared names (`UWindow` alone is 45 of them),
  broken by preferring the longer block and then the smaller trailer, which is a rule of thumb, not a proof.

## Emitting: 15,517 of 15,521 properties render, plus 1,313 subobjects

`inventory/class_defaults_emit.csv`, per package, over the **fully-declared blocks only**:

| package | classes | blocks | properties | rendered | | subobjects |
|---|---:|---:|---:|---:|---:|---:|
| `StargateContent` | 567 | 457 | 5,009 | 5,009 | 100.0% | 350 |
| `Engine` | 367 | 195 | 1,822 | 1,821 | 99.9% | 9 |
| `StargateGame` | 306 | 195 | 1,753 | 1,753 | 100.0% | 89 |
| `GamePlay` | 262 | 182 | 1,656 | 1,656 | 100.0% | 0 |
| `StargateVehicles` | 142 | 124 | 1,559 | 1,559 | 100.0% | 41 |
| `GUI` | 148 | 132 | 1,152 | 1,151 | 99.9% | 46 |
| `Sharpeffects` | 158 | 156 | 1,101 | 1,101 | 100.0% | 467 |
| `UDebugMenu` | 38 | 31 | 384 | 384 | 100.0% | 0 |
| `Stargateeffects` | 77 | 69 | 367 | 367 | 100.0% | 172 |
| others (9 packages) | 254 | 123 | 718 | 716 | 99.7% | 139 |
| **total** | **2,319** | **1,664** | **15,521** | **15,517** | **99.97%** | **1,313** |

What renders, and what each rendering was checked against:

| type | count | rendered as |
|---|---:|---|
| `Float` / `Int` / `Bool` | 7,627 (3,778 / 1,322 / 2,527) | `1.0`, `-1`, `True` |
| `Name` / `Str` / `String` | 3,548 (1,785 / 1,763) | bare when the text is identifier-shaped, quoted when it holds a space or punctuation |
| `Object` / `Class` | 1,670 | `Class'Name'` inside the package, `Class'Package.Name'` across one |
| `Struct(Vector/Rotator/Color/Plane)` | 919 | `(X=..,Y=..,Z=..)`, `(Pitch=..,Yaw=..,Roll=..)`, `(R=..,G=..,B=..,A=..)` |
| `Byte` | 1,052 | an enum element name where the enum can be resolved (771 of them), the number otherwise |
| `Array` | 484 properties, 1,502 elements | one line per element: `DiallingSounds(0)=Sound'Engine.DHD1'`, and inline inside a struct: `Points=((InVal=0.0,OutVal=1.0),…)` |
| other structs | 219 properties | a nested tagged block: `(MeshName=..,FaceTextureName=..,MeshSectionIndex=5)` |

Arrays and the remaining structs are decoded from **UELib's own defaults deserialiser**
(`tools/uelib/src/Core/Classes/UDefaultProperty.cs`, pinned in `tools/versions.lock`) instead of from the bytes,
which is the difference between a guess and a reader: an array is a **compact-index count followed by that many
untagged elements**, their width coming from the *inner* type the property record names (`array<Sound>` — and
`inventory/class_properties.csv` already carries that name), and a struct that is not one of the four atomic
layouts is a **nested tagged block**, `Field=value` pairs terminated by `None`.

**An array of structs is one nested tagged block *per element*, and that is what the last two sessions got wrong.**
The reader treated the whole value region as a single tagged struct, so `array<FaceInformation>` could only decode
when the count was 1: each element ends at its own `None`, and requiring the *region* to end there made every
multi-element array a refusal. Elements are now decoded one at a time — `render_tagged_struct(…, exact=False)` returns
the offset after the element's terminator — and the array loop still has to land exactly on the tag's declared size,
so a wrong element type is a **refusal** and not a short element. The same change closes `InterpCurve`, whose
`Points` member is an array of tagged structs:

    WheelLongFrictionFunc=(Points=((InVal=0.0,OutVal=0.0),(InVal=100.0,OutVal=1.0),(InVal=200.0,OutVal=0.7)))
    Face(0)=(MeshName=sg1_anim.Tokra_Generic,FaceTextureName=TokraTex.Generic_Tokra_Face_01_S,MeshSectionIndex=2)

**An array inside a struct is written inline and positionally** — `Points=((InVal=..),(InVal=..))` — because
`UDefaultProperty` renders arrays that way under its `WithinStruct` flag; only the top level of a
`defaultproperties` block gets `Name(i)=` lines. That also meant the nested form needed the *array* path to return a
formatted string rather than its top-level `(index, text)` pairs, which is what had been printing a Python list.

**A nested array has no metadata here, so its element type comes from the tag stream.** `inventory/class_properties.csv`
covers *class-owned* properties only, so `InterpCurve.Points` names no inner type anywhere in this repository. The
elements of a struct array are tagged, i.e. self-describing, so the attempt is still a read — the tiling test is what
turns a wrong guess into a refusal — and the field names it produces (`InVal`, `OutVal`) are consistent across all
twelve instances.

Every array decode has to tile its own value region exactly — count and element widths must land on the tag's
declared size — so a wrong inner type is a **refusal**, not a short element. That is what turns a guess into 484
decoded and 2 refused, and it is also what caught the defects below.

What is left over, all counted per block in the CSV: **1 array**, **0 structs** and **3** properties with a malformed
value — 0.03% of the class-level block contents, down from 0.26% two changes ago. The array is
`Engine.TextToSpeechAlias.Aliases` (`array<SpeechReplacement>`, 511 bytes), which still does not tile; the three are
delegate tags typed `Invalid`/`FixedArray` in `GUI.MenuItemOverlay`, `StargateMenus.ExtrasPage` and
`StargateMenus.CreditsPage`. A delegate's default is written by the engine rather than by the tag grammar, so the
type-agreement check below deliberately leaves `UDelegateProperty` unconstrained and these three stay counted rather
than explained away.

### The locator scores names first and tag types second

A name score cannot tell a class's block from a parse that starts inside the record in front of it. `KConeLimit`'s
chosen candidate held all four declared names and typed `CollisionHeight` (a `Float` property) as a 16-byte `Float`
and `PhysicsVolume` (an `Object`) as a `Float` — pairings a real tag stream never produces. `tools/class-defaults.py`
now counts, per candidate, how many tags carry a type the class's own property record forbids (`TYPE_AGREEMENT`), and
`choose()` sorts on that **after** the declared-name fraction:

    score = (declared fraction, bad == 0, property count, -trailer)

The map is measured rather than assumed. A `Class<X>` property serialises as an **object reference** in this build —
734 tags in otherwise-clean blocks carry type `Object` for a `UClassProperty` — so `UClassProperty` accepts
`{Class, Object}`; taking the tag type to be `Class` alone demoted **419** clean blocks and cost 3,800 properties.
`UDelegateProperty` and `UPointerProperty` stay unconstrained: the engine writes a delegate's default rather than the
tag grammar, and a pointer carries no value in the block.

Measured on the dump: the residue goes **11 → 4** values, clean blocks **1,666 → 1,664** (two classes are withheld
rather than emitting a block that contradicts their own record: `StargateMenus.SelectPawnPanel`,
`StargateMenus.SelectTeamPanel`), properties **15,530 → 15,521** (the nine impossible tags are gone) and rendered
**15,517 (99.97%)** — the six classes named in the previous note are down to one array and three delegate tags.

**What was deliberately not shipped, measured: ranking type agreement *first*.** Putting `bad == 0` at the head of
that tuple instead promotes **535 withheld classes to clean blocks** — 2,201 of 2,319, 16,764 properties (against
15,521), 272 unrendered values — because those are exactly the classes whose candidate held undeclared package names,
and the type check picks a shorter candidate that agrees. That is a claim that those 535 blocks *are* the classes'
blocks, and nothing here checks it, so it stays a measurement rather than a change.

## Subobjects: 1,317 `Begin Object` blocks

An `editinline` property or array element does not point at a class or a texture: it points at an **export whose
outer is the class being described** — a subobject the compiler expects to be declared inside the same
`defaultproperties`, before the assignment that names it:

    Begin Object Class=SpriteEmitter Name=SpriteEmitter0
        ... that subobject's own defaults, 12 to 40 properties ...
    End Object
    Emitters(0)=SpriteEmitter'SpriteEmitter0'

The emitter renders those blocks recursively, using the same locator on the subobject's own export: **1,313
subobjects carrying 15,798 properties, of which 15,790 render**, so a class's defaults are 15,517 + 15,790 =
**31,307 rendered properties** in total. Subobjects gained the most from the array fix (15,010 → 15,802, before the
type check withheld a few blocks): a particle system's own defaults are full of struct arrays. Without the declaration a reference names an object the compiler has never
seen; that is why they are part of this pass and not a cosmetic extra.

**A correction.** The previous PR reported these 270 arrays as "`editinline` arrays whose elements are serialised
inline object *instances*, a stream this reader does not parse". That was wrong twice: `editinline` is a *flag in
the property's type string* (`array<export editinline ParticleEmitter>` — two flags, and stripping one left
`editinline ParticleEmitter` as a "type"), and the elements are ordinary compact object references. Nothing about
the byte format was missing; a string-stripping bug was reported as a format gap. The count is in this table, and
the earlier claim should not be quoted.

## What the 545 withheld classes are, measured

`tools/defaults-emit.py --explain` reports the residue without printing values. Three outcomes per class:

| outcome | classes | payload median | p90 | under 256 bytes |
|---|---:|---:|---:|---:|
| block accounts for itself (emitted) | 1,664 | 159 B | 570 B | 1,245 |
| candidate that does not account for itself (withheld) | 545 | 112 B | 279 B | 476 |
| no candidate at all | 110 | 103 B | 270 B | 98 |

Of the 545, **228 have exactly one unaccounted name and 317 have two or more**; there are 1,016 unaccounted name
slots in total, and **30 of them have no unaccounted name at all — their block contradicts its own property
record instead** (see the type-agreement section). The unaccounted names are dominated by **package names** —
`Core` 251, `System` 189, `Engine` 73, `Lighting` 69 — followed by names that are struct *fields* elsewhere in the
tree (`Range` 31, `Subject` 25, `LightColor` 18, `Movement` 14, `GenericType` 13, `Type` 11). Package names cannot be property names, which is the useful part:
a candidate that parses a package name as a property is reading a record, not a tag stream. The plausible reading
is that these payloads hold a small table (the bytes between the names are hash-like 4-byte values that repeat
across classes: `b31d40ff`, `2c08ad6c`, `5911e68c`) and that the class sets no defaults at all — but the same
signature could also be a struct block belonging to a component table, so it is **not** settled.

Three explanations have been tested and rejected, which is why the residue stays labelled rather than explained:

* **the 64-byte trailer bound** — relaxing it to 4,096 bytes recovers **2** classes and 4 properties;
* **missing property metadata** — `inventory/class_properties.csv` covers **all 11,493** class-owned property
  exports in the dump (0 missing), so an unaccounted name is not a property we failed to inventory;
* **payload size** — clean payloads are also small (median 159 bytes, minimum 100), so a small payload is not a
  class without defaults.

What would settle it: the reader of that table in the XBE (the same route `docs/NATIVE_PROPERTIES.md` took for the
tag format), or a class whose defaults are known from the shipped text. The shipped text cannot help — it retains a
`defaultproperties` block for only two classes, and neither agrees with its package (below).

## Where a class payload stops being a record

`--misses` calls a class `empty-block` when the payload parses a valid *empty* block somewhere and no offset anywhere
carries two of the class's declared properties — a reason and not a measurement: the payload may be a record and
nothing else, or a record followed by a stream the locator failed to read. The first clause holds for *every* class
payload (each ends in `00`, so an empty parse at the last byte is free — see below), which leaves the second as the
real content of the reason. `tools/class-record.py` splits the two with two indices that come out of the package's own
export table, so neither rests on a guessed field order:

* **`Super` is the record's first field, as a compact index.** Each export row carries its own `super`, and the value
  the payload opens with must be that number (`-n` = import `n`, positive = export). It agrees on **2,319 of 2,319**
  class exports — one counterexample would sink the claim, and there is none.
* **`ScriptText` follows it**, as one compact index pointing at the `TextBuffer` export outered to that class. The cook
  strips the field chain (`Children` is 0) but keeps the script text, so the buffer is exactly one export away: found
  at **+2 for 1,451** classes and **+3 for 868** — immediately after `Super` (one or two bytes) and the zero `Children`
  byte — for **2,319 of 2,319**.

The record's *end* is measured, not derived. Where the locator found a block, that block's start **is** the record's
length. The length tracks the class's size — the correlation with the number of `Function` exports the class owns is
**0.80** over 2,189 blocks (none: **92-byte** median; 1–5: **123**; 6–20: **288**; 21–60: **670**; more than sixty:
**3,410**), and with its `Property` exports **0.67** over the same blocks. That is a correlation and not an identity:
nothing here decodes the record's body, so it says the record grows with the class and not that a field per function
sits in it.

| measure | value |
|---|---|
| class exports | 2,319 |
| a block located (`located`) | 2,209 |
| record lengths (blocks starting at byte ≥ 4) | 2,189: 8 .. 13,665 bytes, median 96, p90 401 |
| modal record | 92 bytes, 357 blocks (431 distinct lengths) |
| records at or below 100 bytes | 1,254 of 2,189 (57%) |
| candidates starting inside the prefix | 20 (5 at byte 0, 15 at byte 1) |

Those 20 are not record lengths — a candidate that starts at byte 0 or 1 begins inside `Super`/`Children`, so it is
reading the record's own prefix as a tag stream, which is the `empty-block`-at-offset-0 shape the section above names.
Keeping them apart is what makes the rest of the distribution a record length.

The payload's shape then follows from its length: `located` (a block was found), `record-only` (no block, and the
payload is short enough that a record alone accounts for every byte) and `long-no-block` (no block, and bytes after
the record are unexplained). Joining the shapes against `inventory/class_defaults_misses.csv`'s reasons moves nothing
but the bucket it explains:

| reason | shape | classes |
|---|---|---:|
| `empty-block` | `record-only` | **50** |
| `empty-block` | `long-no-block` | **59** |
| `single-property` | `long-no-block` | 1 |
| `incomplete` | `located` | 252 |
| `single-declared` | `located` | 263 |
| `type-contradiction` | `located` | 30 |

**The 50 `record-only` payloads are 81–100 bytes** — inside the record-length distribution, and inside the range where
1,254 located classes *do* have a block after their record. For these fifty no candidate accounts for the bytes after
the record, so "this class sets no defaults" is a reading of the payload rather than a parse failure. Their four-byte
tails are the record's own and dominated by `83 03 00 00` (28 of 50), `89 06 00 00` (4) and `03 03 00 00` (3).
**The 59 `long-no-block` payloads are 101–1,396 bytes (median 125)**: a record accounts for the front of each and the
remainder is still a locator miss. That is the residue, and it is 59 of the 109 `empty-block` rows rather than all of
them.

The shortest way to close it would be to read each payload's last byte as the terminator of a zero-tag stream, which
would put the record at `payload-1` bytes on all 109 (80–1,395, median 102; the 50 `record-only` 80–99, median 94,
the 59 `long-no-block` 100–1,395, median 121). **Measured over the whole corpus, that is not available.** Every one
of the 2,319 class payloads ends in `00`, and a one-byte empty tag stream — a name index of 0 — parses at `payload-1`
on 2,319 of 2,319, *including the 2,209 whose record end a located block already fixes at a much earlier byte*. A
predicate that holds on every payload in the population cannot locate an end: it is the trailing zero, not a record
boundary. `inventory/class_records.csv`
therefore carries `last_byte` and `empty_at_end` beside `block_start` and `shape`, so the control sits on every row
where the true end is known, and a later session can read the saturation instead of re-deriving the guess.

What would settle the 59: a start rule that does not require the tags to reach the payload's last byte, or the runtime
reader of the trailing table (`docs/NATIVE_PROPERTIES.md`'s route for the tag format). What would falsify the 50: a
class among them whose payload decodes as a record shorter than its length, which would put unexplained bytes behind a
short record — nothing here decodes the record's body, so that check is open.

### The start rule is measured, and it says the residue holds no multi-tag block

`tools/class-record.py` now also anchors a block **on its tags alone**: a start is an offset past the record prefix
where a tag's name index resolves to a property the class or an ancestor declares, and a clean parse runs from there
with no invented name. Nothing in the test reads the payload's last byte, which is what the residue asked for. A real
block's every suffix parses too, so the candidate with the most properties is its front.

The rule is scored against the 2,209 located block starts rather than trusted: it anchors **1,963 of the 2,319**
payloads and reproduces **1,628 of the 2,209** located starts (73.7%). The miss is not a parse failure: the four
sampled (`Core.Time`, `Engine.Material`, `Engine.SavedMove`, `AmbientCreatures.AmbientCreature`) have a located start
whose tags decode to `Core`, `System`, `<` and `Lighting` — package and operator names, not properties — so those
starts are a shape the tag rule does not confirm, and the 73.7% is agreement with the locator and not a correctness
bound. Either way the rule cannot replace the end boundary, and the doc keeps the locator.

Applied to the residue it is decisive in the other direction:

| shape | no anchor | one tagged property | two or more tags |
|---|---:|---:|---:|
| `long-no-block` (60; 59 of them `empty-block`) | **27** | **33** (14 end on the payload's last byte, 19 with a 66–478-byte trailer) | **0** |
| `record-only` (50) | **45** | **5** (3 trailer ≤64) | **0** |

**Not one of the 60 `long-no-block` payloads holds a block of two or more tags.** A rule that ignores the payload's
end therefore finds nothing the locator could have missed there: the payload's tail is either a single named tag or
no tag of the class at all. The 14 whose single tag ends exactly on the last byte are the same shape the
`single-declared` reason already carries (263 classes), where one tag cannot be told from a table tail; the 19 with a
trailer hold a tag followed by 66–478 bytes that no tag stream explains; the 27 name no declared property anywhere in
the payload, so their tail is record body by elimination — the payload shape, not the locator, is what is left.

`inventory/class_records.csv` gains `anchor_starts`, `anchor_start`, `anchor_props` and `anchor_trailer` on all 2,319
rows, `--report` prints the four counts above, and `--check` covers the rule on a hand-built two-tag payload (a class
that declares nothing claims no start).

### The located start opens with a property tag on 1,687 of the 2,209 payloads

The same declared-name table reads the locator's own `block_start` as a tag, which every "located" count in this page
rests on: **1,687 of the 2,209 located starts carry the name of a property the class declares, and 522 carry something
else** — a package (`System` 121, `Core` 89, `Engine` 41), a state (`Lighting` 38), a struct (`Range` 29), a class
(`Gameplay` 15) or a function (`GetMeshName` 5). A tagged property's name is a property, so those 522 starts are not
property blocks; they are the payload's own footer read as one. `Engine.Material` is the sample: its 118-byte payload's
"block" is the last nine bytes, parsing as `System` + `DefaultMaterial`, and the four neighbouring offsets parse to the
same end with `Core`, `Engine`, `Object` and `?` instead — a block whose every byte is in question.

The split against the reasons is exact and is the finding: **all 263 `single-declared` rows and all 252 `incomplete`
rows are among the 522** (the rest: 4 of the 30 `type-contradiction`, and 3 classes this page calls fully declared). So
those two reasons do not mean "a block the renderer could not match" — their input is a parse of record bytes, and the
`--verify` gaps this page records for them are the renderer being right about a block that is not there.

`inventory/class_records.csv` gains `block_first_name` and `block_first_declared` beside `anchor_*`, `--report` prints
the 1,687 / 522 split, the six names behind it and the per-reason breakdown, and `--check` covers the read with a
declared name, a non-property name and an offset past the payload.

## The shipped text is not an oracle for defaults

`--verify` compares the renderer against the `defaultproperties` blocks the shipped script still carries. It finds
two, and neither agrees:

| class | properties in the shipped text | emitted from the package | matched | differ |
|---|---:|---:|---:|---:|
| `Core.Locale` | 50 | 0 | 0 | 0 |
| `GUI.SGVertScrollBar` | 13 | 2 | 1 | 1 |

`Locale`'s class export stores no defaults block at all. `SGVertScrollBar` stores two of the thirteen its text
lists; `RenderWeight` matches exactly (50.0) and `WinWidth` does not — the text says `0.02`, the package stores
`20.0`, and the bytes there are an exact float either way (`00 00 a0 41`). Three explanations fit and none is
measured: the embedded text is a later revision than the compiled defaults, the packaging dropped assignments it
considered redundant, or the two are in different units. What follows does not depend on which: **the packages
are the authority for a rebuild**, and the shipped text cannot validate this renderer. The renderer's evidence is
therefore structural — every value comes from the same tag/decoder the map T3D pass uses, which is checked against
73 maps — plus the property-name agreement the coverage table already measures.

## A class reference names the package it lives in, not its metaclass

The emitter used to write a foreign class reference as `class'Core.<Name>'`, and `Core` is exactly what an
import's `class_package` field holds for a **class**: it is the package of the *metaclass* (`Class` lives in
`Core`), not of the class. The class's own package is the import's Outer, a signed index this reader already
had. Measured 2026-11-11 over the whole dump: of **885 emitted class references, 183 named a package the class
is not in** before the fix and **2** after -- both the same case difference (`Gameplay` in the name table,
`GamePlay` in `inventory/classes.csv`).

`render_ref` now takes the package from `outer_package()`, which resolves the signed Outer and accepts a name
only when that object really is a `Package`; a nested object falls back to the old field, which is right for
it. `tools/weapon-links.py` is the consumer that scores it: all 219 distinct class references the weapon tree
names resolve in `inventory/classes.csv`, and the emitted text now agrees with that table on all 315 edges.

## Wiring defaults into the source tree

`make extract-script` writes 2,319 classes as `$WORK/source-tree/<Package>/<Class>.uc`, and only 15 of them carry a
`defaultproperties` block. `make defaults-tree` runs the emitter over that tree and appends each block where the
compiler expects it — end of class — replacing any block already there, so re-running is idempotent. The result is
one tree with text and defaults for **1,552 of 2,319 classes (67%)**: the shape 3.6 needs, short of the classes
whose block could not be located or does not account for itself.

## Not measured

* **Whether the 535 promoted blocks are real.** Ranking tag-type agreement first promotes 535 withheld classes to
  clean blocks (2,201 of 2,319, 16,764 properties) — measured, not shipped, as the section above describes. Those
  classes' candidates held undeclared **package** names, and the type rule chooses a shorter candidate that agrees
  with the record; nothing here checks that the shorter block is *complete*. What would settle it: a per-class audit
  of the chosen block against the properties the class is documented to override, or the runtime reader of the
  trailing table (the route `docs/NATIVE_PROPERTIES.md` took for the tag format).
* **The last 4 values.** `Engine.TextToSpeechAlias.Aliases` — a 511-byte `array<SpeechReplacement>` that does not
  tile under any element width tried — and three delegate tags typed `Invalid`/`FixedArray` (`GUI.MenuItemOverlay`,
  `StargateMenus.ExtrasPage`, `StargateMenus.CreditsPage`). Delegates are outside the type map by design, so whether
  those three blocks are misparsed is not measured; a delegate value's real layout is what would settle it.
* **The 545 withheld blocks.** Their candidate parses a package name as a property, so it reads a record and
  not a tag stream — but whether that record means "this class sets no defaults" or is a component table is not
  settled (see the section above for the three explanations already tested and rejected).
* **Whether a compiler accepts the output.** Nothing here has been through `ucc`: the text is shaped like the
  shipped source (verified character by character on the two blocks above that do agree), not compiled. 3.6 is
  where that gets tested.
* **The roster itself.** The squad scripts maps name (`TSS_7A_B_Jaffa_01`, `AI_Jaffa_Horus_Med_single`) appear in
  those packages' name tables but are neither imports nor exports, so the reference to them lives in data inside
  the `Level` export, which this reader does not parse.
