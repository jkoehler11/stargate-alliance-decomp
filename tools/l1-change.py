#!/usr/bin/env python3
"""Change one function's script bytes in a package and rebuild the container around it.

Every emitter family was built until its bytes matched the shipped ones, so the writer has only ever reproduced files or
moved filler around. It has never been handed a payload that is **different and a different length**. That is the step
between "3,056 bodies are byte-exact" and "a class compiles".

A Function's payload is not just its script: `uscript-dis.locate` finds the script range inside the header and epilogue, so
the new bytes are spliced at `start..end` and the rest of the payload is carried through untouched.

    python3 tools/l1-change.py --package DUMP/System/X.u --function Foo --body 041d9a020000 -o /tmp/X.u

The result is written, re-read with the project's own reader, and checked: the file parses, the function's script reads back
as exactly the bytes given, and no other export's payload moved. What it does **not** show is that the game loads it — that
needs the ISO and xemu, and is the next experiment.
"""

import argparse
import importlib.util
import os
import struct
import sys

# The repo root: prefer the tree we were launched in, so the tool works from a copy outside the repo too.
ROOT = os.environ.get("SG1_ROOT") or (os.getcwd() if os.path.exists(os.path.join(os.getcwd(), "tools", "ue2pkg.py"))
                                      else os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(ROOT, "tools"))

from ue2pkg import Package                                        # noqa: E402

_spec = importlib.util.spec_from_file_location("pkgwrite", os.path.join(ROOT, "tools", "pkg-write.py"))
pw = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(pw)

_spec2 = importlib.util.spec_from_file_location("uscript_dis", os.path.join(ROOT, "tools", "uscript-dis.py"))
sd = importlib.util.module_from_spec(_spec2)
_spec2.loader.exec_module(sd)


def find_function(pkg, cls, name):
    kinds = {i + 1: pkg.class_name_of(e) for i, e in enumerate(pkg.exports)}
    names = {i + 1: pkg.name(e["name"]) for i, e in enumerate(pkg.exports)}
    for i, e in enumerate(pkg.exports):
        if kinds[i + 1] != "Function" or names[i + 1].lower() != name.lower():
            continue
        if cls and sd.owner_class(pkg.exports, kinds, names, e["outer"]).lower() != cls.lower():
            continue
        # `e["index"]` is 0-based in this reader (export 5464 carries index 5464), so returning `i + 1` here applied
        # the new bytes to the *next* export -- which is exactly the kind of off-by-one that reads as a corrupt payload
        return e["index"]
    return None


def rebuild_with(pkg, index, blob, append=False):
    """Replace export `index`'s payload with `blob` (any length).

    Two strategies, and the difference is measured, not stylistic. `append=True` leaves the original file a byte prefix,
    patches one export entry in place and puts the new payload at the old end of file. The relayout path moves every
    payload after the target, and a package rebuilt that way boots the game to a **frozen title** while this project's
    reader accepts it (2026-11-22) -- so the relayout is the suspect, not the bytes.
    """

    if append == "insert":
        # names -> payloads -> imports -> exports: the tables sit *after* the payloads, so a grown payload can be inserted
        # at the end of the payload region and move only the tables. Every payload keeps its original offset.
        exports = [dict(e) for e in pkg.exports]
        target = next((e for e in exports if e["index"] == index), None)
        if target is None:
            raise SystemExit("no export with index %r" % index)
        payload_end = pkg.import_offset
        target["offset"] = payload_end
        target["size"] = len(blob)
        table = pw.encode_exports(exports)
        head = bytearray(pkg.data[:pkg.name_offset])
        struct.pack_into("<I", head, 24, pkg.export_offset + len(blob))
        struct.pack_into("<I", head, 32, pkg.import_offset + len(blob))
        out = bytearray(head)
        out += pkg.data[pkg.name_offset:payload_end]
        out += blob
        out += pkg.data[pkg.import_offset:pkg.export_offset]
        out += table
        return bytes(out)

    if append:
        exports = [dict(e) for e in pkg.exports]
        target = next((e for e in exports if e["index"] == index), None)
        if target is None:
            raise SystemExit("no export with index %r" % index)
        original = pw.encode_exports(pkg.exports)
        at = pkg.export_offset
        if pkg.data[at:at + len(original)] != original:
            raise SystemExit("the export table is not where the header says (byte %d)" % at)
        target["offset"] = len(pkg.data)
        target["size"] = len(blob)
        table = pw.encode_exports(exports)
        if len(table) != len(original):
            # an in-place patch cannot absorb a longer table: it would shift the payloads it addresses
            raise SystemExit("the export table changes length (%d -> %d bytes)" % (len(original), len(table)))
        out = bytearray(pkg.data)
        out[at:at + len(table)] = table
        out += blob
        return bytes(out)

    exports = [dict(e) for e in pkg.exports]
    order = sorted(pkg.exports, key=lambda e: e["offset"])
    blobs = {e["index"]: (blob if e["index"] == index
                          else pkg.data[e["offset"]:e["offset"] + e["size"]]) for e in order}
    head = pkg.data[:pkg.name_offset]
    names = pw.encode_names(pkg.names)
    imports = pw.encode_imports(pkg.imports)
    layout = pw.region_order(pkg)
    offsets = {e["index"]: e["offset"] for e in exports}
    starts = {}
    for _round in range(64):
        for e in exports:
            e["size"] = len(blobs[e["index"]])
            e["offset"] = offsets[e["index"]]
        table = pw.encode_exports(exports)
        pos, moved = len(head), {}
        for region in layout:
            starts[region] = pos
            if region == "payloads":
                for e in order:
                    moved[e["index"]] = pos
                    pos += len(blobs[e["index"]])
            elif region == "names":
                pos += len(names)
            elif region == "imports":
                pos += len(imports)
            else:
                pos += len(table)
        total = pos
        if moved == offsets:
            break
        offsets = moved
    else:
        raise SystemExit("%s: layout did not settle" % pkg.path)
    # The header carries the three table offsets. Re-laying the payloads moves them -- and when an export offset crosses
    # a compact-index boundary the export table itself changes length, so the shift is not a constant. Writing the
    # measured positions back is what the first attempt at this experiment missed: the file grew 27 bytes and the header
    # still pointed imports and exports at their old addresses, which the reader reports as `unaligned`.
    head = bytearray(head)
    struct.pack_into("<I", head, 16, starts["names"])
    struct.pack_into("<I", head, 24, starts["exports"])
    struct.pack_into("<I", head, 32, starts["imports"])
    out = bytearray(head)
    for region in layout:
        if region == "names":
            out += names
        elif region == "imports":
            out += imports
        elif region == "exports":
            out += pw.encode_exports(exports)
        else:
            for e in order:
                out += blobs[e["index"]]
    if len(out) != total:
        raise SystemExit("assembled %d bytes, layout says %d" % (len(out), total))
    return bytes(out)


def check(_args):
    """The index base, which is the one thing that broke this tool: `e["index"]` is 0-based.

    A tool that returns `i + 1` here rewrites the *next* export and still reports success on the size it chose, so the
    failure shows up as a corrupt payload somewhere else. Held to a synthetic list so it cannot drift again.
    """

    class FakePkg:
        exports = [
            {"index": 0, "name": 0, "offset": 100, "size": 4},
            {"index": 1, "name": 1, "offset": 104, "size": 4},
            {"index": 2, "name": 2, "offset": 108, "size": 4},
        ]

        def class_name_of(self, e):
            return "Function"

        def name(self, i):
            return ["alpha", "beta", "gamma"][i]

    fake = FakePkg()
    for i, want in enumerate((0, 1, 2)):
        got = find_function(fake, None, fake.name(i))
        if got != want:
            print("FAIL l1-change: %s resolved to index %r, expected %r" % (fake.name(i), got, want))
            return 1
    print("ok l1-change: export indices are 0-based, three of three resolve to themselves")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--package")
    ap.add_argument("--function")
    # A name is not a unique handle: `LogDropped` is carried by two exports and `QueueForceFire` by more than one, so a
    # name lookup silently rewrites whichever the table lists first -- and still reports the size it chose.
    ap.add_argument("--index", type=int)
    ap.add_argument("--append", action="store_true",
                    help="append the new payload and patch one table entry in place instead of re-laying out the file")
    ap.add_argument("--insert", action="store_true",
                    help="insert the grown payload at the end of the payload region, so only the tables move")
    ap.add_argument("--class", dest="cls")
    ap.add_argument("--body", nargs="+", help="hex bytes, concatenated")
    ap.add_argument("-o", "--out")
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    if args.check:
        return check(args)
    if not (args.package and (args.function or args.index is not None) and args.body and args.out):
        print("FAIL l1-change: --package, --function, --body and -o are required")
        return 2

    script = b"".join(bytes.fromhex(p) for p in args.body)
    pkg = Package(args.package)
    pkg.read_names()
    pkg.read_imports()
    pkg.read_exports()
    index = args.index if args.index is not None else find_function(pkg, args.cls, args.function)
    if index is None:
        print("FAIL l1-change: no Function named %r in %s" % (args.function, args.package))
        return 1
    export = next(e for e in pkg.exports if e["index"] == index)
    payload = pkg.data[export["offset"]:export["offset"] + export["size"]]
    status, old_script, start, end, _name_end, _front = sd.locate(export, pkg.data)
    if status != "ok":
        print("FAIL l1-change: %s's script is %s, not locatable" % (args.function, status))
        return 1
    new_payload = payload[:start] + script + payload[end:]

    new_file = rebuild_with(pkg, index, new_payload, append="insert" if args.insert else args.append)
    with open(args.out, "wb") as fh:
        fh.write(new_file)

    back = Package(args.out)
    back.read_names()
    back.read_imports()
    back.read_exports()
    target = next((e for e in back.exports if e["index"] == index), None)
    if target is None:
        print("FAIL l1-change: export %d vanished from the rebuilt package" % index)
        return 1
    status2, got, _s, _e, _ne, _f = sd.locate(target, back.data)
    print("%s: script %d -> %d bytes (%+d), payload %d -> %d, file %d -> %d"
          % (args.function, len(old_script), len(script), len(script) - len(old_script),
             len(payload), target["size"], len(pkg.data), len(new_file)))
    if status2 != "ok":
        print("FAIL l1-change: the rebuilt script does not locate (%s)" % status2)
        return 1
    if got != script:
        print("FAIL l1-change: read back %s, expected %s" % (got.hex(), script.hex()))
        return 1
    original = {e["index"]: e for e in pkg.exports}
    moved = [e["index"] for e in back.exports
             if e["index"] != index
             and (e["index"] not in original
                  or back.data[e["offset"]:e["offset"] + e["size"]]
                  != pkg.data[original[e["index"]]["offset"]:original[e["index"]]["offset"]
                              + original[e["index"]]["size"]])]
    if moved:
        print("FAIL l1-change: %d other payload(s) changed, first %s" % (len(moved), moved[:5]))
        return 1
    print("ok l1-change: rebuilt, re-read, script exact, every other payload untouched")
    return 0


if __name__ == "__main__":
    sys.exit(main())
