#!/usr/bin/env python3
"""Name the callers of the two slot-19/20 bodies from a booted guest's breakpoint record (U8).

`inventory/class_table_slot_pairs.csv` (PLAN 4.3) measured the *installation* of slots 19 and 20: they are
one pair of addresses across all 492 tables -- `0x00020f00` twice (a one-byte `ret`) in 327, and
`0x000c4f60`/`0x000c6d00` in 165, all of them under `Actor`. What the pair *is* cannot be read off `.text`:
82 `call [reg+0x4c]` / `call [reg+0x50]` sites exist and the displacement is not unique to this table, so
each needs its receiver typed first (`docs/UNKNOWNS.md` U8). This tool is the runtime half of that: it turns
a `tools/xemu/bp-args.py` record taken with execution breakpoints on the two bodies into (caller, receiver
class) rows, which is the one thing the static scan cannot produce.

A stop is read in three steps, each of which can fail alone and is reported as a named negative rather than
a zero:

* **Caller.** At the first instruction of a `__thiscall`/`__stdcall` body the word at `ESP` is the return
  address, so `stack[0]` is the caller's continuation. It is resolved against `symbols/xbox_functions.csv`
  exactly when a function starts there, by containment when it lands inside a named body's size, and
  otherwise as `<nearest named function>+0x..` -- an unnamed callee still names its container.
* **Receiver.** A class's table is installed at `[this+0x00]` (`inventory/xbe_class_table_names.csv`), so a
  register whose first dword is a table address *is* a receiver. The record carries `ecx+0x0`, the
  `_thiscall` register; the stack words are printed by the same tool but cannot be dereferenced offline.
* **Which virtual.** `inventory/class_table_slot_pairs.csv` says which of the two addresses is slot 19 and
  which is slot 20, so every row is labelled with the slot its breakpoint is.

Nothing here reads guest memory or a dump: the input is the address/register JSON the stub produced, which
is a run artifact under `$WORK`, not committed. The committed artifact is the derived
`inventory/vcall_slot_stops.csv`. `--check` builds a synthetic record and answers without either.

    python3 tools/vcall-stops.py --json $WORK/vslot2/stops.json
    python3 tools/vcall-stops.py --json ... --csv /tmp/x.csv
    python3 tools/vcall-stops.py --check          # synthetic self-test, no dump, writes nothing
"""
import argparse
import csv
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(HERE)
FUNCTIONS = os.path.join(REPO, "symbols", "xbox_functions.csv")
TABLES = os.path.join(REPO, "inventory", "xbe_class_tables.csv")
NAMES = os.path.join(REPO, "inventory", "xbe_class_table_names.csv")
PAIRS = os.path.join(REPO, "inventory", "class_table_slot_pairs.csv")
CSV = os.path.join(REPO, "inventory", "vcall_slot_stops.csv")

HEADER = ["target", "slot", "stop", "caller_eip", "caller_symbol", "this_va", "table_va", "class"]


def read_csv(path, column):
    rows = []
    with open(path, newline="") as fh:
        for row in csv.DictReader(fh):
            value = row.get(column, "").strip()
            if value:
                rows.append(row)
    return rows


def load_functions(path):
    """address -> (name, size). Sizes are what makes `inside a named body` answerable."""
    out = {}
    for row in read_csv(path, "address"):
        try:
            va = int(row["address"], 0)
        except ValueError:
            continue
        try:
            size = int(row.get("size") or 0, 0)
        except ValueError:
            size = 0
        out[va] = (row.get("name", ""), size)
    return out


def load_tables(path):
    out = {}
    for row in read_csv(path, "table_va"):
        try:
            out[int(row["table_va"], 0)] = row
        except ValueError:
            continue
    return out


def load_names(path):
    out = {}
    for row in read_csv(path, "table_va"):
        name = row.get("class", "").strip()
        if name:
            try:
                out[int(row["table_va"], 0)] = name
            except ValueError:
                continue
    return out


def load_slots(path):
    """body address -> the slot(s) that hold it."""
    out = {}
    for row in read_csv(path, "table_va"):
        for slot in ("19", "20"):
            value = (row.get("slot" + slot) or "").strip()
            if not value:
                continue
            try:
                out.setdefault(int(value, 0), set()).add(int(slot))
            except ValueError:
                continue
    return out


def resolve_caller(eip, functions):
    """Exact start, then containment in a sized body, then `<nearest below>+0x..`."""
    if eip in functions:
        return functions[eip][0]
    best = None
    for va, (name, size) in functions.items():
        if va <= eip and size and eip < va + size:
            if best is None or va > best[0]:
                best = (va, name)
    if best:
        return "%s+0x%x" % (best[1], eip - best[0])
    below = None
    for va in functions:
        if va <= eip and (below is None or va > below):
            below = va
    if below is None:
        return "?"
    return "%s+0x%x" % (functions[below][0], eip - below)


def receiver(stop, tables, names):
    """The `ecx+0x0` dump is `_thiscall`'s receiver: its first dword is the class table."""
    for dump in stop.get("dumps", []):
        if dump.get("expr") != "ecx+0x0":
            continue
        words = dump.get("words") or []
        if not words:
            return None, None, None
        try:
            table = int(words[0], 16)
        except ValueError:
            return None, None, None
        if table not in tables:
            return None, None, None
        this = dump.get("addr")
        return this, table, names.get(table, "")
    return None, None, None


def read_stops(path):
    with open(path) as fh:
        return json.load(fh)


def rows_from(record, functions, tables, names, slots):
    rows = []
    for index, stop in enumerate(record.get("stops", [])):
        if not stop.get("hit"):
            continue
        try:
            target = int(stop["regs"]["eip"])
        except (KeyError, ValueError, TypeError):
            continue
        stack = stop.get("stack") or []
        caller = None
        if stack:
            try:
                caller = int(stack[0], 16)
            except ValueError:
                caller = None
        this, table, cls = receiver(stop, tables, names)
        slot = sorted(slots.get(target, []))
        rows.append({
            "target": "0x%08x" % target,
            "slot": "/".join(str(s) for s in slot) if slot else "",
            "stop": index,
            "caller_eip": "0x%08x" % caller if caller is not None else "",
            "caller_symbol": resolve_caller(caller, functions) if caller is not None else "",
            "this_va": "0x%08x" % this if isinstance(this, int) else "",
            "table_va": "0x%08x" % table if table else "",
            "class": cls or "",
        })
    return rows


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--json", help="the bp-args.py record of one boot")
    ap.add_argument("--csv", default=CSV, help="where to write the derived artifact")
    ap.add_argument("--functions", default=FUNCTIONS)
    ap.add_argument("--tables", default=TABLES)
    ap.add_argument("--names", default=NAMES)
    ap.add_argument("--pairs", default=PAIRS)
    ap.add_argument("--check", action="store_true", help="synthetic self-test; no boot, writes nothing")
    args = ap.parse_args()

    if args.check:
        functions = {0x1000: ("FUN_00001000", 0x40), 0x1040: ("AActor::Tick", 0x100)}
        tables = {0x4C9348: {"table_va": "0x004c9348"}}
        names = {0x4C9348: "Player"}
        slots = {0xC4F60: {19}, 0xC6D00: {20}}
        record = {"stops": [
            {"hit": True, "regs": {"eip": 0xC4F60}, "stack": ["0x00001020", "0x0"],
             "dumps": [{"expr": "ecx+0x0", "addr": 0x1A0000, "words": ["0x004c9348", "0x0"]}]},
            {"hit": True, "regs": {"eip": 0xC6D00}, "stack": ["0x00001040"],
             "dumps": [{"expr": "ecx+0x0", "addr": 0x1A0000, "words": ["0x004c9348"]}]},
            {"hit": True, "regs": {"eip": 0xC6D00}, "stack": ["0x00009000"],
             "dumps": [{"expr": "ecx+0x0", "addr": 0x2A0000, "words": ["0x00410000"]}]},
            {"hit": False, "regs": {"eip": 0x2000000}, "stack": [], "dumps": []},
        ]}
        rows = rows_from(record, functions, tables, names, slots)
        problems = []
        if len(rows) != 3:
            problems.append("a not-mine stop was kept as a hit (%d rows)" % len(rows))
        else:
            if rows[0]["slot"] != "19":
                problems.append("slot label %r, wanted '19'" % rows[0]["slot"])
            if rows[0]["class"] != "Player":
                problems.append("receiver class %r, wanted 'Player'" % rows[0]["class"])
            # 0x1020 sits inside the 0x40-byte body at 0x1000: containment, not "nearest below".
            if rows[0]["caller_symbol"] != "FUN_00001000+0x20":
                problems.append("contained caller %r" % rows[0]["caller_symbol"])
            # 0x1040 is the start of a named body: exact.
            if rows[1]["caller_symbol"] != "AActor::Tick":
                problems.append("exact caller %r" % rows[1]["caller_symbol"])
            # 0x9000 is outside every body: nearest-below fallback, unknown name.
            if not rows[2]["caller_symbol"].endswith("+0x7fc0"):
                problems.append("nearest-below caller %r" % rows[2]["caller_symbol"])
            if rows[2]["class"] != "":
                problems.append("a table outside xbe_class_tables.csv got a name %r" % rows[2]["class"])
        if rows and rows[1]["slot"] != "20":
            problems.append("slot label %r for the 0x000c6d00 body, wanted '20'" % rows[1]["slot"])
        for problem in problems:
            print("FAIL: " + problem)
        if problems:
            return 1
        print("ok: 3 of 4 stops are the armed bodies; caller resolution exact/contained/nearest all "
              "answer; an unknown table stays unnamed")
        return 0

    if not args.json:
        ap.error("--json is required unless --check")
    for path in (args.functions, args.tables, args.names, args.pairs):
        if not os.path.exists(path):
            print("missing " + path, file=sys.stderr)
            return 2
    functions = load_functions(args.functions)
    tables = load_tables(args.tables)
    names = load_names(args.names)
    slots = load_slots(args.pairs)
    record = read_stops(args.json)
    rows = rows_from(record, functions, tables, names, slots)
    with open(args.csv, "w", newline="") as fh:
        writer = csv.DictWriter(fh, fieldnames=HEADER)
        writer.writeheader()
        writer.writerows(rows)

    breaks = record.get("breaks") or []
    print("breaks armed: %s; stops recorded: %d; of them at an armed body: %d"
          % (", ".join(str(b) for b in breaks) or "none", len(record.get("stops", [])), len(rows)))
    if not rows:
        print("no armed body was reached: the boot measured that this pair does not dispatch in the "
              "window it covered -- see the doc for what that rules out")
        return 0
    counts = {}
    for row in rows:
        key = (row["slot"], row["caller_symbol"], row["class"] or "?")
        counts[key] = counts.get(key, 0) + 1
    for (slot, caller, cls), n in sorted(counts.items()):
        print("slot %-2s caller %-32s receiver %-20s %d stop(s)" % (slot, caller, cls, n))
    print("wrote %s (%d rows)" % (args.csv, len(rows)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
