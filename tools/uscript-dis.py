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
    python3 tools/uscript-dis.py --sizefield DUMP/.../System

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


RECORDS = os.path.join(ROOT, "inventory", "gnatives_records.csv")


def exec_name(symbol):
    """`intUObjectexecSwitch` -> `Switch`, `intAActorexecSleep` -> `Sleep`."""
    m = re.match(r"^int\w*?exec(\w+)$", symbol or "")
    return m.group(1) if m else ""


class Table:
    def __init__(self, path=TABLE):
        self.rows = {}
        for r in csv.DictReader(open(path)):
            self.rows[int(r["opcode"], 16)] = r
        self.records = {}
        if os.path.exists(RECORDS):
            for r in csv.DictReader(open(RECORDS)):
                nm = exec_name((r.get("record_name") or "").strip())
                if nm:
                    self.records[int(r["slot"])] = nm

    def high_native(self, op, byte):
        """The native a `HighNative` token names: `(op - 0x60) * 256 + operand byte` (0x61,b=0 -> 256)."""
        if byte is None or not 0x60 <= op <= 0x6F:
            return None
        return self.records.get((op - 0x60) * 256 + byte)

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
        node = {"offset": pos, "opcode": op, "name": name, "operands": b"", "children": [],
                "native": None, "end": pos}
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
        if 0x60 <= op <= 0x6F and node["operands"]:
            # The token is `HighNative<n>` and its operand byte is the low half of a native index >= 256, so the
            # image's own record table names the callee (docs/BYTECODE.md).
            node["native"] = table.high_native(op, node["operands"][0])
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
        if n.get("native"):
            name = "%s (%s)" % (name, n["native"])
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
        ("high native 1, byte 0: native 256, then a parameter list", bytes([0x61, 0x00, 0x16]), 1, 3),
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
    if table.records:
        nodes = parse(bytes([0x61, 0x00, 0x16]), table)
        got = nodes[0]["native"]
        want = table.records.get(256)
        if got != want:
            print("FAIL uscript-dis: 0x61 0x00 resolved to %r, the slot 256 record says %r" % (got, want))
            return 1
        hi = parse(bytes([0x70, 0x16]), table)[0]
        if hi["native"] is not None:
            print("FAIL uscript-dis: a direct-band token carries a high-native name")
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
    # `--shuffle` is a control, so it has to move every value and keep the pool it moves (a rotation that dropped
    # a type would make the control's collapse unreadable as evidence).
    decls = {"F": {"p": "pawn", "c": "controller"}, "G": {"a": "actor"}}
    rets = {"H": "sound"}
    loc_s, ret_s = shuffled_source(decls, rets)
    pool = sorted([v for d in loc_s.values() for v in d.values()] + list(ret_s.values()))
    if pool != sorted(["pawn", "controller", "actor", "sound"]):
        print("FAIL uscript-dis: the shuffle dropped or invented a declared type: %s" % pool)
        return 1
    if loc_s["F"]["p"] == "pawn" or ret_s["H"] == "sound":
        print("FAIL uscript-dis: the shuffle left a declared type on its own name")
        return 1
    # The class-level pass must not see a state's declarations: a state's `return true;` sits inside the class's
    # own ScriptText and would otherwise anchor the class-owned export of the same name (measured 2026-11-12:
    # 12 of the 13 unmatched payloads were exactly that).
    text = ("function bool IsFiring(){ return ((X != None)); }\r\n"
            "state NormalFire { function bool IsFiring(){ return true; } }\r\n")
    stripped = strip_states(text)
    if len(stripped) != len(text):
        print("FAIL uscript-dis: strip_states moved an offset (%d bytes out, %d in)" % (len(stripped), len(text)))
        return 1
    kinds = [first_statement_kind(stripped, m.end()) for m in FUNC_DECL.finditer(stripped)]
    if kinds != [None]:
        print("FAIL uscript-dis: the class-level pass read %r, expected only the class declaration" % (kinds,))
        return 1
    if [n for n, _b in state_blocks(text)] != ["normalfire"]:
        print("FAIL uscript-dis: state_blocks found %r" % (state_blocks(text),))
        return 1
    # A `native` declaration has no body, so the next function's `{` is not its anchor (two of the ten "no rule for
    # that return type" rows were this).
    pair = ("simulated native function rotator GetErrorVec(optional out vector out_Error);\r\n"
            "function bool Other(){ return true; }\r\n")
    match = list(FUNC_DECL.finditer(pair))
    if len(match) != 2 or has_body(pair, match[0].end()) or not has_body(pair, match[1].end()):
        print("FAIL uscript-dis: has_body read %r for a native/real pair"
              % ([(m.group(2), has_body(pair, m.end())) for m in match],))
        return 1
    print("ok   uscript-dis: %d rows, %d streams split, %d refusals honoured, shuffle rotates %d types,"
          " state spans blanked" % (len(table.rows), len(checks), len(refusals), len(pool)))
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


def scan_report(path, lo=8, hi=40):
    """Where the script starts, measured instead of assumed: every candidate start is walked.

    `start_report` tests the one offset a fixed 16-byte run predicts. This pass drops that assumption: for every
    payload whose six-value header ends on its own name index and whose epilogue sits at the trailer position it
    walks from **every** `name_end + delta` in `[lo, hi]` and keeps the deltas whose token walk lands exactly on
    the epilogue. Three numbers come out of one sweep:

    * the delta histogram -- a sharp peak is the header's length measured by content, while a flat profile would
      say no fixed run exists and the bytes after the name are part of the script;
    * how many payloads have exactly one clean delta, i.e. whose start is pinned rather than chosen;
    * for the peak delta, whether the dword in front of it equals the script's length -- the size-field question
      U13 is open on, asked at the start the content chose rather than at the one the reader assumed.

    The ceiling is the opcode table: a script that stops on an opcode the table does not name cannot walk from
    anywhere, so a payload refused at every delta is counted as `none` and is not evidence about the start.
    """
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    from ue2pkg import Package                                # noqa: PLC0415 - only for a real package

    files = ([os.path.join(path, f) for f in sorted(os.listdir(path)) if f.lower().endswith(".u")]
             if os.path.isdir(path) else [path])
    table = Table()
    epilogue = bytes([0x04, 0x0B, 0x47])
    deltas = list(range(lo, hi + 1))
    clean = collections.Counter()
    tried = collections.Counter()
    sized = collections.defaultdict(collections.Counter)
    smallest = collections.Counter()
    unique = choices = none = 0
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
            if n < 24:
                continue
            last, off = None, 0
            try:
                for _ in range(6):
                    last, off = read_index(pay, off)
            except IndexError:
                continue
            if last != e["name"]:
                continue
            if pay[n - 10:n - 7] == epilogue:
                end = n - 7
            elif pay[n - 12:n - 9] == epilogue:
                end = n - 9
            else:
                continue
            hits = []
            for delta in deltas:
                start = off + delta
                if start + 4 > end:
                    continue
                tried[delta] += 1
                try:
                    nodes = parse(pay[start:end], table)
                except (Refused, IndexError, struct.error):
                    continue
                if sum(x["end"] - x["offset"] for x in nodes) == end - start:
                    clean[delta] += 1
                    hits.append((delta, end - start))
            if not hits:
                none += 1
                continue
            if len(hits) == 1:
                unique += 1
            else:
                choices += 1
            smallest[min(d for d, _ in hits)] += 1
            for delta, length in hits:
                word = struct.unpack_from("<I", pay, off + delta - 4)[0]
                sized[delta]["equal" if word == length else ("larger" if word > length else "smaller")] += 1
    total = unique + choices + none
    if not total:
        print("no Function export decoded")
        return 1
    name = os.path.basename(path.rstrip(os.sep)) or path
    print("%s: %d Function payloads with a six-value header ending on their own name and a located epilogue"
          % (name, total))
    print("  a clean walk from name_end+delta to the epilogue, per delta (clean / tried):")
    for delta in deltas:
        if not tried[delta]:
            continue
        print("    +%-2d  %5d / %5d  %5.1f%%%s"
              % (delta, clean[delta], tried[delta], 100.0 * clean[delta] / tried[delta],
                 "   <- the fixed run start_report assumes" if delta == 16 else ""))
    if clean:
        peak, hits = clean.most_common(1)[0]
        print("  peak delta +%d with %d clean walks" % (peak, hits))
    print("  payloads whose start is pinned: %d exactly one clean delta, %d more than one, %d none"
          % (unique, choices, none))
    print("  smallest clean delta per payload: %s" % dict(sorted(smallest.items())))
    for delta, _ in clean.most_common(4):
        print("  +%-2d: the dword in front of that start against the script's length: %s"
              % (delta, dict(sized[delta].most_common())))
    return 0


# ==== new anchor block, spliced over FUNC_DECL .. argparser ====

# ==== anchor block v2: class- and state-owned declarations ====

FUNC_DECL = re.compile(r"^[ \t]*(?:(?:simulated|static|final|latent|native|protected|private|public)\s+)*"
                       r"(?:function|event)\s+(?:([\w<>\[\]]+)\s+)?(\w+)\s*\(",
                       re.IGNORECASE | re.MULTILINE)
STATE_DECL = re.compile(r"\bstate\s*(?:\([^)]*\))?\s*(\w*)\s*\{", re.IGNORECASE)

# The first statement's leading token where the source fixes it *and the emitter writes that token first*.
# `return <literal>;` qualifies: `EX_Return` is the statement's first byte. `if (...)` and `assert (...)` do not --
# this compiler evaluates the condition first and emits the conditional jump behind it, so an `if`-anchored payload
# would be searched for a `07` that is not its first byte at all (measured 2026-11-12: with the two patterns in the
# list, 597 of 716 anchored payloads whose expected byte was absent from the payload entirely were `07`-anchored).
FIRST_STATEMENT = [
    (re.compile(r"return\s*;"), "none"),
    (re.compile(r"return\s+true\s*;", re.IGNORECASE), "true"),
    (re.compile(r"return\s+false\s*;", re.IGNORECASE), "false"),
    (re.compile(r"return\s+0\s*;"), "zero"),
    (re.compile(r"return\s+1\s*;"), "one"),
]

# The literal's opcode is chosen by the *declared return type*, not by the source alone (measured 2026-11-12).
# A function declared with no return type writes `return <expr>;` as a bare `Return Nothing` -- the value is
# dropped, `04 0b` -- and that single rule accounts for 69 of the 104 payloads the type-blind anchor missed; a
# `float` function writes `04 1e` plus the float (11), a `byte` function `04 24` plus the byte (6), and only
# `bool`/`int` reach the `04 25`/`04 26`/`04 27`/`04 28` the first version assumed for every body. A return type
# with no measured rule is left unanchored rather than guessed, so the denominator stays honest.
RETURN_OPS = {
    ("", "none"): b"\x04\x0b", ("", "true"): b"\x04\x0b", ("", "false"): b"\x04\x0b",
    ("", "zero"): b"\x04\x0b", ("", "one"): b"\x04\x0b",
    ("bool", "none"): b"\x04\x0b", ("bool", "true"): b"\x04\x27", ("bool", "false"): b"\x04\x28",
    ("bool", "zero"): b"\x04\x25", ("bool", "one"): b"\x04\x26",
    ("int", "none"): b"\x04\x0b", ("int", "zero"): b"\x04\x25", ("int", "one"): b"\x04\x26",
    ("byte", "none"): b"\x04\x0b", ("byte", "zero"): b"\x04\x24\x00", ("byte", "one"): b"\x04\x24\x01",
    ("float", "none"): b"\x04\x0b", ("float", "zero"): b"\x04\x1e\x00\x00\x00\x00",
    ("float", "one"): b"\x04\x1e\x00\x00\x80\x3f",
}


def _body_start(text, at):
    """The first statement of the body whose `{` follows `at`, with whitespace and comments stripped."""
    i = text.find("{", at)
    if i < 0:
        return None
    i += 1
    while i < len(text):
        while i < len(text) and text[i] in " \t\r\n":
            i += 1
        if text.startswith("//", i):
            j = text.find("\n", i)
            i = len(text) if j < 0 else j + 1
        elif text.startswith("/*", i):
            j = text.find("*/", i)
            i = len(text) if j < 0 else j + 2
        else:
            break
    return text[i:]


def first_statement_kind(text, at):
    """Which `return` form the body that opens after `at` starts with, or None."""
    body = _body_start(text, at)
    if body is None:
        return None
    for pattern, kind in FIRST_STATEMENT:
        if pattern.match(body):
            return kind
    return None


def first_statement_ops(text, at, rettype=None):
    """The opcode bytes the shipped compiler writes first for the body that opens after `at`, or None.

    `rettype` is the declaration's own return type (`None` for a `function Foo()`), because the emitted literal
    depends on it: see `RETURN_OPS`.
    """
    kind = first_statement_kind(text, at)
    if kind is None:
        return None
    return RETURN_OPS.get(((rettype or "").strip().lower(), kind))


def has_body(text, at):
    """False when the declaration ends in `;` before any `{` — a `native` or forward declaration.

    `_body_start` takes the first `{` after the parameter list, and for a `native` declaration that is the *next*
    function's body. Measured 2026-11-12: `simulated native function rotator Weapon.GetErrorVec(...)` was paired
    with the following `return 0;`, so the payload was searched for a literal its body never had — two of the ten
    "no measured rule for that return type" rows were this, not a return-type question.
    """
    brace = text.find("{", at)
    semi = text.find(";", at)
    return brace >= 0 and (semi < 0 or brace < semi)


def state_blocks(text):
    """`(name, body)` for every `state <name> { ... }` block in a class's ScriptText, by brace matching.

    A `Function` export owned by a `State` export carries that state's body, and the class's own ScriptText is the
    only place the declaration exists -- but the offsets inside a returned `body` are already relative to it, so
    `first_statement_kind` must be handed `m.end()` and never `m.end() - offset` (an off-by-the-block-offset
    version read the wrong `{` and turned a 92-of-93 peak into a flat histogram over +16..+39).
    """
    return [(name, text[start:end]) for name, start, end in state_spans(text)]


def state_spans(text):
    """`(name, body_start, body_end)` for every `state <name> { ... }` block, by brace matching."""
    out = []
    for m in STATE_DECL.finditer(text):
        i = text.find("{", m.start())
        if i < 0:
            continue
        depth, j = 0, i
        while j < len(text):
            if text[j] == "{":
                depth += 1
            elif text[j] == "}":
                depth -= 1
                if depth == 0:
                    break
            j += 1
        out.append((m.group(1).lower(), i + 1, j))
    return out


def strip_states(text):
    """The class text with every `state <name> { ... }` block blanked to spaces, offsets unchanged.

    A state's functions are declared *inside* the class text, so a class-level pass over the whole text matches
    them too -- and the class-level key is then searched for a literal only the state's body contains. Measured
    2026-11-12 on `Engine.u`'s `Weapon.IsFiring`, whose class body is `return ((Instigator != None) && ...)`
    (not an anchor, so it is skipped) while a state's `IsFiring` opens `return true;`: the class-level export was
    anchored on the state's bytes and became one of the 13 unmatched payloads. Blanking keeps every offset valid,
    so `first_statement_kind` can still be handed `m.end()` on the result.
    """
    out = list(text)
    for _name, start, end in state_spans(text):
        for k in range(start, min(end + 1, len(out))):
            if out[k] not in "\r\n":
                out[k] = " "
    return "".join(out)


def anchor_report(path, lo=8, hi=40, name_rows=0):
    """Where the script starts, anchored by the source rather than by a walk.

    The walk alone cannot answer it: `--start-scan` finds a clean walk from **every** delta between +11 and +16
    on 99% of payloads, because most bytes decode as one-byte opcodes and a garbage stream still consumes the
    payload exactly. So the anchor is content. Each `Function` export's class ships its `ScriptText`, and a body
    that opens with `return true;` (`return false;`, `return;`, `return 0/1;`) fixes its first opcode bytes -- but
    only together with the declaration's own return type, which chooses the literal (`RETURN_OPS`), and only for
    the class that *owns* the export: a name declared once in a package is not an anchor for the same name in
    another class. Both rules are load-bearing. Keying by name alone paired `Engine.u`'s one `Destroyed`
    declaration with every same-named export in the package, and left 658 anchored payloads of which 225 sat at
    +16.

    A `Function` export owned by a `State` export is keyed by (class, state, function) instead: a state's body is
    its own, so the class-level declaration is not evidence for it, but the class's ScriptText does carry the
    `state <name> { ... }` block and the declaration is there. Over the 18 packages that adds 93 anchored payloads
    of which **92** sit at +16 and all 92 walk; with the 441 class-owned ones the total is **534 anchored, 514 at
    +16**.

    A fixed header length is a peak at one delta against a flat control everywhere else, and the same bytes
    searched from offset 0 should never hit. A body a class declares twice is dropped rather than guessed; the
    anchored count is printed so the denominator is visible. `--lists N` names each residue row instead of only
    counting it -- the expected bytes, what the payload carries at +16 instead, and whether those bytes walk --
    because the residue is where the next question is and its names are only one read away. A declaration with **no
    body** (`native`, or a `function Foo();` that a state implements) is not an anchor either: the body finder would
    take the *next* function's `{`, and its own payload is the empty script, so those payloads are checked as a
    prediction (82 of 82 empty) rather than counted as misses.
    """
    import importlib.util                                  # noqa: PLC0415 - only for the shipped source

    spec = importlib.util.spec_from_file_location("pkg_script_text",
                                                  os.path.join(ROOT, "tools", "pkg-script-text.py"))
    pst = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(pst)
    spec2 = importlib.util.spec_from_file_location("ue2pkg_scan", os.path.join(ROOT, "tools", "ue2pkg.py"))
    ue2 = importlib.util.module_from_spec(spec2)
    spec2.loader.exec_module(ue2)

    files = ([os.path.join(path, f) for f in sorted(os.listdir(path)) if f.lower().endswith(".u")]
             if os.path.isdir(path) else [path])
    table = Table()
    epilogue = bytes([0x04, 0x0B, 0x47])
    deltas = list(range(lo, hi + 1))
    hit = collections.Counter()
    walked = collections.Counter()
    hit_state = collections.Counter()
    walked_state = collections.Counter()
    opcodes = collections.Counter()
    unmatched_by_op = collections.Counter()
    unmatched_shape = collections.Counter()
    unrouted = collections.Counter()
    sized = collections.Counter()
    anchored = anchored_state = located = at_zero = unmatched = unmatched_state = twice = 0
    no_body = unrouted_empty = no_body_empty = 0
    # Named residue: a count says a population exists, a name says what to read next. Each row is one payload the
    # anchor could not place, with the bytes its own source fixed and the bytes it actually carries at +16.
    residue_rows = []
    twice_rows = []
    unrouted_rows = []
    no_body_rows = []
    no_body_scripted = []
    unrouted_empty_rows = []
    unrouted_scripted_rows = []
    unroutable = {}
    no_body_keys = {}
    for one in files:
        pkg = ue2.Package(one)
        pkg.read_names()
        pkg.read_imports()
        pkg.read_exports()
        names = [pkg.name(e["name"]).lower() for e in pkg.exports]
        # A Function export names its class only through its `Outer`, and a state-owned one names a State: a
        # class-scoped key is what keeps one class's `return false;` from anchoring every same-named payload.
        decls = collections.Counter()
        expected = {}
        decl_info = {}
        bodied_keys = set()
        for ci, cls in enumerate(pkg.exports):
            if pkg.class_name_of(cls) != "Class":
                continue
            text = pst.script_text(pkg, cls)
            if not text:
                continue
            cname = names[ci]
            blocks = [(cname, None, strip_states(text))] + [(cname, sname, body)
                                                           for sname, body in state_blocks(text)]
            for owner_class, sname, body in blocks:
                for m in FUNC_DECL.finditer(body):
                    key = (owner_class, sname, (m.group(2) or "").lower())
                    rtype = (m.group(1) or "").strip().lower()
                    hb = has_body(body, m.end())
                    if hb:
                        bodied_keys.add(key)
                    kind = first_statement_kind(body, m.end())
                    if kind is None:
                        continue
                    if not hb:
                        no_body += 1
                        no_body_rows.append((os.path.basename(one), key, rtype, kind))
                        no_body_keys.setdefault(key, (rtype, kind))
                        continue
                    want = RETURN_OPS.get((rtype, kind))
                    if want is None:
                        unrouted["%s/%s" % (rtype or "void", kind)] += 1
                        unrouted_rows.append((os.path.basename(one), owner_class, sname,
                                              m.group(2), rtype, kind))
                        unroutable.setdefault(key, (rtype, kind))
                        continue
                    decls[key] += 1
                    expected.setdefault(key, want)
                    decl_info.setdefault(key, (rtype, kind))
        for key, count in list(decls.items()):
            if count != 1:
                expected.pop(key, None)
                twice += 1
                twice_rows.append((os.path.basename(one), key, count, decl_info.get(key, ("", ""))))
                unroutable.pop(key, None)
        # A key declared both as a stub and with a body is not ambiguous: only the bodied declaration could have
        # produced a script. Two functions do this -- `Weapon.GetErrorVec` and `Weapon.GetFireStart`, each declared
        # `native` and again with a body -- and calling them bodiless produced a "contradiction" that was the key's,
        # not the rule's.
        if bodied_keys & set(no_body_keys):
            no_body_keys = {k: v for k, v in no_body_keys.items() if k not in bodied_keys}
            no_body_rows = [r for r in no_body_rows if r[1] in no_body_keys]
            no_body = len(no_body_rows)
        for i, e in enumerate(pkg.exports):
            if pkg.class_name_of(e) != "Function":
                continue
            outer = e["outer"]
            if outer < 1 or outer > len(pkg.exports):
                continue
            owner = pkg.exports[outer - 1]
            ocls = pkg.class_name_of(owner)
            if ocls == "State":
                so = owner["outer"]
                if so < 1 or so > len(pkg.exports):
                    continue
                key = (names[so - 1], names[outer - 1], names[i])
                is_state = True
            else:
                key = (names[outer - 1], None, names[i])
                is_state = False
            want = expected.get(key)
            routable = want is not None
            if not routable and key not in unroutable and key not in no_body_keys:
                continue
            pay = pkg.data[e["offset"]:e["offset"] + e["size"]]
            n = len(pay)
            if n < 24:
                continue
            last, off = None, 0
            try:
                for _ in range(6):
                    last, off = read_index(pay, off)
            except IndexError:
                continue
            if last != e["name"]:
                continue
            if pay[n - 10:n - 7] == epilogue:
                end = n - 7
            elif pay[n - 12:n - 9] == epilogue:
                end = n - 9
            else:
                continue
            if key in no_body_keys:
                # A declaration with no body (`native`, or a `function Foo();` a state implements) must have been
                # compiled to an empty script. That is a prediction, so count both sides rather than excluding the
                # population: 84 of 84 hold it, which is what says the four former "empty body" residues were the
                # parser's own mispairing and not a compiler behaviour.
                if pay[off + 16:end] == epilogue:
                    no_body_empty += 1
                else:
                    no_body_scripted.append((os.path.basename(one), key, end - (off + 16),
                                             pay[off + 16:off + 22].hex(" ")))
                continue
            if not routable:
                # A declaration whose return type has no measured rule cannot be anchored, but its payload can still
                # answer one question: was the body compiled empty? Eight of the ten do exactly that, so no literal
                # encoding was ever exercised for them and the missing rule does not apply.
                if pay[off + 16:end] == epilogue:
                    unrouted_empty += 1
                    unrouted_empty_rows.append((os.path.basename(one), key, unroutable[key]))
                else:
                    # A rule is missing for this return type and the payload does carry a script, so its first
                    # bytes are the literal the compiler emitted: print them instead of leaving the rule to guess.
                    unrouted_scripted_rows.append((os.path.basename(one), key, unroutable[key],
                                                   end - (off + 16), pay[off + 16:off + 24].hex(" ")))
                continue
            located += 1
            anchored += 1
            opcodes[want.hex()] += 1
            if is_state:
                anchored_state += 1
            if pay[:len(want)] == want:
                at_zero += 1
            found = False
            for delta in deltas:
                pos = off + delta
                if pos + len(want) > end:
                    continue
                if pay[pos:pos + len(want)] != want:
                    continue
                found = True
                hit[delta] += 1
                if is_state:
                    hit_state[delta] += 1
                try:
                    nodes = parse(pay[pos:end], table)
                except (Refused, IndexError, struct.error):
                    continue
                if sum(x["end"] - x["offset"] for x in nodes) == end - pos:
                    walked[delta] += 1
                    if is_state:
                        walked_state[delta] += 1
            if not found:
                unmatched += 1
                unmatched_by_op[want.hex()] += 1
                unmatched_shape[pay[off + 16:end][:2].hex()] += 1
                at16 = pay[off + 16:end]
                walk16 = "no-script"
                if at16:
                    try:
                        nodes = parse(at16, table)
                        used = sum(x["end"] - x["offset"] for x in nodes)
                        walk16 = ("walks %d tok / %d bytes" % (len(nodes), used) if used == len(at16)
                                  else "stops after %d of %d bytes" % (used, len(at16)))
                    except (Refused, IndexError, struct.error) as exc:
                        walk16 = "refused: %s" % exc
                residue_rows.append((os.path.basename(one), key, want.hex(" "), at16[:6].hex(" "),
                                     walk16, struct.unpack_from("<I", pay, off + 12)[0], len(at16),
                                     decl_info.get(key, ("", ""))))
                if is_state:
                    unmatched_state += 1
            elif off + 16 + len(want) <= end and pay[off + 16:off + 16 + len(want)] == want:
                # Only the rows whose start is the claimed +16 say anything about the dword at +12: a match at
                # +18 or +21 is a second occurrence of the same bytes, not the start.
                word = struct.unpack_from("<I", pay, off + 12)[0]
                sized[word - (end - (off + 16))] += 1
    name = os.path.basename(path.rstrip(os.sep)) or path
    print("%s: %d anchored payloads (ScriptText fixes the first opcode) of %d with a located epilogue"
          % (name, anchored, located))
    print("  anchored opcodes: %s" % dict(opcodes.most_common()))
    print("  by owner: %d class function(s), %d owned by a State"
          % (anchored - anchored_state, anchored_state))
    print("  not anchored: %d declaration(s) made twice under one key, %d whose return type has no measured rule"
          " (%d of those payloads compile empty, so no literal was exercised; %s), %d with no body at all"
          % (twice, sum(unrouted.values()), unrouted_empty, dict(unrouted.most_common(4)), no_body))
    if no_body:
        print("  a declaration with no body must compile empty: %d such payloads, %d empty, %d carrying a script"
              % (no_body_empty + len(no_body_scripted), no_body_empty, len(no_body_scripted)))
    print("  a match at name_end+delta (match / at+distance, clean walk from there):")
    for delta in deltas:
        if not hit[delta]:
            continue
        print("    +%-2d  %4d / %4d  %4d walk%s"
              % (delta, hit[delta], anchored, walked[delta],
                 "   <- the fixed run start_report assumes" if delta == 16 else ""))
    if hit:
        peak, most = hit.most_common(1)[0]
        print("  peak delta +%d with %d of %d anchored payloads and %d clean walks"
              % (peak, most, anchored, walked[peak]))
    if anchored_state:
        print("  state-owned only: %d anchored, %d at +16 (%d walk), no match at any delta: %d"
              % (anchored_state, hit_state[16], walked_state[16], unmatched_state))
    print("  no delta matched: %d; by the script's own first two bytes at +16: %s;"
          " the same bytes at payload offset 0: %d"
          % (unmatched, dict(unmatched_shape.most_common()), at_zero))
    if unmatched_by_op:
        print("    of those, by anchored opcode: %s" % dict(unmatched_by_op.most_common()))
    # The dword at name_end+12 against the script's own length (name_end+16 .. the epilogue). Zero means the
    # field is that length; a positive residue is what the field counts beyond it.
    if sized:
        exact = sized.get(0, 0)
        print("  dword at name_end+12 minus the script's length, over the payloads whose start is +16: %s"
              % dict(sorted(sized.items())[:8]))
        print("    equal in %d of %d (%.1f%%), and never negative: %s"
              % (exact, sum(sized.values()), 100.0 * exact / sum(sized.values()),
                 "yes" if min(sorted(sized)) >= 0 else "no (min %d)" % min(sorted(sized))))
    # Named residue. A count of 13 is not actionable; these rows are, and each one reads as a question about that
    # single function: what its own source says the first byte is, what the payload carries instead, and whether
    # the bytes at +16 walk as a script at all.
    if name_rows and residue_rows:
        empties = sum(1 for r in residue_rows if r[5] == 3 and r[3] == "04 0b 47")
        print("  named residue (%d payload(s), up to %d named, %d an empty compiled body):"
              % (len(residue_rows), name_rows, empties))
        for pkgname, key, want, at16, walk16, word, slen, (rtype, kind) in residue_rows[:name_rows]:
            print("    %-12s %s%s.%s  declared %s/%s  want %s  at+16 %s  script %d bytes"
                  "  dword@+12 %+d  %s%s"
                  % (pkgname, key[0], ("[state %s]" % key[1]) if key[1] else "", key[2],
                     rtype or "void", kind, want, at16 or "-", slen, word - slen, walk16,
                     "  <- an empty body, not a start read late" if (slen == 3 and at16 == "04 0b 47") else ""))
    if name_rows and unrouted_empty_rows:
        print("  unanchored but compiled empty, so no literal was exercised (%d):" % len(unrouted_empty_rows))
        for pkgname, key, (rtype, kind) in unrouted_empty_rows[:name_rows]:
            print("    %-12s %s%s.%s  declared %s/%s"
                  % (pkgname, key[0], ("[state %s]" % key[1]) if key[1] else "", key[2],
                     rtype or "void", kind))
    if name_rows and no_body_rows:
        print("  no body to anchor on a `native`, a state-implemented or a forward declaration (%d):"
              % len(no_body_rows))
        for pkgname, key, rtype, kind in no_body_rows[:name_rows]:
            print("    %-12s %s%s.%s  %s/%s"
                  % (pkgname, key[0], ("[state %s]" % key[1]) if key[1] else "", key[2],
                     rtype or "void", kind))
    if no_body_scripted:
        print("  CONTRADICTION, a bodiless declaration with a script (%d):" % len(no_body_scripted))
        for pkgname, key, length, head in no_body_scripted[:max(name_rows, 10)]:
            print("    %-12s %s%s.%s  script %d bytes  at+16 %s"
                  % (pkgname, key[0], ("[state %s]" % key[1]) if key[1] else "", key[2], length, head))
    if name_rows and twice_rows:
        print("  dropped, declared twice under one key (%d):" % len(twice_rows))
        for pkgname, key, count, (rtype, kind) in twice_rows[:name_rows]:
            print("    %-12s %s%s.%s  x%d  %s/%s"
                  % (pkgname, key[0], ("[state %s]" % key[1]) if key[1] else "", key[2], count,
                     rtype or "void", kind))
    if name_rows and unrouted_rows:
        print("  unanchored, no measured rule for that return type (%d):" % len(unrouted_rows))
        for pkgname, cname, sname, fname, rtype, kind in unrouted_rows[:name_rows]:
            print("    %-12s %s%s.%s  %s/%s"
                  % (pkgname, cname, ("[state %s]" % sname) if sname else "", fname, rtype or "void", kind))
    print("  unanchored payloads carrying a script (each one is a literal rule to measure): %d"
          % len(unrouted_scripted_rows))
    if name_rows and unrouted_scripted_rows:
        for pkgname, key, (rtype, kind), slen, at16 in unrouted_scripted_rows[:name_rows]:
            print("    %-12s %s%s.%s  %s/%s  script %d bytes  at+16 %s"
                  % (pkgname, key[0], ("[state %s]" % key[1]) if key[1] else "", key[2],
                     rtype or "void", kind, slen, at16))
    return 0


def _spans_with_op(data, table):
    """`(start, end, kind, opcode)` for every token's own bytes, following the table's grammar.

    kind is `opcode`, `index` or `fixed`, and the fourth field is the opcode of the token that owns the span (for a
    token's own `opcode` span, itself). This is a position-aware copy of `parse`'s walk (that one builds a tree and
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
        spans.append((pos, pos + 1, "opcode", op))
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
                spans.append((q, q + 2, "case", op))
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
                spans.append((start, q, "index", op))
            elif item.startswith("bytes:"):
                n = int(item.split(":", 1)[1])
                if q + n > len(data):
                    raise Refused("%#x: opcode %02x wants %d operand bytes, %d left" % (pos, op, n, len(data) - q))
                spans.append((q, q + n, "fixed", op))
                q += n
            elif item == "str":
                end = data.find(b"\0", q)
                if end < 0:
                    raise Refused("%#x: string operand of %02x is not NUL-terminated" % (pos, op))
                spans.append((q, end + 1, "fixed", op))
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
                spans.append((start, q, "fixed", op))
            elif item == "labeltable":
                start = q
                while True:
                    if q + 8 > len(data):
                        raise Refused("%#x: label table of %02x is not zero-terminated" % (pos, op))
                    entry = struct.unpack_from("<I", data, q)[0]
                    q += 8
                    if entry == 0:
                        break
                spans.append((start, q, "fixed", op))
            else:
                raise Refused("%#x: unknown pattern item %r in the table" % (pos, item))
        return q, op

    pos = 0
    while pos < len(data):
        pos, _op = token(pos)
    spans.sort()
    return spans


def operand_spans(data, table):
    """`(start, end, kind)` for every token's own bytes -- `_spans_with_op` without the owning opcode."""
    return [(a, b, k) for a, b, k, _op in _spans_with_op(data, table)]


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


def _payload_trees(path, table, stats):
    """Yield the parsed token tree of every `Function` payload whose script walks clean end to end.

    `stats` is updated in place (`seen`, `walked`), because both censuses quote those two counts.
    """
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    from ue2pkg import Package                                # noqa: PLC0415 - only for a real package

    files = ([os.path.join(path, f) for f in sorted(os.listdir(path)) if f.lower().endswith(".u")]
             if os.path.isdir(path) else [path])
    epilogue = bytes([0x04, 0x0B, 0x47])
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
            stats["seen"] += 1
            start = off + 16
            if start >= end:
                continue
            try:
                nodes = dis_parse(pay[start:end], table)
            except Refused:
                continue
            if sum(x["end"] - x["offset"] for x in nodes) != end - start:
                continue
            stats["walked"] += 1
            yield nodes


def highnative_report(path, table):
    """Which natives the `HighNative` band calls, over every script that walks clean end to end.

    A token in `0x60..0x6f` is `HighNative<n>` and carries one byte; the index it names is
    `(opcode - 0x60) * 256 + byte`, so `0x61 0x1c` is native 284 and the image's record table gives its name.
    Unresolved rows are counted, not dropped: a native index with no record is a finding.
    """
    declared = {}
    nat = os.path.join(ROOT, "inventory", "natives.csv")
    if os.path.exists(nat):
        for r in csv.DictReader(open(nat)):
            tok = (r.get("native_token") or "").strip()
            if tok:
                declared.setdefault(int(tok), []).append("%s.%s" % (r.get("class", "?"), r.get("function", "?")))
    stats = {"seen": 0, "walked": 0}
    counts = collections.Counter()
    payloads = collections.Counter()
    for nodes in _payload_trees(path, table, stats):
        here = set()

        def visit(items):
            for node in items:
                op = node["opcode"]
                if 0x60 <= op <= 0x6F and node["operands"]:
                    key = (op, node["operands"][0])
                    counts[key] += 1
                    here.add(key)
                visit(node["children"])
        visit(nodes)
        for key in here:
            payloads[key] += 1
    print("%d scripts with an epilogue, %d walk clean end to end" % (stats["seen"], stats["walked"]))
    print("%-6s %-7s %7s %-28s %-28s %10s %9s"
          % ("op", "byte", "native", "record name", "declared", "tokens", "payloads"))
    rows = []
    for (op, byte), _n in sorted(counts.items(), key=lambda kv: (-kv[1], kv[0])):
        index = (op - 0x60) * 256 + byte
        rows.append((op, byte, index, table.records.get(index, ""), counts[(op, byte)], payloads[(op, byte)]))
    for op, byte, index, name, tok, pay in rows[:40]:
        print("0x%02x   0x%02x    %7d %-28s %-28s %10d %9d"
              % (op, byte, index, name or "-", ", ".join(declared.get(index, [])) or "-", tok, pay))
    named = sum(tok for _o, _b, _i, name, tok, _p in rows if name)
    total = sum(tok for _o, _b, _i, _n_, tok, _p in rows)
    unresolved = [(i, tok) for _o, _b, i, name, tok, _p in rows if not name]
    print("%d distinct (opcode, byte) pairs name %d natives; %d token(s) of %d resolve to a record name"
          % (len(rows), len({i for _o, _b, i, name, _t, _p in rows if name}), named, total))
    if unresolved:
        print("unresolved native indices: %s" % ", ".join("%d (%d token(s))" % u for u in unresolved[:8]))
    named_idx = {i for _o, _b, i, name, _t, _p in rows if name}
    confirmed = [i for i in sorted(named_idx)
                 if any(d.split(".", 1)[1] == table.records.get(i) for d in declared.get(i, []))]
    differ = [i for i in sorted(named_idx) if declared.get(i) and i not in confirmed]
    absent = [i for i in sorted(named_idx) if not declared.get(i)]
    print("%d of %d named indices carry a shipped `native(<index>)` declaration; %d name it identically, "
          "%d differ, %d have no declaration" % (len(confirmed) + len(differ), len(named_idx), len(confirmed),
                                                 len(differ), len(absent)))
    for i in differ[:6]:
        print("   native(%d): record `%s`, script declares `%s`"
              % (i, table.records.get(i), ", ".join(declared[i])))
    return 0


def usage_report(path, table, out=None):
    """Which opcodes the shipped scripts actually use, over every payload whose script walks clean.

    A census, not a guess: an opcode no script uses cannot be checked against anything, and one that appears in
    thousands of payloads is worth naming first.
    """
    counts = collections.Counter()
    payloads = collections.Counter()
    stats = {"seen": 0, "walked": 0}
    for nodes in _payload_trees(path, table, stats):
        here = set()

        def visit(items):
            for node in items:
                counts[node["opcode"]] += 1
                here.add(node["opcode"])
                visit(node["children"])
        visit(nodes)
        for op in here:
            payloads[op] += 1
    walked, seen = stats["walked"], stats["seen"]
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
# The tokens that leave an object on the evaluation stack, so one of them can be the expression a
# by-name call dispatches on. The receiver is not an operand: the emitter evaluates the object before
# the call token, so the site carries it as the expression in front (docs/BYTECODE.md).
OBJECT_TOKENS = {"InstanceVariable", "LocalVariable", "DefaultVariable", "ObjectConst", "Context",
                 "ClassContext", "DynamicCast", "MetaCast", "StructMember", "NativeParm", "NoObject",
                 # An array element is an object expression too: `A[i]` leaves the element.
                 "ArrayElement", "DynamicArrayElement",
                 # A call leaves its result, so a call in front of a call is a receiver as well: `GetOwner().Foo()`
                 # dispatches on the callee's declared return type (`source_returns`).
                 "FinalFunction", "VirtualFunction", "GlobalFunction"}


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


def _tree(nodes, parent=None, idx=None):
    """Every node, with its index in its own sibling list.

    A by-name call carries no receiver operand: the emitter evaluates the object *before* the call token, so the
    expression the VM would have on its stack at the call is the one in front of it in the same list. Keeping the
    sibling order is what makes that expression findable.
    """
    for i, n in enumerate(nodes):
        yield n, i, nodes, parent
        for sub in _tree(n["children"], n, i):
            yield sub


# A tagged type carries the declaration's modifiers *inside* the array's angle brackets:
# `array<export editinline ScriptEvent>`, `array<editconst editinline Stream>`, and a struct member's type carries
# them in front of the name (`export editinline Order`). They are the same words the `var` parse accepts, and reading
# them as part of the name rejected every one of those element types: 35 array-element call sites read "the array's
# type names no class" for a class that is named right there.
_TYPE_MODIFIERS = ("const", "config", "globalconfig", "private", "protected", "public", "transient", "native",
                   "editconst", "editconstarray", "editinline", "editinlinenotify", "export", "noexport", "init",
                   "travel", "input", "duplicatetransient", "deprecated", "atomic", "immutable")


def _class_of_type(friendly):
    """The class a property's declared type names, or "" when the type is not one."""
    if not friendly:
        return ""
    friendly = friendly.strip()
    while True:
        low = friendly.lower()
        for mod in _TYPE_MODIFIERS:
            if low.startswith(mod + " "):
                friendly = friendly[len(mod) + 1:].lstrip()
                break
        else:
            break
    # UnrealScript spells these lowercase in its own source (`local class<LocalMessage> M;`) and the tagged
    # property stream capitalises `Class<>`; a reader that knew only one spelling read the other as a class name.
    for prefix in ("class<", "array<"):
        if friendly.lower().startswith(prefix) and friendly.endswith(">"):
            return _class_of_type(friendly[6:-1].strip())
    if "." in friendly:
        return _class_of_type(friendly.rsplit(".", 1)[-1])
    if friendly in ("int", "float", "bool", "byte", "string", "name", "object", "pointer"):
        return ""
    # A property's declared type can carry a struct's inlining flags (`export editinline Order`,
    # `editinlinenotify GUIComponent`), which is not a class name at all: a class is one identifier, so a space or an
    # angle bracket left after the wrappers above says this is not one.
    if " " in friendly or "<" in friendly or ">" in friendly:
        return ""
    return friendly


def _typed(cls, structs, what, fallback):
    """(the class one declared type names, why it names none).

    A tag can name a struct as readily as a class -- `array<PlaylistParser>`, `array<export editinline direction>` --
    and after the modifiers are stripped the two are the same shape, one identifier. What separates them is the
    corpus: `source_structs` already holds every struct's member list, so a name that is a declared struct is
    reported as one instead of being handed on as a class no table has. `what` names the read and `fallback` is the
    reason the branch already had for a type that is neither; an empty fallback means "nothing to say", which lets a
    caller fall through to its next lookup exactly as before.
    """
    if not cls:
        return "", fallback
    if structs is not None and structs(cls) is not None:
        return "", "%s: declared as a struct (`%s`), not a class" % (what, cls)
    return cls, ""


_SCRIPT_TEXT = None

# `--calls --shuffle`: rotate each class's declared types before the walk, as a control on the placements.
_SHUFFLE = False


def _script_text(pkg, export, index):
    """The class's own `ScriptText` (the source it ships), or None. `pkg-script-text` is loaded lazily once."""
    global _SCRIPT_TEXT
    if _SCRIPT_TEXT is None:
        import importlib.util                                 # noqa: PLC0415 - a hyphenated sibling module
        path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "pkg-script-text.py")
        spec = importlib.util.spec_from_file_location("pkg_script_text", path)
        _SCRIPT_TEXT = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_SCRIPT_TEXT)
    return _SCRIPT_TEXT.script_text(pkg, dict(export, index=index))


_SRC_COMMENT = re.compile(r"/\*.*?\*/|//[^\n]*", re.S)
_SRC_FUNC = re.compile(r"^[ \t]*(?:(?:exec|protected|private|public|simulated|static|final|latent|iterator|"
                       r"native|singular)\s+)*"
                       r"(?:function|event)\s+(?:([\w<>\[\].]+(?:\s*>)*)\s+)?(\w+)\s*\(([^)]*)\)",
                       re.I | re.M)
_SRC_LOCAL = re.compile(r"^\s*local\s+(?:const\s+)?([\w<>\[\].]+(?:\s*>)*)\s+(.+?);", re.I)
_SRC_PARAM = re.compile(r"^\s*(?:(?:out|optional|coerce|const)\s+)*([\w<>\[\].]+(?:\s*>)*)\s+(\w+)\s*(?:=.*)?$",
                        re.I)
# A struct's members are `var` lines, not `local` ones, and may carry a declaration category (`var() int X;`).
# `struct` is matched with spaces and tabs only: `\s` would let the modifier run swallow the line break into the
# opening brace and take the first member for the struct's own name.
# A struct member's type can sit behind as many modifiers as a property's (`var config UIWidget Face;`,
# `var() editconst array<Foo> List;`), and reading the first token as the type typed 23 members literally `config`.
_SRC_VAR = re.compile(r"^\s*var(?:\s*\([^)]*\))?\s+(?:(?:const|config|globalconfig|private|protected|public|"
                      r"transient|native|editconst|editinline|editinlinenotify|export|noexport|init|travel|input|"
                      r"duplicatetransient|deprecated)\s+)*([\w<>\[\].]+(?:\s*>)*)\s+(.+?);", re.I)
_SRC_STRUCT = re.compile(r"^[ \t]*struct[ \t]+(?:(?:native|transient|atomic|immutable|export|private|"
                         r"protected|public)[ \t]+)*(\w+)", re.I | re.M)
# The shipped text carries preprocessor conditionals (`#ifdefXBOX ... #else ... #endif`), and the xbox build
# declares a function differently inside one. A directive *line* between a declaration and its `{` stopped the
# body scan: `Engine.InteractionMaster.AddInteraction` is declared in an `#ifdefXBOX` branch and in an
# `#ifndefXBOX` one, its body follows the `#endif`, and its two locals were the only untyped local left in the
# call census. Directives carry no braces, so dropping the lines leaves the spans intact.
_SRC_PREPROC = re.compile(r"^[ \t]*#[A-Za-z_]\w*[^\r\n]*", re.M)


def _src_text(text):
    """A ScriptText with comments and preprocessor directive lines removed (no offsets survive either)."""
    return _SRC_PREPROC.sub("", _SRC_COMMENT.sub("", text))


def _directive_gaps(text):
    """The declarations the parse would lose to a directive line in front of their `{`, by name.

    Counted on text with comments stripped and directives kept, i.e. exactly what the scan used to see, so the
    rows are the population the `_src_text` strip repairs -- not an estimate of it.
    """
    masked = _SRC_COMMENT.sub("", text or "")
    gaps = []
    for m in _SRC_FUNC.finditer(masked):
        j = m.end()
        while j < len(masked) and masked[j] in " \t\r\n":
            j += 1
        if j < len(masked) and masked[j] == "#":
            gaps.append(m.group(2))
    return gaps


def source_returns(text):
    """{function: declared return type} from a class's ScriptText, or {} when it ships none.

    `_SRC_FUNC`'s first group is the token between `function` and the name, i.e. the return type. A call in
    front of another call leaves that type on the stack, so `GetOwner().Foo()` dispatches on the class the
    callee is *declared* to return -- which the bytecode does not carry and the source states outright. A
    function the class declares twice with different types is dropped rather than guessed; a function that
    returns nothing is kept as "" so a caller can say why its result is not an object.
    """
    if not text:
        return {}
    text = _src_text(text)
    out = collections.defaultdict(set)
    for m in _SRC_FUNC.finditer(text):
        out[m.group(2)].add((m.group(1) or "").strip().lower())
    return {fn: (next(iter(t)) if len(t) == 1 else None) for fn, t in out.items()}


def _folded(table, name):
    """The value a declared name has, matched without case -- the compiler folds case and the source does not.

    UnrealScript matches identifiers case-insensitively while the source spells a class `pawn` and the export table
    `Pawn`, so a declaration read from the text has to be looked up the same way the class index is (the same
    defect that made `class<LocalMessage>` and `pawn` type nothing). `None` is a real value in these tables (a
    name declared twice with two types), so the caller separates "absent" from "present with no type" itself.
    """
    if not table or name in table:
        return table.get(name)
    low = name.lower()
    for k, v in table.items():
        if k.lower() == low:
            return v
    return None


def _operand_name(expr, export_at, import_at):
    """The export's or import's own name for a variable token's operand, or "" -- never the call's target."""
    if not expr or not expr.get("operands"):
        return ""
    value, _e = read_index(expr["operands"], 0)
    if value in export_at:
        return export_at[value][0]
    if value < 0 and -value in import_at:
        return import_at[-value][0]
    return ""


def source_declarations(text):
    """{function: {variable: declared type}} from a class's ScriptText, or {} when it ships none.

    A local *is* an export: the bytecode's `LocalVariable` operand points at a property record whose outer is the
    function that declares it, so the record's name is a fact of the export table. Its *type* is not a field the
    corpus can read -- no payload offset of the ObjectProperty records names the declared class for more than 30
    of 281 (measured), so the record is a handle and not a declaration. The class ships the source it was compiled
    from, and `local <Type> <Name>;` plus the parameter list bind exactly that name. A name the class declares
    with two different types is dropped rather than guessed; a bodyless (`native`) declaration contributes its
    parameters and no locals.
    """
    if not text:
        return {}
    text = _src_text(text)
    out = collections.defaultdict(lambda: collections.defaultdict(set))
    for m in _SRC_FUNC.finditer(text):
        fn, params = m.group(2), m.group(3)
        for p in params.split(","):
            pm = _SRC_PARAM.match(p.strip())
            if pm:
                out[fn][pm.group(2)].add(pm.group(1))
        j = m.end()
        while j < len(text) and text[j] in " \t\r\n":
            j += 1
        if j >= len(text) or text[j] != "{":
            continue
        depth, k = 0, j
        while k < len(text):
            if text[k] == "{":
                depth += 1
            elif text[k] == "}":
                depth -= 1
                if depth == 0:
                    break
            k += 1
        for line in text[j:k].splitlines():
            lm = _SRC_LOCAL.match(line)
            if lm:
                for nm in lm.group(2).split(","):
                    out[fn][nm.strip()].add(lm.group(1))
    return {fn: {n: (next(iter(t)) if len(t) == 1 else None) for n, t in decls.items()}
            for fn, decls in out.items()}


def source_structs(text):
    """{struct: {member: declared type}} from a class's ScriptText, or {} when it declares none.

    A struct's members are `var` lines inside the struct's own block in the class text, and no record carries
    their declared class: `SquadItemStruct.FaceWidget` is an export whose class is `ObjectProperty` and whose
    Outer is the struct -- exactly as a local's record names it and not its type. So the block is the same
    file-level parse the local pass already does, one brace span deeper, and a member declared inside a *nested*
    struct block belongs to that struct, not to the one around it. A member the text declares with two types is
    dropped rather than guessed (`None`), the same rule a local gets.
    """
    if not text:
        return {}
    text = _src_text(text)
    out = collections.defaultdict(lambda: collections.defaultdict(set))
    for m in _SRC_STRUCT.finditer(text):
        j = text.find("{", m.end())
        if j < 0:
            continue
        depth, k = 0, j
        while k < len(text):
            if text[k] == "{":
                depth += 1
            elif text[k] == "}":
                depth -= 1
                if depth == 0:
                    break
            k += 1
        rel = 0
        for line in text[j:k].splitlines():
            vm = _SRC_VAR.match(line) if rel <= 1 else None
            rel += line.count("{") - line.count("}")
            if not vm:
                continue
            for nm in vm.group(2).split(","):
                nm = nm.strip()
                if nm:
                    out[m.group(1)][nm].add(vm.group(1))
    return {s: {n: (next(iter(t)) if len(t) == 1 else None) for n, t in ms.items()}
            for s, ms in out.items()}


def shuffled_source(decls, rets):
    """A control: the same class's declarations with every type rotated onto a neighbouring name.

    The receiver walk places a site when the expression in front of the call declares a type that names a class
    *and* that class's chain declares the call's name, so a placement is only evidence if the declaration is doing
    the work. Rotating the class's own declared types (its locals and its return types, one pool) onto the next
    name leaves the shape of the source intact -- the same functions, the same number of names, the same set of
    type spellings -- and breaks exactly the binding that placed the site. What survives the rotation is a name
    that lands on any chain, which is the number a `--calls --shuffle` run exists to report.

    A name whose declaration the parser dropped (two spellings, a type that is not a class) has no value in the
    pool, so it stays absent rather than gaining one: the rotation must not hand a site a declaration it never had.
    """
    slots = [("local", fn, name, t) for fn, d in decls.items() for name, t in d.items()]
    slots += [("return", fn, "", t) for fn, t in rets.items()]
    slots.sort()
    values = [s[3] for s in slots]
    if len(values) > 1:
        values = values[1:] + values[:1]
    loc, ret = {}, {}
    for (kind, fn, name, _was), value in zip(slots, values):
        if kind == "local":
            loc.setdefault(fn, {})[name] = value
        else:
            ret[fn] = value
    return loc, ret


def _kind_key(kind):
    """A property class's name without the C++ `U` prefix.

    The tagged property stream names the class `UObjectProperty` (its C++ name) and an import names the same class
    `ObjectProperty`, so comparing the two as strings rejects every property import in the corpus.
    """
    return kind[1:] if kind.startswith("U") and kind.endswith("Property") else kind


def _receiver_type(expr, export_at, import_at, prop_type, prop_type_by_name, local_types=None,
                   import_owner=None, owner_fixed=None, owner_conflict=None, owner_agree=None,
                   call_result=None, local_chain=None, ancestor_local=None, local_case=None, structs=None):
    """The class the receiver expression's declared type names, or "" when its token cannot say."""
    return _receiver_reading(expr, export_at, import_at, prop_type, prop_type_by_name, local_types,
                             import_owner, owner_fixed, owner_conflict, owner_agree,
                             call_result, local_chain, ancestor_local, local_case, structs)[0]


def _receiver_reading(expr, export_at, import_at, prop_type, prop_type_by_name, local_types=None,
                      import_owner=None, owner_fixed=None, owner_conflict=None, owner_agree=None,
                      call_result=None, local_chain=None, ancestor_local=None, local_case=None, structs=None):
    """(the class the expression's declared type names, why it names none).

    A property's type comes from its declaration in `inventory/class_properties.csv`; a *local* is a property
    record the function owns, so the export table gives its name and the class's own ScriptText the type
    (`source_declarations`); an imported property is looked up by name. A name several classes declare is not a
    tie to break by count: the import's own `OuterIndex` names the class the variable belongs to, and that is the
    declaration the compiler resolved against, so it wins over the corpus-wide vote (`import_owner`). The second
    half of the pair is what a residue site is counted by, so the split says whether the gap is a missing
    declaration, a disagreement between packages, or a type that is not a class at all.
    """
    nm = expr["name"]
    if nm in ("Context", "ClassContext") and expr["children"]:
        return _receiver_reading(expr["children"][-1], export_at, import_at, prop_type, prop_type_by_name,
                                 local_types, import_owner, owner_fixed, owner_conflict, owner_agree,
                                 call_result, local_chain, ancestor_local, local_case, structs)
    if nm in ("ArrayElement", "DynamicArrayElement") and expr["children"]:
        # An array element is the array's declared element type: `_class_of_type` unwraps `array<Pawn>` to `Pawn`.
        # The two children are the index and the array (in that order, measured), so the index names nothing and the
        # array is whichever child the declarations do type.
        kids = []
        for child in expr["children"]:
            cls, one = _receiver_reading(child, export_at, import_at, prop_type, prop_type_by_name, local_types,
                                         import_owner, owner_fixed, owner_conflict, owner_agree,
                                         call_result, local_chain, ancestor_local, local_case, structs)
            if cls:
                return cls, ""
            kids.append(one)
        # The array is the last child (index first, measured), and its reason is the one that answers the site: the
        # index is an int and never names a class. Carrying it through is what turns a blanket "names no class" into
        # the declaration's own words -- a class behind its inlining modifiers (placed), a declared struct (named),
        # or an element that really is neither.
        return "", "array element: " + (kids[-1] if kids and kids[-1]
                                        else "the array's type names no class")
    if nm in CALL_TOKENS and call_result is not None:
        # A call leaves its result on the stack, so a call in front of a call is a receiver too:
        # `GetOwner().Foo()` dispatches on whatever `GetOwner` is *declared* to return.
        return call_result(expr)
    if nm in ("DynamicCast", "MetaCast", "ObjectConst"):
        value, _e = read_index(expr["operands"], 0)
        target = export_at.get(value)
        if target and target[1] == "Class":
            return target[0], ""
        # A class the package *imports* is a class too. `DynamicCast Pawn` in `StargateContent` names
        # `Engine.Pawn` through the import table, and only the export table was consulted -- so every cast whose
        # class is declared in another package read as "names no class", which is 70 of the 181 residue sites.
        if value < 0 and -value in import_at:
            name, kind = import_at[-value]
            if _kind_key(kind) == "Class":
                return name, ""
            return "", "a cast whose import is a %s, not a class" % (kind or "object")
        return "", "a literal or cast that names no class"
    if nm == "StructMember":
        # A struct member's own record names the member and its Outer names the struct (`FaceWidget`'s outer is
        # `SquadItemStruct`, `Message`'s is `LocalizedMessage`), and its class says only that it is a property --
        # the declared class is a `var` line in the `struct` block of the class text (`source_structs`).
        if structs is None:
            return "", "struct member: the struct tables were not loaded"
        value, _e = read_index(expr["operands"], 0)
        name, owner = "", ""
        if value > 0 and value in export_at:
            name, _kind, owner = export_at[value]
        elif value < 0 and -value in import_at:
            # A class that uses a struct another class declares *imports* its members: the record is the import
            # and its own OuterIndex is the struct (`HUDLocalizedMessage`), not a class's property, so the export
            # table alone answers "names no record" for six of the twenty-five sites.
            name, _kind = import_at[-value]
            owner = (import_owner or {}).get(-value) or ""
        if not owner:
            return "", "struct member: the operand names no record"
        members = structs(owner)
        if members is None:
            return "", "struct member: no class text declares the struct"
        if (name or "").lower() not in members:
            return "", "struct member: the struct does not declare it"
        t = members[(name or "").lower()]
        if t is None:
            return "", "struct member: the struct declares it with two types"
        return _typed(_class_of_type(t), structs, "struct member",
                      "struct member: declared, but not as a class")
    if nm not in ("InstanceVariable", "LocalVariable", "DefaultVariable"):
        return "", "not an object expression"
    value, _e = read_index(expr["operands"], 0)
    if nm == "LocalVariable":
        if not (value > 0 and value in export_at):
            return "", "local: the operand names no record"
        name = export_at[value][0]
        if name not in (local_types or {}):
            folded = _folded(local_types, name)
            if folded is not None:
                cls, why = _typed(_class_of_type(folded), structs, "local", "")
                if cls:
                    if local_case is not None:
                        local_case[name] += 1
                    return cls, ""
                if why:
                    return "", why
            if local_chain is not None:
                # A function a class inherits is not in its own source, and neither are the locals its body
                # declares, so the declaration is looked for on the caller's chain -- in the ScriptText of the
                # ancestor that declares the same function.
                hit, where = local_chain(name)
                if hit is not None:
                    cls, why = _typed(_class_of_type(hit), structs, "local", "")
                    if cls:
                        if ancestor_local is not None:
                            ancestor_local[where] += 1
                        return cls, ""
                    return "", why or "local: the declaring ancestor does not type it as a class"
            return "", "local: the class source does not declare it"
        hit = local_types[name]
        if hit is None:
            return "", "local: the class declares it with two types"
        return _typed(_class_of_type(hit), structs, "local", "local: declared, but not as a class")
    if value in export_at:
        name, _kind, owner = export_at[value]
        hit = prop_type.get((owner, name))
        if not hit:
            return "", "property: not in the script's declarations"
        return _typed(_class_of_type(hit[1]), structs, "property", "property: declared, but not as a class")
    if value < 0 and -value in import_at:
        name, kind = import_at[-value]
        # An import's class names the *kind* of object it is, so only a property kind can carry a variable's type:
        # `Class`, `Function`, `Struct` and `Sound` imports sit in the same table and are not variables at all.
        if not kind.endswith("Property"):
            return "", "import: names a %s, not a property" % (kind or "object")
        decl = [c for c in prop_type_by_name.get(name, ()) if c[1] == _kind_key(kind)]
        if not decl:
            return "", "import: no package declares that property"
        types = {_class_of_type(c[2]) for c in decl}
        # The import's OuterIndex is the class the variable belongs to (`-outer` is a negative import index), so
        # the owner's own declaration is the one the compiler resolved against -- a vote between same-named
        # properties of unrelated classes is not a tie to break by count.
        iowner = (import_owner or {}).get(-value) or ""
        own = prop_type.get((iowner, name)) if iowner else None
        if own is not None and _kind_key(own[0]) == _kind_key(kind):
            cls = _class_of_type(own[1])
            if len(types) > 1:
                if cls and owner_fixed is not None:
                    owner_fixed[iowner] += 1
            elif cls and types:
                # The control on the rule: where every declarer agrees, the owner's own class should agree too --
                # a case where it does not is a real disagreement, counted rather than silently preferred.
                if cls in types:
                    if owner_agree is not None:
                        owner_agree[iowner] += 1
                elif owner_conflict is not None:
                    owner_conflict[name] += 1
            return _typed(cls, structs, "import", "import: the owner declares it, but not as a class")
        if len(types) > 1:
            return "", "import: the packages that declare it disagree"
        cls = types.pop()
        return _typed(cls, structs, "import", "import: declared, but not as a class")
    return "", "no record for the operand"


def calls_report(path, table, out=None, lists=0):
    """Every script-level call the corpus makes, and what each one's receiver can be read to be.

    A `FinalFunction` operand is an object reference, so the call is static and the target is known; a
    `VirtualFunction` or `GlobalFunction` operand is a name, so the target is a name the runtime resolves against the
    receiver. Three lookups place a by-name call: the caller's own class, its super chain in the package that
    declares each ancestor, and -- when the name is declared by exactly one class in the corpus -- that class
    whatever the receiver is.

    What is left is a real dispatch question, and the site *does* carry the receiver, just not as an operand: the
    emitter evaluates the object before the call token, so the expression in front of the call is it. When that
    expression is a variable, a cast, a `Context` or an object literal, its declared type names a class, and the
    and the call is placed on *that* class's chain. A base-typed receiver can hold any subclass, so a name the
    chain does not declare is looked for *below* it as the last step: one declaring descendant places the site
    (`descendant:Pkg.Class`), several are a dispatch the static type cannot decide. `--lists N` prints
    the residue with names -- package, class, function, call, the token in front, that token's
    operand name and the reason -- because a residue that is only counted is a residue the next
    session has to re-find. The typed residue is listed the same way: a receiver whose own chain declares no
    such name, the classes below the receiver that do declare it, and the token's own operand name -- because
    that set is what says whether the site is a dispatch or a reading that is still missing. Locals are the
    exception --
    their type is not a class property, so it is not in `inventory/class_properties.csv` -- which is why they are
    counted separately below.

    A function declared inside a `State` is a function of the class that owns the state (the state compiles into
    that class), so a site inside one is attributed to the class, not to the state.
    """
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    from ue2pkg import Package                                # noqa: PLC0415 - only for a real package

    files = ([os.path.join(path, f) for f in sorted(os.listdir(path)) if f.lower().endswith(".u")]
             if os.path.isdir(path) else [path])
    # The package each class's source is read from, by package name: an ancestor's declaration lives outside
    # the package being walked, and `class_source` opens it on first ask.
    pkg_paths = {os.path.basename(one).rsplit(".", 1)[0]: one for one in files}
    sourced = {}
    preproc = {"classes": 0, "gaps": 0, "names": []}   # texts with a directive line, and what it split
    # (package, class) -> (package the super is declared in, super): the chain a by-name call is resolved along.
    supers = {}
    for r in csv.DictReader(open(os.path.join(ROOT, "inventory", "classes.csv"))):
        supers[(r["package"], r["class"])] = (r["package_of_super"] or r["package"], r["super"])
    pkg_of_class = collections.defaultdict(list)
    for (p, c) in supers:
        pkg_of_class[c].append(p)
    # UnrealScript matches a class name case-insensitively while the source spells one `pawn` and the export table
    # `Pawn`, so every class lookup folds the case and keeps the canonical (package, class) pair.
    canon = collections.defaultdict(list)
    for (p, c) in supers:
        canon[c.lower()].append((p, c))

    def class_pairs(one):
        """The canonical (package, class) pairs a type name can mean, exact case first."""
        return [(p, one) for p in pkg_of_class.get(one, ())] or canon.get(one.lower(), [])
    # (class, property) -> (kind, declared type): what a variable receiver's type names.
    prop_type = {}
    prop_type_by_name = collections.defaultdict(set)
    for r in csv.DictReader(open(os.path.join(ROOT, "inventory", "class_properties.csv"))):
        prop_type[(r["class"], r["property"])] = (r["kind"], r["friendly_type"])
        prop_type_by_name[r["property"]].add((r["class"], _kind_key(r["kind"]), r["friendly_type"]))
    # Every package's Function exports, by declaring class -- read from the export tables alone, no payload walk.
    funcs = collections.defaultdict(set)
    # name -> the classes that declare a function of that name, anywhere in the corpus. A virtual call dispatches
    # by name on the *receiver's* class, so a name only one class declares can reach exactly one implementation
    # whatever the receiver is, and a name several classes declare is where the receiver would have decided. A
    # declaration inside a State is owned by the class that owns the state: the state is compiled into that class.
    decls = collections.defaultdict(set)
    # The same declarations keyed by the *class* that owns them (a state's functions count for its class), which is
    # the index a subtree lookup needs: an ancestor's state function is reachable from the subclass that compiles it.
    cfuncs = collections.defaultdict(set)
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
            cfuncs[owner].add(fn)

    def on_chain(pkg_name, owner, name, state_hits=None):
        """The caller's own class, then its ancestors in the package that declares each: what a self-call needs.

        The index is keyed by the *class*, so a function a state declares is reachable from the class that owns the
        state and from its subclasses -- `funcs` alone keys it under the state's own name, which is not a class any
        chain walks. `state_hits` counts the placements that only that folding makes.
        """
        if name in cfuncs.get((pkg_name, owner), ()):
            if state_hits is not None and name not in funcs.get((pkg_name, owner), ()):
                state_hits[name] += 1
            return "package", ""
        here, seen = (pkg_name, owner), set()
        while here in supers and here not in seen:
            seen.add(here)
            here = supers[here]
            if name in cfuncs.get(here, ()):
                if state_hits is not None and name not in funcs.get(here, ()):
                    state_hits[name] += 1
                return "ancestor", "%s.%s" % here
        return "", ""

    def resolve_name(pkg_name, owner, name):
        """Where a by-name call's target is declared: the caller's chain, then the corpus.

        Returns (where, how_many_classes_declare_the_name, the_only_class). The corpus step is the last one that
        needs no receiver; `place_on_receiver` runs before it when the site's expression in front names a class.
        """
        where, decl = on_chain(pkg_name, owner, name, chain_state)
        if where:
            return (decl or where), len(decls.get(name, ())), ""
        owners = decls.get(name, set())
        if len(owners) == 1:
            (op, oc), = owners
            return "corpus:%s.%s" % (op, oc), 1, "%s.%s" % (op, oc)
        return "", len(owners), ""

    def place_on_receiver(rtype, name, state_hits=None):
        """Where a call on an object of class `rtype` is declared: the *receiver's* chain, not the caller's."""
        for p, cname in class_pairs(rtype):
            where, decl = on_chain(p, cname, name, state_hits)
            if where:
                if cname != rtype:
                    case_fixed[rtype] += 1
                return decl or "%s.%s" % (p, cname)
        return ""

    subs = collections.defaultdict(set)
    for (p, c), (pp, sup) in supers.items():
        if sup:
            subs[(pp, sup)].add((p, c))
    subtrees = {}

    def subtree(rtype):
        """Every (package, class) *below* `rtype` in the corpus, walked once per receiver class."""
        if rtype not in subtrees:
            seen, todo, out = set(), list(class_pairs(rtype)), []
            while todo:
                node = todo.pop()
                if node in seen:
                    continue
                seen.add(node)
                for kid in subs.get(node, ()):
                    if kid not in seen:
                        todo.append(kid)
                        out.append(kid)
            subtrees[rtype] = out
        return subtrees[rtype]

    def place_below(rtype, name):
        """The classes below `rtype` that declare `name`.

        A virtual call dispatches on the receiver's class, and a receiver whose static type is the base can hold any
        descendant, so a name that no class on the base's chain declares may still be declared by one below it. One
        answer can be reported as where the call lands; several are an ambiguity that the static type cannot decide,
        and they are named rather than guessed.
        """
        return {k for k in subtree(rtype) if name in cfuncs.get(k, ())}
    epilogue = bytes([0x04, 0x0B, 0x47])
    rows = []
    # Why a by-name site that stays unplaced still has an object expression in front of it.
    receiver_gap = collections.Counter()
    # Every site whose receiver expression names no class, by the reason the reading gives -- a residue that is
    # counted is not a residue that is named, and the name is what the next fix is written from (`--lists`).
    residue = []
    gap_names = collections.Counter()
    # The receiver typed to a class whose chain declares nothing of that name: what the corpus knows *below* it.
    gap_classes = collections.Counter()
    # The typed gap with names: a residue that is only counted is a residue the next session has to re-find,
    # and the token in front plus the classes below the receiver are what says whether the site is a dispatch.
    gap_rows = []
    chain_state = collections.Counter()
    recv_state = collections.Counter()
    case_fixed = collections.Counter()
    below_uniq = collections.defaultdict(set)
    below_multi = collections.Counter()
    below_outside = collections.Counter()
    below_none = collections.Counter()
    gap_class = []
    import_owners = collections.Counter()
    # Receivers that are an array element, typed from the array's declared element type.
    array_fixed = collections.Counter()
    # Receivers typed by the import's own owner class, where the packages disagree on the type.
    owner_fixed = collections.Counter()
    # The owner's own declaration differing from a unanimous vote: a real disagreement, counted.
    owner_conflict = collections.Counter()
    # The owner's own declaration agreeing with a unanimous vote: the rule's control.
    owner_agree = collections.Counter()
    # A receiver that is a call result, by the token and what the callee's declaration typed it to.
    call_sites = collections.Counter()
    # A local typed from an ancestor's declaration rather than the caller's own.
    ancestor_local = collections.Counter()
    # A local whose declaration the source spells in another case than the export table.
    local_case = collections.Counter()
    # The token that produced a value a later call sits in front of: what is left after the call results.
    value_tokens = collections.Counter()
    walked = seen = 0
    for one in files:
        pkg = Package(one)
        pkg.read_names()
        pkg.read_imports()
        pkg.read_exports()
        pkg_name = os.path.basename(one).rsplit(".", 1)[0]
        export_at = {}
        for i, x in enumerate(pkg.exports, 1):
            outer_of = pkg.name(pkg.exports[x["outer"] - 1]["name"]) if x.get("outer") else ""
            export_at[i] = (pkg.name(x["name"]), pkg.class_name_of(x), outer_of)
        import_at = {i: (pkg.name(x["name"]), pkg.name(x["class"])) for i, x in enumerate(pkg.imports, 1)}
        # The import's `package` field is its OuterIndex: a u32 that is a negative import index for a nested object,
        # so -outer names the class (or package) the property belongs to -- which the packages never spell for a
        # property they do not declare.
        import_owner = {}
        for i, x in enumerate(pkg.imports, 1):
            o = x.get("package") or 0
            k = -((1 << 32) - o) if o >= (1 << 31) else 0
            if k < 0 and -k <= len(pkg.imports):
                import_owner[i] = pkg.name(pkg.imports[-k - 1]["name"])
        # A local's declared type is not in the package's records, so it comes from the class's own ScriptText;
        # parse each class once per package and answer per function from that.
        class_export = {}
        for k, x in enumerate(pkg.exports, 1):
            if pkg.class_name_of(x) == "Class":
                class_export.setdefault(pkg.name(x["name"]), (k, x))

        def class_source(one_pkg, cls_name):
            """(locals by function, return types by function) for one class, from its own ScriptText.

            A local's type and a function's return type are both statements of the source, not fields of the
            record, and an ancestor's answer lives in the package that declares it -- so the parse is cached by
            (package, class) and every other package is opened on first ask, once.
            """
            key = (one_pkg, cls_name)
            if key not in sourced:
                text = None
                if one_pkg == pkg_name:
                    hit = class_export.get(cls_name)          # the package being walked is already open
                    text = _script_text(pkg, hit[1], hit[0] - 1) if hit else None
                else:
                    other = pkg_paths.get(one_pkg)
                    if other:
                        pkg2 = Package(other)
                        pkg2.read_names()
                        pkg2.read_imports()
                        pkg2.read_exports()
                        for k, x in enumerate(pkg2.exports, 1):
                            if pkg2.class_name_of(x) == "Class" and pkg2.name(x["name"]) == cls_name:
                                text = _script_text(pkg2, x, k - 1)
                                break
                if text:
                    if _SRC_PREPROC.search(_SRC_COMMENT.sub("", text)):
                        preproc["classes"] += 1
                    for fn in _directive_gaps(text):
                        preproc["gaps"] += 1
                        preproc["names"].append("%s.%s" % (cls_name, fn))
                sourced[key] = (source_declarations(text), source_returns(text))
                if _SHUFFLE:
                    sourced[key] = shuffled_source(*sourced[key])
            return sourced[key]

        def local_decls(cls_name, fn_name):
            """{local name: declared type} for one function of `cls_name`, from its ScriptText."""
            locs = class_source(pkg_name, cls_name)[0]
            hit = locs.get(fn_name)
            if hit is None:
                for fn, d in locs.items():
                    if fn.lower() == fn_name.lower():
                        return d
            return hit or {}

        # A struct's members are `var` lines in the `struct` block of the class text that declares it, and a
        # struct may be declared in any package (`SquadItemStruct` in StargateGame, `LocalizedMessage` in
        # Engine), so the corpus is walked once, on the first site that asks, into {struct: {member: type}}.
        # Two classes that declare the same struct with different member types drop that member, not the struct.
        struct_index = {}
        struct_built = [False]

        def build_structs():
            """{struct: {member: declared type}} over every package's class texts, once."""
            if struct_built[0]:
                return
            struct_built[0] = True
            for pname in sorted(pkg_paths):
                if pname == pkg_name:
                    pobj, cexp = pkg, class_export
                else:
                    pobj = Package(pkg_paths[pname])
                    pobj.read_names()
                    pobj.read_imports()
                    pobj.read_exports()
                    cexp = {}
                    for k, x in enumerate(pobj.exports, 1):
                        if pobj.class_name_of(x) == "Class":
                            cexp.setdefault(pobj.name(x["name"]), (k, x))
                for _cname, (k, x) in cexp.items():
                    for sname, members in source_structs(_script_text(pobj, x, k - 1)).items():
                        sink = struct_index.setdefault(sname.lower(), {})
                        for mname, t in members.items():
                            low = mname.lower()
                            if low not in sink:
                                sink[low] = t
                            elif sink[low] != t:
                                sink[low] = None

        def structs_for(name):
            """{member: declared type} for one struct name, or None when no class text declares it."""
            build_structs()
            return struct_index.get((name or "").lower())

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
            oe = pkg.exports[e["outer"] - 1] if e["outer"] > 0 else None
            owner = pkg.name(oe["name"]) if oe is not None else "?"
            if oe is not None and oe.get("outer") and pkg.class_name_of(oe) == "State":
                owner = pkg.name(pkg.exports[oe["outer"] - 1]["name"])
            here_fn = pkg.name(e["name"])
            here_locals = local_decls(owner, here_fn)

            def call_result(expr):
                """The class a call's declared return type names: the receiver a chained call dispatches on.

                `GetOwner().Foo()` has no variable to type -- the receiver is the first call's *result*, and the
                only statement of its type is the callee's declaration (`function Actor GetOwner()`), which the
                class's own `ScriptText` carries. A `FinalFunction` names its Function export, so the callee's
                class is that export's outer (a State's function belongs to the class that owns the state); a
                `VirtualFunction` or `GlobalFunction` names a name, resolved on the caller's chain the way a
                self-call is, and then by the corpus when exactly one class declares it -- the same last lookup a
                by-name call site gets, because the chained call's receiver is that callee's own object and the
                corpus-only answer needs no receiver.
                """
                value, _e = read_index(expr["operands"], 0)
                if expr["name"] == "FinalFunction":
                    callee = None
                    if 0 < value <= len(pkg.exports):
                        x = pkg.exports[value - 1]
                        if pkg.class_name_of(x) == "Function" and x.get("outer"):
                            outer = pkg.exports[x["outer"] - 1]
                            cls = pkg.name(outer["name"])
                            if pkg.class_name_of(outer) == "State" and outer.get("outer"):
                                cls = pkg.name(pkg.exports[outer["outer"] - 1]["name"])
                            callee = (pkg_name, cls, pkg.name(x["name"]))
                    elif value < 0 and -value in import_at:
                        # A function another package declares is an *import*, and the import's own OuterIndex
                        # names the class that declares it -- so the return type is a statement in that class's
                        # text and not in this package at all. Twelve sites read "names no Function of this
                        # package" with a `PlayerOwner` import in front, which is a function, not a variable.
                        iname, ikind = import_at[-value]
                        iowner = (import_owner or {}).get(-value) or ""
                        pairs = class_pairs(iowner) if iowner else []
                        if _kind_key(ikind) != "Function":
                            return "", "call result: the import names a %s, not a function" % (ikind or "object")
                        if not pairs:
                            return "", "call result: the imported function's class is not in the corpus"
                        if len({p for p, _c in pairs}) > 1:
                            return "", "call result: the imported function's class is declared by two packages"
                        callee = (pairs[0][0], pairs[0][1], iname)
                    if callee is None:
                        return "", "call result: the operand names no Function of this package"
                else:
                    name = pkg.name(value) if 0 <= value < len(pkg.names) else ""
                    where, decl = on_chain(pkg_name, owner, name)
                    if where == "package":
                        callee = (pkg_name, owner, name)
                    elif where == "ancestor":
                        op, oc = decl.split(".", 1)
                        callee = (op, oc, name)
                    else:
                        # The chain is the caller's; the callee may still be declared by one class elsewhere in
                        # the corpus (`GetMoveTarget` and three others are), and a name exactly one class declares
                        # resolves there whatever the receiver is -- the rule the by-name call table already uses.
                        owners = decls.get(name, set())
                        if len(owners) != 1:
                            # Named, not counted: a residue row has to say which callee it could not place and
                            # how many classes claim it, or the next session re-finds it from the bytes.
                            if not owners:
                                return "", ("call result: the callee %s is declared by no class in the corpus"
                                            % (name or "?"))
                            return "", ("call result: the callee %s is declared by %d classes, none on the "
                                        "caller's chain" % (name, len(owners)))
                        (op, oc), = owners
                        callee = (op, oc, name)
                rets = class_source(callee[0], callee[1])[1]
                ret = _folded(rets, callee[2])
                if callee[2] not in rets and ret is None:
                    low = {k.lower(): v for k, v in rets.items()}
                    if callee[2].lower() not in low:
                        return "", "call result: the callee's class declares no return type for it"
                    ret = low[callee[2].lower()]
                if ret is None:
                    return "", "call result: the callee is declared twice with different types"
                if not ret:
                    return "", "call result: the callee returns nothing"
                cls = _class_of_type(ret)
                return (cls, "") if cls else ("", "call result: the callee returns a value that is not a class")

            def local_chain(name):
                """A local the caller's own source does not declare: the ancestor that declares the function.

                A function a class inherits is not in its own source, and neither are the locals its body
                declares, so the chain is walked the way a by-name self-call walks it, reading each ancestor's
                `ScriptText` for the same function name.
                """
                here, seen = (pkg_name, owner), set()
                while here in supers and here not in seen:
                    seen.add(here)
                    here = supers[here]
                    if not here[1]:
                        break
                    locs = class_source(here[0], here[1])[0]
                    decls = locs.get(here_fn)
                    if decls is None:
                        for fn, d in locs.items():
                            if fn.lower() == here_fn.lower():
                                decls = d
                                break
                    hit = _folded(decls, name) if decls else None
                    if hit is not None:
                        return hit, "%s.%s" % here
                return None, ""

            for node, i, sibs, parent in _tree(nodes):
                kind = CALL_TOKENS.get(table.rows[node["opcode"]]["name"])
                if kind is None or not node["operands"]:
                    continue
                value, _e = read_index(node["operands"], 0)
                token = table.rows[node["opcode"]]["name"]
                if kind == "by name":
                    name = pkg.name(value) if 0 <= value < len(pkg.names) else ""
                    prev = sibs[i - 1] if i > 0 else None
                    rtoken = "self" if prev is None else (prev["name"] or "op%02x" % prev["opcode"])
                    rtype, why = "", ""
                    if prev is not None:
                        if rtoken in OBJECT_TOKENS:
                            rtype, why = _receiver_reading(prev, export_at, import_at, prop_type,
                                                           prop_type_by_name, here_locals,
                                                           import_owner, owner_fixed,
                                                           owner_conflict, owner_agree,
                                                           call_result, local_chain, ancestor_local, local_case,
                                                           structs_for)
                            if rtoken in CALL_TOKENS:
                                call_sites[(rtoken, rtype or (why or "no reason"))] += 1
                        else:
                            value_tokens[rtoken] += 1
                            rtoken = "value"
                    where, nowners, uniq = resolve_name(pkg_name, owner, name) if name else ("", 0, "")
                    if rtype and rtoken in ("ArrayElement", "DynamicArrayElement"):
                        array_fixed[(rtoken, rtype)] += 1
                    if not where and rtype and name:
                        placed = place_on_receiver(rtype, name, recv_state)
                        if placed:
                            where = "receiver:%s" % placed
                        else:
                            gap_classes[rtype] += 1
                            hits = place_below(rtype, name)
                            gap_rows.append((pkg_name, owner, pkg.name(e["name"]), name, rtype,
                                             _operand_name(prev, export_at, import_at),
                                             (prev["name"] or "op%02x" % prev["opcode"]) if prev else "self",
                                             ",".join(sorted("%s.%s" % h for h in hits))))
                            if len(hits) == 1:
                                (hp, hc), = hits
                                where = "descendant:%s.%s" % (hp, hc)
                                below_uniq[name].add("%s.%s" % (hp, hc))
                            elif hits:
                                below_multi[name] += 1
                            elif name in decls:
                                below_outside[name] += 1
                            else:
                                below_none[name] += 1
                    if not where and name and rtoken in OBJECT_TOKENS and not rtype:
                        receiver_gap[why or rtoken] += 1
                        residue.append((pkg_name, owner, pkg.name(e["name"]), name, rtoken,
                                        _operand_name(prev, export_at, import_at), why or rtoken))
                        if why.startswith("import:"):
                            gap_names[_operand_name(prev, export_at, import_at)] += 1
                            iv, _ie = read_index(prev["operands"], 0)
                            if iv < 0 and -iv in import_owner:
                                import_owners[import_owner[-iv] or "?"] += 1
                        if why.startswith("import: names a") and len(gap_class) < 8:
                            gap_class.append("%s.%s -> %s on %s"
                                             % (pkg_name, pkg.name(e["name"]), name,
                                                _operand_name(prev, export_at, import_at)))
                    rows.append((pkg_name, owner, pkg.name(e["name"]), token, kind, name,
                                 "Function" if where else "name", where, nowners, uniq, rtoken, rtype))
                    continue
                if value == 0:
                    rows.append((pkg_name, owner, pkg.name(e["name"]), token, kind, "", ""))
                elif value > 0 and value in export_at:
                    rows.append((pkg_name, owner, pkg.name(e["name"]), token, kind, export_at[value][0],
                                 export_at[value][1]))
                elif value < 0 and -value in import_at:
                    rows.append((pkg_name, owner, pkg.name(e["name"]), token, kind, import_at[-value][0],
                                 import_at[-value][1]))
                else:
                    rows.append((pkg_name, owner, pkg.name(e["name"]), token, kind, "", "out of range"))
    static = [r for r in rows if r[4] == "static"]
    by_name = [r for r in rows if r[4] == "by name"]
    print("%d scripts with an epilogue, %d walked; %d call sites" % (seen, walked, len(rows)))
    print("  static (FinalFunction): %d, of which the target is a Function: %d"
          % (len(static), sum(1 for r in static if r[6] == "Function")))
    here = [r for r in by_name if r[7] == "package"]
    above = [r for r in by_name
             if r[7] and r[7] != "package" and not r[7].startswith(("corpus:", "receiver:", "descendant:"))]
    corpus = [r for r in by_name if r[7].startswith("corpus:")]
    recv = [r for r in by_name if r[7].startswith("receiver:")]
    below_rows = [r for r in by_name if r[7].startswith("descendant:")]
    wide = [r for r in by_name if not r[7] and r[5]]
    only = {r[5]: r[9] for r in corpus}
    wide_n = {r[5]: r[8] for r in wide}
    print("  by name (VirtualFunction, GlobalFunction): %d" % len(by_name))
    print("    naming a declaring class: %d of %d (%.1f%%)"
          % (len(by_name) - len(wide), len(by_name),
             100.0 * (len(by_name) - len(wide)) / max(1, len(by_name))))
    print("    on the caller's chain: %d -- %d in its own class, %d on an ancestor (top: %s)"
          % (len(here) + len(above), len(here), len(above),
             ", ".join("%s %d" % kv for kv in collections.Counter(r[7] for r in above).most_common(5))))
    print("      of those, %d name a function only a *state* of that class declares (the class owns the state)"
          % sum(chain_state.values()))
    print("    by the corpus alone: %d site(s) reach the one class that declares the name (%d names), top: %s"
          % (len(corpus), len(only),
             ", ".join("%s -> %s %d" % (n, only[n], c)
                       for n, c in collections.Counter(r[5] for r in corpus).most_common(5))))
    print("    on the receiver's chain: %d (%d names) -- the expression in front of the call types to a class "
          "(%d distinct, top: %s)"
          % (len(recv), len({r[5] for r in recv}),
             len({r[11] for r in recv}),
             ", ".join("%s %d" % kv for kv in collections.Counter(r[11] for r in recv).most_common(5))))
    print("      of those, %d place because the receiver's type names a class in another case, top: %s"
          % (sum(case_fixed.values()), ", ".join("%s %d" % kv for kv in case_fixed.most_common(6))))
    print("      and %d name a function only a *state* of the receiver's chain declares"
          % sum(recv_state.values()))
    open_ = [r for r in wide if not r[7]]
    untyped = [r for r in open_ if r[10] in OBJECT_TOKENS and not r[11]]
    typed_gap = [r for r in open_ if r[10] in OBJECT_TOKENS and r[11]]
    locals_ = [r for r in untyped if r[10] == "LocalVariable"]
    print("    decided by the receiver: %d site(s) across %d names, top: %s"
          % (len(wide), len(wide_n),
             ", ".join("%s %d over %d classes" % (n, c, wide_n[n])
                       for n, c in collections.Counter(r[5] for r in wide).most_common(5))))
    print("      an object expression with no recorded type: %d -- %d of them a local, %d not"
          % (len(untyped), len(locals_), len(untyped) - len(locals_)))
    print("      a directive line between a declaration and its body: %d declaration(s) over %d class text(s)%s"
          % (preproc["gaps"], preproc["classes"],
             (": " + ", ".join(sorted(preproc["names"]))) if preproc["names"] else ""))
    print("      of the receiver-typed sites, %d had a local in front (typed from the class's own ScriptText)"
          % len([r for r in recv if r[10] == "LocalVariable"]))
    if local_case:
        print("      a local whose declaration the source spells in another case: %d site(s), top: %s"
              % (sum(local_case.values()), ", ".join("%s %d" % kv for kv in local_case.most_common(6))))
    if ancestor_local:
        print("      of the locals the caller's own source does not declare, %d are typed from an ancestor's "
              "declaration (%d class(es), top: %s)"
              % (sum(ancestor_local.values()), len(ancestor_local),
                 ", ".join("%s %d" % kv for kv in ancestor_local.most_common(6))))
    print("      an expression that typed to a class whose chain declares no such name: %d, of which %d reach "
          "exactly one descendant that declares it (descendant:Pkg.Class, %d names), top: %s"
          % (len(typed_gap) + len(below_rows), len(below_rows), len({r[5] for r in below_rows}),
             ", ".join("%s -> %s %d" % (n, "/".join(sorted(below_uniq[n])), c)
                       for n, c in collections.Counter(r[5] for r in below_rows).most_common(5))))
    print("        the rest stay a dispatch: %d over several descendants (%d names, top: %s), %d a name only classes "
          "outside the subtree declare, %d a name no class in the corpus declares"
          % (sum(below_multi.values()), len(below_multi),
             ", ".join("%s %d" % kv for kv in below_multi.most_common(4)),
             sum(below_outside.values()), sum(below_none.values())))
    print("        those %d receivers by class: %s"
          % (len(typed_gap) + len(below_rows),
             ", ".join("%s %d" % kv for kv in gap_classes.most_common(6))))
    print("        names only outside the subtree declare: %s"
          % ", ".join("%s %d" % kv for kv in below_outside.most_common(6)))
    print("        names no class declares: %s"
          % ", ".join("%s %d" % kv for kv in below_none.most_common(6)))
    if lists and gap_rows:
        print("      the typed residue with names: %d site(s); the first %d"
              % (len(gap_rows), min(lists, len(gap_rows))))
        for r in sorted(gap_rows, key=lambda r: (r[4], r[3], r[0], r[1], r[2]))[:lists]:
            print("        %-6s %-26s %-28s %-24s receiver %-6s %-16s %-18s below: %s"
                  % (r[0], r[1][:26], r[2][:28], r[3][:24], r[4][:6], r[6][:16], r[5][:18],
                     r[7] or "-"))
    print("      a completed value in front (a self-call, or a receiver the walk did not reach): %d"
          % (len(open_) - len(untyped) - len(typed_gap)))
    if value_tokens:
        print("        the token that produced that value: %s"
              % ", ".join("%s %d" % kv for kv in value_tokens.most_common(8)))
    if call_sites:
        typed_c, rest_c = collections.Counter(), collections.Counter()
        for (tok, what), n in call_sites.items():
            if what and not what.startswith("call result"):
                typed_c[what] += n
            else:
                rest_c[what or "no reason"] += n
        print("      a receiver that is a call result: %d site(s), %d typed from the callee's declared return "
              "type (top: %s)"
              % (sum(call_sites.values()), sum(typed_c.values()),
                 ", ".join("%s %d" % kv for kv in typed_c.most_common(6))))
        if rest_c:
            print("        the rest by what the callee's own statement says: %s"
                  % ", ".join("%s %d" % kv for kv in rest_c.most_common(6)))
    print("      why an expression in front names no class: %s"
          % ", ".join("%s %d" % kv for kv in receiver_gap.most_common(7)))
    if lists and residue:
        print("      the residue with names: %d site(s) over %d reason(s); the first %d, by reason"
              % (len(residue), len(receiver_gap), min(lists, len(residue))))
        for r in sorted(residue, key=lambda r: (r[6], r[0], r[1], r[2], r[3]))[:lists]:
            print("        %-6s %-28s %-30s %-24s %-10s %-16s %s"
                  % (r[0], r[1][:28], r[2][:30], r[3][:24], r[4], r[5][:16], r[6]))
    if array_fixed:
        print("      a receiver that is an array element: %d site(s) over %d (token, class) "
              "pairs, top: %s"
              % (sum(array_fixed.values()), len(array_fixed),
                 ", ".join("%s/%s %d" % (k[0], k[1], v) for k, v in array_fixed.most_common(6))))
    if gap_names:
        print("      the imported properties, by name: %s"
              % ", ".join("%s %d" % kv for kv in gap_names.most_common(8)))
    if import_owners:
        print("      the class those imports belong to: %s"
              % ", ".join("%s %d" % kv for kv in import_owners.most_common(8)))
    if owner_fixed:
        print("      typed by the import's own owner class: %d site(s) over %d class(es), top: %s"
              % (sum(owner_fixed.values()), len(owner_fixed),
                 ", ".join("%s %d" % kv for kv in owner_fixed.most_common(6))))
    if owner_agree or owner_conflict:
        print("      where every declarer agrees, the owner's own class agrees in %d site(s) "
              "and differs in %d" % (sum(owner_agree.values()), len(owner_conflict)))
    if owner_conflict:
        print("      where the owner declares a type the other declarers do not: %d name(s), top: %s"
              % (len(owner_conflict),
                 ", ".join("%s %d" % kv for kv in owner_conflict.most_common(6))))
    if gap_class:
        print("      a receiver import that names a class rather than a variable: %s" % "; ".join(gap_class[:6]))
    callees = collections.Counter([r[5] for r in static if r[6] == "Function" and r[5]]
                                  + [r[5] for r in by_name if r[5] and r[7]])
    print("  most-called resolved targets: %s" % ", ".join("%s %d" % kv for kv in callees.most_common(8)))
    if out:
        os.makedirs(out, exist_ok=True)
        with open(os.path.join(out, "script_calls.csv"), "w", newline="") as fh:
            w = csv.writer(fh, lineterminator="\n")
            w.writerow(["package", "class", "function", "token", "dispatch", "target", "target_class",
                        "resolved_in", "declaring_classes", "only_class", "receiver", "receiver_type"])
            for row in rows:
                w.writerow(list(row) + [""] * (12 - len(row)))
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


def sizefield_report(path, rows=0, residue_csv=None):
    """What the dword 16 bytes before a script is: the script's size with every compact index widened to four.

    `operand_spans` carries the fact this reads: the loader widens a compact index operand to four bytes on the way
    into memory (measured on `StargateHUD.DrawOrderTarget`'s log call, `b8 2a` in the package and `a0 1e 26 01` in
    the guest). A size the compiler wrote *before* that widening is therefore

        len(script) + sum(4 - width)  over the script's index operands

    and that is the field, exactly, in **11,485 of 11,602 walked payloads (98.99%)** -- while only 2,141 payloads
    carry the *stored* length there. So the dword is not a length a reader can locate an end with (the loader uses
    `SerialSize`), it is the size the script has **in memory**, and it is a usable check on an extent instead: any
    end a walk reports must satisfy it. The residue is 117 payloads, 97 above the prediction and 20 below, each
    named by `--lists` with the widths of its index operands.
    """
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    from ue2pkg import Package                                # noqa: PLC0415 - only for a real package

    files = ([os.path.join(path, f) for f in sorted(os.listdir(path)) if f.lower().endswith(".u")]
             if os.path.isdir(path) else [path])
    table = Table()
    epilogue = bytes([0x04, 0x0B, 0x47])
    total = walked = exact = stored = 0
    sign = collections.Counter()
    height = collections.Counter()
    per_pkg = []
    residue = []
    for one in files:
        pkg = Package(one)
        pkg.read_names()
        pkg.read_imports()
        pkg.read_exports()
        n_pkg = n_walk = n_exact = 0
        for e in pkg.exports:
            if pkg.class_name_of(e) != "Function":
                continue
            pay = pkg.data[e["offset"]:e["offset"] + e["size"]]
            n = len(pay)
            off, last, aligned = 0, None, False
            for _ in range(8):
                try:
                    last, off = read_index(pay, off)
                except IndexError:
                    break
                if last == e["name"]:
                    aligned = True
                    break
            if not aligned:
                continue
            if n >= 10 and pay[n - 10:n - 7] == epilogue:
                end = n - 7
            elif n >= 12 and pay[n - 12:n - 9] == epilogue:
                end = n - 9
            else:
                continue
            start = off + 16
            if start > end:
                continue
            body = pay[start:end]
            want = struct.unpack_from("<I", pay, off + 12)[0]
            total += 1
            n_pkg += 1
            if want == len(body):
                stored += 1
            try:
                spans = operand_spans(body, table)
            except (Refused, IndexError, struct.error):
                continue
            walked += 1
            n_walk += 1
            widths = [b - a for a, b, k in spans if k == "index"]
            predicted = len(body) + sum(4 - w for w in widths)
            if predicted == want:
                exact += 1
                n_exact += 1
            else:
                sign["dword larger" if want > predicted else "dword smaller"] += 1
                height[want - predicted] += 1
                if len(residue) < 4000:
                    owner = pkg.exports[e["outer"] - 1] if e["outer"] > 0 else None
                    # The owner's *name* is the class (or the state) that declares the function; its `class_name_of`
                    # is the metaclass, which answers `Class` for every row and made the listing unusable for
                    # picking one to read (`--sizefield --lists` named 400 rows all `Class`).
                    residue.append((os.path.basename(one),
                                    pkg.name(owner["name"]) if owner is not None else "?",
                                    pkg.name(e["name"]), len(body), want, predicted, want - predicted,
                                    len(widths), dict(sorted(collections.Counter(widths).items()))))
        per_pkg.append((os.path.basename(one), n_pkg, n_walk, n_exact))

    name = os.path.basename(path.rstrip(os.sep)) or path
    print("%s: %d payload(s) with a located epilogue, %d walked" % (name, total, walked))
    if not walked:
        return 1
    print("  dword@name_end+12 == the script with every index widened to 4 bytes: %d of %d (%.2f%%)"
          % (exact, walked, 100.0 * exact / walked))
    print("  dword@name_end+12 == the stored script length: %d of %d (%.2f%%)"
          % (stored, walked, 100.0 * stored / walked))
    if os.path.isdir(path):
        print("  per package (payloads / walked / widened match):")
        for pkgname, t, w, x in per_pkg:
            if t:
                print("    %-22s %5d %5d %5d%s" % (pkgname, t, w, x, "" if x == w else "   <- residue"))
    if residue_csv:
        # The rows a guest read has to explain, in the one shape `script-ram.py --residue` reads. Nothing here is game
        # data: a package, two names the inventories already carry, and three numbers.
        with open(residue_csv, "w", newline="") as handle:
            writer = csv.writer(handle)
            writer.writerow(["package", "class", "function", "script", "field", "predicted", "diff",
                             "index_slots", "widths"])
            for row in residue:
                writer.writerow([row[0], row[1], row[2], row[3], row[4], row[5], row[6], row[7],
                                 ";".join("%d:%d" % kv for kv in sorted(row[8].items()))])
        print("  residue rows written to %s" % residue_csv)
    print("  residue by sign: %s; by the difference (dword - widened length): %s"
          % (dict(sign.most_common()), dict(sorted(height.items()))))
    if rows and residue:
        print("  named residue (%d row(s), up to %d named):" % (len(residue), min(rows, len(residue))))
        for pkgname, cls, fn, slen, want, predicted, diff, count, widths in residue[:rows]:
            print("    %-16s %-26s %-30s script %-5d dword %-7d predicted %-6d %+d"
                  "  %d index operand(s) by width %s"
                  % (pkgname, cls, fn, slen, want, predicted, diff, count, widths))
    return 0


def _payload_scripts(one):
    """`(export, body, want)` for every Function export in one package whose script this walk can locate."""
    from ue2pkg import Package                                # noqa: PLC0415 - only for a real package

    pkg = Package(one)
    pkg.read_names()
    pkg.read_imports()
    pkg.read_exports()
    epilogue = bytes([0x04, 0x0B, 0x47])
    for e in pkg.exports:
        if pkg.class_name_of(e) != "Function":
            continue
        pay = pkg.data[e["offset"]:e["offset"] + e["size"]]
        n = len(pay)
        off, last, aligned = 0, None, False
        for _ in range(8):
            try:
                last, off = read_index(pay, off)
            except IndexError:
                break
            if last == e["name"]:
                aligned = True
                break
        if not aligned:
            continue
        if n >= 10 and pay[n - 10:n - 7] == epilogue:
            end = n - 7
        elif n >= 12 and pay[n - 12:n - 9] == epilogue:
            end = n - 9
        else:
            continue
        start = off + 16
        if start > end:
            continue
        yield (pkg, e, pay[start:end], struct.unpack_from("<I", pay, off + 12)[0], pay[off:off + 44])


def _index_width(data, start, end):
    """Width of the compact index occupying `[start, end)`, or None if those bytes do not decode as one."""
    if start >= len(data):
        return None
    q = start + 1
    cont = data[start] & 0x40
    while cont:
        if q >= len(data):
            return None
        cont = data[q] & 0x80
        q += 1
    return q - start


def _row_features(spans):
    """What a row is *made of*, counted for the rule test: one key per opcode, operand width and pattern item."""
    counts = collections.Counter()
    for start, end, kind, op in spans:
        counts["op:%02x" % op] += 1
        if kind == "index":
            counts["index:w%d" % (end - start)] += 1
        elif kind == "case":
            counts["case:%d" % (end - start)] += 1
        elif kind == "fixed":
            counts["fixed:%d" % (end - start)] += 1
    return counts


def diverge_report(path, rows=0):
    """Where a `--sizefield` residue row's difference could come from, asked one operand at a time.

    `--sizefield` says the dword at `name_end+12` is the script with every compact index operand widened to four
    bytes, and is exact for 11,485 of 11,602 walked payloads. This mode asks the 117 residue rows two questions the
    counts cannot:

    * **is one operand responsible?** A single operand explains the row when its class, changed, moves the prediction
      onto the dword without moving the walk -- an `index` operand the compiler did **not** widen (moving it by
      `-(4 - width)`) or a `fixed` operand whose bytes decode as a compact index of exactly its own width (moving it
      by `+(4 - width)`, so the bytes consumed do not change). Every span of every residue row is tried.
    * **is one *class* of feature responsible?** For each feature a row is made of (an opcode, an operand width, a
      pattern item) the rule `dword - widened_length == k * count(feature)` is tested over the whole walked corpus
      for k in 1..8, so a rule that explains a residue row while any exact row contains that feature is visible as a
      violation rather than a fit.

    Neither answers yes for most of the set, which is the result: the residue is individual token encodings, not a
    second rule and not one mis-widthed table row.
    """
    files = ([os.path.join(path, f) for f in sorted(os.listdir(path)) if f.lower().endswith(".u")]
             if os.path.isdir(path) else [path])
    table = Table()
    walked = exact = 0
    residue = []
    features = []
    for one in files:
        for pkg, e, body, want, header in _payload_scripts(one):
            try:
                spans = _spans_with_op(body, table)
            except (Refused, IndexError, struct.error):
                continue
            walked += 1
            widths = [b - a for a, b, k, _op in spans if k == "index"]
            predicted = len(body) + sum(4 - w for w in widths)
            counts = _row_features(spans)
            features.append((want - predicted, counts))
            if predicted == want:
                exact += 1
                continue
            delta = want - predicted
            cands = []
            for a, b, kind, op in spans:
                if kind == "index":
                    if -(4 - (b - a)) == delta:
                        cands.append(("index-not-widened", op, b - a, a))
                elif kind == "fixed":
                    w = _index_width(body, a, b)
                    if w is not None and w == b - a and (4 - w) == delta:
                        cands.append(("fixed-is-index", op, w, a))
            owner = pkg.exports[e["outer"] - 1] if e["outer"] > 0 else None
            # A dword in the millions is a header decode that aligned on the wrong value: say whether the payload
            # carries the prediction at some *other* offset in the 44 bytes the header occupies, and where.
            elsewhere = ""
            if abs(delta) > (1 << 20):
                hits = [k for k in range(0, len(header) - 3, 1)
                        if struct.unpack_from("<I", header, k)[0] == predicted]
                elsewhere = ",".join("%+d" % (k - 12) for k in hits) or "-"
            residue.append((os.path.basename(one),
                            pkg.name(owner["name"]) if owner is not None else "?",
                            pkg.name(e["name"]), len(body), want, predicted, delta, want - len(body),
                            len(widths), cands, elsewhere))

    if not walked:
        print("%s: no walked payloads" % path)
        return 1
    print("%s: %d walked, %d exact, %d residue" % (path, walked, exact, len(residue)))
    print("  rows with exactly one explaining operand: %d (dword larger %d, smaller %d); with none: %d"
          % (sum(1 for r in residue if len(r[9]) == 1),
             sum(1 for r in residue if len(r[9]) == 1 and r[6] > 0),
             sum(1 for r in residue if len(r[9]) == 1 and r[6] < 0),
             sum(1 for r in residue if not r[9])))
    by_kind = collections.Counter((c[0], "op %02x" % c[1], "w%d" % c[2]) for r in residue for c in r[9])
    if by_kind:
        print("  explaining operands: %s"
              % ", ".join("%s %s/%s=%d" % (k[0], k[1], k[2], n) for k, n in by_kind.most_common()))
    groups = collections.Counter((r[0], r[1], r[6]) for r in residue)
    print("  residue groups (package, class, difference), largest first: %s"
          % ", ".join("%s/%s %+d=%d" % (k[0], k[1], k[2], n) for k, n in groups.most_common(8)))
    alone = [r[6] for r in residue if not r[9]]
    if alone:
        print("  the %d rows no single operand explains, by difference: %s"
              % (len(alone), dict(sorted(collections.Counter(alone).items()))))

    best = None
    tried = 0
    rules_feasible = []
    names = set()
    for _d, c in features:
        names.update(c)
    for name in sorted(names):
        for k in range(1, 9):
            tried += 1
            bad = sum(1 for d, c in features if d != k * c.get(name, 0))
            if bad == 0:
                rules_feasible.append("%s*%d" % (name, k))
            if best is None or bad < best[0]:
                best = (bad, name, k)
    print("  feature rules tried: %d over %d features (dword - widened == k * count(feature), k 1..8)"
          % (tried, len(names)))
    print("  rules exact over all %d walked rows: %s" % (walked, rules_feasible or "none"))
    if best:
        print("  closest single-feature rule: %s*%d leaves %d row(s) unexplained" % (best[1], best[2], best[0]))
    wild = [r for r in residue if abs(r[6]) > (1 << 20)]
    if wild:
        print("  dword in the millions: %d row(s), and the prediction sits elsewhere in the header at: %s"
              % (len(wild), ", ".join("%s %s@%s" % (r[0], r[2], r[10]) for r in wild)))
    if rows:
        print("  named rows (up to %d):" % min(rows, len(residue)))
        for pkgname, cls, fn, slen, want, predicted, delta, vs_len, nix, cands, elsewhere in residue[:rows]:
            print("    %-15s %-22s %-26s script %-5d dword %-7d predicted %-6d %+d (dword - script %+d)"
                  " %d index  %s"
                  % (pkgname, cls[:22], fn[:26], slen, want, predicted, delta, vs_len, nix,
                     " ".join("%s@op%02x/w%d@%#x" % (c[0], c[1], c[2], c[3]) for c in cands)
                     or "no single operand")
                  + ("  header-aligned dword at %s" % elsewhere if elsewhere else ""))
    return 0


def struct_check():
    """A control on the struct and declaration readings: the parse, the directive strip, the ways a member
    names nothing, the one that names a class.

    No dump: every case is a synthetic `struct` block or a hand-built expression, so `tool-check-contract` can
    drive it (`make ci`) and a site the reading places is attributable to the declaration that placed it.
    """
    fails, ran = [], [0]

    def want(label, got, expected):
        ran[0] += 1
        if got != expected:
            fails.append("%s: got %r, want %r" % (label, got, expected))

    want("a struct and its members",
         source_structs("class X extends Y;\n\nstruct A\n{\n\tvar int Count;\n\tvar UIWidget Face;\n}\n"),
         {"A": {"Count": "int", "Face": "UIWidget"}})
    want("a commented-out member is not a member",
         source_structs("struct A\n{\n\tvar int Count;\n\t/* var UIWidget Face; */\n}\n"),
         {"A": {"Count": "int"}})
    want("a nested struct's member belongs to the nested struct",
         source_structs("struct A\n{\n\tvar int Count;\n\tstruct B\n\t{\n\t\tvar UIWidget Face;\n\t};\n"
                        "\tvar B Inner;\n}\n"),
         {"A": {"Count": "int", "Inner": "B"}, "B": {"Face": "UIWidget"}})
    want("a member declared twice with two types is dropped",
         source_structs("struct A\n{\n\tvar int X;\n\tvar float X;\n\tvar name Y;\n}\n"),
         {"A": {"X": None, "Y": "name"}})
    want("a class with no struct block declares none", source_structs("function Touch(Actor A) {}\n"), {})
    want("a directive line between a declaration and its body keeps the body",
         source_structs("struct A\n{\n\tvar int Count;\n#ifdefXBOX\n\tvar UIWidget Face;\n#endif\n}\n"),
         {"A": {"Count": "int", "Face": "UIWidget"}})
    want("a body after an #endif is read",
         source_declarations("#ifdefXBOX\nfinal event Interaction Add(string N, optional bool bInFront)\n"
                            "#endif\n#ifndefXBOX\nfinal event Interaction Add(string N)\n#endif\n"
                            "{\n\tlocal Interaction Inner;\n\tlocal class<Interaction> Kind;\n}\n"),
         {"Add": {"N": "string", "bInFront": "bool", "Inner": "Interaction", "Kind": "class<Interaction>"}})
    want("a declaration split by a directive is named",
         _directive_gaps("final event Interaction Add(string N)\n#endif\n{\n}\n"), ["Add"])
    want("a modifier in front of the type is not the type",
         source_structs("struct A\n{\n\tvar config UIWidget Face;\n\tvar() editconst array<Foo> List;\n}\n"),
         {"A": {"Face": "UIWidget", "List": "array<Foo>"}})

    want("a type's inlining modifiers are not its name",
         [_class_of_type("array<export editinline ScriptEvent>"),
          _class_of_type("array<editconst editinline Stream>"),
          _class_of_type("export editinline Order")], ["ScriptEvent", "Stream", "Order"])
    want("a type nested in an array with a space before its bracket",
         _class_of_type("array<class<Info> >"), "Info")
    want("a declaration whose type has a space before its bracket",
         source_declarations("final function bool Init(array<class<Info> > Classes, optional bool bStrict)\n"
                             "{\n\tlocal int i;\n}\n"),
         {"Init": {"Classes": "array<class<Info> >", "bStrict": "bool", "i": "int"}})
    want("a declared struct is named as a struct, not handed on as a class",
         _typed("PlaylistParser", lambda _n: {"name": "string"}, "property", "property: no"),
         ("", "property: declared as a struct (`PlaylistParser`), not a class"))
    want("a type no struct block declares stays a class",
         _typed("ScriptEvent", lambda _n: None, "property", "x"), ("ScriptEvent", ""))
    want("no type at all keeps the branch's own reason",
         _typed("", lambda _n: {"name": "string"}, "property", "property: declared, but not as a class"),
         ("", "property: declared, but not as a class"))

    elem = {"name": "DynamicArrayElement", "operands": b"", "children": [
        {"name": "LocalVariable", "operands": b"\x02", "children": []},
        {"name": "InstanceVariable", "operands": b"\x03", "children": []}]}
    want("an array element whose type is a class behind its modifiers places",
         _receiver_reading(elem, {2: ("i", "IntProperty", "C"), 3: ("Songs", "ArrayProperty", "C")}, {},
                           {("C", "Songs"): ("ArrayProperty", "array<editconst editinline Stream>")},
                           {}, {}, None, None, None, None, None, None, None, None, lambda _n: None),
         ("Stream", ""))
    want("an array element whose type is a struct says so",
         _receiver_reading(elem, {2: ("i", "IntProperty", "C"), 3: ("ParserType", "ArrayProperty", "C")}, {},
                           {("C", "ParserType"): ("ArrayProperty", "array<PlaylistParser>")},
                           {}, {}, None, None, None, None, None, None, None, None,
                           lambda n: {"name": "string"} if n == "PlaylistParser" else None),
         ("", "array element: property: declared as a struct (`PlaylistParser`), not a class"))

    expr = {"name": "StructMember", "operands": b"\x01", "children": [{"name": "LocalVariable",
                                                                     "operands": b"\x01", "children": []}]}

    def read(export_at, members):
        # The struct table is keyed by name: a fixture that answered every name would make the member's own class
        # (`UIWidget`) look like a struct, which is exactly the distinction `_typed` now draws.
        owners = {(o or "").lower() for _n, _k, o in export_at.values()}

        def structs(name):
            return members if members is not None and (name or "").lower() in owners else None

        return _receiver_reading(expr, export_at, {}, {}, {}, None, None, None, None, None, None, None, None, None,
                                 structs)

    want("a member declared as a class names it",
         read({1: ("Face", "ObjectProperty", "SquadItemStruct")}, {"face": "UIWidget"}), ("UIWidget", ""))
    want("a member declared as a class<> names the inner class",
         read({1: ("Message", "ClassProperty", "LocalizedMessage")}, {"message": "class<LocalMessage>"}),
         ("LocalMessage", ""))
    want("a member declared as a scalar names no class",
         read({1: ("X", "IntProperty", "A")}, {"x": "int"}),
         ("", "struct member: declared, but not as a class"))
    want("a struct that declares the member twice names no class",
         read({1: ("X", "IntProperty", "A")}, {"x": None}),
         ("", "struct member: the struct declares it with two types"))
    want("a member the struct does not declare",
         read({1: ("X", "IntProperty", "A")}, {"y": "int"}),
         ("", "struct member: the struct does not declare it"))
    want("a struct no class text declares", read({1: ("X", "IntProperty", "A")}, None),
         ("", "struct member: no class text declares the struct"))
    want("a member that arrives as an import names its struct through the import's own outer",
         _receiver_reading({"name": "StructMember", "operands": b"\x81", "children": []},
                           {}, {1: ("Message", "ClassProperty")}, {}, {}, None, {1: "HUDLocalizedMessage"},
                           None, None, None, None, None, None, None,
                           lambda n: ({"message": "class<LocalMessage>"}
                                      if (n or "").lower() == "hudlocalizedmessage" else None)),
         ("LocalMessage", ""))
    want("a member whose record is absent from the package",
         _receiver_reading({"name": "StructMember", "operands": b"\x09", "children": []},
                           {1: ("X", "IntProperty", "A")}, {}, {}, {}, None, None, None, None, None, None, None,
                           None, None, lambda _name: {"x": "int"}),
         ("", "struct member: the operand names no record"))

    for one in fails:
        print("FAIL " + one)
    print("%d of %d struct assertions pass" % (ran[0] - len(fails), ran[0]))
    return 1 if fails else 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--hex", help="a script as hex")
    ap.add_argument("--file", help="a script as a binary file")
    ap.add_argument("--selftest", action="store_true")
    ap.add_argument("--check", action="store_true",
                    help="self-test the struct reading on synthetic texts (no dump, writes nothing)")
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
    ap.add_argument("--shuffle", action="store_true",
                    help="with --calls: a control, each class's declared types rotated onto a neighbouring name")
    ap.add_argument("--class-refs", metavar="PATH",
                    help="which classes the scripts name in code: class literals and cast targets")
    ap.add_argument("--resolve", metavar="PATH",
                    help="for each index operand, what the value points at (a check on the operand rules)")
    ap.add_argument("--switches", metavar="PATH",
                    help="what a 0x05 switch's operand byte is: the width of the value it compares")
    ap.add_argument("--highnative", metavar="PATH",
                    help="census the HighNative band (0x60..0x6f): which native each token calls "
                         "(console report; inventory/native_opcodes.csv is the full row set)")
    ap.add_argument("--usage", metavar="PATH",
                    help="which opcodes the shipped scripts that walk clean actually use")
    ap.add_argument("-o", "--out", help="write inventory/opcode_usage.csv into this directory (with --usage)")
    ap.add_argument("--start", metavar="PATH",
                    help="three readings of where a script starts: payload floor, walk depth, the dword's meaning")
    ap.add_argument("--start-scan", metavar="PATH",
                    help="walk every start in name_end+8..+40 and report which deltas reach the epilogue")
    ap.add_argument("--start-anchor", metavar="PATH",
                    help="anchor the start on the opcode the shipped ScriptText fixes for the first statement")
    ap.add_argument("--scan-lo", type=int, default=8, help="first delta --start-scan tries (default 8)")
    ap.add_argument("--scan-hi", type=int, default=40, help="last delta --start-scan tries (default 40)")
    ap.add_argument("--lists", type=int, default=0, metavar="N",
                    help="with --calls, --start-anchor or --sizefield: name up to N rows of each residue instead of counting them")
    ap.add_argument("--payloads", metavar="PACKAGE", help="measure the payload trailer of a package's Function exports")
    ap.add_argument("--sizefield", metavar="PATH",
                    help="what the dword 16 bytes before a script counts: the script with every "
                         "compact index widened to four bytes")
    ap.add_argument("--diverge", metavar="PATH",
                    help="which operand, if any, accounts for a --sizefield residue row (one round of every "
                         "operand tried, plus a rule test over the whole corpus)")
    ap.add_argument("--residue-csv", metavar="PATH",
                    help="with --sizefield: write the residue rows in the shape script-ram.py --residue reads")
    args = ap.parse_args()
    if args.shuffle and not args.calls:
        ap.error("--shuffle is a control on --calls: give it a package or a System directory")

    if args.check:
        return struct_check()
    if args.header:
        return header_report(args.header)
    if args.start:
        return start_report(args.start)
    if args.start_scan:
        return scan_report(args.start_scan, args.scan_lo, args.scan_hi)
    if args.start_anchor:
        return anchor_report(args.start_anchor, args.scan_lo, args.scan_hi, args.lists)
    if args.script:
        return function_script(args.script[0], args.script[1], args.script[2], Table(), hex_bytes=args.hex_bytes)
    if args.switches:
        return switches_report(args.switches, Table(), args.out)
    if args.usage:
        return usage_report(args.usage, Table(), args.out)
    if args.highnative:
        return highnative_report(args.highnative, Table())
    if args.resolve:
        return resolve_report(args.resolve, Table())
    if args.native_calls:
        return native_calls_report(args.native_calls, Table(), args.out)
    if args.calls:
        global _SHUFFLE
        _SHUFFLE = args.shuffle
        if _SHUFFLE:
            print("CONTROL RUN: each class's declared local and return types are rotated onto a neighbouring "
                  "name, so no placement below can be attributed to a declaration")
        return calls_report(args.calls, Table(), args.out, args.lists)
    if args.class_refs:
        return class_refs_report(args.class_refs, Table(), args.out)
    if args.compare:
        return compare_report(args.compare[0], args.compare[1], Table(), args.out)
    if args.sizefield:
        return sizefield_report(args.sizefield, args.lists, args.residue_csv)
    if args.diverge:
        return diverge_report(args.diverge, args.lists)
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
