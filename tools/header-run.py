#!/usr/bin/env python3
"""The four dwords `UStruct::Serialize` writes after `FriendlyName`, measured payload by payload.

`UStruct::Serialize` (`0x000392b0`) is the reader here: after `Super`, `Children` and `FriendlyName` it writes a
dword behind the `Ar.Ver() >= 0x77` gate, two more dwords, and the script. The build's packages are version **130**
(docs/BYTECODE.md, "The payload's tail"), so the gate fires and a `Function` payload carries a fixed **16-byte run**
between `FriendlyName` and the script -- the offset `--start` measured three ways, without saying what the run holds.

    python3 tools/header-run.py SYSTEM_DIR -o inventory     # writes inventory/header_run.csv
    python3 tools/header-run.py --report                    # the same read, printed
    python3 tools/header-run.py --write-doc docs/BYTECODE.md
    python3 tools/header-run.py --check-doc docs/BYTECODE.md
    python3 tools/header-run.py --check                     # no dump: reconciles with matching_baseline.csv

Four words, four answers (measured 2026-11-28 over all 18 packages, 12,472 `Function` exports):

* `w0` is **zero in every payload that carries the run** (12,360 of 12,360). It is the version-gated field, not a
  header value the locator can use: nothing about it varies, so a reader would gain nothing from it.
* `w1`/`w2` are a **source line and character offset** pair, and the position is the function's **body**, not its
  declaration: `w2` is the first character after the declaration's `{` (the column histogram peaks at 1 -- the
  first character of the body's first line -- and `w2 - first token` is 0 on 6,269 rows), and `line(w2)` equals `w1`
  on **10,967 of 11,477** (95.6%). The earlier reading that put the pair on the declaration line came from a text
  that still carried the raw buffer's leading fragment; the pair only reproduces after the class text is cut at its
  first `class ` / `//` token, which is the strip `pkg-script-text.py` makes.
* `w3` is the script's size **in memory**, with every compact index operand widened to four bytes -- the field
  `--sizefield` measures exactly (11,687 of 11,687); this tool prints the same word as a control, not as a new claim.

`--check` needs no dump: every row's status is counted per package and compared with `inventory/matching_baseline.csv`'s
`scripted` / `scriptless` / `operator` columns, which is the same corpus counted by a different generator.
"""
import argparse
import collections
import csv
import importlib.util
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = "header_run.csv"
DOC_START = "<!-- generated:header-run -->"
DOC_END = "<!-- /generated:header-run -->"
BASELINE = os.path.join("inventory", "matching_baseline.csv")

ROW = ["package", "class", "function", "status", "w0", "w1", "w2", "w3", "script_bytes",
       "decls", "decl_line", "decl_off", "line_at_w2", "shift", "w2_in_line"]

# `_SRC_FUNC` is `uscript-dis.py`'s own declaration pattern; the position of a declaration is the property this tool
# measures, so it reads the same pattern the rest of the project keys declarations with rather than a second copy.
UD = None


def ud():
    """`tools/uscript-dis.py` as a module: `locate()` and the declaration pattern are its answers, not ours."""
    global UD
    if UD is None:
        sys.path.insert(0, os.path.join(ROOT, "tools"))
        spec = importlib.util.spec_from_file_location("sg1_uscript_dis",
                                                      os.path.join(ROOT, "tools", "uscript-dis.py"))
        UD = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(UD)
    return UD


def packages(path):
    """Every `*.u` under a System directory, or the one package named."""
    if os.path.isdir(path):
        return [os.path.join(path, f) for f in sorted(os.listdir(path)) if f.lower().endswith(".u")]
    return [path]


def class_texts(pkg):
    """{class name: its `ScriptText`} once per package -- the buffer, from its own `TextBuffer` export."""
    out = {}
    for i, e in enumerate(pkg.exports, 1):
        if pkg.class_name_of(e) != "Class":
            continue
        for kid in pkg.exports:
            if kid["outer"] != i:
                continue
            if pkg.class_name_of(kid) != "TextBuffer" or pkg.name(kid["name"]) != "ScriptText":
                continue
            raw = pkg.data[kid["offset"]:kid["offset"] + kid["size"]]
            starts = [x for x in (raw.find(b"class ", 0, 4096), raw.find(b"//", 0, 4096)) if x >= 0]
            if not starts:
                break
            out[pkg.name(e["name"])] = raw[min(starts):].rstrip(b"\x00").decode("latin1", "replace")
            break
    return out


def outer_class(pkg, e):
    """The `Class` export a function reaches through its outers: a state declaration's outer is a state."""
    byidx = {i + 1: x for i, x in enumerate(pkg.exports)}
    cur, hops = e, 0
    while cur is not None and hops < 5:
        if pkg.class_name_of(cur) == "Class":
            return cur
        o = cur.get("outer", 0)
        cur = byidx.get(o) if o > 0 else None
        hops += 1
    return None


# `unaligned` in `locate()`'s vocabulary is the operator declaration, and that is what `matching_baseline.csv` calls
# `operator`; the mapping is one name per population, so a count in one artifact can be checked against the other.
STATUS = {"ok": "scripted", "scriptless": "scriptless", "unaligned": "operator", "short": "short",
          "empty": "empty"}


def census(path):
    """One row per `Function` export: the four words of its run and the declaration they point at."""
    import struct                                                 # noqa: PLC0415 - stdlib, kept local to the matcher
    from ue2pkg import Package                                    # noqa: PLC0415
    rows = []
    for one in packages(path):
        pkg = Package(one)
        pkg.read_names()
        pkg.read_imports()
        pkg.read_exports()
        pname = os.path.basename(one)
        texts = class_texts(pkg)
        for e in pkg.exports:
            if pkg.class_name_of(e) != "Function" or e.get("outer", 0) <= 0:
                continue
            status, body, start, end, name_end, _front = ud().locate(e, pkg.data)
            cls = outer_class(pkg, e)
            cname = pkg.name(cls["name"]) if cls else ""
            fname = pkg.name(e["name"])
            row = {"package": pname, "class": cname, "function": fname,
                   "status": STATUS.get(status, status), "script_bytes": end - start}
            pay = pkg.data[e["offset"]:e["offset"] + e["size"]]
            if name_end and name_end + 16 <= len(pay) and status in ("ok", "scriptless"):
                w = struct.unpack_from("<4I", pay, name_end)
            else:
                w = (None, None, None, None)
            row.update({"w0": w[0], "w1": w[1], "w2": w[2], "w3": w[3]})
            if status == "ok" and cname:
                text = texts.get(cname)
                if text:
                    hits = [m for m in ud()._SRC_FUNC.finditer(text)                  # noqa: SLF001 - shared pattern
                            if m.group(2).lower() == fname.lower()]
                    row["decls"] = len(hits)
                    if hits:
                        # The pair is a (line, offset); the candidate whose line is nearest `w1` is the one the
                        # declaration names, and an ambiguous name is flagged rather than dropped.
                        m = min(hits, key=lambda h: abs(text.count("\n", 0, h.start()) + 1 - w[1]))
                        ls = text.rfind("\n", 0, m.start()) + 1
                        le = text.find("\n", ls)
                        le = len(text) if le < 0 else le
                        row["decl_line"] = text.count("\n", 0, m.start()) + 1
                        row["decl_off"] = ls
                        row["line_at_w2"] = text.count("\n", 0, w[2]) + 1 if w[2] < len(text) else ""
                        row["shift"] = (row["line_at_w2"] - w[1]) if row["line_at_w2"] != "" else ""
                        row["w2_in_line"] = int(ls <= w[2] <= le)
            rows.append(row)
    return rows


def totals(rows):
    """The counts the page quotes and `--check` reconciles, all from the CSV."""
    c = collections.Counter()
    for r in rows:
        c["rows"] += 1
        c["status:" + r["status"]] += 1
        if r.get("w0") not in ("", None):
            c["run"] += 1
            c["w0_zero"] += 1 if int(r["w0"]) == 0 else 0
        if r["status"] == "scripted":
            c["decls_known"] += 1 if r.get("decls") not in ("", None) else 0
            c["decls_one"] += 1 if str(r.get("decls")) == "1" else 0
            c["no_decl"] += 1 if r.get("decls") == "0" else 0
            if r.get("shift") not in ("", None):
                s = int(r["shift"])
                c["shift_known"] += 1
                c["shift_zero"] += 1 if s == 0 else 0
                c["shift_two"] += 1 if abs(s) <= 2 else 0
                c["shift_neg"] += 1 if s < 0 else 0
            c["in_line"] += int(r.get("w2_in_line") or 0)
        if r.get("w3") not in ("", None) and r.get("script_bytes") not in ("", None):
            c["w3_ge"] += 1 if int(r["w3"]) >= int(r["script_bytes"]) else 0
            c["w3_eq"] += 1 if int(r["w3"]) == int(r["script_bytes"]) else 0
    return c


def read_rows(path=None):
    path = path or os.path.join(ROOT, "inventory", OUT)
    if not os.path.exists(path):
        raise SystemExit("header-run: %s is missing (run `make header-run`)" % path)
    with open(path, newline="", encoding="utf-8") as fh:
        rdr = csv.DictReader(fh)
        missing = [c for c in ROW if c not in (rdr.fieldnames or [])]
        if missing:
            raise SystemExit("header-run: %s has no %s column" % (path, ", ".join(missing)))
        return list(rdr)


def write_csv(rows, out):
    path = os.path.join(out, OUT)
    os.makedirs(out, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        wr = csv.DictWriter(fh, fieldnames=ROW, extrasaction="ignore")
        wr.writeheader()
        for r in rows:
            wr.writerow({k: ("" if r.get(k) is None else r.get(k)) for k in ROW})
    return path


def block(rows):
    """The page's block, built from the CSV alone so `--check-doc` and `--write-doc` cannot disagree."""
    c = totals(rows)
    n = c["run"]
    lines = []
    a = lines.append
    a("| measure | answer |")
    a("|---|---|")
    a("| `Function` payloads carrying the run | **%d** of %d exports (%d scripted, %d scriptless; the %d operator "
      "declarations write their three names in front of the header, which is why `locate()` refuses them) |"
      % (n, c["rows"], c["status:scripted"], c["status:scriptless"], c["status:operator"]))
    a("| `w0` == 0 | **%d of %d** -- the dword behind `Ar.Ver() >= 0x77`, live at version 130 and always zero |"
      % (c["w0_zero"], n))
    a("| `w3` >= the stored script | **%d of %d**; equal on %d, larger wherever the script carries a compact index "
      "operand (`w3` is the in-memory size `--sizefield` measures) |"
      % (c["w3_ge"], c["status:scripted"] + c["status:scriptless"], c["w3_eq"]))
    if c["shift_known"]:
        a("| `w1`/`w2` are a (line, offset) pair | the line `w2` falls on is `w1` on **%d** of %d, within two lines "
          "on **%d**, below it on %d |"
          % (c["shift_zero"], c["shift_known"], c["shift_two"], c["shift_neg"]))
    a("| declaration found in the shipped text | **%d** of %d scripted rows (%d name it more than once, %d not at "
      "all) |" % (c["decls_known"] - c["no_decl"], c["status:scripted"], c["decls_known"] - c["decls_one"],
                  c["no_decl"]))
    return "\n".join(lines)


def write_doc(path, rows):
    text = open(path, encoding="utf-8").read()
    if DOC_START not in text or DOC_END not in text:
        raise SystemExit("header-run: %s has no %s marker" % (path, DOC_START))
    head, rest = text.split(DOC_START, 1)
    _old, tail = rest.split(DOC_END, 1)
    out = head + DOC_START + "\n" + block(rows) + "\n" + DOC_END + tail
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(out)
    return 0


def check_doc(path, rows):
    text = open(path, encoding="utf-8").read()
    if DOC_START not in text or DOC_END not in text:
        print("FAIL header-run: %s has no %s marker" % (path, DOC_START))
        return 1
    got = text.split(DOC_START, 1)[1].split(DOC_END, 1)[0].strip("\n")
    want = block(rows)
    if got != want:
        got_lines, want_lines = got.split("\n"), want.split("\n")
        for i, (g, w) in enumerate(zip(got_lines, want_lines)):
            if g != w:
                print("FAIL header-run: %s row %d is stale:\n  have: %s\n  want: %s" % (path, i + 1, g, w))
                break
        else:
            print("FAIL header-run: %s block has %d rows, the artifact gives %d"
                  % (path, len(got_lines), len(want_lines)))
        return 1
    print("ok   header-run: %s's block matches the artifact (%d rows)" % (path, len(rows)))
    return 0


def check():
    """No dump: the artifact's own counts against the other generator that counts the same corpus."""
    rows = read_rows()
    c = totals(rows)
    if not os.path.exists(os.path.join(ROOT, BASELINE)):
        print("FAIL header-run: %s is missing" % BASELINE)
        return 1
    base = {}
    with open(os.path.join(ROOT, BASELINE), newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            base[r["package"]] = r
    mine = collections.Counter()
    for r in rows:
        mine[(r["package"], r["status"])] += 1
    fails = 0
    for pkg, b in sorted(base.items()):
        for status, column in (("scripted", "scripted"), ("scriptless", "scriptless"), ("operator", "operator")):
            want, got = int(b[column] or 0), mine[(pkg, status)]
            if want != got:
                print("FAIL header-run: %s has %d %s rows, matching_baseline.csv says %d"
                      % (pkg, got, status, want))
                fails += 1
    if c["w0_zero"] != c["run"]:
        print("FAIL header-run: %d of %d rows carry a non-zero w0" % (c["run"] - c["w0_zero"], c["run"]))
        fails += 1
    if not fails:
        print("ok   header-run: %d rows, %d carry the run, w0 zero on all, per-package counts equal to "
              "matching_baseline.csv" % (c["rows"], c["run"]))
    return 1 if fails else 0


def report(rows):
    c = totals(rows)
    print("header-run: %d Function exports, %d carry the 16-byte run (%d scripted, %d scriptless); %d operator "
          "declarations are refused by the locator" % (c["rows"], c["run"], c["status:scripted"],
                                                       c["status:scriptless"], c["status:operator"]))
    print("  w0 == 0: %d of %d" % (c["w0_zero"], c["run"]))
    print("  w3 >= the stored script: %d, equal on %d" % (c["w3_ge"], c["w3_eq"]))
    print("  declarations found: %d of %d scripted; %d ambiguous, %d absent"
          % (c["decls_known"] - c["no_decl"], c["status:scripted"],
             c["decls_known"] - c["decls_one"], c["no_decl"]))
    if c["shift_known"]:
        print("  line(w2) - w1: exact %d, within two lines %d, below %d, of %d"
              % (c["shift_zero"], c["shift_two"], c["shift_neg"], c["shift_known"]))
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("system", nargs="?", help="a System directory (or one package) to measure")
    ap.add_argument("-o", "--out", help="write inventory/header_run.csv into this directory")
    ap.add_argument("--report", action="store_true", help="print the read from the committed artifact")
    ap.add_argument("--write-doc", metavar="PATH")
    ap.add_argument("--check-doc", metavar="PATH")
    ap.add_argument("--check", action="store_true", help="no dump: reconcile with matching_baseline.csv")
    args = ap.parse_args()

    if args.check:
        return check()
    if args.write_doc or args.check_doc:
        rows = read_rows(os.path.join(args.out, OUT) if args.out else None)
        return write_doc(args.write_doc, rows) if args.write_doc else check_doc(args.check_doc, rows)
    if args.report:
        return report(read_rows(os.path.join(args.out, OUT) if args.out else None))
    if not args.system:
        ap.error("a System directory, --report, or --check is required")
    rows = census(args.system)
    if args.out:
        path = write_csv(rows, args.out)
        print("wrote %s (%d rows)" % (path, len(rows)))
    return report(rows)


if __name__ == "__main__":
    sys.exit(main())
