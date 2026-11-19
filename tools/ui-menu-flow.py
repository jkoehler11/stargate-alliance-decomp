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

A page can also be reached without its *name* being an argument: a class-typed property can hold the page's
class (`Foo=class'StargateMenus.Bar'`), a body can mention the class (`class'StargateMenus.Bar'`), and the
build's `.ini` states the mid-game, demo, admin and chat-password menus as *keys*, not as `OpenMenu` calls.
Those are the other three reads, and each is recorded apart from a call so the census never adds a mention to
the flow:

* **class references** (`kind=classref`) -- from a `defaultproperties` value and from a class body, which is
  what `docs/systems/UI.md` predicted would explain the pages the closure cannot reach;
* **mentions** (`kind=mention`) -- a string in a class body or a defaults value that names a page but is *not*
  the argument of a menu call, with the commented-out ones kept apart. A page whose only mention is inside a
  comment is called `comment-only`, which is a different thing from a page nothing names;
* **config keys** (`kind=config`) -- every menu-class key in the shipped `.ini`s, live, commented or present
  with an empty value, which is what the computed `OpenMenu(MidGameMenuClass)` sites actually read.

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

# Every class-valued menu key the shipped `.ini`s carry. `GUIController` opens the mid-game, demo, admin and
# chat-password menus with `OpenMenu(MidGameMenuClass)`-shaped calls -- computed arguments the call census
# cannot resolve -- so these keys are where those calls' targets would be. `DefaultPlayerMenu` is the debug
# root window, counted here because it is the same class-valued shape.
MENU_CLASS_KEYS = {
    "mainmenuclass", "autoload", "midgamemenuclass", "demomenuclass", "adminmenuclass",
    "chatpasswordmenuclass", "connectingmenuclass", "disconnectmenuclass", "loadingclass",
    "singleplayermenuclass", "initialmenuclass", "defaultplayermenu",
}
# A key line, with the `;` that comments it out in this corpus kept: `Default.ini` states `MainMenuClass`
# twice, once commented and once live, and only the live one is a root.
INI_ANY = re.compile(r"^\s*(?P<comment>;+)?\s*(?P<key>[A-Za-z]+Class|AutoLoad|DefaultPlayerMenu)\s*="
                     r"\s*(?P<val>.*?)\s*$")
# A class reference: `class'Pkg.Class'` in a body, and `Prop=class'Pkg.Class'` in a defaults block.
CLSREF_CODE = re.compile(r"\bclass'(?P<val>[A-Za-z0-9_]+(?:\.[A-Za-z0-9_]+)?)'")
CLSREF_DEFAULT = re.compile(r"^\s*(?P<prop>[A-Za-z0-9_]+)(?:\(\d+\))?\s*=\s*class'(?P<val>[^']*)'\s*$", re.M)
ANY_LIT = re.compile(r'"([^"\\\n]{1,80})"')
DEFAULTS_ANY = re.compile(r"^\s*(?P<prop>[A-Za-z0-9_]+)(?:\(\d+\))?\s*=\s*(?P<val>.+?)\s*$", re.M)


def names_page(s, pages):
    """Whether a string names one of the page classes: `Pkg.Page` or a bare `Page`."""
    return bool(pages) and s.split(".")[-1] in pages


def mention_note(val, plat, pages):
    """Why a mention is not an edge -- a bare name, a branch this build does not compile, or nothing."""
    if "." not in val:
        return "bare name, so it resolves against the calling package"
    if not branch_live(plat):
        return "branch not compiled by this build"
    return ""



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


def read_edges(text, cls, pkgname, pages=frozenset()):
    """(rows, commented count) from one class body.

    `rows` carries the menu calls, and -- when `pages` names the page family -- the class references and the
    string mentions that are *not* call arguments, so a caller can tell "an `OpenMenu` names this page" apart
    from "a comment names it" and from "a class reference holds it".
    """
    code, commented = strip_comments(text)
    regions = preproc_regions(code)
    rows = []
    args = []
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
            args.append((m.start("arg"), m.end("arg")))
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
    mentions = 0
    if pages:
        for m in ANY_LIT.finditer(code):
            val = m.group(1)
            if not names_page(val, pages) or any(a <= m.start() < b for a, b in args):
                continue
            plat = branch_at(regions, m.start())
            rows.append(dict(kind="mention", name=cls, package=pkgname, form="code", target=val,
                             platform=plat, note=mention_note(val, plat, pages)))
            mentions += 1
        for m in ANY_LIT.finditer(commented):
            val = m.group(1)
            if names_page(val, pages):
                rows.append(dict(kind="mention", name=cls, package=pkgname, form="comment", target=val,
                                 platform="", note="inside a comment" + ("" if "." in val else ", bare name")))
                mentions += 1
        for m in CLSREF_CODE.finditer(code):
            val = m.group("val")
            if names_page(val, pages):
                plat = branch_at(regions, m.start())
                rows.append(dict(kind="classref", name=cls, package=pkgname, form="code", target=val,
                                 platform=plat, note="class literal in the body"))
    skipped = len(OPEN_CALL.findall(commented)) + len(ARRAY_SET.findall(commented))
    return rows, skipped



def resolve(target, per_pkg, by_name):
    """(verdict, target_package, target_class, note) for one `Pkg.Class` string."""
    pkgname, cls = split_target(target)
    if not pkgname:
        return "unqualified", "", cls, "no package prefix, so it resolves against the loading package"
    notes = []
    classes = per_pkg.get(pkgname)
    if classes is None:
        # The shipped `.ini`s spell a package `Stargategame` where the class table says `StargateGame`; a
        # package name is a name, not a path, so a case-only difference resolves rather than reading as a
        # missing package.
        folded = next((p for p in sorted(per_pkg) if p.lower() == pkgname.lower()), "")
        if not folded:
            return "absent-package", pkgname, cls, "no such package on disk"
        notes.append("package name differs in case: %s" % folded)
        pkgname = folded
        classes = per_pkg[pkgname]
    if cls in classes:
        return "class", pkgname, cls, "; ".join(notes)
    lower = {c.lower(): c for c in classes}
    if cls.lower() in lower:
        notes.append("case differs from the class")
        return "case", pkgname, lower[cls.lower()], "; ".join(notes)
    t = tidy(cls)
    if t and t != cls and t in classes:
        notes.append("resolves once the trailing %r is dropped" % cls[len(t):])
        return "typo", pkgname, t, "; ".join(notes)
    if by_name.get(cls):
        notes.append("a class of that name is in %s" % ", ".join(sorted(by_name[cls])))
        return "other-package", pkgname, cls, "; ".join(notes)
    return "absent", pkgname, cls, "; ".join(notes)



DEFAULTS_ARRAY = re.compile(r"^\s*ButtonMenus\((?P<idx>\d+)\)\s*=\s*(?P<val>.+?)\s*$", re.M)
DEFAULTS_HUD = re.compile(r"^\s*HUDType\s*=\s*(?P<val>.+?)\s*$", re.M)


def read_defaults(tree, pages=frozenset()):
    """`ButtonMenus(n)` and `HUDType` from the `defaults-emit --emit` tree, plus any other property whose value
    names a page and any `Prop=class'Pkg.Class'` reference to one.

    A class's `defaultproperties` are **not** in its `TextBuffer`: the block is compiled into the class record, so
    the menu array MainMenu's buttons come from is a property value, not a line of script. `tools/defaults-emit.py`
    renders those blocks (`make ui-menu-flow` runs it first), and this reads the two properties the UI needs.
    Every other value is read too, because a page named by a *property other than `ButtonMenus`* is a different
    edge source: a class-typed property holding a page would be the "opened by class reference" route.
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
            if not pages:
                continue
            for m in CLSREF_DEFAULT.finditer(text):
                val = m.group("val")
                if names_page(val, pages):
                    rows.append(dict(kind="classref", name=cls, package=pkgname, form="defaults",
                                     target=val, platform="", note=m.group("prop")))
            for m in DEFAULTS_ANY.finditer(text):
                prop = m.group("prop")
                if prop in ("ButtonMenus", "HUDType"):
                    continue
                val = m.group("val").strip()
                if val.startswith("class'") or "'" in val or ' ' in val:
                    continue                       # a class ref (counted above) or a typed/typed-string value
                if val.startswith('"') and val.endswith('"'):
                    val = val[1:-1]
                if names_page(val, pages):
                    rows.append(dict(kind="mention", name=cls, package=pkgname, form="defaults",
                                     target=val, platform="", note=prop))
    return rows, classes


def read_inis(system_dir):
    """Every menu-class key in the shipped `.ini`s: live, commented out, or present with an empty value.

    `GUIController`'s mid-game, demo, admin and chat-password menus are opened with a computed argument
    (`OpenMenu(MidGameMenuClass)`), so the key is the only place their target can be read -- and in this build
    the four keys are present and empty, which is a statement about the menu, not a gap in the reader.
    """
    rows = []
    for fname in sorted(os.listdir(system_dir)):
        if not fname.lower().endswith(".ini"):
            continue
        with open(os.path.join(system_dir, fname), "rb") as fh:
            raw = fh.read().decode("latin-1")
        for line in raw.splitlines():
            m = INI_ANY.match(line)
            if not m or m.group("key").lower() not in MENU_CLASS_KEYS:
                continue
            val = (m.group("val") or "").strip()
            notes = []
            if m.group("comment"):
                notes.append("commented out")
            if not val:
                notes.append("empty value")
            rows.append(dict(kind="config", name=fname, package="", form=m.group("key").lower(),
                             target=val, platform="", note="; ".join(notes)))
    return rows


def live_roots(ini_rows):
    """The page the build starts at: the live `MainMenuClass=` (or `AutoLoad=`), never a commented one."""
    roots = sorted({r["target"] for r in ini_rows
                    if r["form"] in ("mainmenuclass", "autoload") and r["target"]
                    and "commented out" not in r["note"]})
    return roots



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


def page_family(supers, by_name):
    """{class: package} for every class outside the stock packages whose chain reaches `SGPageBase`/`GUIPage`."""
    family = {}
    for c in supers:
        cur, seen = c, set()
        while cur in supers and cur not in seen:
            seen.add(cur)
            nxt = supers[cur]
            if nxt in ("SGPageBase", "GUIPage"):
                if c not in ("SGPageBase", "GUIPage") and by_name[c][0] not in STOCK:
                    family[c] = by_name[c][0]
                break
            cur = nxt
    return family


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
    family = page_family(supers, by_name)        # the page set is needed *before* the bodies are read
    page_names = frozenset(family)

    rows, skipped_total, holders = [], 0, 0
    for (pkgname, cls), text in sorted(text_of.items()):
        got, skipped = read_edges(text, cls, pkgname, page_names)
        skipped_total += skipped
        if any(r["kind"] in ("edge", "array", "hud", "hud_swap") for r in got):
            holders += 1
        rows.extend(got)

    def_rows, def_classes = read_defaults(defaults_tree, page_names)
    rows.extend(def_rows)
    ini_rows = read_inis(system_dir)
    roots = live_roots(ini_rows)

    for r in rows:
        if r["kind"] in ("edge", "array", "classref") and r["form"] != "computed":
            r["_resolved"] = resolve(r["target"], per_pkg, by_name)

    # A class reference is an edge like any other -- there are none in this build, so it does not move the
    # closure, but a rebuild that adds one lands in the same walk instead of silently outside it.
    reach = closure(roots, [r for r in rows if r["kind"] in ("edge", "array", "classref")],
                    lambda t: resolve(t, per_pkg, by_name))

    opened_from = collections.Counter()
    for r in rows:
        if r["kind"] in ("edge", "array", "classref") and r["form"] != "computed":
            verdict, tpkg, tcls, _note = r["_resolved"]
            if verdict in ("class", "case", "typo"):
                opened_from["%s.%s" % (tpkg, tcls)] += 1

    out = []
    for r in rows:
        r = dict(r)
        if r["kind"] in ("edge", "array", "classref") and r["form"] != "computed":
            verdict, tpkg, tcls, note = r["_resolved"]
        elif r["kind"] in ("hud", "hud_swap", "mention"):
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
        r.update(target_package=tpkg, verdict=verdict, depth=reach.get(r["target"], ""), sites=1,
                 note="; ".join(x for x in (r["note"], note) if x))
        out.append(r)

    # A mention only counts as naming a page when it *resolves* to that page's class: `StyleName=MainMenu`
    # names a GUI style, not the main menu, and a bare name has no package to resolve against.
    mentioned = collections.defaultdict(set)
    for r in out:
        if r["kind"] == "mention" and r["verdict"] in ("class", "case", "typo"):
            mentioned[r["target"].split(".")[-1]].add((r["package"] + "." + r["name"], r["form"]))


    pages = []
    for cls in sorted(family):
        pkgname = family[cls]
        label = "%s.%s" % (pkgname, cls)
        if label in reach:
            verdict, depth, note = "reachable", reach[label], ""
        elif opened_from.get(label):
            verdict, depth, note = "outside", "", "opened from %d site(s) no root reaches" % opened_from[label]
        elif mentioned.get(cls):
            code = sorted(h for h, f in mentioned[cls] if f != "comment")
            com = sorted(h for h, f in mentioned[cls] if f == "comment")
            verdict, depth = ("mention", "") if code else ("comment-only", "")
            note = "; ".join(x for x in (
                "named by a string that is not a call: %s" % ", ".join(code[:3]) if code else "",
                "named only inside a comment: %s" % ", ".join(com[:3]) if com else "") if x)
        else:
            verdict, depth, note = "unopened", "", "nothing in the corpus names it, by string or by class"
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
                      "mention_pages=%d" % count("page", "mention"),
                      "comment_only_pages=%d" % count("page", "comment-only"),
                      "unopened_pages=%d" % count("page", "unopened"),
                      "defaults_classes=%d" % def_classes,
                      "hud_assign=%d" % count("hud"),
                      "hud_swaps=%d" % count("hud_swap"),
                      "classrefs=%d" % count("classref"),
                      "mentions=%d" % count("mention"),
                      "mention_comment=%d" % sum(1 for r in out if r["kind"] == "mention"
                                                 and r["form"] == "comment"),
                      "mention_unqualified=%d" % sum(1 for r in out if r["kind"] == "mention"
                                                     and r["verdict"] not in ("class", "case", "typo")),
                      "config_rows=%d" % count("config"),
                      "config_empty=%d" % sum(1 for r in out if r["kind"] == "config"
                                              and "empty value" in r["note"]),
                      "config_commented=%d" % sum(1 for r in out if r["kind"] == "config"
                                                  and "commented out" in r["note"]),
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


def interaction_composition():
    """The `SGMenuInteraction` composition: its declared properties and its emitted defaults.

    `docs/systems/UI.md` called this class an "editor-tuned configuration blob" with 294 properties and
    suggested it might hold a page table. The declared set is in `inventory/class_properties.csv` and the
    emitted values in `inventory/class_defaults_per_class.csv`, so the claim is checkable without a dump.
    """
    declared, kinds = 0, collections.Counter()
    class_typed = 0
    try:
        with open(os.path.join(ROOT, "inventory/class_properties.csv"), newline="", encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                if r["class"] != "SGMenuInteraction":
                    continue
                declared += 1
                kinds[r["friendly_type"] or r["kind"]] += 1
                if "Class" in r["kind"] and r["property"] != "Class":
                    class_typed += 1
    except OSError:
        return None
    defaults = ""
    try:
        with open(os.path.join(ROOT, "inventory/class_defaults_per_class.csv"), newline="",
                  encoding="utf-8") as fh:
            for r in csv.DictReader(fh):
                if r["class"] == "SGMenuInteraction":
                    defaults = r["defaults"]
                    break
    except OSError:
        pass
    top = ", ".join("%s %d" % (k, n) for k, n in kinds.most_common(5))
    return dict(declared=declared, class_typed=class_typed, defaults=defaults, top=top)


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
    named = [r for r in rows if r["kind"] in ("mention", "classref")]
    config = [r for r in rows if r["kind"] == "config"]
    total_pages = (int(s["reachable_pages"]) + int(s["outside_pages"]) + int(s["mention_pages"])
                   + int(s["comment_only_pages"]) + int(s["unopened_pages"]))
    out = []
    out.append("**%s of the %s `OpenMenu`-family sites carry a literal page target, %s of those are in the game's "
               "own packages, and %d name a class their own package does not export; %s of the %s menu pages are "
               "reachable from the build's own `MainMenuClass` and the rest split three ways -- %s are named only "
               "by a string that is not a call, %s only inside a comment, and %s nothing names at all.**" %
               (s["literal_edges"], s["edges"], s["game_edges"], len(dead), s["reachable_pages"], total_pages,
                s["mention_pages"], s["comment_only_pages"], s["unopened_pages"]))
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
                     ("classrefs", "class references naming a page (defaults + bodies)"),
                     ("mentions", "strings naming a page outside a menu call"),
                     ("mention_comment", "... of those inside a comment"),
                     ("mention_unqualified", "... that name no package at all"),
                     ("config_rows", "menu-class keys in the shipped `.ini`s"),
                     ("config_empty", "... present with no value"),
                     ("config_commented", "... commented out"),
                     ("reachable_pages", "pages reachable from a root"),
                     ("outside_pages", "pages opened only from outside the graph"),
                     ("mention_pages", "pages named only by a non-call string"),
                     ("comment_only_pages", "pages named only inside a comment"),
                     ("unopened_pages", "pages nothing names at all"),
                     ("hud_assign", "`HUDType=` assignments"), ("hud_swaps", "runtime HUD swaps")]:
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
    rank = {"reachable": 0, "outside": 1, "mention": 2, "comment-only": 3, "unopened": 4}
    for r in sorted(pages, key=lambda r: (rank.get(r["verdict"], 5),
                                          int(r["depth"]) if str(r["depth"]).isdigit() else 99, r["name"])):
        out.append("| `%s.%s` | %s | %s | %s |" % (r["package"], r["name"], r["verdict"],
                                                  r["depth"] if r["depth"] != "" else "-", r["sites"]))
    out.append("")
    out.append("### The config keys, which are the computed calls' targets")
    out.append("")
    out.append("| file | key | value | verdict |")
    out.append("|---|---|---|---|")
    for r in sorted(config, key=lambda r: (r["name"], r["form"])):
        out.append("| %s | `%s` | %s | %s |" % (r["name"], r["form"], "`%s`" % r["target"] if r["target"] else "-",
                                                r["note"] or r["verdict"] or "-"))
    out.append("")
    out.append("### What names the pages no call names")
    out.append("")
    unreached = {r["name"] for r in pages if r["verdict"] in ("mention", "comment-only", "unopened")}
    named = [r for r in named if r["target"].split(".")[-1] in unreached] if named else named
    naming = [r for r in named if r["verdict"] in ("class", "case", "typo")]
    unqualified = [r for r in rows if r["kind"] == "mention" and r["verdict"] not in ("class", "case", "typo")]
    if naming:
        out.append("| page | holder | form | verdict |")
        out.append("|---|---|---|---|")
        for r in sorted(naming, key=lambda r: (r["kind"], r["target"], r["package"], r["name"])):
            out.append("| `%s` | `%s.%s` | %s | %s |" % (r["target"], r["package"], r["name"], r["form"],
                                                         r["note"] or r["verdict"] or "-"))
    else:
        out.append("Nothing: no class reference in a defaults block or a class body names a page, and no string "
                   "outside a menu call names one, so every page the closure does not reach was cut rather than "
                   "held by a class.")
    if unqualified:
        out.append("")
        out.append("%d further value(s) look like a page name but name something else — `%s`, which resolves to "
                   "no package — and are in the artifact with an `unqualified` verdict rather than counted here."
                   % (len(unqualified), "`, `".join(sorted({("%s=%s" % (r["note"], r["target"]))
                                                            for r in unqualified})[:4])))

    comp = interaction_composition()
    if comp:
        out.append("")
        out.append("### `SGMenuInteraction`, the 294-property class, is a style table")
        out.append("")
        out.append("| measure | answer |")
        out.append("|---|---|")
        out.append("| declared properties | **%d** |" % comp["declared"])
        out.append("| ... class-typed | **%d** |" % comp["class_typed"])
        out.append("| ... the five commonest types | %s |" % comp["top"])
        out.append("| values in its emitted defaults block | **%s** |" % (comp["defaults"] or "-"))
        out.append("| properties naming a page | **0** |")
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
    print("pages: %(reachable_pages)s reachable, %(outside_pages)s outside, %(mention_pages)s mention-only, "
          "%(comment_only_pages)s comment-only, %(unopened_pages)s unopened" % s)
    print("names: %(classrefs)s class references, %(mentions)s non-call strings (%(mention_comment)s commented), "
          "%(config_rows)s config keys (%(config_empty)s empty, %(config_commented)s commented)" % s)
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


FIXTURE2 = """
class DemoPanel extends SGPageBase;
function bool KeyEvent(string Key)
{
    local string S;
    S = "StargateMenus.KidPage";
    /* return Controller.OpenMenu("StargateMenus.CutPage"); */
    LoadPage(class'StargateMenus.KidPage');
    return true;
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
    # a body that *names* a page without calling `OpenMenu` on it: one string, one class literal, one comment
    rows2, skipped2 = read_edges(FIXTURE2, "DemoPanel", "StargateMenus", frozenset({"KidPage", "CutPage"}))
    code_mentions = [r for r in rows2 if r["kind"] == "mention" and r["form"] == "code"]
    comment_mentions = [r for r in rows2 if r["kind"] == "mention" and r["form"] == "comment"]
    classrefs = [r for r in rows2 if r["kind"] == "classref"]
    assert len(code_mentions) == 1 and code_mentions[0]["target"] == "StargateMenus.KidPage", code_mentions
    assert len(comment_mentions) == 1 and comment_mentions[0]["target"] == "StargateMenus.CutPage", comment_mentions
    assert len(classrefs) == 1 and classrefs[0]["target"] == "StargateMenus.KidPage", classrefs
    assert skipped2 == 1, skipped2                      # the commented `OpenMenu` is still a commented call
    # a page named by a call argument is an edge, never also a mention
    assert not [r for r in rows if r["kind"] == "mention"], rows
    per_pkg = {"StargateMenus": {"KidPage": {}, "OtherPage": {}}}
    by_name = {"KidPage": ["StargateMenus"]}
    assert resolve("StargateMenus.KidPage", per_pkg, by_name)[0] == "class"
    assert resolve("StargateMenus.kidpage", per_pkg, by_name)[0] == "case"
    assert resolve("StargateMenus.KidPage)", per_pkg, by_name)[0] == "typo"
    assert resolve("StargateMenus.CutPage", per_pkg, by_name)[0] == "absent"
    assert resolve("NoSuchPkg.Page", per_pkg, by_name)[0] == "absent-package"
    assert resolve("KidPage", per_pkg, by_name)[0] == "unqualified"
    # a package name that differs only in case resolves (the `.ini`s write `Stargategame`)
    assert resolve("stargatemenus.KidPage", per_pkg, by_name)[0] == "class"
    assert resolve("stargatemenus.KidPage", per_pkg, by_name)[3] == "package name differs in case: StargateMenus"
    seen = closure(["StargateMenus.OtherPage"],
                   [dict(kind="edge", name="OtherPage", package="StargateMenus", form="literal",
                         target="StargateMenus.KidPage")],
                   lambda t: resolve(t, per_pkg, by_name))
    assert seen == {"StargateMenus.OtherPage": 0, "StargateMenus.KidPage": 1}, seen
    # a doc block renders from rows alone, and an injected figure makes --check-doc fail
    fake = [dict(kind="census", name="corpus", note="packages=1|classes_with_text=1|holders=1|edges=2|"
                  "literal_edges=1|game_edges=1|preproc_edges=0|dead_branch_edges=0|computed=1|arrays=2|commented=1|"
                  "roots=1|classrefs=1|mentions=2|mention_comment=1|mention_unqualified=0|config_rows=3|config_empty=1|"
                  "config_commented=1|reachable_pages=1|outside_pages=0|mention_pages=1|comment_only_pages=1|"
                  "unopened_pages=0|defaults_classes=1|hud_assign=1|hud_swaps=0"),
            dict(kind="page", name="KidPage", package="StargateMenus", target="StargateMenus.KidPage",
                 verdict="reachable", depth=1, sites=2, form="page", target_package="StargateMenus", note=""),
            dict(kind="page", name="CutPage", package="StargateMenus", target="StargateMenus.CutPage",
                 verdict="comment-only", depth="", sites=0, form="page", target_package="StargateMenus",
                 note="named only inside a comment")]
    import tempfile
    with tempfile.TemporaryDirectory() as td:
        os.makedirs(os.path.join(td, "StargateMenus"))
        with open(os.path.join(td, "StargateMenus", "DemoPage.defaultproperties.uc"), "w",
                  encoding="latin-1") as fh:
            fh.write('defaultproperties\n{\n\tButtonMenus(0)=StargateMenus.KidPage\n'
                     '\tButtonMenus(3)="StargateMenus.KidPage)"\n\tHUDType=StargateGame.StargateHUD\n'
                     "\tSomePage=class'StargateMenus.KidPage'\n\tOtherPage=StargateMenus.KidPage\n"
                     "\tIcon=Texture'Engine.cursor'\n}\n")
        drows, dcls = read_defaults(td, frozenset({"KidPage"}))
    assert len(drows) == 5 and dcls == 1, (drows, dcls)
    assert [r["target"] for r in drows if r["kind"] == "array"] == ["StargateMenus.KidPage",
                                                                   "StargateMenus.KidPage)"], drows
    assert [r["note"] for r in drows if r["kind"] == "classref"] == ["SomePage"], drows
    assert [r["note"] for r in drows if r["kind"] == "mention"] == ["OtherPage"], drows
    with tempfile.TemporaryDirectory() as td:
        with open(os.path.join(td, "Default.ini"), "w", encoding="latin-1") as fh:
            fh.write(";MainMenuClass=GUI.MyMainMenu\nMainMenuClass=StargateMenus.MainMenu\n"
                     "MidGameMenuClass=\nUnrelated=4\n")
        ini = read_inis(td)
    assert len(ini) == 3, ini
    assert live_roots(ini) == ["StargateMenus.MainMenu"], live_roots(ini)
    assert sum(1 for r in ini if "empty value" in r["note"]) == 1, ini
    assert sum(1 for r in ini if "commented out" in r["note"]) == 1, ini
    text = render(fake)
    assert "KidPage" in text and "**2**" in text and "comment-only" in text, text
    print("ok   menu-flow: 26 assertions over the parsers, the resolver, the closure and the three name readers")



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
