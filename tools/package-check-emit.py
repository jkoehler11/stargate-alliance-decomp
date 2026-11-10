#!/usr/bin/env python3
"""Write a `Packages.md5` check database of our own (PLAN 6.2).

The reader half is done: `tools/package-check-quick.py` rebuilds the buffer `0x00121cf0`
hashes -- the header dwords plus one 8-byte record per name entry -- and reproduces **168 of
168** shipped records. This is the writer half. The database is an ordinary UE2 package
(130/4) whose 168 exports are 78-byte `PackageCheckInfoN` payloads, so it can be *assembled*
from two values per package -- the header GUID and the quick digest -- through the container
writer's own encoders (`tools/pkg-write.py`).

    python3 tools/package-check-emit.py --dump DUMP          # reproduce the shipped database
    python3 tools/package-check-emit.py --tree BUILD/System --out BUILD/System/Packages.md5
    python3 tools/package-check-emit.py --check              # synthetic self-test, no dump

`--dump` is the proof: it records every package the shipped file names, in that file's own
order, and the result must be **byte-identical** to the 20,111-byte original. `--tree` is the
use: it records every package in a build tree in name order, then reads its own output back
through `ue2pkg` and the record reader and requires every record to match the package it
names, so a build hands the engine a database we wrote rather than a copy of the cook's.

The record payload was measured, not guessed -- all 168 shipped payloads are the same 78-byte
frame:

    00 | 21 <32 uppercase hex> 00 | 01 | 21 <32 lowercase hex> 00 | 01 | 00 x7

`21` is the compact `FString` length (32 characters plus the NUL), so the two strings are the
package's GUID and its quick digest with a single byte in front of each; the trailing
`01 00 x7` is constant across every record. Its meaning is unmeasured (the fields are `0`/`1`
in all 168), which is why the frame is written out verbatim rather than derived. What *is*
measured is that the record is a pure function of the package's header and name table, so a
rebuilt package's record is computable from the file it ships (`docs/PACKAGE_CHECK.md`).

`--check` answers without the dump and writes nothing to the tree, so `make ci` keeps the
frame honest: it assembles a database over two synthetic packages, reads it back, and then
mutates one package's name table to prove the record follows the package and the GUID does not.
"""
import argparse
import glob
import hashlib
import importlib.util
import os
import struct
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
TAG = 0x9E2A83C1
HEADER_LEN = 0x44          # tag .. GUID, generation count + record, then four bytes the reader skips
RECORD_SIZE = 78
EXPORT_FLAGS = 0x00070004
NAME_FLAG_PACKAGE = 0x04070010   # Core, Engine -- the two the imports name
NAME_FLAG_OBJECT = 0x04070410    # None, Package, Class
NAME_FLAG_RECORD = 0x00070010    # PackageCheckInfo, md5 and every record name

# The seven names in front of the records. `PackageCheckInfo` (index 5) is the export class and
# `md5` (index 6) the field's own name; both are in the shipped table and unused by the record
# frame, which is why they are carried over rather than invented.
BASE_NAMES = [("None", NAME_FLAG_OBJECT), ("Core", NAME_FLAG_PACKAGE), ("Engine", NAME_FLAG_PACKAGE),
              ("Package", NAME_FLAG_OBJECT), ("Class", NAME_FLAG_OBJECT),
              ("PackageCheckInfo", NAME_FLAG_RECORD), ("md5", NAME_FLAG_RECORD)]
FIRST_RECORD_NAME = len(BASE_NAMES)

# `Core.Class` and `Engine.PackageCheckInfo`, the two imports every record's `class` field
# resolves through: the export entries carry class = -1, i.e. the object being exported *is* a
# `PackageCheckInfo` and the class comes from the import.
IMPORTS = [{"class_package": 1, "class": 4, "package": 4294967294, "name": 5},
           {"class_package": 1, "class": 3, "package": 0, "name": 2}]


def _tool(name):
    """Import a sibling tool whose filename is not an identifier (`pkg-write.py`)."""
    path = os.path.join(HERE, name + ".py")
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"), path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


pkg_write = _tool("pkg-write")
quick = _tool("package-check-quick")


def record_bytes(guid, digest):
    """One `PackageCheckInfo` payload: the 78-byte frame, measured from all 168 shipped records."""
    if len(guid) != 32 or len(digest) != 32:
        raise ValueError("a record is two 32-character fields, got %r/%r" % (guid, digest))
    return (b"\x00\x21" + guid.encode("ascii") + b"\x00"
            + b"\x01\x21" + digest.encode("ascii") + b"\x00"
            + b"\x01" + b"\x00" * 7)


def encode_database(records, header_guid):
    """Assemble the whole check package: header, name table, payloads, imports, export table.

    The region order is the one `tools/pkg-write.py --layout` measures on every shipped package
    (`names < payloads < imports < exports`), and the header's three table offsets are computed
    from the region lengths rather than replayed from the original.
    """
    names = BASE_NAMES + [("PackageCheckInfo%d" % i, NAME_FLAG_RECORD) for i in range(len(records))]
    payloads = [record_bytes(guid, digest) for guid, digest in records]
    if any(len(p) != RECORD_SIZE for p in payloads):
        raise SystemExit("a record is not %d bytes" % RECORD_SIZE)

    names_blob = pkg_write.encode_names(names)
    imports_blob = pkg_write.encode_imports(IMPORTS)
    name_offset = HEADER_LEN
    payload_start = name_offset + len(names_blob)
    import_offset = payload_start + sum(len(p) for p in payloads)
    export_offset = import_offset + len(imports_blob)

    exports, pos = [], payload_start
    for i, payload in enumerate(payloads):
        exports.append({"index": i, "class": -1, "super": 0, "outer": 0,
                        "name": FIRST_RECORD_NAME + i, "flags": EXPORT_FLAGS,
                        "size": len(payload), "offset": pos})
        pos += len(payload)
    export_blob = pkg_write.encode_exports(exports)

    head = struct.pack("<IHHIIIIIII", TAG, 130, 4, 0x00000001, len(names), name_offset,
                       len(exports), export_offset, len(IMPORTS), import_offset)
    # The GUID text is four `%08X` dwords of the little-endian values stored at 0x24, so it has to be
    # packed back per dword -- `bytes.fromhex` would reverse every one of them (and did).
    head += b"".join(struct.pack("<I", int(header_guid[i:i + 8], 16)) for i in (0, 8, 16, 24))
    head += struct.pack("<I", 1)                     # one generation record
    head += struct.pack("<II", len(exports), len(names))
    head += b"\x00" * 4                              # the four bytes the reader's header skips
    if len(head) != HEADER_LEN:
        raise SystemExit("header assembled to %d bytes, not %#x" % (len(head), HEADER_LEN))
    return head + names_blob + b"".join(payloads) + imports_blob + export_blob


def read_stats(data):
    """Header fields and records of a database we just wrote, through the existing readers."""
    if len(data) < HEADER_LEN or struct.unpack_from("<I", data, 0)[0] != TAG:
        raise SystemExit("not a UE2 package")
    (version, licensee, flags, name_count, name_offset, export_count, export_offset,
     import_count, import_offset) = struct.unpack_from("<HHIIIIIII", data, 4)
    return {"version": version, "licensee": licensee, "flags": flags, "names": name_count,
            "name_offset": name_offset, "exports": export_count, "export_offset": export_offset,
            "imports": import_count, "import_offset": import_offset,
            "header_guid": "".join("%08X" % v for v in struct.unpack_from("<IIII", data, 0x24)),
            "records": quick.check_records(data), "size": len(data)}


def verify(path, records, problems):
    """Re-read a database we wrote and require it to say exactly what we meant.

    Reading our own output back through the *reader* is the check that matters: bytes that parse
    are not bytes that say the right thing, and the export table is where a writer's arithmetic
    goes wrong silently (a record at the wrong offset still reads as 78 plausible bytes).
    """
    with open(path, "rb") as fh:
        data = fh.read()
    try:
        stats = read_stats(data)
    except SystemExit as exc:
        problems.append("%s: %s" % (path, exc))
        return None
    if stats["version"] != 130 or stats["licensee"] != 4:
        problems.append("version/licensee read back %d/%d" % (stats["version"], stats["licensee"]))
    if stats["names"] != len(BASE_NAMES) + len(records):
        problems.append("name count %d, wanted %d" % (stats["names"], len(BASE_NAMES) + len(records)))
    if stats["exports"] != len(records):
        problems.append("export count %d, wanted %d" % (stats["exports"], len(records)))
    if stats["imports"] != len(IMPORTS):
        problems.append("import count %d, wanted %d" % (stats["imports"], len(IMPORTS)))
    if stats["records"] != list(records):
        first = next((i for i in range(min(len(stats["records"]), len(records)))
                      if stats["records"][i] != records[i]), None)
        problems.append("records do not read back in order (first disagreement at record %s)"
                        % ("%d" % first if first is not None else "count"))
    pkg = pkg_write.Package(path)
    pkg.read_names()
    pkg.read_imports()
    pkg.read_exports()
    expect_first = stats["name_offset"] + len(pkg_write.encode_names(pkg.names))
    if pkg.exports and pkg.exports[0]["offset"] != expect_first:
        problems.append("first payload sits at %#x, the name table ends at %#x"
                        % (pkg.exports[0]["offset"], expect_first))
    for e in pkg.exports:
        payload = data[e["offset"]:e["offset"] + e["size"]]
        if len(payload) != RECORD_SIZE or payload != record_bytes(*stats["records"][e["index"]]):
            problems.append("export %d does not carry its own record" % e["index"])
            break
        if pkg.class_name_of(e) != "PackageCheckInfo":
            problems.append("export %d resolves its class as %s" % (e["index"], pkg.class_name_of(e)))
            break
    if [pkg.name(r["name"]) for r in pkg.imports] != ["PackageCheckInfo", "Engine"]:
        problems.append("imports read back as %s" % [pkg.name(r["name"]) for r in pkg.imports])
    return stats


def tree_records(tree, problems):
    """(relative path, guid, digest) for every package in `tree`, in name order."""
    out = []
    for path in sorted(glob.glob(os.path.join(tree, "*"))):
        name = os.path.basename(path)
        if os.path.splitext(name)[1].lower() not in (".u", ".unr", ".uax", ".utx", ".usx", ".ukx"):
            continue
        with open(path, "rb") as fh:
            data = fh.read()
        guid = quick.header_guid(data)
        if guid is None:
            problems.append("%s carries no UE2 header" % name)
            continue
        try:
            digest, _buflen, _census = quick.quick_digest(data)
        except (ValueError, struct.error) as exc:
            problems.append("%s: %s" % (name, exc))
            continue
        out.append((name, guid, digest))
    return out


def derive_guid(records):
    """A header GUID for a database we made, derived from what it says (PLAN 6.3: no timestamps)."""
    blob = b"sg1 Packages.md5\n" + b"".join(("%s%s" % r).encode("ascii") for r in records)
    return hashlib.md5(blob).hexdigest().upper()


def emit(path, records, header_guid, problems):
    data = encode_database(records, header_guid)
    with open(path, "wb") as fh:
        fh.write(data)
    return data


def reproduce_dump(game_dir, problems):
    """Rebuild the shipped database from the packages it names and require byte identity."""
    source = os.path.join(game_dir, "System", "Packages.md5")
    if not os.path.isfile(source):
        problems.append("no System/Packages.md5 under %s" % game_dir)
        return 1
    with open(source, "rb") as fh:
        original = fh.read()
    records = quick.check_records(original)
    if not records:
        problems.append("%s holds no check records" % source)
        return 1
    files = quick.scan(game_dir)
    by_guid, refused = {}, []
    for rel, guid, data in files:
        try:
            by_guid.setdefault(guid, []).append((rel, quick.quick_digest(data)[0]))
        except (ValueError, struct.error):
            refused.append(rel)      # no record can name it if its name table will not parse
    print("shipped database: %s -- %d bytes, %d records, header GUID %s"
          % (os.path.basename(source), len(original), len(records),
             "".join("%08X" % v for v in struct.unpack_from("<IIII", original, 0x24))))
    print("  packages in the dump with a UE2 header:        %d (%d distinct GUIDs%s)"
          % (len(files), len(by_guid),
             ", %d with a name table this reader refuses" % len(refused) if refused else ""))
    unreproduced = []
    for index, (guid, digest) in enumerate(records):
        hits = [rel for rel, got in by_guid.get(guid, []) if got == digest]
        if not hits:
            unreproduced.append(index)
    print("  records whose GUID and quick digest reproduce: %d of %d"
          % (len(records) - len(unreproduced), len(records)))
    if unreproduced:
        problems.append("record(s) %s do not reproduce from the dump" % unreproduced[:8])

    header_guid = "".join("%08X" % v for v in struct.unpack_from("<IIII", original, 0x24))
    emitted = encode_database(records, header_guid)
    same = emitted == original
    at = next((i for i in range(min(len(emitted), len(original))) if emitted[i] != original[i]),
              min(len(emitted), len(original)))
    print("  emitted from the model:                        %d bytes%s"
          % (len(emitted), "" if same else ", differs at +0x%x" % at))
    if not same:
        problems.append("emitted database is not byte-identical: %d vs %d bytes, first difference at +0x%x "
                        "(original %s, emitted %s)"
                        % (len(original), len(emitted), at, original[at:at + 8].hex(" ") or "-",
                           emitted[at:at + 8].hex(" ") or "-"))
    if same:
        print("  -> the shipped database is reproduced byte for byte from its own records")
    return 0 if same else 1


def emit_tree(tree, out_path, problems, records_file=None):
    """Record a build tree: every package in `tree`, in name order, read back and verified."""
    if not os.path.isdir(tree):
        problems.append("no such directory: %s" % tree)
        return 1
    rows = tree_records(tree, problems)
    if not rows:
        problems.append("no packages under %s -- refusing to write an empty database" % tree)
        return 1
    records = [(guid, digest) for _name, guid, digest in rows]
    header_guid = None
    source = None
    if records_file:
        if not os.path.isfile(records_file):
            problems.append("no check file at %s" % records_file)
            return 1
        with open(records_file, "rb") as fh:
            blob = fh.read()
        source = quick.check_records(blob)
        if len(blob) >= HEADER_LEN:
            header_guid = "".join("%08X" % v for v in struct.unpack_from("<IIII", blob, 0x24))
    if not header_guid:
        header_guid = derive_guid(records)
    data = emit(out_path, records, header_guid, problems)
    print("tree %s: %d package(s), %d records" % (tree, len(rows), len(records)))
    print("  wrote %s -- %d bytes, header GUID %s%s"
          % (out_path, len(data), header_guid,
             " (the source's own)" if records_file else " (derived from the records)"))
    stats = verify(out_path, records, problems)
    if stats is not None:
        print("  read back: %d of %d records, %d exports, %d imports, every payload at its own offset"
              % (len(stats["records"]), len(records), stats["exports"], stats["imports"]))
    if source is not None:
        known = set(source)
        same = sum(1 for r in records if r in known)
        new = [name for name, guid, digest in rows if (guid, digest) not in known]
        print("  against the source's %d records: %d present and equal, %d new%s"
              % (len(source), same, len(new),
                 (" (" + ", ".join(new[:4]) + ")") if new else ""))
    return 0 if not problems else 1


def self_test():
    """Assemble a database over two synthetic packages, read it back, then move a name table.

    The second half is the point: a digest that does not follow the package would make the whole
    check vacuous, and the GUID staying put is what proves the two fields are told apart.
    """
    problems = []
    tmp = tempfile.mkdtemp(prefix="pkg-check-emit-check-")
    tree = os.path.join(tmp, "System")
    os.makedirs(tree)
    for i, sizes in enumerate(([63, 10], [7, 200])):
        pkg_write.synthesize(os.path.join(tree, "Synth%d.u" % i), sizes)
    rows = tree_records(tree, problems)
    if len(rows) != 2:
        print("FAIL synthetic tree: %d packages read, wanted 2" % len(rows))
        return 1
    records = [(guid, digest) for _n, guid, digest in rows]
    out = os.path.join(tmp, "Packages.md5")
    emit(out, records, derive_guid(records), problems)
    stats = verify(out, records, problems)
    if stats is not None:
        print("ok   assembled %d records into %d bytes (v%d/%d, %d names, %d exports, %d imports)"
              % (len(records), stats["size"], stats["version"], stats["licensee"], stats["names"],
                 stats["exports"], stats["imports"]))
    # Move one name table: same header, different digest -- the record must follow the package.
    path = os.path.join(tree, "Synth1.u")
    with open(path, "rb") as fh:
        data = bytearray(fh.read())
    name_offset = struct.unpack_from("<I", data, 0x10)[0]
    if data[name_offset + 1:name_offset + 3] != b"Al":
        problems.append("synthetic name table does not start with the expected name")
    else:
        data[name_offset + 1] = ord("Z")
        with open(path, "wb") as fh:
            fh.write(data)
        before = dict((n, (g, d)) for n, g, d in rows)
        moved = tree_records(os.path.join(tmp, "System"), problems)
        after = dict((n, (g, d)) for n, g, d in moved)
        for name in ("Synth0.u", "Synth1.u"):
            if before[name][0] != after[name][0]:
                problems.append("%s: the header GUID moved when a name did" % name)
            changed = before[name][1] != after[name][1]
            if (name == "Synth1.u") != changed:
                problems.append("%s: digest %s despite the name table %s"
                                % (name, "changed" if changed else "held",
                                   "change" if name == "Synth1.u" else "being untouched"))
        if not problems:
            print("ok   a renamed entry moves its digest and leaves the GUID alone (the record "
                  "follows the package, not the file)")
    for p in problems:
        print("FAIL " + p)
    return 0 if not problems else 1


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dump", metavar="DUMP", help="the dump's game directory: reproduce System/Packages.md5")
    ap.add_argument("--tree", metavar="DIR", help="a directory of packages to record (a build's System/)")
    ap.add_argument("--out", metavar="FILE", help="where --tree writes its database")
    ap.add_argument("--records", metavar="FILE",
                    help="with --tree: the source check file to compare against (and to take the "
                         "database's own header GUID from)")
    ap.add_argument("--check", action="store_true", help="synthetic self-test on built bytes, no dump")
    args = ap.parse_args()

    if args.check:
        return self_test()
    problems = []
    if args.dump:
        rc = reproduce_dump(args.dump, problems)
    elif args.tree:
        if not args.out:
            sys.exit("--tree needs --out")
        rc = emit_tree(args.tree, args.out, problems, args.records)
    else:
        ap.error("one of --dump, --tree or --check is required")
    for p in problems:
        print("FAIL " + p)
    return rc


if __name__ == "__main__":
    sys.exit(main())
