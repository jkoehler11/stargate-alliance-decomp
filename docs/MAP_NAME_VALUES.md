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
**13,329 of a map corpus's 249,426 distinct name-table entries (5.3%) exist because a placed actor's property names them -- not because the package imports, exports or declares that name.** A name value is data: `SuggestedSquadScript` is a declared `UNameProperty` on `IntelligentSpawner`, so the roster a map ships is a property value and needs no import, no export and no `Level` export to parse.

| measure | answer |
|---|---|
| maps read | **73** |
| exports over those maps | **194,190** |
| exports with a readable property block | **136,202** |
| name-typed property values | **123,424** |
| distinct name values | **15,578** |
| -- equal to an export's object name | **205** |
| -- equal to an import's name | **2,043** |
| -- equal to neither (data only) | **13,330** |
| -- absent from the name table (the join's control) | **1** |
| `Name` tags refused as mis-splits (wider than an index) | **58** |
| name-table entries | **249,426** |
| -- distinct | **249,426** |
| -- needed only by a name value | **13,329** (5.3%) |
| -- vocabulary (neither object nor value) | **25,134** (10.1%) |
| -- an export's or import's own name | **210,963** (84.6%) |
| squad-script pairs in `inventory/squad_scripts.csv` | **630** |
| -- of those named as a data-only value in their own map | **630** |

The four arms partition the table -- **210,963** objects, **13,329** value-only strings, **25,134** vocabulary, totalling the **249,426** distinct entries the 73 maps need -- so no row is counted twice and the split is the whole table, not a sample of it. **2,248** distinct values resolve to an object (`205` an export's own name, `2,043` an import's), so a `Tag` that repeats an actor's name costs the table nothing.

**The join's own control is the refusals, not a hope.** A `Name` tag's value region is an `FCompactIndex`, at most five bytes, so a wider one cannot be a name index and is counted as a mis-split rather than resolved to the reader's `?`: **58** tags over 28 maps, every one of the shape a `Vector` or a `ZoneNumber` value read as a type-6 tag. After that filter **1** value in the whole corpus names no table entry -- one out-of-range index, not a systematic miss -- which is the assertion that the string comparison is right: an index can only resolve inside the table.

### The properties that carry names

| property | values | distinct | data-only | maps |
|---|---:|---:|---:|---:|
| `Tag` | 10,785 | 7,048 | 8,901 | 73 |
| `Event` | 2,856 | 2,546 | 2,834 | 71 |
| `ExternalEvent` | 1,894 | 1,655 | 1,890 | 70 |
| `DestinationTag` | 1,662 | 1,627 | 1,650 | 61 |
| `Group` | 797 | 448 | 796 | 72 |
| `DialogueName` | 709 | 703 | 709 | 58 |
| `SuggestedSquadScript` | 630 | 617 | 630 | 61 |
| `TargetTag` | 585 | 404 | 407 | 58 |
| `PawnTag` | 538 | 326 | 342 | 61 |
| `ScriptTargetTag` | 507 | 502 | 506 | 54 |
| `ViewTargetTag` | 440 | 384 | 385 | 56 |
| `EventName` | 387 | 376 | 384 | 45 |
| `DeathEvent` | 323 | 289 | 323 | 59 |
| `DialogueFinishedEvent` | 256 | 256 | 256 | 33 |
| `InitialState` | 216 | 11 | 216 | 67 |

### By map

| map | exports | name values | distinct | data-only entries | vocabulary |
|---|---:|---:|---:|---:|---:|
| `M02A` | 3,758 | 2,235 | 410 | 377 | 417 |
| `M00` | 5,078 | 3,011 | 408 | 371 | 346 |
| `M07B` | 3,803 | 2,105 | 391 | 355 | 412 |
| `M15A` | 3,492 | 2,160 | 380 | 343 | 415 |
| `M10D` | 3,832 | 2,294 | 369 | 335 | 494 |
| `M07C` | 3,810 | 2,386 | 364 | 324 | 370 |
| `M07D` | 4,214 | 2,884 | 345 | 313 | 369 |
| `M13A` | 3,710 | 2,172 | 331 | 304 | 362 |
| `M10A` | 3,542 | 2,090 | 325 | 298 | 463 |
| `M14A` | 3,644 | 2,535 | 338 | 296 | 361 |
| `M10B` | 4,783 | 2,758 | 328 | 295 | 452 |
| `M16A` | 2,403 | 1,497 | 319 | 289 | 489 |
| `M17B` | 3,887 | 3,913 | 319 | 284 | 401 |
| `M07E` | 3,529 | 2,331 | 311 | 283 | 391 |
| `M21A` | 3,002 | 2,125 | 317 | 278 | 476 |
| `M14C` | 2,436 | 1,589 | 286 | 256 | 331 |
| `M17A` | 3,384 | 2,745 | 288 | 256 | 406 |
| `M18B` | 3,361 | 3,445 | 282 | 249 | 343 |
| `M20A` | 2,730 | 1,577 | 278 | 245 | 333 |
| `M02B` | 2,793 | 1,562 | 277 | 244 | 400 |
| `M21D` | 3,535 | 2,378 | 286 | 243 | 440 |
| `M19E` | 2,238 | 1,501 | 274 | 241 | 389 |
| `M07A` | 3,323 | 1,758 | 267 | 237 | 392 |
| `M02C` | 2,877 | 1,618 | 266 | 234 | 401 |
| `M19B` | 2,163 | 1,170 | 265 | 234 | 367 |
| `M12C` | 3,512 | 1,928 | 266 | 232 | 368 |
| `M20D` | 2,147 | 1,206 | 243 | 214 | 304 |
| `M17D` | 2,903 | 2,848 | 238 | 209 | 326 |
| `M18D` | 2,891 | 2,138 | 243 | 209 | 367 |
| `M13C` | 2,415 | 1,371 | 231 | 207 | 333 |
| `M21B` | 2,619 | 2,222 | 237 | 206 | 458 |
| `M15F` | 2,600 | 1,524 | 239 | 203 | 346 |
| `M20C` | 3,524 | 2,255 | 222 | 193 | 328 |
| `M04B` | 1,210 | 653 | 216 | 190 | 398 |
| `M12A` | 3,127 | 1,593 | 228 | 190 | 367 |
| `M12D` | 3,020 | 1,523 | 222 | 189 | 353 |
| `M21C` | 3,028 | 2,397 | 220 | 188 | 387 |
| `M04a` | 1,715 | 905 | 213 | 183 | 398 |
| `M12B` | 2,789 | 1,484 | 218 | 183 | 373 |
| `M18A` | 2,468 | 2,727 | 198 | 167 | 319 |
| `M16F` | 1,283 | 1,170 | 191 | 165 | 402 |
| `M15C` | 2,840 | 1,879 | 194 | 159 | 325 |
| `M15E` | 1,969 | 1,096 | 189 | 159 | 300 |
| `M19A` | 2,136 | 1,009 | 194 | 154 | 397 |
| `M13B` | 1,401 | 758 | 177 | 153 | 296 |
| `M16D` | 1,999 | 897 | 173 | 145 | 401 |
| `M15D` | 2,828 | 1,744 | 178 | 142 | 342 |
| `M20B` | 3,059 | 1,657 | 170 | 142 | 310 |
| `M10C` | 2,705 | 1,543 | 170 | 140 | 399 |
| `M19C` | 1,078 | 710 | 164 | 140 | 349 |
| `M19D` | 1,965 | 1,353 | 182 | 136 | 370 |
| `M16E` | 1,224 | 595 | 162 | 134 | 356 |
| `M14B` | 2,996 | 1,877 | 156 | 128 | 266 |
| `M02D` | 1,939 | 1,082 | 159 | 127 | 342 |
| `M16B` | 1,489 | 795 | 150 | 118 | 358 |
| `M13D` | 3,179 | 1,139 | 139 | 110 | 223 |
| `MainMenu` | 958 | 598 | 126 | 108 | 283 |
| `M12E` | 2,989 | 1,777 | 129 | 102 | 293 |
| `M12F` | 1,907 | 1,216 | 124 | 96 | 230 |
| `M16G` | 964 | 510 | 118 | 91 | 349 |
| `M15B` | 1,557 | 853 | 114 | 85 | 276 |
| `M16C` | 850 | 546 | 102 | 74 | 314 |
| `DM-Ha'tak` | 1,584 | 1,042 | 85 | 53 | 216 |
| `SGCTF-AbydosVsSGC` | 5,430 | 2,879 | 89 | 53 | 283 |
| `CTF-Abydos` | 3,983 | 2,553 | 83 | 51 | 297 |
| `DM-WorstCaseScenario` | 4,017 | 2,081 | 67 | 43 | 297 |
| `DM-KelownanTrain` | 1,083 | 526 | 63 | 34 | 261 |
| `DM-Langara` | 1,497 | 1,243 | 58 | 30 | 188 |
| `DM-Tomb` | 3,901 | 3,683 | 53 | 29 | 271 |
| `DM-Subterranean` | 605 | 345 | 49 | 26 | 206 |
| `CTF-LostCity` | 1,719 | 799 | 47 | 20 | 210 |
| `DM-LostCity` | 1,731 | 833 | 44 | 19 | 218 |
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
