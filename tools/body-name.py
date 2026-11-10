#!/usr/bin/env python3
"""What the image can name by itself: a body's own literals, and its byte-identical twins (PLAN 4.2/4.8).

`docs/NATIVE.md` counts 13,037 `FUN_*` functions. PLAN 4.2 would name most of them from MSVC/Xbox-SDK
signature databases this repository may not carry (`docs/UNKNOWNS.md` U9), so this tool measures how far
the *image itself* reaches -- the question that decides how much of 4.2 is ours to do.

Two readings, both from the exported bodies `export-module.sh` leaves under `$WORK/module-export`:

* **a method literal.** UE2's log and assert text often names the method that emitted it, and MSVC keeps
  the literal in the body, so a body carrying exactly **one** distinct `Class::Method` token names itself.
  Two or more is a refusal (a body that logs about another object is common here); the row records the
  refusal as `ambiguous method literals` rather than choosing.
* **a byte-identical twin.** A body whose bytes match a *named* body's is the same function folded by the
  linker (COMDAT). A group with exactly one named member names its unnamed members; groups under 32 bytes
  are refused, because two different getters compile to the same six instructions.

Every body gets a row, so the artifact is the census as well as the candidate list: `already named`,
`own method literal`, `ambiguous method literals`, `byte-identical twin of a named body`,
`source file only` (its assert names a `.cpp` but no method) or `none`. The report is computed from the
artifact alone, which is what lets `--check-doc` answer without the dump.

    body-name.py --xbe DUMP/default.xbe -o inventory/body_names.csv --write-doc docs/NAMING.md \
        --write-overrides
    body-name.py --report                        # the generated tables, to stdout
    body-name.py --check                         # synthetic bodies, no dump, writes nothing
"""
import argparse
import csv
import hashlib
import os
import re
import shutil
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
CSV_PATH = os.path.join(ROOT, "inventory", "body_names.csv")
OVERRIDES = os.path.join(ROOT, "symbols", "xbe_overrides.csv")
DOC_MARK = ("<!-- generated:body-name -->", "<!-- /generated:body-name -->")

COLUMNS = ["module", "address", "name", "size", "evidence", "new_name", "class", "method", "source_file",
           "literals", "twin_of"]
OVERRIDE_COLUMNS = ["address", "name", "evidence", "class", "outer", "object_size", "assert_line",
                    "module", "source_file", "function_size"]
MODULES = ["Core", "Engine", "Gui", "IpDrv", "D3DDrv", "XboxDrv", "XboxAudio", "XboxLive",
           "Gameplay", "Fire", "stargategame", "stargatevehicles"]

HEADER = re.compile(r"^// (\S+) @ 0x([0-9a-fA-F]+)\s+size=(\d+)")
MARKER = "// ---- decompiled ----"
LITERAL = re.compile(r'L?"((?:[^"\\]|\\.)*)"')
PAIR = re.compile(r"([A-Za-z_]\w*)::(~?[A-Za-z_]\w*)")
SRC_PATH = re.compile(r"[\\/]{1,2}([A-Za-z0-9_.]+)\.(cpp|cxx|c|h|hpp)\b", re.IGNORECASE)
ADDR_NAME = re.compile(r"^(?:thunk_)?FUN_[0-9a-f]{8}$")
# `Class::Method` tokens that are an assert *expression* about another object, not a name.
NOISE = {"T::StaticClass", "UObject::StaticClass", "UClass::StaticClass", "FName::StaticInit",
         "NoInitProperties::StaticClass"}
NAMED = "already named"
METHOD = "own method literal"
AMBIGUOUS = "ambiguous method literals"
TWIN = "byte-identical twin of a named body"
FILE_ONLY = "source file only"
NONE = "none"


def die(msg):
    sys.stderr.write(msg.rstrip() + "\n")
    sys.exit(1)


# -- reading the export ----------------------------------------------------
def parse_body(path):
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


def read_bodies(roots):
    """Every exported body under each root/<module>, ascending by address."""
    rows = []
    for root in roots:
        for module in MODULES:
            where = os.path.join(root, module)
            if not os.path.isdir(where):
                continue
            for fn in sorted(os.listdir(where)):
                if not fn.endswith(".c"):
                    continue
                name, addr, size, body = parse_body(os.path.join(where, fn))
                lits = [m.group(1).replace('\\"', '"') for m in LITERAL.finditer(body)]
                pairs = {}
                for s in lits:
                    for m in PAIR.finditer(s):
                        tok = "%s::%s" % (m.group(1), m.group(2))
                        if tok not in NOISE:
                            pairs[tok] = pairs.get(tok, 0) + 1
                src = ""
                for s in lits:
                    m = SRC_PATH.search(s)
                    if m:
                        src = "%s.%s" % (m.group(1), m.group(2))
                        break
                rows.append({
                    "module": module, "address": "0x%08x" % addr, "_va": addr, "name": name,
                    "size": size, "unnamed": 1 if ADDR_NAME.match(name) else 0,
                    "pairs": sorted(pairs), "_lits": len(set(lits)), "source_file": src,
                })
    rows.sort(key=lambda r: (r["_va"], r["module"]))
    return rows


def twin_groups(rows, bytefn, min_size=32):
    """{address: (name, address)} for unnamed bodies whose byte-identical group has one named member."""
    groups = {}
    for r in rows:
        if r["size"] < min_size or r["size"] > 65536:
            continue
        data = bytefn(r["_va"], r["size"])
        if data is None or len(data) != r["size"]:
            continue
        groups.setdefault((r["size"], hashlib.sha1(data).hexdigest()), []).append(r)
    out, stats = {}, {"groups": 0, "unnamed": 0, "named_groups": 0, "reachable": 0}
    for _key, members in groups.items():
        if len(members) < 2:
            continue
        stats["groups"] += 1
        named = [m for m in members if not m["unnamed"]]
        unnamed = [m for m in members if m["unnamed"]]
        stats["unnamed"] += len(unnamed)
        if len(named) != 1 or not unnamed:
            continue
        stats["named_groups"] += 1
        for m in unnamed:
            out[m["_va"]] = (named[0]["name"], named[0]["address"])
            stats["reachable"] += 1
    return out, stats


def classify(rows, twins):
    """One row per body: what, if anything, names it."""
    out = []
    for r in rows:
        new = cls = method = twin = ""
        source = r["source_file"]
        if not r["unnamed"]:
            evidence = NAMED
        elif len(r["pairs"]) == 1:
            evidence = METHOD
            tok = r["pairs"][0]
            cls, method = tok.split("::", 1)
            new = ("%s_%s" % (cls, method)).replace("~", "dtor_").replace("::", "_")
        elif len(r["pairs"]) > 1:
            evidence = AMBIGUOUS
        elif r["_va"] in twins:
            evidence = TWIN
            new, twin = twins[r["_va"]]
        elif source:
            evidence = FILE_ONLY
        else:
            evidence = NONE
        out.append({"module": r["module"], "address": r["address"], "name": r["name"],
                    "size": r["size"], "evidence": evidence, "new_name": new, "class": cls,
                    "method": method, "source_file": source, "literals": r["_lits"], "twin_of": twin})
    return out


# -- artifact --------------------------------------------------------------
def write_csv(rows, path):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS, extrasaction="ignore", lineterminator="\n")
        w.writeheader()
        for r in rows:
            w.writerow(r)
    return path


def read_csv(path):
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def merge_overrides(path, rows):
    """Append the named candidates to `symbols/xbe_overrides.csv`; nothing is reordered or rewritten.

    The file is append-ordered and every row is ten fields, CRLF: `native-summary.py`, `module-split.py`
    and `game-modules.py` read it, so a rerun that inserts nothing must leave it byte-identical.
    """
    with open(path, newline="", encoding="utf-8") as fh:
        rd = csv.DictReader(fh)
        header = list(rd.fieldnames or [])
        existing = [dict(r) for r in rd]
    if header != OVERRIDE_COLUMNS:
        die("%s: header is %r, want %r" % (path, header, OVERRIDE_COLUMNS))
    have = {r["address"].lower() for r in existing}
    added = 0
    with open(path, "a", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh, lineterminator="\r\n")
        for c in rows:
            if not c["new_name"] or c["address"].lower() in have:
                continue
            why = ("own method literal %s::%s" % (c["class"], c["method"])
                   if c["evidence"] == METHOD else c["evidence"])
            w.writerow([c["address"], c["new_name"], why, c["class"], "", "", "", c["module"],
                        c["source_file"], str(c["size"])])
            have.add(c["address"].lower())
            added += 1
    return added


# -- report ----------------------------------------------------------------
def census(rows):
    """Counts by module and evidence class, from the artifact alone."""
    per, tot = {}, {"bodies": len(rows), "unnamed": 0, "named": 0, METHOD: 0, AMBIGUOUS: 0,
                    FILE_ONLY: 0, NONE: 0, "candidates": 0}
    for r in rows:
        e = per.setdefault(r["module"], {"bodies": 0, "unnamed": 0, "named": 0, METHOD: 0,
                                         AMBIGUOUS: 0, FILE_ONLY: 0, NONE: 0, "candidates": 0})
        e["bodies"] += 1
        if r["evidence"] == NAMED:
            e["named"] += 1
            tot["named"] += 1
        else:
            e["unnamed"] += 1
            tot["unnamed"] += 1
            if r["evidence"] in e:
                e[r["evidence"]] += 1
                tot[r["evidence"]] += 1
        if r["new_name"]:
            e["candidates"] += 1
            tot["candidates"] += 1
    return tot, per


def report(rows):
    tot, per = census(rows)
    out = ["| module | bodies | unnamed | named by a literal | ambiguous | source file only | no "
           "evidence |", "|---|---:|---:|---:|---:|---:|---:|"]
    for mod in sorted(per, key=lambda m: (-per[m]["bodies"], m)):
        e = per[mod]
        out.append("| `%s` | %d | %d | %d | %d | %d | %d |"
                   % (mod, e["bodies"], e["unnamed"], e[METHOD], e[AMBIGUOUS], e[FILE_ONLY], e[NONE]))
    out.append("| **all modules** | **%d** | **%d** | **%d** | **%d** | **%d** | **%d** |"
               % (tot["bodies"], tot["unnamed"], tot[METHOD], tot[AMBIGUOUS], tot[FILE_ONLY], tot[NONE]))
    out.append("")
    out.append("**%d of %d** unnamed bodies are named by their own text, **%d** name more than one method "
               "and are refused, **%d** name a source file but no method, and **%d** carry no naming "
               "evidence at all." % (tot[METHOD], tot["unnamed"], tot[AMBIGUOUS], tot[FILE_ONLY],
                                     tot[NONE]))
    named = [r for r in rows if r["new_name"]]
    if named:
        out.append("")
        out.append("| address | module | was | named | evidence | class/method |")
        out.append("|---|---|---|---|---|---|")
        for r in named:
            what = ("`%s`" % r["class"]) if r["evidence"] == METHOD else ("twin of `%s`" % r["twin_of"])
            out.append("| `%s` | `%s` | `%s` | `%s` | %s | %s |"
                       % (r["address"], r["module"], r["name"], r["new_name"], r["evidence"], what))
    return "\n".join(out)


def write_doc(text, path):
    begin, end = DOC_MARK
    doc = open(path, encoding="utf-8").read() if os.path.exists(path) else ""
    if begin not in doc or end not in doc:
        die("%s: no %s ... %s block" % (path, begin, end))
    head, rest = doc.split(begin, 1)
    _, tail = rest.split(end, 1)
    new = head + begin + "\n" + text + "\n" + end + tail
    if new != doc:
        with open(path, "w", encoding="utf-8") as fh:
            fh.write(new)
        return 1
    return 0


# -- the contract ----------------------------------------------------------
FIXTURE = {
    # a method literal names it
    "00001000_FUN_00001000.c": ("FUN_00001000", 0x1000, 60,
                                'void FUN_00001000(int p)\n{\n'
                                '  appErrorf(L"[VL:XBOXLIVE]UUIXInteraction::RegisterPlayer");\n'
                                '  appErrorf(L"c:\\\\alienbrainWork\\\\Game\\\\GameXBOX\\\\Engine\\\\Src\\\\Foo.cpp");\n}\n'),
    # two distinct tokens: a refusal, not a guess
    "00001040_FUN_00001040.c": ("FUN_00001040", 0x1040, 60,
                                'void FUN_00001040(void)\n{\n  appLog("Spline::Fix");\n'
                                '  appLog("Karma::Step");\n}\n'),
    # a file but no method
    "00001080_FUN_00001080.c": ("FUN_00001080", 0x1080, 60,
                                'void FUN_00001080(void)\n{\n  appErrorf(L"...\\\\Src\\\\Bar.cpp");\n}\n'),
    # already named: not a candidate
    "000010c0_Engine_Named.c": ("Engine_Named", 0x10c0, 60, 'void Engine_Named(void)\n{\n  return;\n}\n'),
    # unnamed, no evidence, but a twin of the named body above
    "00001100_FUN_00001100.c": ("FUN_00001100", 0x1100, 60, 'void FUN_00001100(void)\n{\n  return;\n}\n'),
    # a twin under 32 bytes is refused
    "00001140_FUN_00001140.c": ("FUN_00001140", 0x1140, 6, 'void FUN_00001140(void){}\n'),
    "00001160_Engine_Tiny.c": ("Engine_Tiny", 0x1160, 6, 'void Engine_Tiny(void){}\n'),
}


def check():
    """Read a synthetic module back, reconcile every evidence class, and prove the merge is idempotent."""
    tmp = tempfile.mkdtemp(prefix="body-name-")
    try:
        where = os.path.join(tmp, "mod", "Engine")
        os.makedirs(where)
        for fn, (name, addr, size, body) in FIXTURE.items():
            with open(os.path.join(where, fn), "w", encoding="utf-8") as fh:
                fh.write("// %s @ 0x%08x  size=%d\n// fixture\n\n%s\n%s\n" % (name, addr, size,
                                                                            MARKER, body))
        rows = read_bodies([os.path.join(tmp, "mod")])
        bad = []
        if len(rows) != len(FIXTURE):
            bad.append("read %d bodies, want %d" % (len(rows), len(FIXTURE)))
        by = {r["address"]: r for r in rows}
        if by.get("0x00001000", {}).get("pairs") != ["UUIXInteraction::RegisterPlayer"]:
            bad.append("method literal: %r" % (by.get("0x00001000", {}).get("pairs"),))
        if by.get("0x00001040", {}).get("pairs") != ["Karma::Step", "Spline::Fix"]:
            bad.append("two tokens not kept: %r" % (by.get("0x00001040", {}).get("pairs"),))
        if by.get("0x00001080", {}).get("source_file") != "Bar.cpp":
            bad.append("source file: %r" % (by.get("0x00001080", {}).get("source_file"),))

        def bytefn(va, size):
            return b"\x90" * size if va in (0x10c0, 0x1100, 0x1140, 0x1160) else None

        twins, _tstats = twin_groups([r for r in rows if r["_va"] in (0x10c0, 0x1100, 0x1140, 0x1160)],
                                    bytefn)
        if twins.get(0x1100, ("", ""))[0] != "Engine_Named":
            bad.append("twin not resolved: %r" % (twins,))
        if 0x1140 in twins:
            bad.append("a 6-byte twin was accepted")
        out = classify(rows, twins)
        ev = {r["address"]: r["evidence"] for r in out}
        want = {"0x00001000": METHOD, "0x00001040": AMBIGUOUS, "0x00001080": FILE_ONLY,
                "0x000010c0": NAMED, "0x00001100": TWIN, "0x00001140": NONE, "0x00001160": NAMED}
        for addr, e in want.items():
            if ev.get(addr) != e:
                bad.append("evidence %s: %r, want %r" % (addr, ev.get(addr), e))
        names = {r["address"]: r["new_name"] for r in out}
        if names.get("0x00001000") != "UUIXInteraction_RegisterPlayer":
            bad.append("candidate name: %r" % (names.get("0x00001000"),))
        if names.get("0x00001040"):
            bad.append("an ambiguous body was named")
        if names.get("0x00001100") != "Engine_Named":
            bad.append("twin candidate missing: %r" % (names.get("0x00001100"),))
        tot, _per = census(out)
        if tot["candidates"] != 2 or tot[AMBIGUOUS] != 1 or tot[FILE_ONLY] != 1:
            bad.append("census %r" % {k: tot[k] for k in ("candidates", AMBIGUOUS, FILE_ONLY)})
        # the report must reproduce from the artifact it writes, and the merge must be append-only
        art = write_csv(out, os.path.join(tmp, "body_names.csv"))
        if report(read_csv(art)) != report(out):
            bad.append("the report does not reproduce from its own artifact")
        ovr = os.path.join(tmp, "xbe_overrides.csv")
        with open(ovr, "w", newline="", encoding="utf-8") as fh:
            fh.write(",".join(OVERRIDE_COLUMNS) + "\r\n")
            fh.write("0x00002000,Core_Existing,test row,Core,,0,1,Core,,16\r\n")
        first = merge_overrides(ovr, out)
        with open(ovr, "rb") as fh:
            once = fh.read()
        second = merge_overrides(ovr, out)
        with open(ovr, "rb") as fh:
            twice = fh.read()
        if first != 2 or second != 0 or once != twice:
            bad.append("override merge: %d then %d row(s), file %s"
                       % (first, second, "stable" if once == twice else "changed"))
        if once.count(b"\r\n") != 4:
            bad.append("override merge: %d line(s), want 4" % once.count(b"\r\n"))
        if bad:
            for b in bad:
                sys.stderr.write("FAIL %s\n" % b)
            return 1
        print("body-name: 7 synthetic bodies in all six evidence classes, a refused short twin, a "
              "self-reproducing report and an idempotent override merge")
        return 0
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--root", action="append", default=None,
                    help="export root(s); default $WORK/module-export")
    ap.add_argument("--xbe", help="default.xbe, for the byte-identical twin pass")
    ap.add_argument("-o", "--out", default=CSV_PATH)
    ap.add_argument("--overrides", default=OVERRIDES, help="the override CSV candidates are appended to")
    ap.add_argument("--write-overrides", action="store_true",
                    help="append this run's candidates to --overrides (idempotent)")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--write-doc")
    ap.add_argument("--check-doc")
    ap.add_argument("--check", action="store_true")
    a = ap.parse_args()
    if a.check:
        return check()
    if a.check_doc:
        begin, end = DOC_MARK
        doc = open(a.check_doc, encoding="utf-8").read()
        if begin not in doc or end not in doc:
            die("%s: no %s ... %s block" % (a.check_doc, begin, end))
        got = doc.split(begin, 1)[1].split(end, 1)[0].strip("\n")
        if got != report(read_csv(a.out)):
            sys.stderr.write("FAIL %s: the generated block differs from %s (run --write-doc %s)\n"
                             % (a.check_doc, a.out, a.check_doc))
            return 1
        print("body-name: %s matches %s" % (a.check_doc, os.path.basename(a.out)))
        return 0
    if a.report:
        print(report(read_csv(a.out)))
        return 0
    roots = a.root or [os.path.join(os.environ.get("WORK", os.path.expanduser("~/sg1/work")),
                                    "module-export")]
    rows = read_bodies(roots)
    if not rows:
        die("no exported bodies under %s" % ", ".join(roots))
    twins, tstats = {}, {"groups": 0, "unnamed": 0, "named_groups": 0, "reachable": 0}
    if a.xbe:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from xbe import Xbe  # noqa: PLC0415 - only when a dump is given
        x = Xbe(a.xbe)

        def bytefn(va, size):
            try:
                return x.read_va(va, size)
            except Exception:  # noqa: BLE001 - an unmapped body has no bytes to hash
                return None

        twins, tstats = twin_groups(rows, bytefn)
    out = classify(rows, twins)
    write_csv(out, a.out)
    added = merge_overrides(a.overrides, out) if a.write_overrides else 0
    text = report(out)
    if a.write_doc:
        write_doc(text, a.write_doc)
    tot, _per = census(out)
    print("body-name: %d bodies, %d candidate name(s) from method literals, %d twin name(s), %d "
          "ambiguous, %d file-only, %d no evidence -> %s"
          % (tot["bodies"], tot[METHOD], tstats.get("reachable", 0), tot[AMBIGUOUS], tot[FILE_ONLY],
             tot[NONE], a.out))
    print("body-name: twin groups %d hold %d unnamed bodies, %d group(s) with exactly one name"
          % (tstats["groups"], tstats["unnamed"], tstats["named_groups"]))
    if a.write_overrides:
        print("body-name: %d row(s) added to %s" % (added, a.overrides))
    print(text)
    return 0


if __name__ == "__main__":
    sys.exit(main())
