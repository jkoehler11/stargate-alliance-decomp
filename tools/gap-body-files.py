#!/usr/bin/env python3
"""What a body says about the file it was compiled in, and the channel the gap heads do not carry.

`docs/UNKNOWNS.md`'s U40 ends on a name for 1,249 bodies the function map has no row for, and the
naming half of the interface population ends on the same thing: an interface's own name is the name of
a body, and a body nothing names cannot name the interface it implements. The map itself derives its
`<Module>_<File>` names from the build's `__FILE__` assert paths, so the question a per-body read has
to answer is whether a body *holds* that reference.

Three channels, read at the body instead of at the map's name:

  * **`assert`** -- a code site holding the address of one of the image's assert-path strings
    (`alienbrainWork\\Stargate-sg1\\Game\\GameXBOX\\<Module>\\Src\\<File>.cpp`). This is the reference
    the map's own `<Module>_<File>` names are built from, so on the map's population it is a control:
    a body's name and the path it references have to be the same file.
  * **`string`** -- a code site holding the address of any printable string in the image (a UTF-16 run
    of six characters or more, or a narrow run of eight). A body that pushes `invalid stencil op` is a
    D3DDrv body whatever else is unknown about it.
  * **`callee`** -- the `<Module>_<File>` tokens of the map bodies a body calls through a direct
    `E8 rel32`. This places a body in a module and never names it (that channel is already measured in
    `tools/class-interface-behaviour.py`), and an `E8` byte inside another instruction's displacement
    can encode a false call, so it is reported as an upper bound with the map's own rate beside it.

Both ends are in one artifact: the 1,249 gap-head bodies U40 decodes, and a `census` row carrying the
control's counts, which is what lets the page be rebuilt from the committed CSV with no dump.

    SG1_DUMP=$HOME/nfs/sg1-dump python3 tools/gap-body-files.py \\
        --xbe "$SG1_DUMP/Stargate_SG-1_-_The_Alliance-2005-12-15/default.xbe" -o inventory
    python3 tools/gap-body-files.py --report
    python3 tools/gap-body-files.py --write-doc docs/systems/CLASS_REGISTRATION.md
    python3 tools/gap-body-files.py --check-doc docs/systems/CLASS_REGISTRATION.md
    python3 tools/gap-body-files.py --check        # synthetic, no dump, writes nothing

Addresses, file names, string heads and counts only; no package or image content is written.
"""
import argparse
import bisect
import collections
import csv
import importlib.util
import os
import re
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

SYMBOLS = os.path.join(ROOT, "symbols", "xbox_functions.csv")
HEADS = os.path.join(ROOT, "inventory", "gap_heads.csv")
REP = os.path.join(ROOT, "inventory", "class_interface_rep_bodies.csv")
OUT = os.path.join(ROOT, "inventory", "gap_body_files.csv")
DOC = os.path.join(ROOT, "docs", "systems", "CLASS_REGISTRATION.md")
DOC_A = ("<!-- generated:gap-body-files -->", "<!-- /generated:gap-body-files -->")

BODY = ("body_va", "size", "assert_file", "assert_sites", "string_sites", "string_head",
        "callee_file", "callee_files", "verdict")
CENSUS = ("heads", "bodies", "islands", "unclosed", "with_assert", "with_string", "with_callee", "silent",
          "rep_heads", "rep_bodies", "rep_islands", "rep_assert", "rep_string", "rep_callee", "map_bodies", "map_assert",
          "map_string", "map_callee", "name_pairs", "name_agree", "name_other", "name_wrong", "multi_files")
WIDE = re.compile(rb"(?:[ -~]\x00){6,}")
NARROW = re.compile(rb"[ -~]{8,}")
TOKEN = re.compile(r"^([A-Za-z][A-Za-z0-9]*)_([A-Za-z0-9_]+)$")
MAX_HEAD = 40


def read_csv(path):
    if not path or not os.path.isfile(path):
        return []
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def load_symbols(path):
    names, sizes = {}, {}
    for row in read_csv(path):
        try:
            va = int(row["address"], 16)
        except (KeyError, ValueError):
            continue
        names[va] = row.get("name") or ""
        try:
            sizes[va] = int(row.get("size") or 0)
        except ValueError:
            sizes[va] = 0
    return names, sizes


def load_heads(path):
    """{head_va: (size, verdict)} for every gap head the rule decodes, body or not."""
    out = {}
    for row in read_csv(path):
        try:
            va = int(row["head_va"], 16)
            size = int(row.get("size") or 0)
        except (KeyError, ValueError):
            continue
        if size > 0 and row.get("verdict"):
            out[va] = (size, row["verdict"])
    return out


class Bodies:
    """Body extents (map rows and decoded gap heads) and the body a site falls inside."""

    def __init__(self, extents):
        self.extents = dict(extents)
        self.starts = sorted(self.extents)

    def at(self, va):
        i = bisect.bisect_right(self.starts, va) - 1
        if i < 0:
            return None
        start = self.starts[i]
        return start if start <= va < start + self.extents[start] else None


def name_token(name, modules):
    """`<Module>_<File>` -> (module, file) when the prefix is one of the build's modules."""
    m = TOKEN.match(name or "")
    if not m:
        return None
    module = m.group(1)
    for known in modules:
        if module.lower() == known.lower():
            return known, m.group(2)
    return None


def e8_targets(blob, va):
    """{target_va: [site_vas]} for every `E8 rel32` in `blob`; the caller decides which targets count.

    The range is deliberately not bounded here: a call leaves its own body, and a rule that kept only
    targets inside it would answer zero calls by construction.
    """
    out = collections.defaultdict(list)
    for i in range(len(blob) - 4):
        if blob[i] != 0xE8:
            continue
        disp = int.from_bytes(blob[i + 1:i + 5], "little", signed=True)
        out[va + i + 5 + disp].append(va + i)
    return out


def strings_of(image):
    """{string_va: text} for every printable run in the image, wide runs first."""
    out = {}
    for s in image.sections:
        blob = image.data[s.foff:s.foff + min(s.fsize, s.vsize)]
        for m in WIDE.finditer(blob):
            try:
                text = m.group().decode("utf-16-le")
            except UnicodeDecodeError:
                continue
            out.setdefault(s.va + m.start(), text)
        for m in NARROW.finditer(blob):
            out.setdefault(s.va + m.start(), m.group().decode("latin-1"))
    return out


def dword_sites(image, starts):
    """[(site_va, string_va)] for every 4-aligned dword in the image that names a string start."""
    out = []
    for s in image.sections:
        blob = image.data[s.foff:s.foff + min(s.fsize, s.vsize)]
        n = len(blob) - 3
        for i in range(0, n, 4):
            v = int.from_bytes(blob[i:i + 4], "little")
            if v in starts:
                out.append((s.va + i, v))
    return out


def assert_refs(image):
    """{string_va: (module, file)} for the image's assert paths, plus {string_va: path}."""
    by_va, paths = {}, {}
    for path, info in image.assert_paths().items():
        mods = sorted(m for m in info["modules"] if m)
        module = mods[0] if mods else "?"
        stem = os.path.basename(path).rsplit(".", 1)[0]
        by_va[info["va"]] = (module, stem)
        paths[info["va"]] = path
    return by_va, paths


def bucket(sites, bodies):
    """{(body_start, channel): [(site, value)]} -- sites with no owner are dropped."""
    out = collections.defaultdict(list)
    for site, value, channel in sites:
        owner = bodies.at(site)
        if owner is not None:
            out[(owner, channel)].append((site, value))
    return out


def measure(image, names, sizes, heads, modules):
    """The whole read: every body's extent, the three channels, and the control's counts."""
    extents = {va: n for va, n in sizes.items() if n}
    for va, (n, _) in heads.items():
        extents.setdefault(va, n)
    bmap = Bodies(extents)
    gap = {va: n for va, (n, verdict) in heads.items() if verdict == "body"}

    a_by_va, _ = assert_refs(image)
    strings = strings_of(image)
    sites = []
    for sva in a_by_va:                      # the assert channel, by the map's own string index
        for site in image.xrefs_to(sva):
            sites.append((site, sva, "assert"))
    n_assert_sites = len(sites)
    for site, sva in dword_sites(image, strings):
        sites.append((site, sva, "string"))
    n_string_sites = len(sites) - n_assert_sites
    hits = bucket(sites, bmap)

    callee = collections.defaultdict(list)
    for va, n in extents.items():
        blob = image.read_va(va, n)
        if len(blob) < 5:
            continue
        for target, callers in e8_targets(blob, va).items():
            if target in names and names[target]:
                for site in callers:
                    callee[va].append(target)

    rows = []
    for va in sorted(heads):
        size, verdict_head = heads[va]
        files = [a_by_va[sva] for _, sva in hits.get((va, "assert"), [])]
        assert_set = sorted({f"{m}/{f}" for m, f in files})
        strs = [strings[sva] for _, sva in hits.get((va, "string"), [])]
        tokens = []
        for target in callee.get(va, []):
            tok = name_token(names[target], modules)
            if tok and f"{tok[0]}_{tok[1]}" not in tokens:
                tokens.append(f"{tok[0]}_{tok[1]}")
        head = " | ".join(t.replace("|", "/")[:MAX_HEAD] for t in strs[:2])
        verdict = ("assert-file" if assert_set else
                   "string" if strs else
                   "callee-file" if tokens else "silent")
        if verdict_head != "body":
            verdict = verdict_head
        rows.append({"body_va": f"{va:#010x}", "size": str(size),
                     "assert_file": " ".join(assert_set),
                     "assert_sites": " ".join(f"{site:#010x}" for site, _ in hits.get((va, "assert"), [])),
                     "string_sites": str(len(strs)), "string_head": head,
                     "callee_file": tokens[0] if len(tokens) == 1 else "",
                     "callee_files": " ".join(tokens[:3]), "verdict": verdict})

    def count(pop, channel):
        return sum(1 for va in pop if hits.get((va, channel)))

    map_bodies = {va: n for va, n in sizes.items() if n}
    island = {va for va, (_, v) in heads.items() if v == "island"}
    unclosed = {va: n for va, (n, v) in heads.items() if v not in ("body", "island")}
    census = {
        "heads": len(heads),
        "bodies": len(gap),
        "islands": len(island),
        "unclosed": len(unclosed),
        "with_assert": count(gap, "assert"),
        "with_string": count(gap, "string"),
        "with_callee": sum(1 for va in gap if callee.get(va)),
        "silent": sum(1 for r in rows if r["verdict"] == "silent"),
        "map_bodies": len(map_bodies),
        "map_assert": count(map_bodies, "assert"),
        "map_string": count(map_bodies, "string"),
        "map_callee": sum(1 for va in map_bodies if callee.get(va)),
        "multi_files": sum(1 for r in rows if " " in r["assert_file"]),
    }
    pairs = agree = other = wrong = 0
    for va in map_bodies:
        tok = name_token(names.get(va, ""), modules)
        if not tok:
            continue
        got = {f for _, sva in hits.get((va, "assert"), [])
               for m, f in [a_by_va[sva]]}
        pairs += 1
        if tok[1] in got:
            agree += 1
        elif got:
            wrong += 1
        else:
            other += 1
    census.update({"name_pairs": pairs, "name_agree": agree, "name_other": other, "name_wrong": wrong})
    rep = {int(r["body_va"], 16) for r in read_csv(REP)
           if r.get("kind") == "gap" and r.get("body_va")}
    rep_body = rep & set(gap)
    census["rep_heads"] = len(rep)
    census["rep_bodies"] = len(rep_body)
    census["rep_islands"] = len(rep & island)
    census["rep_assert"] = sum(1 for va in rep_body if hits.get((va, "assert")))
    census["rep_string"] = sum(1 for va in rep_body if hits.get((va, "string")))
    census["rep_callee"] = sum(1 for va in rep_body if callee.get(va))
    print(f"{len(rows)} gap-head rows; {census['with_assert']} assert, {census['with_string']} string, "
          f"{census['with_callee']} callee, {census['silent']} silent; "
          f"map control {census['map_assert']} assert / {census['map_string']} string of {census['map_bodies']}; "
          f"name agreement {agree} of {pairs}")
    return rows, census


def write_artifact(rows, census, path):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=BODY + CENSUS)
        w.writeheader()
        w.writerows(rows)
        row = {k: "" for k in BODY + CENSUS}
        row["verdict"] = "census"
        for key, value in census.items():
            row[key] = str(value)
        w.writerow(row)


def read_artifact(path):
    rows = read_csv(path)
    census = {}
    out = []
    for r in rows:
        if r.get("verdict") == "census":
            for key in CENSUS:
                if r.get(key) not in (None, ""):
                    census[key] = int(r[key])
        else:
            out.append({k: r.get(k) or "" for k in BODY})
    return out, census


def render(rows, census):
    hits = [r for r in rows if r["verdict"] not in ("silent", "island") and r["verdict"] != "island"
            and not r["verdict"].startswith("no")]
    hits.sort(key=lambda r: (r["verdict"] != "assert-file", -int(r["string_sites"] or 0), r["body_va"]))
    lines = ["| gap head | size | names | the reference |", "|---|---|---|---|"]
    for r in hits[:60]:
        what = r["assert_file"] or r["string_head"] or r["callee_files"]
        how = ("assert path at " + " ".join(r["assert_sites"].split())) if r["assert_file"] else (
            f"{r['string_sites']} string site(s)" if r["string_head"] else "a call into a named body")
        lines.append(f"| `{r['body_va']}` | {r['size']} | {what} | {how} |")
    if len(hits) > 60:
        lines.append(f"| ... | | {len(hits) - 60} more | |")
    lines.append("")
    lines.append(
        f"**{census['heads']} heads decode as {census['bodies']} bodies and {census['islands']} islands; of the "
        f"bodies {census['with_assert']} hold an assert path, {census['with_string']} a printable string and "
        f"{census['with_callee']} a call into a body the map names, and {census['silent']} hold none of the "
        f"three.**")
    lines.append("")
    lines.append(
        f"**Control: {census['map_assert']} of the map's {census['map_bodies']} bodies hold an assert site, and "
        f"{census['name_agree']} of the {census['name_pairs']} `<Module>_<File>` bodies carry the file their own "
        f"name is built from, {census['name_wrong']} carry a different one and {census['name_other']} reference no "
        f"assert path at all -- so the name is that reference read one level out.**")
    lines.append("")
    lines.append(
        f"**The owed interface representatives' slots hold {census['rep_heads']} of these heads and only "
        f"{census['rep_bodies']} are bodies -- {census['rep_islands']} are islands and the rest never close -- "
        f"so the interface population's gap-head slots over-count their methods by "
        f"{census['rep_heads'] - census['rep_bodies']}; of the {census['rep_bodies']} bodies {census['rep_assert']} "
        f"hold an assert path, {census['rep_string']} a string and {census['rep_callee']} a call into a named "
        f"body, and the per-body read cannot name an interface (U40).**")
    return "\n".join(lines)


def splice_doc(path, block, marks):
    text = open(path, encoding="utf-8").read()
    i, j = text.find(marks[0]), text.find(marks[1])
    if i < 0 or j < 0:
        return None
    return text[:i] + marks[0] + "\n" + block + "\n" + text[j:]


def self_check():
    bad = []
    extents = {0x1000: 0x20, 0x1100: 0x10, 0x1200: 0x40}
    b = Bodies(extents)
    if b.at(0x1000) != 0x1000 or b.at(0x101F) != 0x1000:
        bad.append("a site inside a body is that body")
    if b.at(0x1020) is not None or b.at(0x10FF) is not None:
        bad.append("a site in the hole between bodies belongs to no body")
    if b.at(0x123F) != 0x1200 or b.at(0x1240) is not None:
        bad.append("a body ends at its own size")
    modules = {"Engine", "Core", "IpDrv"}
    if name_token("Engine_UnChan", modules) != ("Engine", "UnChan"):
        bad.append("a module-file name is read as its two halves")
    if name_token("ucpp_Register_GoauldDrone", modules) is not None:
        bad.append("a name whose prefix is not a module is not a file token")
    if name_token("FUN_00011000", modules) is not None:
        bad.append("an unnamed body carries no token")
    targets = e8_targets(b"\xe8\x00\x00\x00\x00\x90\xe8\xfb\xff\xff\xff", 0x1000)
    if dict(targets) != {0x1005: [0x1000], 0x1006: [0x1006]}:
        bad.append("E8 targets are the next instruction plus the displacement")
    far = e8_targets(b"\x90\xe8\x00\x10\x00\x00", 0x1000)
    if dict(far) != {0x2006: [0x1001]}:
        bad.append("a call target outside the body is still reported for the caller to judge")
    if e8_targets(b"\x90\x90", 0x1000):
        bad.append("no E8 is no call")
    hits = bucket([(0x1004, 7, "assert"), (0x1020, 8, "string")], b)
    if hits != {(0x1000, "assert"): [(0x1004, 7)]}:
        bad.append("a site outside every body is dropped, not attributed")
    rows = [{"body_va": "0x00000100", "size": "16", "assert_file": "Engine/UnChan", "assert_sites": "0x00000104",
             "string_sites": "0", "string_head": "", "callee_file": "", "callee_files": "",
             "verdict": "assert-file"},
            {"body_va": "0x00000200", "size": "8", "assert_file": "", "assert_sites": "0",
             "string_sites": "2", "string_head": "invalid stencil op | D3D", "callee_file": "",
             "callee_files": "", "verdict": "string"},
            {"body_va": "0x00000300", "size": "12", "assert_file": "", "assert_sites": "0",
             "string_sites": "0", "string_head": "", "callee_file": "Core_UnObj",
             "callee_files": "Core_UnObj", "verdict": "callee-file"},
            {"body_va": "0x00000400", "size": "6", "assert_file": "", "assert_sites": "0",
             "string_sites": "0", "string_head": "", "callee_file": "", "callee_files": "",
             "verdict": "silent"},
            {"body_va": "0x00000500", "size": "3", "assert_file": "", "assert_sites": "0",
             "string_sites": "0", "string_head": "", "callee_file": "", "callee_files": "",
             "verdict": "island"}]
    census = {"heads": 5, "bodies": 4, "islands": 1, "unclosed": 0, "with_assert": 1, "with_string": 1,
              "with_callee": 1, "silent": 1, "rep_heads": 2, "rep_bodies": 1, "rep_islands": 1, "rep_assert": 0,
              "rep_string": 1, "rep_callee": 0, "map_bodies": 9, "map_assert": 3, "map_string": 4,
              "map_callee": 5, "name_pairs": 6, "name_agree": 5, "name_other": 1, "name_wrong": 0,
              "multi_files": 0}
    text = render(rows, census)
    for want in ("`0x00000100`", "invalid stencil op", "Control: 3 of the map's 9", "5 of the 6",
                 "0 carry a different one", "hold an assert path, 1 a string and 0 a call"):
        if want not in text:
            bad.append("render lacks %r" % (want,))
    if "`0x00000500`" in text:
        bad.append("an island is not rendered as a body the read names")
    tmp = tempfile.mkdtemp()
    path = os.path.join(tmp, "g.csv")
    write_artifact(rows, census, path)
    back, back_c = read_artifact(path)
    if back != rows:
        bad.append("the artifact round trip differs")
    if back_c != census:
        bad.append("the census row does not round trip")
    if bad:
        print("FAIL gap-body-files: " + "; ".join(bad))
        return 1
    print("ok   gap-body-files: the body lookup, the name token, the E8 target, the owner drop, all four "
          "verdicts, the render and both artifact round trips agree with the synthetic rows")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--xbe")
    ap.add_argument("-o", "--out")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--write-doc")
    ap.add_argument("--check-doc")
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    if args.check:
        return self_check()
    if args.check_doc:
        rows, census = read_artifact(OUT)
        if not rows or not census:
            print("FAIL gap-body-files: %s is missing or has no census row" % OUT)
            return 1
        block = render(rows, census)
        text = open(args.check_doc, encoding="utf-8").read()
        i, j = text.find(DOC_A[0]), text.find(DOC_A[1])
        if i < 0 or j < 0:
            print("FAIL gap-body-files: %s has no %s block" % (args.check_doc, DOC_A[0]))
            return 1
        if text[i:j + len(DOC_A[1])] != DOC_A[0] + "\n" + block + "\n" + DOC_A[1]:
            print("FAIL gap-body-files: %s's block does not reproduce" % args.check_doc)
            return 1
        print("ok   gap-body-files: %s reproduces from the committed CSV" % args.check_doc)
        return 0
    if args.report or args.write_doc:
        rows, census = read_artifact(OUT)
        if not rows or not census:
            print("FAIL gap-body-files: %s is missing or has no census row" % OUT)
            return 1
        block = render(rows, census)
        if args.report:
            print(block)
            return 0
        new = splice_doc(args.write_doc, block, DOC_A)
        if new is None:
            print("FAIL gap-body-files: %s has no %s markers" % (args.write_doc, DOC_A[0]))
            return 1
        if new != open(args.write_doc, encoding="utf-8").read():
            open(args.write_doc, "w", encoding="utf-8").write(new)
            print("wrote %s (%s)" % (args.write_doc, DOC_A[0]))
        return 0
    if not args.xbe:
        ap.error("--xbe is required to read the image (or use --report/--check-doc/--check)")

    spec = importlib.util.spec_from_file_location("xbe", os.path.join(ROOT, "tools", "xbe.py"))
    xbemod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(xbemod)
    image = xbemod.Xbe(args.xbe)
    names, sizes = load_symbols(SYMBOLS)
    heads = load_heads(HEADS)
    modules = {row.get("module") for row in read_csv(SYMBOLS) if row.get("module")}
    modules.update({"Engine", "Core", "IpDrv", "Gui", "D3DDrv", "XboxDrv", "XboxAudio", "XboxLive",
                    "Gameplay", "Fire", "stargategame", "stargatevehicles"})
    rows, census = measure(image, names, sizes, heads, modules)
    out = os.path.join(args.out, os.path.basename(OUT)) if args.out else OUT
    write_artifact(rows, census, out)
    print("wrote %s" % out)
    return 0


if __name__ == "__main__":
    sys.exit(main())
