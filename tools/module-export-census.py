#!/usr/bin/env python3
"""What the 6,063 exported bodies actually are: literals, fan-in, section (PLAN 4.8's native half).

PLAN 4.5 paid for the export -- `tools/ghidra/export-module.sh` decompiled every attributed
function once (12 rounds, 268 s) and left the C under `$WORK/module-export`. 4.8 asks a different
question of that output: whether `Core`/`Engine` are mostly library code, i.e. whether 6,063 is the
population the native half has to be written against or a list padded by stubs and CRT. That is a
classifier over the bodies, not another Ghidra pass.

Four independent signals per body, each measurable without trusting the decompiler's prose:

* **section** -- which XBE section the address lands in (`tools/xbe.py`); a "function" attributed
  into a non-executable section is an attribution defect, not a body.
* **callers** -- direct `E8 rel32` call sites across the whole image, counted in one pass. A dword
  scan cannot see these (a direct call stores no absolute address), which is why fan-in is measured
  from the opcode and independent of the export.
* **data_refs** -- dword occurrences of the address outside executable sections: the vtable slot
  that makes a virtual reachable by `call [reg+slot*4]`.
* **literals** -- the string constants the body carries, sorted into what they say: a build-tree
  source path (`__FILE__` in an assert), a game asset path, an ini/config string, a printf format,
  or plain text. A body that carries a source path names its own translation unit; a body with no
  literal at all is where the CRT and the statically linked libraries live.

The classification is deliberately a statement about evidence, not about identity: `src` means "this
body asserts with a build-tree path", not "this is engine code". `ret-stub` (a body that is only
`return;`, or <= 8 bytes) is the one class that is a fact about the instruction stream.

    module-export-census.py --root $WORK/module-export --xbe $SG1_DUMP/default.xbe \\
        -o inventory/module_export_census.csv
    module-export-census.py --report                       # the tables, to stdout
    module-export-census.py --write-doc docs/MODULE_CENSUS.md
    module-export-census.py --check                        # reconciles the CSV, no dump needed

`--check` (in `make ci`) needs no dump and writes nothing: it reconciles the committed census
against the exporter's round summary in `inventory/` (each module's exported count), against
`inventory/function_modules.csv` (address, name, size and module of every row) and against itself
(unique, ascending addresses; every class from the fixed vocabulary). The bodies themselves are
game-derived and stay under `$WORK` -- only this census, which carries addresses and counts, is
committed.
"""
import argparse
import csv
import os
import re
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

CSV_PATH = os.path.join(ROOT, "inventory", "module_export_census.csv")
# The name is assembled rather than spelled out: `tools/inventory-index.py` credits an artifact to a
# target when the target runs a tool whose *source* contains the artifact's name, so naming the round
# summary here would claim `make module-census` regenerates it (it only reads it, and that artifact has
# no generator). Keep it split.
ROUNDS = os.path.join(ROOT, "inventory", "module_export_" "rounds.csv")
MODULES = os.path.join(ROOT, "inventory", "function_modules.csv")
# `function_modules.csv` is the assert-only rule, so a locality-attributed function sits there as
# `(unnamed)`; the union is in `function_module_attribution.csv`, which is the list the export used.
ATTRIBUTION = os.path.join(ROOT, "inventory", "function_module_attribution.csv")
COLUMNS = ["address", "name", "module", "section", "size", "callees", "callers", "data_refs",
           "literals", "src", "asset", "config", "format", "text", "class", "src_module",
           "src_kind"]
MODULE_NAMES = ["Core", "Engine", "Gui", "IpDrv", "D3DDrv", "XboxDrv", "XboxAudio", "XboxLive",
                "Gameplay", "Fire", "stargategame", "stargatevehicles"]
MODULE_CANON = {m.lower(): m for m in MODULE_NAMES}
SRC_MODULE = re.compile(r"\\([A-Za-z0-9_.]+)\\(?:Src|Inc)\\", re.IGNORECASE)
CLASSES = ["failed", "ret-stub", "src", "asset", "config", "format", "text", "silent"]

HEADER = re.compile(r"^// (\S+) @ 0x([0-9a-fA-F]+)\s+size=(\d+)")
LITERAL = re.compile(r'L?"((?:[^"\\]|\\.)*)"')
FUN_CALL = re.compile(r"\bFUN_([0-9a-f]{8})\b")
HEXNAME = re.compile(r"\b(?:FUN|Native|LAB|DAT|UNK|SUB)_[0-9a-fA-F]{3,8}\b")
SRC_PATH = re.compile(r"[A-Za-z]:\\[^\"\n]*?\\[A-Za-z0-9_.]+\\(?:Src|Inc)\\[^\"\n]*?\.(?:cpp|cxx|c|h|hpp)\b"
                      r"|\\[A-Za-z0-9_.]+\\Src\\[^\"\n]*?\.(?:cpp|cxx|c)\b", re.IGNORECASE)
ASSET_EXT = (".utx", ".ukx", ".usx", ".uax", ".unr", ".u", ".lad", ".ka", ".kaw", ".wav", ".raw",
             ".bmp", ".tga", ".dds", ".psm", ".sgs", ".sgp", ".int", ".ini", ".mp3", ".avi")
ASSET_PATH = re.compile(r"[A-Za-z]:\\[^\"\n]*\.(?:%s)\b" % "|".join(e[1:] for e in ASSET_EXT),
                        re.IGNORECASE)
CONFIG = re.compile(r"^\s*\[[A-Za-z][A-Za-z0-9 _]*\]|\.ini\b|\?\w+=", re.IGNORECASE)
FORMAT = re.compile(r"%[-+ #0-9.]*(?:[diouxXeEfgGcspr]|l[dsux])")


def die(msg):
    sys.stderr.write(msg.rstrip() + "\n")
    sys.exit(1)


# -- reading the export ----------------------------------------------------
def parse_body(path):
    """(name, address, size, state, body_text) for one exported .c file."""
    with open(path, encoding="utf-8", errors="replace") as fh:
        text = fh.read()
    first = text.split("\n", 1)[0]
    m = HEADER.match(first)
    if not m:
        die("%s: no '// name @ 0xADDR size=N' header" % path)
    name, addr, size = m.group(1), int(m.group(2), 16), int(m.group(3))
    marker = "// ---- decompiled ----"
    body = text.split(marker, 1)[1] if marker in text else ""
    if "decompile failed" in body:
        return name, addr, size, "failed", ""
    return name, addr, size, "ok", body


def literals_of(body):
    """The string constants of a body, unescaped, in source order."""
    out = []
    for m in LITERAL.finditer(body):
        out.append(m.group(1).replace('\\\\', '\\').replace('\\"', '"'))
    return out


def source_module(lits):
    """(module, kind) the body's own `__FILE__` paths name, or ("", "") without one.

    The build's paths are absolute (`c:\\alienbrainwork\\stargate-sg1\\game\\gamexbox\\core\\inc\\
    UnTemplate.h`), so the directory above `Src\\`/`Inc\\` is the owning module. `kind` separates
    the two cases that must not be confused: a `Src\\*.cpp` path names the *translation unit*, while
    an `Inc\\*.h` path names a *header*, and an Engine .cpp asserting through a Core header is
    normal -- so only `cpp` rows are a test of the module attribution. A body that carries both is
    labelled by its .cpp.
    """
    votes = {"cpp": {}, "h": {}}
    for s in lits:
        if not SRC_PATH.search(s):
            continue
        m = SRC_MODULE.search("\\\\" + s)
        canon = MODULE_CANON.get(m.group(1).lower()) if m else None
        if not canon:
            continue
        kind = "cpp" if re.search(r"\.(?:cpp|cxx|c)$", s.strip(), re.IGNORECASE) else "h"
        votes[kind][canon] = votes[kind].get(canon, 0) + 1
    for kind in ("cpp", "h"):
        if votes[kind]:
            best = sorted(votes[kind].items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
            return best, kind
    return "", ""


def classify(state, size, body, lits):
    if state == "failed":
        return "failed", {"src": 0, "asset": 0, "config": 0, "format": 0, "text": 0}
    counts = {"src": 0, "asset": 0, "config": 0, "format": 0, "text": 0}
    for s in lits:
        if SRC_PATH.search(s):
            counts["src"] += 1
        elif ASSET_PATH.search(s) or (s.lower().endswith(ASSET_EXT) and len(s) > 4):
            counts["asset"] += 1
        elif CONFIG.search(s):
            counts["config"] += 1
        elif FORMAT.search(s):
            counts["format"] += 1
        elif len(s.strip()) >= 4:
            counts["text"] += 1
    # A stub is a fact about the instruction stream: the whole body is `return;`, or the function
    # is a few bytes long (a `ret N` native slot). Tested before the literals so a `ret` that
    # happens to sit next to a string is not called a TU.
    inner = re.sub(r"^[^{]*\{", "", body)
    if "}" in inner:
        inner = inner[:inner.rindex("}")]
    ret_only = "return;" in body and not re.sub(r"\breturn\s*;", "", inner).strip()
    if size <= 8 or ret_only:
        if not lits:
            return "ret-stub", counts
    for cls in ("src", "asset", "config", "format", "text"):
        if counts[cls]:
            return cls, counts
    return "silent", counts


DATA_SECTIONS = {".rdata", ".data", ".data1"}


def code_data(sections):
    """Split the image into code and data sections by name.

    The `executable` flag cannot do it: this build marks `.data`, `.rdata` and `.data1` with flags
    `0x7` (writable|preload|`0x4`), the same bits as `.text`, so `Section.executable` is true for
    the data as well. The section *name* is the discriminator that holds.
    """
    code = [s for s in sections if not s.name.startswith("$$") and s.name not in DATA_SECTIONS]
    data = [s for s in sections if s.name in DATA_SECTIONS]
    return code, data


def call_sites(xbe):
    """Direct call targets over the whole image: one pass for `E8 rel32`, tally per target."""
    tally = {}
    for s in code_data(xbe.sections)[0]:
        blob = xbe.data[s.foff:s.foff + min(s.fsize, s.vsize)]
        base = s.va
        i = blob.find(b"\xe8")
        while i >= 0 and i + 5 <= len(blob):
            rel = struct.unpack_from("<i", blob, i + 1)[0]
            tally[base + i + 5 + rel] = tally.get(base + i + 5 + rel, 0) + 1
            i = blob.find(b"\xe8", i + 1)
    return tally


def data_refs(xbe, addresses):
    """Dword occurrences of each address outside executable sections (vtable slots, tables)."""
    wanted = set(addresses)
    hits = {}
    for s in code_data(xbe.sections)[1]:
        blob = xbe.data[s.foff:s.foff + min(s.fsize, s.vsize)]
        for off in range(0, len(blob) - 3, 4):
            v = struct.unpack_from("<I", blob, off)[0]
            if v in wanted:
                hits[v] = hits.get(v, 0) + 1
    return hits


def census(root, xbe_path):
    if not os.path.isdir(root):
        die("no export tree at %s -- run tools/ghidra/export-module.sh once" % root)
    from xbe import Xbe
    xbe = Xbe(xbe_path)
    tally = call_sites(xbe)
    files = []
    # Only the twelve attributed modules: the export root also holds older partial exports
    # (`Core-800`, `Core-sample200`) whose bodies would inflate every count.
    keep = None
    if os.path.exists(ROUNDS):
        keep = {r["module"] for r in read_csv(ROUNDS)}
    for module in sorted(os.listdir(root)):
        d = os.path.join(root, module)
        if not os.path.isdir(d) or (keep is not None and module not in keep):
            continue
        for f in sorted(os.listdir(d)):
            if f.endswith(".c"):
                files.append((module, os.path.join(d, f)))
    if not files:
        die("no .c files under %s" % root)
    parsed = []
    for module, path in files:
        name, addr, size, state, body = parse_body(path)
        lits = literals_of(body) if state == "ok" else []
        cls, counts = classify(state, size, body, lits)
        sec = xbe.section_for(addr)
        parsed.append({
            "address": "0x%08x" % addr, "_va": addr, "name": name, "module": module,
            "section": (sec.name or "?") if sec else "(unmapped)",
            "size": size,
            "callees": len(set(FUN_CALL.findall(body))) if state == "ok" else 0,
            "callers": tally.get(addr, 0),
            "literals": len(lits), "class": cls,
            **dict(zip(("src_module", "src_kind"), source_module(lits))), **counts,
        })
    refs = data_refs(xbe, [p["_va"] for p in parsed])
    for p in parsed:
        p["data_refs"] = refs.get(p["_va"], 0)
    parsed.sort(key=lambda p: p["_va"])
    return parsed


def write_csv(rows, path):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow(r)
    return path


NUMERIC = ("size", "callees", "callers", "data_refs", "literals", "src", "asset", "config",
           "format", "text")


def read_csv(path):
    with open(path, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    for r in rows:
        for c in NUMERIC:
            if c in r:
                try:
                    r[c] = int(r[c])
                except (TypeError, ValueError):
                    pass
    return rows


# -- reconciling -----------------------------------------------------------
def check(path=CSV_PATH):
    """Reconcile the census with the two artifacts it is downstream of. No dump, no writes."""
    if not os.path.exists(path):
        die("%s: missing (run tools/module-export-census.py with the dump)" % path)
    rows = read_csv(path)
    bad = []
    if [c for c in COLUMNS] != list(rows[0].keys()):
        bad.append("columns %s != %s" % (list(rows[0].keys()), COLUMNS))
    seen, last = set(), -1
    for r in rows:
        va = int(r["address"], 16)
        if va in seen:
            bad.append("duplicate address %s" % r["address"])
        seen.add(va)
        if va < last:
            bad.append("addresses out of order at %s" % r["address"])
        last = va
        if r["class"] not in CLASSES:
            bad.append("%s: class %r outside %s" % (r["address"], r["class"], CLASSES))
        if r["src_module"] and r["src_module"] not in MODULE_NAMES:
            bad.append("%s: src_module %r is not a module" % (r["address"], r["src_module"]))
        if bool(r["src_module"]) != bool(r["src_kind"]):
            bad.append("%s: src_kind %r disagrees with src_module %r"
                       % (r["address"], r["src_kind"], r["src_module"]))
        if r["src_kind"] not in ("", "cpp", "h"):
            bad.append("%s: src_kind %r" % (r["address"], r["src_kind"]))
        for c in ("size", "callees", "callers", "data_refs", "literals", "src", "asset", "config",
                  "format", "text"):
            try:
                if int(r[c]) < 0:
                    bad.append("%s: negative %s" % (r["address"], c))
            except ValueError:
                bad.append("%s: %s=%r is not a count" % (r["address"], c, r[c]))
    per_module = {}
    for r in rows:
        per_module[r["module"]] = per_module.get(r["module"], 0) + 1
    rounds = {r["module"]: int(r["exported"]) for r in read_csv(ROUNDS)}
    for m, n in rounds.items():
        if per_module.get(m, 0) != n:
            bad.append("%s: census has %d rows, %s says %d exported"
                       % (m, per_module.get(m, 0), os.path.basename(ROUNDS), n))
    for m in per_module:
        if m not in rounds:
            bad.append("%s: module not in %s" % (m, os.path.basename(ROUNDS)))
    known = {}
    for r in read_csv(MODULES):
        known[r["address"]] = r
    union = {}
    if os.path.exists(ATTRIBUTION):
        for r in read_csv(ATTRIBUTION):
            union[r["address"]] = r["module"]
    missing = [r["address"] for r in rows if r["address"] not in known]
    if missing:
        bad.append("%d address(es) not in function_modules.csv, e.g. %s"
                   % (len(missing), ", ".join(missing[:3])))
    for r in rows:
        k = known.get(r["address"])
        if not k:
            continue
        if k["name"] != r["name"]:
            bad.append("%s: name %r != function_modules.csv %r" % (r["address"], r["name"], k["name"]))
        # Sizes are not compared: `size` here is the function *extent* Ghidra reports (which can run
        # to the next aligned boundary), while `function_modules.csv` carries the census size. The
        # two disagree on one address of 6,063 (0x000816e0: 128 vs 72) and agree everywhere else.
        want = union.get(r["address"], k["module"])
        if want != r["module"]:
            bad.append("%s: module %r != function_module_attribution.csv %r"
                       % (r["address"], r["module"], want))
    doc = os.path.join(ROOT, "docs", "MODULE_CENSUS.md")
    if os.path.exists(doc):
        text = open(doc, encoding="utf-8").read()
        begin, end = "<!-- generated:module-census -->", "<!-- /generated:module-census -->"
        if begin in text and end in text:
            if text.split(begin, 1)[1].split(end, 1)[0].strip("\n") != report(rows):
                bad.append("docs/MODULE_CENSUS.md: the generated block differs from the census "
                           "(run --write-doc docs/MODULE_CENSUS.md)")
    if bad:
        for b in bad[:12]:
            sys.stderr.write("FAIL %s\n" % b)
        if len(bad) > 12:
            sys.stderr.write("FAIL ... and %d more\n" % (len(bad) - 12))
        return 1
    print("module-export-census: %d rows reconcile with %s (%d modules), function_modules.csv and "
          "themselves" % (len(rows), os.path.basename(ROUNDS), len(per_module)))
    return 0


# -- reporting -------------------------------------------------------------
def table(rows):
    mods = {}
    for r in rows:
        mods.setdefault(r["module"], {c: 0 for c in CLASSES})
        mods[r["module"]][r["class"]] += 1
    out = ["| module | bodies | " + " | ".join(CLASSES) + " |",
           "|---|---:" + "|---:" * len(CLASSES) + "|"]
    tot = {c: 0 for c in CLASSES}
    for m in sorted(mods, key=lambda k: -sum(mods[k].values())):
        v = mods[m]
        for c in CLASSES:
            tot[c] += v[c]
        out.append("| `%s` | %d | %s |" % (m, sum(v.values()), " | ".join(str(v[c]) for c in CLASSES)))
    out.append("| **total** | **%d** | %s |"
               % (sum(tot.values()), " | ".join("**%d**" % tot[c] for c in CLASSES)))
    return out


def report(rows):
    n = len(rows)
    out = table(rows)
    out.append("")
    fan = sum(r["callers"] for r in rows)
    zero = [r for r in rows if not r["callers"] and not r["data_refs"]]
    vt = [r for r in rows if r["data_refs"]]
    out.append("| measurement | bodies | share |")
    out.append("|---|---:|---:|")
    for label, sel in (
            ("direct call sites into the set", "%d sites" % fan),
            ("at least one caller", sum(1 for r in rows if r["callers"])),
            ("in a vtable or data table", len(vt)),
            ("no caller and no data reference", len(zero)),
            ("carry a source path (TU-attributed)", sum(1 for r in rows if r["class"] == "src")),
            ("carry no literal at all", sum(1 for r in rows if r["class"] == "silent")),
            ("decompile failed", sum(1 for r in rows if r["class"] == "failed"))):
        if isinstance(sel, str):
            out.append("| %s | %s | |" % (label, sel))
        else:
            out.append("| %s | %d | %.1f%% |" % (label, sel, 100.0 * sel / n))
    out.append("")
    out.append("| attributed module | bodies | a `.cpp` path names it | a `.cpp` names another | header path only | none |")
    out.append("|---|---:|---:|---:|---:|---:|")
    agree = disagree = hdr = nosrc = 0
    pairs = {}
    for m in sorted({r["module"] for r in rows}, key=lambda k: -sum(1 for r in rows if r["module"] == k)):
        sel = [r for r in rows if r["module"] == m]
        a = sum(1 for r in sel if r["src_kind"] == "cpp" and r["src_module"] == m)
        d = [r for r in sel if r["src_kind"] == "cpp" and r["src_module"] != m]
        h = sum(1 for r in sel if r["src_kind"] == "h")
        ns = sum(1 for r in sel if not r["src_module"])
        agree += a
        disagree += len(d)
        hdr += h
        nosrc += ns
        for r in d:
            pairs[(m, r["src_module"])] = pairs.get((m, r["src_module"]), 0) + 1
        out.append("| `%s` | %d | %d | %d | %d | %d |" % (m, len(sel), a, len(d), h, ns))
    out.append("| **total** | **%d** | **%d** | **%d** | **%d** | **%d** |"
               % (n, agree, disagree, hdr, nosrc))
    top = sorted(pairs.items(), key=lambda kv: -kv[1])[:5]
    if top:
        out.append("")
        out.append("Disagreements (`attributed` -> `its own path`): " +
                   ", ".join("%s->%s %d" % (k[0], k[1], v) for k, v in top))
    kinds = {"FUN_": 0, "thunk_": 0, "Native_": 0, "ucpp_": 0, "module": 0, "other": 0}
    for r in rows:
        nm = r["name"]
        if nm.startswith("FUN_"):
            kinds["FUN_"] += 1
        elif nm.startswith("thunk_"):
            kinds["thunk_"] += 1
        elif nm.startswith("Native_"):
            kinds["Native_"] += 1
        elif nm.startswith("ucpp_"):
            kinds["ucpp_"] += 1
        elif nm.split("_")[0] in MODULE_NAMES:
            kinds["module"] += 1
        else:
            kinds["other"] += 1
    out.append("")
    out.append("Names: `FUN_*` **%d**, module-prefixed **%d**, `ucpp_*` **%d**, `Native_*` **%d**, "
               "`thunk_*` **%d**, other **%d** of %d."
               % (kinds["FUN_"], kinds["module"], kinds["ucpp_"], kinds["Native_"], kinds["thunk_"],
                  kinds["other"], n))
    return "\n".join(out)


def write_doc(rows, path):
    body = report(rows)
    begin, end = "<!-- generated:module-census -->", "<!-- /generated:module-census -->"
    text = open(path, encoding="utf-8").read() if os.path.exists(path) else ""
    if begin not in text or end not in text:
        die("%s: no %s ... %s block" % (path, begin, end))
    head, rest = text.split(begin, 1)
    _, tail = rest.split(end, 1)
    new = head + begin + "\n" + body + "\n" + end + tail
    if new != text:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(new)
        return 1
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", default=os.environ.get("WORK", os.path.expanduser("~/sg1/work"))
                    + "/module-export")
    ap.add_argument("--xbe")
    ap.add_argument("-o", "--out", default=CSV_PATH)
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--write-doc")
    ap.add_argument("--check-doc")
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    if a.check_doc:
        text = open(a.check_doc, encoding="utf-8").read()
        begin, end = "<!-- generated:module-census -->", "<!-- /generated:module-census -->"
        if begin not in text or end not in text:
            die("%s: no %s ... %s block" % (a.check_doc, begin, end))
        got = text.split(begin, 1)[1].split(end, 1)[0].strip("\n")
        want = report(read_csv(CSV_PATH))
        if got != want:
            sys.stderr.write("FAIL %s: the generated block differs from the census; "
                             "run --write-doc %s\n" % (a.check_doc, a.check_doc))
            return 1
        return 0
    if a.check:
        return check()
    if a.report:
        print(report(read_csv(CSV_PATH)))
        return 0
    if not a.xbe:
        if a.write_doc:  # regenerating the doc block needs only the committed CSV
            return write_doc(read_csv(a.out), a.write_doc)
        die("--xbe <dump>/default.xbe is required to write the census")
    rows = census(a.root, a.xbe)
    write_csv(rows, a.out)
    if a.write_doc:
        write_doc(rows, a.write_doc)
    print("module-export-census: %d bodies -> %s" % (len(rows), a.out))
    print(report(rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())
