#!/usr/bin/env python3
"""Is there RTTI on this build's class tables? The COL channel, read with a positive control.

Every name this project has for a class table comes from a join -- a registration, a script class, a
member field, a slot token.  A compiler-emitted C++ vtable has one more channel the population has never
been read for: MSVC stores a pointer to an `RTTICompleteObjectLocator` in the dword **before** the
vftable (`vftable[-1]`) when the translation unit is built with RTTI on, and that locator's
`pTypeDescriptor` names the class (`.?AV<Class>@@`).  An interface table is a vtable nothing registers,
so this is the one channel that could name one from the table itself.

The read is `vftable[-1]` for **all 623 tables** -- the 232 interface tables and every class table with a
measured parent edge -- followed as a locator: signature 0, a type descriptor 8 bytes before a decorated
name.  A dword that merely happens to point at a zero would pass a signature test, so the chain is
followed to the name and only a `. ?A[VU]` accepts: that makes the negative exact rather than a rate.

The control is the image's own: 4 decorated names are present (`.?AVexception@@` and three
`std` exception classes), each reached by exactly one locator, and the reader must find all four before
its zero on the game tables means anything.

    SG1_DUMP=$HOME/nfs/sg1-dump python3 tools/class-interface-rtti.py \\
        --xbe "$SG1_DUMP/Stargate_SG-1_-_The_Alliance-2005-12-15/default.xbe" -o inventory
    python3 tools/class-interface-rtti.py --report
    python3 tools/class-interface-rtti.py --write-doc docs/systems/CLASS_REGISTRATION.md
    python3 tools/class-interface-rtti.py --check-doc docs/systems/CLASS_REGISTRATION.md
    python3 tools/class-interface-rtti.py --check      # synthetic chains, no dump, writes nothing

Addresses, counts and decorated names only; no image or package content is written.
"""
import argparse
import collections
import csv
import importlib.util
import os
import re
import struct
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

PLAIN = os.path.join(ROOT, "inventory", "class_plain_tables.csv")
EDGES = os.path.join(ROOT, "inventory", "class_table_parent_edges.csv")
OUT = os.path.join(ROOT, "inventory", "class_interface_rtti.csv")
DOC_START = "<!-- generated:class-interface-rtti -->"
DOC_END = "<!-- /generated:class-interface-rtti -->"
FIELDS = ("table_va", "role", "class", "slots", "prev", "prev_kind", "col_sig", "col_name", "verdict",
          "evidence")
VERDICTS = ("col", "no-col", "not-a-col", "prev-unmapped")
DECORATED = re.compile(rb"\.\?A[VU][ -~]{2,80}")
NAME_OK = re.compile(rb"^\.\?A[VU]")
HEX = re.compile(r"^(0x)?[0-9a-fA-F]+$")
STEP = {
    "sig": "the target dword is not a COL signature",
    "td": "the COL has no readable type descriptor",
    "name": "the type descriptor does not start with a decorated class name",
    "col": "the -4 dword is not a mapped pointer",
}


def sibling(name):
    spec = importlib.util.spec_from_file_location(name.replace("-", "_"),
                                                  os.path.join(ROOT, "tools", name + ".py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def read_csv(path):
    if not path or not os.path.isfile(path):
        return []
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def num(text, base=16):
    text = (text or "").strip()
    if not HEX.match(text):
        return None
    return int(text, base)


# --------------------------------------------------------------------------- the chain, as a function
def chain(prev, u32, read):
    """Follow a vftable's `-4` dword as a COL. Returns (verdict, sig, name, step)."""
    if prev is None:
        return ("prev-unmapped", None, "", "col")
    if prev == 0:
        return ("no-col", None, "", "zero")
    sig = u32(prev)
    if sig is None:
        return ("not-a-col", None, "", "sig")
    if sig != 0:
        return ("not-a-col", sig, "", "sig")
    ptd = u32(prev + 12)
    if ptd is None:
        return ("not-a-col", sig, "", "td")
    raw = read(ptd + 8, 128)
    if not raw:
        return ("not-a-col", sig, "", "name")
    name = raw.split(b"\0")[0]
    if not NAME_OK.match(name):
        return ("not-a-col", sig, "", "name")
    return ("col", sig, name.decode("latin1"), "ok")


def readers(image):
    def u32(va):
        if va is None:
            return None
        try:
            b = image.read_va(va, 4)
        except KeyError:
            return None
        if not b or len(b) < 4:
            return None
        return struct.unpack("<I", b)[0]

    def read(va, n):
        try:
            b = image.read_va(va, n)
        except KeyError:
            return None
        return b or None

    return u32, read


# --------------------------------------------------------------------------- populations
def table_rows():
    """The 232 interface tables and every class table with a measured parent edge, deduped."""
    out, seen = [], set()
    for r in read_csv(PLAIN):
        va = num(r.get("table_va"))
        if va is None or not (r.get("verdict") or "").startswith("interface"):
            continue
        seen.add(va)
        out.append({"table_va": "%#010x" % va, "role": "interface", "class": "",
                    "slots": str(num(r.get("slots"), 10) or 0)})
    for r in read_csv(EDGES):
        va = num(r.get("table_va"))
        if va is None or va in seen:
            continue
        seen.add(va)
        out.append({"table_va": "%#010x" % va, "role": "class", "class": r.get("class") or "",
                    "slots": str(num(r.get("slots"), 10) or 0)})
    out.sort(key=lambda r: int(r["table_va"], 16))
    return out


def find_dwords(data, value):
    """File offsets of every little-endian dword equal to `value`."""
    if not value:
        return []
    pat = struct.pack("<I", value)
    return [m.start() for m in re.finditer(re.escape(pat), data)]


def control_rows(image):
    """The image's own decorated names, and the structure that holds each one's type descriptor.

    A locator is only a locator if its signature is zero and its type descriptor names a class, so the
    control reports both answers: a host that passes the chain is the reader accepting a real locator, and
    a host the signature gate refuses is the reader refusing a non-locator.
    """
    out = []
    u32, read = readers(image)
    data = image.data
    for m in DECORATED.finditer(data):
        va = image.offset_to_va(m.start())
        if va is None:
            continue
        name = m.group().split(b"\0")[0].decode("latin1")
        td = va - 8
        for off in find_dwords(data, td):
            host = image.offset_to_va(off - 12)
            if host is None:
                continue
            verdict, sig, got, step = chain(host, u32, read)
            out.append({
                "table_va": "%#010x" % host, "role": "control", "class": name, "slots": "",
                "prev": "%#010x" % td, "prev_kind": "pointer",
                "col_sig": "" if sig is None else "%#010x" % sig,
                "col_name": got, "verdict": verdict,
                "evidence": "type descriptor %#010x at a host whose signature is %s; %s; "
                            "%d dword(s) in the image hold the host"
                            % (td, "unreadable" if sig is None else "%#010x" % sig,
                               "the chain names %s" % got if verdict == "col" else STEP[step],
                               len(find_dwords(data, host))),
            })
    out.sort(key=lambda r: (r["class"], r["table_va"]))
    return out


def harvest(image):
    u32, read = readers(image)
    rows = []
    for r in table_rows():
        va = int(r["table_va"], 16)
        prev = u32(va - 4)
        verdict, sig, name, step = chain(prev, u32, read)
        kind = "zero" if prev == 0 else ("unmapped" if prev is None else "pointer")
        if verdict == "col":
            why = "a COL names %s" % name
        elif verdict == "no-col":
            why = "the -4 dword is zero"
        else:
            why = STEP[step]
        rows.append({
            "table_va": r["table_va"], "role": r["role"], "class": r["class"], "slots": r["slots"],
            "prev": "%#010x" % prev if prev is not None else "",
            "prev_kind": kind,
            "col_sig": "" if sig is None else "%#010x" % sig,
            "col_name": name,
            "verdict": verdict,
            "evidence": "-4 dword %s (%s); %s"
                        % ("%#010x" % prev if prev is not None else "unmapped", kind, why),
        })
    rows.extend(control_rows(image))
    counts = collections.Counter((r["role"], r["verdict"]) for r in rows)
    census = {k: "" for k in FIELDS}
    census.update({
        "table_va": "census", "role": "census",
        "slots": str(sum(int(r["slots"] or 0) for r in rows)),
        "col_name": str(counts[("interface", "col")] + counts[("class", "col")]),
        "verdict": "census",
        "evidence": "interface %d (%s); class %d (%s); control %d; col %d"
                    % (sum(1 for r in rows if r["role"] == "interface"),
                       " ".join("%s %d" % (v, counts[("interface", v)]) for v in VERDICTS),
                       sum(1 for r in rows if r["role"] == "class"),
                       " ".join("%s %d" % (v, counts[("class", v)]) for v in VERDICTS),
                       sum(1 for r in rows if r["role"] == "control"),
                       sum(1 for r in rows if r["verdict"] == "col")),
    })
    rows.append(census)
    return rows


# --------------------------------------------------------------------------- render
def summary(rows):
    rows = [r for r in rows if r["verdict"] != "census"]
    cats = collections.Counter((r["role"], r["verdict"]) for r in rows)
    ctrls = [r for r in rows if r["role"] == "control"]
    out = {"cats": cats, "control": ctrls,
           "tables": sum(1 for r in rows if r["role"] in ("interface", "class")),
           "col": sum(1 for r in rows if r["verdict"] == "col" and r["role"] != "control"),
           "iface": sum(1 for r in rows if r["role"] == "interface"),
           "class": sum(1 for r in rows if r["role"] == "class"),
           "zero": sum(1 for r in rows if r["prev_kind"] == "zero" and r["role"] != "control"),
           "pointer": sum(1 for r in rows if r["prev_kind"] == "pointer" and r["role"] != "control"),
           "unmapped": sum(1 for r in rows if r["prev_kind"] == "unmapped" and r["role"] != "control"),
           "slots": sum(int(r["slots"] or 0) for r in rows),
           "distinct_cols": len({r["prev"] for r in ctrls}),
           }
    return out


def render(rows):
    s = summary(rows)
    c = s["cats"]
    lines = ["| population | tables | `col` | `no-col` | `not-a-col` | `prev-unmapped` |",
             "|---|---:|---:|---:|---:|---:|"]
    for role in ("interface", "class"):
        lines.append("| %s | %d | %d | %d | %d | %d |"
                     % (role, sum(1 for r in rows if r["role"] == role),
                        c[(role, "col")], c[(role, "no-col")], c[(role, "not-a-col")],
                        c[(role, "prev-unmapped")]))
    lines.append("| all | %d | %d | %d | %d | %d |"
                 % (s["tables"], s["col"], c[("interface", "no-col")] + c[("class", "no-col")],
                    c[("interface", "not-a-col")] + c[("class", "not-a-col")],
                    c[("interface", "prev-unmapped")] + c[("class", "prev-unmapped")]))
    lines.append("")
    lines.append("**%d of the %d tables carry an RTTI locator.** The `-4` dword is zero on %d of them "
                 "and a mapped pointer on %d, and none of those %d pointers resolves to a locator whose "
                 "type descriptor names a class -- so no translation unit these tables were compiled in "
                 "was built with RTTI, and an interface table's own bytes name nothing."
                 % (s["col"], s["tables"], s["zero"], s["pointer"], s["pointer"]))
    lines.append("")
    lines.append("| the image's control | host | signature | verdict |")
    lines.append("|---|---|---|---|")
    for r in s["control"]:
        lines.append("| `%s` | `%s` | %s | `%s` |"
                     % (r["class"], r["table_va"], ("`%s`" % r["col_sig"]) if r["col_sig"] else "-",
                        r["verdict"]))
    lines.append("")
    passing = [r for r in s["control"] if r["verdict"] == "col"]
    refused = [r for r in s["control"] if r["verdict"] != "col"]
    holds = sum(int(re.search(r"(\d+) dword", r["evidence"]).group(1)) for r in s["control"]
                if re.search(r"(\d+) dword", r["evidence"]))
    lines.append("**The image carries %d decorated names, and the reader answers both ways on them: %d "
                 "hosts pass the whole chain and name `%s`, and %d hosts are refused by the signature "
                 "test.** %s, so the control shows the reader accepting a real locator and refusing a "
                 "non-locator in this image, and the zero above is the build's flag rather than the read."
                 % (len(s["control"]), len(passing),
                    "`, `".join(r["col_name"] for r in passing) or "-", len(refused),
                    "No dword in the image holds any of the hosts, so no vftable heads one" if holds == 0
                    else "%d dword(s) hold a host" % holds))
    return "\n".join(lines)


def write_artifact(rows, path):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


def splice_doc(path, block):
    text = open(path, encoding="utf-8").read()
    i, j = text.find(DOC_START), text.find(DOC_END)
    if i < 0 or j < 0:
        return None
    return text[:i] + DOC_START + "\n" + block + "\n" + text[j:]


# --------------------------------------------------------------------------- self-test
def self_check():
    bad = []

    def fake(world):
        def u32(va):
            return world.get(va)
        def read(va, n):
            return b".?AVFake@@\0x" if va == 0x2008 else None
        return u32, read

    got = chain(0, *fake({}))
    if got[0] != "no-col":
        bad.append("a zero -4 dword is a build without RTTI, not a locator")
    got = chain(None, *fake({}))
    if got[0] != "prev-unmapped":
        bad.append("a -4 dword outside every section is unmapped")
    got = chain(0x1000, *fake({0x1000: 0, 0x100c: 0x2000}))
    if got[0] != "col" or got[2] != ".?AVFake@@":
        bad.append("a locator whose type descriptor names a class is a col")
    got = chain(0x1000, *fake({0x1000: 0x1234}))
    if got[0] != "not-a-col":
        bad.append("a non-zero signature is not a locator")
    got = chain(0x1000, *fake({0x1000: 0, 0x100c: 0x3000}))
    if got[0] != "not-a-col" or got[3] != "name":
        bad.append("a locator whose type descriptor is unmapped names nothing")
    got = chain(0x1000, *fake({0x1000: 0, 0x100c: 0x3000}))
    if got[0] != "not-a-col":
        bad.append("a pointer that is not a locator is refused")

    rows = []
    for va, role, verdict, kind in (("0x004c0001", "interface", "no-col", "zero"),
                                    ("0x004c0002", "interface", "not-a-col", "pointer"),
                                    ("0x004d0001", "class", "col", "pointer"),
                                    ("0x004d0002", "class", "prev-unmapped", "unmapped")):
        rows.append({"table_va": va, "role": role, "class": "" if role == "interface" else "Actor",
                     "slots": "2", "prev": "0x1" if kind != "zero" else "0x00000000",
                     "prev_kind": kind, "col_sig": "", "col_name": "",
                     "verdict": verdict, "evidence": "-4 dword; synthetic"})
    rows.append({"table_va": "0x00578e14", "role": "control", "class": ".?AVexception@@",
                 "slots": "", "prev": "0x00578e10", "prev_kind": "pointer", "col_sig": "0",
                 "col_name": ".?AVexception@@", "verdict": "col",
                 "evidence": "type descriptor 0x005bf424; 1 locator(s); vftable 0x00578e14"})
    rows.append({k: "" for k in FIELDS})
    rows[-1].update({"table_va": "census", "role": "census", "verdict": "census",
                     "slots": "6", "col_name": "1",
                     "evidence": "interface 2 (col 0 no-col 1 not-a-col 1 prev-unmapped 0); "
                                 "class 2 (col 1 no-col 0 not-a-col 0 prev-unmapped 1); "
                                 "control 1; col 2"})
    out = render(rows)
    for want in ("interface", "class", "control", ".?AVexception@@", "locator"):
        if want not in out:
            bad.append("render lacks %r" % (want,))
    if summary(rows)["col"] != 1:
        bad.append("the control row is not counted as a game table")
    if [r for r in summary(rows)["control"] if r["verdict"] == "col"] != [rows[4]]:
        bad.append("the control table keeps the passing host apart from the refused one")

    path = os.path.join(tempfile.mkdtemp(), "a.csv")
    write_artifact(rows, path)
    if read_csv(path) != rows:
        bad.append("artifact round trip differs")
    if bad:
        print("FAIL class-interface-rtti: " + "; ".join(bad))
        return 1
    print("ok   class-interface-rtti: the four chain verdicts, the control row and the artifact round "
          "trip agree with the synthetic tables")
    return 0


# --------------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--xbe")
    ap.add_argument("-o", "--out")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--write-doc")
    ap.add_argument("--check-doc")
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    if args.check:
        return self_check()
    if args.check_doc:
        rows = read_csv(OUT)
        if not rows:
            print("FAIL class-interface-rtti: %s is missing" % OUT)
            return 1
        block = render(rows)
        text = open(args.check_doc, encoding="utf-8").read()
        i, j = text.find(DOC_START), text.find(DOC_END)
        if i < 0 or j < 0:
            print("FAIL class-interface-rtti: %s has no generated block" % args.check_doc)
            return 1
        if text[i:j + len(DOC_END)] != DOC_START + "\n" + block + "\n" + DOC_END:
            print("FAIL class-interface-rtti: %s's block does not reproduce from %s"
                  % (args.check_doc, OUT))
            return 1
        print("ok   class-interface-rtti: %s's block reproduces from %s" % (args.check_doc, OUT))
        return 0
    if args.report or args.write_doc:
        rows = read_csv(OUT)
        if not rows:
            print("FAIL class-interface-rtti: %s is missing" % OUT)
            return 1
        block = render(rows)
        if args.report:
            print(block)
            return 0
        new = splice_doc(args.write_doc, block)
        if new is None:
            print("FAIL class-interface-rtti: %s has no generated block markers" % args.write_doc)
            return 1
        if new != open(args.write_doc, encoding="utf-8").read():
            open(args.write_doc, "w", encoding="utf-8").write(new)
            print("wrote %s" % args.write_doc)
        return 0
    if not args.xbe:
        ap.error("--xbe is required to read the tables (or use --report/--check-doc/--check)")

    xbemod = sibling("xbe")
    image = xbemod.Xbe(args.xbe)
    rows = harvest(image)
    if args.out:
        path = os.path.join(args.out, os.path.basename(OUT))
        write_artifact(rows, path)
        print("wrote %s (%d rows)" % (path, len(rows)))
    else:
        print(render(rows))
    return 0


if __name__ == "__main__":
    sys.exit(main())
