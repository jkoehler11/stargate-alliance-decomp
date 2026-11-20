#!/usr/bin/env python3
"""What kind of body installs a nameless class table: a constructor, a destructor, or neither.

`tools/class-table-installers.py` counts who stores the 250 tables no name reaches and
`tools/class-table-store-fields.py` says where the store writes. Neither says *what the storing
body is*. A class table is installed by the class's own constructor, and `tools/class-ctors.py`
already separates a constructor from a destructor with two signatures that agree -- a constructor
returns `this` (`mov eax, <this reg>` in the epilogue), a destructor installs the table before any
call and returns nothing. This tool runs those two signatures over every body that installs one of
the **724 tables** `inventory/native_vtable_reach.csv` censuses, so the nameless tables are read
by the same rule that named the map's own `ctor_`/`dtor_` rows.

The control is the population the map already names. For the 474 tables the registration path
names, the installer's symbol is usually `ctor_<Class>` or `dtor_<Class>`; the census records how
often the shape rule lands on that answer, and that rate is the base rate for the nameless tables
rather than an assumption about them.

Two readings this settles for the 176 plain-only tables written into the object slot: a body that
installs exactly one table and returns `this` is that object's constructor, so the table is an
object's own dispatch table inside a class the image never registers; a table whose only
installers are destructor-shaped is uninstalled on destruction and has no constructor site here.

    tools/class-table-installer-shape.py --xbe DUMP/default.xbe -o inventory
    tools/class-table-installer-shape.py --report
    tools/class-table-installer-shape.py --check          # synthetic bodies, no dump, writes nothing

The dump is never committed; the artifact and the page block are. Addresses, counts and the two
shape flags only.
"""
import argparse
import bisect
import collections
import csv
import importlib.util
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

OUT = os.path.join("inventory", "class_table_installer_shapes.csv")
REACH = os.path.join(ROOT, "inventory", "native_vtable_reach.csv")
NAMES = os.path.join(ROOT, "inventory", "xbe_class_table_names.csv")
INST = os.path.join(ROOT, "inventory", "class_table_installers.csv")
CTORS = os.path.join(ROOT, "inventory", "class_constructors.csv")
MAP = os.path.join(ROOT, "symbols", "xbox_functions.csv")
PAGE = os.path.join("docs", "systems", "CLASS_REGISTRATION.md")
DOC_START = "<!-- generated:class-table-installer-shapes -->"
DOC_END = "<!-- /generated:class-table-installer-shapes -->"
COLUMNS = ("table_va", "slots", "population", "installers", "self_installs", "ctor_shaped",
           "dtor_shaped", "unclassified", "tail_ret", "tail_jmp", "frame_return",
           "dtor_frame_return", "named_ctor", "named_dtor", "named_agree", "verdict", "evidence")
POPS = ("named", "bounded", "plain-only", "no-store")


def read(path):
    if not os.path.isfile(path):
        return []
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def body_index(path):
    """[(start, end, name)]; the map's size column is a byte count in DECIMAL."""
    out = []
    for r in read(path):
        try:
            a = int(r["address"], 16)
            s = int(r["size"] or 0)
        except (KeyError, ValueError):
            continue
        out.append((a, a + s if s else a, r.get("name", "")))
    out.sort()
    return out


def owner(index, starts, va):
    i = bisect.bisect_right(starts, va) - 1
    if i < 0:
        return None
    a, end, _ = index[i]
    if end and va >= end:
        return None
    return a


def symbol_class(name):
    for pre in ("ctor_", "dtor_"):
        if name.startswith(pre):
            return pre[:-1], name[len(pre):]
    return "", ""


def tail_kind(body):
    """How the body ends: a `ret`, a tail `jmp`, or something else."""
    i = len(body) - 1
    while i >= 0 and body[i] == 0xCC:                    # the pad after a body is not its tail
        i -= 1
    if i < 0:
        return "other"
    b = body[i]
    if b in (0xC3, 0xC2):
        return "ret"
    if b in (0xE9, 0xEB):
        return "jmp"
    if b == 0xFF and i >= 1 and (body[i - 1] & 0x38) in (0x20, 0x28):
        return "jmp"
    return "other"


def frame_return(body):
    """`mov eax, [esp+disp]` / `mov eax, [ebp+disp]`: a constructor returning `this` from the frame.

    The class-ctors signature only matches `mov eax, <reg>` (`8B C0`..`8B C7`), so a constructor whose
    `this` sits on the frame -- a thunk-shaped body or one the compiler spilled -- reads as a
    destructor. This is the second return form, kept as its own column so the two are not confused.
    """
    tail = body[-40:] if len(body) > 40 else body
    for i in range(len(tail) - 2):
        if tail[i] != 0x8B:
            continue
        m = tail[i + 1]
        if m in (0x44, 0x45, 0x84, 0x85):
            return True
    return False


def load_shape():
    spec = importlib.util.spec_from_file_location("sg1_class_ctors",
                                                  os.path.join(ROOT, "tools", "class-ctors.py"))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod.shape


def harvest(reach, installers, names, ctors, index, text, text_va, shape):
    """One row per table: its installer bodies read by the ctor/dtor shape rule."""
    by_addr = {a: (a, end, nm) for a, end, nm in index}
    name_of = {a: nm for a, _, nm in index}
    starts = [a for a, _, _ in index]
    class_form = {}
    for r in read(ctors):
        try:
            class_form.setdefault(int(r["function_va"], 16), (r.get("class") or "", r.get("form") or ""))
        except (KeyError, ValueError):
            continue
    pop = {r["table_va"]: r["verdict"] for r in installers}
    named = {r["table_va"]: r["class"] for r in names if r.get("class")}
    # every table a body installs, so "installs one table only" is a fact about the body
    body_tables = collections.defaultdict(set)
    sites_of = {}
    for t in reach:
        tva = t.get("table_va")
        if not tva:
            continue
        sites = [int(s, 16) for s in (t.get("install_sites") or "").split() if s]
        sites_of[tva] = sites
        for s in sites:
            b = owner(index, starts, s)
            if b is not None:
                body_tables[b].add(tva)
    lo, hi = text_va, text_va + len(text)
    rows = []
    for t in reach:
        tva = t.get("table_va")
        if not tva:
            continue
        sites = sites_of.get(tva, [])
        bodies = sorted({b for b in (owner(index, starts, s) for s in sites) if b is not None})
        counts = collections.Counter()
        tails = collections.Counter()
        frame = collections.Counter()
        self_installs = n_ctor = n_dtor = agree = 0
        cls = named.get(tva, "")
        for b in bodies:
            a, end, nm = by_addr[b]
            body = text[a - text_va:a - text_va + (end - a)]
            if not body:
                counts["unclassified"] += 1
                continue
            store_off = min((s - a for s in sites if owner(index, starts, s) == b), default=0)
            sh = shape(body, store_off, a, lo, hi)
            counts[sh["form"]] += 1
            tail = tail_kind(body)
            tails[tail] += 1
            fr = frame_return(body)
            frame["yes"] += int(fr)
            if sh["form"] == "dtor" and fr:
                frame["dtor"] += 1
            self_installs += int(len(body_tables[b]) == 1)
            kind, name_cls = symbol_class(nm)
            if kind in ("ctor", "dtor"):
                n_ctor += int(kind == "ctor")
                n_dtor += int(kind == "dtor")
                agree += int(bool(cls) and name_cls == cls)
            elif b in class_form:
                cf_cls, cf_form = class_form[b]
                if cf_form in ("ctor", "dtor"):
                    n_ctor += int(cf_form == "ctor")
                    n_dtor += int(cf_form == "dtor")
                    agree += int(bool(cls) and cf_cls == cls)
        if not bodies:
            verdict = "no-body"
        elif counts["ctor"] and not counts["dtor"]:
            verdict = "installed-by-ctor"
        elif counts["dtor"] and not counts["ctor"]:
            verdict = "dtor-only"
        elif counts["ctor"]:
            verdict = "ctor-and-dtor"
        else:
            verdict = "unclassified"
        rows.append({
            "table_va": tva,
            "slots": t.get("table_slots", ""),
            "population": "named" if cls else pop.get(tva, "unnamed-other"),
            "installers": len(bodies),
            "self_installs": self_installs,
            "ctor_shaped": counts["ctor"],
            "dtor_shaped": counts["dtor"],
            "unclassified": counts["unclassified"],
            "tail_ret": tails["ret"],
            "tail_jmp": tails["jmp"],
            "frame_return": frame["yes"],
            "dtor_frame_return": frame["dtor"],
            "named_ctor": n_ctor,
            "named_dtor": n_dtor,
            "named_agree": agree,
            "verdict": verdict,
            "evidence": "%d bod%s: %s; %d install one table only; tails %d ret / %d jmp"
                        % (len(bodies), "" if len(bodies) == 1 else "ies",
                           ", ".join("%s %d" % (k, counts[k]) for k in ("ctor", "dtor", "unclassified")
                                     if counts[k]) or "none read",
                           self_installs, tails["ret"], tails["jmp"]),
        })
    return rows


def counts(rows):
    by_pop = collections.defaultdict(collections.Counter)
    for r in rows:
        d = by_pop[r["population"]]
        d["tables"] += 1
        for k in ("installers", "self_installs", "ctor_shaped", "dtor_shaped", "unclassified",
                  "tail_ret", "tail_jmp", "frame_return", "dtor_frame_return",
                  "named_ctor", "named_dtor", "named_agree"):
            d[k] += int(r[k] or 0)
        d["v:" + r["verdict"]] += 1
    return by_pop


def render(rows):
    c = counts(rows)
    out = ["| population | tables | installer bodies | returns `this` (ctor) | installs before any call (dtor) "
           "| neither | ends in a tail `jmp` | names one table only |",
           "|---|---|---|---|---|---|---|---|"]
    for pop in POPS:
        d = c[pop]
        if not d["tables"]:
            continue
        out.append("| %s | %d | %d | %d | %d | %d | %d | %d |"
                   % (pop, d["tables"], d["installers"], d["ctor_shaped"], d["dtor_shaped"],
                      d["unclassified"], d["tail_jmp"], d["self_installs"]))
    named, plain = c["named"], c["plain-only"]
    out.append("")
    out.append("**The control is the population the registration path already names.** Of the %d installer bodies "
               "behind the %d named tables, %d carry a `ctor_<Class>`/`dtor_<Class>` symbol and the shape rule "
               "agrees with that class on %d; %d bodies return `this`, %d install the table before any call and "
               "%d read as neither."
               % (named["installers"], named["tables"], named["named_ctor"] + named["named_dtor"],
                  named["named_agree"], named["ctor_shaped"], named["dtor_shaped"], named["unclassified"]))
    out.append("")
    out.append("**Of the %d plain-only tables, %d installer bodies return `this`, %d install before any call and "
               "%d read as neither; %d of the bodies install exactly one table, which is a self-install.** "
               "%d tables are installed only by a body that returns `this`, %d only by a destructor-shaped body, "
               "%d by both, %d by bodies the rule reads as neither and %d name no installer body at all."
               % (plain["tables"], plain["ctor_shaped"], plain["dtor_shaped"], plain["unclassified"],
                  plain["self_installs"], plain["v:installed-by-ctor"], plain["v:dtor-only"],
                  plain["v:ctor-and-dtor"], plain["v:unclassified"], plain["v:no-body"]))
    out.append("")
    out.append("**A destructor never returns `this`, and the class-ctors signature only matches `mov eax, <reg>`.** "
               "Its second form is a load from the frame (`mov eax,[esp+N]` / `[ebp+N]`): %d of the %d "
               "dtor-shaped installer bodies behind the plain-only tables return `this` that way; the other %d "
               "carry the destructor shape, which an initializer that installs the table into an object it was "
               "handed -- and returns something other than `this` -- has as well. That is why the verdict is a "
               "reading and not a proof: %d tables have only such installers, and the artifact names them one "
               "body at a time instead of sweeping them into a constructor count."
               % (plain["dtor_frame_return"], plain["dtor_shaped"],
                  plain["dtor_shaped"] - plain["dtor_frame_return"], plain["v:dtor-only"]))
    return out


def write_doc(path, rows, check=False):
    text = open(path, encoding="utf-8").read()
    i, j = text.find(DOC_START), text.find(DOC_END)
    if i < 0 or j < 0:
        raise SystemExit("%s: no %s block" % (path, DOC_START))
    new = text[:i + len(DOC_START)] + "\n" + "\n".join(render(rows)) + "\n" + text[j:]
    if check:
        if new != text:
            raise SystemExit("%s: the class-table-installer-shapes block is stale" % path)
        print("ok   class-table-installer-shapes: the page block reproduces")
        return
    open(path, "w", encoding="utf-8").write(new)
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


def self_check():
    """Synthetic bodies: the two signatures, and a call before the store that is neither."""
    shape = load_shape()
    lo, hi = 0x1000, 0x2000
    ctor = b"\x8b\xc6\xc3"                                          # mov eax,esi ; ret
    dtor = b"\xc7\x06\x00\x20\x00\x00\xc3"                          # store ; ret
    neither = b"\xe8\x00\x00\x00\x00\xc7\x06\x00\x20\x00\x00\xc3"   # call ; store ; ret
    cases = [
        ("a body that returns this reads ctor", shape(ctor, 0, 0x1000, lo, hi)["form"] == "ctor"),
        ("a body that installs before any call reads dtor", shape(dtor, 2, 0x1000, lo, hi)["form"] == "dtor"),
        ("a body with an unrelated call before the store reads neither",
         shape(neither, 5, 0x1000, lo, hi)["form"] == "unclassified"),
        ("a symbol class splits at its prefix", symbol_class("ctor_Foo") == ("ctor", "Foo")
         and symbol_class("FUN_00012340") == ("", "")),
    ]
    bad = [n for n, ok in cases if not ok]
    for n, ok in cases:
        print("%s %s" % ("ok  " if ok else "FAIL", n))
    if bad:
        raise SystemExit("class-table-installer-shape: %d selftest case(s) failed" % len(bad))
    print("class-table-installer-shape: selftest passed (%d cases)" % len(cases))


def main():
    ap = argparse.ArgumentParser(description=(__doc__ or "").splitlines()[0])
    ap.add_argument("--xbe")
    ap.add_argument("-o", "--out")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--write-doc")
    ap.add_argument("--check-doc")
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    if args.check:
        return self_check()
    src_rows = read(OUT)
    if args.check_doc or args.write_doc:
        if not src_rows:
            print("FAIL class-table-installer-shape: %s is missing" % OUT)
            return 1
        try:
            write_doc(args.check_doc or args.write_doc, src_rows, check=bool(args.check_doc))
        except SystemExit as exc:
            print("FAIL class-table-installer-shape: %s" % exc)
            return 1
        return 0
    if args.report:
        print("\n".join(render(src_rows)))
        return 0
    if not args.xbe:
        print("FAIL class-table-installer-shape: --xbe is required to read the image")
        return 1
    from xbe import Xbe
    image = Xbe(args.xbe)
    code = next(s for s in image.sections if s.name == ".text")
    text = image.data[code.foff:code.foff + min(code.fsize, code.vsize)]
    rows = harvest(read(REACH), read(INST), read(NAMES), CTORS, body_index(MAP), text, code.va,
                   load_shape())
    print("%d tables: %s" % (len(rows), ", ".join("%s %d" % (k, v["tables"])
                                                  for k, v in sorted(counts(rows).items()))))
    print("\n".join(render(rows)))
    if args.out:
        os.makedirs(args.out, exist_ok=True)
        write_csv(os.path.join(args.out, os.path.basename(OUT)), rows)
    return 0


if __name__ == "__main__":
    sys.exit(main())
