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

`inventory/class_defaults_coverage.csv` has the per-package rows:

| | classes | blocks found | every property declared |
|---|---:|---:|---:|
| `StargateContent` | 567 | 541 | **441** |
| `Engine` | 367 | 287 | 170 |
| `StargateGame` | 306 | 248 | 150 |
| `GamePlay` | 262 | 260 | 180 |
| `Sharpeffects` | 158 | 157 | **156** |
| `GUI` | 148 | 139 | 128 |
| `StargateVehicles` | 142 | 133 | 117 |
| `StargateMenus` | 98 | 82 | 65 |
| `UWindow` | 78 | 61 | 5 |
| others (9 packages) | 193 | 174 | 140 |
| **total** | **2,319** | **2,084** | **1,552 (67%)** |

## Emitting: 13,307 of 13,345 properties render, plus 1,269 subobjects

`inventory/class_defaults_emit.csv`, per package, over the **fully-declared blocks only**:

| package | classes | blocks | properties | rendered | | subobjects |
|---|---:|---:|---:|---:|---:|---:|
| `StargateContent` | 567 | 441 | 4,711 | 4,704 | 99.9% | 350 |
| `GamePlay` | 262 | 180 | 1,649 | 1,648 | 99.9% | 0 |
| `StargateVehicles` | 142 | 117 | 1,427 | 1,410 | 98.8% | 39 |
| `Engine` | 367 | 170 | 1,293 | 1,288 | 99.6% | 3 |
| `Sharpeffects` | 158 | 156 | 1,101 | 1,101 | 100.0% | 467 |
| `GUI` | 148 | 128 | 1,085 | 1,084 | 99.9% | 29 |
| `StargateGame` | 306 | 150 | 879 | 879 | 100.0% | 75 |
| `Stargateeffects` | 77 | 69 | 367 | 367 | 100.0% | 172 |
| `StargateMenus` | 98 | 65 | 310 | 304 | 98.1% | 134 |
| others (9 packages) | 194 | 76 | 523 | 522 | 99.8% | 0 |
| **total** | **2,319** | **1,552** | **13,345** | **13,307** | **99.7%** | **1,269** |

What renders, and what each rendering was checked against:

| type | count | rendered as |
|---|---:|---|
| `Float` / `Int` / `Bool` | 6,544 (3,171 / 1,192 / 2,181) | `1.0`, `-1`, `True` |
| `Name` / `Str` / `String` | 3,024 (1,563 / 1,461) | bare when the text is identifier-shaped, quoted when it holds a space or punctuation |
| `Object` / `Class` | 1,466 | `Class'Name'` inside the package, `Class'Package.Name'` across one |
| `Struct(Vector/Rotator/Color/Plane)` | 808 | `(X=..,Y=..,Z=..)`, `(Pitch=..,Yaw=..,Roll=..)`, `(R=..,G=..,B=..,A=..)` |
| `Byte` | 965 | an enum element name where the enum can be resolved, the number otherwise |
| `Array` | 444 properties, 1,315 elements | one line per element: `DiallingSounds(0)=Sound'Engine.DHD1'` |
| other structs | 56 properties | a nested tagged block: `(MeshName=..,FaceTextureName=..,MeshSectionIndex=5)` |

Arrays and the remaining structs are decoded from **UELib's own defaults deserialiser**
(`tools/uelib/src/Core/Classes/UDefaultProperty.cs`, pinned in `tools/versions.lock`) instead of from the bytes,
which is the difference between a guess and a reader: an array is a **compact-index count followed by that many
untagged elements**, their width coming from the *inner* type the property record names (`array<Sound>` — and
`inventory/class_properties.csv` already carries that name), and a struct that is not one of the four atomic
layouts is a **nested tagged block**, `Field=value` pairs terminated by `None`.

Every array decode has to tile its own value region exactly — count and element widths must land on the tag's
declared size — so a wrong inner type is a **refusal**, not a short element. That is what turns a guess into 444
decoded and 17 refused, and it is also what caught the defects below.

## Subobjects: 1,269 `Begin Object` blocks

An `editinline` property or array element does not point at a class or a texture: it points at an **export whose
outer is the class being described** — a subobject the compiler expects to be declared inside the same
`defaultproperties`, before the assignment that names it:

    Begin Object Class=SpriteEmitter Name=SpriteEmitter0
        ... that subobject's own defaults, 12 to 40 properties ...
    End Object
    Emitters(0)=SpriteEmitter'SpriteEmitter0'

The emitter renders those blocks recursively, using the same locator on the subobject's own export: **1,269
subobjects carrying 15,687 properties, of which 14,887 render**, so a class's defaults are 13,307 + 14,887 =
**28,194 rendered properties** in total. Without the declaration a reference names an object the compiler has never
seen; that is why they are part of this pass and not a cosmetic extra.

**A correction.** The previous PR reported these 270 arrays as "`editinline` arrays whose elements are serialised
inline object *instances*, a stream this reader does not parse". That was wrong twice: `editinline` is a *flag in
the property's type string* (`array<export editinline ParticleEmitter>` — two flags, and stripping one left
`editinline ParticleEmitter` as a "type"), and the elements are ordinary compact object references. Nothing about
the byte format was missing; a string-stripping bug was reported as a format gap. The count is in this table, and
the earlier claim should not be quoted.

What is left over, all counted per block in the CSV: **17 arrays**, **12 structs** and **10** properties with a
malformed value — 0.3% of the class-level block contents. The 17 are arrays whose element type is a struct that
decodes as neither a tagged block nor an atomic layout (`FaceInformation` 6, `PassengerWeaponStruct` 5, and one
each of five others); the 12 are `InterpCurve`, whose member list the reference itself special-cases. Both are
refusals of the tiling test, not truncated reads.

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

* **The last 17 arrays and 12 structs.** Element types that decode as neither a tagged block nor an atomic layout
  (`FaceInformation`, `PassengerWeaponStruct`, `InterpCurve` …), refused by the tiling test rather than read
  short. Some instances of the same type do decode; the difference is not yet measured per class.
* **The 532 withheld blocks.** Whether their candidate is a truncated real block, a different record at the
  payload's tail, or a parse coincidence is not settled per class — only counted, and excluded.
* **Whether a compiler accepts the output.** Nothing here has been through `ucc`: the text is shaped like the
  shipped source (verified character by character on the two blocks above that do agree), not compiled. 3.6 is
  where that gets tested.
* **The roster itself.** The squad scripts maps name (`TSS_7A_B_Jaffa_01`, `AI_Jaffa_Horus_Med_single`) appear in
  those packages' name tables but are neither imports nor exports, so the reference to them lives in data inside
  the `Level` export, which this reader does not parse.
