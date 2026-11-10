# The library sections: what the SDK code says about itself (PLAN 4.2, U9)

`docs/IMPORTS.md` sized 4.2's gap: the image links its libraries statically and records them in a table
of 13 entries, but the code of seven of them sits in sections named after the library — `XNET`,
`XONLINE`, `D3D`, `D3DX`, `XGRPH`, `DSOUND`, `XPP` — where the function map carries no name for it. In
the engine modules a body that asserts names the `.cpp` it was compiled from, and `tools/body-read.py`
turns that into translation units, classes and a work list; this page asks the same question of the
library sections, because it decides whether 4.2 has a *static* route at all.

It does not. The table below is the whole measurement (`make library-bodies`, artifact
`inventory/library_bodies.csv`, `--check` in `make ci`): it counts, per section, the bodies that
reference any string, the ones that reach a string through a `push imm32` rather than a data-table dword,
the ones that reference a build-tree source path, and the direct calls a body makes, split into calls
into its own section, calls that leave it, and calls that land on no known body.

<!-- generated:library-bodies -->
| section | functions | named | `thunk_` | bytes | any literal | `push` literal | source path | wide | calls | into the section | leaves it | no known body |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| `XNET` | 413 | 0 | 1 | 74962 | 12 | 1 | 0 | 8 | 1304 | 1061 | 111 | 5 |
| `XONLINE` | 704 | 0 | 6 | 139914 | 61 | 39 | 0 | 5 | 3123 | 2859 | 690 | 19 |
| `D3D` | 302 | 0 | 3 | 59686 | 6 | 1 | 0 | 9 | 917 | 655 | 58 | 6 |
| `D3DX` | 379 | 0 | 3 | 94959 | 26 | 21 | 0 | 5 | 1223 | 814 | 212 | 30 |
| `XGRPH` | 642 | 1 | 0 | 118059 | 52 | 44 | 0 | 4 | 3306 | 2752 | 340 | 42 |
| `DSOUND` | 427 | 0 | 6 | 44802 | 6 | 0 | 0 | 6 | 1042 | 929 | 38 | 7 |
| `XPP` | 242 | 0 | 2 | 32775 | 6 | 1 | 0 | 1 | 564 | 481 | 14 | 5 |
| **all library sections** | **3109** | **1** | **21** | **565157** | **169** | **107** | **0** | **38** | **11479** | **9551** | **1463** | **114** |

Of the **22** names the map carries in these sections, **21** are `thunk_` jump stubs and **1** is a real C++ name, so **1** of the 3109 bodies is semantically named.

**169 of 3109** library bodies reference any string at all (**107** through a `push imm32`, **62** only through a data-table dword), **0** reference a build-tree source path, and **38** reference a wide literal. Direct calls resolve to a known body 9551 times, 1463 of them from a body in another section.

`XON_RD` and `DOLBY` also hold code (`tools/xbe.py DUMP/default.xbe sections` prints their extents) but the function map names no body in either, so nothing there was scanned.
The library sections' whole string vocabulary: **363** distinct strings, top 60 by how many bodies reference them.

| literal | sections | bodies |
|---|---|---:|
| `hHyL` | D3D, D3DX, XGRPH, XNET, XONLINE, XPP | 18 |
| `/xpnfront/xmessaging.srf` | XONLINE | 7 |
| `contentmeta.xbx` | XONLINE | 5 |
| `h<yL` | XGRPH, XONLINE | 5 |
| `hd}L` | DSOUND, XONLINE, XPP | 5 |
| `error` | XGRPH | 4 |
| `urn:schemas-upnp-org:service:WANPPPConnection:1` | XONLINE | 3 |
| `urn:schemas-upnp-org:service:WANIPConnection:1` | XONLINE | 3 |
| `symbol expected` | XGRPH | 3 |
| `$.$$$` | XONLINE | 3 |
| `hP}L` | XGRPH, XNET, XONLINE | 3 |
| `\Device\Harddisk0\partition1\CACHE%.4s\LocalCache%02d.bin` | XONLINE | 2 |
| `Ignoring incorrect gAMA value when sRGB is also present` | D3DX | 2 |
| ` \| PS_FINALCOMBINERSETTING_COMPLEMENT_R0)` | XGRPH | 2 |
| ` \| PS_FINALCOMBINERSETTING_COMPLEMENT_V1` | XGRPH | 2 |
| `    PS_FINALCOMBINERSETTING_CLAMP_SUM` | XGRPH | 2 |
| ` \| PS_COMBINEROUTPUT_AB_BLUE_TO_ALPHA` | XGRPH | 2 |
| ` \| PS_COMBINEROUTPUT_CD_BLUE_TO_ALPHA` | XGRPH | 2 |
| `951F0EF630DC46d9_CORRUPT_SECTOR` | XPP | 2 |
| `Expected macro parameter.` | XGRPH | 2 |
| `Duplicate argument name.` | XGRPH | 2 |
| `Expected argument name.` | XGRPH | 2 |
| `    %s \| %s \| %s \| %s` | XGRPH | 2 |
| `Extra compressed data` | D3DX | 2 |
| `Not enough image data` | D3DX | 2 |
| `/stfd/writefile.ashx` | XONLINE | 2 |
| `Too many arguments` | XGRPH | 2 |
| `Unexpected symbol.` | XGRPH | 2 |
| `Too few arguments` | XGRPH | 2 |
| `signaturekey` | XONLINE | 2 |
| `syntax error` | XGRPH | 2 |
| `1.00.5849.1` | D3D, XGRPH | 2 |
| `    0` | XGRPH | 2 |
| ` \| 0` | XGRPH | 2 |
| `%s%s` | XGRPH, XONLINE | 2 |
| `IDAT` | D3DX | 2 |
| `Xbox's EXP macro is no more accurate than EXPP, and it is slower. Consider just using EXPP, or writing your own implementation if you need greater accuracy.` | XGRPH | 1 |
| `Xbox's LOG macro is no more accurate than LOGP, and it is slower. Consider just using LOGP, or writing your own implementation if you need greater accuracy.` | XGRPH | 1 |
| `single-line comment ends with the line-continuation character (\). The next line will be treated as a continuation of the comment.` | XGRPH | 1 |
| `Can't process #include because no resolver object was supplied to preprocessor function` | XGRPH | 1 |
| `Input Pixel Shader Microcode was wrong length. sizeof(D3DPIXELSHADERDEF_FILE)` | XGRPH | 1 |
| `Internal vertex shader optimizer error. Disable optimizations and try again.` | XGRPH | 1 |
| `//msg.%02x/u:%016I64x/%08x-%04x-%04x-%02x%02x-%02x%02x%02x%02x%02x%02x_%02x` | XONLINE | 1 |
| `Expected a version statement ('ps', 'xps', 'vs', 'xvs', 'xvss', or 'xvsw').` | XGRPH | 1 |
| `Instruction combination is not allowed in a vs shader. Use xvs instead.` | XGRPH | 1 |
| `matrix macro expands to use out-of-range constant register (c96, etc)` | XGRPH | 1 |
| `Too many levels of include directives. Maximum of %d levels allowed.` | XGRPH | 1 |
| `Invalid instruction combination. Input swizzles can't be combined.` | XGRPH | 1 |
| `Read from uninitialized register. Turn on validator and try again.` | XGRPH | 1 |
| `It's an error to set both read_data_fn and write_data_fn in the ` | D3DX | 1 |
| `Invalid instruction combination. Opcodes can't be combined.` | XGRPH | 1 |
| `Invalid instruction combination. Outputs can't be combined.` | XGRPH | 1 |
| `aint found between bodies using different bKDoubleTickRate.` | XNET | 1 |
| `-sg1\Game\GameXBOX\stargatevehicles\src\StargateCopter.cpp` | XNET | 1 |
| `Invalid instruction combination. Inputs can't be combined.` | XGRPH | 1 |
| `source modifiers are not allowed on destination registers` | XGRPH | 1 |
| `targate-sg1\Game\GameXBOX\D3DDrv\Src\D3DMaterialState.cpp` | XNET | 1 |
| `Incompatible libpng version in application and library` | D3DX | 1 |
| `Too many nested levels of #ifdef/#ifndef in this file.` | XGRPH | 1 |
| `No temporary register available for EXP / LOG macro.` | XGRPH | 1 |

*...and 303 more distinct strings in `inventory/library_bodies.csv`.*
<!-- /generated:library-bodies -->

## What the absence means

The engine-side method does not transfer: an engine body's assert is a `__FILE__` literal, and a library
body has none. That is not a property of the SDK's code in general — it is a property of *this* build: the
libraries were compiled without asserts (or with them compiled out), so the only strings left are the ones
the code *uses* rather than the ones it would fail with. A handful of bodies still speak, and they say what
the library is for rather than what any function is called:

- **`XGRPH` + `D3DX` are the XDK's shader assembler.** Their literals are instruction-combination
  diagnostics, version statements (`ps`, `xps`, `vs`, `xvs`, `xvss`, `xvsw`), the
  `PS_COMBINEROUTPUT_*`/`PS_FINALCOMBINERSETTING_*` tables, `#include`/preprocessor errors and the
  "Xbox's EXP/LOG macro" notes — i.e. `xgraphics` compiling pixel and vertex shaders, plus the libpng
  messages D3DX carries for its texture loaders.
- **`XONLINE` is an Xbox Live client.** Its strings are HTTP and UPnP (`/xpnfront/xmessaging.srf`,
  `/stfd/writefile.ashx`, `urn:schemas-upnp-org:service:WANIPConnection:1` and `…WANPPPConnection:1`),
  plus title-storage paths (`\Device\Harddisk0\partition1\CACHE%.4s\LocalCache%02d.bin`,
  `contentmeta.xbx`, `signaturekey`).
- **`D3D` is D3D8 proper and is silent.** One body carries `1.00.5849.1` and `%s:%s` — the SDK version
  the XBE's library table already records — and that is the only string of substance in the section.
  `DSOUND` carries none at all, `XNET` and `XPP` one each.
- **The library is not a sealed box.** Roughly one in seven of its resolved direct calls leaves the
  section, i.e. the SDK code calls back into `.text` (the CRT and XAPI half of the image), and a smaller
  number land on no known body at all (jump tables and the thunks the map already names). PLAN 4.8 measured the
  engine calling *into* the device through a table of vtable offsets; this is the other direction of the
  same boundary, and it is why the two modules cannot be read independently.

`XON_RD` and `DOLBY` hold code as well (`tools/xbe.py … sections` prints their extents) but the function
map names no body in either, so no body there was scanned. That is a gap in the function map, not evidence
about the libraries — and it is worth knowing before a signature pass is scored: two sections' worth of
code is not even in the population being scored.

## Why the names still are not there, and what is left

RTTI would have been the other class-level route — MSVC's `.?AV<Class>@@` descriptors name the classes
that carry them — and it is not there either: the image holds four type descriptors in total, all of them
the CRT's exception classes (`exception`, `std::logic_error`, `std::length_error`,
`std::out_of_range`). The one real C++ name the map does carry in these sections
(`?assign@?$basic_string@…`) is an artifact of that same RTTI/CRT corner, not a naming method.

So 4.2's static half is closed, and `docs/UNKNOWNS.md` U9 stands as written: what names these 3,109 bodies
is a signature database supplied locally (an MSVC 7.1/Xbox-SDK FLIRT set), or a runtime observation that
pairs a body with behaviour — which is U11's route for the device caps, not a naming pass. The useful
number for the next session is therefore not a count of bodies but the boundary: the section table above is
the population a signature pass would be scored against, and `inventory/library_bodies.csv` carries each
body's call targets so a partial database's effect is measurable rather than estimated.

## Reproducing

```
python3 tools/library-bodies.py DUMP/default.xbe -o inventory/library_bodies.csv --write-doc docs/LIBRARIES.md
```

The artifact has one row per body: section, address, name, size, `named`/`thunk` (a `thunk_FUN_*` jump
stub is not a name), the four string counts, the call counts and the body's own literal list (truncated to
2 KB).
