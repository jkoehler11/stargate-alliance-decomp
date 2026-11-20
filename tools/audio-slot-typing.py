#!/usr/bin/env python3
"""Type the object the audio module's slot-31 dispatches go through (U34's residual).

`inventory/xboxaudio_chain.csv` counted the `call [reg+0x7c]` sites -- slot 31 of a class table's
dispatch offsets, the slot that installs `XboxAudio_CreateDevice` -- and `tools/vcall-receiver.py`
read the instruction that loads each site's register (`inventory/audio_slot_receivers.csv`). Neither
says what the *object* is, and without that a site is an offset, not a call to the device.

This joins the two halves to the class tables, which are in the image: every table's slot 31 is a
pointer, and a table installs the device body in exactly one place. So a site is decided when its
receiver is the function's own `this` -- the dispatch then goes through the vtable of the class whose
table holds that function, whose slot 31 this file reads out of the image:

    root = this, vtable of C      -> the call reaches the body C's own table installs at slot 31
    root = field/argument/stack   -> the receiver is another object; only its holder is known

The control is the table census: if a class's slot 31 is one of the shared `ret` stubs, no call through
that class's vtable can be the device, whatever the displacement says. The `+0x88` sibling a census row
may record is reported as a sibling and nothing more -- a body that dispatches two displacements is not
a body that dispatches the audio subsystem.

    python3 tools/audio-slot-typing.py --xbe DUMP/default.xbe \\
        -o inventory/audio_slot_typing.csv
    python3 tools/audio-slot-typing.py --check     # synthetic tables, no dump, writes nothing

Addresses, class names and counts only: nothing here carries game data.
"""
import argparse
import csv
import os
import re
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

INVENTORY = os.path.join(ROOT, "inventory")
RECEIVERS = os.path.join(INVENTORY, "audio_slot_receivers.csv")
CENSUS = os.path.join(INVENTORY, "xboxaudio_chain.csv")
TABLES = os.path.join(INVENTORY, "xbe_class_tables.csv")
NAMES = os.path.join(INVENTORY, "xbe_class_table_names.csv")
FUNCTIONS = os.path.join(ROOT, "symbols", "xbox_functions.csv")
OUT = os.path.join(INVENTORY, "audio_slot_typing.csv")

DEVICE_SLOT = 31
PUMP_SLOT = 34
_VR = None
HEADER = ["site_va", "body", "function", "module", "source", "root", "receiver", "field_disp",
          "holder_class", "holder_table", "holder_slot31", "holder_slot31_name", "slot31_is_stub",
          "receiver_class", "verdict", "pump_sibling"]


def narrow_decoder():
    """`vcall-receiver.py`'s own decoder, imported by path (the module name has a hyphen)."""
    global _VR
    if _VR is None:
        import importlib.util
        path = os.path.join(ROOT, "tools", "vcall-receiver.py")
        spec = importlib.util.spec_from_file_location("vcall_receiver", path)
        _VR = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(_VR)
    return _VR


def field_of(xbe, origin_va):
    """(base register, displacement) of the member load at `origin_va`, or `("", "")`.

    The receiver row names the VA of the load that produced the object pointer (`origin_va`); its
    displacement is the field's offset in the holder, which is what a layout question is about.
    """
    if not origin_va:
        return "", ""
    try:
        blob = xbe.read_va(int(origin_va, 16), 16)
    except (KeyError, IndexError, ValueError):
        return "", ""
    insn = narrow_decoder().decode(blob, 0)
    if insn is None or insn.length <= 0:
        return "", ""
    if not insn.base or insn.disp is None:
        return "", ""
    return insn.base, narrow_decoder().hxv(insn.disp)


DECLARATIONS = os.path.join(INVENTORY, "audio_owner_declarations.csv")
DECL_HEADER = ["package", "class", "line", "var", "type", "declaration"]
AUDIO_VAR = re.compile(r"^\s*var(?:\([^)]*\))?[^;]*?\b([\w<>]+subsystem)\s+(\w+)\s*;", re.IGNORECASE)


def declarations(dump, out_path):
    """Every class text in the corpus that declares an object variable of an audio-subsystem type.

    The offset of such a field is not in the package (the property stream carries names, kinds and types,
    never byte offsets), so what the script *can* settle is the field's declared class -- which is the
    type a site's receiver would have if that site read the field. Walking the corpus this way is what
    makes the claim reproducible instead of a remembered grep.
    """
    import glob
    import importlib.util
    sys.path.insert(0, os.path.join(ROOT, "tools"))
    spec = importlib.util.spec_from_file_location("pkg_script_text",
                                                  os.path.join(ROOT, "tools", "pkg-script-text.py"))
    pst = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(pst)
    from ue2pkg import Package  # noqa: E402
    rows, scanned, refused = [], 0, 0
    paths = sorted(glob.glob(os.path.join(dump, "*.u")))
    for path in paths:
        try:
            pkg = Package(path)
            pkg.read_names()
            pkg.read_imports()
            pkg.read_exports()
        except Exception:  # noqa: BLE001 - a package the reader refuses is not a declaration
            refused += 1
            continue
        for export in pkg.exports:
            try:
                if pkg.class_name_of(export) != "Class":
                    continue
                text = pst.script_text(pkg, export)
            except Exception:  # noqa: BLE001
                continue
            if not text:
                continue
            scanned += 1
            cls = pkg.name(export["name"])
            for i, line in enumerate(text.splitlines()):
                m = AUDIO_VAR.match(line)
                if m:
                    rows.append({"package": os.path.basename(path), "class": cls, "line": str(i + 1),
                                 "var": m.group(2), "type": m.group(1),
                                 "declaration": "var %s %s" % (m.group(1), m.group(2))})
    with open(out_path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=DECL_HEADER, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    print("%d package(s), %d refused; %d class texts scanned; %d audio-subsystem declaration(s): %s"
          % (len(paths), refused, scanned, len(rows), ", ".join("%s.%s:%s" % (r["class"], r["var"], r["type"]) for r in rows)))


def read_csv(path):
    if not path or not os.path.isfile(path):
        return []
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def read_tables(path):
    """[(table_va, slots)] from the class-table census."""
    out = []
    for row in read_csv(path):
        try:
            out.append((int(row["table_va"], 16), int(row["slots"])))
        except (KeyError, ValueError, TypeError):
            continue
    return out


def read_names(path):
    return {int(row["table_va"], 16): (row.get("class") or "")
            for row in read_csv(path) if row.get("table_va")}


def read_symbols(path):
    out = {}
    for row in read_csv(path):
        try:
            out[int(row["address"], 16)] = row.get("name") or ""
        except (KeyError, ValueError, TypeError):
            continue
    return out


def slot_map(xbe, tables):
    """(slots, reverse): (table_va, slot) -> target, and target -> [(table_va, slot)]."""
    slots, reverse = {}, {}
    for table_va, count in tables:
        try:
            blob = xbe.read_va(table_va, 4 * count)
        except (KeyError, IndexError):
            continue
        if len(blob) < 4 * count:
            continue
        for i in range(count):
            target = struct.unpack_from("<I", blob, 4 * i)[0]
            slots[(table_va, i)] = target
            reverse.setdefault(target, []).append((table_va, i))
    return slots, reverse


def devices(slots, target):
    """Every (table, slot) that installs `target`."""
    return sorted(((t, s) for (t, s), v in slots.items() if v == target), key=lambda ts: (ts[1], ts[0]))


def holder_of(reverse, names, body):
    """The class whose table holds `body` as a slot, preferring a named table."""
    hits = reverse.get(body, [])
    for table_va, _slot in hits:
        if names.get(table_va):
            return names[table_va], table_va
    return "", (hits[0][0] if hits else None)


def root_of(row):
    """Where the receiver's object pointer came from: `this` when the chain is rooted at the entry register."""
    chain = row.get("origin_chain") or ""
    origin = row.get("origin") or ""
    if origin == "this" or chain.endswith("<-ecx"):
        return "this"
    return origin or row.get("source") or ""


def classify(row, holder, holder_table, slots, stub, field_disp=""):
    """(receiver_class, verdict) for one site, from the holder's own table."""
    source = row.get("source") or ""
    if source == "not-a-boundary":
        return "", "not-a-boundary"
    root = root_of(row)
    slot31 = slots.get((holder_table, DEVICE_SLOT)) if holder_table is not None else None
    if root == "this" and source in ("object-head", "this") and holder:
        # the dispatch goes through this object's vtable: the class is the holder's
        if slot31 is None:
            return holder, "typed-no-slot31"
        if slot31 == stub:
            return holder, "typed-slot31-stub"
        return holder, "typed-slot31-other"
    if source == "unread" or not root:
        return "", "receiver-unread"
    if root == "field":
        return "", "field-of-holder%s" % (field_disp or "")
    return "", "receiver-%s" % root


def measure(image, receivers_path, census_path, tables_path, names_path, functions_path,
            device_name="XboxAudio_CreateDevice", pump_name="XboxAudio_"):
    from xbe import Xbe  # imported late so --check needs no dump
    xbe = Xbe(image)
    tables = read_tables(tables_path)
    names = read_names(names_path)
    symbols = read_symbols(functions_path)
    slots, reverse = slot_map(xbe, tables)
    by_name = {v: k for k, v in symbols.items()}
    device = by_name.get(device_name)
    if device is None:
        raise SystemExit("no %r in %s" % (device_name, functions_path))
    census = {r["site"] : r for r in read_csv(census_path) if (r.get("kind") or "") == "slot31"}
    rows = []
    for row in read_csv(receivers_path):
        site = row["site_va"]
        c = census.get(site, {})
        body = int(c["body"], 16) if c.get("body") else 0
        holder, holder_table = holder_of(reverse, names, body)
        slot31 = slots.get((holder_table, DEVICE_SLOT)) if holder_table is not None else None
        stub = by_name.get("FUN_00020f00", 0x00020F00)
        _fbase, fdisp = field_of(xbe, row.get("origin_va"))
        receiver_class, verdict = classify(row, holder, holder_table, slots, stub, fdisp)
        rows.append({
            "site_va": site, "body": c.get("body", ""), "function": c.get("body_name", ""),
            "module": c.get("module", "") or "(unnamed)", "source": row.get("source", ""),
            "root": root_of(row), "receiver": row.get("form", ""), "field_disp": fdisp,
            "holder_class": holder,
            "holder_table": "0x%08x" % holder_table if holder_table else "",
            "holder_slot31": "0x%08x" % slot31 if slot31 is not None else "",
            "holder_slot31_name": symbols.get(slot31, "") if slot31 is not None else "",
            "slot31_is_stub": "yes" if slot31 == stub else "",
            "receiver_class": receiver_class, "verdict": verdict,
            "pump_sibling": "yes" if "0x88" in (c.get("extra") or "") else "",
        })
    rows.sort(key=lambda r: (r["verdict"], r["site_va"]))
    return rows, {"device": device, "slots": slots, "tables": tables, "names": names,
                  "symbols": symbols, "pump": by_name.get(pump_name + "Pump")}


def report(rows, ctx):
    """The counts the docs quote, all derived from the rows and the table census."""
    device = ctx["device"]
    slots = ctx["slots"]
    names = ctx["names"]
    installed = devices(slots, device)
    print("%d site(s)" % len(rows))
    print("the device body 0x%08x is installed at %d slot entry(ies) over %d table(s): %s"
          % (device, len(installed), len({t for t, _s in slots}), ", ".join(
              "%s+slot%d" % (names.get(t, "0x%08x" % t), s) for t, s in installed[:4])))
    slot31 = {v for (t, s), v in slots.items() if s == DEVICE_SLOT}
    print("slot %d holds %d distinct pointer(s) across %d tables"
          % (DEVICE_SLOT, len(slot31), sum(1 for (t, s) in slots if s == DEVICE_SLOT)))
    stub = ctx["symbols"].get(0x00020F00, "FUN_00020f00")
    stubbed = sum(1 for (t, s), v in slots.items() if s == DEVICE_SLOT and v == 0x00020F00)
    print("  of which %s (a one-byte ret) in %d tables" % (stub, stubbed))
    for key in ("verdict", "root"):
        out = {}
        for row in rows:
            k = row[key] or "-"
            out[k] = out.get(k, 0) + 1
        print("  %s: %s" % (key, ", ".join("%s %d" % (k, v) for k, v in
                                           sorted(out.items(), key=lambda kv: (-kv[1], kv[0])))))
    typed = [r for r in rows if r["receiver_class"]]
    print("  receiver typed to a class: %d, of which the device: %d"
          % (len(typed), sum(1 for r in typed if r["holder_slot31"] == "0x%08x" % device)))
    holders = {}
    for row in rows:
        if row["verdict"].startswith("field-of-holder") and row["holder_class"]:
            holders[row["holder_class"]] = holders.get(row["holder_class"], 0) + 1
    print("  a field of a named class: " + ", ".join("%s %d" % kv for kv in
                                                     sorted(holders.items(), key=lambda kv: (-kv[1], kv[0]))[:8]))
    two = [r for r in rows if r["pump_sibling"]]
    print("  dispatch +0x88 from the same body: %d, of which slot 31 of the holder's own table is a ret stub: %d"
          % (len(two), sum(1 for r in two if r["slot31_is_stub"])))


def selftest():
    """Synthetic tables and rows: a `this` chain types, a field does not, a spanned site stays untyped."""
    stub = 0x00020F00
    device = 0x0002FD70
    # two tables: A at 0x1000 (4 slots), B at 0x2000 (4 slots); A's slot 31 is the device, B's a ret stub
    slots = {(0x1000, 30): 0xAAAA, (0x1000, DEVICE_SLOT): device,
             (0x2000, 30): 0xBBBB, (0x2000, DEVICE_SLOT): stub}
    reverse = {device: [(0x1000, DEVICE_SLOT)], 0xBBBB: [(0x2000, 30)], stub: [(0x2000, DEVICE_SLOT)]}
    names = {0x1000: "AudioHolder", 0x2000: "OtherHolder"}
    body_a, body_b = 0x5000, 0x6000
    reverse.setdefault(body_a, [(0x1000, 30)])
    reverse.setdefault(body_b, [(0x2000, 30)])
    bad = []
    got = holder_of(reverse, names, body_a)
    if got != ("AudioHolder", 0x1000):
        bad.append("holder of a slot body read as %r" % (got,))
    got = holder_of(reverse, names, 0x7000)
    if got != ("", None):
        bad.append("an unheld body got a holder: %r" % (got,))
    holder, table = holder_of(reverse, names, body_a)
    cls, verdict = classify({"source": "object-head", "origin": "this", "origin_chain": "esi<-ecx"},
                            holder, table, slots, stub)
    if (cls, verdict) != ("AudioHolder", "typed-slot31-other"):
        bad.append("a this-rooted site in the device's class read as %r %r" % (cls, verdict))
    holder, table = holder_of(reverse, names, body_b)
    cls, verdict = classify({"source": "object-head", "origin": "this", "origin_chain": "esi<-ecx"},
                            holder, table, slots, stub)
    if (cls, verdict) != ("OtherHolder", "typed-slot31-stub"):
        bad.append("a this-rooted site in the stub class read as %r %r" % (cls, verdict))
    cls, verdict = classify({"source": "object-head", "origin": "field"},
                            "GameEngine", 0x2000, slots, stub, "+0x1c")
    if (cls, verdict) != ("", "field-of-holder+0x1c"):
        bad.append("a field receiver read as %r %r" % (cls, verdict))
    cls, verdict = classify({"source": "not-a-boundary"}, "GameEngine", 0x2000, slots, stub)
    if (cls, verdict) != ("", "not-a-boundary"):
        bad.append("a spanned site read as %r %r" % (cls, verdict))
    cls, verdict = classify({"source": "unread", "origin": ""}, "GameEngine", 0x2000, slots, stub)
    if (cls, verdict) != ("", "receiver-unread"):
        bad.append("an unread site read as %r %r" % (cls, verdict))
    if devices(slots, device) != [(0x1000, DEVICE_SLOT)]:
        bad.append("the device's install sites read as %r" % (devices(slots, device),))
    for line in bad:
        print("FAIL " + line)
    if bad:
        raise SystemExit(1)
    print("selftest ok: this-rooted sites typed, field and unread sites left untyped, one device install")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--xbe")
    ap.add_argument("--receivers", default=RECEIVERS)
    ap.add_argument("--census", default=CENSUS)
    ap.add_argument("--tables", default=TABLES)
    ap.add_argument("--names", default=NAMES)
    ap.add_argument("--functions", default=FUNCTIONS)
    ap.add_argument("--device-name", default="XboxAudio_CreateDevice")
    ap.add_argument("--declarations", metavar="SYSTEM_DIR",
                    help="also walk the packages' class texts for an audio-subsystem variable "
                         "(writes inventory/audio_owner_declarations.csv)")
    ap.add_argument("-o", "--out", default=OUT)
    ap.add_argument("--check", action="store_true", help="synthetic self-test; no dump, writes nothing")
    args = ap.parse_args()
    if args.check:
        selftest()
        return 0
    if args.declarations:
        declarations(args.declarations, DECLARATIONS)
        if not args.xbe:
            return 0
    if not args.xbe:
        ap.error("--xbe is required unless --check")
    rows, ctx = measure(args.xbe, args.receivers, args.census, args.tables, args.names,
                        args.functions, args.device_name)
    with open(args.out, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=HEADER, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)
    report(rows, ctx)
    print("wrote %s" % args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
