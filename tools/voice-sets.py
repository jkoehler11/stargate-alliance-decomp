#!/usr/bin/env python3
"""How many voices a voice pack holds (PLAN 3.10, 8.3; `docs/systems/AUDIO.md`).

`docs/systems/AUDIO.md`'s last open line was *"how many voices a pack holds, and the per-sound `VoiceDesc`
the selector matches on"*. `#356` measured which pack a pawn speaks with; this reads what the pack contains.

**A pack holds exactly one default property, and it is an array.** `StargateVoicePack` declares
`VoiceSets` (`array<VoiceSet>`), and a `*VoicePack` subclass's payload is a class record followed by a
tagged block holding that single tag. `tools/class-defaults.py` rejects it because its `candidates()` floor is
`len(props) < 2` -- a one-property block never becomes a candidate -- so the emitter's locator fell back to a
mis-parse and the class was reported `incomplete` with 2-3 unaccounted names
(`inventory/class_defaults_misses.csv`, 20 `*VoicePack` rows, nine of them `single-declared` on `VoiceSets`).

**The block is read with `tools/prop-stream.py`, not with a scanner.** Two mistakes came from hand-rolling
the tag walk here: a struct's *name index* precedes its size class, so `VDesc` looked 19 bytes long instead of
66 and the next tag started inside its blob; and `VDesc`'s blob can spell a tag-shaped coincidence
(`VSounds` + Array info) that a scanner finds before the real tag. `prop_stream.read_block` is the reader
measured from the game's own code, so the tool locates the block that *terminates at the payload's last byte*
and takes its properties from that.

**A sound reference is an import, not an export.** `StargateContent.u` holds the sets; the sounds they name
live in the character's own sound package (`Carter`, `Oneill`, ...), so `VSounds`' elements are negative
compact indices (`-n` is import `n`), and every one of them resolves to a `Sound`. Counting positive refs only
is what made a correctly parsed array read as zero sounds.

    python3 tools/voice-sets.py --system DUMP/System -o inventory/voice_sets.csv
    python3 tools/voice-sets.py --system DUMP/System --write-doc docs/systems/AUDIO.md
    python3 tools/voice-sets.py --check          # synthetic, no dump, writes nothing

Columns: `package,class,status,slots,sets,empty_slots,sounds,max_sounds,desc_sets,bare_sets,partial_sets`.

* `status` is `measured` when the class's block carries a `VoiceSets` array, `silent` when it carries none at
  all (its payload is the class record alone: 10 of the 25 packs are exactly 108 bytes), and `no-block` when
  the array does not decode (a count/size disagreement).
* `slots` is the array's count, `sets` the slots that resolve to a `VoiceSet` export, `empty_slots` the zeros,
  `sounds` the `Sound` references over those sets, `max_sounds` the widest single set.
* `desc_sets` counts the sets whose `VSoundsDesc` array holds one descriptor per sound (the invariant the
  selector's per-sound match rests on); `partial_sets` the sets that carry sounds but no descriptor array
  (`VLoadPriorityLow`/`VOnlyPlayIfLoaded`/`VPreCaching` instead); `bare_sets` the `VDesc`-only templates.
"""
import argparse
import csv
import importlib.util
import os
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COLUMNS = ["package", "class", "status", "slots", "sets", "empty_slots", "sounds", "max_sounds",
           "desc_sets", "bare_sets", "partial_sets"]
DOC_MARK = ("<!-- generated:voice-sets -->", "<!-- /generated:voice-sets -->")
BASE = "VoicePack"                 # Engine.VoicePack, the root of every pack
ARRAY_TYPE = 9


def load_tool(name, filename):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, "tools", filename))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def prop_stream():
    """The repo's measured property-block reader (`tools/prop-stream.py`)."""
    return load_tool("prop_stream", "prop-stream.py")


# ----------------------------------------------------------------------------------- the block, and its arrays
def block_of(ps, buf, pkg, want=None):
    """(properties, start) of the property block that ends exactly at the payload's last byte.

    UE2 objects do not all begin with their property block -- a class serialises native data first, and a
    `*VoicePack` carries its class record -- so the start is found by requiring the block to terminate at
    the end, and `want` pins it to the block that holds a named property. That is what keeps a class record
    from parsing as a property list, and it is why nothing here scans for tag-shaped bytes.
    """
    best = None
    for start in range(0, min(len(buf), 240)):
        try:
            props, end, reason = ps.read_block(buf[start:], pkg, False)
        except Exception:
            continue
        if reason != "None" or not props or start + end != len(buf):
            continue
        if want and not any(p["name"] == want for p in props):
            continue
        if any(p["name"] == "?" for p in props):        # a start inside a class record invents names
            continue
        for prop in props:                              # offsets are relative to the slice, not the payload
            prop["value_offset"] += start
        if best is None or len(props) > len(best[0]):
            best = (props, start)
    return best if best else (None, None)


def array_of(ps, buf, prop):
    """(count, elements, ok) of an ArrayProperty: a compact count, then that many compact references.

    `ok` is true when the element walk lands exactly on the property's own size, which is the check that
    says the value really is a list of references rather than a struct blob read as one.
    """
    off = prop["value_offset"]
    try:
        count, off = ps.read_index(buf, off)
        if count < 0 or count > len(buf):
            return 0, [], False
        elems = []
        for _ in range(count):
            v, off = ps.read_index(buf, off)
            elems.append(v)
    except IndexError:
        return 0, [], False
    return count, elems, off == prop["value_offset"] + prop["size"]


def ref_of(pkg, ref):
    """(class, object name) a compact object reference names; `None` for 0 or an out-of-range index.

    Positive references are exports of this package; **negative ones are imports** (`-n` is import `n`),
    which is where a voice set's sounds live.
    """
    if ref == 0:
        return None
    if 0 < ref <= len(pkg.exports):
        exp = pkg.exports[ref - 1]
        return pkg.class_name_of(exp), pkg.name(exp["name"])
    imp_index = -ref - 1
    imports = getattr(pkg, "imports", [])
    if 0 <= imp_index < len(imports):
        imp = imports[imp_index]
        return pkg.name(imp["class"]), pkg.name(imp["name"])
    return None


# ------------------------------------------------------------------------------------- the corpus walk
def supers(path=None):
    """{class: super} from the committed class table (no dump)."""
    out = {}
    path = path or os.path.join(ROOT, "inventory", "classes.csv")
    if not os.path.isfile(path):
        return out
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            out[row["class"]] = row.get("super") or ""
    return out


def under_base(cls, tree, base=BASE):
    """Is `cls` a pack? Walk the committed super chain rather than guessing from the name."""
    seen, cur = set(), cls
    while cur and cur not in seen:
        if cur == base:
            return True
        seen.add(cur)
        cur = tree.get(cur, "")
    return False


def sounds_of(ps, pkg, exp, cache):
    """(sounds, desc_agrees, kind) of one `VoiceSet`: its `VSounds` and `VSoundsDesc` counts.

    `kind` is `full` for the three-property block, `partial` when the descriptor array is absent (some sets
    carry load-priority flags instead), `bare` when the block holds a `VDesc` alone (a template), and
    `bad` when the sound array does not decode or a reference does not resolve to a `Sound`.
    """
    key = exp["index"]
    if key in cache:
        return cache[key]
    buf = pkg.data[exp["offset"]:exp["offset"] + exp["size"]]
    props, _start = block_of(ps, buf, pkg)
    if props is None:
        cache[key] = (0, False, "bad")
        return cache[key]
    by_name = {}
    for p in props:
        by_name.setdefault(p["name"], p)
    snd = by_name.get("VSounds")
    if snd is None:
        cache[key] = (0, False, "bare")
        return cache[key]
    count, elems, good = array_of(ps, buf, snd)
    status = "full" if "VSoundsDesc" in by_name else "partial"
    sounds = 0
    if not good:
        status = "bad"
    for v in elems:
        if v == 0:
            continue
        resolved = ref_of(pkg, v)
        if resolved and resolved[0] == "Sound":
            sounds += 1
        else:
            status = "bad"
    agrees = False
    if status != "bad" and "VSoundsDesc" in by_name:
        try:
            descs, _off = ps.read_index(buf, by_name["VSoundsDesc"]["value_offset"])
        except IndexError:
            descs = -1
        agrees = descs == count
        if not agrees:
            status = "bad"
    cache[key] = (sounds, agrees, status)
    return cache[key]


def measure(ps, pkg, pkg_name, cls, exp, cache):
    """One pack's row; `note` is non-empty when something in it did not reconcile."""
    buf = pkg.data[exp["offset"]:exp["offset"] + exp["size"]]
    props, _start = block_of(ps, buf, pkg, "VoiceSets")
    if props is None:
        return [pkg_name, cls, "silent", 0, 0, 0, 0, 0, 0, 0, 0], None
    sets_prop = None
    for p in props:
        if p["name"] == "VoiceSets":
            sets_prop = p
            break
    count, elems, good = array_of(ps, buf, sets_prop)
    if not good:
        return [pkg_name, cls, "no-block", 0, 0, 0, 0, 0, 0, 0, 0], "array=%d" % count
    slots, sets, empty, sounds, widest = 0, 0, 0, 0, 0
    desc_sets = bare_sets = partial_sets = bad = 0
    for v in elems:
        slots += 1
        if v == 0:
            empty += 1
            continue
        resolved = ref_of(pkg, v)
        if not resolved:
            bad += 1
            continue
        if resolved[0] != "VoiceSet":
            bad += 1
            continue
        sub = pkg.exports[v - 1] if v > 0 else None
        if sub is None:
            bad += 1
            continue
        sets += 1
        n_sound, agrees, kind = sounds_of(ps, pkg, sub, cache)
        sounds += n_sound
        widest = max(widest, n_sound)
        if kind == "bad":
            bad += 1
        elif kind == "bare":
            bare_sets += 1
        elif kind == "partial":
            partial_sets += 1
        if agrees:
            desc_sets += 1
    status = "measured" if not bad else "no-block"
    row = [pkg_name, cls, status, slots, sets, empty, sounds, widest, desc_sets, bare_sets, partial_sets]
    return row, (None if not bad else "bad=%d" % bad)


def sweep(system_dir, tree):
    ps = prop_stream()
    ue2pkg = load_tool("ue2pkg", "ue2pkg.py")
    rows, notes, cache = [], {}, {}
    for fname in sorted(f for f in os.listdir(system_dir) if f.lower().endswith(".u")):
        pkg = ue2pkg.Package(os.path.join(system_dir, fname))
        pkg.read_names()
        pkg.read_imports()
        pkg.read_exports()
        pkg_name = fname[:-2]
        for exp in pkg.exports:
            if pkg.class_name_of(exp) != "Class":
                continue
            cls = pkg.name(exp["name"])
            if not under_base(cls, tree):
                continue
            row, note = measure(ps, pkg, pkg_name, cls, exp, cache)
            rows.append(row)
            if note:
                notes[cls] = note
    rows.sort(key=lambda r: (r[0], r[1]))
    return rows, notes


# ------------------------------------------------------------------------------------------ the page
def render(rows):
    """The CSV as it is committed: no field here can need quoting, and the header is the tool's own order."""
    return "\n".join(",".join(str(c) for c in row) for row in [COLUMNS] + rows) + "\n"


def summary(rows):
    counted = [r for r in rows if r[2] == "measured"]
    return {
        "classes": len(rows),
        "measured": len(counted),
        "silent": sum(1 for r in rows if r[2] == "silent"),
        "no_block": sum(1 for r in rows if r[2] == "no-block"),
        "slots": sum(r[3] for r in counted),
        "sets": sum(r[4] for r in counted),
        "empty": sum(r[5] for r in counted),
        "sounds": sum(r[6] for r in counted),
        "desc_sets": sum(r[8] for r in counted),
        "bare_sets": sum(r[9] for r in counted),
        "partial_sets": sum(r[10] for r in counted),
    }


def doc_block(rows):
    counts = summary(rows)
    counted = sorted([r for r in rows if r[2] == "measured"], key=lambda r: (-r[6], r[1]))
    lines = ["", "### How many voices a pack holds", "",
             "Generated by `tools/voice-sets.py` (`make voice-sets`); `--check` in `make ci` keeps it honest.",
             "",
             "**%d of the %d pack classes carry a `VoiceSets` array**, %d slots over %d sets and **%d `Sound` "
             "references**; %d empty slot%s, and %d of the %d classes set no array at all (their payload is "
             "the class record alone)."
             % (counts["measured"], counts["classes"], counts["slots"], counts["sets"], counts["sounds"],
                counts["empty"], "" if counts["empty"] == 1 else "s", counts["silent"], counts["classes"]),
             "",
             "| pack | sets | slots | empty | sounds | widest set |", "|---|---:|---:|---:|---:|---:|"]
    for r in counted[:12]:
        lines.append("| `%s` | %d | %d | %d | %d | %d |" % (r[1], r[4], r[3], r[5], r[6], r[7]))
    lines.append("")
    if counted:
        top = counted[0]
        sg1 = [r for r in counted if r[6] >= 200]
        lines.append("The four SG-1 packs hold **%d of the %d sounds** in %d of the %d sets; the enemy and NPC "
                     "packs hold single-digit sets of 2-8 sounds each. `%s` is the widest single pack."
                     % (sum(r[6] for r in sg1), counts["sounds"], sum(r[4] for r in sg1), counts["sets"],
                        top[1]))
        lines.append("")
        lines.append("The sets themselves are `VoiceSet` exports of `StargateContent.u`, and their shape splits "
                     "three ways: **%d carry the full `VDesc` + `VSounds` + `VSoundsDesc` block and hold one "
                     "descriptor per sound**; %d carry sounds but no descriptor array (load-priority flags "
                     "instead); %d are `VDesc`-only templates. Every sound reference is a *negative* compact "
                     "index -- an import, not an export: the sounds live in the character's own package, which "
                     "is why `Carter` and `Oneill` carry 260-410 effects of their own beside their voice sets."
                     % (counts["desc_sets"], counts["partial_sets"], counts["bare_sets"]))
    lines.append("")
    return "\n".join(lines)


def replace_block(text, block):
    begin, end = DOC_MARK
    i, j = text.find(begin), text.find(end)
    if i < 0 or j <= i:
        sys.exit("document has no %s ... %s block" % DOC_MARK)
    return text[: i + len(begin)] + block + "\n" + text[j:]


def emit(rows, args):
    counts = summary(rows)
    path = args.write_doc or args.check_doc
    if path:
        with open(path, encoding="utf-8") as fh:
            current = fh.read()
        fresh = replace_block(current, doc_block(rows))
        if args.check_doc:
            if fresh != current:
                print("FAIL %s: the generated block is stale (run --write-doc)" % path)
                return 1
            print("ok   voice-sets-doc: %s matches its rows" % path)
        else:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(fresh)
            print("ok   voice-sets-doc: wrote %s" % path)
    if args.out:
        with open(args.out, "w", newline="") as fh:
            fh.write(render(rows))
    elif not path:
        sys.stdout.write(render(rows))
    print("ok   voice-sets: %d pack class(es): %d measured (%d slots, %d sets, %d sounds), %d silent, "
          "%d unread" % (counts["classes"], counts["measured"], counts["slots"], counts["sets"],
                         counts["sounds"], counts["silent"], counts["no_block"]))
    return 0


# ----------------------------------------------------------------------------------------- the self-test
class FakePkg:
    """Just enough of `ue2pkg.Package` for the block reader: a name table, an export table, an import table."""

    def __init__(self, names, exports, imports=()):
        self.names = list(names)
        self.exports = exports
        self.imports = list(imports)
        self.data = b""

    def name(self, i):
        return self.names[i] if 0 <= i < len(self.names) else "?"

    def class_name_of(self, exp):
        return exp["class"]


def enc(v):
    """The compact index `prop_stream.read_index` reads, for building synthetic payloads."""
    if v < 0:
        return bytes([0x80 | ((-v) & 0x3F)])
    out = bytearray([v & 0x3F])
    v >>= 6
    while v:
        out[-1] |= 0x40
        out.append(v & 0x7F)
        v >>= 7
    return bytes(out)


def array_tag(name_idx, elems):
    """A synthetic ArrayProperty tag: name, info (Array + 1-byte size), size, count, elements."""
    body = enc(len(elems)) + b"".join(enc(e) for e in elems)
    return enc(name_idx) + bytes([5 << 4 | ARRAY_TYPE, len(body)]) + body


def struct_tag(name_idx, struct_idx, blob):
    """A synthetic StructProperty tag: name, info (Struct + 1-byte size), **struct name**, size, value."""
    return enc(name_idx) + bytes([5 << 4 | 10]) + enc(struct_idx) + bytes([len(blob)]) + blob


def check():
    """Synthetic: no dump, nothing written to the tree."""
    ps = prop_stream()
    names = ["None", "VDesc", "VSounds", "VSoundsDesc", "VoiceSets", "VoiceDesc", "Sound",
             "VLoadPriorityLow"]
    exports = [
        {"index": 0, "name": 2, "class": "VoiceSet", "offset": 0, "size": 0},
        {"index": 1, "name": 2, "class": "VoiceSet", "offset": 0, "size": 0},
        {"index": 2, "name": 0, "class": "Class", "offset": 0, "size": 0},
        {"index": 3, "name": 6, "class": "Sound", "offset": 0, "size": 0},
    ]
    pkg = FakePkg(names, exports)

    # A pack block: a class record in front, then exactly one VoiceSets tag, then the terminator.
    record = b"\x3f" * 20                               # a prefix that cannot parse as a property list
    buf = record + array_tag(4, [1, 0, 2]) + b"\x00"
    props, start = block_of(ps, buf, pkg, "VoiceSets")
    if props is None or start != len(record) or [p["name"] for p in props] != ["VoiceSets"]:
        print("FAIL a one-property block behind a class record was not located (%r, %r)" % (props, start))
        return 1
    count, elems, ok = array_of(ps, buf, props[0])
    if not ok or (count, elems) != (3, [1, 0, 2]):
        print("FAIL the synthetic array decoded as %r %r" % (count, elems))
        return 1
    # A body one element short of its own size is refused rather than read as references.
    bad = bytearray(array_tag(4, [1, 2]))
    bad[-1] = 99
    bprops, _bs = block_of(ps, bytes(bad) + b"\x00", pkg, "VoiceSets")
    if bprops and array_of(ps, bytes(bad) + b"\x00", bprops[0])[2]:
        print("FAIL an array whose size disagrees with its elements was accepted")
        return 1

    # **The two bugs this reader had**: VDesc's struct name precedes its size, and its blob can spell a
    # `VSounds` tag. A scanner mis-sized the struct and found the coincidence; the block reader does neither.
    inner = array_tag(2, [7])
    vdesc = struct_tag(1, 5, inner + bytes(20))
    desc_body = enc(3) + bytes(9)
    desc_tag = enc(3) + bytes([5 << 4 | ARRAY_TYPE, len(desc_body)]) + desc_body
    set_payload = vdesc + array_tag(2, [-1, -2, 4]) + desc_tag + b"\x00"
    pkg.data = set_payload
    sprops, _sstart = block_of(ps, set_payload, pkg)
    if sprops is None or [p["name"] for p in sprops] != ["VDesc", "VSounds", "VSoundsDesc"]:
        print("FAIL the VoiceSet block answered %r" % ([p["name"] for p in sprops or []],))
        return 1
    if sprops[0]["struct"] != "VoiceDesc":
        print("FAIL VDesc's struct name decoded as %r" % (sprops[0]["struct"],))
        return 1
    pkg.imports = [{"class": 6, "name": 0}, {"class": 6, "name": 0}]   # two imports of class `Sound`
    got = sounds_of(ps, pkg, {"index": 2, "offset": 0, "size": len(set_payload)}, {})
    if got != (3, True, "full"):
        print("FAIL a full VoiceSet block read as %r (expected 3 sounds, agreeing descs, full)" % (got,))
        return 1

    # A set with sounds but no descriptor array is `partial`; a `VDesc`-only template is `bare`.
    partial_payload = vdesc + array_tag(2, [-1]) + enc(7) + bytes([80]) + bytes(1) + b"\x00"
    pkg.data = partial_payload
    if sounds_of(ps, pkg, {"index": 2, "offset": 0, "size": len(partial_payload)}, {})[2] != "partial":
        print("FAIL a set without a descriptor array was not called partial")
        return 1
    bare_payload = vdesc + b"\x00"
    pkg.data = bare_payload
    got = sounds_of(ps, pkg, {"index": 1, "offset": 0, "size": len(bare_payload)}, {})
    if got[0] != 0 or got[2] != "bare":
        print("FAIL a VDesc-only template read as %r" % (got,))
        return 1

    # The pack row: three slots, one empty, two exports of which both resolve, a stubbed sound count.
    pkg.data = buf
    stub = {0: (2, True, "full"), 1: (3, True, "full")}
    real = globals()["sounds_of"]
    globals()["sounds_of"] = lambda p, pk, e, c: stub[e["index"]]
    row, note = measure(ps, pkg, "TestPkg", "TestVoicePack",
                        {"index": 0, "offset": 0, "size": len(buf)}, {})
    globals()["sounds_of"] = real
    if note or row[2:] != ["measured", 3, 2, 1, 5, 3, 2, 0, 0]:
        print("FAIL measure answered %r (%r)" % (row, note))
        return 1
    # A class that sets nothing is silent, and the walk keeps a non-pack out of the rows.
    tree = {"TestVoicePack": "VoicePack", "VoicePack": "", "OtherClass": "Actor"}
    if under_base("OtherClass", tree) or not under_base("TestVoicePack", tree):
        print("FAIL the pack test answered for the wrong class")
        return 1
    if measure(ps, FakePkg(names, exports), "TestPkg", "EmptyVoicePack",
               {"index": 2, "offset": 0, "size": 4}, {})[0][2] != "silent":
        print("FAIL a class with no VoiceSets tag was not called silent")
        return 1
    rows = [["TestPkg", "TestVoicePack", "measured", 3, 2, 1, 5, 3, 2, 0, 0],
            ["TestPkg", "EmptyVoicePack", "silent", 0, 0, 0, 0, 0, 0, 0, 0]]
    counts = summary(rows)
    if counts["measured"] != 1 or counts["sounds"] != 5 or counts["slots"] != 3 or counts["desc_sets"] != 2:
        print("FAIL summary answered %r" % (counts,))
        return 1
    doc = doc_block(rows)
    for needle in ("**1 of the 2 pack classes carry a `VoiceSets` array**",
                   "| `TestVoicePack` | 2 | 3 | 1 | 5 | 3 |"):
        if needle not in doc:
            print("FAIL doc_block is missing %r:\n%s" % (needle, doc))
            return 1
    stale = "a\n<!-- generated:voice-sets -->\nold\n<!-- /generated:voice-sets -->\nb\n"
    fresh = replace_block(stale, "\nnew")
    if "old" in fresh or "new" not in fresh:
        print("FAIL replace_block did not replace the marked block")
        return 1
    if render(rows).splitlines()[0] != ",".join(COLUMNS):
        print("FAIL render dropped the header")
        return 1
    print("ok   voice-sets: a one-property block is located behind its class record, a struct is sized past "
          "its name, a tag-shaped blob inside VDesc is not read as a tag, an import (negative) sound "
          "reference counts, a missing descriptor array and a template are told apart, and the doc block "
          "renders the totals")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--system", help="the dump's System directory")
    ap.add_argument("-o", "--out", help="write the CSV here (default: stdout)")
    ap.add_argument("--write-doc", metavar="PATH", help="regenerate the page's block between its markers")
    ap.add_argument("--check-doc", metavar="PATH", help="fail if the page's block is stale (in `make ci`)")
    ap.add_argument("--from-csv", metavar="PATH", default=os.path.join(ROOT, "inventory", "voice_sets.csv"),
                    help="with --check-doc/--write-doc and no --system: read this artifact instead")
    ap.add_argument("--check", action="store_true", help="synthetic self-test; needs no dump")
    args = ap.parse_args()

    if args.check:
        return check()
    if (args.write_doc or args.check_doc) and not args.system:
        if not os.path.exists(args.from_csv):
            sys.exit("voice-sets: %s does not exist" % args.from_csv)
        with open(args.from_csv, newline="", encoding="utf-8") as fh:
            rows = [[r[c] if c in ("package", "class", "status") else int(r[c]) for c in COLUMNS]
                    for r in csv.DictReader(fh)]
        if not rows:
            sys.exit("voice-sets: %s holds no rows" % args.from_csv)
        return emit(rows, args)
    if not args.system:
        sys.exit("voice-sets: pass --system <DUMP/System> (or --check)")
    rows, notes = sweep(args.system, supers())
    if not rows:
        sys.exit("voice-sets: no class under %s found" % BASE)
    for cls, note in sorted(notes.items()):
        print("note voice-sets: %s %s" % (cls, note))
    return emit(rows, args)


if __name__ == "__main__":
    sys.exit(main())
