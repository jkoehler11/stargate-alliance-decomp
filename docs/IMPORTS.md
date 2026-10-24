# The import surface: what this XBE was linked against (PLAN 2.6)

Short answer: **there is no import table to enumerate.** The image links its libraries statically and
records them in one table of 13 entries; there is no thunk list, no ordinal list, and therefore no
imported function the symbol map could name. What the header does carry, and what the sections show,
is measured below.

## The header fields

| header field | value in this image |
|---|---|
| non-kernel import address | `0x00000000` |
| library version table | `0x00010738`, **13** entries, 16 bytes each |
| kernel image thunk address | `0xeffd9a72` — not a VA in this image, i.e. the field is unused |

An XBE with imports would list them as an array of thunk descriptors there; all three signals agree
that this build has none.

## The 13 libraries (`inventory/xbe_libraries.csv`)

Every one is version **1.0.5849**, which is the Xbox SDK build the game was compiled against:

| library | flags | code lives in |
|---|---|---|
| `XAPILIB` | `0x4001` | `.text` (linked in) |
| `UIX` | `0x4007` | `.text` |
| `XONLINE` | `0x0007` | section `XONLINE` |
| `D3D8` | `0x4001` | section `D3D` |
| `D3DX8` | `0x4001` | section `D3DX` |
| `XKBD` | `0x0001` | `.text` |
| `XGRAPHC` | `0x4001` | section `XGRPH` |
| `DSOUND` | `0x4001` | section `DSOUND` |
| `XBOXKRNL` | `0x4001` | not in the image (the kernel) |
| `XVOICE` | `0x4004` | not identified |
| `VOICMAIL` | `0x4804` | not identified |
| `LIBCP` | `0x4001` | `.text` |
| `LIBC` | `0x4001` | `.text` |

The section column is certain only where the section is named after the library (`XONLINE`, `D3D`,
`D3DX`, `XGRPH`, `DSOUND`); the rest are marked "in .text" when the library is linked into the code
section, and `XVOICE`/`VOICMAIL` are left unidentified rather than guessed at (`XPP`, `XON_RD` and
`DOLBY` are the remaining candidate sections).

## What the sections say about the libraries' size

Functions and bytes attributed by section, from `symbols/xbox_functions.csv` (this is the shape of
the work the signature pass in 4.2 would take on — `docs/UNKNOWNS.md` U9):

| section | functions | named | bytes |
|---|---|---|---|
| `.text` (engine + game + XAPI + CRT) | 11,499 | 1,965 | 3,395,088 |
| `XONLINE` | 704 | 6 | 139,914 |
| `XGRPH` | 642 | 1 | 118,059 |
| `DSOUND` | 427 | 6 | 44,802 |
| `XNET` | 413 | 1 | 74,962 |
| `D3DX` | 379 | 3 | 94,959 |
| `D3D` | 302 | 3 | 59,686 |
| `XPP` | 242 | 2 | 32,775 |
| `.rdata` / `.data` (misidentified as code) | 348 | 0 | 7,741 |

The five library sections add up to **3,109 functions and 565 KB** of code outside `.text`, of which
**22 carry a name**. That is the measurable size of the signature-library gap: naming them needs
MSVC/Xbox-SDK signature data this repository may not carry, so they stay `FUN_*` — an honest unknown
with a body, which is what `docs/NATIVE.md` reports per module.

## What is deferred, and why

- **D3D8 device caps.** The other half of 2.6 asks which caps the renderer requests. That is a runtime
  observation (the device is created through `D3D8`'s `Direct3DCreate8` and `CreateDevice`, both inside
  the `D3D` section with no symbol), so it needs a live run with a probe, not a static read. Recorded in
  `docs/UNKNOWNS.md` U11 rather than guessed from the cap bitmask constants in `.rdata`.
- **Ordinal-to-name tables.** Enumerating ordinals would only be useful with a name table, and that
  table is SDK-derived (U9). There are no ordinals in this image to enumerate anyway.

## Reproducing

```
python3 tools/xbe-libraries.py DUMP/default.xbe -o inventory
```

The tool fails loudly if the table does not parse (bad address, implausible count, an entry with no
name), so a different build cannot silently produce a plausible-looking wrong table.
