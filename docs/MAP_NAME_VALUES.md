# What a map package's name table is made of

A `.unr` is a package like any other: every `FName` in it is an index into one string table, and
`docs/PACKAGE_READER.md` reads that table for every map in the build. The table is usually described as the
package's *names* — its exports, its imports, the property and class names its record needs. That description
is incomplete in one direction that matters: a package also carries strings that nothing resolves at all,
the **values of name-typed properties**. A name value is data. It needs no import, no export and no
resolution, only a table entry.

The distinction is not cosmetic. `docs/CLASS_DEFAULTS.md` carried an open bullet saying the squad scripts a
map names "appear in those packages' name tables but are neither imports nor exports, so the reference to them
lives in data inside the `Level` export, which this reader does not parse." There is no `Level` export to
parse: `SuggestedSquadScript` is a declared `UNameProperty` on `IntelligentSpawner`
(`inventory/class_properties.csv`), so the roster is a property value on the spawner that names it, and the
property-block reader that already reads `Tag`, `Event` and `ExternalEvent` reads it too.

## The rule

`tools/map-name-values.py` classifies every distinct entry of every map's name table by what needs it:

* an **export**'s object name — the actor, the level, the model;
* an **import**'s name — the class, the package, the outer an export points at;
* a **name value** — a `UNameProperty` value read out of a placed export's property block;
* **vocabulary** — none of the three: property names, class names, package names, the strings the table
  exists to be indexed by.

The join is by string and folds case, because a name index resolves to one entry and UnrealScript compares
names case-insensitively. An entry is counted once per map that needs it, so the totals are per-map sums and
a string two maps share is two entries — which is what a table is.

A `Name` tag is only read when its value region is narrow enough to be an `FCompactIndex` (five bytes). A
wider one cannot be a name index; the walk has landed on a value byte and read it as a type-6 tag, and such a
row is counted as a mis-split rather than resolved to the reader's `?`.

<!-- generated:map-name-values -->
**13,225 of a map corpus's 249,426 distinct name-table entries (5.3%) exist because a placed actor's property names them -- not because the package imports, exports or declares that name.** A name value is data: `SuggestedSquadScript` is a declared `UNameProperty` on `IntelligentSpawner`, so the roster a map ships is a property value and needs no import, no export and no `Level` export to parse.

| measure | answer |
|---|---|
| maps read | **73** |
| exports over those maps | **194,190** |
| exports with a readable property block | **136,690** |
| name-typed property values | **122,461** |
| distinct name values | **15,463** |
| -- equal to an export's object name | **200** |
| -- equal to an import's name | **2,038** |
| -- equal to neither (data only) | **13,225** |
| -- absent from the name table (the join's control) | **0** |
| `Name` tags refused as mis-splits (wider than an index) | **1** |
| name-table entries | **249,426** |
| -- distinct | **249,426** |
| -- needed only by a name value | **13,225** (5.3%) |
| -- vocabulary (neither object nor value) | **25,238** (10.1%) |
| -- an export's or import's own name | **210,963** (84.6%) |
| squad-script pairs in `inventory/squad_scripts.csv` | **632** |
| -- of those named as a data-only value in their own map | **632** |

The four arms partition the table -- **210,963** objects, **13,225** value-only strings, **25,238** vocabulary, totalling the **249,426** distinct entries the 73 maps need -- so no row is counted twice and the split is the whole table, not a sample of it. **2,238** distinct values resolve to an object (`200` an export's own name, `2,038` an import's), so a `Tag` that repeats an actor's name costs the table nothing.

**The join's own control is the refusals, not a hope.** A `Name` tag's value region is an `FCompactIndex`, at most five bytes, so a wider one cannot be a name index and is counted as a mis-split rather than resolved to the reader's `?`: **1** tags over 1 maps, every one of the shape a `Vector` or a `ZoneNumber` value read as a type-6 tag. After that filter **0** value in the whole corpus names no table entry -- one out-of-range index, not a systematic miss -- which is the assertion that the string comparison is right: an index can only resolve inside the table.

### The properties that carry names

| property | values | distinct | data-only | maps |
|---|---:|---:|---:|---:|
| `Tag` | 10,800 | 7,060 | 8,915 | 73 |
| `Event` | 2,867 | 2,551 | 2,845 | 71 |
| `ExternalEvent` | 1,894 | 1,655 | 1,890 | 70 |
| `DestinationTag` | 1,662 | 1,627 | 1,650 | 61 |
| `Group` | 797 | 448 | 796 | 72 |
| `DialogueName` | 710 | 704 | 710 | 58 |
| `SuggestedSquadScript` | 632 | 619 | 632 | 61 |
| `TargetTag` | 585 | 404 | 407 | 58 |
| `PawnTag` | 538 | 326 | 342 | 61 |
| `ScriptTargetTag` | 505 | 500 | 505 | 54 |
| `ViewTargetTag` | 440 | 384 | 385 | 56 |
| `EventName` | 387 | 376 | 384 | 45 |
| `DeathEvent` | 323 | 289 | 323 | 59 |
| `DialogueFinishedEvent` | 256 | 256 | 256 | 33 |
| `InitialState` | 216 | 11 | 216 | 67 |

### By map

| map | exports | name values | distinct | data-only entries | vocabulary |
|---|---:|---:|---:|---:|---:|
| `M02A` | 3,758 | 2,235 | 410 | 377 | 417 |
| `M00` | 5,078 | 3,008 | 405 | 369 | 348 |
| `M07B` | 3,803 | 2,107 | 392 | 356 | 411 |
| `M15A` | 3,492 | 2,162 | 378 | 341 | 417 |
| `M10D` | 3,832 | 2,280 | 367 | 333 | 496 |
| `M07C` | 3,810 | 2,385 | 363 | 323 | 371 |
| `M07D` | 4,214 | 2,876 | 343 | 311 | 371 |
| `M13A` | 3,710 | 2,168 | 328 | 301 | 365 |
| `M10A` | 3,542 | 2,033 | 323 | 296 | 465 |
| `M14A` | 3,644 | 2,395 | 336 | 294 | 363 |
| `M10B` | 4,783 | 2,756 | 324 | 292 | 455 |
| `M16A` | 2,403 | 1,491 | 318 | 288 | 490 |
| `M17B` | 3,887 | 3,924 | 318 | 283 | 402 |
| `M07E` | 3,529 | 2,326 | 307 | 279 | 395 |
| `M21A` | 3,002 | 2,121 | 319 | 277 | 477 |
| `M17A` | 3,384 | 2,745 | 288 | 256 | 406 |
| `M14C` | 2,436 | 1,446 | 282 | 252 | 335 |
| `M18B` | 3,361 | 3,447 | 282 | 249 | 343 |
| `M02B` | 2,793 | 1,561 | 276 | 243 | 401 |
| `M20A` | 2,730 | 1,572 | 276 | 243 | 335 |
| `M21D` | 3,535 | 2,382 | 285 | 242 | 441 |
| `M19E` | 2,238 | 1,497 | 269 | 237 | 393 |
| `M02C` | 2,877 | 1,618 | 266 | 234 | 401 |
| `M07A` | 3,323 | 1,757 | 264 | 234 | 395 |
| `M19B` | 2,163 | 1,168 | 264 | 233 | 368 |
| `M12C` | 3,512 | 1,928 | 266 | 232 | 368 |
| `M20D` | 2,147 | 1,206 | 243 | 214 | 304 |
| `M17D` | 2,903 | 2,846 | 237 | 208 | 327 |
| `M18D` | 2,891 | 2,128 | 242 | 208 | 368 |
| `M13C` | 2,415 | 1,372 | 231 | 207 | 333 |
| `M21B` | 2,619 | 2,221 | 236 | 205 | 459 |
| `M15F` | 2,600 | 1,523 | 238 | 202 | 347 |
| `M20C` | 3,524 | 2,268 | 224 | 195 | 326 |
| `M04B` | 1,210 | 652 | 215 | 190 | 398 |
| `M12A` | 3,127 | 1,593 | 229 | 190 | 367 |
| `M21C` | 3,028 | 2,397 | 220 | 188 | 387 |
| `M12D` | 3,020 | 1,474 | 218 | 187 | 355 |
| `M04a` | 1,715 | 905 | 213 | 183 | 398 |
| `M12B` | 2,789 | 1,481 | 216 | 182 | 374 |
| `M18A` | 2,468 | 2,619 | 197 | 166 | 320 |
| `M16F` | 1,283 | 1,170 | 191 | 165 | 402 |
| `M15C` | 2,840 | 1,876 | 192 | 157 | 327 |
| `M15E` | 1,969 | 1,053 | 185 | 157 | 302 |
| `M19A` | 2,136 | 1,006 | 193 | 154 | 397 |
| `M13B` | 1,401 | 757 | 176 | 152 | 297 |
| `M16D` | 1,999 | 894 | 170 | 143 | 403 |
| `M20B` | 3,059 | 1,657 | 170 | 142 | 310 |
| `M10C` | 2,705 | 1,538 | 170 | 139 | 400 |
| `M15D` | 2,828 | 1,683 | 175 | 139 | 345 |
| `M19C` | 1,078 | 708 | 160 | 136 | 353 |
| `M19D` | 1,965 | 1,350 | 181 | 136 | 370 |
| `M16E` | 1,224 | 590 | 157 | 129 | 361 |
| `M14B` | 2,996 | 1,874 | 153 | 125 | 269 |
| `M02D` | 1,939 | 1,066 | 156 | 124 | 345 |
| `M16B` | 1,489 | 766 | 143 | 113 | 363 |
| `MainMenu` | 958 | 601 | 127 | 108 | 283 |
| `M12E` | 2,989 | 1,775 | 128 | 101 | 294 |
| `M13D` | 3,179 | 1,126 | 128 | 100 | 233 |
| `M12F` | 1,907 | 1,131 | 122 | 94 | 232 |
| `M16G` | 964 | 498 | 114 | 88 | 352 |
| `M15B` | 1,557 | 853 | 114 | 85 | 276 |
| `M16C` | 850 | 480 | 100 | 71 | 317 |
| `DM-Ha'tak` | 1,584 | 1,032 | 84 | 53 | 216 |
| `SGCTF-AbydosVsSGC` | 5,430 | 2,875 | 88 | 52 | 284 |
| `CTF-Abydos` | 3,983 | 2,553 | 83 | 51 | 297 |
| `DM-WorstCaseScenario` | 4,017 | 2,062 | 67 | 43 | 297 |
| `DM-KelownanTrain` | 1,083 | 525 | 62 | 33 | 262 |
| `DM-Langara` | 1,497 | 1,243 | 58 | 30 | 188 |
| `DM-Tomb` | 3,901 | 3,682 | 52 | 29 | 271 |
| `DM-Subterranean` | 605 | 345 | 49 | 26 | 206 |
| `CTF-LostCity` | 1,719 | 794 | 44 | 17 | 213 |
| `DM-LostCity` | 1,731 | 802 | 42 | 17 | 220 |
| `Entry` | 30 | 23 | 21 | 16 | 61 |
<!-- /generated:map-name-values -->

## What this measures, and what it does not

* It measures the **whole table**, not a sample: the four arms partition the distinct entries of all 73 maps,
  so a row cannot be counted twice and nothing falls outside the split.
* It does not read a `Level` export's own serialised data, and the corpus gives no reason to: no map export in
  this build carries a script object, and the names that looked like they needed one are property values on
  the actors that name them (`docs/MAP_SCRIPTS.md`, `docs/MAP_ACTION_WIRING.md`).
* It does not claim a *property* name is vocabulary in every case — a property name is vocabulary because
  nothing else in the package names it, which is exactly what the classification says.
* An array-valued name property is read one element at a time by the property reader, so an element index is
  a separate value; the counts are element counts, not property counts.

## Regenerating

    make map-name-values        # sweep the 73 maps, rewrite the artifact and this page's block
    python3 tools/map-name-values.py --check      # synthetic name tables, no dump, writes nothing
    python3 tools/map-name-values.py --check-doc docs/MAP_NAME_VALUES.md

`--check` is driven by `tool-check-contract` in `make ci`; `--check-doc` renders this block from the committed
`inventory/map_name_values.csv` alone, so the page and the artifact cannot drift.
