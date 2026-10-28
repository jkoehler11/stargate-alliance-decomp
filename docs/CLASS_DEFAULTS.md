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
| `StargateMenus` | 98 | 86 | 68 |
| `UWindow` | 78 | 78 | 12 |
| others (9 packages) | 193 | 182 | 145 |
| **total** | **2,319** | **2,209** | **1,666 (71.8%)** |

### The scan window was the biggest single defect

The locator used to search the first **512 bytes** of an export for the block. A class export carries its record
(script tables, super, package dependencies) *before* its defaults, and the record of every base class is longer
than that, so the classes with the most defaults were exactly the ones reported as having none:
`Actor` (record 2,451 bytes), `Pawn` (3,992), `PlayerController` (13,665), `GameInfo` (5,682), `Weapon` (2,580).
Scanning the whole payload recovers **114 classes**, takes blocks found from 2,084 to **2,209**, fully-declared
blocks from 1,552 to **1,666**, and classes with no block from 235 down to **110** — and the recovered set is the
inheritance spine of the whole tree, which is why the emitted property count jumps from 13,307 to 15,491.

Where it still fails:

* `UWindow` (12 of 78) and `Fire` (0 of 7) are the worst. `UWindow`'s classes are engine-heavy GUI types whose
  declared properties mostly come from `GUI`/`Core` parents; `Fire`'s are fractals with almost no declared
  properties, so "every property declared" is the wrong test there — they may well be correct blocks scored
  harshly.
* **110 classes have no usable block, and the trailer bound is not why.** `make defaults-misses`
  (`tools/class-defaults.py --misses`, `inventory/class_defaults_misses.csv`, `--check-misses` in `make ci`) classifies
  every one of the 653 classes that are not fully declared, against the committed per-class artifact:

  | reason | classes | what it means |
  |---|---:|---|
  | `incomplete` | 543 | a block was chosen, but it holds names the class does not declare — the tie below |
  | `empty-block` | 109 | the payload parses a valid **empty** block, and no offset anywhere yields a parse holding even two of the class's properties |
  | `single-property` | 1 | only one-property blocks parse |
  | `trailer-over-64` | **0** | no class is rejected by the 64-byte bound |
  | `no-block-parses` | **0** | every class parses *something* |

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
* Relaxing the one-property filter was tried and rejected: it would "recover" 19 of the single-property group, but the
  matches are spurious (`Modifier` matching its own class name, `TexPanner` matching on `ObjectFlags`). A false block is
  worse than a miss: it would emit wrong defaults where the compiler would otherwise leave the class alone.
* 428 classes are ambiguous — ties between blocks with equally declared names (`UWindow` alone is 45 of them),
  broken by preferring the longer block and then the smaller trailer, which is a rule of thumb, not a proof.

## Emitting: 15,519 of 15,530 properties render, plus 1,317 subobjects

`inventory/class_defaults_emit.csv`, per package, over the **fully-declared blocks only**:

| package | classes | blocks | properties | rendered | | subobjects |
|---|---:|---:|---:|---:|---:|---:|
| `StargateContent` | 567 | 457 | 5,010 | 5,009 | 100.0% | 350 |
| `Engine` | 367 | 195 | 1,823 | 1,821 | 99.9% | 9 |
| `StargateGame` | 306 | 195 | 1,753 | 1,753 | 100.0% | 89 |
| `GamePlay` | 262 | 182 | 1,657 | 1,656 | 99.9% | 0 |
| `StargateVehicles` | 142 | 124 | 1,559 | 1,559 | 100.0% | 41 |
| `GUI` | 148 | 132 | 1,152 | 1,151 | 99.9% | 46 |
| `Sharpeffects` | 158 | 156 | 1,101 | 1,101 | 100.0% | 467 |
| `UDebugMenu` | 38 | 31 | 384 | 384 | 100.0% | 0 |
| `Stargateeffects` | 77 | 69 | 367 | 367 | 100.0% | 172 |
| others (9 packages) | 254 | 125 | 724 | 718 | 99.2% | 143 |
| **total** | **2,319** | **1,666** | **15,530** | **15,519** | **99.9%** | **1,317** |

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

What is left over, all counted per block in the CSV: **2 arrays**, **0 structs** and **9** properties with a malformed
value — 0.07% of the class-level block contents, down from 0.26%. The residue is no longer a format gap: every one of
the eleven is a *type contradiction* inside a block the locator accepted (below).

## Subobjects: 1,317 `Begin Object` blocks

An `editinline` property or array element does not point at a class or a texture: it points at an **export whose
outer is the class being described** — a subobject the compiler expects to be declared inside the same
`defaultproperties`, before the assignment that names it:

    Begin Object Class=SpriteEmitter Name=SpriteEmitter0
        ... that subobject's own defaults, 12 to 40 properties ...
    End Object
    Emitters(0)=SpriteEmitter'SpriteEmitter0'

The emitter renders those blocks recursively, using the same locator on the subobject's own export: **1,317
subobjects carrying 15,810 properties, of which 15,802 render**, so a class's defaults are 15,519 + 15,802 =
**31,321 rendered properties** in total. Subobjects gained the most from the array fix (15,010 → 15,802): a particle
system's own defaults are full of struct arrays. Without the declaration a reference names an object the compiler has never
seen; that is why they are part of this pass and not a cosmetic extra.

**A correction.** The previous PR reported these 270 arrays as "`editinline` arrays whose elements are serialised
inline object *instances*, a stream this reader does not parse". That was wrong twice: `editinline` is a *flag in
the property's type string* (`array<export editinline ParticleEmitter>` — two flags, and stripping one left
`editinline ParticleEmitter` as a "type"), and the elements are ordinary compact object references. Nothing about
the byte format was missing; a string-stripping bug was reported as a format gap. The count is in this table, and
the earlier claim should not be quoted.

## What the 543 withheld classes are, measured

`tools/defaults-emit.py --explain` reports the residue without printing values. Three outcomes per class:

| outcome | classes | payload median | p90 | under 256 bytes |
|---|---:|---:|---:|---:|
| block accounts for itself (emitted) | 1,666 | 159 B | 570 B | 1,247 |
| candidate that does not account for itself (withheld) | 543 | 112 B | 279 B | 474 |
| no candidate at all | 110 | 103 B | 270 B | 98 |

Of the 543, **227 have exactly one unaccounted name and 316 have two or more**; there are 1,017 unaccounted name
slots in total. Those names are dominated by **package names** — `Core` 252, `System` 189, `Engine` 73,
`Lighting` 69 — followed by names that are struct *fields* elsewhere in the tree (`Range`, `LightColor`,
`Movement`, `Subject`, `GenericType`, `Type`). Package names cannot be property names, which is the useful part:
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

## Wiring defaults into the source tree

`make extract-script` writes 2,319 classes as `$WORK/source-tree/<Package>/<Class>.uc`, and only 15 of them carry a
`defaultproperties` block. `make defaults-tree` runs the emitter over that tree and appends each block where the
compiler expects it — end of class — replacing any block already there, so re-running is idempotent. The result is
one tree with text and defaults for **1,552 of 2,319 classes (67%)**: the shape 3.6 needs, short of the classes
whose block could not be located or does not account for itself.

## Not measured

* **The last 11 values: a located block whose tag *types* contradict the class's declared kinds.** Two arrays and
  nine scalars in six classes (`Engine.TextToSpeechAlias`, `GamePlay.EFFECT_DOME_SAINTPAULS`, `Engine.KConeLimit`,
  `GUI.MenuItemOverlay`, `StargateMenus.SelectPawnPanel`, `StargateContent.ReplicatorQueen`). The locator scores a
  candidate by *names* only — "does this block hold properties the class declares?" — never by whether each tag's
  type agrees with the property record, so it can accept a start inside a preceding native blob. Examples from the
  committed artifact: `CollisionHeight` (a `Float` property) arriving as a 16-byte `Float` tag,
  `PhysicsVolume` (an `Object`) as a `Float`, `RegularMovementAnims` (a `Name`) as a `Vector`. In all six classes a
  *type-agreeing* candidate exists that the name score ranks lower, so the fix is a scoring change (type agreement
  as a hard filter, or as a tie-break), and it cannot be made without re-measuring the 1,666/2,319 block count —
  which is why it is a separate task and not part of the array fix. What would settle the correct start per class:
  that type-aware locator, cross-checked against a stock `ucc` build. **Do not read these eleven as an undecoded
  byte format** — the bytes decode fine; the block chosen is not the class's block.
* **The 543 withheld blocks.** Their candidate parses a package name as a property, so it reads a record and
  not a tag stream — but whether that record means "this class sets no defaults" or is a component table is not
  settled (see the section above for the three explanations already tested and rejected).
* **Whether a compiler accepts the output.** Nothing here has been through `ucc`: the text is shaped like the
  shipped source (verified character by character on the two blocks above that do agree), not compiled. 3.6 is
  where that gets tested.
* **The roster itself.** The squad scripts maps name (`TSS_7A_B_Jaffa_01`, `AI_Jaffa_Horus_Med_single`) appear in
  those packages' name tables but are neither imports nor exports, so the reference to them lives in data inside
  the `Level` export, which this reader does not parse.
