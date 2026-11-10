#!/usr/bin/env python3
"""Read one module's exported bodies: their translation unit, their literals, their callee modules (PLAN 4.8).

`tools/module-export-census.py` sized the whole corpus with four signals per body and stopped at the
population -- which bodies name themselves, which carry a literal, which a table holds. PLAN 4.8's renderer
question is a module-sized one and wants the next layer: for **one** module, which translation unit does each
body claim in its own assert, which module does each of its callees belong to, and how does it reach the
device -- a direct call, or a slot off an interface object's table?

Four readings per body, all from the export `export-module.sh` leaves under `$WORK/module-export`:

* **tunit** -- the `.cpp`/`.h` basename in the body's own `__FILE__` literal. A body that asserts names the
  file it was compiled from, so the module's file count is measurable without the build tree.
* **literals** -- the string constants it carries, split into build-tree paths (an assert's `__FILE__`) and
  `text` (everything else: log messages, format strings, assert expressions).
* **callee modules** -- every resolved callee, attributed to a module: an address-form name (`FUN_*`,
  `thunk_FUN_*`) through `inventory/function_module_attribution.csv`, a module-prefixed name (`Engine_*`,
  `Core_*`) by its prefix, and the CRT/engine runtime names (`appAssertFailed`, `memcpy`, ...) as `runtime`.
  Only *resolved* callees are counted; an anonymous `undefined4` cast is not a callee and is not invented as one.
* **slots** -- the byte offsets of `call [object + off]` sites in the decompiled body, i.e. the vtable slots
  the body reaches through an interface object. Offsets are counted, not named: naming the table is 4.2's
  signature work (U9), and `tools/vtable-reach.py` answers the reverse question for a known address.

    body-read.py --module D3DDrv --root $WORK/module-export -o inventory/d3ddrv_bodies.csv
    body-read.py --report                       # the tables, to stdout
    body-read.py --write-doc docs/systems/RENDERER.md
    body-read.py --check                        # synthetic bodies, no dump, writes nothing

`--check` (in `make ci`) builds two bodies in a temporary directory and reads them back, so the contract is
tested without the dump: the tool must answer `--check`, exit 0, and leave the tree untouched.
"""
import argparse
import csv
import os
import re
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

CSV_PATH = os.path.join(ROOT, "inventory", "d3ddrv_bodies.csv")
CENSUS = os.path.join(ROOT, "inventory", "module_export_census.csv")
ATTRIBUTION = os.path.join(ROOT, "inventory", "function_module_attribution.csv")
DOC_MARK = ("<!-- generated:renderer-body-read -->", "<!-- /generated:renderer-body-read -->")

COLUMNS = ["address", "name", "class", "size", "tunit", "literals", "text", "calls", "intra", "cross",
           "unnamed", "runtime", "slots", "slot_max", "slot_offsets", "modules"]

MODULE_NAMES = ["Core", "Engine", "Gui", "IpDrv", "D3DDrv", "XboxDrv", "XboxAudio", "XboxLive",
                "Gameplay", "Fire", "stargategame", "stargatevehicles"]
# The runtime every compiled body may call without the call being a statement about the module.
RUNTIME = {"appAssertFailed", "appErrorf", "appMalloc", "appFree", "appMemcpy", "appMemset",
           "memcpy", "memmove", "memset", "memcmp", "strlen", "strcmp", "strcpy", "strncpy", "sprintf",
           "vsprintf", "wcslen", "wcscmp", "wcscpy", "_ftol", "_ftol2", "__security_check_cookie"}

HEADER = re.compile(r"^// (\S+) @ 0x([0-9a-fA-F]+)\s+size=(\d+)")
MARKER = "// ---- decompiled ----"
LITERAL = re.compile(r'L?"((?:[^"\\]|\\.)*)"')
ADDR_NAME = re.compile(r"^(?:thunk_)?FUN_([0-9a-f]{8})$")
CALLEE = re.compile(r"\b([A-Za-z_][A-Za-z0-9_]*)\s*\(")
SLOT = re.compile(r"\(code \*\*\)\(\*?\w+ \+ (0x[0-9a-f]+)\)")
SRC_PATH = re.compile(r"\\([A-Za-z0-9_.]+)\.(cpp|cxx|c|h|hpp)\b", re.IGNORECASE)


def die(msg):
    sys.stderr.write(msg.rstrip() + "\n")
    sys.exit(1)


# -- reading the export ----------------------------------------------------
def parse_body(path):
    """(name, address, size, body_text) for one exported .c file."""
    with open(path, encoding="utf-8", errors="replace") as fh:
        text = fh.read()
    m = HEADER.match(text.split("\n", 1)[0])
    if not m:
        die("%s: no '// name @ 0xADDR size=N' header" % path)
    name, addr, size = m.group(1), int(m.group(2), 16), int(m.group(3))
    body = text.split(MARKER, 1)[1] if MARKER in text else ""
    if "decompile failed" in body:
        body = ""
    return name, addr, size, body


def literals_of(body):
    return [m.group(1).replace('\\\\', '\\').replace('\\"', '"') for m in LITERAL.finditer(body)]


def tunit_of(lits):
    """The `.cpp`/`.h` basename the body's own assert names, preferring a .cpp over a header."""
    seen = {"cpp": [], "h": []}
    for s in lits:
        for m in SRC_PATH.finditer(s):
            kind = "h" if m.group(2).lower() in ("h", "hpp") else "cpp"
            seen[kind].append(m.group(1) + "." + m.group(2))
    for kind in ("cpp", "h"):
        if seen[kind]:
            return sorted(set(seen[kind]))[0]
    return ""


def callees_of(body, self_name, attribution, module):
    """(counts, modules, slots): resolved callees by bucket, the cross-module names, the slot offsets."""
    counts = {"intra": set(), "cross": set(), "unnamed": set(), "runtime": set()}
    mods = set()
    for tok in set(CALLEE.findall(body)):
        if tok == self_name:
            continue
        m = ADDR_NAME.match(tok)
        if m:
            mod = attribution.get(int(m.group(1), 16))
            if not mod:
                counts["unnamed"].add(tok)
            elif mod == module:
                counts["intra"].add(tok)
            else:
                counts["cross"].add(tok)
                mods.add(mod)
            continue
        if tok in RUNTIME:
            counts["runtime"].add(tok)
            continue
        head = tok.split("_", 1)[0]
        if head in MODULE_NAMES and "_" in tok:
            if head == module:
                counts["intra"].add(tok)
            else:
                counts["cross"].add(tok)
                mods.add(head)
    slots = sorted({int(m.group(1), 16) for m in SLOT.finditer(body)})
    return {k: len(v) for k, v in counts.items()}, mods, slots


def read_module(root, module, attribution, classes):
    """Every body of one module, ascending by address."""
    where = os.path.join(root, module)
    if not os.path.isdir(where):
        die("%s: no exported bodies (run tools/ghidra/export-module.sh first)" % where)
    rows = []
    for fn in sorted(os.listdir(where)):
        if not fn.endswith(".c"):
            continue
        name, addr, size, body = parse_body(os.path.join(where, fn))
        lits = literals_of(body)
        src = [s for s in lits if SRC_PATH.search(s)]
        text = sorted({s for s in lits if s not in src})
        counts, mods, slots = callees_of(body, name, attribution, module)
        rows.append({
            "address": "0x%08x" % addr, "_va": addr, "name": name,
            "class": classes.get("0x%08x" % addr, ""), "size": size,
            "tunit": tunit_of(lits), "literals": len(set(lits)), "text": len(text),
            "calls": sum(counts.values()), **counts,
            "slots": len(slots), "slot_max": "0x%x" % max(slots) if slots else "",
            "slot_offsets": ";".join("0x%x" % o for o in slots),
            "modules": ";".join(sorted(mods)),
        })
    rows.sort(key=lambda r: r["_va"])
    return rows


def write_csv(rows, path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)
    return path


NUMERIC = ("size", "literals", "text", "calls", "intra", "cross", "unnamed", "runtime", "slots")


def read_csv(path):
    with open(path, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    for r in rows:
        for c in NUMERIC:
            r[c] = int(r[c] or 0)
        if r.get("slot_max"):
            r["slot_max"] = int(r["slot_max"], 16)
        r["_slots"] = [int(x, 16) for x in filter(None, (r.get("slot_offsets") or "").split(";"))]
    return rows


def read_attribution(path=ATTRIBUTION):
    out = {}
    with open(path, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            if r["module"] and r["module"] != "(unnamed)":
                out[int(r["address"], 16)] = r["module"]
    return out


def read_classes(path=CENSUS):
    if not os.path.exists(path):
        return {}
    with open(path, newline="", encoding="utf-8") as fh:
        return {r["address"]: r["class"] for r in csv.DictReader(fh)}


# -- reporting -------------------------------------------------------------
def report(rows, module=""):
    out = []
    if not rows:
        return "no bodies"
    total = sum(r["size"] for r in rows)
    out.append("%d bodies, %d bytes, %d distinct literals (%d of them not a build path)."
               % (len(rows), total, sum(r["literals"] for r in rows), sum(r["text"] for r in rows)))

    units = {}
    for r in rows:
        u = units.setdefault(r["tunit"] or "(none)", {"bodies": 0, "bytes": 0, "slots": 0, "cross": 0})
        u["bodies"] += 1
        u["bytes"] += r["size"]
        u["slots"] += r["slots"]
        u["cross"] += r["cross"]
    out.append("")
    out.append("| translation unit | bodies | bytes | slot calls | cross-module callees |")
    out.append("|---|---:|---:|---:|---:|")
    for u in sorted(units, key=lambda k: (-units[k]["bodies"], k)):
        v = units[u]
        out.append("| `%s` | %d | %d | %d | %d |" % (u, v["bodies"], v["bytes"], v["slots"], v["cross"]))
    out.append("| **total** | **%d** | **%d** | **%d** | **%d** |"
               % (len(rows), total, sum(r["slots"] for r in rows), sum(r["cross"] for r in rows)))

    per_mod = {}
    for r in rows:
        for m in filter(None, r.get("modules", "").split(";")):
            e = per_mod.setdefault(m, {"bodies": 0, "edges": 0})
            e["bodies"] += 1
            e["edges"] += 1
    out.append("")
    if per_mod:
        out.append("| callee module | bodies calling it |")
        out.append("|---|---:|")
        for m in sorted(per_mod, key=lambda k: (-per_mod[k]["bodies"], k)):
            out.append("| `%s` | %d |" % (m, per_mod[m]["bodies"]))
    else:
        out.append("No body calls into another attributed module.")

    offsets = {}
    for r in rows:
        slots = r.get("_slots")
        if slots is None:
            slots = [int(x, 16) for x in filter(None, (r.get("slot_offsets") or "").split(";"))]
        for off in slots:
            offsets[off] = offsets.get(off, 0) + 1
    bodies_with_slots = [r for r in rows if r["slots"]]
    out.append("")
    out.append("Slot calls: **%d** of %d bodies reach an interface object through `call [obj + off]`, "
               "**%d** distinct offsets%s%s."
               % (len(bodies_with_slots), len(rows), len(offsets),
                  (", all <= `0x%x`" % max(offsets)) if offsets else "",
                  (" (`" + "`, `".join("0x%x" % o for o in sorted(offsets)[:24]) + "`)") if offsets else ""))

    named = [r for r in rows if r["tunit"]]
    out.append("")
    out.append("| address | name | translation unit | size | literals | slots | callee modules |")
    out.append("|---|---|---|---:|---:|---:|---|")
    for r in named:
        out.append("| `%s` | `%s` | `%s` | %d | %d | %d | %s |"
                   % (r["address"], r["name"], r["tunit"], r["size"], r["literals"], r["slots"],
                      ", ".join("`%s`" % m for m in filter(None, r.get("modules", "").split(";"))) or "-"))
    return "\n".join(out)


def write_doc(rows, path):
    begin, end = DOC_MARK
    text = open(path, encoding="utf-8").read() if os.path.exists(path) else ""
    if begin not in text or end not in text:
        die("%s: no %s ... %s block" % (path, begin, end))
    head, rest = text.split(begin, 1)
    _, tail = rest.split(end, 1)
    new = head + begin + "\n" + report(rows) + "\n" + end + tail
    if new != text:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(new)
        return 1
    return 0


# -- the contract ----------------------------------------------------------
FIXTURE = {
    "00001000_Engine_Foo.c": ("Engine_Foo", 0x1000, 40,
                              'void Engine_Foo(int p)\n{\n'
                              '  appErrorf(L"c:\\\\alienbrainWork\\\\Game\\\\Engine\\\\Src\\\\Foo.cpp");\n'
                              '  (**(code **)(*p + 0x88))();\n'
                              '  FUN_00002000();\n}\n'),
    "00002000_FUN_00002000.c": ("FUN_00002000", 0x2000, 16,
                                'void FUN_00002000(void)\n{\n  D3DDrv_Bar();\n'
                                '  return;\n}\n'),
}


def check():
    """Read a synthetic module back and reconcile it with the parsed fields. No dump, no writes."""
    tmp = tempfile.mkdtemp(prefix="body-read-")
    try:
        where = os.path.join(tmp, "D3DDrv")
        os.makedirs(where)
        for fn, (name, addr, size, body) in FIXTURE.items():
            with open(os.path.join(where, fn), "w", encoding="utf-8") as fh:
                fh.write("// %s @ 0x%08x  size=%d\n// fixture\n\n%s\n%s\n"
                         % (name, addr, size, MARKER, body))
        attribution = {0x2000: "Core"}
        rows = read_module(tmp, "D3DDrv", attribution, {})
        bad = []
        if len(rows) != 2:
            bad.append("read %d bodies, want 2" % len(rows))
        else:
            foo, bar = rows
            if foo["name"] != "Engine_Foo" or foo["tunit"] != "Foo.cpp":
                bad.append("Engine_Foo: name/tunit %r/%r" % (foo["name"], foo["tunit"]))
            if foo["slots"] != 1 or int(foo["slot_max"], 16) != 0x88:
                bad.append("Engine_Foo: slots %r/%r" % (foo["slots"], foo["slot_max"]))
            if foo["cross"] != 1 or foo["modules"] != "Core" or foo["runtime"] != 1 or foo["text"] != 0:
                bad.append("Engine_Foo: buckets %r"
                           % {k: foo[k] for k in ("cross", "modules", "runtime", "text")})
            if bar["intra"] != 1 or bar["calls"] != 1 or bar["literals"] != 0:
                bad.append("FUN_00002000: buckets %r" % {k: bar[k] for k in ("intra", "calls", "literals")})
            if [r["address"] for r in rows] != ["0x00001000", "0x00002000"]:
                bad.append("addresses not ascending")
        if bad:
            for b in bad:
                sys.stderr.write("FAIL %s\n" % b)
            return 1
        print("body-read: 2 synthetic bodies read (tunit, text/buckets, slots, order) in %s" % tmp)
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=os.path.join(os.environ.get("WORK", os.path.expanduser("~/sg1/work")),
                                                   "module-export"))
    ap.add_argument("--module", default="D3DDrv")
    ap.add_argument("-o", "--out", default=CSV_PATH)
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--write-doc")
    ap.add_argument("--check-doc")
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    if a.check:
        return check()
    if a.check_doc:
        text = open(a.check_doc, encoding="utf-8").read()
        begin, end = DOC_MARK
        if begin not in text or end not in text:
            die("%s: no %s ... %s block" % (a.check_doc, begin, end))
        got = text.split(begin, 1)[1].split(end, 1)[0].strip("\n")
        want = report(read_rows_with_slots(a.out))
        if got != want:
            sys.stderr.write("FAIL %s: the generated block differs from %s (run --write-doc %s)\n"
                             % (a.check_doc, a.out, a.check_doc))
            return 1
        return 0
    if a.report:
        print(report(read_rows_with_slots(a.out), a.module))
        return 0
    rows = read_module(a.root, a.module, read_attribution(), read_classes())
    write_csv(rows, a.out)
    if a.write_doc:
        write_doc(rows, a.write_doc)
    print("body-read: %d %s bodies -> %s" % (len(rows), a.module, a.out))
    print(report(rows, a.module))
    return 0


def read_rows_with_slots(path):
    """The committed CSV, with `slot_offsets` expanded to the list the report histogram walks."""
    return read_csv(path)


if __name__ == "__main__":
    sys.exit(main())
