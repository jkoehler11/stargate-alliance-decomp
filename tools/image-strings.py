#!/usr/bin/env python3
"""One scanner for "which functions carry this subsystem's strings", with one entry per topic.

Each subsystem in this image announces itself: LIPSinc logs through `[LIPSinc]:` lines, the package check carries
`Packages.md5` and `PackageCheckInfo` (and still the `PackageCheckInfo.cpp` assert path), Karma carries `Karma*` strings
and `KarmaSupport.cpp`, and the console's one readable output carries `cur FPS` / `avg FPS`. This finds the strings,
attributes each `push` to the function containing it, walks callers where the question needs it, and writes one
artifact per topic.

A needle is a substring, so the address it is found at is *not* the address the code pushes (`cur FPS` sits at
`0x0051f35e`, eight bytes into `%u cur FPS` at `0x0051f358`). `needles()` therefore resolves every hit back to the
first character of its string -- without that, a topic whose needle is a suffix of the pushed literal reports zero
pushers, which reads as "nothing in the code uses this string" rather than "the search looked at the wrong address".

    python3 tools/image-strings.py --topic lipsinc        --xbe DUMP/.../default.xbe -o inventory
    python3 tools/image-strings.py --topic package-check  --xbe DUMP/.../default.xbe -o inventory --callers
    python3 tools/image-strings.py --topic karma          --xbe DUMP/.../default.xbe -o inventory
    python3 tools/image-strings.py --topic stat-fps       --xbe DUMP/.../default.xbe -o inventory
    python3 tools/image-strings.py --check                # every committed artifact, dump-free (in `make ci`)

A topic is a table entry, not a copy of this file: it names the artifacts, the needles, the columns and how to decide a
function belongs. Adding the next subsystem should be a dict literal.
"""
import argparse
import bisect
import csv
import os
import re
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SYMBOLS = os.path.join(ROOT, "symbols", "xbox_functions.csv")

# --- topic table ------------------------------------------------------------------------------------------------
# `needles` are searched as ascii and utf-16; `prefix` is stripped from the evidence text. `callers` walks `call rel32`
# sites one level (and, for package-check, a second level to answer "is the game class in the chain").
TOPICS = {
    "lipsinc": {
        "artifact": "lipsinc_runtime.csv",
        "needles": ("[LIPSinc]: ",),
        "strip": "[LIPSinc]: ",
        "columns": ["address", "size", "strings", "proposed_name"],
        "callers": False,
        "name_rule": "lipsinc",
    },
    # Which body reads which LIPSinc file, and whether anything in the image can enter it at all. The four
    # authoring-side carriers (the `.LBP` importer, the `.LTF` track reader, the `.map` writer and the level scan)
    # are told apart from the one game-side loader by their own literals; `reach` is what separates "present" from
    # "reachable" -- see docs/formats/lipsinc-lad.md.
    "lipsinc-formats": {
        "artifact": "lipsinc_formats.csv",
        "needles": ("obsolete .LBP", "LTF format", "[PhonemeToSpeechTargetMapping]", "*.lad", "Prefix: "),
        "strip": "",
        "columns": ["address", "size", "name", "evidence", "reach", "calls", "jumps", "refs", "sites"],
        "callers": False,
        "name_rule": None,
        "reach": True,
    },
    "package-check": {
        "artifact": "package_check_code.csv",
        "needles": ("Packages.md5", "PackageCheckInfo", "CheckInfo"),
        "strip": "",
        "columns": ["address", "size", "name", "evidence", "callers", "depth2", "game_reaches"],
        "callers": True,
        "depth2": True,
        "name_rule": None,
    },
    "karma": {
        "artifact": "karma_runtime.csv",
        "needles": ("Karma", "KarmaSupport.cpp"),
        "strip": "",
        "columns": ["address", "size", "name", "evidence", "callers"],
        "callers": True,
        "name_rule": None,
    },
    # PLAN 7.3's last question: what draws the console's one readable output. Both formats are UTF-16 in the
    # code section's own `.rdata`, and both are `push`ed, so this topic needs no boot -- see docs/XEMU.md.
    "stat-fps": {
        "artifact": "stat_fps_render.csv",
        "needles": ("cur FPS", "avg FPS"),
        "strip": "",
        "columns": ["address", "size", "name", "evidence", "callers"],
        "callers": True,
        "caller_names": True,
        "name_rule": None,
    },
}

# --- image access ----------------------------------------------------------------------------------------------
class Image:
    def __init__(self, path):
        sys.path.insert(0, os.path.join(ROOT, "tools"))
        from xbe import Xbe                                            # noqa: PLC0415 - only with a dump
        xbe = Xbe(path)
        self.data = xbe.data
        self.sections = [(s.va, s.foff, s.fsize) for s in xbe.sections]
        self.funcs = {int(r["address"], 16): (int(r["size"]), r["name"])
                      for r in csv.DictReader(open(SYMBOLS, encoding="utf-8"))}
        self.starts = sorted(self.funcs)

    def va_of(self, foff):
        for va, f, size in self.sections:
            if f <= foff < f + size:
                return va + (foff - f)
        return None

    def off_of(self, va):
        for v, f, size in self.sections:
            if v <= va < v + size:
                return f + (va - v)
        return None

    def owner(self, va):
        k = bisect.bisect_right(self.starts, va) - 1
        if k < 0:
            return None
        s = self.starts[k]
        return s if s <= va < s + self.funcs[s][0] else None

    def string_at(self, va, wide):
        off = self.off_of(va)
        if off is None:
            return ""
        out = []
        step = 2 if wide else 1
        while off + step <= len(self.data) and len(out) < 2048:      # a string ends at NUL; the cap is a runaway guard
            chunk = self.data[off:off + step]
            if chunk == b"\x00" * step:
                break
            out.append(chunk.decode("utf-16-le" if wide else "latin1", "replace"))
            off += step
        return "".join(out)

    def string_start(self, va, wide):
        """The address of the first character of the string a needle hit landed inside.

        A needle may be a suffix of the pushed literal (`cur FPS` sits inside `%u cur FPS`), while `push imm32`
        names the first character -- so a hit is walked back to the terminator behind it. The 256-unit cap keeps a
        needle found in a run of unterminated data from walking back to the start of the section; a non-printable
        unit stops the walk for the same reason (these literals are ASCII, stored wide).
        """
        off = self.off_of(va)
        if off is None:
            return va
        step = 2 if wide else 1
        for _ in range(256):
            prev = off - step
            if prev < 0:
                break
            unit = self.data[prev:prev + step]
            if unit == b"\x00" * step:
                break
            char = unit.decode("utf-16-le" if wide else "latin1", "replace")
            if char < " " or char > "~":
                break
            off = prev
        back = self.va_of(off)
        return va if back is None else back

    def needles(self, needles):
        """{string start va: text} for every string occurrence, ascii and utf-16."""
        found = {}
        for needle in needles:
            for enc, wide in (("ascii", False), ("utf-16-le", True)):
                blob = needle.encode(enc)
                start = 0
                while True:
                    i = self.data.find(blob, start)
                    if i < 0:
                        break
                    va = self.va_of(i)
                    if va is not None:
                        first = self.string_start(va, wide)
                        found[first] = self.string_at(first, wide)
                    start = i + 1
        return found

    def string_spans(self, strings):
        """[(start, end, wide, text)] -- the byte range each found literal occupies.

        `strings` is keyed by the walk-back's idea of a literal's first character, and that walk can carry a unit or
        two of whatever precedes the literal: this image's wide pool sits behind runs of tiny floats whose high word
        is 0x0023, which decode as `#` and are printable. So a literal's reported start can be one character early,
        and a `push` of the literal's real first character then matches no key at all.
        """
        spans = []
        for va, text in strings.items():
            for wide in (True, False):
                if self.string_at(va, wide) == text:
                    break
            spans.append((va, va + len(text) * (2 if wide else 1), wide, text))
        return spans

    def resolve_push(self, target, spans):
        """(start, wide) of the literal a pushed address falls inside, or None.

        Matching by containment rather than equality is what keeps a literal whose push names an interior address:
        the push is still the code's own reference, and the nearest literal start at or below it is the literal.
        """
        best = None
        for start, end, wide, _text in spans:
            if start <= target < end and (best is None or start > best[0]):
                best = (start, wide)
        return best

    def pushers(self, spans):
        """{function: {pushed address}} -- `push imm32` is how a log/format string reaches a call site."""
        hits = {}
        for off in range(len(self.data) - 5):
            if self.data[off] != 0x68:
                continue
            target = struct.unpack_from("<I", self.data, off + 1)[0]
            if self.resolve_push(target, spans) is None:
                continue
            va = self.va_of(off)
            if va is None:
                continue
            f = self.owner(va)
            if f is not None:
                hits.setdefault(f, set()).add(target)
        return hits

    def callers_of(self, wanted):
        """{target: {caller function}} from `call rel32` sites in the whole image."""
        out = {t: set() for t in wanted}
        for off in range(len(self.data) - 5):
            if self.data[off] != 0xE8:
                continue
            rel = struct.unpack_from("<i", self.data, off + 1)[0]
            va = self.va_of(off)
            if va is None:
                continue
            target = va + 5 + rel
            if target in out:
                f = self.owner(va)
                if f is not None and f != target:
                    out[target].add(f)
        return out


    def reach(self, targets):
        """{target: {"calls": [(site, owner)], "jumps": [site], "refs": [va]}} -- every way an address is entered.

        Three scans, because each one alone answers "nothing reaches it" for a body that is reached:

        * a direct call is `E8 rel32`, a **tail call is `E9 rel32`** -- counting only `E8` calls a jump-called body
          unreachable, and the LIPSinc readers sit next to exactly that shape;
        * a **dword** reference is how a vtable or a function-pointer table names a body, so a virtual is invisible
          to both call scans until its slot is read;
        * a site's owner may be `None`. A site in a region the symbol map does not bound is still a site, and
          dropping it is how `callers_of` prints "no caller" for a function that has one (measured on
          `LIPSincInitLevel`, whose single site `0x0015e237` belongs to no mapped body). Report the site and say
          the owner is unknown rather than losing the evidence.

        Targets are compared as integers; a 32-bit store anywhere in a file-backed section counts as a reference.
        """
        out = {t: {"calls": [], "jumps": [], "refs": []} for t in targets}
        data = self.data
        for off in range(len(data) - 5):
            op = data[off]
            if op != 0xE8 and op != 0xE9:
                continue
            rel = struct.unpack_from("<i", data, off + 1)[0]
            va = self.va_of(off)
            if va is None:
                continue
            target = va + 5 + rel
            if target not in out:
                continue
            if op == 0xE8:
                out[target]["calls"].append((va, self.owner(va)))
            else:
                out[target]["jumps"].append(va)
        for off in range(len(data) - 4):
            value = struct.unpack_from("<I", data, off)[0]
            if value in out:
                out[value]["refs"].append(self.va_of(off))
        return out


def propose_lipsinc(texts):
    """A name only where the module names itself; otherwise empty rather than a guess."""
    joined = " ".join(texts)
    for token in ("LIPSincInitLevel", "LIPSincInitGame", "LIPSincShutdownLevel", "LIPSincShutdownGame",
                  "LIPSincShutdownEditor", "LIPSincInitEditor"):
        if token in joined:
            return token
    if "LoadLIPSincDBFromDisk" in joined:
        return "LIPSincLoadDBFromDisk"
    if "Read Track" in joined and "Couldn't load file" in joined:
        return "LIPSincReadAnimFile"
    if "LIPSinc bone linkup failed" in joined and "obsolete .LBP" in joined:
        return "LIPSincReadControllerFile"
    if "PlayLIPSincAnim for" in joined:
        return "LIPSincPlayAnim"
    if "Linking Bones for" in joined:
        return "LIPSincLinkBones"
    if "Searching for .lad files" in joined:
        return "LIPSincSearchAnimFiles"
    return ""


def build(topic_name, xbe_path):
    topic = TOPICS[topic_name]
    image = Image(xbe_path)
    strings = image.needles(topic["needles"])
    spans = image.string_spans(strings)
    hits = image.pushers(spans)
    wanted = sorted(hits)
    first = image.callers_of(wanted) if topic.get("callers") else {}
    reached = image.reach(wanted) if topic.get("reach") else {}
    second = {}
    if topic.get("depth2"):
        deeper = image.callers_of(sorted({c for ts in first.values() for c in ts}))
        for f, ts in first.items():
            acc = set()
            for c in ts:
                acc |= deeper.get(c, set())
            second[f] = acc - set(ts)

    rows = []
    for f in wanted:
        size, name = image.funcs[f]
        # Read each literal at the address the code pushes, through the encoding that address belongs to: a push of
        # an interior character must not gain the prefix the walk-back added to the key.
        texts = sorted(image.string_at(t, image.resolve_push(t, spans)[1]) for t in hits[f])
        # A literal that carries CRLF (`[PhonemeToSpeechTargetMapping]\r\nNumSpeechTargets=%d\r\n`) must not put a
        # newline inside a CSV field: one row per line is what `--check` and a diff can read.
        texts = [t.replace("\r", " ").replace("\n", " ") for t in texts]
        row = {"address": "0x%08x" % f, "size": size, "name": name,
               "evidence": " | ".join(t.replace(topic["strip"], "", 1) if topic["strip"] else t for t in texts)}
        if topic["name_rule"] == "lipsinc":
            row["strings"] = row.pop("evidence")
            row["proposed_name"] = propose_lipsinc(texts)
        if topic.get("callers"):
            callers = sorted(first.get(f, set()))
            if topic.get("caller_names"):
                row["callers"] = " ".join("0x%08x %s" % (c, image.funcs.get(c, (0, "?"))[1]) for c in callers[:6])
            else:
                row["callers"] = " ".join("0x%08x" % c for c in callers[:6])
            if topic.get("depth2"):
                row["depth2"] = " ".join("0x%08x" % g for g in sorted(second.get(f, set()))[:6])
                chain = callers + sorted(second.get(f, set()))
                row["game_reaches"] = "true" if any(image.funcs[c][1].startswith("Engine_UnGame") for c in chain) else "false"
        if topic.get("reach"):
            r = reached[f]
            row["calls"] = len(r["calls"])
            row["jumps"] = len(r["jumps"])
            row["refs"] = len(r["refs"])
            # The verdict names the weakest way in, and a site whose owner the map cannot bound keeps its address
            # with `?` for the owner -- the site is the evidence, the name is a convenience.
            row["reach"] = "call" if r["calls"] else ("jump" if r["jumps"] else ("ref" if r["refs"] else "none"))
            row["sites"] = " ".join("0x%08x@%s" % (site, "?" if own is None else "0x%08x" % own)
                                    for site, own in r["calls"][:6])
        rows.append(row)
    return [ {c: r.get(c, "") for c in topic["columns"]} for r in rows ]


def check(topic_name, path):
    topic = TOPICS[topic_name]
    try:
        rows = list(csv.DictReader(open(path, encoding="utf-8")))
    except OSError as exc:
        print("FAIL image-strings: %s" % exc)
        return 1
    problems = []
    if not rows:
        problems.append("%s: no rows" % topic_name)
    for r in rows:
        if sorted(r) != sorted(topic["columns"]):
            problems.append("%s: columns" % topic_name)
            break
        evidence = r.get("evidence") or r.get("strings") or ""
        if not evidence:
            problems.append("%s: %s has no evidence" % (topic_name, r["address"]))
        if int(r["size"]) <= 0:
            problems.append("%s: %s has no size" % (topic_name, r["address"]))
        name = r.get("proposed_name", "")
        if name and not name.startswith("LIPSinc"):
            problems.append("%s: proposed name %r" % (r["address"], name))
        if topic.get("reach") and r.get("reach") not in ("call", "jump", "ref", "none"):
            problems.append("%s: %s has no reach verdict %r" % (topic_name, r["address"], r.get("reach")))
        if topic.get("reach") and r.get("reach") == "none" and (r.get("sites") or "").strip():
            problems.append("%s: %s is unreachable but names a site" % (topic_name, r["address"]))
    if problems:
        print("FAIL image-strings: " + "; ".join(problems[:5]))
        return 1
    print("ok   image-strings [%s]: %d functions carrying this subsystem's strings" % (topic_name, len(rows)))
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--topic", choices=sorted(TOPICS))
    ap.add_argument("--xbe")
    ap.add_argument("-o", "--out", default=os.path.join(ROOT, "inventory"))
    ap.add_argument("--check", action="store_true", help="validate every committed artifact (no dump needed)")
    args = ap.parse_args()
    if args.check:
        rc = 0
        for topic in sorted(TOPICS):
            rc |= check(topic, os.path.join(args.out, TOPICS[topic]["artifact"]))
        return rc
    if not args.topic or not args.xbe:
        ap.error("--topic and --xbe are required unless --check")
    rows = build(args.topic, args.xbe)
    os.makedirs(args.out, exist_ok=True)
    path = os.path.join(args.out, TOPICS[args.topic]["artifact"])
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=TOPICS[args.topic]["columns"], lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    print("%d functions -> %s" % (len(rows), path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
