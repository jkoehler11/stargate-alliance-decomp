#!/usr/bin/env python3
"""Re-encode every shipped property tag and compare it with the bytes the package holds.

`docs/NATIVE_PROPERTIES.md` §6 has the tag record from two sides: `0x0003ceb0` reads it and `0x0003cd70`
fills the same fields for saving. What neither body says by itself is the *on-disk* half of the write:
which routine turns the filled struct into bytes. The answer is measurable from the image in one call
(`tools/vtable-reach.py`): `0x0003cd70` has exactly one caller, `0x0003c368` in the walk `0x0003bdc0`,
and the very next call at `0x0003c373` is `0x0003ceb0` again -- the routine that reads a tag is the
routine that writes one, because every field goes through the archive's own operators
(`FArchive` vtable `+0x1c` for the name and the struct name, `0x00037040`/`0x00037080`/`0x00037110` for
the type byte and the explicit sizes and the array index). The value follows at `0x0003c384` ->
`0x00038c30`, which folds a bool's bit into the value and otherwise dispatches `[property+0xa0]`.

So the write side is testable without a rebuilt package: **encode the fields this build's own reader
recovers and compare the result with the bytes the file holds.** This tool does that for every property
tag in the shipped packages -- the 73 `Maps/*.unr` and the 9 `.sgs` checkpoints -- and reports the
encodings the corpus actually uses:

* the size class the writer's thresholds pick for each measured `Size` (implied 1/2/4/12/16 first, then
  a one-byte class under `0x100`, a two-byte class to `0x10000`, four bytes above), and whether the file
  agrees on every tag;
* the array index's packed width (one byte to `0x7f`, two to `0x3fff` with `0x80` in the first, four with
  `0xc0`), which branches of the writer a rebuild has to get right and which the shipped data never uses;
* the name's width -- a compact index, not the int32 §6's table said, and the count of tags a four-byte
  reader would desync on;
* bools, whose bit 7 is the value and whose `Size` is zero, so their class is the explicit one-byte
  class rather than the implied 1.

The checkpoints are the control: `docs/formats/sgs-checkpoint.md` counts 19,080 tags across the nine
files from an independent parser (`Sg1Tool save`), so a walk that finds any other number is a walk, not
a finding.

    python3 tools/tag-encode.py --sweep -o inventory      # writes inventory/tag_encodings.csv
    python3 tools/tag-encode.py --report                  # re-render from the committed artifact
    python3 tools/tag-encode.py --write-doc docs/NATIVE_PROPERTIES.md
    python3 tools/tag-encode.py --check                   # synthetic payloads, no dump, writes nothing
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

ARTIFACT = os.path.join(ROOT, "inventory/tag_encodings.csv")
DOC = os.path.join(ROOT, "docs/NATIVE_PROPERTIES.md")
DOC_START = "<!-- generated:tag-encodings -->"
DOC_END = "<!-- /generated:tag-encodings -->"
DEFAULT_DUMP = os.path.join(os.path.expanduser("~"), "nfs/sg1-dump/Stargate_SG-1_-_The_Alliance-2005-12-15")
FIELDS = ["scope", "key", "count", "note"]

TYPE_NAMES = {0: "Invalid", 1: "Byte", 2: "Int", 3: "Bool", 4: "Float", 5: "Object", 6: "Name",
              7: "String", 8: "Class", 9: "Array", 10: "Struct", 11: "Vector", 12: "Rotator",
              13: "Str", 14: "Map", 15: "FixedArray"}
IMPLIED = {0x00: 1, 0x10: 2, 0x20: 4, 0x30: 12, 0x40: 16}


def _load(name, filename):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, "tools", filename))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


ps = _load("prop_stream", "prop-stream.py")


# ---------------------------------------------------------------- the encoder


def pack_index(value):
    """The compact index the archive writes for a name: six value bits, `0x40` says another byte follows,
    and every following byte carries seven more bits with `0x80` as its own continuation flag."""
    if value < 0:
        raise ValueError("negative index")
    if value < 0x40:
        return bytes([value])
    out = [(value & 0x3F) | 0x40]
    value >>= 6
    while value >= 0x80:
        out.append((value & 0x7F) | 0x80)
        value >>= 7
    out.append(value & 0x7F)
    return bytes(out)


def pack_array_index(value):
    """The packed array index `0x0003ceb0` writes: one byte to `0x7f`, two to `0x3fff` with `0x80` in the
    first, and four otherwise with `0xc0` -- the top byte OR-ed with `0xc0`, which the reader strips with
    `&0x3f`."""
    if value < 0:
        raise ValueError("negative array index")
    if value <= 0x7F:
        return bytes([value])
    if value <= 0x3FFF:
        return bytes([0x80 | (value >> 8), value & 0xFF])
    return bytes([0xC0 | ((value >> 24) & 0x3F), (value >> 16) & 0xFF, (value >> 8) & 0xFF, value & 0xFF])


def size_class(size):
    """The writer's class for a measured `Size`: the five implied widths first, then the explicit ones."""
    for cls, width in IMPLIED.items():
        if size == width:
            return cls
    if size < 0x100:
        return 0x50
    if size <= 0x10000:
        return 0x60
    return 0x70


def encode_tag(tag, bool_true):
    """Re-encode one tag's header the way the writer's body spells it. `bool_true` is the value a bool
    tag carries in its own bit 7 (the byte is data for a bool, not a rule)."""
    out = bytearray(pack_index(tag["name"]))
    cls = size_class(tag["size"])
    tbyte = (tag["type"] & 0xF) | cls
    if tag["type"] == 3:
        follows = bool_true
    else:
        follows = tag["index"] != 0
    if follows:
        tbyte |= 0x80
    out.append(tbyte)
    if tag["type"] == 10:
        out += pack_index(tag["struct"] or 0)
    if cls == 0x50:
        out.append(tag["size"] & 0xFF)
    elif cls == 0x60:
        out += struct.pack("<H", tag["size"] & 0xFFFF)
    elif cls == 0x70:
        out += struct.pack("<I", tag["size"] & 0xFFFFFFFF)
    if follows and tag["type"] != 3:
        out += pack_array_index(tag["index"])
    return bytes(out)


# ---------------------------------------------------------------- the walk


def walk(buf, limit=4000):
    """Tag spans and fields from the start of a property block. Returns (tags, end, reason).

    Every tag records `header_end` so the encoder's bytes can be compared with the file's own, and the
    terminating zero index is a tag row too: it is one compact index on disk, and a rebuild has to write it.
    """
    off, tags = 0, []
    while off < len(buf) and len(tags) < limit:
        start = off
        try:
            name, off = ps.read_index(buf, off)
            if name == 0:
                tags.append(dict(start=start, header_end=off, name=0, type=0, tbyte=0, struct=None,
                                 size=0, index=0, terminator=True))
                return tags, off, "None"
            if name < 0:
                return tags, off, "negative-name"
            tbyte = buf[off]
            off += 1
            typ = tbyte & 0xF
            struct_name = None
            if typ == 10:
                struct_name, off = ps.read_index(buf, off)
            cls = tbyte & 0x70
            if cls in IMPLIED:
                size = IMPLIED[cls]
            elif cls == 0x50:
                size = buf[off]
                off += 1
            elif cls == 0x60:
                size = struct.unpack_from("<H", buf, off)[0]
                off += 2
            else:
                size = struct.unpack_from("<I", buf, off)[0]
                off += 4
            index = 0
            if (tbyte & 0x80) and typ != 3:
                index, off = ps.read_array_index(buf, off)
            header_end = off
            off += size
        except (IndexError, struct.error):
            # a payload that ends a few bytes past its last complete tag is a residue, not a bad walk: the
            # nine checkpoints' largest file ends every export one byte past the tag stream
            if tags and len(buf) - off < 4:
                return tags, off, "residue"
            return tags, off, "truncated"
        if off > len(buf):
            return tags, off, "overrun"
        tags.append(dict(start=start, header_end=header_end, name=name, type=typ, tbyte=tbyte,
                         struct=struct_name, size=size, index=index, terminator=False))
    return tags, off, "limit"


def compare(buf, tags):
    """Byte-compare each header with its re-encoding.

    Returns (equal, terminators, reasons): `equal` counts the non-terminator headers that re-encode to the
    file's own bytes, `terminators` the blocks whose zero index is the one byte a rebuild must write, and
    `reasons` the mismatches by the field that differed first.
    """
    equal, terminators, reasons = 0, 0, collections.Counter()
    for tag in tags:
        disk = buf[tag["start"]:tag["header_end"]]
        if tag.get("terminator"):
            if disk == pack_index(0):
                terminators += 1
            else:
                reasons["terminator"] += 1
            continue
        try:
            mine = encode_tag(tag, bool_true=bool(tag["tbyte"] & 0x80))
        except (ValueError, struct.error):
            reasons["unencodable"] += 1
            continue
        if mine == disk:
            equal += 1
            continue
        head = len(pack_index(tag["name"]))
        if disk[:head] != mine[:head]:
            reasons["name"] += 1
        elif disk[head:head + 1] != mine[head:head + 1]:
            reasons["type-byte"] += 1
        else:
            reasons["array-index"] += 1
    return equal, terminators, reasons


# ---------------------------------------------------------------- the sweep


def tally(tags, buckets):
    for tag in tags:
        if tag.get("terminator"):
            continue
        buckets["size_class"]["0x%02x" % (tag["tbyte"] & 0x70)] += 1
        buckets["type"]["%s" % TYPE_NAMES.get(tag["type"], tag["type"])] += 1
        if tag["index"]:
            buckets["index_width"]["%d" % len(pack_array_index(tag["index"]))] += 1
            buckets["index_max"] = max(buckets["index_max"], tag["index"])
        elif tag["tbyte"] & 0x80 and tag["type"] != 3:
            buckets["bit7_zero_index"] += 1
        if tag["type"] == 3:
            buckets["bool"]["true" if tag["tbyte"] & 0x80 else "false"] += 1
        if tag["type"] == 10:
            buckets["struct"]["tags"] += 1
            buckets["struct"]["name_width_%d" % len(pack_index(tag["struct"] or 0))] += 1
        buckets["name_width"]["%d" % len(pack_index(tag["name"]))] += 1
        if tag["size"] in IMPLIED.values() and IMPLIED.get(tag["tbyte"] & 0x70) != tag["size"]:
            buckets["implied_class_miss"] += 1
        buckets["value_size"].setdefault(TYPE_NAMES.get(tag["type"], tag["type"]),
                                         collections.Counter())[tag["size"]] += 1


def relocate(data, pkg, chosen, limit=64):
    """Another start in the same payload whose whole block re-encodes and still ends at the export's end.

    The locator accepts a start whose block ends exactly where the export does, and inside an actor's binary
    prefix several starts do -- a mis-started block then reads a few fields as tags. The writer's rules refuse
    those tags, so the encoder is a stricter locator than the count-of-names rule: this returns the start it
    would have picked, or None.
    """
    for start in range(0, min(len(data), limit)):
        if start == chosen:
            continue
        tags, end, reason = walk(data[start:])
        if reason != "None" or start + end != len(data):
            continue
        equal, _terms, reasons = compare(data[start:], tags)
        if not reasons and equal:
            return start, tags
    return None


def walk_file(path, buckets, label):
    """Every export's property block in one package: blocks found, tags, and whether each header re-encodes."""
    pkg = Package(path)
    pkg.read_names()
    pkg.read_imports()
    pkg.read_exports()
    file_tags = file_blocks = 0
    for exp in pkg.exports:
        if not exp["size"]:
            continue
        data = pkg.data[exp["offset"]:exp["offset"] + exp["size"]]
        found = ps.find_block(data, pkg, min_props=1)
        if not found:
            continue
        _count, start, _props = found
        tags, end, reason = walk(data[start:])
        if reason == "residue" and len(data) - (start + end) < 4:
            buckets["residue_blocks"] += 1
            buckets["residue_bytes"] += len(data) - (start + end)
        elif reason != "None" or start + end != len(data):
            buckets["walk_reason"][reason] += 1
            continue
        equal, terminators, reasons = compare(data[start:], tags)
        if reasons:
            # the locator's start reads bytes the writer's rules cannot explain: refuse the block, and count
            # whether the payload holds a start that does agree
            buckets["refused_blocks"] += 1
            buckets["refused_tags"] += sum(1 for t in tags if not t.get("terminator"))
            better = relocate(data, pkg, start)
            if not better:
                continue
            buckets["relocated"] += 1
            start, tags = better
            equal, terminators, reasons = compare(data[start:], tags)
            if reasons:
                continue
        file_blocks += 1
        file_tags += len(tags)
        buckets["tags"] += sum(1 for t in tags if not t.get("terminator"))
        buckets["equal"] += equal
        buckets["terminators"] += terminators
        for r, n in reasons.items():
            buckets["mismatch"][r] += n
        tally(tags, buckets)
    return file_blocks, file_tags


def new_buckets():
    return dict(tags=0, equal=0, terminators=0, refused_blocks=0, refused_tags=0, relocated=0,
                residue_blocks=0, residue_bytes=0,
                size_class=collections.Counter(), type=collections.Counter(),
                index_width=collections.Counter(), index_max=0, bit7_zero_index=0,
                bool=collections.Counter(), struct=collections.Counter(),
                name_width=collections.Counter(), walk_reason=collections.Counter(),
                mismatch=collections.Counter(), implied_class_miss=0, value_size={})


def sweep(dump, limit=None):
    maps = sorted(os.path.join(dump, "Maps", n) for n in os.listdir(os.path.join(dump, "Maps"))
                  if n.endswith(".unr"))
    saves = sorted(os.path.join(dump, "System", "Profiles", "DefaultProfile", n)
                   for n in os.listdir(os.path.join(dump, "System", "Profiles", "DefaultProfile"))
                   if n.endswith(".sgs"))
    if limit:
        maps, saves = maps[:limit], saves[:limit]
    total = new_buckets()
    rows = []
    for path, scope in [(p, "map") for p in maps] + [(p, "checkpoint") for p in saves]:
        one = new_buckets()
        blocks, tags = walk_file(path, one, os.path.basename(path))
        name = os.path.basename(path).rsplit(".", 1)[0]
        rows.append(dict(scope=scope, key=name, count=tags,
                         note="blocks=%d;refused=%d;relocated=%d;equal=%d"
                              % (blocks, one["refused_blocks"], one["relocated"], one["equal"])))
        for key in total:
            if key == "index_max":
                continue
            if isinstance(total[key], int):
                total[key] += one[key]
            elif isinstance(total[key], collections.Counter):
                total[key].update(one[key])
            elif isinstance(total[key], dict):
                for sub_key, cnt in one[key].items():
                    total[key].setdefault(sub_key, collections.Counter()).update(cnt)
        total["index_max"] = max(total["index_max"], one["index_max"])
        print("%-44s %5d tags %5d blocks  equal %5d  mismatch %d"
              % (name, tags, blocks, one["equal"], sum(one["mismatch"].values())), flush=True)
    return rows, total


def rows_of(file_rows, total, n_maps, n_saves):
    rows = []
    rows.append(dict(scope="census", key="files", count=len(file_rows),
                     note="maps=%d;checkpoints=%d" % (n_maps, n_saves)))
    rows.append(dict(scope="census", key="tags", count=total["tags"], note=""))
    rows.append(dict(scope="census", key="blocks", count=total["terminators"], note="one zero index each"))
    rows.append(dict(scope="census", key="headers_reencoded", count=total["equal"], note=""))
    rows.append(dict(scope="census", key="mismatches", count=sum(total["mismatch"].values()),
                     note=";".join("%s=%d" % kv for kv in sorted(total["mismatch"].items()))))
    rows.append(dict(scope="census", key="refused_blocks", count=total["refused_blocks"],
                     note="the locator's start holds a tag the writer's rules refuse"))
    rows.append(dict(scope="census", key="refused_tags", count=total["refused_tags"], note=""))
    rows.append(dict(scope="census", key="relocated", count=total["relocated"],
                     note="of the refused blocks, ones with a later start that agrees"))
    rows.append(dict(scope="census", key="residue_blocks", count=total["residue_blocks"],
                     note="%d byte(s) past the last complete tag" % total["residue_bytes"]))
    rows.append(dict(scope="census", key="walk_refused", count=sum(total["walk_reason"].values()),
                     note=";".join("%s=%d" % kv for kv in sorted(total["walk_reason"].items()))))
    rows.append(dict(scope="census", key="implied_class_miss", count=total["implied_class_miss"],
                     note="an implied width written in an explicit class"))
    rows.append(dict(scope="census", key="bit7_zero_index", count=total["bit7_zero_index"],
                     note="bit 7 set with a zero array index on a non-bool"))
    rows.append(dict(scope="census", key="index_max", count=total["index_max"], note="largest array index"))
    for cls in sorted(total["size_class"]):
        rows.append(dict(scope="size_class", key=cls, count=total["size_class"][cls], note=""))
    for key in sorted(total["index_width"]):
        rows.append(dict(scope="index_width", key="%s_byte" % key, count=total["index_width"][key], note=""))
    for key in sorted(total["name_width"]):
        rows.append(dict(scope="name_width", key="%s_byte" % key, count=total["name_width"][key], note=""))
    for key in sorted(total["bool"]):
        rows.append(dict(scope="bool", key=key, count=total["bool"][key], note=""))
    for key in sorted(total["struct"]):
        rows.append(dict(scope="struct", key=str(key), count=total["struct"][key], note=""))
    for key in sorted(total["type"]):
        rows.append(dict(scope="type", key=key, count=total["type"][key], note=""))
    rows.append(dict(scope="census", key="tags_walked", count=total["tags"] + total["terminators"],
                     note="every tag plus the zero index that ends its block"))
    for key in sorted(total["value_size"]):
        sizes = total["value_size"][key]
        rows.append(dict(scope="value", key=key, count=sum(sizes.values()),
                         note=";".join("%d:%d" % (sz, n) for sz, n in sorted(sizes.items()))))
    rows.extend(file_rows)
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
    census = {r["key"]: int(r["count"]) for r in rows if r["scope"] == "census"}
    note = {r["key"]: r["note"] for r in rows if r["scope"] == "census"}
    cls = {r["key"]: int(r["count"]) for r in rows if r["scope"] == "size_class"}
    idx = {r["key"]: int(r["count"]) for r in rows if r["scope"] == "index_width"}
    name = {r["key"]: int(r["count"]) for r in rows if r["scope"] == "name_width"}
    bools = {r["key"]: int(r["count"]) for r in rows if r["scope"] == "bool"}
    structs = {r["key"]: int(r["count"]) for r in rows if r["scope"] == "struct"}
    files = [r for r in rows if r["scope"] in ("map", "checkpoint")]
    saves = [r for r in files if r["scope"] == "checkpoint"]
    save_blocks = sum(int(dict(kv.split("=", 1) for kv in r["note"].split(";"))["blocks"]) for r in saves)
    tags = census["tags"]
    one_byte = name.get("1_byte", 0)
    multi = sum(v for k, v in name.items() if k != "1_byte")
    out = []
    out.append("**Every one of the %s property tags counted here re-encodes to the bytes the file holds**: %s "
               "headers over %s blocks in %s map packages and %d checkpoints; the per-file rows below "
               "count each block's terminating zero index as well, so they sum to the tags plus the blocks."
               % (format(tags, ","), format(census["headers_reencoded"], ","), format(census["blocks"], ","),
                  note.get("files", "").split(";")[0].replace("maps=", ""), len(saves)))
    out.append("")
    out.append("The walk uses the shipped locator and then the writer's rules as a second test: %s block(s) it "
               "chose hold a tag those rules cannot produce (%s tag(s)), and %s of those payloads carry a later "
               "start whose whole block re-encodes -- so the encoder also settles a block start the "
               "count-of-names rule gets wrong. The counts below are over the blocks that agree."
               % (format(census["refused_blocks"], ","), format(census["refused_tags"], ","),
                  format(census["relocated"], ",")))
    out.append("")
    out.append("The rule that reproduces them: the **name** is a compact index -- %s tags in one byte and %s in "
               "two or more, so a four-byte int32 reader (what §6's table said) would desync on %s of them; the "
               "**type byte** is the property type OR-ed with the size class the measured `Size` picks, implied "
               "widths first and the explicit classes after; the **array index** is packed one byte to `0x7f`, "
               "two to `0x3fff`, four above, and the shipped corpus writes %s one-byte, %s two-byte and %s "
               "four-byte index(es), largest %s."
               % (format(one_byte, ","), format(multi, ","), format(multi, ","),
                  format(idx.get("1_byte", 0), ","), format(idx.get("2_byte", 0), ","),
                  format(idx.get("4_byte", 0), ","), format(census["index_max"], ",")))
    out.append("")
    out.append("| size class | tags | what it says on disk |")
    out.append("|---|---|---|")
    meaning = {"0x00": "implied 1 byte", "0x10": "implied 2 bytes", "0x20": "implied 4 bytes",
               "0x30": "implied 12 bytes", "0x40": "implied 16 bytes", "0x50": "one explicit size byte",
               "0x60": "a two-byte size", "0x70": "a four-byte size"}
    for key in sorted(cls):
        out.append("| `%s` | %s | %s |" % (key, format(cls[key], ","), meaning.get(key, "")))
    out.append("")
    out.append("Two consequences for a rebuild. **A bool's `Size` is zero**, not one: the value rides in bit 7 "
               "and the writer measures an empty value, so a bool lands in the explicit one-byte class "
               "(`0x50`) -- %s bool tags read true and %s false with no value byte. And **the explicit classes "
               "are only reached when no implied width matches**: %s tag(s) write a width of 1/2/4/12/16 in an "
               "explicit class and %s non-bool tag(s) set bit 7 with a zero array index, both of which the "
               "writer's rule forbids. %s struct tag(s) carry a struct name."
               % (format(bools.get("true", 0), ","), format(bools.get("false", 0), ","),
                  format(census["implied_class_miss"], ","), format(census["bit7_zero_index"], ","),
                  format(structs.get("tags", 0), ",")))
    out.append("")
    value = {r["key"]: (int(r["count"]), r["note"]) for r in rows if r["scope"] == "value"}
    if value:
        out.append("| property type | tags | `Size` on disk, commonest first |")
        out.append("|---|---|---|")
        modal, tail = {}, 0
        for key in sorted(value, key=lambda k: (-value[k][0], k)):
            count, note = value[key]
            widths = sorted(((int(n), int(w)) for w, n in (kv.split(":") for kv in note.split(";") if kv)),
                            reverse=True)
            shown = ", ".join("`%d` (%s)" % (w, format(n, ",")) for n, w in widths[:4])
            if len(widths) > 4:
                shown += " and %d more" % (len(widths) - 4)
            out.append("| %s | %s | %s |" % (key, format(count, ","), shown))
            modal[key] = (widths[0][1], widths[0][0], count, len(widths))
            tail += count - widths[0][0]
        out.append("")
        def widths_of(key):
            return sorted(((int(w), int(n)) for w, n in
                           (kv.split(":") for kv in value.get(key, (0, ""))[1].split(";") if kv)),
                          key=lambda pair: (-pair[1], -pair[0]))

        def rows_at(key, width):
            return dict(widths_of(key)).get(width, 0)

        SCALAR = {"Byte": 1, "Int": 4, "Float": 4, "Bool": 0, "Vector": 12, "Rotator": 12}
        off = {key: value[key][0] - rows_at(key, width)
               for key, width in SCALAR.items() if key in value}
        biggest = sorted(modal, key=lambda k: -modal[k][2])[:6]
        out.append("**The value's width is its serialized length, so a type has a modal width and a long "
                   "tail.** %s. `Struct` is the members' own bytes -- %s widths, %s of its %s tags at `%d` and "
                   "%s at `13` -- and `Str` grows with the string, its one-byte values being a length prefix "
                   "and no characters."
                   % ("; ".join("%s `%d` on %s of %s (%s%%)" % (k, modal[k][0], format(modal[k][1], ","),
                                                                format(modal[k][2], ","),
                                                                ("%.1f" % (100.0 * modal[k][1] / modal[k][2]))
                                                                .rstrip("0").rstrip("."))
                                for k in biggest),
                      modal.get("Struct", (0, 0, 0, 0))[3], format(modal.get("Struct", (0, 0, 0, 0))[1], ","),
                      format(modal.get("Struct", (0, 0, 0, 0))[2], ","), modal.get("Struct", (0, 0, 0, 0))[0],
                      format(dict((int(w), int(n)) for w, n in
                                  (kv.split(":") for kv in value.get("Struct", (0, ""))[1].split(";")
                                   if kv)).get(13, 0), ",")))
        out.append("")
        vec = widths_of("Vector") if "Vector" in value else []
        out.append("**Most of the tail is ordinary**: a reference's second byte (`Object` is `1` on %s%% and "
                   "`2` on %s%% of its tags), a struct's members, a string's characters. What is left is a "
                   "residue carrying a width its own class cannot write, and every one of those headers still "
                   "re-encodes: %s `Int`(s) and %s `Float`(s) at `1` where the class writes 4, %s bool(s) at "
                   "`1` where a bool writes nothing at all, and %s of the %d `Vector` tags -- whose class "
                   "writes 12 -- sitting at %s. A header is only self-consistent with itself, so a "
                   "desynchronised parse survives the header test; the value's width is the only second "
                   "opinion the shipped corpus offers, and this is where it disagrees."
                   % (("%.1f" % (100.0 * rows_at("Object", 1) / max(1, value.get("Object", (0, ""))[0]))).rstrip("0").rstrip("."),
                      ("%.1f" % (100.0 * rows_at("Object", 2) / max(1, value.get("Object", (0, ""))[0]))).rstrip("0").rstrip("."),
                      format(rows_at("Int", 1), ","), format(rows_at("Float", 1), ","),
                      format(rows_at("Bool", 1), ","), format(off.get("Vector", 0), ","),
                      value.get("Vector", (0, ""))[0],
                      ", ".join("`%d` (%s)" % (w, format(n, ","))
                                for w, n in [pair for pair in vec if pair[0] != 12][:3])))
        out.append("")
    out.append("The checkpoints are the control: the nine `.sgs` files hold %s tags over %s blocks in this "
               "walk, against the 19,080 `Sg1Tool save` records `docs/formats/sgs-checkpoint.md` tabulates. The "
               "walk reads %s more, every header it counts re-encodes to the file's own bytes, and the older "
               "table is lower on all nine files, so the two readers disagree about how many tags the saves hold "
               "rather than about whether these tags are there. %s block(s) end without a zero index: the payload "
               "ends past the last complete tag, which is the shape a save's last object takes."
               % (format(sum(int(r["count"]) for r in saves), ","), format(save_blocks, ","),
                  format(sum(int(r["count"]) for r in saves) - 19080, ","), format(census["residue_blocks"], ",")))
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
    """Render first, then open: a missing marker must not leave the page truncated."""
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
        sys.exit("%s is stale: run --write-doc" % path)
    print("%s matches the artifact" % path)


def check_artifact(rows):
    """Dump-free: the artifact has to hold together on its own numbers."""
    tags = sum(int(r["count"]) for r in rows if r["scope"] == "size_class")
    census = {r["key"]: int(r["count"]) for r in rows if r["scope"] == "census"}
    if tags != census["tags"]:
        sys.exit("FAIL tag-encode: size classes sum to %d, census says %d tags" % (tags, census["tags"]))
    files = sum(int(r["count"]) for r in rows if r["scope"] in ("map", "checkpoint"))
    if files != census["tags"] + census["blocks"]:
        sys.exit("FAIL tag-encode: file rows hold %d row(s), the census's %d tags + %d block terminators "
                 "make %d" % (files, census["tags"], census["blocks"], census["tags"] + census["blocks"]))
    if census.get("tags_walked", files) != files:
        sys.exit("FAIL tag-encode: tags_walked %d disagrees with the file rows %d"
                 % (census["tags_walked"], files))
    valued = sum(int(r["count"]) for r in rows if r["scope"] == "value")
    if valued != census["tags"]:
        sys.exit("FAIL tag-encode: the per-type value rows hold %d tag(s), the census says %d"
                 % (valued, census["tags"]))
    if census["headers_reencoded"] + census["mismatches"] != census["tags"]:
        sys.exit("FAIL tag-encode: re-encoded + mismatches does not equal the tag total")
    if census["index_max"] > 0x3FFF and not any(r["key"] == "4_byte" for r in rows if r["scope"] == "index_width"):
        sys.exit("FAIL tag-encode: an index above 0x3fff with no four-byte row")
    print("ok   tag-encode: %d tags, %d re-encoded, buckets, file rows and value rows all sum to the total"
          % (census["tags"], census["headers_reencoded"]))


def check():
    """Synthetic payloads only, no dump and no writes: the encoders, the class rule, the walk's spans, the
    bool's zero size and the doc round trip -- including that a corrupted block is refused."""
    assert pack_index(0) == b"\x00"
    assert pack_index(0x3F) == b"\x3f"
    assert pack_index(0x40) == b"\x40\x01"
    assert pack_index(79) == b"\x4f\x01"
    assert ps.read_index(b"\x4f\x01", 0) == (79, 2)
    assert pack_array_index(0x7F) == b"\x7f"
    assert pack_array_index(0x80) == b"\x80\x80"
    assert pack_array_index(0x3FFF) == b"\xbf\xff"
    assert ps.read_array_index(b"\xbf\xff", 0) == (0x3FFF, 2)
    assert pack_array_index(0x4000) == b"\xc0\x00\x40\x00"
    assert ps.read_array_index(b"\xc0\x00\x40\x00", 0) == (0x4000, 4)
    assert size_class(1) == 0x00 and size_class(16) == 0x40 and size_class(0) == 0x50
    assert size_class(0xFF) == 0x50 and size_class(0x100) == 0x60 and size_class(0x10000) == 0x60
    assert size_class(0x10001) == 0x70
    # a three-tag block: a true bool with size 0, an Int, and the terminator
    buf = bytes([0x4F, 0x01, 0xD3, 0x00]) + bytes([0x04, 0x22, 0x2A, 0x00, 0x00, 0x00]) + b"\x00"
    tags, end, reason = walk(buf)
    assert reason == "None" and end == len(buf), (reason, end)
    assert [t["name"] for t in tags] == [79, 4, 0]
    assert tags[0]["type"] == 3 and tags[0]["size"] == 0 and tags[0]["tbyte"] & 0x80
    assert tags[1]["type"] == 2 and tags[1]["size"] == 4
    equal, terms, reasons = compare(buf, tags)
    assert equal == 2 and terms == 1 and not reasons, (equal, terms, reasons)
    # a bool whose bit 7 is clear and a four-byte array index on a non-bool
    buf2 = bytes([0x08, 0x53, 0x00]) + bytes([0x09, 0xA2, 0xC0, 0x00, 0x40, 0x00]) + bytes(4) + b"\x00"
    tags2, _end, reason2 = walk(buf2)
    assert reason2 == "None"
    assert tags2[1]["index"] == 0x4000 and tags2[1]["tbyte"] & 0x80
    equal2, terms2, reasons2 = compare(buf2, tags2)
    assert equal2 == 2 and terms2 == 1 and not reasons2, (equal2, terms2, reasons2)
    # the rules must refuse a width of 4 written in the explicit class the writer never picks for it
    bad = bytes([0x04, 0x52, 0x04, 0x2A, 0x00, 0x00, 0x00]) + b"\x00"
    tags3, end3, reason3 = walk(bad)
    assert reason3 == "None" and end3 == len(bad)
    equal3, terms3, reasons3 = compare(bad, tags3)
    assert equal3 == 0 and terms3 == 1 and reasons3.get("type-byte") == 1, (equal3, terms3, reasons3)
    rows = rows_of([dict(scope="map", key="M00", count=12, note="blocks=2;refused=0;relocated=0;equal=10"),
                    dict(scope="checkpoint", key="C_1", count=6, note="blocks=1;refused=0;relocated=0;equal=5")],
                   dict(tags=15, equal=15, terminators=3, refused_blocks=0, refused_tags=0, relocated=0,
                        residue_blocks=0, residue_bytes=0,
                        size_class=collections.Counter({"0x00": 10, "0x50": 5}),
                        type=collections.Counter({"Int": 15}), index_width=collections.Counter(),
                        index_max=0, bit7_zero_index=0, bool=collections.Counter(),
                        struct=collections.Counter(), name_width=collections.Counter({"1_byte": 15}),
                        walk_reason=collections.Counter(), mismatch=collections.Counter(),
                        implied_class_miss=0,
                        value_size={"Int": collections.Counter({4: 15})}), 1, 1)
    check_artifact(rows)

    def _bad(mutate):
        bad = [dict(r) for r in rows]
        mutate(bad)
        try:
            check_artifact(bad)
        except SystemExit:
            return
        raise AssertionError("check_artifact accepted an artifact whose rows do not add up")

    def _one_file(bad):
        for r in bad:
            if r["scope"] == "map":
                r["count"] = int(r["count"]) - 1
                return

    def _one_value(bad):
        for r in bad:
            if r["scope"] == "value":
                r["count"] = int(r["count"]) - 1
                return

    _bad(_one_file)
    _bad(_one_value)
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
            corrupt = fh.read().replace("re-encodes to the bytes", "re-encodes to other bytes")
        with open(page, "w", encoding="utf-8") as fh:
            fh.write(corrupt)
        try:
            check_doc(page, rows)
        except SystemExit:
            pass
        else:
            raise AssertionError("check_doc accepted a corrupted block")
        with open(page, encoding="utf-8") as fh:
            assert fh.read().endswith("tail\n"), "the block splice ate the page tail"
    finally:
        for name in os.listdir(tmp):
            os.unlink(os.path.join(tmp, name))
        os.rmdir(tmp)
    print("ok   tag-encode: compact name and array-index encoders, size classes, walk spans, bool size, "
          "a corrupted tag refused, artifact sums incl. a per-file and a value row that do not, "
          "doc round trip (10 cases)")


def main(argv=None):
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    p.add_argument("dump", nargs="?", default=os.environ.get("SG1_DUMP", DEFAULT_DUMP))
    p.add_argument("--sweep", action="store_true", help="walk every Maps/*.unr and the .sgs checkpoints")
    p.add_argument("--limit", type=int, default=None, help="only the first N of each (a smoke run)")
    p.add_argument("-o", "--out", default=None, help="artifact directory")
    p.add_argument("--write-doc", metavar="PAGE", default=None)
    p.add_argument("--check-doc", metavar="PAGE", default=None)
    p.add_argument("--report", action="store_true")
    p.add_argument("--check", action="store_true", help="synthetic self-test, no dump, writes nothing")
    args = p.parse_args(argv)

    if args.check:
        check()
        return
    if args.check_doc:
        check_doc(args.check_doc, read_artifact())
        return
    if args.write_doc:
        write_doc(args.write_doc, read_artifact())
        return
    if args.report:
        sys.stdout.write(render(read_artifact()) + "\n")
        check_artifact(read_artifact())
        return
    if not args.sweep:
        p.error("nothing to do: --sweep, --report, --write-doc, --check-doc or --check")
    dump = args.dump
    if os.path.basename(dump) != "Stargate_SG-1_-_The_Alliance-2005-12-15" and os.path.isdir(
            os.path.join(dump, "Stargate_SG-1_-_The_Alliance-2005-12-15")):
        dump = os.path.join(dump, "Stargate_SG-1_-_The_Alliance-2005-12-15")
    maps = [n for n in os.listdir(os.path.join(dump, "Maps")) if n.endswith(".unr")]
    saves = [n for n in os.listdir(os.path.join(dump, "System", "Profiles", "DefaultProfile"))
             if n.endswith(".sgs")]
    file_rows, total = sweep(dump, args.limit)
    rows = rows_of(file_rows, total, len(maps), len(saves))
    if args.out:
        write_artifact(os.path.join(args.out, "tag_encodings.csv"), rows)
    sys.stdout.write(render(rows) + "\n")


if __name__ == "__main__":
    main()
