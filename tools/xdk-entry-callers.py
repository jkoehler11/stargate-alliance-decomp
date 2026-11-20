#!/usr/bin/env python3
"""Who calls an XDK entry point, and how the caller itself is reached.

The audio stack reading ended with one open question: 303 methods are attributed to `dsound.lib` and
`xvoice.lib`, but **which body creates the game's own DirectSound device** is not a library method at
all -- `module_export_census.csv` indexes UE2 exports and holds none of these addresses. A direct call
is `E8 rel32`, so it is invisible to a dword scan, and a virtual method's body is stored only in its
class table.

So this tool answers both halves for a set of entry points (default: the three `_DirectSound*` entry
points the voice modes and the engine both call):

1. every `E8 rel32` in an executable section whose target is the entry point -- the direct call sites,
   counted only where a forward decode from the window behind them lands exactly on the call
   (`tools/record-writer-calls.py`'s boundary rule, through the same `tools/vcall-receiver.py` decoder);
2. for each caller body, **how that body is reached**: a `call [reg+disp]`-style virtual through a known
   class table (the slot and the class), a dword reference, or its own direct callers -- so an unnamed
   body that nothing calls but a vtable does not read as unreachable.

    python3 tools/xdk-entry-callers.py --image default.xbe -o inventory
    python3 tools/xdk-entry-callers.py --report
    python3 tools/xdk-entry-callers.py --check        # committed artifact, no dump, writes nothing

Reads the image and the committed symbol/classtable inventories; no boot, no Ghidra round.
"""
import argparse
import collections
import csv
import importlib.util
import os
import struct
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from xbe import Xbe  # noqa: E402

ROOT = os.path.dirname(HERE)
ARTIFACT = "xdk_entry_callers.csv"
TARGETS = ["_DirectSoundCreate@12", "_DirectSoundCreateStream@8", "_DirectSoundDoWork@0"]
WINDOW = 48
MIN_CHAIN = 2
COLUMNS = ["entry_name", "entry", "site", "caller", "caller_name", "caller_size", "module",
           "calls_in", "caller_slot", "caller_refs"]


def decoder():
    spec = importlib.util.spec_from_file_location("sg1_vcall_receiver", os.path.join(HERE, "vcall-receiver.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def candidates(blob, base_va):
    out = []
    for i in range(0, len(blob) - 4):
        if blob[i] != 0xE8:
            continue
        site = base_va + i
        rel = struct.unpack_from("<i", blob, i + 1)[0]
        out.append((site, (site + 5 + rel) & 0xFFFFFFFF, i))
    return out


def chain(blob, site_off, vr):
    """The instruction run that ends exactly on the call site, or None (longest clean decode wins)."""
    for back in range(WINDOW, 5, -1):
        start = site_off - back
        if start < 0:
            continue
        insns, i = [], start
        while i < site_off:
            insn = vr.decode(blob, i)
            if insn is None or insn.length <= 0:
                break
            insns.append((i, insn))
            i += insn.length
        if i == site_off and len(insns) >= MIN_CHAIN:
            return insns
    return None


def read_functions(path):
    out = []
    if not os.path.exists(path):
        return out
    with open(path, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            try:
                out.append((int(r["address"], 16), int(r["size"] or 0), r["name"]))
            except (KeyError, ValueError):
                continue
    out.sort()
    return out


def read_modules(path):
    out = {}
    if not os.path.exists(path):
        return out
    with open(path, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            mod = (r.get("module") or "").strip()
            if mod and mod != "(unnamed)":
                out[int(r["address"], 16)] = mod
    return out


def read_tables(path, names_path):
    tables, names = [], {}
    if os.path.exists(path):
        with open(path, newline="", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                try:
                    tables.append((int(r["table_va"], 16), int(r["slots"])))
                except (KeyError, ValueError):
                    continue
        tables.sort()
    if os.path.exists(names_path):
        with open(names_path, newline="", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                cls = (r.get("class") or "").strip()
                if cls:
                    try:
                        names[int(r["table_va"], 16)] = cls
                    except (KeyError, ValueError):
                        continue
    return tables, names


def slot_of(tables, va):
    for table_va, slots in tables:
        if table_va <= va < table_va + 4 * slots:
            return table_va, slots, (va - table_va) // 4
    return None


def owner(functions, va):
    """(address, name, size) of the function whose span holds `va`, or None."""
    best = None
    for addr, size, name in functions:
        if addr > va:
            break
        if size and va < addr + size:
            best = (addr, name, size)
    return best


def dword_refs(xbe, va):
    """Every 4-aligned dword equal to `va` in a file-backed section, as site addresses."""
    needle = struct.pack("<I", va)
    hits = []
    for s in xbe.sections:
        if s.fsize <= 0:
            continue
        blob = xbe.data[s.foff:s.foff + s.fsize]
        start = 0
        while True:
            i = blob.find(needle, start)
            if i < 0:
                break
            if i % 4 == 0:
                hits.append(s.va + i)
            start = i + 1
    return hits


def measure(image, targets, functions, modules, tables, names):
    vr = decoder()
    xbe = Xbe(image)
    wanted = set(targets.values())
    calls = collections.defaultdict(list)       # target va -> validated call sites
    for sec in xbe.sections:
        if not sec.executable or sec.fsize <= 0:
            continue
        blob = xbe.data[sec.foff:sec.foff + sec.fsize]
        for site, target, off in candidates(blob, sec.va):
            if target not in wanted:
                continue
            if chain(blob, off, vr) is None:
                continue
            calls[target].append(site)
    reach_cache = {}

    def reach(body):
        if body in reach_cache:
            return reach_cache[body]
        direct = len(calls.get(body, []))
        refs = dword_refs(xbe, body)
        slot = ""
        for ref in refs:
            hit = slot_of(tables, ref)
            if hit:
                table_va, slots, index = hit
                slot = "%s +0x%x (slot %d of %d)" % (names.get(table_va, "table 0x%08x" % table_va),
                                                     index * 4, index, slots)
                break
        reach_cache[body] = (direct, slot, refs)
        return reach_cache[body]

    rows = []
    for name, entry in targets.items():
        for site in sorted(calls.get(entry, [])):
            held = owner(functions, site)
            caller, caller_name, caller_size = (held if held else ("", "no known function", 0))
            direct, slot, refs = reach(caller) if caller else (0, "", [])
            rows.append({
                "entry_name": name,
                "entry": "0x%08x" % entry,
                "site": "0x%08x" % site,
                "caller": "" if not caller else "0x%08x" % caller,
                "caller_name": caller_name,
                "caller_size": caller_size,
                "module": modules.get(caller, ""),
                "calls_in": direct,
                "caller_slot": slot,
                "caller_refs": " ".join("0x%08x" % r for r in refs[:4]),
            })
    order = {name: i for i, name in enumerate(targets)}
    rows.sort(key=lambda r: (order[r["entry_name"]], r["site"]))
    return rows


def report(rows):
    by_entry = collections.OrderedDict()
    for r in rows:
        by_entry.setdefault((r["entry_name"], r["entry"]), []).append(r)
    for (name, entry), group in by_entry.items():
        print("%s (%s): %d call site(s)" % (name, entry, len(group)))
        for r in group:
            calls_in = int(r["calls_in"] or 0)
            how = r["caller_slot"] or ("%d direct caller(s)" % calls_in if calls_in else "no caller found")
            print("  0x%s  %-46s %-9s via %s" % (r["site"][2:], r["caller_name"][:46],
                                                 r["module"] or "-", how))


def check(path, symbols_path):
    """Validate the committed artifact against the committed symbol map -- no dump, no writes."""
    problems = []
    if not os.path.isfile(path):
        return ["missing artifact %s" % path]
    with open(path, newline="", encoding="utf-8") as fh:
        reader = csv.DictReader(fh)
        if reader.fieldnames != COLUMNS:
            return ["%s: columns are %r, expected %r" % (path, reader.fieldnames, COLUMNS)]
        rows = list(reader)
    functions = read_functions(symbols_path)
    spans = {addr: (name, size) for addr, size, name in functions}
    by_name = {name: addr for addr, _size, name in functions}
    names = [r["entry_name"] for r in rows]
    for want in TARGETS:
        if want not in names:
            problems.append("%s: no row for %s" % (path, want))
    for r in rows:
        try:
            site = int(r["site"], 16)
            entry = int(r["entry"], 16)
        except ValueError:
            problems.append("%s: unparsable row %r" % (path, r))
            continue
        if spans.get(entry) is None or by_name.get(r["entry_name"]) != entry:
            problems.append("%s: entry %s is not 0x%08x in the symbol map"
                            % (path, r["entry_name"], entry))
        if r["caller"]:
            caller = int(r["caller"], 16)
            name, size = spans.get(caller, ("", 0))
            if not name:
                problems.append("%s: caller 0x%08x is not in the symbol map" % (path, caller))
            elif name != r["caller_name"]:
                problems.append("%s: caller 0x%08x is %s, row says %s" % (path, caller, name, r["caller_name"]))
            elif size and not (caller <= site < caller + size):
                problems.append("%s: site 0x%08x is outside %s (0x%08x+%d)" % (path, site, name, caller, size))
        elif r["caller_name"] != "no known function":
            problems.append("%s: empty caller with name %r" % (path, r["caller_name"]))
    return problems


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--image", help="the dump's default.xbe")
    ap.add_argument("--symbols", default=os.path.join(ROOT, "symbols", "xbox_functions.csv"))
    ap.add_argument("--modules", default=os.path.join(ROOT, "inventory", "function_modules.csv"))
    ap.add_argument("--tables", default=os.path.join(ROOT, "inventory", "xbe_class_tables.csv"))
    ap.add_argument("--table-names", default=os.path.join(ROOT, "inventory", "xbe_class_table_names.csv"))
    ap.add_argument("--targets", help="comma-separated symbol names (default: %s)" % ", ".join(TARGETS))
    ap.add_argument("-o", "--out", default=os.path.join(ROOT, "inventory"))
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()

    path = os.path.join(args.out, ARTIFACT) if os.path.isdir(args.out) or not args.out.endswith(".csv") \
        else args.out
    if args.check:
        problems = check(path, args.symbols)
        if problems:
            for p in problems:
                print("FAIL xdk-entry-callers: %s" % p)
            return 1
        print("xdk-entry-callers: %s OK" % ARTIFACT)
        return 0

    wanted = (args.targets or ",".join(TARGETS)).split(",")
    functions = read_functions(args.symbols)
    by_name = {name: addr for addr, _size, name in functions}
    targets = collections.OrderedDict()
    for name in [w.strip() for w in wanted if w.strip()]:
        if name not in by_name:
            print("no symbol %r in %s" % (name, args.symbols))
            return 2
        targets[name] = by_name[name]
    if args.report:
        with open(path, newline="", encoding="utf-8") as fh:
            report(list(csv.DictReader(fh)))
        return 0
    if not args.image:
        print("--image is required")
        return 2
    modules = read_modules(args.modules)
    tables, names = read_tables(args.tables, args.table_names)
    rows = measure(args.image, targets, functions, modules, tables, names)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=COLUMNS)
        writer.writeheader()
        writer.writerows(rows)
    print("%d site(s) over %d entry point(s) -> %s" % (len(rows), len(targets), path))
    report(rows)
    return 0


if __name__ == "__main__":
    sys.exit(main())
