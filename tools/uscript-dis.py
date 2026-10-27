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


def payloads(path, natives=os.path.join(ROOT, "inventory", "natives.csv")):
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    from ue2pkg import Package                                # noqa: PLC0415 - only for a real package

    pkg = Package(path)
    pkg.read_names()
    pkg.read_imports()
    pkg.read_exports()
    exports = {e["index"]: e for e in pkg.exports}
    pkg_name = os.path.basename(path).rsplit(".", 1)[0]

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
                known[(r["class"].lower(), r["function"].lower())] = (int(r["parameter_count"]),
                                                                      flags(r["function_flags"]))
    payloads_seen = 0
    joined = matched = 0
    epilogue = 0
    size_hits = 0
    size_hits_epilogue_only = 0
    for e in pkg.exports:
        if pkg.class_name_of(e) != "Function":
            continue
        pay = pkg.data[e["offset"]:e["offset"] + e["size"]]
        if len(pay) < 16:
            continue
        payloads_seen += 1
        if len(pay) >= 10 and pay[-10:-7] == bytes([0x04, 0x0B, 0x47]):
            epilogue += 1
        owner = exports.get(e["outer"] - 1) if e["outer"] > 0 else None
        if owner is not None:
            key = (pkg.name(owner["name"]).lower(), pkg.name(e["name"]).lower())
            if key in known:
                joined += 1
                if struct.unpack_from("<I", pay, len(pay) - 4)[0] == known[key][1]:
                    matched += 1
        end = len(pay) - 7
        hits = [k for k in range(0, max(0, end - 4))
                if struct.unpack_from("<I", pay, k)[0] == end - (k + 4)]
        if hits:
            size_hits += 1
            if end - (hits[0] + 4) == 3:
                size_hits_epilogue_only += 1
    print("%s: %d Function exports with a payload of 16 bytes or more" % (pkg_name, payloads_seen))
    print("  FunctionFlags at payload_end-4 equals inventory/natives.csv: %d of %d joinable"
          % (matched, joined))
    print("  script epilogue 04 0b 47 at payload_end-10..end-8: %d of %d" % (epilogue, payloads_seen))
    print("  a dword immediately before the script equal to its length: %d (of which %d are the 3-byte "
          "epilogue-only body)" % (size_hits, size_hits_epilogue_only))
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--hex", help="a script as hex")
    ap.add_argument("--file", help="a script as a binary file")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--payloads", metavar="PACKAGE", help="measure the payload trailer of a package's Function exports")
    args = ap.parse_args()
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
