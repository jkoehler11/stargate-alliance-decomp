#!/usr/bin/env python3
"""Per-method evidence for one library class (PLAN 4.9).

Reading 144 decompiled bodies is not a deliverable anyone can check. Each library member's own
relocation list is: a `REL32` is a call whose target the library names, and a `DIR32` into `.rdata`
is a literal whose *text* the image holds. That gives, per method, its callees and its strings —
enough to describe a subsystem's structure without claiming what its logic does.

    python3 tools/native-method-notes.py --class CXnIp --libs ~/sg1/work/xdk/libs-all \\
        --xbe ~/sg1/work/dump-local/default.xbe -o inventory/cxn_ip_methods.csv

`--check` validates the artifact without the dump. `--class` takes a demangled class name.
"""

import argparse
import collections
import csv
import os
import re
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

from cofflib import CoffError, CoffObject, parse_archive  # noqa: E402

COLUMNS = ["address", "method", "args", "size", "callees", "callee_classes", "strings"]
NAMES = os.path.join("symbols", "xbox_functions.csv")
IMAGE_REL_I386_REL32 = 0x20


def split_mangled(name):
    """('CXnIp', 'IpTerm', 'IAEXXZ') for '?IpTerm@CXnIp@@IAEXXZ', else (None, name, '')."""
    if not name.startswith("?") or "@@" not in name:
        return None, name, ""
    head, args = name[1:].split("@@", 1)
    parts = [p for p in head.split("@") if p]
    if len(parts) < 2:
        return None, parts[0] if parts else name, args
    return parts[1], parts[0], args


def class_of(sym):
    _cls, _method, _args = split_mangled(sym)
    return _cls or ""


def printable(buf):
    out = []
    for b in buf:
        if b == 0:
            break
        if 32 <= b < 127 or b in (9,):
            out.append(chr(b))
        else:
            return None
    text = "".join(out).strip()
    return text if len(text) >= 4 else None


def load_libs(lib_dir):
    table = {}
    for fn in sorted(os.listdir(lib_dir)):
        if not fn.lower().endswith(".lib"):
            continue
        lib = os.path.splitext(fn)[0]
        try:
            members = parse_archive(open(os.path.join(lib_dir, fn), "rb").read())
        except (CoffError, OSError):
            continue
        for member, blob in members:
            try:
                obj = CoffObject(member, blob)
                funcs = obj.functions_full()
            except CoffError:
                continue
            for name, _body, rels in funcs:
                table.setdefault((lib, member, name), rels)
    return table


def generate(args):
    from xbe import Xbe  # noqa: PLC0415

    xbe = Xbe(args.xbe)
    libs = load_libs(args.libs)
    rows = [r for r in csv.DictReader(open(NAMES, encoding="utf-8"))
            if split_mangled(r["name"])[0] == args.target]
    # body evidence by symbol name: the body-match artifact knows (lib, member); the call-edge names
    # do not, so index the libraries by symbol instead
    by_name = {}
    for (_lib, _member, name), rels in libs.items():
        by_name.setdefault(name, rels)
    out = []
    for r in sorted(rows, key=lambda r: int(r["address"], 16)):
        cls, method, margs = split_mangled(r["name"])
        addr, size = int(r["address"], 16), int(r["size"])
        body = xbe.read_va(addr, size)
        rels = by_name.get(r["name"])
        callees, strings = [], []
        if body and rels:
            for off, width, sym, typ in rels:
                if off + 4 > len(body):
                    continue
                if typ & IMAGE_REL_I386_REL32:
                    disp = struct.unpack("<i", body[off:off + 4])[0]
                    va = addr + off + 4 + disp
                    callees.append((vy_to_name(sym), va))
                else:
                    va = struct.unpack("<I", body[off:off + 4])[0]
                    if xbe.section_for(va) is None:
                        continue
                    text = printable(xbe.read_va(va, 120) or b"")
                    if text:
                        strings.append(text)
        seen = []
        for name, _va in callees:
            if name and name not in seen:
                seen.append(name)
        out.append({"address": r["address"], "method": method, "args": margs, "size": size,
                    "callees": "; ".join(seen),
                    "callee_classes": "; ".join(sorted({c for c in map(class_of, seen) if c})),
                    "strings": "; ".join(dict.fromkeys(strings))})
    with open(args.out, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS, lineterminator="\n")
        w.writeheader()
        w.writerows(out)
    print("%s: %d methods, %d with callees, %d with strings"
          % (args.target, len(out), sum(1 for r in out if r["callees"]),
             sum(1 for r in out if r["strings"])))
    cc = collections.Counter()
    for r in out:
        for c in r["callee_classes"].split("; "):
            if c:
                cc[c] += 1
    for name, n in cc.most_common(8):
        print("  callee class %-24s in %d methods" % (name, n))
    return 0


def vy_to_name(sym):
    """Trim MSVC decoration for display: keep Class::Method when the symbol names one."""
    if not sym.startswith("?"):
        return sym.split("@")[0]
    cls, method, _args = split_mangled(sym)
    return "%s::%s" % (cls, method) if cls else method


def check(args):
    problems = []
    if not os.path.isfile(args.out):
        raise SystemExit("%s missing -- run the generator" % args.out)
    with open(args.out, newline="") as fh:
        rd = csv.DictReader(fh)
        if rd.fieldnames != COLUMNS:
            raise SystemExit("%s columns are %r" % (args.out, rd.fieldnames))
        rows = list(rd)
    if not rows:
        problems.append("no rows")
    addrs = collections.Counter(r["address"].lower() for r in rows)
    problems.extend("%s appears twice" % a for a, n in addrs.items() if n > 1)
    for r in rows:
        if not r["method"]:
            problems.append("%s has no method name" % r["address"])
        if int(r["size"]) < 1:
            problems.append("%s has size 0" % r["address"])
    if problems:
        print("native-method-notes: %d problem(s)" % len(problems))
        for p in problems[:10]:
            print("  " + p)
        return 1
    print("ok   native-method-notes: %d methods" % len(rows))
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--class", dest="target", default="CXnIp",
                    help="demangled class name (default CXnIp, the 4.9 reading)")
    ap.add_argument("--libs")
    ap.add_argument("--xbe")
    ap.add_argument("-o", "--out")
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    # The default output follows the class, so `--check` with no arguments is the contract check.
    if not args.out:
        # CXnIp -> cxn_ip_methods.csv: snake-case at the lower->upper boundary, not lowercased flat
        snake = re.sub(r"(?<=[a-z0-9])(?=[A-Z])", "_", args.target).lower()
        args.out = os.path.join("inventory", "%s_methods.csv" % snake)
    if args.check:
        return check(args)
    missing = [f for f in ("libs", "xbe", "out", "target") if not getattr(args, f)]
    if missing:
        raise SystemExit("missing --%s" % missing[0].replace("target", "class"))
    return generate(args)


if __name__ == "__main__":
    sys.exit(main())