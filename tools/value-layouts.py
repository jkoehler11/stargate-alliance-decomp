#!/usr/bin/env python3
"""The value half of a property tag: what each class's serialiser writes, and the corpus test of it.

`docs/NATIVE_PROPERTIES.md` §6 settles the tag's *header* in both directions and measures the value's
**width** per type; §7 left the value's internal layout open.  It is one slot away: `0x0003c384` hands the
value to `0x00038c30`, which folds a bool's bit into the tag's type byte and otherwise dispatches
`[property+0xa0]` -- the per-class `SerializeItem` body `inventory/property_serialisers.csv` already names.
Fourteen of those bodies are readable, and each one's traffic is a rule:

    Int / Pointer   0x00077c30 -> 0x00037110    four bytes
    Float           0x000783c0 -> 0x00037170    four bytes
    Byte            0x00077ab0 -> 0x00037040    one byte
    Bool            0x00078230 -> 0x00037040    one byte, nested only -- in a tag it rides in bit 7
    Object / Class  0x00078660 -> Ar[+0x18]     one compact index
    Name            0x00078840 -> Ar[+0x1c]     one compact index
    Delegate        0x00077f00                  an object's index then a name's
    Str             0x00078900 -> 0x00058850    a compact length, then that many characters
    Array           0x00078ea0                  a compact count, then the Inner's own rule count times
    FixedArray      0x00078a20                  the Inner's rule ArrayDim times, and no count
    Struct          0x000795f0                  the struct's own tagged walk, or a native body
    Map             0x000794a0                  nothing at all (three bytes, no archive traffic)

The widths are not quoted from the listing: `op_width` reads each archive-operator body and takes the
length its own `push` hands the archive's `Serialize`.  The `FString` and `compact` bodies are read the
same way, which is how a one-byte empty string (`0x00`) and a two-byte name index both fall out.

`0x00058850` is worth reading in full, because it corrects a guess this project had shipped: the length
prefix **counts the terminating NUL** and the writer emits exactly that many characters -- one byte each
for an ANSI string (`0x37040` in the loop at `0x00058940`), two for a Unicode one (`0x37080` at
`0x0005896e`), with the length negative in the Unicode case (`neg edx` at `0x00058897`).  An empty string
is therefore `0x00` and one byte wide, which is the `Size` 2,492 shipped `Str` tags report.

`0x000795f0` splits the structs in two: `[Inner+0x20]` is compared with three name indices -- `0x57`,
`0x58`, `0x5a` -- and those three skip the tagged walk (`[Inner+0x98]`) for the native body.  The seed
table (`inventory/gname_seed.csv`) spells them `Vector`, `Rotator`, `Color`, and the corpus agrees: those
are the three structs whose value never walks as a tag list.

So the value is a second opinion on the header, and that is what this tool measures: every tag's value is
decoded by its class's rule and required to consume exactly the `Size` the header declares.  The shipped
locator (`prop-stream.find_block`) now takes that opinion too -- a candidate whose value does not decode is
passed over for one whose values all do -- so this census reports the locator's own start, how many starts
it moved, and the blocks where a value still does not decode because no start fixes them.

    python3 tools/value-layouts.py DUMP --sweep -o inventory    # writes inventory/value_layouts.csv
    python3 tools/value-layouts.py --report                     # re-render from the artifact
    python3 tools/value-layouts.py --write-doc docs/NATIVE_PROPERTIES.md
    python3 tools/value-layouts.py --check                      # synthetic, no dump, writes nothing
"""

import argparse
import collections
import csv
import importlib.util
import os
import struct
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
from ue2pkg import Package  # noqa: E402

ARTIFACT = os.path.join(ROOT, "inventory/value_layouts.csv")
SERIALISERS = os.path.join(ROOT, "inventory/property_serialisers.csv")
SEED = os.path.join(ROOT, "inventory/gname_seed.csv")
DOC = os.path.join(ROOT, "docs/NATIVE_PROPERTIES.md")
DOC_START = "<!-- generated:value-layouts -->"
DOC_END = "<!-- /generated:value-layouts -->"
DEFAULT_DUMP = os.path.join(os.path.expanduser("~"), "nfs/sg1-dump/Stargate_SG-1_-_The_Alliance-2005-12-15")
FIELDS = ["scope", "key", "count", "note"]

# The archive operators a value serialiser reaches, with the name the archive's own vtable gives them.
OPS = {0x37040: "byte", 0x37080: "word", 0x37110: "int", 0x37170: "float",
       0x58850: "FString", 0x637e0: "compact", 0x55ed0: "realloc", 0x2c800: "grow"}

# The three struct names `0x000795f0` skips the tagged walk for, by the GNames indices it compares.
NATIVE_INDICES = (0x57, 0x58, 0x5A)

TYPE_NAMES = {0: "Invalid", 1: "Byte", 2: "Int", 3: "Bool", 4: "Float", 5: "Object", 6: "Name",
              7: "String", 8: "Class", 9: "Array", 10: "Struct", 11: "Vector", 12: "Rotator",
              13: "Str", 14: "Map", 15: "FixedArray"}


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, "tools", filename))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ps = _load("prop_stream", "prop-stream.py")      # read_index / find_block, the shipped reader
te = _load("tag_encode", "tag-encode.py")        # the tag walk, one implementation for both tools
nv = _load("native_vtable", "native-vtable.py")  # objdump's sweep of .text


# ---------------------------------------------------------------- the image half

def op_width(insns, va):
    """The byte count an archive operator's own body hands `Serialize`: its one immediate `push`.

    `0x00037040` pushes `0x1`, `0x00037080` `0x2`, `0x00037110` and `0x00037170` `0x4`.  A body with no
    immediate push, or with several, is not a width and answers `None`.
    """
    pushes, a, end = [], va, va + 96
    while a in insns and a < end:
        op, args, n = insns[a]
        if op == "push" and args.startswith("0x") and args.count(",") == 0:
            pushes.append(int(args, 16))
        if op == "ret" or op == "retf":
            break
        a += n
    return pushes[0] if len(pushes) == 1 else None


def class_rules(xbe_path):
    """One rule per property class: the `SerializeItem` body's traffic, and the widths read off it.

    The class -> slot table is `inventory/property_serialisers.csv`, which `make property-serialisers`
    writes and gates; this re-reads each body from the image so the *widths* are measured here and not
    copied from a listing.
    """
    import xbe as X
    x = X.Xbe(xbe_path)
    text = next((s for s in x.sections if s.name == ".text"), None)
    insns, calls = nv.sweep(x, text)
    widths = {va: op_width(insns, va) for va in (0x37040, 0x37080, 0x37110, 0x37170)}
    rows = []
    with open(SERIALISERS, newline="", encoding="utf-8") as fh:
        for r in csv.DictReader(fh):
            if r["offset"] != "SerializeItem":
                continue
            rows.append(dict(cls=r["class"], slot=r["slot_va"], ops=r["ops"], shape=r["shape"]))
    for r in rows:
        r["rule"] = rule_of(r["ops"], widths, r["cls"])
    return {r["cls"]: r for r in rows}, widths


def rule_of(ops, widths, cls=""):
    """The value rule a body's traffic implies, in the words the page uses."""
    ops = [o for o in ops.split() if o]
    calls = [o.split(":", 1)[1] for o in ops if o.startswith("call:")]
    vts = [o for o in ops if o.startswith("vt:")]

    def w(va):
        return widths.get(int(va, 16))

    if not ops:
        return "nothing: no archive traffic at all"
    if "vt:0x98" in vts:
        return "the struct's own tagged walk, unless it is Vector, Rotator or Color"
    if "vt:0xa0" in vts and calls and calls[0] == "0x637e0":
        return "a compact count, then the Inner's rule count times"
    if "vt:0xa0" in vts:
        return "the Inner's rule ArrayDim times, and no count"
    if "vt:0x18" in vts and "vt:0x1c" in vts:
        return "one compact object index, then one compact name index"
    if "vt:0x18" in vts:
        return "one compact object index"
    if "vt:0x1c" in vts:
        return "one compact name index"
    if len(calls) == 1:
        va = int(calls[0], 16)
        name = OPS.get(va)
        if name == "FString":
            return "a compact length (counting the NUL), then that many characters"
        if name == "compact":
            return "one compact count"
        if name in ("byte", "word", "int", "float"):
            if cls == "BoolProperty":
                return "no byte in a tag (the value rides in bit 7), one byte when nested"
            width = w(calls[0])
            return "%s byte%s (`0x%08x`)" % ("one" if width == 1 else str(width),
                                             "" if width == 1 else "s", va)
    return "unclassified: " + " ".join(ops)


def native_names(path=SEED):
    """The three struct names the serialiser's `[Inner+0x20]` comparison spells."""
    with open(path, newline="", encoding="utf-8") as fh:
        names = {int(r["index"]): r["name"] for r in csv.DictReader(fh)}
    return {names[i] for i in NATIVE_INDICES if i in names}


# ---------------------------------------------------------------- the corpus half

CHECKABLE = ps.CHECKABLE

# The tag type a property class reports (`vtable+0xcc`), so the corpus's type names find their body.
TYPE_CLASS = {1: "ByteProperty", 2: "IntProperty", 3: "BoolProperty", 4: "FloatProperty",
              5: "ObjectProperty", 6: "NameProperty", 8: "ClassProperty", 9: "ArrayProperty",
              10: "StructProperty", 13: "StrProperty", 14: "MapProperty", 15: "FixedArrayProperty"}

# Types with no property class in this build: `Invalid`, the legacy `String`, and `Vector`/`Rotator`
# (whose properties are `Struct`s of those names).  A tag carrying one is a parse, not a property.
IMPOSSIBLE = ps.IMPOSSIBLE


def decode(t, v, struct_name, native_width):
    """The value rule, owned by the shipped reader: `prop-stream.decode_value`.

    It lives there because the locator now uses it too -- a block whose value does not decode is a start
    inside a value, and `find_block` passes it over -- and one implementation is what makes the census and
    the locator agree by construction rather than by inspection.
    """
    return ps.decode_value(t, v, struct_name, native_width)


def struct_census(dump, limit=None):
    """Pass one: the `Size` each struct name's values take, which is how the three native ones show up."""
    sizes = collections.defaultdict(collections.Counter)
    for path in package_paths(dump, limit):
        pkg = Package(path)
        pkg.read_names(); pkg.read_imports(); pkg.read_exports()
        for _lead, _c, start, tags, end, data, moved in blocks(pkg):
            if tags is None:
                continue
            blob = data[start:]
            for t in tags:
                if t.get("terminator") or t["type"] != 10:
                    continue
                name = pkg.name(t["struct"]) if t["struct"] else "?"
                sizes[name][t["size"]] += 1
    return sizes


def package_paths(dump, limit=None):
    paths = []
    maps = os.path.join(dump, "Maps")
    if os.path.isdir(maps):
        paths += [os.path.join(maps, n) for n in sorted(os.listdir(maps)) if n.endswith(".unr")]
    saves = os.path.join(dump, "System/Profiles/DefaultProfile")
    if os.path.isdir(saves):
        paths += [os.path.join(saves, n) for n in sorted(os.listdir(saves)) if n.endswith(".sgs")]
    return paths[:limit] if limit else paths


def blocks(pkg, native_width=None):
    """Every export that holds a property block: `(label, count, start, tags, end, data, moved)`.

    `start` is the locator's answer (`prop-stream.find_block`, value test on) and `moved` says the header
    test alone would have chosen a different start -- so this census reports the locator's own decision
    rather than a second guess at it.
    """
    for i, exp in enumerate(pkg.exports):
        if not exp["size"]:
            continue
        data = pkg.data[exp["offset"]:exp["offset"] + exp["size"]]
        found = ps.find_block(data, pkg, min_props=1, native_width=native_width)
        if not found:
            continue
        count, start, _props = found
        header = ps.find_block(data, pkg, min_props=1, values=False)
        moved = header is not None and header[1] != start
        tags, end, reason = te.walk(data[start:])
        if reason != "None" or start + end != len(data):
            yield (i, count, start, None, None, data, moved)
            continue
        yield (i, count, start, tags, end, data, moved)


def native_widths(sizes, names):
    """The width of the three structs the serialiser skips the tagged walk for, and only those.

    The width is the **modal** one: a native body is fixed-width, so a name the image skips has to hold a
    single size on all but a sliver of its tags -- and the sliver is then an inexact value, which is a
    finding rather than a failure.  Every other struct is judged as a tagged block, so a name that happens
    to appear once cannot pass by having one size.
    """
    width = {}
    for name in sorted(names):
        counter = sizes.get(name)
        if counter:
            width[name] = counter.most_common(1)[0][0]
    return width


def sweep(dump, limit=None, native=()):
    """Two passes: the struct widths, then every value against its class's rule."""
    sizes = struct_census(dump, limit)
    native_width = native_widths(sizes, native)

    total = collections.Counter()
    lone = collections.Counter()
    cls = collections.defaultdict(collections.Counter)
    structs = collections.defaultdict(collections.Counter)
    struct_sizes = collections.defaultdict(collections.Counter)
    file_rows = []
    residue = []
    for path in package_paths(dump, limit):
        rel = os.path.relpath(path, dump)
        pkg = Package(path)
        pkg.read_names(); pkg.read_imports(); pkg.read_exports()

        def struct_of(t):
            return pkg.name(t["struct"]) if t["struct"] else "?"

        counts = collections.Counter()
        for _i, _c, start, tags, end, data, moved in blocks(pkg, native_width):
            if tags is None:
                continue
            counts["blocks"] += 1
            counts["blocks_relocated"] += 1 if moved else 0
            blob = data[start:]
            _equal, _terms, hreasons = te.compare(blob, tags)
            if hreasons:
                counts["blocks_header_refused"] += 1
            marks = []
            for t in tags:
                if t.get("terminator"):
                    continue
                v = blob[t["header_end"]:t["header_end"] + t["size"]]
                verdict, _detail = decode(t["type"], v, struct_of(t), native_width)
                marks.append((t, verdict))
            bad = [m for m in marks if m[1] == "inexact"]
            for t, verdict in marks:
                name = TYPE_NAMES.get(t["type"], t["type"])
                cls[name][verdict] += 1
                if t["type"] == 10:
                    structs[struct_of(t)][verdict] += 1
                    struct_sizes[struct_of(t)][t["size"]] += 1
            counts["tags"] += len(marks)
            if bad:
                counts["blocks_inexact"] += 1
                counts["inexact_values"] += len(bad)
                if len(bad) == 1:
                    lone[TYPE_NAMES.get(bad[0][0]["type"], bad[0][0]["type"])] += 1
                if not hreasons:
                    counts["blocks_value_only"] += 1
                    residue.append(dict(scope="residue", key="%s#%d" % (rel, _i + 1),
                                        count=len(bad),
                                        note="%s;start=%d;tags=%d" % (pkg.class_name_of(pkg.exports[_i]),
                                                                      start, len(marks))))
            else:
                counts["blocks_exact"] += 1
        counts["files"] += 1
        if rel.startswith("Maps"):
            counts["maps"] += 1
        else:
            counts["checkpoints"] += 1
        total.update({k: v for k, v in counts.items() if k != "files"})
        total["files"] += 1
        row = dict(scope="file", key=rel, count=counts["tags"], note="")
        row["_counts"] = counts
        file_rows.append(row)
    return total, cls, structs, struct_sizes, file_rows, native_width, lone, residue


def _type_of(name):
    """The tag type number a rendered type name stands for."""
    for num, spelled in TYPE_NAMES.items():
        if spelled == name:
            return num
    return -1


def rows_of(total, cls, structs, struct_sizes, file_rows, native_width, rules, native, lone, residue=()):
    rows = []
    tags = total["tags"]
    exact = sum(c["exact"] for c in cls.values())
    inexact = sum(c["inexact"] for c in cls.values())
    opened = sum(c["open"] for c in cls.values())
    impossible = sum(c["impossible"] for c in cls.values())
    rows.append(dict(scope="census", key="files", count=total["files"],
                     note="maps=%d;checkpoints=%d" % (total["maps"], total["checkpoints"])))
    rows.append(dict(scope="census", key="tags", count=tags, note=""))
    rows.append(dict(scope="census", key="exact", count=exact, note="the value consumes exactly its Size"))
    rows.append(dict(scope="census", key="inexact", count=inexact, note="the value does not"))
    rows.append(dict(scope="census", key="open", count=opened, note="the type has no self-describing rule"))
    rows.append(dict(scope="census", key="impossible", count=impossible,
                     note="a type no property class in this build reports"))
    rows.append(dict(scope="census", key="blocks", count=total["blocks"], note=""))
    rows.append(dict(scope="census", key="blocks_exact", count=total["blocks_exact"],
                     note="every checkable value in the block decodes"))
    rows.append(dict(scope="census", key="blocks_inexact", count=total["blocks_inexact"], note=""))
    rows.append(dict(scope="census", key="blocks_header_refused", count=total["blocks_header_refused"],
                     note="the header test refuses the block (the tag-encode refusal)"))
    rows.append(dict(scope="census", key="blocks_value_only", count=total["blocks_value_only"],
                     note="a value still does not decode where the locator put the start (the residue)"))
    rows.append(dict(scope="census", key="lone_inexact", count=sum(lone.values()),
                     note="inexact value(s) that sit in a block where every other value decodes"))
    rows.append(dict(scope="census", key="blocks_relocated", count=total["blocks_relocated"],
                     note="the header test's start held a value its class cannot produce and another start does not"))
    rows.append(dict(scope="census", key="native_structs", count=len(native_width),
                     note="the names the serialiser skips: %s" % ", ".join(sorted(native_width))))
    for name in sorted(native_width):
        held = struct_sizes.get(name) or {}
        rows.append(dict(scope="native", key=name, count=native_width[name],
                         note="%d of %d value(s)" % (held.get(native_width[name], 0), sum(held.values()))))
    for name in sorted(cls):
        prop = TYPE_CLASS.get(_type_of(name))
        rule = rules.get(prop, {}).get("rule", "no property class in this build")
        rows.append(dict(scope="class", key=name, count=sum(cls[name].values()),
                         note="%s; exact=%d; inexact=%d; open=%d"
                              % (rule, cls[name]["exact"], cls[name]["inexact"], cls[name]["open"])))
        for verdict in ("exact", "inexact", "open", "impossible"):
            rows.append(dict(scope="class-" + verdict, key=name, count=cls[name][verdict], note=""))
        rows.append(dict(scope="class-lone", key=name, count=lone.get(name, 0), note=""))
    for name in sorted(structs, key=lambda n: -sum(structs[n].values())):
        where = "native %d" % native_width[name] if name in native_width else "tagged"
        sizes = " ".join("%d:%d" % kv for kv in sorted(struct_sizes[name].items())[:6])
        rows.append(dict(scope="struct", key=name, count=sum(structs[name].values()),
                         note="%s; exact=%d; inexact=%d; sizes %s"
                              % (where, structs[name]["exact"], structs[name]["inexact"], sizes)))
    for row in residue:
        rows.append(row)
    for row in file_rows:
        counts = row.pop("_counts")
        row["note"] = "blocks=%d;inexact_values=%d;relocated=%d" % (
            counts["blocks"], counts["inexact_values"], counts["blocks_relocated"])
        rows.append(row)
    return rows


# ---------------------------------------------------------------- artifact and page

def write_artifact(path, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS, extrasaction="ignore")
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in FIELDS})
    print("wrote %s (%d rows)" % (path, len(rows)))


def read_artifact(path=ARTIFACT):
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def render(rows):
    def scope(name):
        return {r["key"]: r for r in rows if r["scope"] == name}

    census = {k: int(v["count"]) for k, v in scope("census").items()}
    classes = scope("class")
    exact = scope("class-exact")
    inexact = scope("class-inexact")
    opened = scope("class-open")
    impossible = scope("class-impossible")
    structs = scope("struct")
    out = []
    out.append("**Every value is decoded by its class's own rule, and the decode has to consume exactly "
               "the `Size` its header declares.** Of the %s tag(s) in %s file(s), %s are exact, %s are "
               "not, %s carry a type no property class in this build reports (`Invalid`, the legacy "
               "`String`, and `Vector`/`Rotator`, whose properties are `Struct`s of those names), and %s "
               "carry a type whose value is not self-describing (`Map`, and an `Array`'s elements, whose "
               "`Inner` the tag does not name)."
               % (format(census["tags"], ","), census["files"], format(census["exact"], ","),
                  format(census["inexact"], ","), format(census["impossible"], ","),
                  format(census["open"], ",")))
    out.append("")
    out.append("| class | tags | the rule its body spells | exact | inexact | open | impossible |")
    out.append("|---|---:|---|---:|---:|---:|---:|")
    for name in sorted(classes, key=lambda n: -int(classes[n]["count"])):
        out.append("| %s | %s | %s | %s | %s | %s | %s |" % (
            name, format(int(classes[name]["count"]), ","),
            classes[name]["note"].split("; exact=")[0],
            format(int(exact.get(name, {"count": 0})["count"]), ","),
            format(int(inexact.get(name, {"count": 0})["count"]), ","),
            format(int(opened.get(name, {"count": 0})["count"]), ","),
            format(int(impossible.get(name, {"count": 0})["count"]), ",")))
    out.append("")
    native = sorted(n for n in structs if structs[n]["note"].startswith("native"))
    tagged = [n for n in structs if not structs[n]["note"].startswith("native")]
    out.append("**A struct value is a tagged block, unless the struct is one of the three the serialiser "
               "skips.** `0x000795f0` compares `[Inner+0x20]` with name indices `0x57`, `0x58` and `0x5a`, "
               "which the seed table (`inventory/gname_seed.csv`) spells %s -- and the corpus splits the "
               "same way: %d struct name(s) hold a fixed width (%s) and never walk as a tag list, %d walk "
               "as tag lists (%s), and no name does both."
               % (", ".join("`%s`" % n for n in native) if native else "nothing",
                  len(native),
                  ", ".join("`%s` %s" % (n, structs[n]["note"].split(";")[0]) for n in native),
                  len(tagged), ", ".join("`%s`" % n for n in tagged[:8])))
    out.append("")
    lone = scope("class-lone")
    loud = sorted((n for n in lone if int(lone[n]["count"])), key=lambda n: -int(lone[n]["count"]))
    out.append("**The failures cluster, which is what says the rules are not simply wrong.** Only %s of "
               "the %s inexact value(s) sit in a block where every other value decodes%s -- a rule that "
               "missed one type would fail on lone values too, so these are parses that began inside a "
               "value and not a class whose layout is still unread."
               % (format(census["lone_inexact"], ","), format(census["inexact"], ","),
                  (" (%s)" % ", ".join("%s %d" % (n, int(lone[n]["count"])) for n in loud)) if loud else ""))
    out.append("")
    out.append("**The value is the block start's second opinion, and the locator now takes it.** Of the "
               "%s block(s) that walk, the header test refuses %s outright (a tag the writer's rules cannot "
               "produce, `tag-encode`'s refusal); %s more are blocks whose every header re-encodes and whose "
               "start the value test moves -- the header's candidate held a value its class cannot produce "
               "and another start's did not. What is left is %s block(s) where a value still does not decode: "
               "no start in the prefix window fixes those, so they are counted rather than guessed away. A "
               "header is only self-consistent with itself; the value's declared `Size` is the number a parse "
               "inside another value cannot satisfy."
               % (format(census["blocks"], ","), format(census["blocks_header_refused"], ","),
                  format(census["blocks_relocated"], ","), format(census["blocks_value_only"], ",")))
    out.append("")
    out.append("What stays open is what the rule cannot reach, and it is counted above rather than "
               "guessed at: an `Array`'s count decodes but its elements are the `Inner`'s rule, a "
               "`FixedArray`'s dimension is the property's own field and not on disk at all, a `Map` "
               "value is the stub at `0x000794a0` that writes three bytes and no field, and the legacy "
               "`String` has no class to read.")
    return "\n".join(out)


def splice(path, block):
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    if DOC_START not in text or DOC_END not in text:
        sys.exit("markers not found in %s" % path)
    head, rest = text.split(DOC_START, 1)
    _old, tail = rest.split(DOC_END, 1)
    return head + DOC_START + "\n" + block + "\n" + DOC_END + tail


def write_doc(path, rows):
    body = splice(path, render(rows))
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(body)
    print("wrote %s" % path)


def check_doc(path, rows):
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    if DOC_START not in text or DOC_END not in text:
        sys.exit("markers not found in %s" % path)
    if splice(path, render(rows)) != text:
        sys.exit("FAIL value-layouts: %s is stale: run --write-doc" % path)
    print("ok   value-layouts: %s matches the artifact" % path)


def check_artifact(rows):
    census = {r["key"]: int(r["count"]) for r in rows if r["scope"] == "census"}
    for verdict in ("exact", "inexact", "open", "impossible"):
        held = sum(int(r["count"]) for r in rows if r["scope"] == "class-" + verdict)
        if held != census[verdict]:
            sys.exit("FAIL value-layouts: the class-%s rows hold %d, the census says %d"
                     % (verdict, held, census[verdict]))
    if census["exact"] + census["inexact"] + census["open"] + census["impossible"] != census["tags"]:
        sys.exit("FAIL value-layouts: exact + inexact + open + impossible does not equal the tag total")
    held_lone = sum(int(r["count"]) for r in rows if r["scope"] == "class-lone")
    if held_lone != census["lone_inexact"]:
        sys.exit("FAIL value-layouts: the class-lone rows hold %d, the census says %d"
                 % (held_lone, census["lone_inexact"]))
    if census["lone_inexact"] > census["inexact"]:
        sys.exit("FAIL value-layouts: more lone inexact values than inexact values")
    blocks = sum(int(r["count"]) for r in rows if r["scope"] == "file")
    if blocks != census["tags"]:
        sys.exit("FAIL value-layouts: the file rows hold %d tag(s), the census says %d"
                 % (blocks, census["tags"]))
    if census["blocks_relocated"] + census["blocks_value_only"] > census["blocks"]:
        sys.exit("FAIL value-layouts: more relocated or residue blocks than blocks")
    held_residue = sum(1 for r in rows if r["scope"] == "residue")
    if held_residue != census["blocks_value_only"]:
        sys.exit("FAIL value-layouts: %d residue row(s), the census says %d"
                 % (held_residue, census["blocks_value_only"]))
    tagged = sum(int(r["count"]) for r in rows if r["scope"] == "struct")
    held = sum(int(r["count"]) for r in rows if r["scope"] == "class" and r["key"] == "Struct")
    if tagged > held:
        sys.exit("FAIL value-layouts: the struct rows hold %d tag(s) of %d Struct tags" % (tagged, held))
    print("ok   value-layouts: %d tags, %d exact, %d inexact, %d open, %d impossible, rows add up"
          % (census["tags"], census["exact"], census["inexact"], census["open"], census["impossible"]))


def report(rows=ARTIFACT):
    print(render(read_artifact(rows)))


def check():
    """Synthetic values only, no dump and no writes: every rule, the two widths the bodies spell, and the
    artifact/doc round trip -- including that a value the rule cannot produce is called inexact."""
    assert ps.read_index(b"\x00", 0) == (0, 1)
    assert ps.read_index(b"\x40\x01", 0) == (0x40, 2)
    # a one-byte empty FString, a seven-character name and a two-byte index
    assert decode(13, b"\x00", "", {}) == ("exact", "0 chars")
    assert decode(13, b"\x07Abydos\x00", "", {}) == ("exact", "7 chars")
    assert decode(13, b"\x07Abydos", "", {})[0] == "inexact"
    assert decode(13, b"\x83" + bytes(6), "", {}) == ("exact", "3 chars")  # sign bit: two-byte chars
    assert decode(13, b"\x83" + bytes(5), "", {})[0] == "inexact"
    assert decode(5, b"\x02", "", {}) == ("exact", "1-byte index")
    assert decode(5, b"\x4f\x01", "", {}) == ("exact", "2-byte index")
    assert decode(5, b"\x4f\x01\x00", "", {})[0] == "inexact"
    assert decode(1, b"\x2a", "", {})[0] == "exact"
    assert decode(2, b"\x2a\x00\x00\x00", "", {})[0] == "exact"
    assert decode(2, b"\x2a\x00", "", {})[0] == "inexact"
    assert decode(3, b"", "", {})[0] == "exact"
    assert decode(14, b"\x01\x00\x00\x02", "", {})[0] == "open"
    assert decode(0, b"\x81", "", {}) == ("impossible", "no property class")
    assert decode(11, b"\x01\x00\x00\x02", "", {})[0] == "impossible"
    # a native struct's width is the one size it holds; a tagged one walks its own tag list
    assert decode(10, bytes(12), "Vector", {"Vector": 12})[0] == "exact"
    assert decode(10, bytes(11), "Vector", {"Vector": 12})[0] == "inexact"
    block = bytes([0x04, 0x22]) + struct.pack("<i", 7) + b"\x00"   # one Int tag, then the terminator
    assert decode(10, block, "Range", {})[0] == "exact"
    assert decode(10, block[:-1], "Range", {})[0] == "inexact"
    # the body's own immediate push is the width
    fake = {0x100: ("mov", "ecx,DWORD PTR [esp+0x8]", 5), 0x105: ("push", "0x4", 1),
            0x106: ("push", "ecx", 1), 0x107: ("call", "DWORD PTR [eax+0x4]", 2),
            0x109: ("ret", "", 1)}
    assert op_width(fake, 0x100) == 4
    fake[0x105] = ("push", "ecx", 1)
    assert op_width(fake, 0x100) is None
    # the rule names the width it measured, not one it was told
    assert "four" in rule_of("call:0x37110", {0x37110: 4}) or "4" in rule_of("call:0x37110", {0x37110: 4})
    assert "FString" in rule_of("call:0x58850", {}) or "compact length" in rule_of("call:0x58850", {})
    assert "compact count" in rule_of("call:0x637e0 call:0x55ed0 call:0x2c800 vt:0xa0", {})
    assert "ArrayDim" in rule_of("vt:0xa0", {})
    assert rule_of("", {}) .startswith("nothing")
    rows = [
        dict(scope="census", key="files", count=2, note="maps=2;checkpoints=0"),
        dict(scope="census", key="tags", count=6, note=""),
        dict(scope="census", key="exact", count=5, note=""),
        dict(scope="census", key="inexact", count=1, note=""),
        dict(scope="census", key="open", count=0, note=""),
        dict(scope="census", key="impossible", count=0, note=""),
        dict(scope="census", key="blocks", count=2, note=""),
        dict(scope="census", key="blocks_exact", count=1, note=""),
        dict(scope="census", key="blocks_inexact", count=1, note=""),
        dict(scope="census", key="blocks_header_refused", count=0, note=""),
        dict(scope="census", key="blocks_value_only", count=1, note=""),
        dict(scope="census", key="lone_inexact", count=0, note=""),
        dict(scope="census", key="blocks_relocated", count=1, note=""),
        dict(scope="census", key="native_structs", count=1, note="struct names one size only: Vector"),
        dict(scope="class", key="Int", count=3, note="4 bytes (`0x00037110`); exact=3; inexact=0; open=0"),
        dict(scope="class-exact", key="Int", count=3, note=""),
        dict(scope="class-impossible", key="Int", count=0, note=""),
        dict(scope="class-lone", key="Int", count=0, note=""),
        dict(scope="class-inexact", key="Int", count=0, note=""),
        dict(scope="class-impossible", key="Int", count=0, note=""),
        dict(scope="class-lone", key="Int", count=0, note=""),
        dict(scope="class-open", key="Int", count=0, note=""),
        dict(scope="class-impossible", key="Int", count=0, note=""),
        dict(scope="class-lone", key="Int", count=0, note=""),
        dict(scope="class", key="Str", count=2, note="a compact length; exact=1; inexact=1; open=0"),
        dict(scope="class-exact", key="Str", count=1, note=""),
        dict(scope="class-inexact", key="Str", count=1, note=""),
        dict(scope="class-open", key="Str", count=0, note=""),
        dict(scope="class-impossible", key="Str", count=0, note=""),
        dict(scope="class-lone", key="Str", count=0, note=""),
        dict(scope="class", key="Struct", count=1, note="tagged walk; exact=1; inexact=0; open=0"),
        dict(scope="class-exact", key="Struct", count=1, note=""),
        dict(scope="class-inexact", key="Struct", count=0, note=""),
        dict(scope="class-open", key="Struct", count=0, note=""),
        dict(scope="class-impossible", key="Struct", count=0, note=""),
        dict(scope="class-lone", key="Struct", count=0, note=""),
        dict(scope="struct", key="Vector", count=1, note="native 12; exact=1; inexact=0; sizes 12:1"),
        dict(scope="file", key="Maps/M00.unr", count=6, note="blocks=2;inexact_values=1;relocated=1"),
        dict(scope="residue", key="Maps/M00.unr#7", count=1, note="Mover;start=12;tags=24"),
    ]
    check_artifact(rows)

    def _bad(mutate):
        bad = [dict(r) for r in rows]
        mutate(bad)
        try:
            check_artifact(bad)
        except SystemExit:
            return
        raise AssertionError("check_artifact accepted an artifact whose rows do not add up")

    def _one_exact(bad):
        for r in bad:
            if r["scope"] == "class-exact":
                r["count"] = int(r["count"]) - 1
                return

    def _one_file(bad):
        for r in bad:
            if r["scope"] == "file":
                r["count"] = int(r["count"]) - 1
                return

    _bad(_one_exact)
    _bad(_one_file)
    block = render(rows)
    assert render(rows) == block
    tmp = tempfile.mkdtemp()
    try:
        page = os.path.join(tmp, "page.md")
        with open(page, "w", encoding="utf-8") as fh:
            fh.write("# page\n\n" + DOC_START + "\nold\n" + DOC_END + "\n\ntail\n")
        write_doc(page, rows)
        check_doc(page, rows)
        with open(page, encoding="utf-8") as fh:
            text = fh.read().replace("are exact", "are all exact")
        with open(page, "w", encoding="utf-8") as fh:
            fh.write(text)
        try:
            check_doc(page, rows)
        except SystemExit as exc:
            assert "stale" in str(exc), exc
        else:
            raise AssertionError("check_doc accepted a page that had been edited")
    finally:
        for name in os.listdir(tmp):
            os.unlink(os.path.join(tmp, name))
        os.rmdir(tmp)
    print("ok   value-layouts: compact int, FString, index, struct and width rules, artifact and doc gates")


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("dump", nargs="?", default=DEFAULT_DUMP)
    ap.add_argument("--sweep", action="store_true", help="walk every Maps/*.unr and Save/*.sgs")
    ap.add_argument("--limit", type=int, default=None, help="only the first N packages (a smoke run)")
    ap.add_argument("-o", "--out", default=None, help="artifact directory")
    ap.add_argument("--report", action="store_true", help="re-render the page block from the artifact")
    ap.add_argument("--write-doc", metavar="PAGE", default=None)
    ap.add_argument("--check-doc", metavar="PAGE", default=None)
    ap.add_argument("--check", action="store_true", help="synthetic self-test, no dump, writes nothing")
    args = ap.parse_args()

    if args.check:
        check()
        return
    if args.check_doc:
        check_artifact(read_artifact())
        check_doc(args.check_doc, read_artifact())
        return
    if args.write_doc:
        rows = read_artifact()
        check_artifact(rows)
        write_doc(args.write_doc, rows)
        return
    if args.report:
        report()
        return
    if not args.sweep:
        ap.error("one of --sweep, --report, --write-doc, --check-doc or --check is required")

    rules, widths = class_rules(os.path.join(args.dump, "default.xbe"))
    native = native_names()
    total, cls, structs, struct_sizes, file_rows, native_width, lone, residue = sweep(
        args.dump, args.limit, native)
    # the three names the image skips have to be the three the corpus finds at one size
    if native and native != set(native_width):
        sys.exit("FAIL value-layouts: the image skips %s, the corpus holds a width for %s"
                 % (sorted(native), sorted(native_width)))
    for name in sorted(native_width):
        held = struct_sizes.get(name) or {}
        seen = sum(held.values())
        if seen and held.get(native_width[name], 0) < 0.99 * seen:
            sys.exit("FAIL value-layouts: %s is native %d on only %d of %d value(s), so the native body is "
                     "not fixed-width" % (name, native_width[name], held.get(native_width[name], 0), seen))
    rows = rows_of(total, cls, structs, struct_sizes, file_rows, native_width, rules, native, lone, residue)
    check_artifact(rows)
    out = args.out or os.path.join(ROOT, "inventory")
    write_artifact(os.path.join(out, "value_layouts.csv"), rows)
    print(render(rows))


if __name__ == "__main__":
    main()
