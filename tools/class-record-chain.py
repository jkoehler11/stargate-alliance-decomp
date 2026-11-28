#!/usr/bin/env python3
"""Walk the class record end to end from the writer's order, with no anchor on the closing run.

`class-record-group-anchor.py` looks for the array's head only where the element walk can end *exactly* on the
closing group's start, and that start comes from `class-tail-run.py`: the group is parsed between the last
`01 00 00 00` unit before the defaults block and that block. On 954 of the 2,139 located classes no head is found,
and **951 of those 954 are classes whose group does not parse** -- so on that population the group's *start*, not
the array, is what is in question.

The writer's order does not need either end first. `UClass::Serialize` (`0x0003a800`) calls `UState::Serialize`
(`0x000394c0`), which calls `UStruct::Serialize` (`0x000392b0`); the front's field list and widths are measured
(`docs/NATIVE_VM.md`, *The front is the writer's field list, twice over*). So the front can be read at the prefix's
end, the array at the front's end (`FUN_0003d900`: a compact count, then one compact reference plus two dwords per
element), and only then is there a group start to check: `class-tail-run.py`'s own grammar (names, reference, name,
list) has to close exactly on the defaults block start.

Each step is anchored on something outside the payload bytes:

* the front's name field is the class's **own export-name index**, read from the package's export table;
* the array's references are read against the package's export and import tables;
* the group's name indices are read against the package's name table, and its end is the block start that
  `class-prefix.py` located from the defaults side.

Three controls, each a walk whose answer has to be worse than the chain's:

* **one byte later** -- the whole chain from `prefix_end + 1`;
* **two bytes past the array** -- the group parsed from `array_end + 2`, the same shift the group anchor's null uses;
* **the modal front** -- the array taken at `prefix_end + 61` regardless of what the front decoded.

    python3 tools/class-record-chain.py --dump $SG1_DUMP --sweep -o inventory
    python3 tools/class-record-chain.py --report
    python3 tools/class-record-chain.py --dump $SG1_DUMP --class AmbientCreatures.AmbientCreature
    python3 tools/class-record-chain.py --check

Reads the packages and three committed artifacts; no boot, no value decoded, no name published.
"""
import argparse
import collections
import csv
import importlib.util
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from ue2pkg import Package  # noqa: E402

ROOT = os.path.dirname(HERE)
FRONT_DWORDS = 4 + 8 + 4           # `+0x5c`, then `+0x58`/`+0x54`, then the script's length
STATE_BYTES = 8 + 8 + 2 + 4        # `+0x80`, `+0x88`, `+0x94`, `+0x90`
CLASS_DWORDS = 4 + 16              # the flags word and `+0x4a0`..`+0x4ac`
FIXED_TAIL = FRONT_DWORDS + STATE_BYTES + CLASS_DWORDS
ELEMENT_BODY = 8                   # the two dwords `FUN_0003d900` streams after each reference
MAX_FIELD = 5                      # one compact field is 1 to 5 bytes
MAX_COUNT = 8                      # the array holds 2 on 1,184 of 1,185 anchored rows, 5 on one
SEARCH_LO, SEARCH_HI = 56, 80      # where a front's end can sit, in bytes after the prefix
GROUP_MAX = 64                     # the group is 6-11 bytes on the anchored rows; a walk that needs more failed
MODAL_FRONT = 61                   # the front's modal length over the anchored rows
CTL_SHIFT = 2                      # the group's null aim, in bytes past the array
ARTIFACT = "class_record_chain.csv"
COLUMNS = ["package", "class", "payload", "prefix_end", "block_start", "x", "x_len", "name", "name_len",
           "name_matches", "front_len", "head_tail", "head_walk", "head_match", "heads", "count", "ref_widths",
           "array_len", "array_end", "group_start_tail", "group_off", "verdict_tail", "verdict_walk", "group_len",
           "chain", "heads_all", "head_index", "best_end", "best_delta", "best_count", "ctl_offsets", "ctl_shift",
           "ctl_aim", "ctl_modal", "slack", "slack_hex"]

_TR = None
_GA = None


def tail_run():
    """`tools/class-tail-run.py` (hyphenated), loaded on first use: its group grammar is the last link."""
    global _TR
    if _TR is None:
        path = os.path.join(HERE, "class-tail-run.py")
        spec = importlib.util.spec_from_file_location("class_tail_run", path)
        _TR = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_TR)
    return _TR


def group_anchor():
    """`tools/class-record-group-anchor.py`, for its `array_heads` scanner (the same rule the anchor uses)."""
    global _GA
    if _GA is None:
        path = os.path.join(HERE, "class-record-group-anchor.py")
        spec = importlib.util.spec_from_file_location("group_anchor_tool", path)
        _GA = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_GA)
    return _GA


def read_index(buf, off):
    """UE2 `FCompactIndex`: bit 7 is the sign, bit 6 means a byte follows, 6 bits then 7 per byte."""
    b = buf[off]
    sign, cont, val = b & 0x80, b & 0x40, b & 0x3F
    off += 1
    shift = 6
    while cont and shift < 32:
        b = buf[off]
        off += 1
        cont = b & 0x80
        val |= (b & 0x7F) << shift
        shift += 7
    return (-val if sign else val), off


def read_front(data, start, name_index=None):
    """The front's fields in the writer's order, read without a known end.

    Returns `(fields, end)`; `fields` is `None` and `end` a refusal string when a field overruns the payload or the
    name compact does not decode to `name_index` (which is read from the export table, not from the bytes).
    """
    out = {"x": "", "x_len": "", "name": "", "name_len": ""}
    try:
        out["x"], p = read_index(data, start)
        out["x_len"] = p - start
        if out["x_len"] > 3:
            return None, "x wider than 3"
        out["name"], q = read_index(data, p)
        out["name_len"] = q - p
        if out["name_len"] > MAX_FIELD:
            return None, "name wider than 5"
        if name_index is not None and out["name"] != name_index:
            return None, "name is not the class"
        if q + FRONT_DWORDS > len(data):
            return None, "overruns the payload"
        script_len = int.from_bytes(data[q + FRONT_DWORDS - 4:q + FRONT_DWORDS], "little")
        p = q + FRONT_DWORDS + script_len + STATE_BYTES + CLASS_DWORDS
        if p > len(data):
            return None, "script overruns the payload"
    except IndexError:
        return None, "overruns the payload"
    return out, p


def read_array(data, start, stop, nexp, nimp):
    """`FUN_0003d900`'s stream at `start`: a compact count, then one compact reference plus two dwords each.

    Returns `(count, widths, end)` or `(None, None, refusal)`.
    """
    try:
        count, o = read_index(data, start)
    except IndexError:
        return None, "count overruns", None
    if count <= 0 or count > MAX_COUNT:
        return None, "count %d outside 1..%d" % (count, MAX_COUNT), None
    widths = []
    for _ in range(count):
        try:
            ref, e = read_index(data, o)
        except IndexError:
            return None, "element overruns", None
        if e - o > MAX_FIELD:
            return None, "reference wider than 5", None
        if not (0 < ref <= nexp or -nimp <= ref < 0):
            return None, "reference outside the tables", None
        widths.append(e - o)
        o = e + ELEMENT_BODY
        if o > stop:
            return None, "array overruns", None
    return count, widths, o


def chain(data, pe, block, nexp, nimp, nnames, name_index):
    """The whole walk: front, array, group. Returns a dict of what each link answered."""
    out = {"x": "", "x_len": "", "name": "", "name_len": "", "name_matches": 0, "front_len": "",
           "count": "", "ref_widths": "", "array_len": "", "array_end": "", "verdict_walk": "", "group_len": "",
           "chain": ""}
    front, front_end = read_front(data, pe, name_index)
    if front is None:
        out["chain"] = "front: %s" % front_end
        return out
    out.update(front)
    out["name_matches"] = int(front["name"] == name_index)
    out["front_len"] = front_end - pe
    out["head_walk"] = out["front_len"]
    count, widths, array_end = read_array(data, front_end, block, nexp, nimp)
    if count is None:
        out["chain"] = "array: %s" % widths
        return out
    out["count"], out["ref_widths"] = count, "+".join(str(w) for w in widths)
    out["array_len"] = array_end - front_end
    out["array_end"] = array_end
    got = tail_run().parse_group(data, array_end, block, nnames)
    out["verdict_walk"] = tail_run().verdict(got)
    out["group_len"] = block - array_end
    if not got or not got["ok"]:
        out["chain"] = "group: %s" % out["verdict_walk"]
        return out
    out["chain"] = "close"
    return out


def search_end(data, pe, block, nexp, nimp, nnames, name_index, walked):
    """The first end in the front's own window where the array and the group both close.

    Only run on a row the writer's decoded fields do not close: the search is the measurement of *where* the front
    really ends, not a replacement for the grammar. Returns `(end - pe, count, n_closing_offsets)`; the count of
    closing offsets is the fit's own base rate and has to be 1.
    """
    hits = []
    for end in range(pe + SEARCH_LO, min(pe + SEARCH_HI, block) + 1):
        if end == walked:
            continue
        count, widths, array_end = read_array(data, end, block, nexp, nimp)
        if count is None:
            continue
        got = tail_run().parse_group(data, array_end, block, nnames)
        if got and got["ok"]:
            hits.append((end - pe, count))
    if not hits:
        return "", "", 0
    return hits[0][0], hits[0][1], len(hits)


def load_artifacts():
    def rows(name):
        with open(os.path.join(ROOT, "inventory", name), newline="", encoding="utf-8") as fh:
            return list(csv.DictReader(fh))
    # split names: a file another tool writes is credited to that tool, not to this one, by inventory-index.py
    bound = {(r["package"], r["class"]): r for r in rows("class_record_" "bound.csv")
             if r["prefix_end"] and r["block_start"]}
    anchor = {(r["package"], r["class"]): r for r in rows("class_record_group_" "anchor.csv")}
    return bound, anchor


def class_payloads(pkg):
    out = {}
    for exp in pkg.exports:
        if pkg.class_name_of(exp) == "Class":
            out[pkg.name(exp["name"])] = (exp["offset"], exp["size"], exp["name"])
    return out


def measure(data, row, anchor_row, nexp, nimp, nnames, name_index):
    pe, block = int(row["prefix_end"]), int(row["block_start"])
    out = {k: "" for k in COLUMNS}
    out.update({"prefix_end": pe, "block_start": block, "head_tail": "", "heads": "", "group_start_tail": "",
                "group_off": "", "verdict_tail": "", "ctl_shift": 0, "ctl_aim": 0, "ctl_modal": 0,
                "heads_all": "", "head_index": "", "best_end": "", "best_delta": "", "best_count": "",
                "ctl_offsets": "", "slack": "", "slack_hex": ""})
    if anchor_row:
        out["heads"] = anchor_row.get("heads", "")
        out["head_tail"] = anchor_row.get("head", "")
        out["group_start_tail"] = anchor_row.get("group_start", "")
        out["verdict_tail"] = anchor_row.get("group_verdict", "")
    got = chain(data, pe, block, nexp, nimp, nnames, name_index)
    out.update(got)
    out["head_match"] = int(out["head_walk"] != "" and out["head_tail"] != ""
                            and int(out["head_walk"]) == int(out["head_tail"]))
    if out["array_end"] != "" and out["group_start_tail"] != "":
        out["group_off"] = int(out["array_end"]) - int(out["group_start_tail"])
    if out["front_len"] != "" and out["head_tail"] != "":
        out["slack"] = int(out["head_tail"]) - int(out["front_len"])
        lo = pe + int(out["front_len"])
        hi = pe + int(out["head_tail"])
        out["slack_hex"] = data[lo:hi].hex() if 0 <= lo <= hi <= len(data) else ""
    # controls: the same chain one byte later, the group aimed two bytes past the array, and the array at the
    # front's modal length instead of the length it decoded
    heads = (group_anchor().array_heads(data, pe, int(out["group_start_tail"]), nexp, nimp)
             if out["group_start_tail"] != "" else [])
    out["heads_all"] = len(heads)
    if out["head_walk"] != "" and heads:
        offs = [h[0] - pe for h in heads]
        out["head_index"] = offs.index(int(out["head_walk"])) if int(out["head_walk"]) in offs else ""
    if out["chain"] != "close" and out["front_len"] != "":
        end, count, hits = search_end(data, pe, block, nexp, nimp, nnames, name_index, pe + int(out["front_len"]))
        out["best_end"], out["best_count"] = end, count
        out["best_delta"] = (int(end) - int(out["front_len"])) if end != "" else ""
        out["ctl_offsets"] = hits
    out["ctl_shift"] = int(chain(data, pe + 1, block, nexp, nimp, nnames, name_index)["chain"] == "close")
    if out["array_end"] != "":
        got2 = tail_run().parse_group(data, int(out["array_end"]) + CTL_SHIFT, block, nnames)
        out["ctl_aim"] = int(bool(got2) and got2["ok"])
    modal_end = pe + MODAL_FRONT
    count, widths, mend = read_array(data, modal_end, block, nexp, nimp)
    if count is not None:
        got3 = tail_run().parse_group(data, mend, block, nnames)
        out["ctl_modal"] = int(bool(got3) and got3["ok"])
    return out


def sweep(args):
    bound, anchor = load_artifacts()
    by_pkg = collections.defaultdict(list)
    for key in bound:
        by_pkg[key[0]].append(key)
    rows = []
    for name in sorted(by_pkg):
        pkg = Package(os.path.join(args.dump, "System", name + ".u"))
        pkg.read_names()
        pkg.read_imports()
        pkg.read_exports()
        payloads = class_payloads(pkg)
        nnames, nimp, nexp = len(pkg.names), len(pkg.imports), len(pkg.exports)
        for key in sorted(by_pkg[name]):
            if key[1] not in payloads:
                continue
            off, size, name_index = payloads[key[1]]
            data = pkg.data[off:off + size]
            row = measure(data, bound[key], anchor.get(key), nexp, nimp, nnames, name_index)
            row.update({"package": name, "class": key[1], "payload": size})
            rows.append(row)
    if args.out:
        os.makedirs(args.out, exist_ok=True)
        path = os.path.join(args.out, ARTIFACT)
        with open(path, "w", newline="", encoding="utf-8") as fh:
            w = csv.DictWriter(fh, fieldnames=COLUMNS)
            w.writeheader()
            w.writerows({k: r.get(k, "") for k in COLUMNS} for r in rows)
        print("wrote %s: %d rows" % (path, len(rows)))
    report(rows)
    return 0


def _share(rows, key, pred=lambda v: True):
    return collections.Counter(r[key] for r in rows if r[key] != "" and pred(r[key])).most_common(8)


def report(rows=None):
    if rows is None:
        with open(os.path.join(ROOT, "inventory", ARTIFACT), newline="", encoding="utf-8") as fh:
            rows = list(csv.DictReader(fh))
    total = len(rows)
    closed = [r for r in rows if r["chain"] == "close"]
    print("located classes walked front -> array -> group: %d" % total)
    print("the chain closes on the defaults block: %d (%.1f%%)" % (len(closed), 100.0 * len(closed) / max(1, total)))
    print("where it stops: %s" % _share(rows, "chain"))
    print("front lengths: %s" % _share(rows, "front_len"))
    print("array counts: %s; element width pairs: %s" % (_share(rows, "count"), _share(rows, "ref_widths")))
    print("the front's name is the class's own export-name index: %d of %d"
          % (sum(int(r["name_matches"]) for r in rows if r["name_matches"] != ""), total))
    for label, pop in (("anchored rows (a head from the group)", [r for r in rows if r["head_tail"] != ""]),
                       ("head-less rows", [r for r in rows if r["head_tail"] == ""])):
        if not pop:
            continue
        c = [r for r in pop if r["chain"] == "close"]
        print("\n%s: %d rows, the chain closes on %d (%.1f%%); their tail verdicts %s"
              % (label, len(pop), len(c), 100.0 * len(c) / len(pop), _share(pop, "verdict_tail")))
        print("  stops: %s" % _share(pop, "chain"))
    a = [r for r in rows if r["head_tail"] != ""]
    print("\non the %d anchored rows: the walked array head equals the anchored one on %d"
          % (len(a), sum(int(r["head_match"]) for r in a)))
    print("  the walk's head against all heads the anchor finds (head_index): %s" % _share(a, "head_index"))
    two = [r for r in a if r["heads"] == "2"]
    print("  the %d two-head rows: the walk's head is head %s"
          % (len(two), _share(two, "head_index")))
    hl = [r for r in rows if r["head_tail"] == "" and r["group_start_tail"] != ""]
    print("\nhead-less rows with a group start: %d; their group verdicts from the tail: %s"
          % (len(hl), _share(hl, "verdict_tail")))
    print("  read at the array's end instead: %s; the tails' group start against that end: %s"
          % (_share(hl, "verdict_walk"), _share(hl, "group_off")))
    print("  of them the chain closes on %d" % sum(1 for r in hl if r["chain"] == "close"))
    res = [r for r in rows if r["chain"] != "close" and r["best_end"] != ""]
    print("\nthe %d rows the decoded front does not close, a searched end in [%d, %d] closes: "
          "%d; the end it finds against the decoded one: %s"
          % (sum(1 for r in rows if r["chain"] != "close"), SEARCH_LO, SEARCH_HI, len(res),
             _share(res, "best_delta")))
    print("  closing offsets per recovered row: %s" % _share(res, "ctl_offsets"))
    print("\nthe front's residual against the anchored head (anchored head - walked front): %s"
          % _share([r for r in rows if r["slack"] != ""], "slack"))
    odd = [r for r in rows if r["slack"] not in ("", "0")]
    print("  the %d rows with one: bytes at the join: %s" % (len(odd), _share(odd, "slack_hex")))
    print("\ncontrols: one byte later closes %d; the group aimed %d bytes past the array parses on %d; "
          "the array at the modal front (%d bytes) closes the chain on %d"
          % (sum(int(r["ctl_shift"]) for r in rows), CTL_SHIFT, sum(int(r["ctl_aim"]) for r in rows),
             MODAL_FRONT, sum(int(r["ctl_modal"]) for r in rows)))
    return 0


def one(args):
    if "." in args.cls and not args.package:
        args.package, args.cls = args.cls.split(".", 1)
    pkg = Package(os.path.join(args.dump, "System", args.package + ".u"))
    pkg.read_names()
    pkg.read_imports()
    pkg.read_exports()
    payloads = class_payloads(pkg)
    if args.cls not in payloads:
        print("%s.%s has no class payload" % (args.package, args.cls))
        return 1
    bound, anchor = load_artifacts()
    key = (args.package, args.cls)
    off, size, name_index = payloads[args.cls]
    data = pkg.data[off:off + size]
    got = measure(data, bound[key], anchor.get(key), len(pkg.exports), len(pkg.imports), len(pkg.names),
                  name_index)
    for k in COLUMNS:
        print("%-17s %s" % (k, got.get(k, "")))
    pe = int(bound[key]["prefix_end"])
    end = pe + int(got["front_len"]) if got["front_len"] != "" else pe + MODAL_FRONT
    print("front bytes     %s" % data[pe:end].hex(" "))
    print("array bytes     %s" % data[end:end + 24].hex(" "))
    return 0


def self_check():
    """A synthetic class payload: forty-two bytes of prefix, the writer's front, a two-record array, a group."""
    def compact(v):
        if v < 0x40:
            return bytes([v])
        if v < 0x2000:
            return bytes([(v & 0x3F) | 0x40, v >> 6])
        raise ValueError(v)

    names = ["None", "Alpha", "Beta", "Gamma", "Delta"]
    names_n = len(names)
    front = compact(0) + compact(1)                        # the open compact, the FriendlyName
    front += bytes(4) + b"\xff" * 8 + (0).to_bytes(4, "little")
    front += bytes(8) + b"\xff" * 8 + b"\xff\xff" + bytes(4)
    front += (0x12).to_bytes(4, "little") + bytes(16)
    assert len(front) == 60, len(front)
    array = b"\x02" + b"\x02" + b"\x11" * 8 + b"\x83" + b"\x22" * 8
    group = b"\x02\x01\x02\x83\x03\x00"
    body = bytes(4) + front + array + group
    pe, block = 4, len(body)
    got = chain(body, pe, block, nexp=9, nimp=9, nnames=names_n, name_index=1)
    assert got["chain"] == "close", got
    assert got["name"] == 1 and got["name_matches"] == 1 and got["front_len"] == 60, got
    assert got["count"] == 2 and got["ref_widths"] == "1+1", got
    assert got["array_end"] == pe + 60 + 19 and got["group_len"] == 6, got
    # the chain refuses a front whose name is not the class, an out-of-table reference, and a group that does
    # not end on the block
    assert chain(body, pe, block, 9, 9, names_n, 2)["chain"] == "front: name is not the class"
    bad = bytearray(body)
    bad[pe + 60 + 10] = 0x7F
    assert chain(bytes(bad), pe, block, 9, 9, names_n, 1)["chain"].startswith("array: reference outside"), \
        chain(bytes(bad), pe, block, 9, 9, names_n, 1)
    assert chain(body, pe, block - 1, 9, 9, names_n, 1)["chain"].startswith("group:"), \
        chain(body, pe, block - 1, 9, 9, names_n, 1)
    # an array count outside the grammar is a refusal, not a scan
    bad2 = bytearray(body)
    bad2[pe + 60] = 0x09
    assert chain(bytes(bad2), pe, block, 9, 9, names_n, 1)["chain"].startswith("array: count"), \
        chain(bytes(bad2), pe, block, 9, 9, names_n, 1)
    # the two controls: one byte later never closes, and the group aimed two bytes past never parses
    assert chain(body, pe + 1, block, 9, 9, names_n, 1)["chain"] != "close"
    aimed = tail_run().parse_group(body, pe + 60 + 19 + CTL_SHIFT, block, names_n)
    assert not aimed or not aimed["ok"]
    print("ok   class-record-chain: the front, array and group close on a synthetic payload; a wrong name, an "
          "out-of-table reference, an out-of-grammar count and a short block all refuse; both nulls miss")
    return 0


def main():
    global MAX_COUNT
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dump", default=os.environ.get("SG1_DUMP", ""))
    ap.add_argument("--sweep", action="store_true")
    ap.add_argument("-o", "--out")
    ap.add_argument("--class", dest="cls", help="one class, `Package.Class` or with --package")
    ap.add_argument("--package", default="")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--max-count", type=int, default=MAX_COUNT, help="the array's count bound (default %d)" % MAX_COUNT)
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    if args.check:
        return self_check()
    MAX_COUNT = args.max_count
    if args.report:
        return report()
    if not args.dump:
        ap.error("--dump is required (or --check/--report)")
    if args.cls:
        if not args.package and "." not in args.cls:
            ap.error("--class needs `Package.Class` or --package")
        return one(args)
    if args.sweep:
        return sweep(args)
    ap.error("pick --sweep, --class or --report")


if __name__ == "__main__":
    sys.exit(main())
