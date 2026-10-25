# The package check file, measured

`System/Packages.md5` is the game's own integrity list, and PLAN 1.4 asks which shipped packages satisfy it.
None of them do, and this page now says exactly what the file holds and everything that was ruled out — because
the first version of it was reading the file wrong in a way that guaranteed a mismatch.

    python3 tools/package-check.py DUMP/.../System -o inventory

## What the file is

An **Unreal Engine 2 package**, not text:

| field | value |
|---|---|
| tag | `0x9E2A83C1` (UE2 package) |
| version / licensee | 130 / 4 |
| package flags | `0x00000001` |
| name table | 175 entries at `0x44` |
| exports | 168 at `0x43D3` |
| imports | 2 at `0x43C5` (`Core`, `Engine`) |
| size | 20,111 bytes |

Its 168 exports are `PackageCheckInfo0` … `PackageCheckInfo167`. Each one is 78 bytes and has a **fixed
layout with no filename field**:

```
00 21 <32 uppercase hex> 00 01 21 <32 lowercase hex> 00  00×7
```

So there are two digests per record — same length, different case *and* different value — and no name to tie a
record to a file. The record order is the only ordering there is, and the name table's `PackageCheckInfoN` names
are its index. 168 records for 18 shipped `.u` files: the list covers the whole game's package set, not the
scripts in `System/`.

## The bug this page used to hide

The first version of `tools/package-check.py` computed raw 16-byte MD5 digests and searched for those bytes in
the file. **The file stores hex text**, so that search could never succeed no matter what the transform was —
"none of the four hash schemes appears in the check file" was true and meaningless at the same time. The tool now
compares hex, case-insensitively, and parses the records with the layout above.

## What has actually been ruled out

| search | scope | result |
|---|---|---|
| plain `md5` of the file as shipped | **all 4,003 files in the dump** | 0 matches |
| 89 range/zeroing variants of `md5` | `Engine.u`: starts at 0/4/8/12/16/32/36/52/64/`hdr`/`names`/`imports`/`exports`, end trims of 4–64 bytes, GUID / version / tag / header-size zeroed | 0 matches |
| 8 name- and data-shaped variants | **489 package files** (`.u`, `.unr`, `.uax`, `.utx`, `.usx`, `.ukx`): `md5(name)`, lower, upper, data+name, name+data, trailing-NUL strip, data+own-md5, `data[0x44:]` | 0 matches |

That is a measured negative, not a shrug: the digests are not a hash of any shipped file under any framing
tried, and roughly 500 files were covered rather than 18.

## What that leaves

Three explanations survive, none measured yet:

1. **The digest is of the source package, before cooking.** A rebuild cannot reproduce it without the original
   cook inputs, which the dump does not contain.
2. **The digest is keyed or salted** — two values per record suggests two inputs or two algorithms rather than
   one hash written twice.
3. **The exported files were rewritten by whoever extracted this dump**, so the recorded digests describe the
   originals.

Because the digest set and the package list are both inside this 20 KB file, the question is still tractable
offline. What is *not* tractable is pairing a digest with a filename: the record has no name field, so any
mapping would have to come from the order the cooker wrote them in, which is an assumption, not a measurement.

For PLAN 6.2 that means: satisfying the check by regenerating it is out of reach until the transform is known,
so the practical route for a rebuilt game stays **bypass** — patch or skip the check — and this page is the
evidence for saying so.

## Not measured

* The transform behind the digests (above).
* Which packages the 168 records describe, and in what order.
* Whether the runtime checks them at all in this build, or only on a command-line path — that needs the runtime
  probe in U5 / 7.3 territory.
