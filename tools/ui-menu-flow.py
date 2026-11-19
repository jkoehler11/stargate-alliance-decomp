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

# The four controller-button hints `SGPageBase.InitComponent` wires, and the `Engine.Localizer` string each
# caption comes from. The base declares the four flags with no value, so a page opts in by defaulting one True.
HINT_FLAGS = (("bButtonA", "sSelect"), ("bButtonB", "sBack"), ("bButtonX", "sDelete"), ("bButtonY", "sCreateNew"))
CAPTION_KEYS = ("sSelect", "sBack", "sDelete", "sCreateNew")
# `SGPageBase`'s own `TitleString`; a page that never sets one still shows this placeholder.
PLACEHOLDER_TITLE = "Basic SG GUI Page: Insert Title"
# Properties a page's own defaults use to draw something, as opposed to behaviour, a child handle or a delegate.
DRAW_PROPS = {"StyleName", "Background", "BackgroundStyle", "WinLeft", "WinTop", "WinWidth", "WinHeight",
              "BoundingType", "ScalingType"}
BEGIN_OBJECT = re.compile(r"^\s*Begin\s+Object\b")
END_OBJECT = re.compile(r"^\s*End\s+Object\b")
# A component block states its class and the property the page binds it to: `Begin Object Class=GUILabel Name=cTitle`.
BEGIN_COMP = re.compile(r"^\s*Begin\s+Object\b(?P<rest>.*)$")
COMP_CLASS = re.compile(r"\bClass=(?P<cls>[A-Za-z0-9_]+)")
COMP_NAME = re.compile(r"\bName=(?P<name>[A-Za-z0-9_]+)")
# The four `GUIComponent` geometry values a widget can state for itself.
WIN_KEYS = ("WinTop", "WinLeft", "WinWidth", "WinHeight")
# A class-typed default names a subobject as `Class'Name'`; the block that declares it may be in another file.
REF_VALUE = re.compile(r"^(?P<cls>[A-Za-z_][A-Za-z0-9_]*)'(?P<name>[A-Za-z_][A-Za-z0-9_]*)'$")
TOP_PROP = re.compile(r"^\s*(?P<prop>[A-Za-z0-9_]+)(?:\(\d+\))?\s*=\s*(?P<val>.*?)\s*$")
FUNC_DECL = re.compile(r"^function\s+(?:[A-Za-z0-9_]+\s+)?(?P<name>[A-Za-z0-9_]+)\s*\(", re.M)
XC_CODE = re.compile(r"\bXC_[A-Z]+\b")
# The four functions `SGPageBase` uses to wire a page: the component setup, the controller handler, the key
# handler and the activation hook. Overriding one is how a page replaces the base's behaviour.
WIRING_FUNCS = ("InitComponent", "InternalOnXControllerEvent", "MyKeyEvent", "InternalOnActivate")


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


def read_default_block(tree, pkgname, cls):
    """(top-level properties, subobject count) from a class's emitted `defaultproperties` file.

    `defaults-emit` renders a page's `Begin Object ... End Object` children *inline*, and a child's properties
    (`WinTop`, `StyleName`, `Caption`, its own `bButtonA`) are the component's, not the page's. A first pass that
    counted every `Name=value` line reported 31 pages setting `WinTop` where the page itself sets it on two, so
    the file is read with the nesting tracked and only depth-0 properties are the page's own.
    """
    path = os.path.join(tree, pkgname, cls + ".defaultproperties.uc")
    if not os.path.isfile(path):
        return {}, 0
    props, depth, children = {}, 0, 0
    with open(path, encoding="latin-1") as fh:
        for line in fh:
            if BEGIN_OBJECT.match(line):
                depth += 1
                children += 1
                continue
            if END_OBJECT.match(line):
                depth = max(0, depth - 1)
                continue
            if depth:
                continue
            m = TOP_PROP.match(line)
            if m and not m.group("prop").startswith("//"):
                props[m.group("prop")] = m.group("val")
    return props, children


def super_path(pkgname, cls, supers, by_name):
    """The class's chain up the tree, package-qualified, stopping at a name the corpus does not carry."""
    path, cur, curpkg, seen = [], cls, pkgname, set()
    while cur and cur not in seen and len(path) < 16:
        seen.add(cur)
        path.append((curpkg, cur))
        nxt = supers.get(cur, "")
        if not nxt or nxt == "None":
            break
        pkgs = by_name.get(nxt) or []
        curpkg = curpkg if curpkg in pkgs else (pkgs[0] if pkgs else curpkg)
        cur = nxt
    return path


def read_page_defaults(tree, family, supers, by_name):
    """What each page's own defaults state: its four hint flags, its title, and how much of it is visual.

    `SGPageBase.InitComponent` builds four `ControllerButton`s and calls `SetVisibility(bButtonA)`..`bButtonY` on
    them, so a page's controller hints are four booleans in the same defaults tree the flow's `ButtonMenus` array
    comes from -- and the base declares them with no value, which is why a page that sets none shows none. A page
    can also inherit one (three take `bButtonA`/`bButtonB` from `OptionsEditBase`), so the effective value is read
    up the chain and the row names the class it came from.

    The same pass measures what else a page states: its `TitleString`, the ten visual rows six pages carry, and
    the `Begin Object` children (114 blocks over 46 pages) that hold the widgets, which is where a page's drawing
    actually lives. Values are not committed -- only the property names and the captions the flags print.
    """
    captions = {}
    loc, _ = read_default_block(tree, "Engine", "Localizer")
    for key in CAPTION_KEYS:
        if key in loc:
            captions[key] = loc[key].strip('"').strip()
    rows, stats = [], collections.Counter()
    for cls, pkgname in sorted(family.items()):
        path = super_path(pkgname, cls, supers, by_name)
        own, children = read_default_block(tree, pkgname, cls)
        stats["page_blocks"] += 1 if (own or children) else 0
        stats["page_top_props"] += len(own)
        stats["page_components"] += children
        stats["pages_with_components"] += 1 if children else 0
        if "TitleString" in own:
            stats["titles_own"] += 1
        title = ""
        for p2, c2 in path:                      # effective title, for the pages that never set one
            blk, _ = read_default_block(tree, p2, c2)
            if "TitleString" in blk:
                title = blk["TitleString"].strip('"')
                break
        if title == PLACEHOLDER_TITLE:
            stats["placeholder_titles"] += 1
        draw = sorted(k for k in own if k in DRAW_PROPS)
        stats["draw_rows"] += len(draw)
        stats["draw_pages"] += 1 if draw else 0
        if draw:
            rows.append(dict(kind="draw", name=cls, package=pkgname, form="defaults", target=",".join(draw),
                             verdict=len(draw), platform="", note=""))
        if "URL" in own:
            stats["url_pages"] += 1
            rows.append(dict(kind="draw", name=cls, package=pkgname, form="defaults", target="URL", verdict=1,
                             platform="", note=own["URL"].strip().strip('"')[:60]))
        flags = {}
        for flag, key in HINT_FLAGS:
            value, src = "False", ""
            for i, (p2, c2) in enumerate(path):
                blk, _ = (own, children) if i == 0 else read_default_block(tree, p2, c2)
                if flag in blk:
                    value = "True" if blk[flag].strip().lower() == "true" else "False"
                    src = "own" if i == 0 else "%s.%s" % (p2, c2)
                    break
            flags[flag] = value
            if value == "True":
                rows.append(dict(kind="hint", name=cls, package=pkgname,
                                 form="own" if src == "own" else "inherited", target=flag, platform="",
                                 note="%s via Engine.Localizer.%s%s" %
                                      (captions.get(key, "?"), key,
                                       "" if src == "own" else ", inherited from " + src)))
                stats["hints_on"] += 1
                stats["hint_own" if src == "own" else "hint_inherited"] += 1
        if any(v == "True" for v in flags.values()):
            stats["hint_pages"] += 1
        else:
            stats["no_hint_pages"] += 1
    for key, text in sorted(captions.items()):
        rows.append(dict(kind="caption", name=key, package="Engine", form="defaults", target=text,
                         platform="", note="Localizer"))
    stats["captions"] = len(captions)
    return rows, stats


def read_children(tree, pkgname, cls):
    """A class's own `Begin Object` children, one dict per block: its widget class, its `Name=` and its properties.

    `defaults-emit` renders a page's subobjects inline, each as `Begin Object Class=X Name=Y` ... `End Object`,
    so a page's layout is this list rather than its own properties: `Class=` is the widget's class and `Name=`
    the property the page binds it to. Only a depth-1 block is a component of this class; a nested block belongs
    to its parent component and is not counted twice.
    """
    path = os.path.join(tree, pkgname, cls + ".defaultproperties.uc")
    if not os.path.isfile(path):
        return []
    kids, depth, cur = [], 0, None
    with open(path, encoding="latin-1") as fh:
        for line in fh:
            m = BEGIN_COMP.match(line)
            if m:
                depth += 1
                if depth == 1:
                    c, n = COMP_CLASS.search(m.group("rest")), COMP_NAME.search(m.group("rest"))
                    cur = dict(cls=c.group("cls") if c else "", name=n.group("name") if n else "", props=[])
                    kids.append(cur)
                continue
            if END_OBJECT.match(line):
                depth = max(0, depth - 1)
                if depth == 0:
                    cur = None
                continue
            if depth == 1 and cur is not None:
                p = TOP_PROP.match(line)
                if p and not p.group("prop").startswith("//"):
                    cur["props"].append(p.group("prop"))
                    cur.setdefault("values", {})[p.group("prop")] = p.group("val")
    return kids


def class_home(tree, cls, pkgname, by_name):
    """The package a widget class's emitted defaults live in: the corpus's own package for the name when the
    tree carries that class there, else the page's.

    A component's class is rarely in the page's package -- `GUIButton` is in `GUI` and the Stargate pages are in
    `StargateMenus` -- so reading its defaults from the page's directory would find no file and report "no
    geometry anywhere" for every widget.
    """
    for p in (by_name.get(cls) or []):
        if os.path.isfile(os.path.join(tree, p, cls + ".defaultproperties.uc")):
            return p
    return pkgname


def class_geometry(tree, pkgname, cls, supers, by_name, _cache=None):
    """Where a widget's `Win*` geometry comes from when the page states none: the nearest class on its chain
    whose own defaults state one, or `""` when no emitted block does.

    A component that sets no `WinLeft`/`WinTop`/`WinWidth`/`WinHeight` still draws somewhere: `GUIButton`'s and
    `GUILabel`'s own blocks carry the values the widget falls back on, and this names the class that supplies
    them. The cache is per tree because a self-test reads a second, synthetic one.
    """
    if _cache is None:
        _cache = {}
    key = (tree, pkgname, cls)
    if key in _cache:
        return _cache[key]
    found = ""
    for p2, c2 in super_path(pkgname, cls, supers, by_name):
        blk, _ = read_default_block(tree, p2, c2)
        if any(k in blk for k in WIN_KEYS):
            found = "%s.%s" % (p2, c2)
            break
    _cache[key] = found
    return found


def read_components(tree, family, supers, by_name, per_pkg):
    """What each page's `Begin Object` blocks declare: the widget class, whether the page states it or a
    subclass restates an inherited one, how much of its own geometry it carries, and where that geometry
    comes from when it carries none.

    The page-level census counts these blocks (`114 over 46 pages`); this reads them. Two questions it
    answers: a page's widget set is *not* inherited the way its hint flags are -- `SGPageBase`'s five
    components are declared once and by no page again, so a page's own blocks are additions to a shared
    frame -- and a page can replace an inherited widget outright by restating its `Name=` with a subclass,
    which is how `MultiplayerMapSelect` turns `SinglePlayerChapterSelect`'s chapter panel into a
    `MapSelectionPanel`.
    """
    known = {c.lower() for pkg in per_pkg for c in per_pkg[pkg]}
    rows, stats, classes, cache = [], collections.Counter(), collections.Counter(), {}
    for cls, pkgname in sorted(family.items()):
        path = super_path(pkgname, cls, supers, by_name)
        own = read_children(tree, pkgname, cls)
        if not own:
            continue
        anc = {}
        for p2, c2 in path[1:]:
            for c in read_children(tree, p2, c2):
                anc.setdefault(c["name"], ("%s.%s" % (p2, c2), c["cls"]))
        stats["pages"] += 1
        for c in own:
            stats["components"] += 1
            classes[c["cls"]] += 1
            geo = [p for p in c["props"] if p in WIN_KEYS]
            note = "props=%d geometry=%d/4" % (len(c["props"]), len(geo))
            if len(geo) == 4:
                stats["geom_full"] += 1
            elif not geo:
                stats["geom_none"] += 1
                src = class_geometry(tree, class_home(tree, c["cls"], pkgname, by_name), c["cls"],
                                     supers, by_name, cache)
                if src:
                    stats["geom_from_class"] += 1
                    note += ", geometry from " + src
                else:
                    stats["geom_nowhere"] += 1
            form = "own"
            if c["name"] in anc:
                where, where_cls = anc[c["name"]]
                stats["restated"] += 1
                form = "restated"
                note += "; restates %s whose class is %s" % (where, where_cls)
            else:
                stats["own"] += 1
            if c["cls"].lower() not in known:
                stats["unresolved"] += 1
            rows.append(dict(kind="component", name=cls, package=pkgname, form=form, target=c["name"],
                             verdict=c["cls"], platform="", note=note))
    stats["classes"] = len(classes)
    return rows, stats


def box_of(child):
    """A block's four `Win*` values as a tuple, or `None` when it states fewer than four."""
    vals = child.get("values") or {}
    try:
        return tuple(float(vals[k]) for k in WIN_KEYS)
    except (KeyError, ValueError):
        return None


def class_pages(tree, family):
    """{widget class: [(page, the page-side rectangle the page places it at)]} for a page's components.

    The rectangle is the *page's* statement of where the component goes; it is carried because a class used by
    several pages is the argument for what a child's `Win*` is relative to (one defaults block per class, so the
    children's numbers cannot encode a screen position when the instances sit in different places).
    """
    pages_of = collections.defaultdict(list)
    for cls, pkgname in sorted(family.items()):
        for c in read_children(tree, pkgname, cls):
            pages_of[c["cls"]].append(("%s.%s" % (pkgname, cls), box_of(c)))
    return pages_of


def layout_gaps(classes):
    """{class: row} for the component classes whose defaults *block* the reader refuses.

    A widget class the defaults tree does not carry is not therefore a panel built in script: the package holds
    its block (`inventory/class_defaults_misses.csv` names the declared property count, the one tag the block
    holds and the property it names), and the emitted tree has no file because the class-level locator ranks a
    parse inside the record's closing group above the block at the group's end. The join is against that
    committed artifact so the doc says *why* the layout is missing, not just that it is.
    """
    path = os.path.join(ROOT, "inventory", "class_defaults_misses.csv")
    out = {}
    if not os.path.isfile(path):
        return out
    with open(path, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            if r["class"] in classes:
                out[r["class"]] = r
    return out


def read_subcomponents(tree, family, supers, by_name, _cache=None):
    """What a page's component declares *inside itself*: the panel classes' own nested layout.

    A page's `Begin Object` block names a widget class and, most of the time, states only that class's
    rectangle -- the contents are in the class's own defaults. `MapSelectionPanel` declares a filter box, its
    label and the map list, so a page that binds `chPN=MapSelectionPanel'cPN'` has a three-row layout that the
    page's block does not contain. This is the second read: for every widget class a page names, the class's own
    blocks become `depth=2` rows, and a child class that declares blocks of its own (`GUIVertScrollBar` names
    its zone and two buttons) is followed to `depth=3`, so the walk is a tree rather than one level.

    A widget class with no emitted file is reported as `form="none"` and joined to the defaults chooser's own
    verdict by `layout_gaps` -- its block is in the package, one tag long, and the ledgers name the tag.
    """
    if _cache is None:
        _cache = {}
    pages_of = class_pages(tree, family)
    rows, stats, child_classes = [], collections.Counter(), set()
    max_depth = 6
    for cls in sorted(pages_of):
        uses = pages_of[cls]
        users = [u for u, _box in uses]
        home = class_home(tree, cls, users[0].split(".")[0], by_name)
        stats["classes"] += 1
        stats["uses"] += len(users)
        boxes = {b for _u, b in uses if b}
        if len(users) > 1:
            stats["multi_use"] += 1
            if len(boxes) > 1:
                stats["multi_place"] += 1
        stack, seen = [(cls, home, 2)], {(cls, home)}
        first = True
        while stack:
            owner, opkg, level = stack.pop(0)
            kids = read_children(tree, opkg, owner)
            if first:
                first = False
                if kids:
                    stats["with_layout"] += 1
                elif os.path.isfile(os.path.join(tree, opkg, owner + ".defaultproperties.uc")):
                    stats["leaf"] += 1                       # a widget: its block states no children
                    rows.append(dict(kind="subcomponent", name=cls, package=home, form="leaf", target="",
                                     verdict="", depth="2", platform="",
                                     note="used by %d page(s); its block declares no children" % len(users)))
                    continue
                else:
                    stats["no_layout"] += 1                  # no emitted block: the block exists, refused
                    rows.append(dict(kind="subcomponent", name=cls, package=home, form="none", target="",
                                     verdict="", depth="2", platform="",
                                     note="used by %d page(s); no emitted defaults block" % len(users)))
                    continue
            else:
                stats["second_level"] += 1
            for k in kids:
                child_classes.add(k["cls"])
                stats["children"] += 1
                stats["depth_%d" % level] += 1
                geo = [p for p in WIN_KEYS if p in k["props"]]
                note = "props=%d geometry=%d/4" % (len(k["props"]), len(geo))
                if len(geo) == 4:
                    stats["geom_full"] += 1
                    try:
                        box = tuple(float(k.get("values", {})[p]) for p in WIN_KEYS)
                    except ValueError:
                        box = ()
                    if box and any(v < 0 or v > 1 for v in box):
                        stats["outside_unit"] += 1
                    if box and (box[0] < 0 or box[1] < 0 or box[0] + box[2] > 1 or box[1] + box[3] > 1):
                        stats["overshoot"] += 1
                elif not geo:
                    stats["geom_none"] += 1
                    src = class_geometry(tree, class_home(tree, k["cls"], opkg, by_name), k["cls"],
                                         supers, by_name, _cache)
                    if src:
                        stats["geom_from_class"] += 1
                        note += ", geometry from " + src
                    else:
                        stats["geom_nowhere"] += 1
                else:
                    stats["geom_partial"] += 1
                rows.append(dict(kind="subcomponent", name=owner, package=opkg, form="class",
                                 target=k["name"], verdict=k["cls"], depth=str(level), platform="",
                                 note=note))
                home2 = class_home(tree, k["cls"], opkg, by_name)
                if level + 1 <= max_depth and (k["cls"], home2) not in seen \
                        and read_children(tree, home2, k["cls"]):
                    seen.add((k["cls"], home2))
                    stack.append((k["cls"], home2, level + 1))
    stats["child_classes"] = len(child_classes)
    base_home = class_home(tree, "GUIComponent", "GUI", by_name)
    base_blk, _ = read_default_block(tree, base_home, "GUIComponent")
    stats["base_keys"] = ",".join(k for k in WIN_KEYS if k in base_blk)
    for cls in sorted(pages_of):
        if cls not in {r["name"] for r in rows if r["form"] == "none"}:
            continue
        gap = layout_gaps({cls}).get(cls)
        if gap:
            stats["gap_joined"] += 1
            stats["gap_declared"] += int(gap.get("declared_properties") or 0)
            rows.append(dict(kind="layout_gap", name=cls, package=gap["package"], form="defaults",
                             target=gap.get("declared_properties", ""), verdict=gap.get("reason", ""),
                             depth="", platform="",
                             note="%s of its %s declared properties read before the block start is refused"
                                  % (gap.get("best_properties", "?"), gap.get("declared_properties", "?"))))
    return rows, stats


def _subobject_refs(path):
    """[(property, target class, target name, enclosing block class)] for the `Class'Name'` values in a file.

    A page's defaults bind its widgets twice: once by the `Begin Object ... Name=` block that declares them, and
    once by the property that points at the block (`MenuPanel=RightLockingMenuPanel'cPanel'`,
    `BuildStampLB=SGLabel'cBuildStamp'`). A binding whose `Name=` has no block is a widget the file does not
    declare -- in the UI tree that is the *panel* case: the button lives in the panel class's defaults, which
    the reader refuses, so the page can name it and nothing in the emitted tree places it.
    """
    names, refs = set(), []
    depth, stack = 0, []
    for line in open(path, encoding="latin-1", errors="replace"):
        m = BEGIN_COMP.match(line)
        if m:
            depth += 1
            c, n = COMP_CLASS.search(m.group("rest")), COMP_NAME.search(m.group("rest"))
            stack.append(c.group("cls") if c else "")
            if n:
                names.add(n.group("name"))
            continue
        if END_OBJECT.match(line):
            depth = max(0, depth - 1)
            if stack:
                stack.pop()
            continue
        t = TOP_PROP.match(line)
        if not t:
            continue
        v = REF_VALUE.match(t.group("val").strip())
        if v:
            refs.append((t.group("prop"), v.group("cls"), v.group("name"), stack[0] if stack else ""))
    return names, refs


def read_bindings(tree, family, sub_rows, by_name):
    """The widgets a page *names* without declaring anywhere in the emitted tree.

    A page's `MyComponents(0)=GUIButton'cContinueBN'` names a button through a property of the *panel* it placed,
    while the button itself is a subobject of the page. A row here is a binding that resolves to nothing even
    after the page's own blocks and the target class's blocks are both read -- a genuinely missing widget, not a
    panel whose contents were not emitted. Row `kind=binding` carries only the dangling ones; the count of all
    bindings is in the census, because a binding that resolves is already a `component` row.
    """
    refused = {r["name"] for r in sub_rows if r["form"] == "none"}
    rows, stats = [], collections.Counter()
    for cls, pkgname in sorted(family.items()):
        path = os.path.join(tree, pkgname, cls + ".defaultproperties.uc")
        if not os.path.isfile(path):
            continue
        names, refs = _subobject_refs(path)
        page_names = {c["name"] for c in read_children(tree, pkgname, cls)} | names
        for prop, target_cls, name, owner in refs:
            if prop in ("Class", "Package", "Outer"):
                continue
            stats["bindings"] += 1
            if name in page_names:
                stats["resolved"] += 1
                continue
            home = class_home(tree, target_cls, pkgname, by_name)
            if name in {c["name"] for c in read_children(tree, home, target_cls)}:
                stats["resolved"] += 1        # the target class declares it after all
                continue
            stats["dangling"] += 1
            note = "bound by %s" % prop
            if owner:
                note += " inside %s" % owner
                if owner in refused:
                    stats["in_refused_panel"] += 1
                    note += ", whose defaults block the reader refuses"
            else:
                note += " at the page's level"
            rows.append(dict(kind="binding", name=cls, package=pkgname, form="dangling", target=name,
                             verdict=target_cls, depth="", platform="", note=note))
    return rows, stats


def read_overrides(text, cls, pkgname):
    """Which of the four wiring functions a page replaces, and whether `InitComponent` forwards to `Super`.

    A page that replaces `InitComponent` without calling `Super.InitComponent` never runs the base's hint wiring,
    so a flag it sets would print nothing -- the census the flags need to mean anything. The handler rows name
    the `XC_` codes the page's *own* body compares, which is the other half of a hint: the caption says what the
    button is for, the handler says what it does.
    """
    rows = []
    for m in FUNC_DECL.finditer(text):
        name = m.group("name")
        if name not in WIRING_FUNCS:
            continue
        end = text.find("\n}", m.start())
        body = text[m.start():end if end > 0 else m.start() + 6000]
        if name == "InitComponent":
            sup = bool(re.search(r"\bSuper\s*\.\s*Init[Cc]omponent", body, re.I))
            verdict = "super" if sup else "no-super"
            note = "calls Super.InitComponent" if sup else "replaces the base wiring, never calls Super"
        else:
            codes = sorted(handler_codes(text))
            verdict = "own"
            note = "names %s" % ", ".join(codes) if codes else "names no XC_ code"
        rows.append(dict(kind="override", name=cls, package=pkgname, form="body", target=name, verdict=verdict,
                         platform="", note=note))
    return rows


def handler_codes(text):
    """The `XC_` codes a class's *own* `InternalOnXControllerEvent` body compares."""
    for m in FUNC_DECL.finditer(text):
        if m.group("name") != "InternalOnXControllerEvent":
            continue
        end = text.find("\n}", m.start())
        body = text[m.start():end if end > 0 else m.start() + 6000]
        return set(XC_CODE.findall(body))
    return set()


def handler_coverage(hint_rows, codes_by_class, supers, by_name):
    """For each hint flag a page shows: does any class on its chain handle that button code?

    The flag states the *caption*; the press is answered by the page's own handler, an inherited one, or the
    focused component's delegate -- so `no-handler` here means "no page class on the chain names the code", a
    fact about the class tree, not a claim that the button does nothing.
    """
    rows = []
    for r in hint_rows:
        pkgname, cls = r["package"], r["name"]
        code = "XC_" + r["target"][-1]
        who = [c for _p, c in super_path(pkgname, cls, supers, by_name) if code in codes_by_class.get(c, ())]
        rows.append(dict(kind="hint_handler", name=cls, package=pkgname, form="body", target=r["target"],
                         verdict="handled" if who else "no-handler", platform="",
                         note=("names " + ", ".join(who)) if who else ("names no %s on the chain" % code)))
    return rows


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

    # The page's *contents*: the four hint flags `SGPageBase.InitComponent` wires, the `Engine.Localizer` strings
    # they print, what each page's own defaults state, and which of the four wiring functions it replaces. All of
    # it is page-level and needs the defaults tree, so a `--defaults`-less run counts the flags as unread rather
    # than reporting zero hints.
    page_rows, page_stats = (read_page_defaults(defaults_tree, family, supers, by_name)
                             if defaults_tree else ([], collections.Counter()))
    rows.extend(page_rows)
    # The page's *layout*: the `Begin Object` blocks a page declares. Same tree, so a `--defaults`-less run
    # reports no components rather than zero of them.
    comp_rows, comp_stats = (read_components(defaults_tree, family, supers, by_name, per_pkg)
                             if defaults_tree else ([], collections.Counter()))
    rows.extend(comp_rows)
    # The page's *nested* layout: what each of those components declares inside itself, which is the panel
    # class's own defaults rather than the page's block.
    sub_rows, sub_stats = (read_subcomponents(defaults_tree, family, supers, by_name)
                           if defaults_tree else ([], collections.Counter()))
    rows.extend(sub_rows)
    # The bindings: a page can name a widget whose block the panel class declares, and when that class has no
    # emitted block the name is all that is left -- which is what the panels above cost.
    bind_rows, bind_stats = (read_bindings(defaults_tree, family, sub_rows, by_name)
                             if defaults_tree else ([], collections.Counter()))
    rows.extend(bind_rows)
    codes_by_class = {}
    for (pkgname, cls), text in sorted(text_of.items()):
        if family.get(cls) == pkgname:
            rows.extend(read_overrides(text, cls, pkgname))
            codes_by_class[cls] = handler_codes(text)
        elif cls == "SGPageBase":                # the base's handler is what every page inherits for Back
            codes_by_class[cls] = handler_codes(text)
    rows.extend(handler_coverage([r for r in page_rows if r["kind"] == "hint"], codes_by_class, supers, by_name))

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
        elif r["kind"] in ("hint", "caption", "draw", "override", "hint_handler", "component",
                           "subcomponent", "layout_gap", "binding"):
            verdict, tpkg, tcls, note = (r.get("verdict") or "", "", "", "")
        else:
            verdict, tpkg, tcls, note = "", "", "", ""
        r.update(target_package=tpkg, verdict=verdict, depth=r.get("depth") or "", sites=1,
                 note=(r.get("note") or note))
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

    over = [r for r in out if r["kind"] == "override"]
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
                      "defaults_tree=%d" % (1 if defaults_tree else 0),
                      "hint_pages=%d" % page_stats.get("hint_pages", 0),
                      "no_hint_pages=%d" % page_stats.get("no_hint_pages", 0),
                      "hints_on=%d" % page_stats.get("hints_on", 0),
                      "hint_own=%d" % page_stats.get("hint_own", 0),
                      "hint_inherited=%d" % page_stats.get("hint_inherited", 0),
                      "page_blocks=%d" % page_stats.get("page_blocks", 0),
                      "page_top_props=%d" % page_stats.get("page_top_props", 0),
                      "page_components=%d" % page_stats.get("page_components", 0),
                      "pages_with_components=%d" % page_stats.get("pages_with_components", 0),
                      "component_pages=%d" % comp_stats.get("pages", 0),
                      "component_own=%d" % comp_stats.get("own", 0),
                      "component_restated=%d" % comp_stats.get("restated", 0),
                      "component_classes=%d" % comp_stats.get("classes", 0),
                      "component_geom_full=%d" % comp_stats.get("geom_full", 0),
                      "component_geom_none=%d" % comp_stats.get("geom_none", 0),
                      "component_geom_from_class=%d" % comp_stats.get("geom_from_class", 0),
                      "component_geom_nowhere=%d" % comp_stats.get("geom_nowhere", 0),
                      "component_unresolved=%d" % comp_stats.get("unresolved", 0),
                      "sub_classes=%d" % sub_stats.get("classes", 0),
                      "sub_uses=%d" % sub_stats.get("uses", 0),
                      "sub_with_layout=%d" % sub_stats.get("with_layout", 0),
                      "sub_no_layout=%d" % sub_stats.get("no_layout", 0),
                      "sub_leaf=%d" % sub_stats.get("leaf", 0),
                      "sub_multi_use=%d" % sub_stats.get("multi_use", 0),
                      "sub_multi_place=%d" % sub_stats.get("multi_place", 0),
                      "sub_children=%d" % sub_stats.get("children", 0),
                      "sub_depth2=%d" % sub_stats.get("depth_2", 0),
                      "sub_depth3=%d" % sub_stats.get("depth_3", 0),
                      "sub_second_level=%d" % sub_stats.get("second_level", 0),
                      "sub_child_classes=%d" % sub_stats.get("child_classes", 0),
                      "sub_geom_full=%d" % sub_stats.get("geom_full", 0),
                      "sub_geom_none=%d" % sub_stats.get("geom_none", 0),
                      "sub_geom_partial=%d" % sub_stats.get("geom_partial", 0),
                      "sub_geom_from_class=%d" % sub_stats.get("geom_from_class", 0),
                      "sub_geom_nowhere=%d" % sub_stats.get("geom_nowhere", 0),
                      "sub_outside_unit=%d" % sub_stats.get("outside_unit", 0),
                      "sub_overshoot=%d" % sub_stats.get("overshoot", 0),
                      "sub_base_keys=%s" % (sub_stats.get("base_keys") or "-"),
                      "gap_classes=%d" % sub_stats.get("gap_joined", 0),
                      "binding_rows=%d" % bind_stats.get("bindings", 0),
                      "binding_resolved=%d" % bind_stats.get("resolved", 0),
                      "binding_dangling=%d" % bind_stats.get("dangling", 0),
                      "binding_in_refused=%d" % bind_stats.get("in_refused_panel", 0),
                      "gap_declared=%d" % sub_stats.get("gap_declared", 0),
                      "titles_own=%d" % page_stats.get("titles_own", 0),
                      "placeholder_titles=%d" % page_stats.get("placeholder_titles", 0),
                      "draw_rows=%d" % page_stats.get("draw_rows", 0),
                      "draw_pages=%d" % page_stats.get("draw_pages", 0),
                      "url_pages=%d" % page_stats.get("url_pages", 0),
                      "override_pages=%d" % len({r["name"] for r in over}),
                      "init_overrides=%d" % sum(1 for r in over if r["target"] == "InitComponent"),
                      "init_super=%d" % sum(1 for r in over if r["verdict"] == "super"),
                      "init_nosuper=%d" % sum(1 for r in over if r["verdict"] == "no-super"),
                      "handler_pages=%d" % sum(1 for r in over if r["target"] == "InternalOnXControllerEvent"),
                      "hint_handled=%d" % sum(1 for r in out if r["kind"] == "hint_handler"
                                              and r["verdict"] == "handled"),
                      "hint_no_handler=%d" % sum(1 for r in out if r["kind"] == "hint_handler"
                                                 and r["verdict"] == "no-handler"),
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


def components_block(s, rows):
    """The generated half of the layout census: the count table, each page's widgets, and the restatements."""
    comps = [r for r in rows if r["kind"] == "component"]
    out = ["### The widgets a page declares", ""]
    if not comps:
        out.append("No `--defaults` tree was read, so the `Begin Object` blocks are uncounted in this run.")
        out.extend(subcomponents_block(s, rows))
        return out
    out.append("A page's `Begin Object` blocks are its layout: each block names a widget class and the property "
               "the page binds it to, and the four `Win*` values a block states are that widget's place on the "
               "page. The base's own five (`cTitle`, `cButtonA`..`cButtonY`) are declared once, so a page draws "
               "its inherited frame plus whatever it adds — and a subclass can replace an inherited widget "
               "outright by restating its `Name=` with a different class.")
    out.append("")
    out.append("| measure | answer |")
    out.append("|---|---|")
    out.append("| `Begin Object` component blocks | **%s** over %s pages |" %
               (s.get("page_components", "0"), s.get("pages_with_components", "0")))
    out.append("| ... declared once, by the page itself | **%s** |" % s.get("component_own", "0"))
    out.append("| ... restating a widget an ancestor already declares | **%s** |" %
               s.get("component_restated", "0"))
    out.append("| distinct widget classes | **%s** |" % s.get("component_classes", "0"))
    out.append("| ... classes the corpus does not carry | **%s** |" % s.get("component_unresolved", "0"))
    out.append("| ... stating all four `Win*` values on the page | **%s** |" % s.get("component_geom_full", "0"))
    out.append("| ... stating none | **%s**, of which **%s** take theirs from the widget class's own defaults and "
               "**%s** state none anywhere |" % (s.get("component_geom_none", "0"),
                                                 s.get("component_geom_from_class", "0"),
                                                 s.get("component_geom_nowhere", "0")))
    per = collections.OrderedDict()
    for r in sorted(comps, key=lambda r: (r["package"], r["name"], r["target"])):
        per.setdefault((r["package"], r["name"]), []).append(r)
    out.append("")
    out.append("| page | widgets | with geometry | own | restated |")
    out.append("|---|---|---:|---:|---:|")
    for (pkg, page), got in per.items():
        cells = ", ".join("`%s:%s`" % (r["verdict"], r["target"]) for r in got)
        full = sum(1 for r in got if "geometry=4/4" in r["note"])
        own = sum(1 for r in got if r["form"] == "own")
        out.append("| `%s.%s` | %s | %d | %d | %d |" % (pkg, page, cells, full, own, len(got) - own))
    restated = [r for r in comps if r["form"] == "restated"]
    if restated:
        out.append("")
        out.append("| page | widget | class | ancestor | its class |")
        out.append("|---|---|---|---|---|")
        for r in sorted(restated, key=lambda r: (r["package"], r["name"], r["target"])):
            m = re.search(r"restates (?P<where>\S+) whose class is (?P<cls>\S+)", r["note"])
            out.append("| `%s.%s` | `%s` | `%s` | `%s` | `%s` |" %
                       (r["package"], r["name"], r["target"], r["verdict"],
                        m.group("where") if m else "?", m.group("cls") if m else "?"))
    out.extend(subcomponents_block(s, rows))
    return out


def subcomponents_block(s, rows):
    """The nested half of the layout census: what each component's *own class* declares inside itself.

    A page's block places a panel; the panel's class states what is inside it. The two reads are separate files
    in the same tree, so the second one is the panel classes' own defaults -- and a class the decode refuses has
    no file at all, which is a gap in the reader rather than a panel the script builds.
    """
    subs = [r for r in rows if r["kind"] == "subcomponent"]
    gaps = [r for r in rows if r["kind"] == "layout_gap"]
    out = ["", "### What a panel declares inside itself", ""]
    if not subs:
        out.append("No `--defaults` tree was read, so the panels' own contents are uncounted in this run.")
        return out
    out.append("A page's block places a panel; what the panel *contains* is its own class's defaults, in a "
               "second file of the same tree. So the layout is a tree and not a list: %s of the %s widget "
               "classes a page names declare blocks of their own, %s are widgets whose block declares no "
               "children, and a child class can declare blocks too (`GUIVertScrollBar` states its zone and "
               "two buttons, which is the third level)." %
               (s.get("sub_with_layout", "0"), s.get("sub_classes", "0"), s.get("sub_leaf", "0")))
    out.append("")
    out.append("| measure | answer |")
    out.append("|---|---|")
    out.append("| widget classes a page names | **%s**, used by **%s** component blocks |" %
               (s.get("sub_classes", "0"), s.get("sub_uses", "0")))
    out.append("| ... declaring their own children | **%s** |" % s.get("sub_with_layout", "0"))
    out.append("| ... widgets, whose block declares no children | **%s** |" % s.get("sub_leaf", "0"))
    out.append("| ... with no emitted block at all | **%s** |" % s.get("sub_no_layout", "0"))
    out.append("| nested children | **%s** over **%s** classes (depth 2 **%s**, depth 3 **%s**) |" %
               (s.get("sub_children", "0"), s.get("sub_child_classes", "0"), s.get("sub_depth2", "0"),
                s.get("sub_depth3", "0")))
    out.append("| ... stating all four `Win*` values | **%s** |" % s.get("sub_geom_full", "0"))
    out.append("| ... stating none | **%s**, %s resolved on the child's own class chain |" %
               (s.get("sub_geom_none", "0"),
                "all" if s.get("sub_geom_none") == s.get("sub_geom_from_class") else
                s.get("sub_geom_from_class", "0")))
    out.append("| ... a rectangle that runs past the unit square | **%s** of the %s that state one |" %
               (s.get("sub_overshoot", "0"), s.get("sub_geom_full", "0")))
    out.append("")
    by_owner = collections.OrderedDict()
    for r in sorted(subs, key=lambda r: (r["package"], r["name"], r["depth"], r["target"])):
        by_owner.setdefault((r["package"], r["name"]), []).append(r)
    out.append("| panel class | package | children | child classes | stating a rectangle |")
    out.append("|---|---|---:|---:|---:|")
    for (pkg, cls), got in by_owner.items():
        kids = [r for r in got if r["form"] == "class"]
        if not kids:
            continue
        full = sum(1 for r in kids if "geometry=4/4" in r["note"])
        out.append("| `%s` | %s | %d | %d | %d |" %
                   (cls, pkg, len(kids), len({r["verdict"] for r in kids}), full))
    leaves = sorted({(r["package"], r["name"]) for r in subs if r["form"] == "leaf"})
    if leaves:
        out.append("")
        out.append("The %d classes that declare no children are widgets, not empty panels: %s. Their block "
                   "states properties (`Caption`, `StyleName`, the rectangle), never a child." %
                   (len(leaves), ", ".join("`%s`" % n for p, n in leaves)))
    if gaps:
        out.append("")
        out.append("The panels the tree does not carry are **not** classes whose block the reader refuses: each "
                   "block is one tag, sitting exactly where its record's closing group ends and ending on the "
                   "payload's last byte, and that tag names a child object the panel's own class owns "
                   "(`ChapterList'chList'`, `GUILabel'cHint'`, `SGListBox'cListBox'`, ...). The emitter writes no "
                   "file for them because its class-level locator ranks a three-tag parse *inside* the record's "
                   "closing group above the block at the group's end — the same ranking the misses ledger used to "
                   "call these `type-contradiction`. So what is missing is not the panel's contents but the "
                   "file's one line, and every child a panel owns is itself an export with a readable block.")
        out.append("")
        out.append("| widget class | package | declared properties | reader verdict | its block's tag |")
        out.append("|---|---|---:|---|---|")
        for r in sorted(gaps, key=lambda r: (r["package"], r["name"])):
            prop = r.get("property") or ""
            out.append("| `%s` | %s | %s | `%s` | %s |" %
                       (r["name"], r["package"], r["target"], r["verdict"],
                        ("`%s`" % prop) if prop else "none (empty block)"))
        dangle = [r for r in rows if r["kind"] == "binding"]
        if dangle:
            out.append("")
            out.append("**%s of the %s bindings name a widget no block declares.** A page binds each widget "
                       "twice: the `Begin Object ... Name=` block places it and a property points at it "
                       "(`MenuPanel=RightLockingMenuPanel'cPanel'`). Where the block lives in a panel class the "
                       "reader refuses, the pointer resolves to a name and nothing else -- so the rows below are "
                       "controls that exist (their class is stated) and cannot be placed (their rectangle is in "
                       "the refused block). %s of them sit inside a refused panel."
                       % (s.get("binding_dangling", "0"), s.get("binding_rows", "0"),
                          s.get("binding_in_refused", "0")))
            out.append("")
            out.append("| page | widget | class | bound by | enclosing block |")
            out.append("|---|---|---|---|---|")
            for r in sorted(dangle, key=lambda r: (r["package"], r["name"], r["target"])):
                m = re.search(r"bound by (\w+)(?: inside ([\w.]+))?", r["note"])
                out.append("| `%s.%s` | `%s` | `%s` | `%s` | `%s` |" %
                           (r["package"], r["name"], r["target"], r["verdict"],
                            m.group(1) if m else "?", m.group(2) if m and m.group(2) else "-"))
        else:
            out.append("")
            out.append("**All %s of the page-level bindings resolve.** A page binds each widget twice: the "
                       "`Begin Object ... Name=` block places it, and a property points at it "
                       "(`MenuPanel=RightLockingMenuPanel'cPanel'`, `MyComponents(0)=GUIButton'cContinueBN'`). "
                       "The second form points *into* a panel's block, but the object it names is a subobject "
                       "of the **page** (its Outer is the page class, and its rectangle is in its own block) — "
                       "so a reader that stops at the panel's contents loses those controls, which is what a "
                       "dangling binding was. Following one more level places all %s of them: %s resolved, %s "
                       "dangling."
                       % (s.get("binding_rows", "0"), s.get("binding_rows", "0"),
                          s.get("binding_resolved", "0"), s.get("binding_dangling", "0")))
        out.append("")
        keys = [k for k in s.get("sub_base_keys", "-").split(",") if k.startswith("Win")]
        out.append("**What the `Win*` numbers are relative to.** The base `GUIComponent` states %s of the four "
                   "(%s) and a panel class states none, so every panel *is* its container: the two values the "
                   "base does state make its rectangle its parent's whole box. Two measurements then say the "
                   "numbers are read against that box and not against the screen. First, %s of the %s children "
                   "that state a rectangle run past the unit square on one axis — nothing clamps them, so the "
                   "range is not a screen fraction the engine keeps inside the display. Second, %s widget "
                   "classes are used by more than one page and %s of those are placed at more than one page-side "
                   "rectangle, while the children's numbers are one set — a class has one defaults "
                   "block, so a child's `Win*` cannot be a screen position, only an offset inside whichever "
                   "instance box the parent draws. The screen position is the composed product down the tree, "
                   "which is what `GUIComponent`'s own native body computes and what `docs/UNKNOWNS.md` U27 "
                   "still wants decoded."
                   % ("**%d**" % len(keys) if keys else "none", ", ".join("`%s`" % k for k in keys) or "none",
                      s.get("sub_overshoot", "0"), s.get("sub_geom_full", "0"),
                      s.get("sub_multi_use", "0"), s.get("sub_multi_place", "0")))
    return out


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

    hints = [r for r in rows if r["kind"] == "hint"]
    captions = [r for r in rows if r["kind"] == "caption"]
    draw = [r for r in rows if r["kind"] == "draw"]
    overrides = [r for r in rows if r["kind"] == "override"]
    if s.get("defaults_tree") == "1":
        out.append("")
        out.append("### The controller hints each page shows")
        out.append("")
        by_page = collections.defaultdict(list)
        for r in hints:
            by_page[r["name"]].append(r)
        out.append("A page's hints are its `bButtonA`…`bButtonY` defaults, and %s of the %d pages set at least one: "
                   "**%s hint rows**, %s declared by the page itself and %s inherited. The other **%s pages set none** "
                   "and therefore draw no controller caption at all." %
                   (s["hint_pages"], total_pages, s["hints_on"], s["hint_own"], s["hint_inherited"],
                    s["no_hint_pages"]))
        inherited_from = collections.Counter(r["note"].split("inherited from ")[-1] for r in hints
                                             if r["form"] == "inherited" and "inherited from " in r["note"])
        if inherited_from:
            out.append("")
            out.append("The inherited ones come from %s." % "; ".join(
                "`%s` (%d flag%s)" % (k, v, "" if v == 1 else "s") for k, v in sorted(inherited_from.items())))
        out.append("")
        out.append("| hints | pages | which |")
        out.append("|---|---:|---|")
        for hs, n in sorted(collections.Counter(tuple(sorted(r["target"] for r in v))
                                               for v in by_page.values()).items(), key=lambda kv: (-kv[1], kv[0])):
            who = sorted({v[0]["package"] + "." + v[0]["name"] for v in by_page.values()
                          if tuple(sorted(r["target"] for r in v)) == hs})
            out.append("| %s | %d | %s |" % (" ".join(h[-1] for h in hs), n, ", ".join("`%s`" % w for w in who)))
        out.append("")
        out.append("| flag | caption | from |")
        out.append("|---|---|---|")
        order = {f: i for i, (f, _k) in enumerate(HINT_FLAGS)}
        for r in sorted(captions, key=lambda r: order.get(dict((k, f) for f, k in HINT_FLAGS).get(r["name"], ""), 9)):
            flag = dict((k, f) for f, k in HINT_FLAGS).get(r["name"], "")
            out.append("| `%s` | **%s** | `%s.%s` |" % (flag, r["target"], r["package"], r["note"]))
        out.append("")
        out.append("| page | hints | declared |")
        out.append("|---|---|---|")
        for name in sorted(by_page):
            rs = sorted(by_page[name], key=lambda r: r["target"])
            got = " ".join("%s=%s" % (r["target"][-1], r["note"].split(" via ")[0]) for r in rs)
            src = "own" if all(r["form"] == "own" for r in rs) else \
                ", ".join(sorted({r["note"].split("inherited from ")[-1] for r in rs if r["form"] == "inherited"}))
            out.append("| `%s.%s` | %s | %s |" % (rs[0]["package"], name, got, src))
        no_hint = sorted(p["name"] for p in pages if p["name"] not in by_page)
        out.append("")
        out.append("%d pages show no controller hint: %s." % (len(no_hint), ", ".join("`%s`" % n for n in no_hint)))
        verdict_of = {p["name"]: p["verdict"] for p in pages}
        withhint = collections.Counter(verdict_of.get(n, "?") for n in by_page)
        out.append("")
        out.append("By page verdict, the %d hinting pages are %s and the %d silent ones break down as %s." %
                   (len(by_page), ", ".join("%s %d" % (k, v) for k, v in sorted(withhint.items())),
                    len(no_hint), ", ".join("%s %d" % (k, v) for k, v in
                                            sorted(collections.Counter(verdict_of[n] for n in no_hint).items()))))
        out.append("")
        out.append("### What a page's own defaults state")
        out.append("")
        out.append("| measure | answer |")
        out.append("|---|---|")
        out.append("| pages with a defaults block of their own | **%s** of %d (%d state nothing) |" %
                   (s["page_blocks"], total_pages, total_pages - int(s["page_blocks"])))
        out.append("| top-level properties over those pages | **%s** |" % s["page_top_props"])
        out.append("| ... `TitleString`, the page's caption | **%s**, and **%s** pages keep `SGPageBase`'s "
                   "placeholder |" % (s["titles_own"], s["placeholder_titles"]))
        out.append("| `Begin Object` component blocks | **%s** over %s pages |" %
                   (s["page_components"], s["pages_with_components"]))
        out.append("| properties that draw something (`StyleName`, `Background`, `BackgroundStyle`, the four `Win*` "
                   "geometry values) | **%s** over %s pages |" % (s["draw_rows"], s["draw_pages"]))
        if s.get("url_pages", "0") != "0":
            out.append("| `URL`, a page whose default is a map | **%s** page |" % s["url_pages"])
        if draw:
            out.append("")
            out.append("| page | what it states |")
            out.append("|---|---|")
            for r in sorted(draw, key=lambda r: r["name"]):
                out.append("| `%s.%s` | %s |" % (r["package"], r["name"],
                                                 ", ".join("`%s`" % p for p in r["target"].split(","))))
            out.append("")
            out.extend(components_block(s, rows))
            out.append("")
            out.append("### The wiring, and the pages that replace it")
            out.append("")
        else:
            out.append("")
            out.extend(components_block(s, rows))
            out.append("")
            out.append("### The wiring, and the pages that replace it")
            out.append("")
        out.append("| measure | answer |")
        out.append("|---|---|")
        out.append("| pages replacing one of the four wiring functions | **%s** of %d |" %
                   (s["override_pages"], total_pages))
        out.append("| ... `InitComponent` | **%s**, of which **%s** call `Super.InitComponent` |" %
                   (s["init_overrides"], s["init_super"]))
        out.append("| ... replacing it without the `Super` call | **%s** |" % s["init_nosuper"])
        out.append("| ... `InternalOnXControllerEvent` | **%s** pages handle the controller themselves |" %
                   s["handler_pages"])
        inits = [r for r in overrides if r["target"] == "InitComponent"]
        handlers = [r for r in overrides if r["target"] != "InitComponent"]
        if inits:
            out.append("")
            out.append("The `InitComponent` overrides: %s." %
                       ", ".join("`%s`" % r["name"] for r in sorted(inits, key=lambda r: r["name"])))
        if handlers:
            out.append("")
            out.append("| page | replaces | codes its own body names |")
            out.append("|---|---|---|")
            for r in sorted(handlers, key=lambda r: (r["target"], r["name"])):
                out.append("| `%s.%s` | `%s` | %s |" % (r["package"], r["name"], r["target"],
                                                        r["note"].replace("names ", "")))
        out.append("")
        if s["init_nosuper"] == "0":
            out.append("Every one of the %s `InitComponent` overrides forwards to `Super`, so the four captions are "
                       "wired on every page that declares a flag — the flags are a real caption census, not a "
                       "declaration the base never reads." % s["init_overrides"])
        else:
            out.append("**%s** of the %s `InitComponent` overrides never call `Super.InitComponent`, so those "
                       "pages' flags never reach `SetVisibility` — read their hint rows as declarations the page "
                       "made and did not wire." % (s["init_nosuper"], s["init_overrides"]))
        out.append("")
        out.append("On the handler side the base's own body answers `XC_B`/`XC_BACK` with `CloseMenu(true,true)`, "
                   "which is why Back is the one hint a page can trust without a handler of its own. For the other "
                   "three the flag states the caption only: **%s of the %s hint flags** have a class on the page's "
                   "chain whose handler names the code, and the other **%s** name none — that is where a press "
                   "falls to the focused component's delegate, or to nothing the class tree states. The rows below "
                   "are the page's own body, so a page that inherits `XC_A` from a mid class counts for that class."
                   % (s["hint_handled"], s["hints_on"], s["hint_no_handler"]))
        covered = [r for r in rows if r["kind"] == "hint_handler" and r["verdict"] == "no-handler"]
        if covered:
            out.append("")
            out.append("| page | flag with no handler on its chain |")
            out.append("|---|---|")
            for r in sorted(covered, key=lambda r: (r["target"], r["name"])):
                out.append("| `%s.%s` | `%s` |" % (r["package"], r["name"], r["target"]))

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
    if s.get("defaults_tree") == "1":
        print("pages: %(hint_pages)s show a controller hint (%(hints_on)s flags, %(hint_inherited)s inherited), "
              "%(no_hint_pages)s show none; %(init_overrides)s replace InitComponent (%(init_nosuper)s without "
              "Super), %(handler_pages)s handle the controller" % s)
        print("layout: %(page_components)s Begin Object blocks over %(pages_with_components)s pages, "
              "%(component_own)s own and %(component_restated)s restated, %(component_classes)s widget classes, "
              "%(component_geom_full)s with all four Win* values and %(component_geom_none)s with none "
              "(%(component_geom_from_class)s from the widget class)" % s)


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
                  "unopened_pages=0|defaults_classes=1|hud_assign=1|hud_swaps=0|defaults_tree=1|hint_pages=1|"
                  "no_hint_pages=1|hints_on=2|hint_own=1|hint_inherited=1|page_blocks=2|page_top_props=5|"
                  "page_components=1|pages_with_components=1|titles_own=1|placeholder_titles=1|draw_rows=1|"
                  "draw_pages=1|override_pages=1|init_overrides=1|init_super=1|init_nosuper=0|handler_pages=0|"
                  "url_pages=0|hint_handled=1|hint_no_handler=1"),
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
    assert "The controller hints each page shows" in text, text

    # --- the page's contents: a child's properties belong to the component, not the page
    with tempfile.TemporaryDirectory() as td:
        os.makedirs(os.path.join(td, "StargateMenus"))
        os.makedirs(os.path.join(td, "Engine"))
        with open(os.path.join(td, "Engine", "Localizer.defaultproperties.uc"), "w", encoding="latin-1") as fh:
            fh.write('defaultproperties\n{\n\tsSelect=Select\n\tsBack=Back\n\tsDelete=Delete\n'
                     '\tsCreateNew="Create New"\n}\n')
        with open(os.path.join(td, "StargateMenus", "BasePage.defaultproperties.uc"), "w", encoding="latin-1") as fh:
            fh.write('defaultproperties\n{\n\tbButtonA=True\n\tbButtonB=True\n\tbButtonX=True\n'
                     '\tTitleString="Base"\n}\n')
        with open(os.path.join(td, "StargateMenus", "MidPage.defaultproperties.uc"), "w", encoding="latin-1") as fh:
            fh.write('defaultproperties\n{\n\tbButtonB=True\n\tbButtonX=False\n}\n')
        with open(os.path.join(td, "StargateMenus", "KidPage.defaultproperties.uc"), "w", encoding="latin-1") as fh:
            fh.write('defaultproperties\n{\n\tbButtonY=True\n\ttitlebar=SGLabel\'cTitle\'\n'
                     'Begin Object Class=ControllerButton Name=ButtonA\n\tbButtonA=False\n\tWinTop=0.1\n'
                     'End Object\n\tStyleName=MainMenu\n}\n')
        supers = {"BasePage": "SGPageBase", "MidPage": "BasePage", "KidPage": "MidPage"}
        by_pkg = {"BasePage": ["StargateMenus"], "MidPage": ["StargateMenus"], "KidPage": ["StargateMenus"],
                  "SGPageBase": ["StargateMenus"]}
        fam = {"BasePage": "StargateMenus", "MidPage": "StargateMenus", "KidPage": "StargateMenus"}
        rows3, stats3 = read_page_defaults(td, fam, supers, by_pkg)
    flags_of = collections.defaultdict(dict)
    for r in rows3:
        if r["kind"] == "hint":
            flags_of[r["name"]][r["target"]] = r
    assert set(flags_of["BasePage"]) == {"bButtonA", "bButtonB", "bButtonX"}, flags_of["BasePage"]
    # MidPage turns X off, so it is absent; A is inherited from BasePage and B is the page's own
    assert set(flags_of["MidPage"]) == {"bButtonA", "bButtonB"}, flags_of["MidPage"]
    assert flags_of["MidPage"]["bButtonA"]["form"] == "inherited", flags_of["MidPage"]
    # KidPage's *child* block defaults bButtonA False; the page's own value is the inherited True
    assert flags_of["KidPage"]["bButtonA"]["form"] == "inherited", flags_of["KidPage"]
    assert flags_of["KidPage"]["bButtonY"]["form"] == "own", flags_of["KidPage"]
    # and the child's WinTop is not one of the page's visual rows -- only its StyleName is
    draw3 = [r for r in rows3 if r["kind"] == "draw"]
    assert [r["target"] for r in draw3] == ["StyleName"], draw3
    assert stats3["draw_rows"] == 1 and stats3["page_components"] == 1, stats3
    assert stats3["hint_pages"] == 3 and stats3["no_hint_pages"] == 0, stats3
    caps = {r["name"]: r["target"] for r in rows3 if r["kind"] == "caption"}
    assert caps == {"sSelect": "Select", "sBack": "Back", "sDelete": "Delete", "sCreateNew": "Create New"}, caps
    body = ("class KidPage extends MidPage;\n"
            "function InitComponent(GUIController MyController, GUIComponent MyOwner)\n{\n"
            "\tSuper.InitComponent(MyController, MyOwner);\n\tButtonA.SetVisibility(bButtonA);\n}\n"
            "function bool InternalOnXControllerEvent(Byte ID, eXControllerCodes Code)\n{\n"
            "\tif (Code == XC_X) return true;\n\treturn false;\n}\n")
    ors = read_overrides(body, "KidPage", "StargateMenus")
    assert {r["target"]: r["verdict"] for r in ors} == {"InitComponent": "super",
                                                       "InternalOnXControllerEvent": "own"}, ors
    assert "XC_X" in [r for r in ors if r["target"] == "InternalOnXControllerEvent"][0]["note"], ors
    nosup = read_overrides("function InitComponent(GUIController c, GUIComponent o)\n{\n\treturn;\n}\n",
                           "KidPage", "StargateMenus")
    assert [r["verdict"] for r in nosup] == ["no-super"], nosup
    # the coverage join: KidPage's own XC_A handles Select, the base's XC_B handles Back, nobody handles Create New
    cov = {(r["name"], r["target"]): r["verdict"] for r in
           handler_coverage([r for r in rows3 if r["kind"] == "hint"],
                            {"KidPage": {"XC_A"}, "SGPageBase": {"XC_B", "XC_BACK"}, "BasePage": set()},
                            supers, by_pkg)}
    assert cov[("KidPage", "bButtonA")] == "handled", cov
    assert cov[("KidPage", "bButtonB")] == "handled", cov           # inherited from SGPageBase's handler
    assert cov[("KidPage", "bButtonY")] == "no-handler", cov
    # --- a page's widgets: the block's class and bound name, a restatement, and geometry the widget class supplies
    with tempfile.TemporaryDirectory() as td:
        for pkg in ("StargateMenus", "GUI"):
            os.makedirs(os.path.join(td, pkg))
        with open(os.path.join(td, "GUI", "GUIButton.defaultproperties.uc"), "w", encoding="latin-1") as fh:
            fh.write("defaultproperties\n{\n\tWinTop=0.100000\n\tWinLeft=0.200000\n\tWinWidth=0.300000\n"
                     "\tWinHeight=0.400000\n}\n")
        with open(os.path.join(td, "StargateMenus", "MidPage.defaultproperties.uc"), "w", encoding="latin-1") as fh:
            fh.write("defaultproperties\n{\n\tBegin Object Class=GUIButton Name=ButtonA\n\t\tWinTop=0.5\n"
                     "\t\tWinLeft=0.5\n\t\tWinWidth=0.1\n\t\tWinHeight=0.1\n\tEnd Object\n}\n")
        with open(os.path.join(td, "StargateMenus", "KidPage.defaultproperties.uc"), "w", encoding="latin-1") as fh:
            fh.write("defaultproperties\n{\n\tBegin Object Class=GUIButton Name=ButtonA\n\t\tCaption=\"Go\"\n"
                     "\tEnd Object\n\tBegin Object Class=GUIButton Name=ButtonB\n\t\tWinTop=0.9\n\tEnd Object\n"
                     "\tBegin Object Class=GUILabel Name=cTitle\n\tEnd Object\n}\n")
        per_pkg2 = {"StargateMenus": {"MidPage": {}, "KidPage": {}, "GUIButton": {}, "GUILabel": {}},
                    "GUI": {"GUIButton": {}, "GUILabel": {}}}
        by_pkg2 = {"GUIButton": ["GUI"], "GUILabel": ["GUI"], "MidPage": ["StargateMenus"],
                   "KidPage": ["StargateMenus"], "SGPageBase": ["StargateMenus"]}
        supers2 = {"MidPage": "SGPageBase", "KidPage": "MidPage", "SGPageBase": "None"}
        comp_rows, comp_stats = read_components(td, {"MidPage": "StargateMenus", "KidPage": "StargateMenus"},
                                                supers2, by_pkg2, per_pkg2)
        kids = read_children(td, "StargateMenus", "KidPage")
    assert [c["name"] for c in kids] == ["ButtonA", "ButtonB", "cTitle"], kids
    assert [c["cls"] for c in kids] == ["GUIButton", "GUIButton", "GUILabel"], kids
    assert len(comp_rows) == 4 and comp_stats["components"] == 4, (comp_rows, comp_stats)
    assert comp_stats["restated"] == 1 and comp_stats["own"] == 3, comp_stats
    assert comp_stats["classes"] == 2 and comp_stats["unresolved"] == 0, comp_stats
    # MidPage's ButtonA states all four values; KidPage restates it with none, which `GUIButton` supplies;
    # cTitle's `GUILabel` has no emitted block, so that one states nothing anywhere.
    assert comp_stats["geom_full"] == 1 and comp_stats["geom_none"] == 2, comp_stats
    assert comp_stats["geom_from_class"] == 1 and comp_stats["geom_nowhere"] == 1, comp_stats
    restated = [r for r in comp_rows if r["form"] == "restated"]
    assert restated[0]["name"] == "KidPage" and restated[0]["target"] == "ButtonA", restated
    assert "restates StargateMenus.MidPage whose class is GUIButton" in restated[0]["note"], restated[0]
    assert "geometry from GUI.GUIButton" in restated[0]["note"], restated[0]
    # --- the nested read: a component's own class declares what is inside it, one level deeper than the page
    with tempfile.TemporaryDirectory() as td:
        for pkg in ("StargateMenus", "GUI"):
            os.makedirs(os.path.join(td, pkg))
        with open(os.path.join(td, "GUI", "GUIVertScrollBar.defaultproperties.uc"), "w", encoding="latin-1") as fh:
            fh.write("defaultproperties\n{\n\tWinWidth=16.0\n\tBegin Object Class=GUIVertScrollZone Name=ScrollZone\n"
                     "\tEnd Object\n\tBegin Object Class=GUIVertScrollButton Name=UpBut\n\tEnd Object\n}\n")
        with open(os.path.join(td, "StargateMenus", "MapPanel.defaultproperties.uc"), "w", encoding="latin-1") as fh:
            fh.write("defaultproperties\n{\n\tBegin Object Class=GUIEditBox Name=cFilter\n\t\tWinTop=0.9\n"
                     "\t\tWinLeft=0.2\n\t\tWinWidth=0.5\n\t\tWinHeight=0.1\n\tEnd Object\n"
                     "\tBegin Object Class=GUIVertScrollBar Name=cBar\n\tEnd Object\n}\n")
        with open(os.path.join(td, "StargateMenus", "KidPage.defaultproperties.uc"), "w", encoding="latin-1") as fh:
            fh.write("defaultproperties\n{\n\tBegin Object Class=MapPanel Name=cPN\n\tEnd Object\n"
                     "\tBegin Object Class=ChapterPanel Name=cCh\n\tEnd Object\n}\n")
        supers3 = {"MapPanel": "GUIPanel", "ChapterPanel": "GUIPanel", "KidPage": "SGPageBase"}
        by3 = {"GUIVertScrollBar": ["GUI"], "MapPanel": ["StargateMenus"], "ChapterPanel": ["StargateMenus"],
               "KidPage": ["StargateMenus"]}
        sub_rows, sub_stats = read_subcomponents(td, {"KidPage": "StargateMenus"}, supers3, by3)
        sub_text = render(fake[:1] + fake[1:2] + [dict(kind=r["kind"], name=r["name"], package=r["package"],
                                                       form=r["form"], target=r["target"], verdict=r["verdict"],
                                                       depth=r.get("depth", ""), note=r["note"],
                                                       target_package="", sites="", platform="")
                                                   for r in sub_rows])
    assert sub_stats["classes"] == 2 and sub_stats["uses"] == 2, sub_stats
    assert sub_stats["with_layout"] == 1 and sub_stats["no_layout"] == 1, sub_stats
    assert sub_stats["depth_2"] == 2 and sub_stats["depth_3"] == 2, sub_stats
    assert sub_stats["second_level"] == 1 and sub_stats["child_classes"] == 4, sub_stats
    assert sub_stats["geom_full"] == 1 and sub_stats["geom_from_class"] == 1, sub_stats
    assert sub_stats["overshoot"] == 1, sub_stats          # the synthetic rectangle ends at 1.0 and one past it
    assert sorted((r["name"], r["target"], r["depth"]) for r in sub_rows if r["form"] == "class") == \
        [("GUIVertScrollBar", "ScrollZone", "3"), ("GUIVertScrollBar", "UpBut", "3"),
         ("MapPanel", "cBar", "2"), ("MapPanel", "cFilter", "2")], sub_rows
    assert [r["name"] for r in sub_rows if r["form"] == "none"] == ["ChapterPanel"], sub_rows
    assert "geometry from GUI.GUIVertScrollBar" in [r["note"] for r in sub_rows
                                                    if r["target"] == "cBar"][0], sub_rows
    assert "What a panel declares inside itself" in sub_text, sub_text
    # a page that binds a widget its panel class does not declare: the binding is dangling, and it is inside a
    # refused panel, so the row says which class the widget is and which property points at it.
    with tempfile.TemporaryDirectory() as td:
        os.makedirs(os.path.join(td, "StargateMenus"))
        with open(os.path.join(td, "StargateMenus", "KidPage.defaultproperties.uc"), "w", encoding="latin-1") as fh:
            fh.write("defaultproperties\n{\n\tBegin Object Class=ChapterPanel Name=cCh\n"
                     "\t\tMyComponents(0)=GUIButton'cGoBN'\n\tEnd Object\n"
                     "\tMenuPanel=ChapterPanel'cCh'\n}\n")
        b_rows, b_stats = read_bindings(td, {"KidPage": "StargateMenus"},
                                       [dict(name="ChapterPanel", form="none")], {})
    assert b_stats["bindings"] == 2 and b_stats["resolved"] == 1 and b_stats["dangling"] == 1, b_stats
    assert b_stats["in_refused_panel"] == 1, b_stats
    assert b_rows[0]["target"] == "cGoBN" and b_rows[0]["verdict"] == "GUIButton", b_rows
    assert "bound by MyComponents inside ChapterPanel" in b_rows[0]["note"], b_rows
    assert "The widgets a page declares" in render(fake), "the layout section renders without rows"
    print("ok   menu-flow: 67 assertions over the parsers, the resolver, the closure, the three name readers, "
          "the page-level defaults, the component blocks, the panels' own nested layout and the dangling bindings")



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
    if args.sweep and not args.defaults:
        sys.exit("FAIL menu-flow: --sweep needs --defaults <the defaults-emit tree>; the page-level half reads it")
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
