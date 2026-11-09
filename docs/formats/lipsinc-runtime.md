# `.lad` in the engine — how a controller becomes bone poses

The container is measured end to end in [lipsinc-lad.md](lipsinc-lad.md): header, channel table, twenty pose
frames, a clip section and a track table of `<Map>.<Animation>` records. What was missing is the *other* half —
what the engine does with a channel name and with a key block. This page is that half, read from the image.
Every address below is a function the LIPSinc module's own log strings already named
(`inventory/lipsinc_runtime.csv`), and the reader the format notes kept pointing at turned out to be the wrong
one: `LIPSincReadAnimFile` and `LIPSincReadControllerFile` are **text** parsers (LTF and LBP), and no `.lad`
byte goes through either of them.

## The path a `.lad` takes

`LIPSincInitLevel` (`0x000ba750`) is the entry point:

* it finds the files itself — `FUN_000192a0(L"*.lad")` — and logs `Searching for .lad files... %s` and
  `Found files...` around the result;
* each found name is compared with `_wcsstr` against the level's controller-prefix list (0xc-byte `FString`
  entries, count at `+0x5a4` of the level object) and the stems that match are copied into a list;
* that list goes to `LIPSincLoadDBFromDisk` (`0x000b9e90`), which walks it, strips the directory (`\\`) and the
  extension (`.`) off each name and hands the **stem** on.

So the file name is the controller name: `Controllers/sg1_crew_carter.lad` is the controller `sg1_crew_carter`,
and the directory is presentation only.

`LIPSincLoadDBFromDisk` parses nothing. For each stem it constructs an object of the class at
`PTR_005bf508` **by name** (a UE2-style factory call with the name as the object name), hands it the stem
through `FUN_000b9d50`, and releases it again. `FUN_000b9d50` in turn calls `FUN_000bce40(object, stem)`, which
is the parse: a chain of stages, each handed the object and writing at a fixed offset —

| stage | writes at |
|---|---|
| `FUN_00037110` (twice) | `+0x00`, `+0x14` |
| `FUN_00035b60` | `+0x18`, `+0x30`, `+0x48`, `+0x54` |
| `FUN_000bc470` | (returns the next stage's object) |
| `FUN_000bcb60` | (returns the next stage's object) |
| `FUN_000bcd30` | (returns the next stage's object) |
| `FUN_00037110` | `+0x60` |
| `FUN_00058850` (three times) | `+0x64`, `+0x70`, `+0x7c` |

which stage is the channel table and which is the track section is **not** read yet; it is a five-function
question against a chain that is already located (see *What is left*).

## The channel table is linked to bones by name

`LIPSincLinkBones` (`0x00225cd0`) is called once a level's skeletal meshes exist. It finds the controller for
the mesh's name (`FUN_000b5250`: `_wcscmp` over `count` names at `+0x14`, returning `index * 0x94 +
*(this + 0x10)` — the controller store is an array of **0x94-byte** records) and then calls `FUN_000b4930`,
which is the link:

* for each channel index `i` (count at controller `+0x08`) it reads the channel's name (`FUN_000b48e0`; the
  names are 0xc-byte `FString` entries at `+0x04`);
* it compares that name with `_wcsicmp` against every bone name of the mesh (0x40-byte bone entries at
  `mesh + 0x1dc`, count at `mesh + 0x1e0`) — case-insensitive, so the file's `BN-Head` is the mesh's `BN-Head`;
* on a match it writes the **mesh bone index** `j` into a dword array on the controller: `*(controller + 0x10)
  + i * 4 = j`. That is the only place a channel number and a bone meet, and the file never states it — the
  runtime derives it from the names;
* a channel that matches nothing is appended (name only) to a caller-supplied list, and the function logs
  `is valid controller` only when **every** channel matched, `NOT VALID CONTROLLER!` otherwise.

So the channel table is the file's bone list because the loader resolves it as one; a pack whose names do not
exist on the mesh is rejected rather than partially applied.

After linking, a bone is an **id**, not a name: `FUN_000b3880` (the pose builder, `ERROR -> bone %d doesn't
exist in base set!`) searches the mesh's id array (`+0x24`, count `+0x28`) for the record's id and indexes the
transforms at a 0x20 stride (`+0x3c`), reading a 32-byte record whose fields are the same shape the container
carries — three floats, three words that are not float data, and a float that is negated on the way out
(`+0x1c`).

## The channel names are bones of five skeletons

The name comparison above is only meaningful if the two sides exist in the data, so `tools/lad-bone-owners.py`
(`make lipsinc-bone-owners`, `inventory/lipsinc_bone_owners.csv`, `--check` in `make ci`) reads all eight
channel tables and scans the name table of every UE2 package in the dump (**482** of the corpus's packages
parse; the tool refuses a file whose name table it cannot tile rather than guessing):

**all 21 channel names are declared by exactly five packages, and by nothing else** —
`Animations/Grey_anim.ukx`, `Animations/SGC_soldiers_anim.ukx`, `Animations/jaffa_anim.ukx`,
`Animations/sg1_anim.ukx`, `Animations/tokra_anim.ukx`. One facial skeleton family, five characters' animation
sets, and a bone list identical in all five: `BN-Blink_L/R`, `BN-Cheek-Left/Right`, four brow bones, `BN-Jaw`,
`BN-Jaw-Pivot`, nine `BN-LIPS-*`, `BN-Head`, and the two tongues.

The two tongue bones are the exception in the *packs*, not in the skeletons: all five skeletons declare them,
but only the four 21-channel packs list them as channels (`file_count` 4 against 8 in the CSV), which is the same
two-bone difference the channel-table measurement found from the file side.

That is also the answer to "which weight slot is which bone": **none of them is a bone slot.** The runtime maps
one dword per *channel*, by name; a track's 26 key groups belong to an animation, and the 21- and 19-channel
packs carry the same 26 — so a group is not a channel and the slot table is a property of the track schema.

## Why the two named readers were the wrong half

Both functions the format notes pointed at read **text**, not the container:

* `LIPSincReadAnimFile` (`0x000b8340`) splits each line with `Core_UnTemplate(..., L" ")` and converts fields
  with `__wtof`/`__wtol`; its strings are `Read Track %s`, `ERROR: %s is a more recent LTF format`, `ERROR: %s
  is not a valid LTF file!` and `Parsed %s`. One shape is worth carrying forward: its track loop flushes the
  key array every **26** (time, value) pairs — `(i / 2 + 1) % 0x1a == 0` — the same number the binary key block
  uses for its group count. Recorded, not explained.
* `LIPSincReadControllerFile` (`0x000b7260`) does the same, with `Parsed %s`, `WARNING: LIPSinc bone linkup
  failed!` and `Attempt to import an obsolete .LBP file!`, and fills a pose array with a 32-byte stride.

So the `.lad` binary is read by the controller object above (LTF/LBP are the middleware's authoring formats), and
an earlier note's "the reader is the only witness left" was pointing at the wrong two functions.

## What is left

* **Which stage of `FUN_000bce40` is which section** — five stages (`0x00035b60`, `0x000bc470`, `0x000bcb60`,
  `0x000bcd30`, `0x00058850`) for five parts of the file (channel table, pose frames, clip section, track table,
  key blocks). Mapping stage to section is the read that would put the record fields of the container on a
  *writer's* footing instead of a census.
* **What the 7 signed slots mean.** Nothing in the name path touches them: the link is one dword per channel and
  the signed slots are not channels.
