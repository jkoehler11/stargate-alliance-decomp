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
        ("final function with an empty parameter list", bytes([0x1C, 0, 0, 0, 0, 0x16]), 1, 6),
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
        ("a parameter list with no 0x16", bytes([0x1C, 0, 0, 0, 0, 0x47])),
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
    """What the five values in front of `FriendlyName` are, resolved against the package's own tables.

    The decode is aligned by requiring the sixth value to be the export's name index, then each of the five is
    classified by what it points at: zero, a function with this one's name in another outer (`Super`), another
    function in the same outer (`Next`), or a child of this function (`Children`).
    """
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    from ue2pkg import Package                                # noqa: PLC0415 - only for a real package

    files = []
    if os.path.isdir(path):
        files = [os.path.join(path, f) for f in sorted(os.listdir(path)) if f.lower().endswith(".u")]
    else:
        files = [path]
    kind = [collections.Counter() for _ in range(5)]
    total = 0
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
            if pkg.class_name_of(e) != "Function":
                continue
            pay = pkg.data[e["offset"]:e["offset"] + e["size"]]
            try:
                off, values = 0, []
                for _ in range(6):
                    value, off = read_index(pay, off)
                    values.append(value)
            except IndexError:
                continue
            if values[5] != e["name"]:
                continue
            total += 1
            name = pkg.name(e["name"])
            kids = children[e["index"] + 1]
            for i, v in enumerate(values[:5]):
                c = kind[i]
                if v == 0:
                    c["zero"] += 1
                elif v > 0 and v <= len(pkg.exports):
                    target = pkg.exports[v - 1]
                    if v == e["outer"]:
                        c["own outer"] += 1
                    elif v in kids:
                        c["child"] += 1
                    elif pkg.name(target["name"]) == name:
                        c["same-name function"] += 1
                    elif pkg.class_name_of(target) == "Function":
                        c["other function"] += 1
                    else:
                        c["other export"] += 1
                elif v < 0 and -v <= len(pkg.imports):
                    c["import"] += 1
                else:
                    c["out of range"] += 1
    labels = ["v0", "v1", "v2", "v3", "v4"]
    print("%-4s %8s %8s %8s %8s %8s %8s %8s" %
          ("", "zero", "import", "same-name", "other fn", "child", "other", "out"))
    for label, c in zip(labels, kind):
        print("%-4s %8d %8d %8d %8d %8d %8d %8d" %
              (label, c["zero"], c["import"], c["same-name function"], c["other function"], c["child"],
               c["other export"] + c["own outer"], c["out of range"]))
    print("payloads whose sixth value is the export's own name index: %d" % total)
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--hex", help="a script as hex")
    ap.add_argument("--file", help="a script as a binary file")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--header", metavar="PATH",
                    help="classify the five values in front of FriendlyName, for a package or a System directory")
    ap.add_argument("--payloads", metavar="PACKAGE", help="measure the payload trailer of a package's Function exports")
    args = ap.parse_args()

    if args.header:
        return header_report(args.header)
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
