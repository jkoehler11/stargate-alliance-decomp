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
* **110 classes still have no candidate — and none of them is blockless.** `make defaults-misses`
  (`tools/class-defaults.py --misses`, `inventory/class_defaults_misses.csv`, `--check-misses` in `make ci`) classifies
  every one of the 653 classes that are not fully declared against the committed per-class artifact:

  | reason | classes | what it means |
  |---|---:|---|
  | `incomplete` | 543 | a block was chosen, but it carries names the class does not declare — the tie below |
  | `single-property` | 62 | every block that parses holds exactly **one** property, so the `len(props) < 2` filter drops it |
  | `trailer-over-64` | 48 | blocks parse, but every one ends more than 64 bytes before the export's end |
  | `no-block-parses` | **0** | no class in the tree is without a parseable block |

  So the doc's old phrasing ("absent or fails the trailer bound") is answered: nothing is absent. The two groups are
  different problems. The 48 are a *bound* problem — the 64-byte trailer is a rule of thumb for the component table that
  can follow a defaults block, and these exports carry more than that. The 62 are a *shape* problem, and relaxing the
  one-property filter was tried and rejected: 19 of the 62 would then match a name the class declares, but the matches
  are spurious (`Modifier` matching its own class name, `TexPanner` matching on `ObjectFlags`), which is worse than a
  miss — a false block would emit wrong defaults for classes the compiler would otherwise leave alone.
* 428 classes are ambiguous — ties between blocks with equally declared names (`UWindow` alone is 45 of them),
  broken by preferring the longer block and then the smaller trailer, which is a rule of thumb, not a proof.

## Emitting: 15,491 of 15,530 properties render, plus 1,317 subobjects

`inventory/class_defaults_emit.csv`, per package, over the **fully-declared blocks only**:

| package | classes | blocks | properties | rendered | | subobjects |
|---|---:|---:|---:|---:|---:|---:|
| `StargateContent` | 567 | 457 | 5,010 | 5,003 | 99.9% | 350 |
| `Engine` | 367 | 195 | 1,823 | 1,818 | 99.7% | 9 |
| `StargateGame` | 306 | 195 | 1,753 | 1,753 | 100.0% | 89 |
| `GamePlay` | 262 | 182 | 1,657 | 1,656 | 99.9% | 0 |
| `StargateVehicles` | 142 | 124 | 1,559 | 1,542 | 98.9% | 41 |
| `GUI` | 148 | 132 | 1,152 | 1,150 | 99.8% | 46 |
| `Sharpeffects` | 158 | 156 | 1,101 | 1,101 | 100.0% | 467 |
| `UDebugMenu` | 38 | 31 | 384 | 384 | 100.0% | 0 |
| `Stargateeffects` | 77 | 69 | 367 | 367 | 100.0% | 172 |
| others (9 packages) | 254 | 125 | 724 | 717 | 99.0% | 143 |
| **total** | **2,319** | **1,666** | **15,530** | **15,491** | **99.7%** | **1,317** |

What renders, and what each rendering was checked against:

| type | count | rendered as |
|---|---:|---|
| `Float` / `Int` / `Bool` | 7,627 (3,778 / 1,322 / 2,527) | `1.0`, `-1`, `True` |
| `Name` / `Str` / `String` | 3,548 (1,785 / 1,763) | bare when the text is identifier-shaped, quoted when it holds a space or punctuation |
| `Object` / `Class` | 1,670 | `Class'Name'` inside the package, `Class'Package.Name'` across one |
| `Struct(Vector/Rotator/Color/Plane)` | 919 | `(X=..,Y=..,Z=..)`, `(Pitch=..,Yaw=..,Roll=..)`, `(R=..,G=..,B=..,A=..)` |
| `Byte` | 1,052 | an enum element name where the enum can be resolved (771 of them), the number otherwise |
| `Array` | 468 properties, 1,435 elements | one line per element: `DiallingSounds(0)=Sound'Engine.DHD1'` |
| other structs | 207 properties | a nested tagged block: `(MeshName=..,FaceTextureName=..,MeshSectionIndex=5)` |

Arrays and the remaining structs are decoded from **UELib's own defaults deserialiser**
(`tools/uelib/src/Core/Classes/UDefaultProperty.cs`, pinned in `tools/versions.lock`) instead of from the bytes,
which is the difference between a guess and a reader: an array is a **compact-index count followed by that many
untagged elements**, their width coming from the *inner* type the property record names (`array<Sound>` — and
`inventory/class_properties.csv` already carries that name), and a struct that is not one of the four atomic
layouts is a **nested tagged block**, `Field=value` pairs terminated by `None`.

Every array decode has to tile its own value region exactly — count and element widths must land on the tag's
declared size — so a wrong inner type is a **refusal**, not a short element. That is what turns a guess into 468
decoded and 18 refused, and it is also what caught the defects below.

What is left over, all counted per block in the CSV: **18 arrays**, **12 structs** and **10** properties with a
malformed value — 0.26% of the class-level block contents. The arrays are element types that decode as neither a
tagged block nor an atomic layout (`FaceInformation` 6, `PassengerWeaponStruct` 5, and singles); the structs are
`InterpCurve`, whose member list the reference itself special-cases. Both are refusals of the tiling test, not
truncated reads.

## Subobjects: 1,317 `Begin Object` blocks

An `editinline` property or array element does not point at a class or a texture: it points at an **export whose
outer is the class being described** — a subobject the compiler expects to be declared inside the same
`defaultproperties`, before the assignment that names it:

    Begin Object Class=SpriteEmitter Name=SpriteEmitter0
        ... that subobject's own defaults, 12 to 40 properties ...
    End Object
    Emitters(0)=SpriteEmitter'SpriteEmitter0'

The emitter renders those blocks recursively, using the same locator on the subobject's own export: **1,317
subobjects carrying 15,810 properties, of which 15,010 render**, so a class's defaults are 15,491 + 15,010 =
**30,501 rendered properties** in total. Without the declaration a reference names an object the compiler has never
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

* **The last 18 arrays and 12 structs.** Element types that decode as neither a tagged block nor an atomic layout
  (`FaceInformation`, `PassengerWeaponStruct`, `InterpCurve` …), refused by the tiling test rather than read
  short. Some instances of the same type do decode; the difference is not yet measured per class.
* **The 543 withheld blocks.** Their candidate parses a package name as a property, so it reads a record and
  not a tag stream — but whether that record means "this class sets no defaults" or is a component table is not
  settled (see the section above for the three explanations already tested and rejected).
* **Whether a compiler accepts the output.** Nothing here has been through `ucc`: the text is shaped like the
  shipped source (verified character by character on the two blocks above that do agree), not compiled. 3.6 is
  where that gets tested.
* **The roster itself.** The squad scripts maps name (`TSS_7A_B_Jaffa_01`, `AI_Jaffa_Horus_Med_single`) appear in
  those packages' name tables but are neither imports nor exports, so the reference to them lives in data inside
  the `Level` export, which this reader does not parse.
