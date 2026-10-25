# The XBE, measured

The header and section table of `default.xbe`, read straight out of the file, plus the one field that
does not decode — recorded because `tools/xbe.py` used to report a value that is not the entry point, and
`tools/ghidra/make-spec.py` printed it to the analyser as one.

    python3 tools/xbe.py DUMP/Stargate_SG-1_-_The_Alliance-2005-12-15/default.xbe sections

## Header

| offset | field | value | note |
|---|---|---|---|
| `0x000` | magic | `XBEH` | |
| `0x104` | base address | `0x00010000` | everything below is a VA from here |
| `0x108` | size of headers | `0x00000B44` | headers live at `0x10000`–`0x10B44` |
| `0x10C` | size of image | `0x007D95E0` | 8,229,344 bytes |
| `0x110` | size of image header | `0x00000184` | |
| `0x114` | timestamp | `0x43A0C3CB` | build date |
| `0x118` | **certificate** | `0x00010184` | verified, see below |
| `0x11C` | section count | `0x0000000F` | **15**, not 5 |
| `0x120` | section headers | `0x00010370` | inside the header block |
| `0x124` | init flags | `0x00000001` | |
| `0x128` | entry point field | `0x94B66E5C` | **does not decode**, see below |
| `0x12C` | TLS | `0x0053FE74` | lands in `.rdata` |
| `0x130` | stack commit | `0x00100000` | |
| `0x134` | heap reserve | `0x00100000` | |
| `0x138` | heap commit | `0x00001000` | |
| `0x13C` | PE base | `0x00010B40` | |
| `0x140` | image size in memory | `0x00813980` | larger than the file image |

The certificate at `0x10184` is not a guess: at that offset the file holds a size field of `0x1EC`, the
same timestamp as the header, and a UTF-16 title string reading `Stargate SG-1`.

## The entry-point field does not decode

The documented layout has an entry-point address at `0x128`, XOR-encrypted with `0xA8FC57AB`. Decrypting
this file's value gives `0x3C4A39F7`, which is **outside every section** — `.text` ends at `0x4190FC`, and
the largest mapped VA is `0x7E95E0`. So either the key differs in this build or the field is something
else; either way it cannot be trusted as an address.

What the tools did about it: `tools/xbe.py` used to read offset `0x118` and call it `entry`, so every
`make ghidra` run passed the **certificate address** to `XbePre.java` as the analysis entry point, and
`tools/ghidra/make-spec.py` wrote it into the project spec as `entry=0x10184`. Both are now named for
what they are. Ghidra's own analysis was unaffected in practice — it found code at `.text` start and
followed calls from there — but the seed was meaningless, and anything that trusted it would have been
wrong.

The honest state: **the game's true entry point is not known from the header.** It has to be observed at
run time (single-step or breakpoint from the loader) rather than read. Until then, no tool should claim
one.

## Sections

15 sections, not the 5 an early note assumed. The seven library sections are separate mappings after
`.text`, which is why their code has its own address ranges:

| # | name | flags | VA | virtual size | file size |
|---|---|---|---:|---:|---:|
| 0 | `.text` | `0x36` exec | `0x00011000` | `0x4080FC` | `0x4080FC` |
| 1 | `XNET` | `0x36` exec | `0x00419100` | `0x012CD8` | `0x012CD8` |
| 2 | `XONLINE` | `0x16` exec | `0x0042BDE0` | `0x023250` | `0x022E28` |
| 3 | `D3D` | `0x07` exec | `0x0044F040` | `0x014694` | `0x010DC8` |
| 4 | `D3DX` | `0x07` exec | `0x004636E0` | `0x01A520` | `0x01A518` |
| 5 | `XGRPH` | `0x07` exec | `0x0047DC00` | `0x021748` | `0x020ACC` |
| 6 | `DSOUND` | `0x07` exec | `0x0049F360` | `0x01E68C` | `0x01E424` |
| 7 | `XPP` | `0x07` exec | `0x004BDA00` | `0x009108` | `0x009108` |
| 8 | `.rdata` | `0x06` | `0x004C6B20` | `0x0D22CC` | `0x0D22BC` |
| 9 | `.data` | `0x07` | `0x00598E00` | `0x226BD4` | `0x034B9C` |
| 10 | `XON_RD` | `0x26` | `0x007BF9E0` | `0x001998` | `0x001998` |
| 11 | `DOLBY` | `0x16` | `0x007C1380` | `0x007180` | `0x00716C` |
| 12 | `.data1` | `0x07` | `0x007C8500` | `0x0000E0` | `0x0000B0` |
| 13 | `$$XTIMAGE` | `0x28` | `0x007C85E0` | `0x010800` | `0x010800` |
| 14 | `$$XSIMAGE` | `0x38` | `0x007D8DE0` | `0x010800` | `0x010800` |

Three things this table settles:

* **`.data` is backed by only 215 KB of file** (`0x034B9C`) against 2.25 MB of virtual size — the rest is
  zero-fill, which is where runtime heaps and the package cache end up.
* The **class tables at `0x004C78C0` sit in `.rdata`**, immediately after it starts at `0x004C6B20`, and
  the 462 KB table block is a fraction of that section.
* `$$XTIMAGE` / `$$XSIMAGE` are the dashboard title image and save image, 67,584 bytes each, and are not
  executable — they are presentation, not code.

## Checking

```sh
python3 tools/xbe.py DUMP/default.xbe sections    # header + section table, as above
python3 tools/xbe.py DUMP/default.xbe asserts -o inventory   # assert-path mining, uses the same reader
```

Every VA in this project goes through `Xbe.va_to_offset`, so if a section row here is wrong, the whole
inventory is wrong in the same direction. That is why the reader validates the magic, the header size and
each section rather than trusting the file.
