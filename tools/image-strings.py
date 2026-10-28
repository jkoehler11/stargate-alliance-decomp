#!/usr/bin/env python3
"""One scanner for "which functions carry this subsystem's strings", with one entry per topic.

Each subsystem in this image announces itself: LIPSinc logs through `[LIPSinc]:` lines, the package check carries
`Packages.md5` and `PackageCheckInfo` (and still the `PackageCheckInfo.cpp` assert path), Karma carries `Karma*` strings
and `KarmaSupport.cpp`. This finds the strings, attributes each `push` to the function containing it, walks callers
where the question needs it, and writes one artifact per topic.

    python3 tools/image-strings.py --topic lipsinc        --xbe DUMP/.../default.xbe -o inventory
    python3 tools/image-strings.py --topic package-check  --xbe DUMP/.../default.xbe -o inventory --callers
    python3 tools/image-strings.py --topic karma          --xbe DUMP/.../default.xbe -o inventory
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

    def needles(self, needles):
        """{va: text} for every string occurrence, ascii and utf-16."""
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
                        found[va] = self.string_at(va, wide)
                    start = i + 1
        return found

    def pushers(self, targets):
        """{function: {string va}} -- `push imm32` is how a log/format string reaches a call site."""
        hits = {}
        for off in range(len(self.data) - 5):
            if self.data[off] != 0x68:
                continue
            target = struct.unpack_from("<I", self.data, off + 1)[0]
            if target not in targets:
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
    hits = image.pushers(strings)
    wanted = sorted(hits)
    first = image.callers_of(wanted) if topic.get("callers") else {}
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
        texts = sorted(strings[t] for t in hits[f])
        row = {"address": "0x%08x" % f, "size": size, "name": name,
               "evidence": " | ".join(t.replace(topic["strip"], "", 1) if topic["strip"] else t for t in texts)}
        if topic["name_rule"] == "lipsinc":
            row["strings"] = row.pop("evidence")
            row["proposed_name"] = propose_lipsinc(texts)
        if topic.get("callers"):
            callers = sorted(first.get(f, set()))
            row["callers"] = " ".join("0x%08x" % c for c in callers[:6])
            if topic.get("depth2"):
                row["depth2"] = " ".join("0x%08x" % g for g in sorted(second.get(f, set()))[:6])
                chain = callers + sorted(second.get(f, set()))
                row["game_reaches"] = "true" if any(image.funcs[c][1].startswith("Engine_UnGame") for c in chain) else "false"
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
