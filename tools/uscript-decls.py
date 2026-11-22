#!/usr/bin/env python3
"""Parse the shipped source's declarations and score them against the packages (PLAN 3.6, front end stage 2a).

Stage 2 is a parser, and its first half is declarations: a class header, its `var`s, `function`s, `state`s, `struct`s,
`enum`s and `const`s. Those are exactly the things `inventory/classes.csv` already counts from the package export
tables, so the parser can be scored instead of trusted -- per class, six numbers and a super name, each side measured
independently.

    python3 tools/uscript-decls.py --tree $WORK/scripts-all -o inventory
    python3 tools/uscript-decls.py --tree $WORK/scripts-all --residue --dump DUMP/System -o inventory
    python3 tools/uscript-decls.py --check        # synthetic corpus, no tree needed

The read is **name level**, not count level: `parse()` returns the declared *names* per kind, the counts are their
lengths, and `--residue` joins the names against the package's own export names. A count comparison cannot see a
rename -- `var HaakenPodDoor Doors[NUM_DOORS]` read as `Num_Doors` has the same count as `Doors` -- so the name join
is what turns a residue into a cause.

`inventory/uscript_decls.csv` is the per-file table and `inventory/uscript_decls_residue.csv` the per-name residue;
the source tree is game-derived and stays under `$WORK`.
"""

import argparse
import csv
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

import importlib.util

_spec = importlib.util.spec_from_file_location("uscript_lex", os.path.join(ROOT, "tools", "uscript-lex.py"))
lexer = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(lexer)

SKIP = {"space", "comment", "macro", "high"}

# The same build guard `tools/text-branches.py` measured: this corpus is the Xbox image, so XBox and DEBUG are defined
# and the PS2/PC branches were never compiled. `#ifdefPS2` spells the directive with no space, so the macro is the rest
# of the word, and names are case-folded (reading `#ifdefXBOX` case-sensitively breaks 8 functions there).
BUILD_DEFINED = frozenset({"xbox", "debug"})
GUARD = re.compile(r"^\s*#\s*(ifdef|ifndef|else|endif)\s*(\w*)", re.I)

KINDS = ("var", "function", "native_function", "state", "struct", "enum", "const")
# The package column each kind is compared against. `functions` is the total (natives inside it), so the non-native
# count is the difference; every other kind is its own export class.
PROPERTY_KINDS = ("BoolProperty", "ByteProperty", "FloatProperty", "IntProperty", "ObjectProperty", "NameProperty",
                  "StringProperty", "ClassProperty", "StructProperty", "ArrayProperty", "MapProperty",
                  "DelegateProperty", "InterfaceProperty", "StrProperty", "PointerProperty", "FixedArrayProperty")


def unbuilt_removed(toks):
    """Drop every token inside a branch this build did not compile."""
    out = []
    stack = []
    active = True
    for kind, val in toks:
        if kind == "directive":
            m = GUARD.match(val)
            if not m:
                continue
            what, name = m.group(1).lower(), m.group(2).lower()
            if what in ("ifdef", "ifndef"):
                cond = (name in BUILD_DEFINED) if what == "ifdef" else (name not in BUILD_DEFINED)
                stack.append((active, cond))
                active = active and cond
            elif what == "else" and stack:
                parent, cond = stack[-1]
                active = parent and not cond
                stack[-1] = (parent, not cond)
            elif what == "endif" and stack:
                active = stack.pop()[0]
            continue
        if active:
            out.append((kind, val))
    return out


def tokens_of(text):
    toks, bad = lexer.lex(text)
    if bad is not None:
        return None
    out = []
    for kind, start, end in toks:
        if kind in SKIP:
            continue
        out.append((kind, text[start:end]))
    return out


def is_native(toks, i):
    """True when the declaration at `i` is native.

    `native(881) final simulated static function ...` puts six tokens between the two words, and the tokens between
    include the `native(<index>)` group, so the check walks back to the statement boundary rather than looking at `i-1`.
    """
    j = i - 1
    while j >= 0 and i - j <= 14:
        kind, val = toks[j]
        if kind == "op" and val in (";", "{", "}"):
            return False
        if val.lower() == "native":
            return True
        j -= 1
    return False


def name_before_paren(toks, i, cap=16):
    """The identifier immediately in front of the first `(` at or after `i`: a function's or delegate's own name.

    Reading the *last word* of a parameter list is what an argument is called; reading the first identifier after the
    keyword is the return type (`delegate bool OnClick(...)` reads `bool`). The name is the word that the `(` follows.
    """
    j = i
    last = None
    while j < len(toks) and j - i <= cap:
        kind, val = toks[j]
        if kind == "op" and val in (";", "{", "}"):
            return None
        if kind == "op" and val == "(":
            return last
        if kind in ("ident", "keyword"):
            last = val
        j += 1
    return None


def header(toks, stem):
    """(class, super) for the file's own declaration, or (None, None) when the text does not carry one.

    The shipped `ScriptText` of a few classes begins inside a comment whose `//` is not in the buffer, so prose sits in
    the token stream and `class is used to indicate the selected menu item` reads as a class named `is`. The file is
    named after the class (the extractor writes one `<Class>.uc` per `Class` export), so the declaration is the one
    whose name matches the file's stem; prose that merely starts with the word `class` never does.
    """
    cands = []
    i = 0
    while i < len(toks):
        kind, val = toks[i]
        if kind == "keyword" and val.lower() == "class":
            j = i + 1
            nm = None
            sup = None
            while j < len(toks) and toks[j][0] in ("ident", "keyword"):
                if nm is None:
                    nm = toks[j][1]
                elif toks[j][1].lower() == "extends" and j + 1 < len(toks):
                    sup = toks[j + 1][1]
                    break
                j += 1
            if nm:
                cands.append((nm, sup))
        i += 1
    if stem:
        for nm, sup in cands:
            if nm.lower() == stem.lower():
                return nm, sup
    for nm, sup in cands:
        if sup:
            return nm, sup
    return (cands[0] if cands else (None, None))


def skip_group(toks, i):  # toks[i] is an opening bracket; return the index one past its match
    depth = 0
    while i < len(toks):
        if toks[i][0] == "op" and toks[i][1] in "([{":
            depth += 1
        elif toks[i][0] == "op" and toks[i][1] in ")]}":
            depth -= 1
            if depth == 0:
                return i + 1
        i += 1
    return i


def strip_groups(seg):
    """A statement's tokens with `(...)`, `[...]` and `<...>` groups removed.

    `var HaakenPodDoor Doors[NUM_DOORS]` declares `Doors`; the array size is another declaration's name (`NUM_DOORS`
    is a `const` of the same class), so reading the last identifier of the raw statement reads the constant.
    """
    out = []
    i = 0
    while i < len(seg):
        kind, val = seg[i]
        if kind == "op" and val in "([{":
            i = skip_group(seg, i)
            continue
        if kind == "op" and val in ")]}":
            i += 1
            continue
        out.append((kind, val))
        i += 1
    return out


# The compiler names an operator declaration after its verb and its operand types: `operator(20) color + (color A,
# color B)` is `Add_ColorColor`, and `!=` is `NotEqual_BoolBool`. The text spells a symbol, so the only shared part is
# the type suffix -- a package-only Function whose tail decomposes into type names is that operator.
TYPE_WORDS = ("bool", "byte", "int", "float", "string", "str", "name", "object", "vector", "rotator", "color",
              "pointer", "array", "class", "map", "struct")


def operator_shaped(nm, types=None):
    words = sorted(TYPE_WORDS if types is None else types, key=len, reverse=True)
    tail = nm.rsplit("_", 1)[-1]
    if not tail or tail == nm:
        return False
    for prefix in ("pre", "post"):
        if tail.startswith(prefix):
            tail = tail[len(prefix):]
            break
    i = 0
    while i < len(tail):
        for word in words:
            if tail.startswith(word, i):
                i += len(word)
                break
        else:
            return False
    return i > 0


def parse(text, stem=None):
    """Name-level read of one file.

    Returns None when the text does not lex, else a dict: `class`, `super`, `status` (`ok` / `no-header`) and `names`,
    a list of (kind, name) pairs where `kind` is one of KINDS. Counts are `len` over `names`, so the two can never
    disagree -- a count column and a name list computed separately is how a parser scores a rename as agreement.
    """
    toks = tokens_of(text)
    if toks is None:
        return None
    toks = unbuilt_removed(toks)
    cls, sup = header(toks, stem)
    names = []
    depth = 0
    i = 0
    while i < len(toks):
        kind, val = toks[i]
        if kind == "op" and val == "{":
            depth += 1
            i += 1
            continue
        if kind == "op" and val == "}":
            depth = max(0, depth - 1)
            i += 1
            continue
        if kind != "keyword" or depth != 0:
            i += 1
            continue
        lv = val.lower()   # UnrealScript is case-insensitive: `class ClientMover Extends Mover` is valid
        if lv == "class":
            j = i + 1
            while j < len(toks) and toks[j][0] in ("ident", "keyword"):
                if toks[j][1].lower() == "extends":
                    j += 2
                    break
                j += 1
            i = j
            continue
        if lv == "state":
            # `state() LoopMove` is a state whose label is empty: 12 of Mover's 19 states are spelled that way, and a
            # parser that wants an identifier straight after the keyword misses all twelve.
            j = i + 1
            if j < len(toks) and toks[j] == ("op", "("):
                j = skip_group(toks, j)
            if j < len(toks) and toks[j][0] in ("ident", "keyword"):
                names.append(("state", toks[j][1]))
                i = j + 1
            else:
                i += 1
            continue
        if lv in ("struct", "enum"):
            j = i + 1
            while j < len(toks) and toks[j][0] == "keyword" and toks[j][1].lower() not in ("struct", "enum"):
                j += 1
            if j < len(toks) and toks[j][0] in ("ident", "keyword"):
                names.append((lv, toks[j][1]))
                i = j + 1
            else:
                i += 1      # leave the brace to the depth walk: skipping it reads the whole body at class scope
            continue
        if lv == "const":
            if i + 1 < len(toks) and toks[i + 1][0] == "ident":
                names.append(("const", toks[i + 1][1]))
                i += 2
            else:
                i += 1      # `const enum EPhysics` belongs to the `var` statement it appears in
            continue
        if lv == "delegate":
            nm = name_before_paren(toks, i + 1)
            if nm:
                # the export table carries one property per delegate, named `__<name>__delegate`, and one Function
                # named after the delegate itself (measured: 75 such functions across 20 classes)
                names.append(("var", "__%s__delegate" % nm))
                names.append(("function", nm))
            i += 1
            continue
        if lv == "operator":
            # `static final operator(20) color + (color A, color B)` is a Function export named `Add_ColorColor`: the
            # text cannot spell that name, and every such function is native.
            names.append(("operator", "operator"))
            i += 1
            continue
        if lv in ("function", "event"):
            nm = name_before_paren(toks, i + 1)
            if nm:
                names.append(("native_function" if is_native(toks, i) else "function", nm))
            i += 1
            continue
        if lv == "var":
            # the statement runs to `;` at this depth; a `var(Display) enum EPhysics { ... } Physics;` carries an enum
            # block inside it, which is a brace group to step over rather than the end of the statement
            j = i + 1
            seg = []
            enums = []
            while j < len(toks):
                k2, v2 = toks[j]
                if k2 == "op" and v2 == ";":
                    break
                if k2 == "op" and v2 in "([":
                    end = skip_group(toks, j)
                    seg.extend(toks[j:end])
                    j = end
                    continue
                if k2 == "op" and v2 == "{":
                    end = skip_group(toks, j)
                    j = end
                    continue
                if k2 == "keyword" and v2.lower() == "enum" and j + 1 < len(toks):
                    end = None
                    for m in range(j + 1, len(toks)):
                        if toks[m] == ("op", "{"):
                            end = m
                            break
                        if toks[m] == ("op", ";"):
                            break
                    if end is not None and toks[j + 1][0] in ("ident", "keyword"):
                        enums.append(toks[j + 1][1])
                        j = skip_group(toks, end)
                        continue
                seg.append((k2, v2))
                j += 1
            for nm in enums:
                names.append(("enum", nm))
            for part in split_commas(seg):
                # the name is the last word of the segment and need not be an identifier: `var localized string
                # MapTitle, Author, Restart, Continue, ...` declares a property called `Continue`, and a name is a
                # name whether or not the lexer also knows the word as a keyword.
                words = [t[1] for t in strip_groups(part) if t[0] in ("ident", "keyword")]
                if words:
                    names.append(("var", words[-1]))
            i = j + 1
            continue
        i += 1
    if cls is None:
        return {"class": None, "super": None, "status": "no-header", "names": names}
    return {"class": cls, "super": sup, "status": "ok", "names": names}


def split_commas(seg):
    """Split a statement's tokens on top-level commas."""
    parts = [[]]
    depth = 0
    for kind, val in seg:
        if kind == "op" and val in "([{":
            depth += 1
        elif kind == "op" and val in ")]}":
            depth -= 1
        if kind == "op" and val == "," and depth == 0:
            parts.append([])
            continue
        parts[-1].append((kind, val))
    return parts


def counts_of(names):
    out = dict.fromkeys(KINDS, 0)
    for kind, _name in names:
        # an operator is a native Function in the package (Object's 104 are all native), so it counts there even
        # though the text spells a symbol where the compiler writes `Add_ColorColor`
        out["native_function" if kind == "operator" else kind] += 1
    return out


def declarations(text, stem=None):
    """(class, super, counts) -- the shape the two sibling tools unpack (stage 2b/2c ask only for the class)."""
    got = parse(text, stem)
    if got is None:
        return None
    return (got["class"], got["super"], counts_of(got["names"]))


def load_packages():
    """lowercased class name -> row, from the export-derived table (UnrealScript is case-insensitive)."""
    out = {}
    path = os.path.join(ROOT, "inventory", "classes.csv")
    with open(path, newline="") as fh:
        for row in csv.DictReader(fh):
            out.setdefault(row["class"].lower(), row)
    return out


def scored_rows(tree):
    """Every file walked: (row, names) with names kept in memory for --residue."""
    pkgs = load_packages()
    rows = []
    seen = {}
    for dirpath, _dirs, files in os.walk(tree):
        for name in sorted(files):
            if not name.endswith(".uc"):
                continue
            path = os.path.join(dirpath, name)
            with open(path, "r", encoding="latin-1", newline="") as fh:
                text = fh.read()
            stem = name[:-3]
            got = parse(text, stem)
            rel = os.path.relpath(path, tree)
            if got is None:
                rows.append(({"file": rel, "class": "", "super": "", "status": "unlexable"}, []))
                continue
            cls, sup = got["class"], got["super"]
            counts = counts_of(got["names"])
            row = {"file": rel, "class": cls or "", "super": sup or "", "status": got["status"], **counts}
            if not cls:
                row["status"] = "no-header"
                rows.append((row, got["names"]))
                continue
            key = cls.lower()
            if key in seen:
                row["status"] = "duplicate"
                rows.append((row, got["names"]))
                continue
            seen[key] = rel
            pkg = pkgs.get(key)
            if pkg:
                row["package_class"] = pkg["class"]
                row["status"] = "scored"
                row["package"] = pkg["package"]
                rows.append((row, got["names"]))
                continue
            rows.append((row, got["names"]))
    return rows


def report(rows):
    scored = [r for r, _n in rows if r["status"] == "scored"]
    pkgs = load_packages()
    print("%d file(s), %d joined to a package row" % (len(rows), len(scored)))
    heads = {"class": None, "super": "super", "var": "properties", "function": "functions",
             "native_function": "native_functions", "state": "states", "struct": "structs",
             "enum": "enums", "const": "consts"}
    for col in ("class", "super", "var", "function", "native_function", "state", "struct", "enum", "const"):
        agree = hi = lo = 0
        nonzero = 0
        for r in scored:
            p = pkgs[r["class"].lower()]
            if col == "class":
                agree += 1
                continue
            if col == "super":
                agree += (r["super"] or "").lower() == (p["super"] or "").lower()
                continue
            key = heads[col]
            theirs = int(p[key])
            if col == "function":
                theirs -= int(p.get("native_functions") or 0)
            if theirs:
                nonzero += 1
            mine = int(r[col])
            if theirs == mine:
                agree += 1
            elif theirs > mine:
                hi += 1
            else:
                lo += 1
        print("   %-16s %5d / %5d  %5.1f%%   table higher %4d, source higher %4d   non-zero rows %d"
              % (col, agree, len(scored), 100.0 * agree / len(scored) if scored else 0.0, hi, lo, nonzero))


def write_csv(rows, out_dir):
    fields = ["file", "class", "super", "status", "var", "function", "native_function", "state",
              "struct", "enum", "const", "package", "package_class"]
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "uscript_decls.csv")
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, lineterminator="\n")
        w.writeheader()
        for row, _names in rows:
            w.writerow({k: row.get(k, "") for k in fields})
    print("wrote %s" % os.path.relpath(path, ROOT))
    return path


def residue(rows, dump, out_dir):
    """Join the source's declared names against the package's export names per class, one row per name that differs."""
    from ue2pkg import Package
    bypkg = {}
    out = []
    for row, names in rows:
        if row["status"] != "scored":
            continue
        pkg = row["package"]
        if pkg not in bypkg:
            p = Package(os.path.join(dump, pkg + ".u"))
            p.read_names()
            p.read_imports()
            p.read_exports()
            bypkg[pkg] = p
        p = bypkg[pkg]
        want = row["class"].lower()
        idx = None
        for e in p.exports:
            if p.class_name_of(e) == "Class" and p.name(e["name"]).lower() == want:
                idx = e["index"] + 1
                break
        if idx is None:
            continue
        theirs = {"var": [], "function": [], "state": [], "enum": [], "const": []}
        types = set(TYPE_WORDS)
        for c in p.exports:
            if c["outer"] != idx:
                continue
            k = p.class_name_of(c)
            nm = p.name(c["name"]).lower()
            if k in PROPERTY_KINDS:
                theirs["var"].append(nm)
            elif k == "Function":
                theirs["function"].append(nm)
            elif k == "State":
                theirs["state"].append(nm)
            elif k == "Enum":
                theirs["enum"].append(nm)
            elif k == "Const":
                theirs["const"].append(nm)
            elif k == "Struct":
                types.add(nm)       # an operator's operand types can be structs this class declares
        mine = {"var": [], "function": [], "state": [], "enum": [], "const": []}
        delegates = set()
        operators = 0
        for kind, nm in names:
            low = nm.lower()
            if kind == "operator":
                operators += 1
            elif kind in ("function", "native_function"):
                mine["function"].append(low)
            elif kind in mine:
                mine[kind].append(low)
            if kind == "var" and low.startswith("__") and low.endswith("__delegate"):
                delegates.add(low[2:-10])
        for kind in ("var", "function", "state", "enum", "const"):
            a, b = sorted(mine[kind]), sorted(theirs[kind])
            only_pkg = sorted(set(b) - set(a))
            only_src = sorted(set(a) - set(b))
            for nm in only_pkg:
                cause = "unexplained"
                if kind == "function" and nm in delegates:
                    cause = "delegate-function"
                elif kind == "function" and operators and operator_shaped(nm, types):
                    cause = "operator"
                out.append({"class": row["class"], "kind": kind, "side": "package", "name": nm, "cause": cause})
            for nm in only_src:
                out.append({"class": row["class"], "kind": kind, "side": "source", "name": nm, "cause": "unexplained"})
    os.makedirs(out_dir, exist_ok=True)
    path = os.path.join(out_dir, "uscript_decls_residue.csv")
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["class", "kind", "side", "name", "cause"], lineterminator="\n")
        w.writeheader()
        w.writerows(out)
    causes = {}
    for r in out:
        causes[(r["kind"], r["cause"])] = causes.get((r["kind"], r["cause"]), 0) + 1
    print("residue: %d name(s) differ" % len(out))
    for (kind, cause), n in sorted(causes.items()):
        print("   %-8s %-18s %4d" % (kind, cause, n))
    print("wrote %s" % os.path.relpath(path, ROOT))
    return path


SYNTH = """class Probe extends Actor
    config(Engine) native;
var int Count, Other;
var() string Label;
var array<int> Items;
native function bool Check(optional int N);
function DoIt() { local int T; T = 1; }
state Idle { function Tick() { } }
state() LoopMove { }
struct Point { var float X, Y; };
enum EFlag { A, B };
const Limit = 4;
const NUM_DOORS = 3;
var Point Doors[NUM_DOORS];
var(Display) enum EPhysics { PHYS_None, PHYS_Falling } Physics;
delegate bool OnClick(Actor Sender);
function bool KeyEvent(out byte Key, out byte State, float delta) { return State == 3; }
"""

# Prose whose `//` is not in the shipped buffer sits in the token stream, so the header scan sees `class is used ...`
# before the real declaration. The file's stem is what tells them apart.
SYNTH_PROSE = """class is used to indicate the selected menu item for main menus
  It is composed of a primary image
 * ============================================================================
 */
class ProbeProse extends CameraEffect
\tnative;
var int One;
"""


def check(_args):
    got = parse(SYNTH, "Probe")
    if got is None:
        print("FAIL uscript-decls: the synthetic corpus does not lex")
        return 1
    counts = counts_of(got["names"])
    # `Tick` lives inside `state Idle` and is *not* counted: the measured convention excludes state functions
    want = {"var": 7, "function": 3, "native_function": 1, "state": 2, "struct": 1, "enum": 2, "const": 2}
    if got["class"] != "Probe" or (got["super"] or "") != "Actor":
        print("FAIL uscript-decls: header parsed as %r extends %r" % (got["class"], got["super"]))
        return 1
    for key, value in want.items():
        if counts[key] != value:
            print("FAIL uscript-decls: %s counted %d, expected %d (%r)"
                  % (key, counts[key], value, [n for k, n in got["names"] if k == key]))
            return 1
    names = [n for _k, n in got["names"]]
    for expect in ("Doors", "Physics", "__OnClick__delegate", "KeyEvent", "NUM_DOORS"):
        if expect not in names:
            print("FAIL uscript-decls: %r is not in the declared names %r" % (expect, names))
            return 1
    for reject in ("NUM_DOORS_keyevent", "bool", "State"):
        if reject in names:
            print("FAIL uscript-decls: %r was read as a declaration name" % reject)
            return 1

    prose = parse(SYNTH_PROSE, "ProbeProse")
    if not prose or prose["class"] != "ProbeProse":
        print("FAIL uscript-decls: a prose `class` line was read as the header (%r)" % (prose and prose["class"]))
        return 1
    # and a text with no declaration of its own is refused, not guessed at
    bare = parse("// FightEnemy\nfunction FightEnemy() { local float d; }\n", "BotScarab")
    if not bare or bare["status"] != "no-header":
        print("FAIL uscript-decls: a text with no class header was not refused")
        return 1
    print("ok uscript-decls: class=%s extends %s, %s" % (got["class"], got["super"], counts))
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--tree")
    ap.add_argument("-o", "--out", default=os.path.join(ROOT, "inventory"))
    ap.add_argument("--residue", action="store_true", help="join the declared names against the package's exports")
    ap.add_argument("--dump", help="the package directory (System/) --residue reads")
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    if args.check:
        return check(args)
    if not args.tree:
        print("FAIL uscript-decls: --tree is required (the source corpus stays under $WORK)")
        return 2
    if args.residue and not args.dump:
        print("FAIL uscript-decls: --residue needs --dump (the live packages, not just the committed table)")
        return 2
    rows = scored_rows(args.tree)
    report(rows)
    write_csv(rows, args.out)
    if args.residue:
        residue(rows, args.dump, args.out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
