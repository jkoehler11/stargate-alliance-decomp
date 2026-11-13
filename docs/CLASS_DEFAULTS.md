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

**The record's tail has an owner now (2026-11-12).** `UClass::Serialize` (`0x0003a800`) is read in
`docs/NATIVE_VM.md`: after `UStruct`'s fields it writes a `TArray` of 12-byte records at `+0x4d0` and two
`TArray<dword>`s at `+0x4dc` and (version > `0x62`) `+0x4f4`, and only then the defaults, which a vtable slot reads
into a buffer sized from the class's own `GetPropertiesSize()`. So the "small table (the bytes between the names are
hash-like 4-byte values that repeat across classes: `b31d40ff`, `2c08ad6c`, `5911e68c`)" above is the record's own
arrays, not a component table belonging to the defaults: a scan that starts inside one reads an array element as a
name index and the next byte as a tag type, which is exactly what "a candidate that parses a package name as a
property" looks like. Each class's record length is still not measured, so this does not relocate the 545 by itself --
but it fixes the direction: the block is the last thing in the payload and ends on the payload's last byte
(`inventory/class_records.csv`'s `last_byte` is `00` for all 2,319 class exports), so it is read **backwards** from
that terminator, not forwards from a candidate start.


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

## The bytes in front of a block start, measured

`tools/class-tail.py` reads the four fields `UClass::Serialize` writes *before* the defaults (`docs/NATIVE_VM.md`)
from the other side: for every candidate start it records what sits at `start-1`, `start-12`, `start-4`, and whether
the bytes before it repeat at a fixed stride. The control is the 1,664 starts whose block accounts for itself; the
residue is the 540 candidates that do not (`inventory/class_tails.csv`, `--report`).

| measure | control (1,664) | residue (540) |
|---|---:|---:|
| the last four bytes are a plausible 32-bit `TArray` count | **1** | 47 |
| the twelve bytes before the start are all zero | **0** | 4 |
| the byte before the start is `00` | **1,328** (79.8%) | 47 (top byte `04`, 59) |
| the four bytes before the start repeat at a stride of 4..24 | **1** | 69 (stride 9: 41) |
| the tail arrays decode backwards with compact counts | 636 | 105 |
| a clean bytecode walk ends exactly on the start | 5 (longest 26 B) | 11 (longest 35 B) |
| the payload carries the script epilogue `04 0b 47` | 0 | 0 |

Three readings, and the first two are negatives about the route the previous note proposed:

* **The field in front of a block start is not a 32-bit count, and not three empty fields.** A `TArray<dword>` count
  read as a little-endian dword at `start-4` is plausible on **one** of 1,664 known-good starts, and twelve zero
  bytes (two empty counts plus an empty `FName` number) occur on none of them. Whatever the record's tail is, it does
  not end in the shape "read a count, skip its elements" that a backward walk needs to take its first step.
* **Compact counts do not rescue it.** Reading each count as a one-byte index -- which 79.8% of the control's
  starts invite, since `00` in front of a start is exactly what an empty array looks like -- walks back cleanly on
  only **636 of 1,664** control starts, and where it lands is scattered (offsets 50, 62, 52, 63, 384 in the top
  five), not at a record boundary. The order in `UClass::Serialize`'s table is a property of the *object*, and the
  stream does not agree with it byte for byte: the fields are there, their encodings are not the ones a walk from
  the block end can assume.
* **The residue's starts sit inside fixed-size runs, and walking those runs back relocates nothing.** 69 of the 540
  residue starts are preceded by a repeating 4-byte unit (stride 9 in 41 of them) against 1 of 1,664 control starts,
  which is the measured form of "the candidate began inside a table rather than at its end". But re-parsing at every
  offset the run reaches -- keeping only a parse that ends on the payload's last byte, invents no name and names at
  least one declared property -- finds a better start for **one** residue class, and finds one for one control class
  too, so the rule has no discriminating power.

* **Nor is the tail bytecode, and that closes the forward route the same way.** Walking every window that ends on a
  known-good start with the image's own operand table (`tools/uscript-dis.py`, opcodes `0x48..0x5f` and the
  walker's bad-token case refused) finds a clean run ending exactly on the start for **5 of 1,664** control payloads,
  longest 26 bytes -- the length a two-token false positive would give -- and the script epilogue `04 0b 47` occurs in
  **none** of them. That is what `docs/BYTECODE.md` predicts from the other side: a class export carries its script
  *text* reference, and the bytecode lives in each `Function` export, so the class record's tail is neither a tag
  stream nor a script.

So the 545 withheld blocks are not relocated by reading the payload backwards, and the residue's periodicity says why:
their payloads are a record whose tail is a table of fixed-size entries, and a tag-stream reader cannot find the seam
from the block end because there is no tag stream there to end at. Both encodings a stream-side guess could offer --
counts and elements, or a token walk -- are now measured and refused on the control, so what is left is the record's
own field widths: `UStruct::Serialize`'s body (`0x000392b0`) is where the prefix's encodings come from, and every
candidate for the tail's 9-byte unit is a guess until that body is read.

### The tail's widths are named, and the list is still short of the record

Three writers give the record's field widths (`docs/NATIVE_VM.md`, *The record's field widths are compact*): a
`TArray` count is an `FCompactIndex` in a cooked package (`FUN_000637e0`), the 12-byte-record array streams one
object reference plus two dwords per record (`FUN_0003d900`), and the two "dword" arrays are `TArray<FName>` whose
elements go through the archive's name method (`FUN_0003d160`). **The stride-9 unit this page measured in the
residue is one of those streamed records** -- a one-byte reference and two dwords, the first repeating -- so the
"fixed-size table the candidate started inside" reading is not needed to explain it.

`tools/class-record-fields.py` walks the list with those widths from the prefix's end on all 2,209 located classes:
**0** ends on the block start, **1,038** run off the payload, and the walk explains a median **47** bytes of a
91-byte middle, leaving **42** before the block. So the widths are settled and the field *set* is not; the 545
stay withheld for the same reason as before, with one fewer unknown in the record.

### The middle is a record array and a mixed tail, and neither closes the 545

`tools/class-record-middle.py` reads the window between the measured prefix's end and the block start on all 2,204
located classes and labels it, so "what is in front of a block start" has a census rather than another candidate
field list (`inventory/class_record_middle.csv`; depth in `docs/NATIVE_VM.md`, *The stride-9 array is in the
middle*):

| measure | value |
|---|---|
| a stride-9 run of >=3 identical units in the middle | **536 of 2,204** (24.3%), median 5 units, max 269 (2,421 bytes) |
| the same search on the block itself (control) | **13** (0.6%) |
| leftover after the documented walk: `mixed` | 1,037 of 1,166, median 42 bytes, commonest size 20 on 190 classes |
| ... `dwords` / `records` / `compact` / `zero` | 44 / 27 / 7 / 4 |
| ... `overshoot` (the walk ends past the block) | 47 |
| the fit sweep in the documented order | 80 land (3.6%); control at +8: 46 (2.1%) |
| the fit sweep with the five dwords after the arrays | 618 (28.0%); control 74 (3.4%), but 360 of the 618 are classes with an 88-byte middle |

Two consequences for the 545 withheld blocks. The record's tail holds the 9-byte record array on a quarter of the
classes, and it can be found by its *period*, so an instrument that reads that array no longer needs the count
field. And no field added in front of the block can close the walk, because the leftover's commonest size -- 20
bytes -- is the five-dword group's own width. What remains is the window's grammar per class, not one more field of
`UClass::Serialize`.

### The array's head is not in front of its run

`tools/class-record-run.py` reads the window in front of that run rather than around the block
(`inventory/class_record_run.csv`, depth in `docs/NATIVE_VM.md`, *The run has no head*):
a compact count equal to the run's unit count ends within six bytes in front of it on **16 of 536** (3.0%), while
the same test at every other byte position of the same middles answers **9,675 of 268,834** (3.6%) -- expected 19,
observed 16, so the run's head is at chance. A raw 4-byte count is in front of it on **none**, and a count equal to
the whole array's element count at any 9-byte lattice point up to 40 units back lands on 184 classes (34.3%) where a
41-point sweep over a 3.6% background should land on about 78%: below chance, not above. Because the writer stores
each record's reference as a compact index of **1 to 5 bytes**, the array's earlier elements need not be 9 bytes
wide, so a variable-stride walk back -- not another field in front of the block -- is what a head search needs next.

What sits either side of the run is measured too: **131** bytes in front of it (`mixed` on 499 of 536) and **58**
behind it (modes 5, 4 and 49). The repeating dword is one small index-shaped byte in a zero dword on **431 of 536**
(import-shaped on 227, positive on 204), so the period is a value repeating at a 9-byte stride rather than a
pointer.

### The elements continue past the run, and the count is not at the end of them

`tools/class-record-walkback.py` walks the class record's 9-byte run backwards at the widths its writer uses -- one
compact reference of 1-5 bytes plus two dwords per element, an element taken only where its reference names an
object the package has. Two numbers come out, and they point in opposite directions:

* **the body is real.** 242 of the 536 classes with a run walk at least one element back and 24 walk eight or more
  (eleven on `AmbientCreatures.HerdAnimal`), against 52 and 0 for the same walk aimed one byte off the boundary. So
  the run is a stretch *inside* a variable-stride body, not the body's front.
* **the count is still not there.** A compact count equal to the elements walked plus the run's units, ending
  exactly at the boundary the walk reached, exists on **1 of 536** classes -- against **3 of 536** for the null.
  The array whose elements these are therefore does not carry its count at the end of them, which is why five
  searches from the block's side, the payload's side and now the elements' side have all refused.

The 545 withheld blocks are untouched by this: the walk says where the record's element grammar continues, not where
the defaults start. The next instrument is the writer that allocates the array (`FUN_0003d900`'s caller), since a
`TArray`'s count is written where it is created, not where it is filled.

### What the record writes in front of the block, now that the prefix is measured

`tools/class-prefix.py` decodes the record's opening with `UStruct::Serialize`'s own field order and scores it against
the package's export table on all 2,319 class payloads (`inventory/class_prefixes.csv`): a compact index at offset 0
equals the export's `super` **2,319 / 2,319**, the field after it is a single `00` byte on **2,319 / 2,319** (a null
object reference — an `FName` there could not be `NAME_None`, which the reader asserts), and the third field names the
class's own `TextBuffer` export on **2,319 / 2,319**. The reference is **1 or 2 bytes, never 4**, and the prefix ends
between offset 3 and 6. So the record's front is three references, and the fields the body writes between them —
`FriendlyName`, the source line, the text position — are not in the cooked stream at all.

The `UClass` tail's field list is not in the record either, with a control: scanning every offset for five dwords, a
count plus 12-byte records, a config `FName` and two count-plus-4-byte arrays lands exactly on the block start for
**191 of 2,209** located classes and **184 of 2,209** when the same walk is aimed eight bytes short — a coincidence
rate, spread over 124 distinct offsets. The middle between the prefix and the block is 91 bytes at the median (p90
394, max 13,660), and 15 located starts fall *inside* the prefix (offset 1), which are the locator's own false
positives rather than a record shape.


### The array's head is at +42 and the bytes after it are one field, not a walk

`tools/class-record-array.py` reads the record's `+0x4d0` array where its writer puts it. `UClass::Serialize`
(`0x0003a800`) is the array writer's only caller -- a direct-call census finds one `E8` site for `0x0003d900` in
the whole image, `0x3a8a6` -- and `FUN_0003d900` writes the member in place, count first (`FUN_000637e0` on `Num`,
a compact index whenever either archive flag is set), so there is no allocating caller and the count is the
array's own first field. With the prefix, `UState`'s 22 bytes and the five dwords in front of it, the head is at
`prefix_end + 42` on all 2,204 located classes: the array parses there on **549** (24.9%), against **36** of those
549 when the dword group is moved after it and **0** when the walk is aimed one byte late. That is the documented
field order confirming itself from the bytes.

What it does not do is reach the block start: **3** of the 549 land exactly, and the rest stop short by a
distribution whose mode is exactly **20** bytes -- the five-dword group's width -- on 190 classes (22 on 59, 21 on
31). A missing name array would leave 2-3 bytes and a wrong array start would leave 42, so the 20 bytes are the
next field after the tail, and they are what stands between the record's field list and the 545 withheld
`defaultproperties` blocks. The stride-9 run the three preceding notes measured sits inside this array's element
span on only 98 of 536 classes; on `AmbientCreatures.HerdAnimal` the array is 22 one-byte-referenced records
ending at offset 246, and the run it was read from starts at 952.
The 20 bytes that paragraph leaves are the array's own end, not a field (below).

**The 20 bytes are not a field, and the writer's body is why (2026-11-12).** `UClass::Serialize` between the second
name array's call (`0x3a904`) and the defaults branch (`0x3a90c`) is four bytes of stack cleanup and the branch
itself: the `0x63` version gate at `0x3a8f6` is the last one in the function and it guards only the `+0x4f4` array,
so nothing is written behind it. The fields the writer does write there -- a name array, the 8-byte config `FName`,
a name array -- close the gap on **0 of 549** classes; measured from the array's *bare* tail the gap is 27 bytes on
188 of them (the 20 above is that gap minus the 7 bytes the two-name-array walk had already consumed), 29 on 61 and
28 on 30, and on 13 classes the walk runs *past* the defaults start. So the residue is the array's own end -- its
element span -- and not a field after it.


Numbers: `docs/NATIVE_VM.md`, *The array's count is its own first field, and its writer has one caller*;
`inventory/class_record_array.csv`.
## The shipped text is not an oracle for defaults
### The bytes after the array are a pair list, and on five packages it is the same bytes

The gap between the located array's tail and a class's defaults start is 27 bytes at the median, and the last 6 to 9 of
them are a **pair list**: `tools/class-record-tail.py` reads 291 closing records and 225 of them as a compact count (3 on
219, 2 on 6) plus that many `(compact, compact)` pairs, ending exactly on the block start on 219 with a control of **0 of
225** when the same grammar starts one to three bytes later (`inventory/class_record_tail.csv`).

**And the archive's name slot writes one field, not two, which closes that reading from the other side.** `0x38bf0`
(vtable `0x4d40d0`+0x1c) converts an `FName` to a single index through its proxied name table and writes it as **one**
compact field (`tools/archive-slots.py`), so an element is 1-5 bytes and a name index is unsigned -- bit 7 of a leading
byte is the sign. A seven-byte tail holding three names would need three two-byte indices, and its bytes instead read
as a count of three plus three `(small value, negative reference)` pairs, which is what `class_record_tail.csv`
records. The pair list is therefore not the two `TArray<FName>`s under either width, and the field list in front of it
is still open.

The second value of the middle pair is negative on **219 of 225** and only four distinct values occur across the corpus,
one per package (-6 on 149 classes, -3 on 52, -4 on 12, -7 on 6). An `FName` number is an unsigned duplicate counter, so
the signed term is a reference, and the pair list is not the two `TArray<FName>`s `docs/NATIVE_VM.md` used to name here.

**What it costs the gap argument.** The tail is byte-identical across all 51 `Stargateeffects` rows, all 6
`StargateGame`, 123 of 149 `Sharpeffects`, 5 of 6 `Engine` and 7 of 12 `GamePlay` -- and it varies per class on
`StargateContent` (48 distinct tails over 61) and `StargateVehicles` (6 over 6). So on five of the seven packages the
record's last bytes carry package-level content rather than the class's own fields, and a grammar that closes all 291
says nothing about the 545 withheld blocks: their start is still open, and the tail is not the byte range that decides
it. What is left is which object writes the pair list at all -- nothing inside `UClass::Serialize` does, and the two
callees at `0x3a8a6` / `0x3a8af` write `+0x4d0`'s records and `+0x4dc`'s names, neither of which can be the negative
term.

**The pair list is not the defaults stream's first tag, and the tag side says so on all 291.** `class-record-tail.py`
also runs the property-tag parser (`tools/prop-stream.py`, the one `class-defaults.py` locates blocks with) from both
positions, requiring the stream to terminate on `None` with only declared names and end exactly on the payload's last
byte: from `block_start` it closes on **291 of 291** classes, and from `block_start - width` on **67 of 291**. So the
block start these measurements were built on is confirmed from the tag side a second time, and the 6-to-9 bytes in
front of it belong to the record. 67 is not zero, which is why the boundary rule alone is not enough to place a block;
requiring the *same* rule at the earlier position is what separates them.

### The tail's leading value is a count, and each pair's second field is a reference

The named next measurement was to parse each pair as one `FName` "and ask whether the leading compact value is the
pair count or the first name's index". Both halves are measured on the 291 records that close
(`inventory/class_record_tail.csv`; `fname_exact`, `refs`, `neg_target`, `names_kind`):

- **The leading value is the count.** Read the other way -- the leading value as the first name's index and the rest
  as a bare `(index, number)` sequence -- the tail closes on **0 of the 219** three-pair tails. It closes on the six
  two-pair tails only because their byte count is even (6 and 8 bytes are three and four two-byte names), and even
  there the reading leaves a negative number, which an `FName` cannot hold. A seven-byte tail is three two-byte
  names and one byte over, so the count-leading parse is the only one that consumes the bytes it is given.
- **The second field is a reference, and the negative one names the same object in every package.** Each pair's
  second value fits `ue2pkg.class_name_of`'s encoding -- 0 null, negative import `-v-1`, positive export `v-1` -- on
  **225 of 225** parsed tails. The negative one names **one import**, `Class.Object` (the import of `Core.Object`),
  on **219 of 225** rows; the same test aimed one import earlier scatters over five targets (123 `Class.Emitter`,
  52 `Package.Core`, 26 `Class.Pawn`, 12 `Class.Actor`, 6 others). So the four spellings measured above (-6 on 149,
  -3 on 52, -4 on 12, -7 on 6) are **one object at four import-table positions**: the per-package constant is
  positional, not a value.
- **The three first values are the package, `Core` and `System`.** Classified as tokens rather than strings (no name
  of the package's own content enters the artifact), the rows split `pkg;Core;System` **189**, `pkg;Core;other`
  **18** (17 `StargateContent`, 1 `StargateVehicles` -- the third value is a per-class name there),
  `other;Core;System` **12** (all `GamePlay`, whose name-table spelling is `Gameplay`, not the file stem) and
  `pkg;?` **6** (`Engine`'s two-pair tails, whose first value is outside the name table). So the tail is
  package-level content, which is what the hash groups above measured from the other side.

The tail is therefore a compact count and three (name, reference) pairs, and neither field is an `FName`: an
`FName`'s second field is an unsigned number, and this one is signed and resolves. What is still open is not the
grammar but the **owner** -- the destructor places no member in the record region that can hold a (reference,
value) pair, and the tail is identical across every class of five of the seven packages, so its writer runs once
per package rather than once per class.

**Where the list sits, from the reader's own listing.** `0x0003a800` writes nothing between the last field it reads
(`+0x4f4`'s name array, version-gated at `0x63`, `0x3a8f6`-`0x3a904`) and the defaults buffer (`0x3a96d`), so the
6-to-9-byte list is that array and not bytes left over before the block: the count of 3 is its element count and the
three two-byte elements are its elements. The 20-byte middle behind it is the `+0x4dc` array plus two separate fields, not one 8-byte `FName`: `+0x4b0`
is the class reference `ClassWithin` (the image's own assert at `0x397b0` names it) and `+0x4b4` is the
config name, each one compact field through a different archive slot (`+0x18` for the reference, `+0x1c` for
the name). That is the correction the pair census made; depth in `docs/NATIVE_VM.md`, *The pair at
+0x4b0/+0x4b4 is a class reference and a name, and the image names the first*. Depth: `docs/NATIVE_VM.md`, *The reader writes nothing between the last field
and the defaults, so the list is a field*.


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

### The 27-byte gap is an array and a list, and the 20 bytes are the array

`inventory/class_record_gap.csv` (`tools/class-record-gap.py`, `--check` in `make ci`) splits the gap the field
walk leaves on the 549 located classes. The gap is 27 bytes on 188 of them, and it decomposes as a **second array
of `FUN_0003d900`'s own grammar** -- 361 of 549 classes hold one, 20 bytes at the median and 20 on 260, two records
on 321, references resolving on 355 -- followed by the **count-plus-pairs list**, which closes the record exactly
on 219. The control that makes the position mean something: the same grammar a byte later parses 811 times and
lands on the defaults start once. `docs/NATIVE_VM.md` (*The 20-byte middle is a second record array*) carries the
table and the refutation of the config-`FName` reading of the 8 bytes in front of the list.

The middle's **owner** is named since -- the reader is handed `+0x4dc` at `0x3a8af`, staged under the record
writer's own call (`docs/NATIVE_VM.md`, *Only one call site writes the record array, and its reader stages the second
member under that call*) -- but its shape is not: this CSV's twelve-byte parse fits 361 classes and the writer's own
compact-pair grammar fits 408, and neither closes the gap at its position (0 of 549). `nm_*` in
`inventory/class_record_gap.csv` carries both readings side by side.

**The middle's other candidate array has no writer.** The 4-byte-element `TArray` at `+0x4c4` -- the group's
middle member, between the 8-byte one and the record array -- is initialised empty and destroyed empty, and all
nine stores into its head are the three constructors' dword zero-fills
(`inventory/class_array_head_census.csv`, `docs/NATIVE_VM.md`, *The array at +0x4c4 is initialised and destroyed,
and no body fills it*). So the 27-byte gap is not its stream either: the gap stays a located array plus a
count-plus-pairs list, with one end still unexplained.
