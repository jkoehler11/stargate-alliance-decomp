#!/usr/bin/env python3
"""A UnrealScript bytecode disassembler built on the image's own operand table (PLAN 3.7).

`inventory/opcode_table.csv` (from `tools/opcode-table.py`) says, per opcode, which operand stream the engine's
script walker consumes: fixed bytes, nested expressions, a parameter list ending in `0x16`, a NUL-terminated string,
a label table, and so on. This tool applies that table to a byte string and prints the token tree. It is the
"disassembler" half of 3.7; the comparator half needs a compiler (`PLAN 3.6`).

    python3 tools/uscript-dis.py --hex 040b47
    python3 tools/uscript-dis.py --file script.bin
    python3 tools/uscript-dis.py --selftest            # dump-free; what `make ci` runs
    python3 tools/uscript-dis.py --payloads DUMP/.../System/Engine.u

`--payloads` is the measurement behind `docs/UNKNOWNS.md` U13: it reports where a `Function` export's payload
stops looking like a header (the `FunctionFlags` dword at `payload_end-4`), where the script's own epilogue
(`04 0b 47` = Return, Nothing, EndOfScript) sits, and whether a size field can be found in front of it. Counts only
-- no payload bytes are printed, per `AGENTS.md`.
"""
import argparse
import collections
import csv
import os
import re
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
TABLE = os.path.join(ROOT, "inventory", "opcode_table.csv")


class Refused(Exception):
    pass


class Table:
    def __init__(self, path=TABLE):
        self.rows = {}
        for r in csv.DictReader(open(path)):
            self.rows[int(r["opcode"], 16)] = r

    def pattern(self, op):
        r = self.rows.get(op)
        if r is None:
            raise Refused("opcode %#04x is not in the table" % op)
        return r["pattern"], r["name"]

    def fixed(self, op):
        return int(self.rows[op]["fixed_bytes"])


def parse(data, table):
    """Split data into a token tree. Raises Refused with the offset when the stream does not fit the table."""
    def token(pos, depth):
        if depth > 64:
            raise Refused("%#x: nesting deeper than 64" % pos)
        if pos >= len(data):
            raise Refused("%#x: ran off the end" % pos)
        op = data[pos]
        pattern, name = table.pattern(op)
        if pattern == "bad-token":
            raise Refused("%#x: opcode %#02x is the walker's bad-token case" % (pos, op))
        node = {"offset": pos, "opcode": op, "name": name, "operands": b"", "children": [], "end": pos}
        q = pos + 1
        items = pattern.split(",") if pattern else []
        for item in items:
            if item == "expr":
                child = token(q, depth + 1)
                node["children"].append(child)
                q = child["end"]
            elif item == "index":
                # A UE1/UE2 index: bit 7 is the sign, bit 6 means more bytes follow, six bits then
                # seven per byte. Five groups' handlers read an int and the table recorded that as four
                # fixed bytes; the corpus says the operand is this encoding (docs/BYTECODE.md).
                start = q
                if q >= len(data):
                    raise Refused("%#x: index of opcode %02x runs off the end" % (pos, op))
                b = data[q]
                q += 1
                cont = b & 0x40
                while cont and q < len(data):
                    b = data[q]
                    q += 1
                    cont = b & 0x80
                node["operands"] += data[start:q]
            elif item.startswith("bytes:"):
                n = int(item.split(":", 1)[1])
                if q + n > len(data):
                    raise Refused("%#x: opcode %02x wants %d operand bytes, %d left" % (pos, op, n, len(data) - q))
                node["operands"] += data[q:q + n]
                q += n
            elif item == "parms":
                while True:
                    if q >= len(data):
                        raise Refused("%#x: parameter list of %02x never reaches 0x16" % (pos, op))
                    child = token(q, depth + 1)
                    node["children"].append(child)
                    q = child["end"]
                    if child["opcode"] == 0x16:
                        break
            elif item == "str":
                end = data.find(b"\0", q)
                if end < 0:
                    raise Refused("%#x: string operand of %02x is not NUL-terminated" % (pos, op))
                node["operands"] += data[q:end + 1]
                q = end + 1
            elif item == "utf16":
                start = q
                while True:
                    if q + 2 > len(data):
                        raise Refused("%#x: UTF-16 string operand of %02x is not NUL-terminated" % (pos, op))
                    unit = struct.unpack_from("<H", data, q)[0]
                    q += 2
                    if unit == 0:
                        break
                node["operands"] += data[start:q]
            elif item == "labeltable":
                while True:
                    if q + 8 > len(data):
                        raise Refused("%#x: label table of %02x is not zero-terminated" % (pos, op))
                    entry = struct.unpack_from("<I", data, q)[0]
                    node["operands"] += data[q:q + 8]
                    q += 8
                    if entry == 0:
                        break
            elif item == "case":
                if q + 2 > len(data):
                    raise Refused("%#x: case operand of %02x runs off the end" % (pos, op))
                value = struct.unpack_from("<H", data, q)[0]
                node["operands"] += data[q:q + 2]
                q += 2
                if value != 0xFFFF:
                    child = token(q, depth + 1)
                    node["children"].append(child)
                    q = child["end"]
            else:
                raise Refused("%#x: unknown pattern item %r in the table" % (pos, item))
        node["end"] = q
        return node

    out = []
    pos = 0
    while pos < len(data):
        node = token(pos, 0)
        out.append(node)
        pos = node["end"]
    return out


def render(nodes, out=None, depth=0):
    out = [] if out is None else out
    for n in nodes:
        operands = n["operands"].hex() if n["operands"] else ""
        name = n["name"] or "op%02x" % n["opcode"]
        out.append("%s%04x  %-24s %-20s %s" % ("  " * depth, n["offset"], name, operands,
                                               "%d byte(s)" % (n["end"] - n["offset"])))
        if n["children"] and depth < 4:
            render(n["children"], out, depth + 1)
    return out


def selftest():
    """Dump-free: the table must load and split the streams 3.7 cares about."""
    table = Table()
    if len(table.rows) != 256:
        print("FAIL uscript-dis: %d rows in the table, expected 256" % len(table.rows))
        return 1
    checks = [
        # (name, bytes, expected token count, expected total length)
        ("return nothing, then end of script -- the epilogue every payload carries", bytes([0x04, 0x0B, 0x47]), 2, 3),
        ("int constant, 5 bytes", bytes([0x1D, 0x01, 0x00, 0x00, 0x00]), 1, 5),
        ("float constant, 5 bytes", bytes([0x1E, 0x00, 0x00, 0x80, 0x3F]), 1, 5),
        ("vector constant, 13 bytes", bytes([0x23] + [0] * 12), 1, 13),
        ("string constant, NUL-terminated", bytes([0x1F]) + b"ab\0" + bytes([0x47]), 2, 5),
        ("unicode string constant, one unit", bytes([0x34]) + bytes([0x61, 0x00, 0x00, 0x00]) + bytes([0x47]), 2, 6),
        ("final function with an empty parameter list", bytes([0x1C, 0x05, 0x16]), 1, 3),
        ("virtual function, two-byte index, one argument", bytes([0x1B, 0x41, 0x02, 0x27, 0x16]), 1, 5),
        ("return of an int one", bytes([0x04, 0x26]), 1, 2),
        ("jump over a byte", bytes([0x06, 0x02, 0x00, 0x47]), 2, 4),
        ("iterator pop, no operands", bytes([0x30, 0x31]), 2, 2),
        ("extended native (0x60): byte then parameter list", bytes([0x60, 0x01, 0x16]), 1, 3),
    ]
    for label, data, want_tokens, want_len in checks:
        try:
            nodes = parse(data, table)
        except Refused as exc:
            print("FAIL uscript-dis: %s: %s" % (label, exc))
            return 1
        total = sum(n["end"] - n["offset"] for n in nodes)
        if len(nodes) != want_tokens or total != want_len:
            print("FAIL uscript-dis: %s: %d tokens / %d bytes, expected %d / %d"
                  % (label, len(nodes), total, want_tokens, want_len))
            return 1
    refusals = [
        ("the bad-token opcode", bytes([0x03])),
        ("an unterminated string", bytes([0x1F]) + b"ab"),
        ("a parameter list with no 0x16", bytes([0x1C, 0x05, 0x47])),
        ("a truncated int constant", bytes([0x1D, 0x01])),
    ]
    for label, data in refusals:
        try:
            parse(data, table)
        except Refused:
            continue
        print("FAIL uscript-dis: %s was accepted, expected a refusal" % label)
        return 1
    print("ok   uscript-dis: %d rows, %d streams split, %d refusals honoured"
          % (len(table.rows), len(checks), len(refusals)))
    return 0


def read_index(data, off):
    """The archive's compact index: bit 7 is the sign, bit 6 means "more bytes", six bits then seven per byte."""
    b = data[off]
    sign, cont, val = b & 0x80, b & 0x40, b & 0x3F
    off += 1
    shift = 6
    while cont and shift < 32:
        b = data[off]
        off += 1
        cont = b & 0x80
        val |= (b & 0x7F) << shift
        shift += 7
    return (-val if sign else val), off


def payloads(path, natives=os.path.join(ROOT, "inventory", "natives.csv"), quiet=False):
    """Measure a package's `Function` payloads: the trailer, and how far locating the script gets.

    What the trailer *is* (measured, `docs/BYTECODE.md`), and what is still open:

      * `FunctionFlags` is the dword at `payload_end-4`;
      * in front of it sit **`NativeToken` (u16) and `OperPrecedence` (u8)** -- the two fields `UFunction` adds
        after `UStruct`'s script. The token agrees with the index the script declares (`native(1234)`) for the
        great majority of functions, and an empty script has no `04 0b 47` epilogue at all, which is why the
        native half of `natives.csv` is also the half with no epilogue;
      * a Net function (flag 0x40) serialises `RepOffset` (u16) between `OperPrecedence` and the flags, which is
        why its epilogue sits two bytes earlier: a 9-byte trailer instead of 7;
      * **still open**: where the 4-byte `ScriptStorageSize` sits. A dword immediately before a script the table
        walks exactly is found in a minority of payloads, so the object references in front of it are not pinned
        -- `docs/UNKNOWNS.md` U13.
    """
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    from ue2pkg import Package                                # noqa: PLC0415 - only for a real package

    if os.path.isdir(path):
        # A whole dump directory: one line per package, then the corpus totals, since a per-package number
        # invites a total retyped from one file.
        tot, per_pkg = {}, []
        for fname in sorted(f for f in os.listdir(path) if f.lower().endswith(".u")):
            stats = payloads(os.path.join(path, fname), natives, quiet=True)
            per_pkg.append(stats)
            for k, v in stats.items():
                if isinstance(v, int):
                    tot[k] = tot.get(k, 0) + v
        print("%-18s %6s %6s %6s %6s %5s %5s %5s %6s %6s %6s %6s" %
              ("package", "fn", "join", "flags", "token", "tr7", "tr9", "none", "hdr", "size16", "walk16", "sized"))
        for stats in per_pkg:
            print("%-18s %6d %6d %6d %6d %5d %5d %5d %6d %6d %6d %6d" %
                  (stats["package"], stats["seen"], stats["joined"], stats["matched"], stats["token_ok"],
                   stats["tail7"], stats["tail9"], stats["neither"], stats["header"], stats["header_sized"],
                   stats["header_walk"], stats["located"]))
        print("%-18s %6d %6d %6d %6d %5d %5d %5d %6d %6d %6d %6d" %
              ("TOTAL", tot.get("seen", 0), tot.get("joined", 0), tot.get("matched", 0), tot.get("token_ok", 0),
               tot.get("tail7", 0), tot.get("tail9", 0), tot.get("neither", 0), tot.get("header", 0),
               tot.get("header_sized", 0), tot.get("header_walk", 0), tot.get("located", 0)))
        print("token disagreements beyond a declared none: %d; they are listed only when one package is named"
              % tot.get("token_other", 0))
        return 0

    pkg = Package(path)
    pkg.read_names()
    pkg.read_imports()
    pkg.read_exports()
    exports = {e["index"]: e for e in pkg.exports}
    pkg_name = os.path.basename(path).rsplit(".", 1)[0]
    table = Table()

    def flags(text):
        total = 0
        for part in text.split(";"):
            if ":" in part:
                total |= int(part.split(":", 1)[0], 16)
        return total

    known = {}
    if os.path.isfile(natives):
        for r in csv.DictReader(open(natives)):
            if r["package"].lower() == pkg_name.lower():
                known[(r["class"].lower(), r["function"].lower())] = flags(r["function_flags"])

    declared_token = {}
    if os.path.isfile(natives):
        for r in csv.DictReader(open(natives)):
            if r["package"].lower() == pkg_name.lower():
                declared_token[(r["class"].lower(), r["function"].lower())] = int(r["native_token"])

    seen = joined = matched = 0
    tail = {"7": 0, "9": 0, "neither": 0}
    net = {"7": 0, "9": 0}
    located = 0
    header = header_sized = header_walk = 0
    token_ok = token_unset = token_declared_only = token_other = 0
    exceptions = []
    for e in pkg.exports:
        if pkg.class_name_of(e) != "Function":
            continue
        pay = pkg.data[e["offset"]:e["offset"] + e["size"]]
        n = len(pay)
        if n < 16:
            continue
        seen += 1
        owner = exports.get(e["outer"] - 1) if e["outer"] > 0 else None
        key = None
        if owner is not None:
            key = (pkg.name(owner["name"]).lower(), pkg.name(e["name"]).lower())
            if key in known:
                joined += 1
                if struct.unpack_from("<I", pay, n - 4)[0] == known[key]:
                    matched += 1
                else:
                    exceptions.append("%s.%s: flags %#x in the payload, %#x in the script"
                                      % (key[0], key[1], struct.unpack_from("<I", pay, n - 4)[0], known[key]))
        # `NativeToken` (u16) then `OperPrecedence` (u8) sit in front of the flags; a Net function keeps
        # `RepOffset` (u16) between them, so the token moves two bytes further back. The count runs over *every*
        # payload, because the native functions -- the only ones `natives.csv` describes -- have no script and so
        # no epilogue to find.
        flags_here = struct.unpack_from("<I", pay, n - 4)[0]
        token_at = n - 9 if (flags_here >> 6) % 2 == 1 else n - 7
        if token_at >= 0 and key is not None and key in declared_token:
            token = struct.unpack_from("<H", pay, token_at)[0]
            want = declared_token[key]
            if token == want:
                token_ok += 1
            elif want == 0:
                token_unset += 1
            elif token == 0:
                token_declared_only += 1
            else:
                token_other += 1
                exceptions.append("%s.%s: native token %d in the payload, %d in the script"
                                  % (key[0], key[1], token, want))
        epilogue = bytes([0x04, 0x0B, 0x47])
        if n >= 10 and pay[n - 10:n - 7] == epilogue:
            tail["7"] += 1
            if struct.unpack_from("<I", pay, n - 4)[0] & 0x40:
                net["7"] += 1
            end, trailer = n - 7, 7
        elif n >= 12 and pay[n - 12:n - 9] == epilogue:
            tail["9"] += 1
            if struct.unpack_from("<I", pay, n - 6)[0] & 0x40:
                net["9"] += 1
            end, trailer = n - 9, 9
        else:
            tail["neither"] += 1
            continue
        # The header in front of the script is six archive-encoded values whose *last* is `FriendlyName`, so
        # the export's own name index is what says the decode is aligned. This is the measurement that
        # replaced the `Children`-chain model: the count is constant, it is the widths that vary.
        try:
            off, values = 0, []
            for _ in range(6):
                value, off = read_index(pay, off)
                values.append(value)
        except IndexError:
            continue
        if values[5] != e["name"]:
            continue
        header += 1
        start = off + 16
        if start + 4 > end or start <= off:
            continue
        if struct.unpack_from("<I", pay, off + 12)[0] == end - start:
            header_sized += 1
        try:
            nodes = parse(pay[start:end], table)
        except Refused:
            continue
        if sum(x["end"] - x["offset"] for x in nodes) == end - start:
            header_walk += 1

        for k in range(0, min(48, end - 6)):
            size = struct.unpack_from("<I", pay, k)[0]
            if size != end - (k + 4):
                continue
            try:
                nodes = parse(pay[k + 4:end], table)
            except Refused:
                continue
            if sum(x["end"] - x["offset"] for x in nodes) == size:
                located += 1
                break

    stats = {"package": pkg_name, "seen": seen, "joined": joined, "matched": matched, "token_ok": token_ok,
             "token_unset": token_unset, "token_declared_only": token_declared_only, "token_other": token_other,
             "tail7": tail["7"], "tail9": tail["9"], "neither": tail["neither"], "located": located,
             "header": header, "header_sized": header_sized, "header_walk": header_walk}
    if quiet:
        return stats
    print("%s: %d Function exports with a payload of 16 bytes or more" % (pkg_name, seen))
    print("  FunctionFlags at payload_end-4 equals inventory/natives.csv: %d of %d joinable"
          % (matched, joined))
    print("  trailer 7 bytes (epilogue at end-10..end-8): %d, of which Net flag set: %d"
          % (tail["7"], net["7"]))
    print("  trailer 9 bytes (epilogue at end-12..end-10, RepOffset after the flags): %d, "
          "of which Net flag set: %d" % (tail["9"], net["9"]))
    print("  neither: %d" % tail["neither"])
    print("  size dword immediately before a script the table walks exactly: %d of %d"
          % (located, seen))
    print("  header decode ends on FriendlyName: %d of %d; of those the size dword 16 bytes after it equals the "
          "script's length in %d, and the walk from there lands on the epilogue in %d"
          % (header, seen, header_sized, header_walk))
    print("  NativeToken at payload_end-7 (payload_end-9 when Net) equals the script's declared index: "
          "%d of %d joinable; %d carry a token where the script declares none, %d the other way"
          % (token_ok, joined, token_unset, token_declared_only))
    for line in exceptions:
        print("    %s" % line)
    return 0


def header_report(path, quiet=False):
    """What the header's encoded values are, for a package or a System directory, classes included.

    The decode is aligned by requiring a value to be the export's own name index -- `FriendlyName` -- and then
    every value in front of it is classified by what it points at: zero, an import, a function with this one's
    name in another outer (`Super` for a function), another object in the same outer (`Next`), or a child of
    this export (`Children`).

    Classes are the control: a class runs the same `UStruct::Serialize` and has no `UFunction` tail, so a count
    that is constant for both kinds, and unchanged by a class's child count, is a fixed header rather than one
    reference per child.
    """
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    from ue2pkg import Package                                # noqa: PLC0415 - only for a real package

    files = ([os.path.join(path, f) for f in sorted(os.listdir(path)) if f.lower().endswith(".u")]
             if os.path.isdir(path) else [path])
    counts = {"Class": collections.Counter(), "Function": collections.Counter()}
    kind = {"Class": [collections.Counter() for _ in range(6)],
            "Function": [collections.Counter() for _ in range(6)]}
    sized = {"Class": collections.Counter(), "Function": collections.Counter()}
    epilogue = bytes([0x04, 0x0B, 0x47])
    for one in files:
        pkg = Package(one)
        pkg.read_names()
        pkg.read_imports()
        pkg.read_exports()
        children = collections.defaultdict(set)
        for k in pkg.exports:
            if k["outer"] > 0:
                children[k["outer"]].add(k["index"] + 1)
        for e in pkg.exports:
            what = pkg.class_name_of(e)
            if what not in counts:
                continue
            pay = pkg.data[e["offset"]:e["offset"] + e["size"]]
            name = pkg.name(e["name"])
            kids = children[e["index"] + 1]
            off, values = 0, []
            while len(values) < 400:
                try:
                    value, off = read_index(pay, off)
                except IndexError:
                    values = None
                    break
                if value == e["name"]:
                    break
                values.append(value)
            if values is None or len(values) >= 400 or not values:
                continue
            counts[what][len(values)] += 1
            for i, v in enumerate(values[:6]):
                c = kind[what][i]
                if v == 0:
                    c["zero"] += 1
                elif v > 0 and v <= len(pkg.exports):
                    target = pkg.exports[v - 1]
                    if v in kids:
                        c["child"] += 1
                    elif pkg.name(target["name"]) == name:
                        c["same-name"] += 1
                    elif pkg.class_name_of(target) == "Function":
                        c["other function"] += 1
                    else:
                        c["other export"] += 1
                elif v < 0 and -v <= len(pkg.imports):
                    c["import"] += 1
                else:
                    c["out of range"] += 1
            # The fixed run after the name: is there a dword that is exactly the distance to the script's
            # end? A size field in front of the script would be one, whatever the script's length.
            end = None
            n = len(pay)
            if n >= 10 and pay[n - 10:n - 7] == epilogue:
                end = n - 7
            elif n >= 12 and pay[n - 12:n - 9] == epilogue:
                end = n - 9
            if end is None:
                continue
            for k in (4, 8, 12, 16, 20):
                if off + k + 4 > end:
                    break
                if struct.unpack_from("<I", pay, off + k)[0] == end - (off + k + 4):
                    sized[what][k + 4] += 1

    for what in ("Function", "Class"):
        print("%s exports whose header ends on FriendlyName: %d" % (what, sum(counts[what].values())))
        print("  values in front of it: %s" % dict(sorted(counts[what].most_common(6))))
        print("  %-4s %8s %7s %10s %13s %7s %6s" %
              ("", "zero", "import", "same-name", "other function", "child", "other"))
        for i in range(6):
            c = kind[what][i]
            if not sum(c.values()):
                continue
            print("  v%-3d %8d %7d %10d %13d %7d %6d" %
                  (i, c["zero"], c["import"], c["same-name"], c["other function"], c["child"],
                   c["other export"] + c["out of range"]))
        print("  dword = the distance to the script's end at name_end+%s: %s" %
              ("k", dict(sorted(sized[what].items()))))
    return 0


def start_report(path):
    """Where the script starts: three independent readings of the same offset, for a package or a directory.

    1. the **smallest** payload after `FriendlyName` -- an empty body still costs the epilogue and the trailer, so the
       floor of that distribution is the fixed run's length plus 3 plus 7 (or 9 for a Net function);
    2. how **deep** the walk from `name_end + 16` gets before it refuses -- a wrong start desynchronises within a
       token or two, a right one decodes tens of bytes and then stops on an opcode the table does not name;
    3. the dword 16 bytes past the name against the physical script length, which is what a size field would carry.
    """
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    from ue2pkg import Package                                # noqa: PLC0415 - only for a real package

    files = ([os.path.join(path, f) for f in sorted(os.listdir(path)) if f.lower().endswith(".u")]
             if os.path.isdir(path) else [path])
    table = Table()
    epilogue = bytes([0x04, 0x0B, 0x47])
    tail = collections.Counter()
    depth = collections.Counter()
    word = collections.Counter()
    smallest = None
    for one in files:
        pkg = Package(one)
        pkg.read_names()
        pkg.read_imports()
        pkg.read_exports()
        for e in pkg.exports:
            if pkg.class_name_of(e) != "Function":
                continue
            pay = pkg.data[e["offset"]:e["offset"] + e["size"]]
            n = len(pay)
            off, last = 0, None
            aligned = True
            for _ in range(6):
                try:
                    last, off = read_index(pay, off)
                except IndexError:
                    aligned = False
                    break
            if not aligned or last != e["name"]:
                continue
            rest = n - off
            tail[rest] += 1
            smallest = rest if smallest is None else min(smallest, rest)
            if n < 10 or pay[n - 10:n - 7] != epilogue:
                continue
            end = n - 7
            start = off + 16
            if start >= end:
                continue
            try:
                nodes = parse(pay[start:end], table)
            except Refused as exc:
                text = str(exc)
                m = re.match(r"0x([0-9a-f]+)", text)
                got = int(m.group(1), 16) if m else 0
                depth["under 4" if got < 4 else "4-15" if got < 16 else "16-63" if got < 64
                      else "64-255" if got < 256 else "256 or more"] += 1
            else:
                if sum(x["end"] - x["offset"] for x in nodes) == end - start:
                    depth["walked to the epilogue"] += 1
                else:
                    depth["stopped short"] += 1
            sized = struct.unpack_from("<I", pay, off + 12)[0]
            word["equal" if sized == end - start else ("larger" if sized > end - start else "smaller")] += 1
    if smallest is None:
        print("no Function export decoded")
        return 1
    print("smallest payload after FriendlyName: %d bytes" % smallest)
    print("  the ten smallest (bytes -> payloads): %s" % dict(sorted(tail.items())[:10]))
    print("walk from name_end + 16 got this far before refusing: %s" % dict(depth.most_common()))
    print("dword at name_end + 12 against the physical script length: %s" % dict(word.most_common()))
    return 0



dis_parse = parse


def operand_spans(data, table):
    """`(start, end, kind)` for every token's own bytes, following the table's grammar.

    kind is `opcode`, `index` or `fixed`. This is a position-aware copy of `parse`'s walk (that one builds a tree and
    drops operand offsets), and it exists for one reason: a compact index operand is the one thing the package loader
    rewrites on the way into memory, so a RAM search for a function's code has to leave those bytes open.
    Measured on a 128 MiB dump of a loaded `M00`: `StargateHUD.DrawOrderTarget`'s log call reads `b8 2a` in the
    package and `a0 1e 26 01` in the guest, an index widened to four bytes, while its `07 cd 00` jump is unchanged.
    """
    spans = []

    def token(pos):
        if pos >= len(data):
            raise Refused("%#x: ran off the end" % pos)
        op = data[pos]
        pattern, _name = table.pattern(op)
        if pattern == "bad-token":
            raise Refused("%#x: opcode %02x is the walker's bad-token case" % (pos, op))
        spans.append((pos, pos + 1, "opcode"))
        q = pos + 1
        for item in (pattern.split(",") if pattern else []):
            if item == "expr":
                q, _op = token(q)
            elif item == "parms":
                while True:
                    q, child_op = token(q)
                    if child_op == 0x16:
                        break
            elif item == "case":
                value = struct.unpack_from("<H", data, q)[0]
                spans.append((q, q + 2, "fixed"))
                q += 2
                if value != 0xFFFF:
                    q, _op = token(q)
            elif item == "index":
                start = q
                if q >= len(data):
                    raise Refused("%#x: index of opcode %02x runs off the end" % (pos, op))
                b = data[q]
                q += 1
                cont = b & 0x40
                while cont and q < len(data):
                    b = data[q]
                    q += 1
                    cont = b & 0x80
                spans.append((start, q, "index"))
            elif item.startswith("bytes:"):
                n = int(item.split(":", 1)[1])
                if q + n > len(data):
                    raise Refused("%#x: opcode %02x wants %d operand bytes, %d left" % (pos, op, n, len(data) - q))
                spans.append((q, q + n, "fixed"))
                q += n
            elif item == "str":
                end = data.find(b"\0", q)
                if end < 0:
                    raise Refused("%#x: string operand of %02x is not NUL-terminated" % (pos, op))
                spans.append((q, end + 1, "fixed"))
                q = end + 1
            elif item == "utf16":
                start = q
                while True:
                    if q + 2 > len(data):
                        raise Refused("%#x: UTF-16 string operand of %02x is not NUL-terminated" % (pos, op))
                    unit = struct.unpack_from("<H", data, q)[0]
                    q += 2
                    if unit == 0:
                        break
                spans.append((start, q, "fixed"))
            elif item == "labeltable":
                start = q
                while True:
                    if q + 8 > len(data):
                        raise Refused("%#x: label table of %02x is not zero-terminated" % (pos, op))
                    entry = struct.unpack_from("<I", data, q)[0]
                    q += 8
                    if entry == 0:
                        break
                spans.append((start, q, "fixed"))
            else:
                raise Refused("%#x: unknown pattern item %r in the table" % (pos, item))
        return q, op

    pos = 0
    while pos < len(data):
        pos, _op = token(pos)
    spans.sort()
    return spans


def hex_pattern(data, spans, limit):
    """A search pattern for the guest's copy of a script: `??` marks each byte of a widened index operand.

    `limit` is in script bytes, so a pattern for "the first 48 bytes" covers whole operands and the caller cannot
    accidentally cut a four-byte index slot in half.
    """
    out, cursor, used = [], 0, 0
    for start, end, kind in spans:
        if start < cursor:
            continue
        if kind == "index" and used + (start - cursor) < limit:
            out.append(data[cursor:start].hex())
            out.append("??" * 4)
            used += (start - cursor) + 1
        elif used + (end - cursor) < limit:
            out.append(data[cursor:end].hex())
            used += end - cursor
        else:
            break
        cursor = end
    return "".join(out)


def function_script(path, want_class, want_function, table, hex_bytes=0):
    """One function's script, from the header to the epilogue, as a token tree.

    The start is `name_end + 16` (`--start` proves it), the end is the epilogue, so this is the first path that
    disassembles a shipped function end to end rather than from a guessed offset.
    """
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    from ue2pkg import Package                                # noqa: PLC0415 - only for a real package

    pkg = Package(path)
    pkg.read_names()
    pkg.read_imports()
    pkg.read_exports()
    epilogue = bytes([0x04, 0x0B, 0x47])
    for e in pkg.exports:
        if pkg.class_name_of(e) != "Function" or e["outer"] <= 0:
            continue
        owner = pkg.exports[e["outer"] - 1]
        if pkg.name(owner["name"]).lower() != want_class.lower():
            continue
        if pkg.name(e["name"]).lower() != want_function.lower():
            continue
        pay = pkg.data[e["offset"]:e["offset"] + e["size"]]
        n = len(pay)
        off, last = 0, None
        for _ in range(6):
            last, off = read_index(pay, off)
        if last != e["name"]:
            print("%s.%s: the header does not end on FriendlyName" % (want_class, want_function))
            return 1
        if n >= 10 and pay[n - 10:n - 7] == epilogue:
            end = n - 7
        elif n >= 12 and pay[n - 12:n - 9] == epilogue:
            end = n - 9
        else:
            print("%s.%s: no epilogue, so no script" % (want_class, want_function))
            return 1
        start = off + 16
        print("%s.%s: payload %d bytes, script %d..%d (%d bytes), header %d bytes"
              % (want_class, want_function, n, start, end, end - start, start))
        try:
            nodes = dis_parse(pay[start:end], table)
        except Refused as exc:
            print("  the walk refuses at script offset %s" % exc)
            return 1
        if hex_bytes:
            # The pattern form. **The loaded copy is not the package's bytes**: measured on a 128 MiB dump of a
            # loaded `M00` (2026-11-07), every compact index operand arrives as a **four-byte slot** -- the file's
            # `b8 2a` at DrawOrderTarget's log call is `a0 1e 26 01` in the guest -- while jump offsets and fixed
            # operands are unchanged. So the pattern is emitted on that model: opcodes and fixed operands exact,
            # each index operand four `??` bytes, which `drive.py --step=brk=` matches as "any byte".
            spans = operand_spans(pay[start:end], table)
            print(hex_pattern(pay[start:end], spans, hex_bytes))
            return 0
        for line in render(nodes):
            print("  " + line)
        return 0
    print("%s.%s: no such function" % (want_class, want_function))
    return 1


def usage_report(path, table, out=None):
    """Which opcodes the shipped scripts actually use, over every payload whose script walks clean.

    A census, not a guess: an opcode no script uses cannot be checked against anything, and one that appears in
    thousands of payloads is worth naming first.
    """
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    from ue2pkg import Package                                # noqa: PLC0415 - only for a real package

    files = ([os.path.join(path, f) for f in sorted(os.listdir(path)) if f.lower().endswith(".u")]
             if os.path.isdir(path) else [path])
    epilogue = bytes([0x04, 0x0B, 0x47])
    counts = collections.Counter()
    payloads = collections.Counter()
    walked = 0
    seen = 0
    for one in files:
        pkg = Package(one)
        pkg.read_names()
        pkg.read_imports()
        pkg.read_exports()
        for e in pkg.exports:
            if pkg.class_name_of(e) != "Function":
                continue
            pay = pkg.data[e["offset"]:e["offset"] + e["size"]]
            n = len(pay)
            off, last, ok = 0, None, True
            for _ in range(6):
                try:
                    last, off = read_index(pay, off)
                except IndexError:
                    ok = False
                    break
            if not ok or last != e["name"]:
                continue
            if n >= 10 and pay[n - 10:n - 7] == epilogue:
                end = n - 7
            elif n >= 12 and pay[n - 12:n - 9] == epilogue:
                end = n - 9
            else:
                continue
            seen += 1
            start = off + 16
            if start >= end:
                continue
            try:
                nodes = dis_parse(pay[start:end], table)
            except Refused:
                continue
            if sum(x["end"] - x["offset"] for x in nodes) != end - start:
                continue
            walked += 1
            here = set()

            def visit(items):
                for node in items:
                    counts[node["opcode"]] += 1
                    here.add(node["opcode"])
                    visit(node["children"])
            visit(nodes)
            for op in here:
                payloads[op] += 1
    print("%d scripts with an epilogue, %d walk clean end to end" % (seen, walked))
    print("%-6s %-24s %10s %9s" % ("op", "name", "tokens", "payloads"))
    for op, _n in sorted(payloads.items(), key=lambda kv: -payloads[kv[0]]):
        print("0x%02x   %-24s %10d %9d" % (op, table.rows[op]["name"] or "-", counts[op], payloads[op]))
    if out:
        # Sorted by opcode, because this file is a table a reader looks things up in; the console listing is
        # sorted by use, which answers a different question.
        os.makedirs(out, exist_ok=True)
        with open(os.path.join(out, "opcode_usage.csv"), "w", newline="") as fh:
            w = csv.writer(fh, lineterminator="\n")
            w.writerow(["opcode", "name", "tokens", "payloads", "scripts_walked", "scripts_with_epilogue"])
            for op in sorted(payloads):
                w.writerow(["0x%02x" % op, table.rows[op]["name"], counts[op], payloads[op], walked, seen])
        print("wrote %s/opcode_usage.csv (%d opcodes, %d scripts)"
              % (out, len(payloads), walked))
    return 0



def resolve_report(path, table):
    """What each reference operand points at, over every script that walks clean.

    An opcode whose operand is an index is only *read* correctly if the value names the kind of object the token
    implies: a final-function reference should name a `Function`, a variable reference a property. This resolves
    every such operand against the package's own export and import tables and reports the distribution -- a check
    on the operand rules that no walk can give, because a wrong width can still happen to tile.
    """
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    from ue2pkg import Package                                # noqa: PLC0415 - only for a real package

    files = ([os.path.join(path, f) for f in sorted(os.listdir(path)) if f.lower().endswith(".u")]
             if os.path.isdir(path) else [path])
    epilogue = bytes([0x04, 0x0B, 0x47])
    kinds = collections.defaultdict(collections.Counter)
    counts = collections.Counter()
    walked = 0
    for one in files:
        pkg = Package(one)
        pkg.read_names()
        pkg.read_imports()
        pkg.read_exports()
        exports = [(pkg.name(e["name"]), pkg.class_name_of(e)) for e in pkg.exports]
        imports = [(pkg.name(i["name"]), pkg.name(i["class"])) for i in pkg.imports]
        # The same compact encoding indexes two different tables depending on the opcode: an object reference
        # names an export or an import, a name reference names a row of the name table. Both are in range most of
        # the time, so the discriminator has to be *what* the value lands on.
        function_names = {pkg.name(e["name"]) for e in pkg.exports if pkg.class_name_of(e) == "Function"}
        all_names = {n for n, _f in pkg.names}
        for e in pkg.exports:
            if pkg.class_name_of(e) != "Function":
                continue
            pay = pkg.data[e["offset"]:e["offset"] + e["size"]]
            n = len(pay)
            off, last, ok = 0, None, True
            for _ in range(6):
                try:
                    last, off = read_index(pay, off)
                except IndexError:
                    ok = False
                    break
            if not ok or last != e["name"]:
                continue
            if n >= 10 and pay[n - 10:n - 7] == epilogue:
                end = n - 7
            elif n >= 12 and pay[n - 12:n - 9] == epilogue:
                end = n - 9
            else:
                continue
            start = off + 16
            if start >= end:
                continue
            try:
                nodes = parse(pay[start:end], table)
            except Refused:
                continue
            if sum(x["end"] - x["offset"] for x in nodes) != end - start:
                continue
            walked += 1
            stack = list(nodes)
            while stack:
                node = stack.pop()
                stack.extend(node["children"])
                if not node["operands"] or not table.rows[node["opcode"]]["pattern"].startswith("index"):
                    continue
                name = table.rows[node["opcode"]]["name"] or "op%02x" % node["opcode"]
                value, _end = read_index(node["operands"], 0)
                counts[name] += 1
                if value == 0:
                    kinds[name]["none"] += 1
                    continue
                if value > 0 and value <= len(exports):
                    kinds[name][exports[value - 1][1]] += 1
                elif value < 0 and -value <= len(imports):
                    kinds[name][imports[-value - 1][1]] += 1
                else:
                    kinds[name]["out of range"] += 1
                # ... and the same value read as a name-table index, which is what a virtual call carries.
                if 0 < value < len(pkg.names):
                    nm = pkg.names[value][0]
                    kinds[name]["NAMES:" + ("a function in the package" if nm in function_names
                                            else "a name" if nm in all_names else "not a name")] += 1
    print("%d scripts walked clean; reference operands resolved against the package tables" % walked)
    for name, _n in counts.most_common():
        top = ", ".join("%s %d" % (k, v) for k, v in kinds[name].most_common(4))
        print("  %-18s %6d operands: %s" % (name, counts[name], top))
    return 0



CLASS_REF_TOKENS = ("ObjectConst", "DynamicCast", "MetaClassCast")


def class_refs_report(path, table, out=None):
    """Which classes the scripts name in code -- a class literal or a cast target -- resolved per package.

    A `ObjectConst`, `DynamicCast` or `MetaClassCast` operand is an object reference, so the value indexes the
    export table or (negated) the import table; when the object it names is itself a `Class`, the operand names
    that class. This is the code side of the question `tools/class-reach.py` asks from the content side: a class a
    script names is one a rebuild has to carry, whether or not a map places an instance of it. Naming is not
    instantiation -- `class'Foo'` passed to `Spawn` is, a `DynamicCast` target is only a test -- which is why the
    row records the token that named it.
    """
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    from ue2pkg import Package                                # noqa: PLC0415 - only for a real package

    files = ([os.path.join(path, f) for f in sorted(os.listdir(path)) if f.lower().endswith(".u")]
             if os.path.isdir(path) else [path])
    epilogue = bytes([0x04, 0x0B, 0x47])
    rows = []
    seen = walked = 0
    for one in files:
        pkg = Package(one)
        pkg.read_names()
        pkg.read_imports()
        pkg.read_exports()
        pkg_name = os.path.basename(one).rsplit(".", 1)[0]
        exports = [(pkg.name(e["name"]), pkg.class_name_of(e)) for e in pkg.exports]
        imports = [(pkg.name(i["name"]), pkg.name(i["class"])) for i in pkg.imports]
        for e in pkg.exports:
            if pkg.class_name_of(e) != "Function":
                continue
            pay = pkg.data[e["offset"]:e["offset"] + e["size"]]
            n = len(pay)
            off, last, ok = 0, None, True
            for _ in range(6):
                try:
                    last, off = read_index(pay, off)
                except IndexError:
                    ok = False
                    break
            if not ok or last != e["name"]:
                continue
            if n >= 10 and pay[n - 10:n - 7] == epilogue:
                end = n - 7
            elif n >= 12 and pay[n - 12:n - 9] == epilogue:
                end = n - 9
            else:
                continue
            seen += 1
            start = off + 16
            if start >= end:
                continue
            try:
                nodes = parse(pay[start:end], table)
            except Refused:
                continue
            if sum(x["end"] - x["offset"] for x in nodes) != end - start:
                continue
            walked += 1
            owner = pkg.name(pkg.exports[e["outer"] - 1]["name"]) if e["outer"] > 0 else "?"
            stack = list(nodes)
            while stack:
                node = stack.pop()
                stack.extend(node["children"])
                token = table.rows[node["opcode"]]["name"]
                if token not in CLASS_REF_TOKENS or not node["operands"]:
                    continue
                value, _e = read_index(node["operands"], 0)
                if 0 < value <= len(exports):
                    target = exports[value - 1]
                elif value < 0 and -value <= len(imports):
                    target = imports[-value - 1]
                else:
                    continue
                rows.append((pkg_name, owner, pkg.name(e["name"]), token, target[0], target[1]))
    named = [r for r in rows if r[5] == "Class"]
    print("%d scripts walked; %d class-reference operands, %d of them name a Class (%d distinct)"
          % (walked, len(rows), len(named), len({r[4] for r in named})))
    print("  by token: %s"
          % ", ".join("%s %d" % kv for kv in collections.Counter(r[3] for r in named).most_common()))
    print("  top classes named: %s"
          % ", ".join("%s %d" % kv for kv in collections.Counter(r[4] for r in named).most_common(8)))
    if out:
        os.makedirs(out, exist_ok=True)
        with open(os.path.join(out, "script_class_refs.csv"), "w", newline="") as fh:
            w = csv.writer(fh, lineterminator="\n")
            w.writerow(["package", "class", "function", "token", "target", "target_class"])
            for row in rows:
                w.writerow(list(row))
        print("wrote %s/script_class_refs.csv (%d rows)" % (out, len(rows)))
    return 0


CALL_TOKENS = {"FinalFunction": "static", "VirtualFunction": "by name", "GlobalFunction": "by name"}


def native_calls_report(path, table, out=None):
    """What the opcodes above 0x5f call: the token is the native index, read against the caller's chain.

    The opcode table stops naming at `0x5f` and carries `0x60..0x6f` as "one byte then parms" and everything above
    it as "parms" -- the operand shape of a native call without saying which native. `GNatives` holds the bodies:
    slot `n` is native `n` for the declared band, and the image's own `.data` record for that slot spells the body's
    name (`{slot, intUObjectexec<Function>}`). Three candidate index rules are scored side by side -- `op`, `op-0x70`
    (this tool's first reading) and `op-0x60` -- because arithmetic on the token is what the earlier census guessed
    and the token needs no arithmetic at all.
    """
    from ue2pkg import Package                                # noqa: PLC0415 - only for a real package
    supers = {r["class"]: r["super"]
              for r in csv.DictReader(open(os.path.join(ROOT, "inventory", "classes.csv")))}
    declared = collections.defaultdict(dict)
    for r in csv.DictReader(open(os.path.join(ROOT, "inventory", "natives.csv"))):
        token = int(r["native_token"] or 0)
        if token:
            declared[r["class"]][token] = (r["class"], r["function"], int(r["parameter_count"] or 0))
    # The body each GNatives slot holds, named by the image's own record rather than by a script declaration --
    # this is the half of the question the declarations cannot answer on their own.
    bodies = {}
    records = os.path.join(ROOT, "inventory", "gnatives_records.csv")
    if os.path.exists(records):
        for r in csv.DictReader(open(records)):
            bodies[int(r["slot"])] = r["record_name"] or ""

    def chain_of(cls):
        names, seen = [], set()
        while cls and cls not in seen:
            seen.add(cls)
            names.append(cls)
            cls = supers.get(cls, "")
        return names

    def lookup(cls, index):
        for name in chain_of(cls):
            got = declared.get(name, {}).get(index)
            if got:
                return got
        return None

    def body_names(index, hit=None):
        """The body at `index` is `exec<Function>` for the declaration the same index resolves to."""
        record = bodies.get(index, "")
        if not record or not hit:
            return False
        return record.endswith("exec" + hit[1])

    def arg_hist(hist):
        """`2:4100/5368` -- one native means one argument count; a per-class ordinal does not."""
        total = sum(hist.values()) or 1
        return ";".join("%d:%d/%d" % (n, c, total) for n, c in hist.most_common(3))

    files = ([os.path.join(path, f) for f in sorted(os.listdir(path)) if f.lower().endswith(".u")]
             if os.path.isdir(path) else [path])
    epilogue = bytes([0x04, 0x0B, 0x47])
    # sites, resolved, param-fit, (class, fn, params), argument-count histogram
    rows = collections.defaultdict(lambda: [0, 0, 0, None, collections.Counter()])
    scores = {"A": [0, 0, 0, 0], "B": [0, 0, 0, 0], "C": [0, 0, 0, 0]}   # sites, resolved, fit, body
    walked = seen_payloads = 0
    for one in files:
        pkg = Package(one)
        pkg.read_names()
        pkg.read_imports()
        pkg.read_exports()
        for e in pkg.exports:
            if pkg.class_name_of(e) != "Function":
                continue
            # A Function export's class is its Outer export, named through that object's own Name.
            outer_ix = e.get("outer")
            cls = pkg.name(pkg.exports[outer_ix - 1]["name"]) if outer_ix else ""
            pay = pkg.data[e["offset"]:e["offset"] + e["size"]]
            n = len(pay)
            off, last, ok = 0, None, True
            for _ in range(6):
                try:
                    last, off = read_index(pay, off)
                except IndexError:
                    ok = False
                    break
            if not ok or last != e["name"]:
                continue
            if n >= 10 and pay[n - 10:n - 7] == epilogue:
                end = n - 7
            elif n >= 12 and pay[n - 12:n - 9] == epilogue:
                end = n - 9
            else:
                continue
            seen_payloads += 1
            start = off + 16
            if start >= end:
                continue
            try:
                nodes = parse(pay[start:end], table)
            except Refused:
                continue
            if sum(x["end"] - x["offset"] for x in nodes) != end - start:
                continue
            walked += 1
            stack = list(nodes)
            while stack:
                node = stack.pop()
                stack.extend(node["children"])
                op = node["opcode"]
                if op < 0x60:
                    continue
                operand = node["operands"][0] if (0x60 <= op <= 0x6f and node["operands"]) else None
                args = len([c for c in node["children"]]) - 1       # the terminating 0x16 is a child too
                if op > 0x6f:
                    candidates = {"A": op - 0x70, "B": op - 0x60, "C": op}
                else:
                    candidates = {"A": ((op - 0x60) << 8) | (operand or 0), "B": operand or 0,
                                  "C": ((op - 0x60) << 8) | (operand or 0)}
                for rule, index in candidates.items():
                    scores[rule][0] += 1
                    hit = lookup(cls, index)
                    if hit:
                        scores[rule][1] += 1
                        if hit[2] == args:
                            scores[rule][2] += 1
                        if body_names(index, hit):
                            scores[rule][3] += 1
                index = candidates["C"]
                hit = lookup(cls, index)
                key = (op, operand, index)
                cell = rows[key]
                cell[0] += 1
                cell[4][args] += 1
                if hit:
                    cell[1] += 1
                    if hit[2] == args:
                        cell[2] += 1
                    cell[3] = hit
    print("%d payload(s) with an epilogue, %d walk clean" % (seen_payloads, walked))
    for rule, label in (("A", "op-0x70"), ("B", "op-0x60"), ("C", "the token itself")):
        sites, resolved, fit, named = scores[rule]
        print("rule %s (%s): %d native-call token(s), %d name a declaration on the caller's chain, %d also match "
              "its declared parameter count, %d name a body the image's own GNatives record spells"
              % (rule, label, sites, resolved, fit, named))
    print("%-6s %-7s %6s %7s %8s %8s  %-22s %-22s %s"
          % ("op", "operand", "index", "sites", "resolved", "params", "body (GNatives record)", "native",
             "args (top 3)"))
    for (op, operand, index), (sites, resolved, fit, hit, hist) in \
            sorted(rows.items(), key=lambda kv: -kv[1][0])[:40]:
        record = bodies.get(index, "")
        print("0x%02x   %-7s %6d %7d %8d %8d  %-22s %-22s %s"
              % (op, "-" if operand is None else "0x%02x" % operand, index, sites, resolved, fit,
                 (record + (" *" if body_names(index, hit) else "")) if record else "-",
                 ("%s.%s" % (hit[0], hit[1])) if hit else "-", arg_hist(hist)))
    if out:
        os.makedirs(out, exist_ok=True)
        name = os.path.join(out, "native_opcodes.csv")
        with open(name, "w", newline="") as fh:
            w = csv.writer(fh, lineterminator="\n")
            w.writerow(["opcode", "operand", "index", "sites", "resolved", "param_fit", "body_name", "body_match",
                        "native", "declared_in", "declared_params", "args"])
            for (op, operand, index), (sites, resolved, fit, hit, hist) in sorted(rows.items()):
                w.writerow(["0x%02x" % op, "" if operand is None else "0x%02x" % operand, index, sites, resolved,
                            fit, bodies.get(index, ""), int(body_names(index, hit)),
                            hit[1] if hit else "", hit[0] if hit else "", hit[2] if hit else "",
                            arg_hist(hist)])
        print("wrote %s (%d row(s), %d sites)" % (name, len(rows), sum(v[0] for v in rows.values())))
    return 0


def calls_report(path, table, out=None):
    """Every script-level call the corpus makes, resolved as far as the operand rules allow.

    A `FinalFunction` operand is an object reference, so the call is static and the target is known; a
    `VirtualFunction` or `GlobalFunction` operand is a name, so the target is a name the runtime resolves against the
    receiver -- which is recorded as such rather than guessed. A name that is not a function of the caller's own
    package is looked up on the caller's super chain in the package that declares each ancestor, because a native
    parent's function is not in the child's name table and "not here" is not "nowhere". This is the script half of a
    call graph, and the static edges are the ones a reader can rely on.
    """
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    from ue2pkg import Package                                # noqa: PLC0415 - only for a real package

    files = ([os.path.join(path, f) for f in sorted(os.listdir(path)) if f.lower().endswith(".u")]
             if os.path.isdir(path) else [path])
    # (package, class) -> (package the super is declared in, super): the chain a by-name call is resolved along.
    supers = {}
    for r in csv.DictReader(open(os.path.join(ROOT, "inventory", "classes.csv"))):
        supers[(r["package"], r["class"])] = (r["package_of_super"] or r["package"], r["super"])
    # Every package's Function exports, by declaring class -- read from the export tables alone, no payload walk.
    funcs = collections.defaultdict(set)
    # name -> the classes that declare a function of that name, anywhere in the corpus. A virtual call dispatches
    # by name on the *receiver's* class, so a name only one class declares can reach exactly one implementation
    # whatever the receiver is, and a name several classes declare is where the receiver would have decided. A
    # declaration inside a State is owned by the class that owns the state: the state is compiled into that class.
    decls = collections.defaultdict(set)
    for one in files:
        pkg_name = os.path.basename(one).rsplit(".", 1)[0]
        pre = Package(one)
        pre.read_names()
        pre.read_imports()
        pre.read_exports()
        for e in pre.exports:
            outer = e.get("outer") or 0
            if pre.class_name_of(e) != "Function" or not outer:
                continue
            fn = pre.name(e["name"])
            oe = pre.exports[outer - 1]
            funcs[(pkg_name, pre.name(oe["name"]))].add(fn)
            owner = (pkg_name, pre.name(oe["name"]))
            if pre.class_name_of(oe) == "State" and oe.get("outer"):
                owner = (pkg_name, pre.name(pre.exports[oe["outer"] - 1]["name"]))
            decls[fn].add(owner)

    def resolve_name(pkg_name, owner, name):
        """Where a by-name call's target is declared.

        First the caller's own class, then its super chain in the package that declares each ancestor. Those two
        answer for a call to `this`; a call to another object's virtual resolves on that object's class, which the
        site does not name. So the last step is the corpus: the set of classes that declare a function of that name.
        One means the call can only reach that one implementation; several means the receiver picks, and the count
        records how wide the choice is. Returns (where, how_many_classes_declare_the_name, the_only_class).
        """
        if name in funcs.get((pkg_name, owner), ()):
            return "package", len(decls.get(name, ())), ""
        here, seen = (pkg_name, owner), set()
        while here in supers and here not in seen:
            seen.add(here)
            here = supers[here]
            if name in funcs.get(here, ()):
                return "%s.%s" % here, len(decls.get(name, ())), ""
        owners = decls.get(name, set())
        if len(owners) == 1:
            (op, oc), = owners
            return "corpus:%s.%s" % (op, oc), 1, "%s.%s" % (op, oc)
        return "", len(owners), ""
    epilogue = bytes([0x04, 0x0B, 0x47])
    rows = []
    walked = seen = 0
    for one in files:
        pkg = Package(one)
        pkg.read_names()
        pkg.read_imports()
        pkg.read_exports()
        pkg_name = os.path.basename(one).rsplit(".", 1)[0]
        exports = [(pkg.name(e["name"]), pkg.class_name_of(e)) for e in pkg.exports]
        imports = [(pkg.name(i["name"]), pkg.name(i["class"])) for i in pkg.imports]
        # A by-name call carries a name-table index; whether it names a function is a question for the chain above.
        for e in pkg.exports:
            if pkg.class_name_of(e) != "Function":
                continue
            pay = pkg.data[e["offset"]:e["offset"] + e["size"]]
            n = len(pay)
            off, last, ok = 0, None, True
            for _ in range(6):
                try:
                    last, off = read_index(pay, off)
                except IndexError:
                    ok = False
                    break
            if not ok or last != e["name"]:
                continue
            if n >= 10 and pay[n - 10:n - 7] == epilogue:
                end = n - 7
            elif n >= 12 and pay[n - 12:n - 9] == epilogue:
                end = n - 9
            else:
                continue
            seen += 1
            start = off + 16
            if start >= end:
                continue
            try:
                nodes = parse(pay[start:end], table)
            except Refused:
                continue
            if sum(x["end"] - x["offset"] for x in nodes) != end - start:
                continue
            walked += 1
            owner = pkg.name(pkg.exports[e["outer"] - 1]["name"]) if e["outer"] > 0 else "?"
            stack = list(nodes)
            while stack:
                node = stack.pop()
                stack.extend(node["children"])
                kind = CALL_TOKENS.get(table.rows[node["opcode"]]["name"])
                if kind is None or not node["operands"]:
                    continue
                value, _e = read_index(node["operands"], 0)
                token = table.rows[node["opcode"]]["name"]
                if kind == "by name":
                    name = pkg.name(value) if 0 <= value < len(pkg.names) else ""
                    where, nowners, uniq = resolve_name(pkg_name, owner, name) if name else ("", 0, "")
                    rows.append((pkg_name, owner, pkg.name(e["name"]), token, kind, name,
                                 "Function" if where else "name", where, nowners, uniq))
                    continue
                if value == 0:
                    rows.append((pkg_name, owner, pkg.name(e["name"]), token, kind, "", ""))
                elif 0 < value <= len(exports):
                    rows.append((pkg_name, owner, pkg.name(e["name"]), token, kind, exports[value - 1][0],
                                 exports[value - 1][1]))
                elif -value <= len(imports):
                    rows.append((pkg_name, owner, pkg.name(e["name"]), token, kind, imports[-value - 1][0],
                                 imports[-value - 1][1]))
                else:
                    rows.append((pkg_name, owner, pkg.name(e["name"]), token, kind, "", "out of range"))
    static = [r for r in rows if r[4] == "static"]
    by_name = [r for r in rows if r[4] == "by name"]
    print("%d scripts with an epilogue, %d walked; %d call sites" % (seen, walked, len(rows)))
    print("  static (FinalFunction): %d, of which the target is a Function: %d"
          % (len(static), sum(1 for r in static if r[6] == "Function")))
    here = [r for r in by_name if r[7] == "package"]
    above = [r for r in by_name if r[7] and r[7] != "package" and not r[7].startswith("corpus:")]
    corpus = [r for r in by_name if r[7].startswith("corpus:")]
    wide = [r for r in by_name if not r[7] and r[5]]
    only = {r[5]: r[9] for r in corpus}
    wide_n = {r[5]: r[8] for r in wide}
    print("  by name (VirtualFunction, GlobalFunction): %d" % len(by_name))
    print("    on the caller's chain: %d -- %d in its own class, %d on an ancestor (top: %s)"
          % (len(here) + len(above), len(here), len(above),
             ", ".join("%s %d" % kv for kv in collections.Counter(r[7] for r in above).most_common(5))))
    print("    by the corpus alone: %d site(s) reach the one class that declares the name (%d names), top: %s"
          % (len(corpus), len(only),
             ", ".join("%s -> %s %d" % (n, only[n], c)
                       for n, c in collections.Counter(r[5] for r in corpus).most_common(5))))
    print("    decided by the receiver: %d site(s) across %d names, top: %s"
          % (len(wide), len(wide_n),
             ", ".join("%s %d over %d classes" % (n, c, wide_n[n])
                       for n, c in collections.Counter(r[5] for r in wide).most_common(5))))
    callees = collections.Counter([r[5] for r in static if r[6] == "Function" and r[5]]
                                  + [r[5] for r in by_name if r[5] and r[7]])
    print("  most-called resolved targets: %s" % ", ".join("%s %d" % kv for kv in callees.most_common(8)))
    if out:
        os.makedirs(out, exist_ok=True)
        with open(os.path.join(out, "script_calls.csv"), "w", newline="") as fh:
            w = csv.writer(fh, lineterminator="\n")
            w.writerow(["package", "class", "function", "token", "dispatch", "target", "target_class",
                        "resolved_in", "declaring_classes", "only_class"])
            for row in rows:
                w.writerow(list(row) + [""] * (10 - len(row)))
        print("wrote %s/script_calls.csv (%d rows)" % (out, len(rows)))
    return 0



def switches_report(path, table, out=None):
    """What a `0x05` switch's operand byte is: the width, in bytes, of the value it compares.

    `execSwitch` is the body `GNatives` slot 5 holds (`0x0004ca20`, 348 bytes) and it reads the operand itself:
    one byte, then the switch expression evaluated into a 12-byte result buffer, then per case a 16-bit offset, a
    case value expression into a second buffer, and a comparison. `FUN_00036bd0` is that comparison -- `repz cmpsb`
    over the count in its third argument, no locals, `ret` -- and when the byte is zero the body calls `__wcsicmp`
    (`0x00036d00`) on the two buffers instead. So the byte is the number of bytes compared, i.e. the width of the
    value the switch compares, and zero says the value is a wide string.

    This mode is the corpus side of that reading: the byte each switch carries, the opcodes the expression and the
    case values are built from, and how many cases follow. The width reading has to survive three pairs -- 1 with
    one-byte constants, 4 with `IntConst`, 0 with `StringConst` -- and any other byte is a finding.
    """
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    from ue2pkg import Package                                # noqa: PLC0415 - only for a real package

    def nm(op):
        return (table.rows[op]["name"] or "op%02x" % op) if op in table.rows else "?"

    files = ([os.path.join(path, f) for f in sorted(os.listdir(path)) if f.lower().endswith(".u")]
             if os.path.isdir(path) else [path])
    rows = []
    sites = collections.Counter()          # (byte, expression opcode) -> sites
    values = collections.Counter()         # (byte, case-value opcode) -> case values
    span = {}                              # byte -> [min cases, max cases]
    ended = collections.Counter()          # (byte, how the case list ended) -> sites
    walked = 0
    for one in files:
        pkg = Package(one)
        pkg.read_names()
        pkg.read_imports()
        pkg.read_exports()
        name = os.path.basename(one).rsplit(".", 1)[0]
        for e in pkg.exports:
            if pkg.class_name_of(e) != "Function":
                continue
            found = top_level(pkg, e, table)
            if found is None:
                continue
            nodes, data = found
            walked += 1
            cls = pkg.name(pkg.exports[e["outer"] - 1]["name"]) if e.get("outer") else "-"
            func = pkg.name(e["name"])
            for i, node in enumerate(nodes):
                if node["opcode"] != 0x05:
                    continue
                byte = node["operands"][0] if node["operands"] else -1
                expr = node["children"][0]["opcode"] if node["children"] else -1
                sites[(byte, expr)] += 1
                seen, vals, why = 0, [], "end"
                for nxt in nodes[i + 1:]:
                    if nxt["opcode"] == 0x05:
                        why = "nested-switch"
                        break
                    if nxt["opcode"] != 0x0A:
                        continue
                    if nxt["operands"][:2] == b"\xff\xff":
                        why = "terminator"
                        break
                    seen += 1
                    # The case is `<2-byte offset><value expression>`; the byte after the offset names the
                    # token the value is built from, which is the check this census exists for.
                    pos = nxt["offset"] + 3
                    if pos < len(data):
                        vals.append(data[pos])
                        values[(byte, data[pos])] += 1
                ended[(byte, why)] += 1
                lo, hi = span.get(byte, (None, None))
                span[byte] = (seen if lo is None else min(lo, seen), seen if hi is None else max(hi, seen))
                rows.append([name, cls, func, "0x%04x" % node["offset"], byte, nm(expr), seen,
                             ";".join(nm(v) for v in sorted(set(vals))) or "-", why])
    total = sum(sites.values())

    print("%d scripts walk clean; %d switch sites" % (walked, total))
    print("%-4s %6s %-30s %-30s %s" % ("byte", "sites", "expression built from", "case values built from", "cases"))
    for byte in sorted(span):
        exprs = [(nm(op), n) for (b, op), n in sites.items() if b == byte]
        vals = [(nm(op), n) for (b, op), n in values.items() if b == byte]
        print("%-4d %6d %-30s %-30s %d..%d" % (
            byte, sum(n for _t, n in exprs),
            ", ".join("%s %d" % kv for kv in sorted(exprs, key=lambda kv: -kv[1])[:3]),
            ", ".join("%s %d" % kv for kv in sorted(vals, key=lambda kv: -kv[1])[:3]),
            span[byte][0], span[byte][1]))
    print("how each case list ended: %s" % ", ".join(
        "byte %d %s %d" % (b, why, n) for (b, why), n in sorted(ended.items(), key=lambda kv: -kv[1])))
    if out:
        os.makedirs(out, exist_ok=True)
        path_out = os.path.join(out, "switch_operands.csv")
        with open(path_out, "w", newline="") as fh:
            w = csv.writer(fh, lineterminator="\n")
            w.writerow(["package", "class", "function", "offset", "width", "expression", "cases",
                        "case_values", "ended"])
            for row in sorted(rows):
                w.writerow(row)
        print("wrote %s (%d switch sites, widths %s)"
              % (path_out, total, ", ".join(str(b) for b in sorted(span))))
    return 0


def top_level(pkg, export, table):
    """A function's top-level token list and the bytes it was parsed from, or None when it does not disassemble."""
    epilogue = bytes([0x04, 0x0B, 0x47])
    pay = pkg.data[export["offset"]:export["offset"] + export["size"]]
    n = len(pay)
    off, last, ok = 0, None, True
    for _ in range(6):
        try:
            last, off = read_index(pay, off)
        except IndexError:
            ok = False
            break
    if not ok or last != export["name"]:
        return None
    if n >= 10 and pay[n - 10:n - 7] == epilogue:
        end = n - 7
    elif n >= 12 and pay[n - 12:n - 9] == epilogue:
        end = n - 9
    else:
        return None
    start = off + 16
    if start >= end:
        return None
    data = pay[start:end]
    try:
        nodes = parse(data, table)
    except Refused:
        return None
    if sum(x["end"] - x["offset"] for x in nodes) != len(data):
        return None
    return nodes, data


def flatten(nodes):
    """Tokens in stream order, as (opcode, operands) -- the unit a match percentage is computed over."""
    out = []
    for n in nodes:
        out.append((n["opcode"], n["operands"]))
        out.extend(flatten(n["children"]))
    return out


def compare_tokens(a, b):
    """How two token streams differ: bytes, opcode sequence, or halfway.

    The three tiers this project grades with are `L2` (opcode sequence matches) and `L3` (bytes match), so a
    comparison has to report them separately -- an operand that changed is not an opcode that changed.
    """
    import difflib
    same_bytes = a == b
    same_opcodes = [op for op, _ in a] == [op for op, _ in b]
    ratio = difflib.SequenceMatcher(None, [op for op, _ in a], [op for op, _ in b]).ratio()
    return {"bytes": same_bytes, "opcodes": same_opcodes, "similarity": ratio, "tokens": (len(a), len(b))}


def script_of(pkg, export, table):
    """One function's token stream, or None when the payload does not disassemble."""
    epilogue = bytes([0x04, 0x0B, 0x47])
    pay = pkg.data[export["offset"]:export["offset"] + export["size"]]
    n = len(pay)
    off, last, ok = 0, None, True
    for _ in range(6):
        try:
            last, off = read_index(pay, off)
        except IndexError:
            ok = False
            break
    if not ok or last != export["name"]:
        return None
    if n >= 10 and pay[n - 10:n - 7] == epilogue:
        end = n - 7
    elif n >= 12 and pay[n - 12:n - 9] == epilogue:
        end = n - 9
    else:
        return None
    start = off + 16
    if start >= end:
        return None
    try:
        nodes = parse(pay[start:end], table)
    except Refused:
        return None
    if sum(x["end"] - x["offset"] for x in nodes) != end - start:
        return None
    return flatten(nodes)


def compare_report(path_a, path_b, table, out=None):
    """Compare two packages function by function: is a rebuild the same bytes, the same opcodes, or neither?

    This is the comparator half of PLAN 3.8. It needs no compiler -- it compares two packages -- so it can be built and
    validated now, and pointed at a recompiled package the day one exists. Comparing a package with itself is the
    integration test that it is wired correctly.
    """
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    from ue2pkg import Package                                # noqa: PLC0415 - only for a real package

    def index_of(path):
        pkg = Package(path)
        pkg.read_names()
        pkg.read_imports()
        pkg.read_exports()
        out = {}
        for e in pkg.exports:
            if pkg.class_name_of(e) != "Function" or e["outer"] <= 0:
                continue
            owner = pkg.name(pkg.exports[e["outer"] - 1]["name"])
            out[(owner, pkg.name(e["name"]))] = e
        return pkg, out

    pkg_a, funcs_a = index_of(path_a)
    pkg_b, funcs_b = index_of(path_b)
    shared = sorted(set(funcs_a) & set(funcs_b))
    rows = []
    only_a = sorted(set(funcs_a) - set(funcs_b))
    only_b = sorted(set(funcs_b) - set(funcs_a))
    skipped = 0
    for key in shared:
        ta = script_of(pkg_a, funcs_a[key], table)
        tb = script_of(pkg_b, funcs_b[key], table)
        if ta is None or tb is None:
            skipped += 1
            continue
        c = compare_tokens(ta, tb)
        rows.append((key[0], key[1], c["tokens"][0], c["bytes"], c["opcodes"], round(c["similarity"], 4)))
    byte_identical = sum(1 for r in rows if r[3])
    opcode_identical = sum(1 for r in rows if r[4])
    mean = sum(r[5] for r in rows) / len(rows) if rows else 0.0
    print("%s vs %s" % (os.path.basename(path_a), os.path.basename(path_b)))
    print("  %d functions in both, %d only in the first, %d only in the second, %d not disassembled"
          % (len(shared), len(only_a), len(only_b), skipped))
    print("  byte-identical (L3): %d; opcode-identical (L2): %d; mean opcode similarity: %.2f%%"
          % (byte_identical, opcode_identical, 100.0 * mean))
    diff = sorted([r for r in rows if not r[4]], key=lambda r: r[5])
    for r in diff[:5]:
        print("  differs: %s.%s  %d vs %d tokens, %.0f%% of the opcode sequence"
              % (r[0], r[1], r[2], r[2], 100.0 * r[5]))
    if out:
        os.makedirs(out, exist_ok=True)
        with open(os.path.join(out, "script_match.csv"), "w", newline="") as fh:
            w = csv.writer(fh, lineterminator="\n")
            w.writerow(["class", "function", "tokens", "bytes_identical", "opcodes_identical", "similarity"])
            for row in rows:
                w.writerow(row)
        print("wrote %s/script_match.csv (%d rows)" % (out, len(rows)))
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--hex", help="a script as hex")
    ap.add_argument("--file", help="a script as a binary file")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--header", metavar="PATH",
                    help="classify the five values in front of FriendlyName, for a package or a System directory")
    ap.add_argument("--script", nargs=3, metavar=("PACKAGE", "CLASS", "FUNCTION"),
                    help="disassemble one function's script from the header to the epilogue")
    ap.add_argument("--hex-bytes", type=int, default=0, metavar="N",
                    help="with --script, print the first N script bytes as hex and stop: that string is the pattern "
                         "`drive.py --step=brk=` searches a RAM dump for (docs/XEMU.md)")
    ap.add_argument("--compare", nargs=2, metavar=("PACKAGE_A", "PACKAGE_B"),
                    help="compare two packages function by function: bytes, opcode sequence, or neither")
    ap.add_argument("--native-calls", metavar="PATH",
                    help="what the opcodes above 0x5f call: each index read against the caller's native chain")
    ap.add_argument("--calls", metavar="PATH",
                    help="every script-level call site, static or by name, for a package or a System directory")
    ap.add_argument("--class-refs", metavar="PATH",
                    help="which classes the scripts name in code: class literals and cast targets")
    ap.add_argument("--resolve", metavar="PATH",
                    help="for each index operand, what the value points at (a check on the operand rules)")
    ap.add_argument("--switches", metavar="PATH",
                    help="what a 0x05 switch's operand byte is: the width of the value it compares")
    ap.add_argument("--usage", metavar="PATH",
                    help="which opcodes the shipped scripts that walk clean actually use")
    ap.add_argument("-o", "--out", help="write inventory/opcode_usage.csv into this directory (with --usage)")
    ap.add_argument("--start", metavar="PATH",
                    help="three readings of where a script starts: payload floor, walk depth, the dword's meaning")
    ap.add_argument("--payloads", metavar="PACKAGE", help="measure the payload trailer of a package's Function exports")
    args = ap.parse_args()

    if args.header:
        return header_report(args.header)
    if args.start:
        return start_report(args.start)
    if args.script:
        return function_script(args.script[0], args.script[1], args.script[2], Table(), hex_bytes=args.hex_bytes)
    if args.switches:
        return switches_report(args.switches, Table(), args.out)
    if args.usage:
        return usage_report(args.usage, Table(), args.out)
    if args.resolve:
        return resolve_report(args.resolve, Table())
    if args.native_calls:
        return native_calls_report(args.native_calls, Table(), args.out)
    if args.calls:
        return calls_report(args.calls, Table(), args.out)
    if args.class_refs:
        return class_refs_report(args.class_refs, Table(), args.out)
    if args.compare:
        return compare_report(args.compare[0], args.compare[1], Table(), args.out)
    if args.selftest:
        return selftest()
    if args.payloads:
        return payloads(args.payloads)
    if args.hex is not None:
        data = bytes.fromhex(args.hex.replace(" ", "").replace("0x", ""))
    elif args.file:
        data = open(args.file, "rb").read()
    else:
        ap.error("one of --hex, --file, --selftest or --payloads is required")
    table = Table()
    try:
        nodes = parse(data, table)
    except Refused as exc:
        print("refused: %s" % exc)
        return 1
    for line in render(nodes):
        print(line)
    total = sum(n["end"] - n["offset"] for n in nodes)
    print("%d token(s), %d byte(s)" % (len(nodes), total))
    return 0


if __name__ == "__main__":
    sys.exit(main())
