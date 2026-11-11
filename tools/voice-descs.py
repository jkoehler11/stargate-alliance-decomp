#!/usr/bin/env python3
"""What a per-sound `VoiceDesc` carries, and what the selector reads off it (PLAN 8.3; `docs/systems/AUDIO.md`).

`docs/systems/AUDIO.md`'s remaining open line was *"the descriptor array is counted (one entry per sound
wherever the set carries it), but its fields are not decoded, so which tone, bucket or probability the
selector reads off it is still open"*. `#357` counted the descriptors; this reads them.

**A `VoiceSet` payload *begins* with its property block; a class payload does not.** A `*VoicePack` export is a
class record followed by its one tagged array, which is why `tools/voice-sets.py` has to hunt for the block
start. A `VoiceSet` is an ordinary object, so its payload is `VDesc` (Struct `VoiceDesc`, 66 bytes),
`VSounds` (array of `Sound` references) and `VSoundsDesc` (array of Struct `VoiceDesc`) from byte 0, and the
block terminates on the payload's last byte. Reading it from 0 is what keeps the *losing* candidate out: a
block located a few bytes late re-reads `VDesc`'s own tagged fields as top-level properties (seven of them
instead of three), which is how a `VDesc` field can look like a property of the set.

**A `VoiceDesc` array is `count` + one tagged block per element.** Unlike `VSounds` (whose elements are bare
compact references), a struct array writes each element as a `None`-terminated list of tagged fields, so the
element walk is `read_block` per element and the *only* honest check is that the last element lands exactly on
the property's size -- a wrong element type that happens to stop inside it is what a guess looks like.

**The descriptor carries match keys, not probabilities.** `VoiceSelector.SearchVoices` compares `Id`,
`Recipient`, `Subject` and `Tone` and reads `Type`/`GenericType` through `IsSubTypeOf`; `bForbidGeneric`
clears the generic pools for the rest of the set. Every probability lives on the `VoiceSelector`'s own
defaults (`FullMatchProba`, `ToneMatchProba`, ...), never in a descriptor -- so "which probability a descriptor
carries" has the answer *none*.

    python3 tools/voice-descs.py --system DUMP/System -o inventory/voice_descs.csv
    python3 tools/voice-descs.py --system DUMP/System --write-doc docs/systems/AUDIO.md
    python3 tools/voice-descs.py --check          # synthetic, no dump, writes nothing

Columns: `package,class,sets,descs,multi_tone,no_set_id,id_override,type_override,generic,sgeneric,forbid,
recipient,subject,customize,text,tone_none,tone_normal,tone_combat,tone_stealth,tone_critical,tone_other`.

* `sets` counts the pack's slots that resolve to a `VoiceSet` carrying a descriptor array; `descs` the
  descriptors in them (one per sound where the set is complete).
* `multi_tone` counts the sets whose descriptors name more than one `Tone` -- the per-sound array exists to
  hold the alternate takes of one line.
* `id_override`, `type_override`, `generic`, `forbid`, `recipient`, `subject`, `customize` and `text` count
  descriptors that set the corresponding field away from its fallback (`GetVoiceID`/`GetVoiceType`/... fall
  back to the set's own `VDesc` when the descriptor's field is empty).
* `tone_*` is the `Actor.EMsgTone` histogram over all descriptors.
"""
import argparse
import csv
import importlib.util
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COLUMNS = ["package", "class", "sets", "descs", "multi_tone", "no_set_id", "id_override", "type_override",
           "generic", "sgeneric", "forbid", "recipient", "subject", "customize", "text",
           "tone_none", "tone_normal", "tone_combat", "tone_stealth", "tone_critical", "tone_other"]
TONES = ["tone_none", "tone_normal", "tone_combat", "tone_stealth", "tone_critical", "tone_other"]
FIELDS = ["Id", "Type", "SType", "GenericType", "SGenericType", "bForbidGeneric", "Recipient", "Subject",
          "Tone", "eCustomize", "Text"]
DOC_MARK = ("<!-- generated:voice-descs -->", "<!-- /generated:voice-descs -->")
BASE = "VoicePack"


def load_tool(name, filename):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, "tools", filename))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def prop_stream():
    return load_tool("prop_stream", "prop-stream.py")


def voice_sets():
    """`tools/voice-sets.py` for the parts that are the same read: the pack tree, refs and arrays."""
    return load_tool("voice_sets", "voice-sets.py")


# ---------------------------------------------------------------------------------------------- one VoiceSet
def block_at_zero(ps, buf, pkg, want):
    """The block a `VoiceSet` payload starts with, or `None` when it does not tile the payload exactly."""
    try:
        props, end, reason = ps.read_block(buf, pkg, False)
    except Exception:
        return None
    if reason != "None" or not props or end != len(buf):
        return None
    if want and not any(p["name"] == want for p in props):
        return None
    if any(p["name"] == "?" for p in props):
        return None
    return props


def vdesc_of(ps, buf, pkg, prop):
    """The tagged fields of a `VoiceDesc` struct value (`Id`, `Type`, `Tone`, ...); `{}` when it does not read."""
    start, size = prop["value_offset"], prop["size"]
    if size <= 0 or start + size > len(buf):
        return {}
    try:
        fields, end, reason = ps.read_block(buf[start:start + size], pkg, True)
    except Exception:
        return {}
    if reason != "None" or end > size:
        return {}
    return {f["name"]: f for f in fields}


def descs_of(ps, buf, pkg, prop):
    """(list of field dicts, None) or (None, why) for a `VSoundsDesc` array property."""
    vo, size = prop["value_offset"], prop["size"]
    try:
        count, off = ps.read_index(buf, vo)
    except IndexError:
        return None, "count past the payload"
    if count < 0 or count > len(buf):
        return None, "count %d" % count
    out = []
    for _ in range(count):
        try:
            fields, end, reason = ps.read_block(buf[off:], pkg, True)
        except Exception:
            return None, "element at %d does not read" % (off - vo)
        if reason != "None" or not fields:
            return None, "element at %d ends %s" % (off - vo, reason)
        if not any(f["name"] == "Id" for f in fields):
            return None, "element at %d has no Id" % (off - vo)
        out.append({f["name"]: f for f in fields})
        off += end
    if off != vo + size:
        return None, "%d element(s) consume %d of %d bytes" % (count, off - vo, size)
    return out, None


def set_block(ps, pkg, exp, cache):
    """(VDesc fields, [descriptor field dicts]) for one `VoiceSet` export, or `None`."""
    key = exp["index"]
    if key in cache:
        return cache[key]
    buf = pkg.data[exp["offset"]:exp["offset"] + exp["size"]]
    props = block_at_zero(ps, buf, pkg, "VDesc")
    if props is None:
        cache[key] = None
        return None
    by_name = {}
    for p in props:
        by_name.setdefault(p["name"], p)
    vdesc = vdesc_of(ps, buf, pkg, by_name["VDesc"]) if "VDesc" in by_name else {}
    descs, why = ([], None)
    if "VSoundsDesc" in by_name:
        descs, why = descs_of(ps, buf, pkg, by_name["VSoundsDesc"])
    if descs is None:
        cache[key] = None
        return None
    cache[key] = (vdesc, descs)
    return cache[key]


def value(fields, name, default=None):
    f = fields.get(name)
    if f is None:
        return default
    return f.get("value", default)


def tally(descs, vdesc, acc):
    """Fold one set's descriptors into a pack's counters."""
    set_id = value(vdesc, "Id", "None")
    set_tone = value(vdesc, "Tone", 0)
    if set_id == "None":
        acc["no_set_id"] += 1
    tones = set()
    for d in descs:
        acc["descs"] += 1
        if value(d, "Id", "None") != set_id:
            acc["id_override"] += 1
        if value(d, "Type", 0):
            acc["type_override"] += 1
        if value(d, "GenericType", 0):
            acc["generic"] += 1
        if value(d, "SGenericType", ""):
            acc["sgeneric"] += 1
        if value(d, "bForbidGeneric", False):
            acc["forbid"] += 1
        if value(d, "Recipient", "None") != "None":
            acc["recipient"] += 1
        if value(d, "Subject", "None") != "None":
            acc["subject"] += 1
        if value(d, "eCustomize", 0):
            acc["customize"] += 1
        if value(d, "Text", "") != "":
            acc["text"] += 1
        tone = value(d, "Tone", set_tone)
        if isinstance(tone, int) and 0 <= tone < len(TONES):
            acc[TONES[tone]] += 1
            tones.add(tone)
        else:
            acc["tone_other"] += 1
            tones.add(-1)
    if len(tones) > 1:
        acc["multi_tone"] += 1


# ----------------------------------------------------------------------------------------------- the corpus
def sweep(system_dir, tree):
    ps = prop_stream()
    vs = voice_sets()
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
            if not vs.under_base(cls, tree):
                continue
            acc = dict((c, 0) for c in COLUMNS[2:])
            buf = pkg.data[exp["offset"]:exp["offset"] + exp["size"]]
            props = vs.block_of(ps, buf, pkg, "VoiceSets")[0]
            if props is None:
                row = [pkg_name, cls] + [0] * len(COLUMNS[2:])
                rows.append([pkg_name, cls, 0, 0] + [0] * (len(COLUMNS) - 4))
                continue
            sets_prop = [p for p in props if p["name"] == "VoiceSets"][0]
            count, elems, good = vs.array_of(ps, buf, sets_prop)
            bad = 0 if good else 1
            seen = set()
            for v in elems:
                if v == 0:
                    continue
                resolved = vs.ref_of(pkg, v)
                if not resolved or resolved[0] != "VoiceSet" or v <= 0:
                    bad += 1
                    continue
                sub = pkg.exports[v - 1]
                got = set_block(ps, pkg, sub, cache)
                if got is None:
                    bad += 1
                    continue
                if sub["index"] in seen:      # one export can fill several slots; count it once
                    continue
                seen.add(sub["index"])
                if not got[1]:                # a set with no descriptor array answers nothing here
                    continue
                acc["sets"] += 1
                tally(got[1], got[0], acc)
            if bad:
                notes[cls] = "unread=%d" % bad
            rows.append([pkg_name, cls] + [acc[c] for c in COLUMNS[2:]])
    rows.sort(key=lambda r: (r[0], r[1]))
    return rows, notes


def pl(n, word):
    return "%d %s%s" % (n, word, "" if n == 1 else "s")


def summary(rows):
    counted = [r for r in rows if r[3] > 0]
    out = {"classes": len(rows), "voiced": len(counted)}
    for i, c in enumerate(COLUMNS[2:], start=2):
        out[c] = sum(r[i] for r in rows)
    return out


# --------------------------------------------------------------------------------------------- the page block
def doc_block(rows):
    n = summary(rows)
    counted = sorted([r for r in rows if r[3] > 0], key=lambda r: (-r[3], r[1]))
    lines = ["", "### What a per-sound `VoiceDesc` carries", "",
             "Generated by `tools/voice-descs.py` (`make voice-descs`); `--check` in `make ci` keeps it honest.",
             ""]
    if not counted:
        lines += ["No pack carries a descriptor array in this measurement.", ""]
        return "\n".join(lines)
    idx = dict((c, i) for i, c in enumerate(COLUMNS))
    col = lambda r, c: r[idx[c]]
    lines += ["**%s decode over %s in %s, and every one of them is the same 11 fields.** "
              "The array exists only where the extended pack does: `%s` carry one, the other packs' sets hold "
              "`VDesc` + `VSounds` and no per-sound descriptor at all."
              % (pl(n["descs"], "descriptor"), pl(n["sets"], "set"), pl(n["voiced"], "pack"),
                 "`, `".join(r[1] for r in counted)), "",
              "| pack | sets | descs | multi-tone sets | recipient | subject | customize | forbid | text |",
              "|---|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for r in counted:
        lines.append("| `%s` | %d | %d | %d | %d | %d | %d | %d | %d |"
                     % (r[1], col(r, "sets"), col(r, "descs"), col(r, "multi_tone"),
                        col(r, "recipient"), col(r, "subject"), col(r, "customize"),
                        col(r, "forbid"), col(r, "text")))
    lines.append("")
    tones = ["`%s` %d" % (t.replace("tone_", ""), n[t]) for t in TONES if n[t]]
    lines.append("**Tone is the field the per-sound array exists for.** %d of the %d sets name more than one "
                 "tone among their descriptors; over all descriptors the histogram is %s -- `TONE_Critical` "
                 "and `TONE_Other` are declared and never used. A set is one line of dialogue and its "
                 "`VSounds` are the alternate takes, so `VSoundsDesc[i].Tone` is what puts take *i* in the "
                 "selector's tone-match or tone-generic bucket; without it every take of a line would land in "
                 "the same bucket and only `Rand` would choose between them."
                 % (n["multi_tone"], n["sets"], ", ".join(tones) or "empty"))
    lines.append("")
    lines.append("**A descriptor names its type by string, and the int is written at load.** `Type` and "
                 "`GenericType` are 0 in all %d descriptors and `SGenericType` is set in %d; "
                 "`VoiceSet.Build` runs `GetMsgTypeFromString` over the strings when the pack builds, which is "
                 "when the ints the selector's `IsSubTypeOf` compares appear. So the descriptor file does not "
                 "hold the type the lottery matches on -- it holds the name the build resolves it from."
                 % (n["descs"], n["sgeneric"]))
    lines.append("")
    lines.append("**What the descriptor does not hold is a probability.** It carries match keys -- `Id`, "
                 "`Recipient` (%d descriptors name one), `Subject` (%d), `Tone`, `bForbidGeneric` (%d) and "
                 "`eCustomize` (%d, the recipient-name prefix/postfix modes) -- and every probability lives on "
                 "`VoiceSelector`'s own defaults. %d of the %d sets have no `VDesc.Id` of their own (the "
                 "descriptor Ids define the line) and %d descriptors name an Id different from their set's, "
                 "which is the fall-through `GetVoiceID`/`GetVoiceTone` chain the set's own `VDesc` covers."
                 % (n["recipient"], n["subject"], n["forbid"], n["customize"], n["no_set_id"], n["sets"],
                    n["id_override"]))
    lines.append("")
    lines.append("`inventory/voice_descs.csv` holds one row per pack. Each set's own `VDesc` is a 66-byte "
                 "`VoiceDesc` and carries the same fields; no descriptor *text* is published here, only "
                 "whether one is set.")
    lines.append("")
    return "\n".join(lines)


def replace_block(text, block):
    begin, end = DOC_MARK
    i, j = text.find(begin), text.find(end)
    if i < 0 or j <= i:
        sys.exit("document has no %s ... %s block" % DOC_MARK)
    return text[: i + len(begin)] + block + "\n" + text[j:]


def render(rows):
    return "\n".join(",".join(str(c) for c in row) for row in [COLUMNS] + rows) + "\n"


def emit(rows, args):
    n = summary(rows)
    path = args.write_doc or args.check_doc
    if path:
        with open(path, encoding="utf-8") as fh:
            current = fh.read()
        fresh = replace_block(current, doc_block(rows))
        if args.check_doc:
            if fresh != current:
                print("FAIL %s: the generated block is stale (run --write-doc)" % path)
                return 1
            print("ok   voice-descs-doc: %s matches its rows" % path)
        else:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(fresh)
            print("ok   voice-descs-doc: wrote %s" % path)
    if args.out:
        with open(args.out, "w", newline="") as fh:
            fh.write(render(rows))
    elif not path:
        sys.stdout.write(render(rows))
    print("ok   voice-descs: %d pack class(es), %d voiced: %d descriptors over %d sets, %d multi-tone"
          % (n["classes"], n["voiced"], n["descs"], n["sets"], n["multi_tone"]))
    return 0


# ---------------------------------------------------------------------------------------------- the self-test
class FakePkg:
    def __init__(self, names):
        self.names = list(names)
        self.exports = []
        self.imports = []
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


def tag(name_idx, type_code, blob):
    """A synthetic tagged property: name, info (1-byte size class), size, value."""
    return enc(name_idx) + bytes([(5 << 4) | type_code]) + bytes([len(blob)]) + blob


def int_field(name_idx, v):
    return tag(name_idx, 2, v.to_bytes(4, "little"))


def name_field(name_idx, idx):
    return tag(name_idx, 6, enc(idx))


def byte_field(name_idx, v):
    return tag(name_idx, 1, bytes([v]))


def str_field(name_idx, text):
    """An FString: the ASCII length prefix counts the terminating NUL (`#357` read `Text` this way)."""
    blob = (bytes([len(text) + 1]) + text.encode("latin1") + b"\x00") if text else b"\x00"
    return tag(name_idx, 13, blob)


def bool_field(name_idx, v):
    return enc(name_idx) + bytes([0x80 | 3 if v else 3]) + bytes([0])


def desc_block(names, tone, recipient=0, text="", customize=0, forbid=False, generic=0):
    """One `VoiceDesc` element: the 11 fields in declaration order, then the element's `None`."""
    return b"".join([
        name_field(names["Id"], names["MSGID_Name"]),
        int_field(names["Type"], 0),
        str_field(names["SType"], ""),
        int_field(names["GenericType"], generic),
        str_field(names["SGenericType"], ""),
        bool_field(names["bForbidGeneric"], forbid),
        name_field(names["Recipient"], recipient),
        name_field(names["Subject"], 0),
        byte_field(names["Tone"], tone),
        byte_field(names["eCustomize"], customize),
        str_field(names["Text"], text),
    ]) + b"\x00"


def descriptor_array(names, elements):
    body = enc(len(elements)) + b"".join(elements)
    return tag(names["VSoundsDesc"], 9, body)


def check():
    ps = prop_stream()
    names = {"None": 0, "VDesc": 1, "VSounds": 2, "VSoundsDesc": 3, "VoiceSets": 4, "VoiceDesc": 5,
             "Sound": 6, "Id": 7, "Type": 8, "SType": 9, "GenericType": 10, "SGenericType": 11,
             "bForbidGeneric": 12, "Recipient": 13, "Subject": 14, "Tone": 15, "eCustomize": 16,
             "Text": 17, "MSGID_Name": 18, "Oneill": 19}
    pkg = FakePkg(list(names))
    n = names

    # A VDesc struct: `Id` + `Tone`, then its own terminator, wrapped in the struct tag.
    vdesc_blob = name_field(n["Id"], n["MSGID_Name"]) + byte_field(n["Tone"], 0) + b"\x00"
    vdesc = enc(n["VDesc"]) + bytes([(5 << 4) | 10]) + enc(n["VoiceDesc"]) + bytes([len(vdesc_blob)]) \
        + vdesc_blob
    # Three takes of one line: one normal, one combat, one stealth, the last naming a recipient.
    elems = [desc_block(n, 1), desc_block(n, 2), desc_block(n, 3, recipient=n["Oneill"], text="On me")]
    payload = vdesc + tag(n["VSounds"], 9, enc(3) + enc(-1) + enc(-2) + enc(-3)) \
        + descriptor_array(n, elems) + b"\x00"

    props = block_at_zero(ps, payload, pkg, "VDesc")
    if props is None or [p["name"] for p in props] != ["VDesc", "VSounds", "VSoundsDesc"]:
        print("FAIL the VoiceSet block did not read from byte 0 (%r)"
              % ([p["name"] for p in props] if props else None,))
        return 1
    by = {p["name"]: p for p in props}
    if by["VDesc"]["struct"] != "VoiceDesc" or by["VDesc"]["size"] != len(vdesc_blob):
        print("FAIL the VDesc struct read as %r/%r" % (by["VDesc"]["struct"], by["VDesc"]["size"]))
        return 1
    vf = vdesc_of(ps, payload, pkg, by["VDesc"])
    if value(vf, "Id") != "MSGID_Name" or value(vf, "Tone") != 0:
        print("FAIL VDesc's own fields read as %r" % ({k: v.get("value") for k, v in vf.items()},))
        return 1
    descs, why = descs_of(ps, payload, pkg, by["VSoundsDesc"])
    if descs is None or len(descs) != 3:
        print("FAIL the descriptor array did not decode (%s)" % why)
        return 1
    if [value(d, "Tone") for d in descs] != [1, 2, 3]:
        print("FAIL the per-sound tones read as %r" % ([value(d, "Tone") for d in descs],))
        return 1
    if value(descs[2], "Recipient") != "Oneill" or value(descs[2], "Text") != "On me":
        print("FAIL a descriptor's recipient/text read as %r" % (value(descs[2], "Recipient"),))
        return 1
    if value(descs[0], "Recipient") != "None" or value(descs[0], "Text") != "":
        print("FAIL an unset descriptor field did not fall back to None/empty")
        return 1

    # Tally: three takes of one line are one multi-tone set, one recipient, one text, three tones.
    acc = dict((c, 0) for c in COLUMNS[2:])
    acc["sets"] = 1
    tally(descs, vf, acc)
    expect = {"sets": 1, "descs": 3, "multi_tone": 1, "no_set_id": 0, "id_override": 0,
              "type_override": 0, "generic": 0, "sgeneric": 0, "recipient": 1, "subject": 0,
              "customize": 0, "text": 1, "forbid": 0, "tone_none": 0, "tone_normal": 1,
              "tone_combat": 1, "tone_stealth": 1, "tone_critical": 0, "tone_other": 0}
    if acc != expect:
        print("FAIL the tally answered %r" % ({k: (acc[k], expect[k]) for k in acc
                                              if k in expect and acc[k] != expect[k]},))
        return 1

    # **The bug the reader has to refuse**: an element walk that stops inside the array. One byte off the
    # last element's size and the whole set is unread rather than one element short.
    short = bytearray(payload)
    dprop = by["VSoundsDesc"]
    short[dprop["value_offset"] + dprop["size"] - 1] = 0x99
    got, why = descs_of(ps, bytes(short), pkg, dprop)
    if got is not None:
        print("FAIL an element walk that overshot the array was accepted")
        return 1

    rows = [["TestPkg", "TestVoicePack"] + [acc[c] for c in COLUMNS[2:]],
            ["TestPkg", "SilentVoicePack"] + [0] * len(COLUMNS[2:])]
    n2 = summary(rows)
    if n2["voiced"] != 1 or n2["descs"] != 3 or n2["sets"] != 1 or n2["multi_tone"] != 1:
        print("FAIL summary answered %r" % (n2,))
        return 1
    doc = doc_block(rows)
    for needle in ("3 descriptors decode over 1 set in 1 pack",
                   "| `TestVoicePack` | 1 | 3 | 1 | 1 | 0 | 0 | 0 | 1 |"):
        if needle not in doc:
            print("FAIL doc_block is missing %r:\n%s" % (needle, doc))
            return 1
    stale = "a\n<!-- generated:voice-descs -->\nold\n<!-- /generated:voice-descs -->\nb\n"
    fresh = replace_block(stale, "\nnew")
    if "old" in fresh or "new" not in fresh:
        print("FAIL replace_block did not replace the marked block")
        return 1
    if render(rows).splitlines()[0] != ",".join(COLUMNS):
        print("FAIL render dropped the header")
        return 1
    print("ok   voice-descs: a VoiceSet block reads from byte 0, a VDesc's own fields do not pose as the "
          "set's, a descriptor array tiles count + tagged elements, an element walk that stops short is "
          "refused, and the per-tone tally and doc block render the totals")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--system", help="the dump's System directory")
    ap.add_argument("-o", "--out", help="write the CSV here (default: stdout)")
    ap.add_argument("--write-doc", metavar="PATH", help="regenerate the page's block between its markers")
    ap.add_argument("--check-doc", metavar="PATH", help="fail if the page's block is stale (in `make ci`)")
    ap.add_argument("--from-csv", metavar="PATH", default=os.path.join(ROOT, "inventory", "voice_descs.csv"),
                    help="with --check-doc/--write-doc and no --system: read this artifact instead")
    ap.add_argument("--check", action="store_true", help="synthetic self-test; needs no dump")
    args = ap.parse_args()

    if args.check:
        return check()
    if (args.write_doc or args.check_doc) and not args.system:
        if not os.path.exists(args.from_csv):
            sys.exit("voice-descs: %s does not exist" % args.from_csv)
        with open(args.from_csv, newline="", encoding="utf-8") as fh:
            rows = [[r[c] if c in ("package", "class") else int(r[c]) for c in COLUMNS]
                    for r in csv.DictReader(fh)]
        if not rows:
            sys.exit("voice-descs: %s holds no rows" % args.from_csv)
        return emit(rows, args)
    if not args.system:
        sys.exit("voice-descs: pass --system <DUMP/System> (or --check)")
    rows, notes = sweep(args.system, voice_sets().supers())
    if not rows:
        sys.exit("voice-descs: no class under %s found" % BASE)
    for cls, note in sorted(notes.items()):
        print("note voice-descs: %s %s" % (cls, note))
    return emit(rows, args)


if __name__ == "__main__":
    sys.exit(main())
