#!/usr/bin/env python3
"""The native package each `<Class>_vt<slot>` class belongs to, read from its registration site.

`inventory/function_modules.csv` attributes a `<Class>_vt<slot>` name to a module through the class's
own package in `inventory/classes.csv` -- the package a shipped `.u` declares it in. **273 of the 538
such rows name one of 68 classes no shipped package declares** (`ActorChannel`, `BoolProperty`,
`ControlChannel`, `D3DRenderDevice`, `TcpipConnection`, ...): they are the engine's and the native
driver packages' classes, and this build links those packages into the XBE rather than shipping them as
`.u`, so the class census has no row to read.

The registration site is the evidence that survives. `inventory/registration_sites.csv` resolves all
493 `Class`-constructor call sites to a class literal, and every one of the 68 classes has at least one
site. The function that carries the site is a compiled translation unit of exactly one native package,
and `inventory/function_module_attribution.csv` already names it -- from the `__FILE__` assert the body
carries where it has one, from address locality where it does not. So:

    class -> its registration site -> the body holding the site -> that body's module = the package

**The unanimity is the check, not an assumption.** A class registered at more than one site must have
every site's body agree, or the row is a `conflict` and no package is written; a class whose sites all
land in unattributed bodies is `no-module`. Both verdicts are printed, and `--check` refuses a committed
row that a re-run does not reproduce.

    tools/native-class-packages.py                 # summary + the rows
    tools/native-class-packages.py -o inventory     # write inventory/native_class_packages.csv
    tools/native-class-packages.py --write-doc      # render the page block (no dump)
    tools/native-class-packages.py --check          # synthetic, no dump, writes nothing

CSV in, CSV out: no dump, no boot, nothing the image has to be present for.
"""
import argparse
import bisect
import collections
import csv
import os
import re
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join("inventory", "native_class_packages.csv")
SITES = os.path.join("inventory", "registration_sites.csv")
MAP = os.path.join("symbols", "xbox_functions.csv")
ATTRIB = os.path.join("inventory", "function_module_attribution.csv")
CLASSES = os.path.join("inventory", "classes.csv")
MODULES = os.path.join("inventory", "function_modules.csv")
PAGE = os.path.join("docs", "NATIVE_CLASS_TREE.md")
BLOCK_MARK = ("<!-- generated:native-class-packages -->", "<!-- /generated:native-class-packages -->")
COLUMNS = ("class", "package", "site_va", "site_body", "body_module", "module_source", "sites", "verdict")

CLASS_VT = re.compile(r"^([A-Za-z_][A-Za-z0-9_]*)_vt\d+$")
# The modules `tools/module-split.py` knows, in its order: a package's name is the module's name in
# this build (`XboxAudio_XBoxAudioSubsystem` is `XboxAudio`'s TU), so one list serves both.
KNOWN_MODULES = ("Engine", "Core", "IpDrv", "Gui", "D3DDrv", "XboxDrv", "XboxAudio", "XboxLive",
                 "Gameplay", "Fire", "stargategame", "stargatevehicles")
_UNNAMED = ("(unnamed)", "(other named)", "")


def read(path):
    if not os.path.isfile(path):
        return []
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def known(mod):
    for m in KNOWN_MODULES:
        if m.lower() == (mod or "").lower():
            return m
    return ""


def body_index(map_path):
    """[(start, end, name)] sorted, for "which body holds this address"."""
    out = []
    for r in read(map_path):
        try:
            a = int(r["address"], 16)
            s = int(r["size"] or "0", 16)
        except (KeyError, ValueError):
            continue
        out.append((a, a + s if s else a, r.get("name", "")))
    out.sort()
    return out


def owner(index, starts, va):
    """(start, name) of the body holding `va`, or (None, "")."""
    i = bisect.bisect_right(starts, va) - 1
    if i < 0:
        return None, ""
    a, end, name = index[i]
    if end and va >= end:
        return None, ""
    return a, name


def vt_classes(modules_path):
    """{class: [names]} for every `<Class>_vt<slot>` row, from the modules artifact."""
    out = collections.defaultdict(list)
    for r in read(modules_path):
        m = CLASS_VT.match(r.get("name", ""))
        if m:
            out[m.group(1)].append(r["name"])
    return out


def build(sites_path, map_path, attrib_path, classes_path, modules_path):
    """[(row dict)] -- one row per class that a `<Class>_vt<slot>` name needs and `classes.csv` lacks."""
    declared = {r["class"].lower() for r in read(classes_path) if r.get("class")}
    index = body_index(map_path)
    starts = [a for a, _, _ in index]
    attr = {}
    for r in read(attrib_path):
        try:
            attr["0x%08x" % int(r["address"], 16)] = r
        except (KeyError, ValueError):
            continue
    sites = collections.defaultdict(list)
    for r in read(sites_path):
        if r.get("class") and r.get("site_va"):
            sites[r["class"]].append(r["site_va"])

    rows = []
    for cls, names in sorted(vt_classes(modules_path).items()):
        if cls.lower() in declared:
            continue                      # `classes.csv` already answers it; this tool is the residue
        got = []
        for site in sorted(sites.get(cls, []), key=lambda v: int(v, 16)):
            a, bname = owner(index, starts, int(site, 16))
            if a is None:
                got.append((site, "", "", "", "no-body"))
                continue
            r = attr.get("0x%08x" % a, {})
            asserted = known(r.get("asserted_module"))
            local = known(r.get("module"))
            mod, src = (asserted, "assert") if asserted else (local, "locality") if local else ("", "")
            got.append((site, "0x%08x" % a, bname, mod, src))
        mods = {g[3] for g in got if g[3]}
        if not got:
            verdict, package = "no-site", ""
        elif len(mods) == 1 and all(g[3] for g in got):
            verdict, package = "unanimous", mods.pop()
        elif len(mods) > 1:
            verdict, package = "conflict", ""
        else:
            verdict, package = "no-module", ""
        first = got[0] if got else ("", "", "", "", "")
        rows.append({
            "class": cls,
            "package": package,
            "site_va": first[0],
            "site_body": first[2],
            "body_module": first[3],
            "module_source": first[4],
            "sites": len(got),
            "verdict": verdict,
            "names": names,
            "all": got,
        })
    return rows


# --------------------------------------------------------------------------- rendering

def summary(rows):
    c = collections.Counter(r["verdict"] for r in rows)
    pk = collections.Counter(r["package"] for r in rows if r["package"])
    names = sum(len(r["names"]) for r in rows if r["package"])
    print("%d class(es) a `<Class>_vt<slot>` name needs and `classes.csv` lacks: %s"
          % (len(rows), ", ".join("%s %d" % (k, c[k]) for k in sorted(c))))
    print("attributed %d class(es) = %d `<Class>_vt<slot>` rows -> %s"
          % (sum(pk.values()), names,
             ", ".join("%s %d" % (k, v) for k, v in pk.most_common())))
    for v in ("conflict", "no-module", "no-site"):
        if c[v]:
            print("  %s: %s" % (v, ", ".join(r["class"] for r in rows if r["verdict"] == v)))
    return names


def render(rows):
    c = collections.Counter(r["verdict"] for r in rows)
    pk = collections.Counter(r["package"] for r in rows if r["package"])
    names = sum(len(r["names"]) for r in rows if r["package"])
    out = ["| the 68 native classes' package | classes | `<Class>_vt<slot>` rows |",
           "| --- | --- | --- |"]
    for k, v in pk.most_common():
        n = sum(len(r["names"]) for r in rows if r["package"] == k)
        out.append("| `%s` | %d | %d |" % (k, v, n))
    out.append("| **total** | **%d** | **%d** |" % (sum(pk.values()), names))
    out.append("")
    out.append("%d class(es) resolve, %d conflict, %d land in no attributed body, %d have no site. "
               "The rule is the registration site's body, and unanimity across a class's sites is the check."
               % (sum(pk.values()), c["conflict"], c["no-module"], c["no-site"]))
    return out


def write_doc(path, rows, check=False):
    lines = render(rows)
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    a, b = BLOCK_MARK
    if a not in text or b not in text:
        raise SystemExit("%s: no generated block" % path)
    head, rest = text.split(a, 1)
    _, tail = rest.split(b, 1)
    new = head + a + "\n" + "\n".join(lines) + "\n" + b + tail
    if check:
        if new != text:
            raise SystemExit("%s: the class-package block is stale" % path)
        print("ok   native-class-packages: %s reproduces" % path)
        return
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(new)
    print("wrote %s" % path)


def write_csv(path, rows):
    tmp = path + ".tmp"
    with open(tmp, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS)
        w.writeheader()
        for r in rows:
            w.writerow({k: r[k] for k in COLUMNS})
    os.replace(tmp, path)
    print("wrote %s (%d rows)" % (path, len(rows)))


# --------------------------------------------------------------------------- self-test

def selftest():
    """Three cases: one class one site (attributed), one class two disagreeing sites, one class no site."""
    d = tempfile.mkdtemp(prefix="ncpack-")
    with open(os.path.join(d, "map.csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["address", "name", "size", "module"])
        w.writerow(["0x1000", "Core_UnProp", "0x100", "Core"])
        w.writerow(["0x2000", "Engine_UnChan", "0x100", "Engine"])
    with open(os.path.join(d, "attr.csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["address", "name", "size", "asserted_module", "module"])
        w.writerow(["0x1000", "Core_UnProp", "0x100", "Core", "Core"])
        w.writerow(["0x2000", "Engine_UnChan", "0x100", "(unnamed)", "Engine"])
    with open(os.path.join(d, "sites.csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["site_va", "class"])
        w.writerow(["0x1010", "BoolProperty"])       # in Core_UnProp
        w.writerow(["0x2010", "ActorChannel"])       # in Engine_UnChan, locality only
        w.writerow(["0x2020", "ZeroSize"])           # in Engine_UnChan too
        w.writerow(["0x9000", "Missing"])            # no body holds this address
    with open(os.path.join(d, "classes.csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["package", "class"])
        w.writerow(["Engine", "Actor"])              # a declared class, skipped by the tool
    with open(os.path.join(d, "modules.csv"), "w", newline="", encoding="utf-8") as fh:
        w = csv.writer(fh)
        w.writerow(["address", "name", "size", "module"])
        for nm in ("BoolProperty_vt3", "ActorChannel_vt7", "Actor_vt9", "Missing_vt2", "Absent_vt5"):
            w.writerow(["0x1", nm, "4", "(other named)"])
    rows = build(os.path.join(d, "sites.csv"), os.path.join(d, "map.csv"),
                 os.path.join(d, "attr.csv"), os.path.join(d, "classes.csv"),
                 os.path.join(d, "modules.csv"))
    by = {r["class"]: r for r in rows}
    cases = [
        ("a declared class is not this tool's population", "Actor" not in by),
        ("one site, one module -> attributed", by["BoolProperty"]["package"] == "Core"
         and by["BoolProperty"]["verdict"] == "unanimous"),
        ("locality-only evidence still resolves, and says so",
         by["ActorChannel"]["module_source"] == "locality" and by["ActorChannel"]["package"] == "Engine"),
        ("a site no body holds writes no package, and says why",
         by["Missing"]["verdict"] == "no-module" and by["Missing"]["package"] == ""),
        ("a class with no registration site is its own verdict",
         by["Absent"]["verdict"] == "no-site" and by["Absent"]["package"] == ""),
    ]
    bad = [n for n, ok in cases if not ok]
    for n, ok in cases:
        print("%s %s" % ("ok  " if ok else "FAIL", n))
    if bad:
        raise SystemExit("native-class-packages: %d selftest case(s) failed" % len(bad))
    print("native-class-packages: selftest passed (%d cases)" % len(cases))


# --------------------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    ap.add_argument("-o", "--out-dir", default=None, help="write the artifact under this directory")
    ap.add_argument("--write-doc", action="store_true", help="render the page's block")
    ap.add_argument("--check-doc", nargs="?", const="", default=None, help="refuse a stale page block")
    ap.add_argument("--check", action="store_true", help="synthetic self-test, no dump")
    ap.add_argument("--sites", default=os.path.join(ROOT, SITES))
    ap.add_argument("--map", default=os.path.join(ROOT, MAP))
    ap.add_argument("--attribution", default=os.path.join(ROOT, ATTRIB))
    ap.add_argument("--classes", default=os.path.join(ROOT, CLASSES))
    ap.add_argument("--modules", default=os.path.join(ROOT, MODULES))
    args = ap.parse_args()
    if args.check:
        selftest()
        return 0
    rows = build(args.sites, args.map, args.attribution, args.classes, args.modules)
    if args.write_doc or args.check_doc is not None:
        target = args.check_doc or os.path.join(ROOT, PAGE)
        if not os.path.isabs(target):
            target = os.path.join(ROOT, target)
        write_doc(target, rows, check=args.check_doc is not None)
        return 0
    for r in rows:
        if r["verdict"] == "unanimous":
            print("%-22s %-10s %s %s (%s) %d site(s)"
                  % (r["class"], r["package"], r["site_va"], r["site_body"], r["module_source"], r["sites"]))
    names = summary(rows)
    if args.out_dir:
        os.makedirs(os.path.join(args.out_dir, "inventory"), exist_ok=True)
        write_csv(os.path.join(args.out_dir, OUT), rows)
    return 0 if names else 1


if __name__ == "__main__":
    sys.exit(main())
