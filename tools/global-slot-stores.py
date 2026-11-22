#!/usr/bin/env python3
"""Global function-pointer slots, read at the store: who writes each slot, and what it holds.

`tools/gap-body-families.py` places a gap body that is installed into a global slot by a `mov DWORD PTR
ds:<global>,<body>` -- that store is the only reference the body has -- and stops there: it counts the
stores and the longest run of consecutive slots and says nothing about who makes them or what the table
is. This read takes a *slot window* and answers that per slot:

  * **the store.** A slot's address appears in `.text` in several instruction forms. Only a `c7 /0` store
    with a `disp32` destination writes it (`c7 05 <slot> <imm32>`, ten bytes); a `call DWORD PTR [<slot>]`
    reads it and is the form most of a table's occurrences take. Every candidate is decoded with `objdump`
    from the `c7 05` opcode two bytes before the address, and one whose bytes are that opcode but whose
    decode is not a store of *this* slot is reported as refused rather than dropped.
  * **the owner.** The symbol map's extents name the body the store sits in, which is the body that installs
    the entry -- one body usually installs a whole table.
  * **the default.** The file-backed dword at the slot is what the image ships; a store whose value differs
    is an *override* at startup, the pattern a middleware callback table patched by the game takes.
  * **the value.** The stored body's own name, and whether it is one of the generated `<Class>_lookup`
    walkers `inventory/gap_walker_names.csv` records.

The two windows this was written for are U40's last small channel: the nine slot stores the post-add census
leaves (one installer's stores into the MathEngine runtime table at `0x005ccadc`) and the 65 consecutive
walker slots at `0x00630e20` (one startup body's 68 stores). The answer to the question the census could not
ask -- "is this run of pointers a class table?" -- is in the shape: a run whose entries repeat, whose values
are bodies of one library family and which one installer fills is a callback table, and the
`int[AU]<Class>exec<Func>` grammar has no key for it.

    SG1_DUMP=$HOME/nfs/sg1-dump python3 tools/global-slot-stores.py \\
        --xbe "$SG1_DUMP/Stargate_SG-1_-_The_Alliance-2005-12-15/default.xbe" \\
        --slots 0x005ccadc:0x005ccb34 --slots 0x00630e20:0x00630f2c -o inventory
    python3 tools/global-slot-stores.py --report
    python3 tools/global-slot-stores.py --write-doc docs/systems/CLASS_REGISTRATION.md
    python3 tools/global-slot-stores.py --check-doc docs/systems/CLASS_REGISTRATION.md
    python3 tools/global-slot-stores.py --apply-symbols    # the recorded names become map rows
    python3 tools/global-slot-stores.py --check-symbols   # every recorded name is a map row (dump-free, in ci)
    python3 tools/global-slot-stores.py --check           # synthetic, no dump, writes nothing

Addresses, names and counts only; no image or package content is written.
"""
import argparse
import importlib.util
import os
import re
import struct
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SYMBOLS = os.path.join(ROOT, "symbols", "xbox_functions.csv")
OUT = os.path.join(ROOT, "inventory", "global_slot_stores.csv")
# The names this read derives, as a committed input: `body_va,size,name,evidence`. A record, not a vote --
# the tool applies it and gates it, and nothing derives a name from a byte again.
NAMES = os.path.join(ROOT, "inventory", "global_slot_names.csv")
WALKERS = os.path.join(ROOT, "inventory", "gap_walker_names.csv")
DOC = os.path.join(ROOT, "docs", "systems", "CLASS_REGISTRATION.md")
DOC_A = ("<!-- generated:global-slot-stores -->", "<!-- /generated:global-slot-stores -->")

BODY = ("window", "slot", "index", "site", "owner_va", "owner_name", "value", "value_name",
        "static_value", "static_name", "form", "note")
CENSUS = ("windows", "slots", "stores", "slots_written", "owners", "overrides", "unowned", "values_named",
          "values_walker", "runs", "run_slots", "run_len", "refused", "refused_forms", "window_text")
CENSUS_TEXT = ("window_text", "refused_forms")

# `c7 05 <disp32> <imm32>` -- the only form that writes an absolute slot.
STORE = re.compile(r"^mov DWORD PTR ds:0x([0-9a-f]+),0x([0-9a-f]+)$")
OPEN = re.compile(r"0x([0-9a-f]+):\t([0-9a-f ]+?)\s*\t(.*)$")


def read_csv(path):
    if not path or not os.path.isfile(path):
        return []
    import csv
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def load_symbols(path=None):
    names, sizes = {}, {}
    for row in read_csv(path or SYMBOLS):
        try:
            va = int(row["address"], 16)
        except (KeyError, ValueError):
            continue
        names[va] = row.get("name") or ""
        try:
            sizes[va] = int(row.get("size") or 0)
        except ValueError:
            sizes[va] = 0
    return names, sizes


def objdump(blob, base):
    """[(va, text)] from objdump over a byte window -- an independent decoder, at instruction boundaries."""
    with tempfile.NamedTemporaryFile(delete=False, suffix=".bin") as fh:
        fh.write(blob)
        temp = fh.name
    try:
        out = subprocess.run(["objdump", "-D", "-b", "binary", "-m", "i386", "-M", "intel",
                              "--adjust-vma=%d" % base, temp], capture_output=True, text=True).stdout
    finally:
        os.unlink(temp)
    insns = []
    for line in out.splitlines():
        if line.count("\t") < 2:
            continue
        head, rest = line.split("\t", 1)
        try:
            va = int(head.strip().rstrip(":"), 16)
        except ValueError:
            continue
        text = re.sub(r"\s+", " ", rest.split("\t")[-1].strip())
        if text and not text.startswith("."):
            insns.append((va, text))
    insns.sort()
    return insns


def dword(blob, offset):
    if offset < 0 or offset + 4 > len(blob):
        return None
    return struct.unpack("<I", blob[offset:offset + 4])[0]


def store_at(window, site, slot):
    """The value a candidate site stores into `slot`, or why it is not a store of it.

    `window` is twelve bytes whose first byte is at `site`. The form is `c7 05 <disp32> <imm32>`: the opcode
    two bytes before the address, the slot as its disp32 and the value as its immediate, ten bytes in all.
    A candidate whose bytes are not that opcode -- `ff 15 <slot>` is the `call [slot]` that most of a table's
    occurrences take -- is reported as `spans` rather than dropped, so the refusal count is a measurement.
    """
    if window[:2] != b"\xc7\x05":
        return None, "spans"
    insns = objdump(window[:12], site)
    if not insns or insns[0][0] != site:
        return None, "undecoded"
    m = STORE.match(insns[0][1])
    if not m or int(m.group(1), 16) != slot:
        return None, "not-a-store"
    return int(m.group(2), 16), ""


def owner_of(va, names, sizes):
    """The map row whose extent holds the site -- the body that installs the entry."""
    starts = sorted(names)
    import bisect
    i = bisect.bisect_right(starts, va) - 1
    if i < 0:
        return None
    start = starts[i]
    n = sizes.get(start, 0)
    return start if n and start <= va < start + n else None


def parse_slots(spec):
    """`lo:hi` in hex, both inclusive, stepped by four."""
    if not spec:
        return []
    lo, hi = spec.split(":")
    lo, hi = int(lo, 16), int(hi, 16)
    if hi < lo:
        raise SystemExit("FAIL global-slot-stores: --slots %s has hi below lo" % spec)
    return list(range(lo, hi + 1, 4))


def measure(image, slots_for_window, names, sizes, walkers):
    rows, census = [], {k: 0 for k in CENSUS if k not in CENSUS_TEXT}
    seen_owners, refused, refused_forms = set(), 0, {}
    for window_lo, slots in slots_for_window:
        census["windows"] += 1
        for slot in slots:
            census["slots"] += 1
            needle = struct.pack("<I", slot)
            written = False
            for sec in image.sections:
                if not sec.executable:
                    continue
                blob = image.data[sec.foff:sec.foff + min(sec.fsize, sec.vsize)]
                i = 0
                while True:
                    i = blob.find(needle, i)
                    if i < 0:
                        break
                    site = sec.va + i - 2          # the `c7 05` opcode precedes the disp32
                    window = image.read_va(site, 12) if site >= sec.va else b""
                    value, why = store_at(window, site, slot) if window else (None, "undecoded")
                    if value is None:
                        refused += 1
                        refused_forms[why] = refused_forms.get(why, 0) + 1
                        i += 1
                        continue
                    owner = owner_of(site, names, sizes)
                    if owner is not None:
                        seen_owners.add(owner)
                    written = True
                    census["stores"] += 1
                    static = None
                    off = image.va_to_offset(slot)
                    if off is not None:
                        static = dword(image.data, off)
                    if static is not None and static != value:
                        census["overrides"] += 1
                    if owner is None:
                        census["unowned"] += 1
                    if names.get(value):
                        census["values_named"] += 1
                    if value in walkers:
                        census["values_walker"] += 1
                    rows.append({"window": "0x%08x" % window_lo, "slot": "0x%08x" % slot,
                                 "index": str((slot - window_lo) // 4), "site": "0x%08x" % site,
                                 "owner_va": "0x%08x" % owner if owner is not None else "",
                                 "owner_name": names.get(owner, "") if owner is not None else "",
                                 "value": "0x%08x" % value, "value_name": names.get(value, "") or walkers.get(value, ""),
                                 "static_value": "0x%08x" % static if static else "",
                                 "static_name": names.get(static, "") if static else "",
                                 "form": "", "note": "override" if static and static != value else ""})
                    i += 1
            if written:
                census["slots_written"] += 1
    census["owners"] = len(seen_owners)
    census["refused"] = refused
    census["refused_forms"] = ", ".join("%s %d" % (k, v) for k, v in sorted(refused_forms.items()))
    # The longest run of consecutive written slots, and how many runs the window holds.
    written_slots = sorted({int(r["slot"], 16) for r in rows if r["form"] == ""})
    runs, run = [], [written_slots[0]] if written_slots else []
    for va in written_slots[1:]:
        if va == run[-1] + 4:
            run.append(va)
        else:
            runs.append(run)
            run = [va]
    if run:
        runs.append(run)
    census["runs"] = len(runs)
    if runs:
        best = max(runs, key=len)
        census["run_slots"] = best[0]
        census["run_len"] = len(best)
    census["window_text"] = " ".join("0x%08x:0x%08x" % (lo, s[-1]) for lo, s in slots_for_window)
    return rows, census


def write_artifact(rows, census, path):
    import csv
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=BODY + CENSUS)
        w.writeheader()
        w.writerows(rows)
        row = {k: "" for k in BODY + CENSUS}
        row["form"] = "census"
        for key, value in census.items():
            row[key] = str(value)
        w.writerow(row)


def read_artifact(path):
    rows, census = [], {}
    for r in read_csv(path):
        if r.get("form") == "census":
            for key in CENSUS:
                if r.get(key) not in (None, ""):
                    census[key] = r[key] if key in CENSUS_TEXT else int(r[key])
        else:
            rows.append({k: r.get(k) or "" for k in BODY})
    return rows, census


def render(rows, census):
    lines = []
    if not rows:
        return "no rows"
    installers = {}
    for r in rows:
        if r["form"]:
            continue
        key = r["owner_va"] or ""
        installers.setdefault(key, []).append(r)
    lines.append("| window | slots | stores | written | installer(s) | overrides | values the map names |")
    lines.append("|---|---|---|---|---|---|---|")
    for window in sorted({r["window"] for r in rows}):
        got = [r for r in rows if r["window"] == window]
        live = [r for r in got if not r["form"]]
        owners = sorted({(r["owner_va"], r["owner_name"]) for r in live})
        lines.append("| `%s` | %d | %d | %d | %s | %d | %d |" % (
            window, len({r["slot"] for r in got}), len(live), len({r["slot"] for r in live}),
            ", ".join("`%s` %s" % (a, n) if a else "-" for a, n in owners) or "-",
            sum(1 for r in live if r["note"] == "override"),
            sum(1 for r in live if r["value_name"])))
    lines.append("")
    lines.append(
        "**%d stores fill %d of the %d slots the census read, from %d installer body/bodies; %d of them "
        "replace a default the image ships (%d slots have no store at all) and %d other candidates in "
        "`.text` spelled a slot's address without being a store of it (%s).** The longest run of consecutive "
        "written slots is **%d entries at `0x%08x`**, and the windows hold **%d** run(s) of consecutive slots."
        % (census.get("stores", 0), census.get("slots_written", 0), census.get("slots", 0),
           census.get("owners", 0), census.get("overrides", 0),
           census.get("slots", 0) - census.get("slots_written", 0), census.get("refused", 0),
           census.get("refused_forms", "") or "-",
           census.get("run_len", 0), census.get("run_slots", 0), census.get("runs", 0)))
    lines.append("")
    lines.append(
        "**%d of the %d stored values are bodies the map names and %d are generated `<Class>_lookup` "
        "walkers** -- the split is the artifact's `value_name` column against `inventory/gap_walker_names.csv`, "
        "and it is what separates a table of library routines from the intrinsic-lookup array."
        % (census.get("values_named", 0), census.get("stores", 0), census.get("values_walker", 0)))
    lines.append("")
    lines.append("| slot | site | installer | stored | name | ships |")
    lines.append("|---|---|---|---|---|---|")
    for r in rows:
        if r["form"]:
            continue
        lines.append("| `%s` | `%s` | %s | `%s` | %s | %s |" % (
            r["slot"], r["site"], ("`%s`" % r["owner_name"]) if r["owner_name"] else (r["owner_va"] or "-"),
            r["value"], r["value_name"] or "-",
            ("`%s`" % (r["static_name"] or r["static_value"])) if r["static_value"] else "no file bytes"))
    lines.append("")
    return "\n".join(lines)


def splice_doc(path, block, marks):
    text = open(path, encoding="utf-8").read()
    i, j = text.find(marks[0]), text.find(marks[1])
    if i < 0 or j < 0:
        return None
    return text[:i] + marks[0] + "\n" + block + "\n" + text[j:]


def name_state(names_path=None):
    """The recorded names against the map: present / missing / misfiled."""
    have, sizes = load_symbols()
    present, missing, misfiled = 0, [], []
    for r in read_csv(names_path or NAMES):
        try:
            va = int(r["body_va"], 16)
        except (KeyError, ValueError):
            continue
        name = (r.get("name") or "").strip()
        if not name:
            continue
        if va not in have:
            missing.append(r)
        elif have[va] == name and sizes.get(va) == int(r.get("size") or 0):
            present += 1
        else:
            misfiled.append(r)
    return present, missing, misfiled, len(have)


def insert_symbols(names_path=None):
    """One `address,name,size,` row per missing recorded name; every existing line is copied verbatim."""
    present, missing, misfiled, _ = name_state(names_path)
    if misfiled:
        return 0, misfiled
    with open(SYMBOLS, newline="", encoding="utf-8") as fh:
        lines = fh.readlines()
    if not lines:
        raise SystemExit("FAIL global-slot-stores: %s is empty" % SYMBOLS)
    nl = "\r\n" if lines[0].endswith("\r\n") else "\n"
    header, body = lines[0], [l for l in lines[1:] if l.strip()]
    held = set()
    for line in body:
        try:
            held.add(int(line.split(",", 1)[0], 16))
        except ValueError:
            continue
    added = []
    for r in sorted(missing, key=lambda r: int(r["body_va"], 16)):
        va = int(r["body_va"], 16)
        if va in held:
            continue
        held.add(va)
        added.append("0x%08x,%s,%d,%s%s" % (va, r["name"], int(r["size"]), "", nl))
    if added:
        merged = sorted([(int(l.split(",", 1)[0], 16), l) for l in body + added])
        with open(SYMBOLS, "w", newline="", encoding="utf-8") as fh:
            fh.write(header)
            for _, line in merged:
                fh.write(line if line.endswith(nl) else line + nl)
    return len(added), []


def self_check():
    bad = []
    # `c7 05 <slot> <value>` decodes as the store; a `call [slot]` at the same address does not.
    store = b"\xc7\x05" + struct.pack("<I", 0x630e20) + struct.pack("<I", 0x120050)
    value, why = store_at(store + b"\x90" * 4, 0x1000, 0x630e20)
    if value != 0x120050 or why:
        bad.append("a `c7 05` store is read as one, with its value")
    call = b"\xff\x15" + struct.pack("<I", 0x5ccadc) + b"\x90" * 4
    value, why = store_at(call, 0x1000, 0x5ccadc)
    if value is not None or why != "spans":
        bad.append("a `call [slot]` is not a store of it")
    # A `c7 05` whose disp32 is a different slot is not a store into this one.
    other = b"\xc7\x05" + struct.pack("<I", 0x630e24) + struct.pack("<I", 0x120050)
    value, why = store_at(other + b"\x90" * 4, 0x1000, 0x630e20)
    if value is not None or why != "not-a-store":
        bad.append("a store into the next slot is not a store into this one")
    if parse_slots("0x1000:0x1008") != [0x1000, 0x1004, 0x1008]:
        bad.append("a slot window steps by four and includes both ends")
    names = {0x2000: "Installer", 0x2100: "Alpha_lookup"}
    sizes = {0x2000: 16, 0x2100: 8}
    if owner_of(0x2004, names, sizes) != 0x2000:
        bad.append("a site inside a body's extent is that body")
    if owner_of(0x2010, names, sizes) is not None:
        bad.append("a site past every extent has no owner")
    rows = [{"window": "0x1000", "slot": "0x1000", "index": "0", "site": "0x2002",
             "owner_va": "0x2000", "owner_name": "Installer", "value": "0x2100",
             "value_name": "Alpha_lookup", "static_value": "", "static_name": "", "form": "",
             "note": ""}]
    census = {"windows": 1, "slots": 1, "stores": 1, "slots_written": 1, "owners": 1, "overrides": 0,
              "unowned": 0, "values_named": 1, "values_walker": 1, "runs": 1, "run_slots": 0x1000,
              "run_len": 1, "refused": 0, "refused_forms": "", "window_text": "0x1000:0x1000"}
    block = render(rows, census)
    if "| `0x1000` | 1 | 1 | 1 |" not in block:
        bad.append("the render counts the window's stores and slots")
    if "Alpha_lookup" not in block:
        bad.append("the render names the stored body")
    if bad:
        print("FAIL global-slot-stores: " + "; ".join(bad))
        return 1
    print("ok   global-slot-stores: the store form, the next-slot refusal, the window step, the owner lookup and the render all answer")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--xbe")
    ap.add_argument("--slots", action="append", default=[],
                    help="a slot window as lo:hi in hex, both ends inclusive (repeatable)")
    ap.add_argument("-o", "--out")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--write-doc")
    ap.add_argument("--check-doc")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--apply-symbols", action="store_true")
    ap.add_argument("--check-symbols", action="store_true")
    args = ap.parse_args()
    if args.check:
        return self_check()
    if args.apply_symbols or args.check_symbols:
        present, missing, misfiled, map_rows = name_state()
        if misfiled:
            print("FAIL global-slot-stores: %d recorded name(s) the map holds under another name or size, "
                  "first %s" % (len(misfiled), misfiled[0]["body_va"]))
            return 1
        if args.apply_symbols:
            added, _ = insert_symbols()
            present2, missing2, _, map_rows2 = name_state()
            print("inserted %d rows into symbols/xbox_functions.csv (%d present, %d missing); map %d -> %d rows"
                  % (added, present2, len(missing2), map_rows, map_rows2))
            return 0
        if missing:
            print("FAIL global-slot-stores: %d recorded name(s) are not map rows, first %s (%s)"
                  % (len(missing), missing[0]["body_va"], missing[0]["name"]))
            return 1
        print("ok   global-slot-stores: all %d recorded names are rows of symbols/xbox_functions.csv at the "
              "recorded address and size (map %d rows)" % (present, map_rows))
        return 0
    if args.check_doc:
        rows, census = read_artifact(OUT)
        if not rows or not census:
            print("FAIL global-slot-stores: %s is missing or has no census row" % OUT)
            return 1
        block = render(rows, census)
        text = open(args.check_doc, encoding="utf-8").read()
        i, j = text.find(DOC_A[0]), text.find(DOC_A[1])
        if i < 0 or j < 0:
            print("FAIL global-slot-stores: %s has no %s block" % (args.check_doc, DOC_A[0]))
            return 1
        if text[i:j + len(DOC_A[1])] != DOC_A[0] + "\n" + block + "\n" + DOC_A[1]:
            print("FAIL global-slot-stores: %s's block does not reproduce from the committed CSV" % args.check_doc)
            return 1
        print("ok   global-slot-stores: %s reproduces from the committed CSV" % args.check_doc)
        return 0
    if args.report or args.write_doc:
        rows, census = read_artifact(OUT)
        if not rows or not census:
            print("FAIL global-slot-stores: %s is missing or has no census row" % OUT)
            return 1
        block = render(rows, census)
        if args.report:
            print(block)
            return 0
        new = splice_doc(args.write_doc, block, DOC_A)
        if new is None:
            print("FAIL global-slot-stores: %s has no %s markers" % (args.write_doc, DOC_A[0]))
            return 1
        if new != open(args.write_doc, encoding="utf-8").read():
            open(args.write_doc, "w", encoding="utf-8").write(new)
            print("wrote %s (%s)" % (args.write_doc, DOC_A[0]))
        return 0
    if not args.xbe:
        ap.error("--xbe is required to read the image (or use --report/--check-doc/--check)")

    spec = importlib.util.spec_from_file_location("xbe", os.path.join(ROOT, "tools", "xbe.py"))
    xbemod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(xbemod)
    image = xbemod.Xbe(args.xbe)
    names, sizes = load_symbols()
    walkers = {}
    for r in read_csv(WALKERS):
        try:
            walkers[int(r["body_va"], 16)] = r["derived_name"]
        except (KeyError, ValueError):
            continue
    windows = [(parse_slots(s)[0], parse_slots(s)) for s in args.slots]
    if not windows:
        ap.error("--slots is required to read the image")
    rows, census = measure(image, windows, names, sizes, walkers)
    out = os.path.join(args.out, os.path.basename(OUT)) if args.out else OUT
    write_artifact(rows, census, out)
    print("wrote %s (%d rows, %d stores, %d refused)" % (out, len(rows), census["stores"], census["refused"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
