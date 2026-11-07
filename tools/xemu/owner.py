#!/usr/bin/env python3
"""Name the object that owns a script copy: which `UFunction` holds a bytecode pointer (PLAN 7.3 / U15).

A `brk=` step in `drive.py` arms a read watchpoint on a script function's **first byte**, found by matching the
function's *widened* bytecode as a pattern in a 128 MiB dump. That says where the bytecode is, not whose it is: the
same function name is declared by more than one class (`SetupInteractionsFor` is one), so "the route ran" and "the
HUD's route ran" are different claims until each armed copy has an owner.

The owner is in `UObject::GObjObjects` -- the array `objects.py` located at `0x006c21b0` -- because a `UFunction`
holds its bytecode as a pointer, and that pointer is the copy's linear address. So a whole-RAM search for the copy's
dword finds every field that names it, and the object the hit sits in is the owner. This tool does that over a dump,
which is seconds rather than the ~16 minutes a stub-side pass costs (`docs/XEMU.md`).

    # a dump taken by `drive.py --step="brk=..."`: the dump plus cr3 (`info registers`), no guest
    python3 tools/xemu/owner.py ram.bin --cr3 0xf000 --pattern "SetupInteractionsFor=0f00????????2e..."

    # an address a brk log already printed
    python3 tools/xemu/owner.py ram.bin --cr3 0xf000 --addr 0x01a7e000

**A dump is physical and a pointer is linear** (`docs/XEMU.md`): the array's own `Data` pointer translates, and every
object pointer translates, so the walk is not an identity mapping -- reading the pointers as physical lands on 0 of
200 objects, as linear on 124 (`docs/XEMU.md`, the same dump).

**What this answers, and what it cannot.** Each copy's owner comes back as an object of class `Function`, the header
field that names its *declaring class* (`Outer`), that class object's address, the number of functions it declares,
and the classes in `inventory/class_graph.csv` that declare that many. A `UClass`'s own name is an `FName`, an index
into the engine's global name table, and that table is not a pointer array in this build's dump (`docs/XEMU.md`), so
the tool names a class by its *arity* and says so rather than inventing a name.

A dump is guest data: it stays on the desktop, and this prints addresses, counts and class names only.
"""
import argparse
import csv
import importlib.util
import os
import re
import struct
import sys

TOOLS = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(TOOLS))
CLASS_NAMES = os.path.join(REPO, "inventory", "xbe_class_table_names.csv")
CLASS_TABLES = os.path.join(REPO, "inventory", "xbe_class_tables.csv")
CLASS_GRAPH = os.path.join(REPO, "inventory", "class_graph.csv")
ARRAY = 0x006C21B0          # `UObject::GObjObjects`, measured 2026-11-06 (docs/XEMU.md)
SPAN = 0x400                # how far past an object's base a field may sit and still be that object's
FIELDS = (0x08, 0x0C, 0x10, 0x14, 0x18, 0x1C)
OUTER_SAMPLE = 300


def load(name):
    spec = importlib.util.spec_from_file_location(name, os.path.join(TOOLS, name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def class_names():
    """{table VA: class name}, from the two inventory CSVs that name a table."""
    names = {}
    for path, column in ((CLASS_NAMES, "class"), (CLASS_TABLES, "class")):
        if not os.path.exists(path):
            continue
        with open(path, "r", encoding="utf-8", newline="") as handle:
            for row in csv.DictReader(handle):
                try:
                    table = int(row["table_va"], 16)
                except (KeyError, TypeError, ValueError):
                    continue
                name = (row.get(column) or "").strip()
                if name and table not in names:
                    names[table] = name
    return names


def class_graph():
    """{function count: [package.class, ...]} -- the arity a class can be recognised by."""
    out = {}
    if not os.path.exists(CLASS_GRAPH):
        return out
    with open(CLASS_GRAPH, "r", encoding="utf-8", newline="") as handle:
        for row in csv.DictReader(handle):
            try:
                count = int(row["functions"])
            except (KeyError, TypeError, ValueError):
                continue
            out.setdefault(count, []).append("%s.%s" % (row.get("package", "?"), row.get("class", "?")))
    return out


def compile_pattern(hexes):
    """`drive.py`'s pattern dialect: exact bytes, `??` for one byte the loader rewrites."""
    hexes = hexes.replace(" ", "").replace("0x", "")
    parts = []
    index = 0
    while index < len(hexes):
        if hexes[index:index + 2] == "??":
            parts.append(b".")
            index += 2
        else:
            parts.append(re.escape(bytes([int(hexes[index:index + 2], 16)])))
            index += 2
    return re.compile(b"".join(parts), re.DOTALL)


class Objects(object):
    """The walked object array: base address, class, and the header words a copy's owner needs."""

    def __init__(self, blob, vamap, cr3, names):
        self.blob = blob
        self.vamap = vamap
        self.cr3 = cr3
        self.names = names
        self.rows = []              # (base VA, physical, class name)
        self.by_physical = []       # sorted [(physical, base VA, class name)]
        self.array = {}
        self.rejected = 0

    def _physical(self, va):
        for candidate, _how, _flags in self.vamap.physical_for(self.blob, self.cr3, va):
            return candidate
        return None

    def load(self):
        blob = self.blob
        at = self._physical(ARRAY)
        if at is None:
            raise SystemExit("GObjObjects at %#x is not mapped in this dump" % ARRAY)
        data, num, cap = struct.unpack_from("<III", blob, at)
        self.array = {"physical": at, "data": data, "num": num, "max": cap}
        array_pa = self._physical(data)
        if array_pa is None or not 0 < num <= 400000:
            raise SystemExit("the object array's Data pointer %#x (%d slots) does not translate" % (data, num))
        for slot in range(num):
            va, = struct.unpack_from("<I", blob, array_pa + slot * 4)
            if not va:
                continue
            pa = self._physical(va)
            if pa is None or pa + 24 > len(blob):
                self.rejected += 1
                continue
            table, = struct.unpack_from("<I", blob, pa)
            if not table:
                self.rejected += 1
                continue
            # An unnamed table is still an object: the walk keys on a pointer that translated, not on the
            # inventory knowing the class (492 of the tables are named; the array holds many more).
            name = self.names.get(table, "unknown")
            self.rows.append((va, pa, name))
            self.by_physical.append((pa, va, name))
        self.by_physical.sort()
        return self

    def word(self, pa, offset=0):
        return struct.unpack_from("<I", self.blob, pa + offset)[0]

    def target_class(self, pa, offset, as_linear=True):
        """The class name of the object a field names, or None when it names nothing walked."""
        value = self.word(pa, offset)
        candidates = [c for c, _h, _f in self.vamap.physical_for(self.blob, self.cr3, value)] if as_linear else [value]
        for candidate in candidates:
            if 0 <= candidate and candidate + 4 <= len(self.blob):
                name = self.names.get(self.word(candidate))
                if name:
                    return name
        return None

    def owner(self, pa, span=SPAN):
        """The object a field at physical `pa` sits in: the greatest base at or below it, within `span`.

        `near` is reported either way: a hit that sits 6 MB past the nearest object is in package-side data (the
        loader's own tables live there), not in an object, and saying so is the measurement.
        """
        best = None
        for base_pa, va, name in self.by_physical:
            if base_pa > pa:
                break
            best = (base_pa, va, name)
        if best is None:
            return {"near": None, "distance": None}
        near = {"near": best[1], "distance": pa - best[0], "class": best[2]}
        if pa - best[0] > span:
            return near
        near.update({"base": best[1], "physical": best[0], "offset": pa - best[0]})
        return near

    def outer(self, sample=OUTER_SAMPLE):
        """(offset, as_linear, agreement, sample): the header field that names a `Class` for the most Functions."""
        functions = [row for row in self.rows if row[2] == "Function"][:sample]
        scores = []
        for offset in FIELDS:
            for as_linear in (True, False):
                good = sum(1 for _va, pa, _name in functions
                           if self.target_class(pa, offset, as_linear) in ("Class", "State"))
                scores.append((good, offset, as_linear))
        scores.sort(reverse=True)
        best = scores[0] if scores else (0, None, True)
        return best[1], best[2], best[0], len(functions)

    def functions_declared_by(self, pa, offset):
        """How many `Function` objects carry the same raw `Outer` value as the one at `pa+offset`."""
        value = self.word(pa, offset)
        return sum(1 for _va, other, name in self.rows if name == "Function" and self.word(other, offset) == value)


def main():
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0],
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("dump", nargs="?", help="RAM image written by `monitor=pmemsave` (address == file offset)")
    parser.add_argument("--cr3", type=lambda v: int(v, 0), default=0xF000, metavar="ADDR",
                        help="page directory from `info registers` (measured 0xf000)")
    parser.add_argument("--pattern", action="append", default=[], metavar="LABEL=HEX",
                        help="a widened bytecode pattern, `??` for a byte the loader rewrites; repeatable")
    parser.add_argument("--addr", action="append", default=[], metavar="ADDR",
                        help="a copy's linear address, as a brk log printed it; repeatable")
    parser.add_argument("--limit", type=int, default=4, help="occurrences to report per copy (default 4)")
    parser.add_argument("--selftest", action="store_true", help="prove the walk on a synthetic image, no guest")
    args = parser.parse_args()

    if args.selftest:
        return selftest()
    if not args.dump:
        parser.error("a dump is required unless --selftest is given")

    with open(args.dump, "rb") as handle:
        blob = handle.read()
    vamap = load("vamap")
    objects = Objects(blob, vamap, args.cr3, class_names()).load()
    print("dump %s, %d bytes, cr3=%#x" % (os.path.basename(args.dump), len(blob), args.cr3))
    print("GObjObjects %#x: %d slots, %d objects walked, %d rejected"
          % (ARRAY, objects.array["num"], len(objects.rows), objects.rejected))

    copies = []
    for item in args.pattern:
        label, _, hexes = item.partition("=")
        if not label or not hexes:
            raise SystemExit("--pattern=%r is not LABEL=HEX" % item)
        pattern = compile_pattern(hexes)
        linear = []
        for match in list(pattern.finditer(blob))[:8]:
            for va, _how, _flags in vamap.linear_for(blob, args.cr3, match.start()):
                if va not in linear:
                    linear.append(va)
        for va in sorted(linear):
            copies.append((label.strip(), va))
    for item in args.addr:
        copies.append(("addr", int(item, 0)))
    if not copies:
        raise SystemExit("nothing to own: pass --pattern or --addr")

    offset, as_linear, agree, sample = objects.outer()
    if offset is None:
        raise SystemExit("no Function object in this dump: the array carries no outer field to test")
    print("Outer is header +%#x (%s): it names a Class for %d of the first %d Function objects"
          % (offset, "linear" if as_linear else "physical", agree, sample))

    graph = class_graph()
    seen = {}
    print("")
    print("%-22s %-10s %-20s %-10s %-6s %s"
          % ("function", "copy", "owner", "class", "arity", "classes that declare that many"))
    for label, va in copies:
        packed = struct.pack("<I", va)
        hits = []
        at = blob.find(packed)
        while at >= 0 and len(hits) < args.limit:
            if at % 4 == 0:
                hits.append(at)
            at = blob.find(packed, at + 1)
        if not hits:
            print("%-22s %#010x  no dword in the dump names this copy" % (label, va))
            continue
        for pa in hits:
            owner = objects.owner(pa)
            if owner.get("offset") is None:
                near = ("%#010x +%#x (%s)" % (owner["near"], owner["distance"], owner["class"])
                        if owner.get("near") is not None else "no object below it")
                # The words around the hit are the record the loader wrote: numbers, not payload.
                before = struct.unpack_from("<II", blob, pa - 8)
                after = struct.unpack_from("<III", blob, pa + 4)
                print("%-22s %#010x  at %#010x, nearest object %s -- record %s | %#x | %s"
                      % (label, va, pa, near, before, va, after))
                continue
            declared = objects.functions_declared_by(owner["physical"], offset)
            class_va = objects.word(owner["physical"], offset)
            candidates = graph.get(declared, [])
            shown = ", ".join(candidates[:3]) if candidates else "none"
            if len(candidates) > 3:
                shown += " (+%d)" % (len(candidates) - 3)
            print("%-22s %#010x  %#010x %-8s %#010x %-6d %s"
                  % (label, va, owner["base"], "%s+%#x" % (owner["class"], owner["offset"]),
                     class_va, declared, shown))
            seen.setdefault(label, set()).add(class_va)
    if len(seen) > 1:
        print("\nfunction names owned by more than one declaring class: %d of %d"
              % (sum(1 for classes in seen.values() if len(classes) > 1), len(seen)))
        for label, classes in seen.items():
            if len(classes) > 1:
                print("  %-22s %d classes" % (label, len(classes)))
    return 0


def selftest():
    """A synthetic image: one Function whose +0x0c names its Class, one dword naming the function's bytecode."""
    vamap = load("vamap")
    blob = bytearray(0x800000)
    cr3 = 0x1000
    struct.pack_into("<I", blob, cr3 + 0, 0x00000083)     # 4 MiB page, identity, present
    struct.pack_into("<I", blob, cr3 + 4, 0x00400083)
    class_table, class_object, function, copy = 0x2000, 0x3000, 0x3100, 0x700000
    names = {class_table: "Class", 0x2222: "Function"}
    struct.pack_into("<III", blob, ARRAY, 0x400000, 2, 2)                  # Data, Num, Max
    struct.pack_into("<II", blob, 0x400000, class_object, function)
    struct.pack_into("<IIIIII", blob, class_object, class_table, 1, 0, 0, 0, 0xFFFFFFFF)
    struct.pack_into("<IIIIII", blob, function, 0x2222, 2, copy, class_object, 0, 0xFFFFFFFF)
    objects = Objects(bytes(blob), vamap, cr3, names).load()
    assert len(objects.rows) == 2, "walked %d objects" % len(objects.rows)
    owner = objects.owner(function + 0x8)
    assert owner and owner["class"] == "Function" and owner["offset"] == 0x8, owner
    assert objects.target_class(function, 0x0C, True) == "Class", objects.target_class(function, 0x0C, True)
    offset, as_linear, agree, sample = objects.outer()
    assert (offset, as_linear, agree, sample) == (0x0C, True, 1, 1), (offset, as_linear, agree, sample)
    assert objects.functions_declared_by(function, 0x0C) == 1
    print("selftest ok: 2 objects, owner Function+0x8, Outer +0x0c names a Class, arity 1")
    return 0


if __name__ == "__main__":
    sys.exit(main())
