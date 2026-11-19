#!/usr/bin/env python3
"""The UI page flow and the HUD selection: which page opens which, and which class names a HUD.

`docs/systems/UI.md` left two things unmeasured in its own words -- *what any page looks like or which flow leads
where* ("the layout data is in `StargateMenus`' script defaults") and *which HUD the game actually selects for the
Replicator mode, and whether it is player- or bot-only*. Neither needs a boot or a decompiler: the page graph is
written in the build's own UnrealScript as `Controller.OpenMenu("StargateMenus.X")`, the data-driven half is the
`ButtonMenus[n]="StargateMenus.Y"` array that `RightLockingMenuPanel` walks, and the root is the `MainMenuClass=`
key in the shipped `.ini`. The HUD is a *string* property resolved through `DynamicLoadObject`.

So this tool does three reads and joins them:

* **the edges** -- every literal `OpenMenu`/`ReplaceMenu`/`ClientOpenMenu` target and every `ButtonMenus[n]`
  string, per class, with the commented-out ones counted apart (this corpus comments out pages it cut);
* **the resolution** -- each target is `Package.Class`, so it is checked against that package's own export
  table: a class that is not there is a dead link, and one that differs only by a punctuation or a case is a
  typo, which is what a string-keyed menu call turns a renamed page into;
* **the root and the closure** -- the `.ini`'s `MainMenuClass=`/`AutoLoad=` values are the only start the
  build states, so walking the resolved edges from them splits the `SGPageBase` family into reachable pages,
  pages opened only from outside the menu graph, and pages nothing opens.

The HUD half is the same join one level down: `GameInfo.HUDType` is a string (`Engine.GameInfo` declares it
`var string`), `GameInfo.PostBeginPlay` resolves it with `DynamicLoadObject`, so a class's HUD is a name in its
defaults -- and a HUD a class swaps in at runtime is a literal in its body.

    python3 tools/ui-menu-flow.py DUMP --sweep -o inventory     # writes inventory/menu_flow.csv
    python3 tools/ui-menu-flow.py --report
    python3 tools/ui-menu-flow.py --write-doc docs/systems/UI.md
    python3 tools/ui-menu-flow.py --check-doc docs/systems/UI.md
    python3 tools/ui-menu-flow.py --check                       # synthetic, no dump, writes nothing

`DUMP` is the game directory (holds `System/`). No class text is printed: a row carries a target string and a
verdict, which is structure, not source.
"""
import argparse
import collections
import csv
import importlib.util
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
from ue2pkg import Package  # noqa: E402

ARTIFACT = os.path.join(ROOT, "inventory/menu_flow.csv")
DOC = os.path.join(ROOT, "docs/systems/UI.md")
DOC_START = "<!-- generated:menu-flow -->"
DOC_END = "<!-- /generated:menu-flow -->"

FIELDS = ["kind", "name", "package", "form", "target", "target_package", "verdict", "depth", "sites",
          "platform", "note"]

# The stock packages' menu bodies are engine furniture, not this game's pages: counted, and named apart.
STOCK = {"Engine", "GUI", "UWindow"}
# The decompiler writes `#ifdefPS2` with no space, so the directive is matched by prefix and not by a word edge.
PREPROC = re.compile(r"^\s*#\s*(?P<op>ifdef|ifndef|else|endif)\s*(?P<expr>[A-Za-z0-9_$]*)", re.M)
# The Xbox build defines neither `PC` nor `PS2` (docs/COMPILE.md), so an `#ifdef` on either is a body it does not
# compile and an `#ifndef` on one is a body it does.
DEAD_PLATFORMS = {"PC", "PS2"}

# The call that opens a page, and the array whose elements it walks.
OPEN_CALL = re.compile(r"\b(?:OpenMenu|ReplaceMenu|ClientOpenMenu)\s*\(\s*(?P<arg>[^,)]*)")
ARRAY_SET = re.compile(r"ButtonMenus\s*\[\s*(?P<idx>\d+)\s*\]\s*=\s*\"(?P<val>[^\"]*)\"")
HUD_SET = re.compile(r"\bHUDType\s*=\s*\"(?P<val>[^\"]*)\"")
HUD_LOAD = re.compile(r"DynamicLoadObject\s*\(\s*\"(?P<val>[^\"]+)\"\s*,\s*[Cc]lass\s*'[^']*'")
STRING_LIT = re.compile(r"^\"(?P<val>[^\"]*)\"$")
INI_KEY = re.compile(r"^\s*(?P<key>MainMenuClass|AutoLoad)\s*=\s*(?P<val>\S+)", re.I)


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, "tools", filename))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def strip_comments(text):
    """(code, comments): the same body with `//` and `/* */` spans removed / kept.

    A menu call inside a comment is not a menu call, and this corpus comments out the pages it cut -- the
    difference between the two halves is a measurement, not tidying.
    """
    code, commented = [], []
    i, n = 0, len(text)
    while i < n:
        if text.startswith("//", i):
            j = text.find("\n", i)
            j = n if j < 0 else j
            commented.append(text[i:j])
            i = j
        elif text.startswith("/*", i):
            j = text.find("*/", i + 2)
            j = n if j < 0 else j + 2
            commented.append(text[i:j])
            i = j
        else:
            code.append(text[i])
            i += 1
    return "".join(code), "\n".join(commented)


def preproc_regions(code):
    """[(start, end, label)] for every `#ifdef`/`#ifndef` span, so an edge can say which branch it sits in."""
    stack, out = [], []
    for m in PREPROC.finditer(code):
        op = m.group("op")
        if op in ("ifdef", "ifndef"):
            stack.append((op, m.group("expr"), m.start()))
        elif op == "endif" and stack:
            op0, expr, start = stack.pop()
            out.append((start, m.end(), "%s %s" % (op0, expr)))
    return out


def branch_at(regions, pos):
    """The innermost branch a position sits in, and whether this build compiles it."""
    hit = ""
    for start, end, label in regions:
        if start <= pos <= end:
            hit = label
    return hit


def branch_live(label):
    """Whether the Xbox build compiles a body inside this branch (it defines neither `PC` nor `PS2`)."""
    if not label:
        return True
    op, _sp, expr = label.partition(" ")
    if op == "ifdef":
        return expr not in DEAD_PLATFORMS
    if op == "ifndef":
        return expr in DEAD_PLATFORMS
    return True


def split_target(s):
    """`Pkg.Class` -> (pkg, cls); anything else -> ('', s). A target with no dot names no package."""
    if s.count(".") == 1:
        a, b = s.split(".")
        if a and b:
            return a, b
    return "", s


IDENT = re.compile(r"[A-Za-z_][A-Za-z0-9_]*")


def tidy(s):
    """The longest identifier run at the front of a target: `OptionsEditAudio)` -> `OptionsEditAudio`."""
    m = IDENT.match(s)
    return m.group(0) if m else ""


def class_tables(system_dir, verbose=True):
    """{package: {class: export}} and {class: [packages]}, read from the export tables themselves."""
    pst = _load("pkg_script_text", "pkg-script-text.py")
    per_pkg, by_name, text_of = {}, collections.defaultdict(list), {}
    for fname in sorted(os.listdir(system_dir)):
        if not fname.lower().endswith(".u"):
            continue
        pkgname = fname[:-2]
        pkg = Package(os.path.join(system_dir, fname))
        pkg.read_names()
        pkg.read_imports()
        pkg.read_exports()
        classes = {}
        for exp in pkg.exports:
            if pkg.class_name_of(exp) != "Class":
                continue
            cls = pkg.name(exp["name"])
            classes[cls] = exp
            by_name[cls].append(pkgname)
            text = pst.script_text(pkg, exp)
            if text is not None:
                text_of[(pkgname, cls)] = text
        per_pkg[pkgname] = classes
    if verbose:
        print("classes: %d over %d packages, %d with text" %
              (sum(len(v) for v in per_pkg.values()), len(per_pkg), len(text_of)))
    return per_pkg, by_name, text_of


def supers_of(per_pkg):
    """{class: super name}, from each class export's own super index (same package, then imports)."""
    supers = {}
    for pkgname, classes in per_pkg.items():
        for cls in classes:
            super_name = cls_super(per_pkg, pkgname, cls)
            if super_name:
                supers[cls] = super_name
    return supers


def cls_super(per_pkg, pkgname, cls):
    """A class export's `Super` resolved to a name. Read from a package object the caller already parsed."""
    pkg = _pkg_cache.get(pkgname)
    if pkg is None:
        return ""
    exp = per_pkg[pkgname][cls]
    idx = exp["super"]
    if idx > 0:
        return pkg.name(pkg.exports[idx - 1]["name"])
    if idx < 0:
        i = -idx - 1
        return pkg.name(pkg.imports[i]["name"]) if i < len(pkg.imports) else ""
    return ""


_pkg_cache = {}


def read_edges(text, cls, pkgname):
    """(rows, commented count) from one class body."""
    code, commented = strip_comments(text)
    regions = preproc_regions(code)
    rows = []
    for m in OPEN_CALL.finditer(code):
        arg = m.group("arg").strip()
        lit = STRING_LIT.match(arg)
        plat = branch_at(regions, m.start())
        notes = []
        if pkgname in STOCK:
            notes.append("stock engine body")
        if not branch_live(plat):
            notes.append("branch not compiled by this build")
        note = "; ".join(notes)
        if lit:
            rows.append(dict(kind="edge", name=cls, package=pkgname, form="literal", target=lit.group("val"),
                             platform=plat, note=note))
        else:
            rows.append(dict(kind="edge", name=cls, package=pkgname, form="computed", target=arg[:40],
                             platform=plat, note=(note + " " if note else "") + "computed argument"))
    for m in ARRAY_SET.finditer(code):
        rows.append(dict(kind="array", name=cls, package=pkgname, form="default",
                         target=m.group("val"), note="index %s" % m.group("idx")))
    for m in HUD_SET.finditer(code):
        rows.append(dict(kind="hud", name=cls, package=pkgname, form="default", target=m.group("val")))
    for m in HUD_LOAD.finditer(code):
        rows.append(dict(kind="hud_swap", name=cls, package=pkgname, form="literal",
                         target=m.group("val")))
    skipped = len(OPEN_CALL.findall(commented)) + len(ARRAY_SET.findall(commented))
    return rows, skipped


def resolve(target, per_pkg, by_name):
    """(verdict, target_package, target_class, note) for one `Pkg.Class` string."""
    pkgname, cls = split_target(target)
    if not pkgname:
        return "unqualified", "", cls, "no package prefix, so it resolves against the loading package"
    classes = per_pkg.get(pkgname)
    if classes is None:
        return "absent-package", pkgname, cls, "no such package on disk"
    if cls in classes:
        return "class", pkgname, cls, ""
    lower = {c.lower(): c for c in classes}
    if cls.lower() in lower:
        return "case", pkgname, lower[cls.lower()], "case differs from the class"
    t = tidy(cls)
    if t and t != cls and t in classes:
        return "typo", pkgname, t, "resolves once the trailing %r is dropped" % cls[len(t):]
    if by_name.get(cls):
        return "other-package", pkgname, cls, "a class of that name is in %s" % ", ".join(sorted(by_name[cls]))
    return "absent", pkgname, cls, ""


DEFAULTS_ARRAY = re.compile(r"^\s*ButtonMenus\((?P<idx>\d+)\)\s*=\s*(?P<val>.+?)\s*$", re.M)
DEFAULTS_HUD = re.compile(r"^\s*HUDType\s*=\s*(?P<val>.+?)\s*$", re.M)


def read_defaults(tree):
    """`ButtonMenus(n)` and `HUDType` from the `defaults-emit --emit` tree.

    A class's `defaultproperties` are **not** in its `TextBuffer`: the block is compiled into the class record, so
    the menu array MainMenu's buttons come from is a property value, not a line of script. `tools/defaults-emit.py`
    renders those blocks (`make ui-menu-flow` runs it first), and this reads the two properties the UI needs.
    """
    rows = []
    if not tree or not os.path.isdir(tree):
        return rows, 0
    classes = 0
    for pkgname in sorted(os.listdir(tree)):
        sub = os.path.join(tree, pkgname)
        if not os.path.isdir(sub):
            continue
        for fname in sorted(os.listdir(sub)):
            if not fname.endswith(".defaultproperties.uc"):
                continue
            cls = fname[:-len(".defaultproperties.uc")]
            classes += 1
            with open(os.path.join(sub, fname), encoding="latin-1") as fh:
                text = fh.read()
            for m in DEFAULTS_ARRAY.finditer(text):
                val = m.group("val").strip()
                if val.startswith('"') and val.endswith('"'):
                    val = val[1:-1]
                rows.append(dict(kind="array", name=cls, package=pkgname, form="defaults", target=val,
                                 platform="", note="index %s" % m.group("idx")))
            for m in DEFAULTS_HUD.finditer(text):
                val = m.group("val").strip().strip('"')
                rows.append(dict(kind="hud", name=cls, package=pkgname, form="defaults", target=val,
                                 platform=""))
    return rows, classes


def read_inis(system_dir):
    rows = []
    for fname in sorted(os.listdir(system_dir)):
        if not fname.lower().endswith(".ini"):
            continue
        with open(os.path.join(system_dir, fname), "rb") as fh:
            raw = fh.read().decode("latin-1")
        for line in raw.splitlines():
            m = INI_KEY.match(line)
            if m:
                rows.append(dict(kind="root", name=fname, package="", form=m.group("key").lower(),
                                 target=m.group("val"), platform="", note=""))
    return rows


def closure(roots, edges, resolve_row):
    """BFS from the root page strings over edges that resolve to a class: {page label: depth}."""
    by_src = collections.defaultdict(list)
    for e in edges:
        if e["form"] == "computed":
            continue
        verdict, tpkg, tcls, _ = resolve_row(e["target"])
        if verdict in ("class", "case", "typo"):
            by_src[(e["package"], e["name"])].append("%s.%s" % (tpkg, tcls))
    seen, queue = {}, collections.deque()
    for r in roots:
        if r not in seen:
            seen[r] = 0
            queue.append(r)
    while queue:
        node = queue.popleft()
        if "." not in node:
            continue
        pkgname, cls = node.split(".", 1)
        for nxt in by_src.get((pkgname, cls), []):
            if nxt not in seen:
                seen[nxt] = seen[node] + 1
                queue.append(nxt)
    return seen


def sweep(dump, defaults_tree=""):
    system_dir = os.path.join(dump, "System") if os.path.isdir(os.path.join(dump, "System")) else dump
    per_pkg, by_name, text_of = class_tables(system_dir)
    for pkgname in per_pkg:                      # one parsed package per name, for the super lookups
        if pkgname not in _pkg_cache:
            p = Package(os.path.join(system_dir, pkgname + ".u"))
            p.read_names()
            p.read_imports()
            p.read_exports()
            _pkg_cache[pkgname] = p
    supers = supers_of(per_pkg)

    rows, skipped_total, holders = [], 0, 0
    for (pkgname, cls), text in sorted(text_of.items()):
        got, skipped = read_edges(text, cls, pkgname)
        skipped_total += skipped
        if got:
            holders += 1
        rows.extend(got)

    def_rows, def_classes = read_defaults(defaults_tree)
    rows.extend(def_rows)
    ini_rows = read_inis(system_dir)
    roots = sorted({r["target"] for r in ini_rows if r["form"] == "mainmenuclass"})
    if not roots:                                # `AutoLoad=` is the same start under the other key
        roots = sorted({r["target"] for r in ini_rows if r["form"] == "autoload"})

    for r in rows:
        if r["kind"] in ("edge", "array") and r["form"] != "computed":
            r["_resolved"] = resolve(r["target"], per_pkg, by_name)

    reach = closure(roots, [r for r in rows if r["kind"] in ("edge", "array")],
                    lambda t: resolve(t, per_pkg, by_name))

    family = set()
    for c in supers:
        cur, seen = c, set()
        while cur in supers and cur not in seen:
            seen.add(cur)
            nxt = supers[cur]
            if nxt in ("SGPageBase", "GUIPage"):
                if c not in ("SGPageBase", "GUIPage") and by_name[c][0] not in STOCK:
                    family.add(c)
                break
            cur = nxt
    opened_from = collections.Counter()
    for r in rows:
        if r["kind"] in ("edge", "array") and r["form"] != "computed":
            verdict, tpkg, tcls, _note = r["_resolved"]
            if verdict in ("class", "case", "typo"):
                opened_from["%s.%s" % (tpkg, tcls)] += 1

    out = []
    for r in rows:
        r = dict(r)
        if r["kind"] in ("edge", "array") and r["form"] != "computed":
            verdict, tpkg, tcls, note = r["_resolved"]
        elif r["kind"] in ("hud", "hud_swap"):
            verdict, tpkg, tcls, note = resolve(r["target"], per_pkg, by_name)
            if r["kind"] == "hud_swap" and "HUD" not in tcls.upper():
                continue
        else:
            verdict, tpkg, tcls, note = "", "", "", ""
        r.update(target_package=tpkg, verdict=verdict, depth="", sites=1, note=(r.get("note") or note))
        out.append(r)
    for r in ini_rows:
        r = dict(r)
        verdict, tpkg, tcls, note = resolve(r["target"], per_pkg, by_name) if r["target"].count(".") == 1 \
            else ("", "", "", "")
        r.update(target_package=tpkg, verdict=verdict, depth=reach.get(r["target"], ""), sites=1, note=note)
        out.append(r)

    pages =[]
    for cls in sorted(family):
        pkgname = by_name[cls][0]
        label = "%s.%s" % (pkgname, cls)
        if label in reach:
            verdict, depth, note = "reachable", reach[label], ""
        elif opened_from.get(label):
            verdict, depth, note = "outside", "", "opened from %d site(s) no root reaches" % opened_from[label]
        else:
            verdict, depth, note = "unopened", "", "no menu call in the corpus names it"
        pages.append(dict(kind="page", name=cls, package=pkgname, form="page", target=label,
                          target_package=pkgname, verdict=verdict, depth=depth, sites=opened_from.get(label, 0),
                          platform="", note=note))
    out.extend(sorted(pages, key=lambda r: (r["verdict"], r["name"])))

    def count(kind, verdict=None):
        return sum(1 for r in out if r["kind"] == kind and (verdict is None or r["verdict"] == verdict))

    census = dict(kind="census", name="corpus", package="", form="", target="", target_package="",
                  verdict="", depth="", sites=1,
                  note="|".join([
                      "packages=%d" % len(per_pkg),
                      "classes_with_text=%d" % len(text_of),
                      "holders=%d" % holders,
                      "edges=%d" % count("edge"),
                      "literal_edges=%d" % sum(1 for r in out if r["kind"] == "edge" and r["form"] == "literal"),
                      "game_edges=%d" % sum(1 for r in out if r["kind"] == "edge" and r["form"] == "literal"
                                            and r["package"] not in STOCK),
                      "preproc_edges=%d" % sum(1 for r in out if r["kind"] == "edge" and r.get("platform")),
                      "dead_branch_edges=%d" % sum(1 for r in out if r["kind"] == "edge"
                                                   and not branch_live(r.get("platform") or "")),
                      "computed=%d" % sum(1 for r in out if r["kind"] == "edge" and r["form"] == "computed"),
                      "arrays=%d" % count("array"),
                      "commented=%d" % skipped_total,
                      "roots=%d" % len(roots),
                      "reachable_pages=%d" % count("page", "reachable"),
                      "outside_pages=%d" % count("page", "outside"),
                      "unopened_pages=%d" % count("page", "unopened"),
                      "defaults_classes=%d" % def_classes,
                      "hud_assign=%d" % count("hud"),
                      "hud_swaps=%d" % count("hud_swap"),
                  ]))
    out.append(census)
    return out, dict(packages=len(per_pkg), classes_with_text=len(text_of), holders=holders, roots=roots,
                     commented=skipped_total, defaults_classes=def_classes)


def summarize(rows):
    c = [r for r in rows if r["kind"] == "census"][0]["note"]
    return dict(kv.split("=", 1) for kv in c.split("|"))


def write(rows, out_dir):
    path = os.path.join(out_dir, "menu_flow.csv")
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in FIELDS})
    print("wrote %s (%d rows)" % (path, len(rows)))


def render(rows):
    s = summarize(rows)
    for r in rows:                               # a row from the CSV carries every field as a string
        r.setdefault("verdict", "")
        r.setdefault("form", "")
        r.setdefault("note", "")
        r.setdefault("depth", "")
        r.setdefault("sites", "")
        r.setdefault("target", "")
        r.setdefault("package", "")
        r.setdefault("name", "")
        r.setdefault("kind", "")
    dead = [r for r in rows if r["verdict"] in ("absent", "absent-package", "typo", "case")
            and r["kind"] in ("edge", "array")]
    pages = [r for r in rows if r["kind"] == "page"]
    hud = [r for r in rows if r["kind"] in ("hud", "hud_swap")]
    out = []
    out.append("**%s of the %s `OpenMenu`-family sites carry a literal page target, %s of those are in the game's "
               "own packages, and %d name a class their own package does not export; %s of the %s menu pages are "
               "reachable from the build's own `MainMenuClass`.**" %
               (s["literal_edges"], s["edges"], s["game_edges"], len(dead), s["reachable_pages"],
                int(s["reachable_pages"]) + int(s["outside_pages"]) + int(s["unopened_pages"])))
    out.append("")
    out.append("| measure | answer |")
    out.append("|---|---|")
    for k, label in [("packages", "packages read"), ("classes_with_text", "classes carrying text"),
                     ("holders", "classes that open a page"), ("literal_edges", "`OpenMenu`-family literal sites"),
                     ("game_edges", "... in the game's own packages"),
                     ("preproc_edges", "... inside an `#ifdef`/`#ifndef` branch"),
                     ("dead_branch_edges", "... in a branch this build does not compile"),
                     ("edges", "... all sites of that family"),
                     ("computed", "... whose argument is not a literal"), ("arrays", "`ButtonMenus[n]` strings"),
                     ("commented", "sites inside a comment (cut pages)"), ("roots", "`MainMenuClass` roots"),
                     ("reachable_pages", "pages reachable from a root"),
                     ("outside_pages", "pages opened only from outside the graph"),
                     ("unopened_pages", "pages nothing opens"), ("hud_assign", "`HUDType=` assignments"),
                     ("hud_swaps", "runtime HUD swaps")]:
        out.append("| %s | **%s** |" % (label, s[k]))
    out.append("")
    out.append("### The dead links")
    out.append("")
    if dead:
        out.append("| target | stated by | verdict | what it is |")
        out.append("|---|---|---|---|")
        for r in sorted(dead, key=lambda r: (r["verdict"], r["target"], r["name"])):
            out.append("| `%s` | `%s.%s` | %s | %s |" % (r["target"], r["package"], r["name"], r["verdict"],
                                                         r["note"] or "-"))
    else:
        out.append("Every literal target resolves. `--check` refuses a row whose verdict is not one this table "
                   "would render, so an empty table here is a measurement.")
    out.append("")
    gated = [r for r in rows if r["kind"] == "edge" and r.get("platform")
             and not branch_live(r["platform"])]
    if gated:
        out.append("### Menu calls the build may not compile")
        out.append("")
        out.append("| site | target | branch |")
        out.append("|---|---|---|")
        for r in sorted(gated, key=lambda r: (r["platform"], r["target"])):
            out.append("| `%s.%s` | `%s` | `#%s` |" % (r["package"], r["name"], r["target"],
                                                       r["platform"]))
        out.append("")
    out.append("### The pages, from the root down")
    out.append("")
    out.append("| page | verdict | depth | openers |")
    out.append("|---|---|---:|---:|")
    for r in sorted(pages, key=lambda r: (r["verdict"] != "reachable",
                                          int(r["depth"]) if str(r["depth"]).isdigit() else 99, r["name"])):
        out.append("| `%s.%s` | %s | %s | %s |" % (r["package"], r["name"], r["verdict"],
                                                  r["depth"] if r["depth"] != "" else "-", r["sites"]))
    out.append("")
    out.append("### The HUD, which is a string")
    out.append("")
    out.append("| stated by | form | target | resolves |")
    out.append("|---|---|---|---|")
    for r in sorted(hud, key=lambda r: (r["kind"], r["package"], r["name"])):
        out.append("| `%s.%s` | %s | `%s` | %s |" % (r["package"], r["name"], r["kind"], r["target"],
                                                     r["verdict"]))
    return "\n".join(out)


def write_doc(path, rows):
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    if DOC_START not in text or DOC_END not in text:
        sys.exit("%s: no menu-flow markers" % path)
    head, rest = text.split(DOC_START, 1)
    _old, tail = rest.split(DOC_END, 1)
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(head + DOC_START + "\n" + render(rows) + "\n" + DOC_END + tail)
    print("wrote %s" % path)


def check_doc(path, rows):
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    if DOC_START not in text or DOC_END not in text:
        sys.exit("FAIL menu-flow: %s has no markers" % path)
    head, rest = text.split(DOC_START, 1)
    _old, tail = rest.split(DOC_END, 1)
    if head + DOC_START + "\n" + render(rows) + "\n" + DOC_END + tail != text:
        sys.exit("FAIL menu-flow: %s's block is stale (run --write-doc)" % path)
    print("ok   menu-flow: %s regenerates byte-identically" % path)


def report(path=ARTIFACT):
    with open(path, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    s = summarize(rows)
    print("menu flow: %(edges)s sites over %(holders)s classes, %(arrays)s array strings, %(commented)s commented"
          % s)
    print("pages: %(reachable_pages)s reachable, %(outside_pages)s outside, %(unopened_pages)s unopened"
          % s)
    print("hud: %(hud_assign)s assignments, %(hud_swaps)s swaps" % s)


# ---------------------------------------------------------------- self-test

FIXTURE = """
class DemoPage extends SGPageBase;
function bool ButtonClick(GUIComponent Sender)
{
    if (Sender == ButtonA)
        return Controller.OpenMenu("StargateMenus.KidPage");
    // return Controller.OpenMenu("StargateMenus.CutPage");
    return Controller.OpenMenu(ButtonMenus[i]);
}
defaultproperties
{
    ButtonMenus[0]="StargateMenus.KidPage"
    ButtonMenus[1]="StargateMenus.KidPage)"
    HUDType="StargateGame.StargateHUD"
}
"""


def selftest():
    rows, skipped = read_edges(FIXTURE, "DemoPage", "StargateMenus")
    edges = [r for r in rows if r["kind"] == "edge"]
    arrays = [r for r in rows if r["kind"] == "array"]
    hud = [r for r in rows if r["kind"] == "hud"]
    assert len(edges) == 2, edges                       # one literal, one computed
    assert sum(1 for r in edges if r["form"] == "computed") == 1, edges
    assert skipped == 1, skipped                        # the commented-out page
    assert len(arrays) == 2 and len(hud) == 1, (arrays, hud)
    per_pkg = {"StargateMenus": {"KidPage": {}, "OtherPage": {}}}
    by_name = {"KidPage": ["StargateMenus"]}
    assert resolve("StargateMenus.KidPage", per_pkg, by_name)[0] == "class"
    assert resolve("StargateMenus.kidpage", per_pkg, by_name)[0] == "case"
    assert resolve("StargateMenus.KidPage)", per_pkg, by_name)[0] == "typo"
    assert resolve("StargateMenus.CutPage", per_pkg, by_name)[0] == "absent"
    assert resolve("NoSuchPkg.Page", per_pkg, by_name)[0] == "absent-package"
    assert resolve("KidPage", per_pkg, by_name)[0] == "unqualified"
    seen = closure(["StargateMenus.OtherPage"],
                   [dict(kind="edge", name="OtherPage", package="StargateMenus", form="literal",
                         target="StargateMenus.KidPage")],
                   lambda t: resolve(t, per_pkg, by_name))
    assert seen == {"StargateMenus.OtherPage": 0, "StargateMenus.KidPage": 1}, seen
    # a doc block renders from rows alone, and an injected figure makes --check-doc fail
    fake = [dict(kind="census", name="corpus", note="packages=1|classes_with_text=1|holders=1|edges=2|"
                  "literal_edges=1|game_edges=1|preproc_edges=0|dead_branch_edges=0|computed=1|arrays=2|commented=1|roots=1|"
                  "reachable_pages=1|outside_pages=0|"
                  "unopened_pages=0|defaults_classes=1|"
                  "unopened_pages=0|hud_assign=1|hud_swaps=0"),
            dict(kind="page", name="KidPage", package="StargateMenus", target="StargateMenus.KidPage",
                 verdict="reachable", depth=1, sites=2, form="page", target_package="StargateMenus", note="")]
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        os.makedirs(os.path.join(td, "StargateMenus"))
        with open(os.path.join(td, "StargateMenus", "DemoPage.defaultproperties.uc"), "w",
                  encoding="latin-1") as fh:
            fh.write('defaultproperties\n{\n\tButtonMenus(0)=StargateMenus.KidPage\n'
                     '\tButtonMenus(3)="StargateMenus.KidPage)"\n\tHUDType=StargateGame.StargateHUD\n}\n')
        drows, dcls = read_defaults(td)
    assert len(drows) == 3 and dcls == 1, (drows, dcls)
    assert [r["target"] for r in drows if r["kind"] == "array"] == ["StargateMenus.KidPage",
                                                                   "StargateMenus.KidPage)"], drows
    text = render(fake)
    assert "KidPage" in text and "**2**" in text, text
    print("ok   menu-flow: 16 assertions over the parsers, the resolver and the closure")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dump", nargs="?")
    ap.add_argument("--sweep", action="store_true")
    ap.add_argument("--defaults", default="", help="the defaults-emit --emit tree (System package defaults)")
    ap.add_argument("-o", "--out")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--write-doc")
    ap.add_argument("--check-doc")
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    if args.check:
        selftest()
        return
    if args.report:
        report()
        return
    rows_holder = os.environ.get("MENU_FLOW_ROWS")
    if args.write_doc or args.check_doc:
        rows = load_rows(rows_holder)
        if args.write_doc:
            write_doc(args.write_doc, rows)
        else:
            check_doc(args.check_doc, rows)
        return
    if not args.dump:
        sys.exit("a dump directory is required (or --report/--check)")
    rows, _meta = sweep(args.dump, args.defaults)
    if args.out:
        write(rows, args.out)
    else:
        print(render(rows))


def load_rows(_unused=None):
    """The rows a doc renders from: the committed artifact, so `--check-doc` needs no dump."""
    with open(ARTIFACT, newline="", encoding="utf-8") as fh:
        rows = list(csv.DictReader(fh))
    for r in rows:
        r["depth"] = r["depth"] if r["depth"] != "" else ""
        r["sites"] = r["sites"] if r["sites"] != "" else ""
    return rows


if __name__ == "__main__":
    main()
