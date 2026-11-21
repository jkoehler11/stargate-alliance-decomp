#!/usr/bin/env python3
"""Does a supplied library's symbol name an interface table? The slot's own class, voted.

The five naming channels this project has read for the 232 interface tables all failed for the same
reason: none of them is a *name* in the image.  The assert token, the callee's file, the pushed literal,
the slot body's own file and the RTTI locator are all shut (`docs/systems/CLASS_REGISTRATION.md`), and
the sixth, the ctor symbol on the store body, names 3 tables because the other store bodies are `FUN_`
bodies.

There is a sixth channel that is not a name in the *image* but in the **library** the image links: a
UIX/XMO class is in no package, so its methods were statically linked out of `uix.lib`/`xvoice.lib`, and
`inventory/xdk_lib_names.csv` already holds the mangled symbol of every library body `tools/xdk-lib-match.py`
matched.  A table's slot points at a body, and that body's symbol says which class the method belongs to
(`?CreateScreen@CScreen@UIXNamespace@@UAEJXZ` is `CScreen`'s).  A table is a class's vtable, so its slot
methods are that class's methods -- which makes the majority of the named slots a *vote* for the class.

The rule is the one `tools/xdk-call-names.py` uses for its callers: the top class must hold a strict
majority of the named slots, a tie is refused, and a name on exactly one named slot is reported apart as
`thin` because a single inherited method is not a vote.  A free function at namespace scope
(`?f@UIXNamespace@@`) carries a namespace where a method carries its class, so a two-token name votes
nothing.

Three controls, all independent of the vote:

  * the tables a **ctor symbol** already labels (`inventory/class_interface_installers.csv`'s
    `holder_class`, from `??0CClass@...`) -- the vote must reproduce the class the symbol gave;
  * the **492 class tables**, whose slots are game code no supplied library contains: 0 of them may vote,
    so a name this tool reports cannot come from reading a vtable it was not given;
  * the **losses** are kept, so a table the vote cannot name says which classes it was split between.

    SG1_DUMP=$HOME/nfs/sg1-dump python3 tools/interface-lib-names.py \\
        --xbe "$SG1_DUMP/Stargate_SG-1_-_The_Alliance-2005-12-15/default.xbe" -o inventory
    python3 tools/interface-lib-names.py --report
    python3 tools/interface-lib-names.py --write-doc docs/systems/CLASS_REGISTRATION.md
    python3 tools/interface-lib-names.py --check-doc docs/systems/CLASS_REGISTRATION.md
    python3 tools/interface-lib-names.py --check     # synthetic votes, no dump, writes nothing

Addresses, counts and decorated library symbols only; no image or package content is written.
"""
import argparse
import collections
import csv
import os
import re
import struct
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

SHARED = os.path.join(ROOT, "inventory", "native_vtable_shared.csv")
INSTALLERS = os.path.join(ROOT, "inventory", "class_interface_installers.csv")
MEMBERS = os.path.join(ROOT, "inventory", "class_member_tables.csv")
CLASS_TABLES = os.path.join(ROOT, "inventory", "xbe_class_tables.csv")
CLASS_NAMES = os.path.join(ROOT, "inventory", "xbe_class_table_names.csv")
XDK = os.path.join(ROOT, "inventory", "xdk_lib_names.csv")
OUT = os.path.join(ROOT, "inventory", "interface_lib_names.csv")
DOC_START = "<!-- generated:interface-lib-names -->"
DOC_END = "<!-- end:interface-lib-names -->"
FIELDS = ("table_va", "role", "owner_label", "slots", "named_slots", "classed", "class", "votes",
          "others", "verdict", "evidence")
VERDICTS = ("named", "thin", "split", "none")
HEX = re.compile(r"^(0x)?[0-9a-fA-F]+$")
CTOR = ("??0", "??1", "??_G", "??_E")


def read_csv(path):
    if not path or not os.path.isfile(path):
        return []
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def num(text, base=16):
    text = (text or "").strip()
    if not HEX.match(text):
        return None
    return int(text, base)


# --------------------------------------------------------------------------- the symbol, read as a class
def symbol_class(symbol):
    """The class an MSVC mangled *method* symbol belongs to, or ''.

    `?CreateScreen@CScreen@UIXNamespace@@UAEJXZ` is a method of `CScreen`: the tokens after the method
    are its class and then its namespace.  `?CreateScreen@UIXNamespace@@YAJXZ` is a free function, whose
    second token is the *namespace*, so a two-token name has no class and votes nothing.  A constructor,
    destructor or scalar-deleting destructor carries the class first (`??0CEncoderXMO@@QAE@XZ`), and a
    template (`??$`) is refused rather than guessed.  `__purecall` and a bare `FUN_` body have no mangled
    name at all, which is why the caller only asks about symbols the library supplied.
    """
    if not symbol or not symbol.startswith(("?", "@")) and not symbol.startswith("??"):
        return ""
    if symbol.startswith("??$"):
        return ""
    if symbol.startswith(CTOR):
        prefix = 4 if symbol.startswith(("??_G", "??_E")) else 3
        parts = symbol[prefix:].split("@@")[0].split("@")
        return parts[0] if parts and parts[0] else ""
    if not symbol.startswith("?"):
        return ""
    body = symbol[1:].split("@@")[0]
    parts = [p for p in body.split("@") if p]
    if len(parts) < 3:
        return ""
    return parts[1]


def vote(named_classes, slots):
    """(verdict, class, votes, others) over the classes the named slots carry."""
    counts = collections.Counter(named_classes)
    if not counts:
        return ("none", "", [], [])
    total = sum(counts.values())
    ordered = counts.most_common()
    best, top = ordered[0]
    others = [(k, v) for k, v in ordered[1:]]
    if top * 2 > total and top >= 2:
        return ("named", best, ordered, others)
    if len(ordered) == 1 and top == 1:
        return ("thin", best, ordered, others)
    return ("split", best, ordered, others)


# --------------------------------------------------------------------------- populations
def interface_tables():
    """The 232 interface tables, with the label another channel already gave them, if any."""
    members = {}
    for r in read_csv(MEMBERS):
        va = num(r.get("table_va"))
        if va is None:
            continue
        label = (r.get("member_name") or "").strip()
        if label:
            members[va] = label
    holders = {}
    for r in read_csv(INSTALLERS):
        va = num(r.get("table_va"))
        if va is None:
            continue
        holders[va] = ((r.get("holder_class") or "").strip(), (r.get("verdict") or "").strip(),
                       (r.get("place") or "").strip())
    out = []
    for r in read_csv(SHARED):
        va = num(r.get("table_va"))
        slots = num(r.get("table_slots"), 10)
        if va is None or slots is None:
            continue
        holder, verdict, place = holders.get(va, ("", "", ""))
        label = members.get(va, "")
        role = "member" if label else ("holder" if holder else "anonymous")
        out.append({"table_va": va, "slots": slots, "role": role,
                    "owner_label": label or holder, "holder_verdict": verdict, "place": place})
    out.sort(key=lambda r: r["table_va"])
    return out


def class_tables():
    """The 492 class tables, the population the vote must stay silent on."""
    names = {}
    for r in read_csv(CLASS_NAMES):
        va = num(r.get("table_va"))
        if va is not None and (r.get("class") or "").strip():
            names[va] = r["class"].strip()
    out = []
    for r in read_csv(CLASS_TABLES):
        va = num(r.get("table_va"))
        slots = num(r.get("slots"), 10)
        if va is None or slots is None:
            continue
        out.append({"table_va": va, "slots": slots, "role": "class",
                    "owner_label": names.get(va, ""), "holder_verdict": "", "place": ""})
    out.sort(key=lambda r: r["table_va"])
    return out


def library(path):
    """{body_va: (symbol, library)} from the supplied-library match."""
    out = {}
    for r in read_csv(path):
        va = num(r.get("address"))
        sym = (r.get("name") or "").strip()
        if va is None or not sym:
            continue
        out[va] = (sym, (r.get("lib") or "").strip())
    return out


# --------------------------------------------------------------------------- harvest
def harvest(image, lib):
    def u32(va):
        try:
            b = image.read_va(va, 4)
        except KeyError:
            return None
        return struct.unpack("<I", b)[0] if b and len(b) >= 4 else None

    rows = []
    for r in interface_tables() + class_tables():
        classes, hits, libs = [], 0, collections.Counter()
        for i in range(r["slots"]):
            body = u32(r["table_va"] + 4 * i)
            if body is None or body not in lib:
                continue
            hits += 1
            sym, name_of_lib = lib[body]
            libs[name_of_lib] += 1
            cls = symbol_class(sym)
            if cls:
                classes.append(cls)
        verdict, cls, votes, others = vote(classes, r["slots"])
        classed = len(classes)
        if verdict == "named":
            why = "the slot methods name %s on %d of %d named slot(s)" % (cls, votes[0][1], len(classes))
        elif verdict == "thin":
            why = "one named slot names %s, which is not a vote" % cls
        elif verdict == "split":
            why = "no class holds a majority: " + ", ".join("%s %d" % (k, v) for k, v in votes)
        else:
            why = "no slot body carries a supplied library symbol"
        rows.append({
            "table_va": "%#010x" % r["table_va"], "role": r["role"],
            "owner_label": r["owner_label"], "slots": str(r["slots"]), "named_slots": str(hits),
            "classed": str(classed),
            "class": cls if verdict in ("named", "thin") else "",
            "votes": " ".join("%s=%d" % (k, v) for k, v in votes),
            "others": " ".join("%s=%d" % (k, v) for k, v in others),
            "verdict": verdict,
            "evidence": "%s; %d of %d slot(s) in a library (%s), %d of them with a class in the symbol"
                        % (why, hits, r["slots"],
                           ", ".join("%s %d" % (k, v) for k, v in libs.most_common()) or "none",
                           classed),
        })
    rows.sort(key=lambda r: (r["role"] != "class", int(r["table_va"], 16)))
    counts = collections.Counter((r["role"] == "class", r["verdict"]) for r in rows)
    census = {k: "" for k in FIELDS}
    census.update({
        "table_va": "census", "role": "census",
        "slots": str(sum(int(r["slots"] or 0) for r in rows)),
        "named_slots": str(sum(int(r["named_slots"] or 0) for r in rows)),
        "classed": str(sum(int(r["classed"] or 0) for r in rows)),
        "class": str(sum(1 for r in rows if r["verdict"] == "named")),
        "verdict": "census",
        "evidence": "interface %d (%s); class %d (%s); %d named table(s); %s classed slot(s)"
                    % (sum(1 for r in rows if r["role"] != "class"),
                       " ".join("%s %d" % (v, counts[(False, v)]) for v in VERDICTS),
                       sum(1 for r in rows if r["role"] == "class"),
                       " ".join("%s %d" % (v, counts[(True, v)]) for v in VERDICTS),
                       sum(1 for r in rows if r["verdict"] == "named"),
                       census["classed"]),
    })
    rows.append(census)
    return rows


# --------------------------------------------------------------------------- render
def summary(rows):
    rows = [r for r in rows if r["verdict"] != "census"]
    iface = [r for r in rows if r["role"] != "class"]
    cls = [r for r in rows if r["role"] == "class"]
    named = [r for r in iface if r["verdict"] == "named"]
    near = [r for r in iface if r["verdict"] in ("named", "thin")]
    agree = [r for r in near if r["owner_label"]
             and r["class"].lower() == r["owner_label"].split("_member_")[0].lower()]
    return {
        "iface": iface, "class": cls, "named": named, "thin": [r for r in iface if r["verdict"] == "thin"],
        "split": [r for r in iface if r["verdict"] == "split"],
        "cats": collections.Counter(r["verdict"] for r in iface),
        "ccats": collections.Counter(r["verdict"] for r in cls),
        "agree": agree, "labelled": [r for r in near if r["owner_label"]],
        "disagree": [r for r in near if r["owner_label"]
                     and r["class"].lower() != r["owner_label"].split("_member_")[0].lower()],
        "class_votes": sum(1 for r in cls if r["verdict"] != "none"),
        "slots": sum(int(r["slots"]) for r in iface),
        "named_slots": sum(int(r["named_slots"]) for r in iface),
        "lib_slots": sum(int(r["named_slots"]) for r in iface if r["named_slots"] != "0"),
        "classed": sum(int(r["classed"]) for r in iface),
    }


def render(rows):
    s = summary(rows)
    c = s["cats"]
    lines = ["| population | tables | `named` | `thin` | `split` | `none` |",
             "|---|---:|---:|---:|---:|---:|"]
    lines.append("| interface | %d | %d | %d | %d | %d |"
                 % (len(s["iface"]), c["named"], c["thin"], c["split"], c["none"]))
    lines.append("| class (control) | %d | %d | %d | %d | %d |"
                 % (len(s["class"]), s["ccats"]["named"], s["ccats"]["thin"], s["ccats"]["split"],
                    s["ccats"]["none"]))
    lines.append("")
    lines.append("**%d of the %d interface tables carry a name the supplied libraries write, and %d of "
                 "the %d class tables vote at all.** A vote needs a strict majority of the named slots "
                 "over at least two of them, so a table split between two classes, or named by a single "
                 "library symbol, is reported rather than named. The %d named tables hold %d of the %d "
                 "interface slots and account for %d of the %d interface slots that point at a library "
                 "body; only %d of those %d symbols carry a class -- the rest are plain C names "
                 "(`XMOEncoderProcess`) or free functions at namespace scope, which vote nothing."
                 % (c["named"], len(s["iface"]), s["class_votes"], len(s["class"]),
                    c["named"], sum(int(r["slots"]) for r in s["named"]), s["slots"],
                    sum(int(r["named_slots"]) for r in s["named"]), s["named_slots"],
                    s["classed"], s["named_slots"]))
    lines.append("")
    lines.append("| the table | slots | named | the class the slots name | votes | the label another channel gave it |")
    lines.append("|---|---:|---:|---|---|---|")
    for r in s["named"]:
        lines.append("| `%s` | %s | %s | **`%s`** | %s | %s |"
                     % (r["table_va"], r["slots"], r["named_slots"], r["class"],
                        r["votes"].replace(" ", ", "), ("`%s`" % r["owner_label"]) if r["owner_label"] else "-"))
    lines.append("")
    labs = s["labelled"]
    lines.append("**Control: of the %d tables a constructor symbol had already labelled, the vote "
                 "reproduces that class on %d and disagrees on %d**; the label comes from a `??0`/`??1` "
                 "symbol on the store body, the vote from the slot bodies' own symbols, so the two never "
                 "read the same bytes. The %d `thin` rows (%s) each rest on one library symbol, and the "
                 "%d `split` row on none of theirs."
                 % (len(labs), len(s["agree"]), len(s["disagree"]),
                    len(s["thin"]), ", ".join("`%s` `%s`" % (r["table_va"], r["class"]) for r in s["thin"]),
                    len(s["split"])))
    return "\n".join(lines)


def write_artifact(rows, path):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


def splice_doc(path, block):
    text = open(path, encoding="utf-8").read()
    i, j = text.find(DOC_START), text.find(DOC_END)
    if i < 0 or j < 0:
        return None
    return text[:i] + DOC_START + "\n" + block + "\n" + text[j:]


# --------------------------------------------------------------------------- self-test
def self_check():
    bad = []

    if symbol_class("?CreateScreen@CScreen@UIXNamespace@@UAEJXZ") != "CScreen":
        bad.append("a method's class is its second token")
    if symbol_class("?SetInput@CLiveEngine@UIXNamespace@@QAGJKPBU_XINPUT_STATE@@@Z") != "CLiveEngine":
        bad.append("a static method's class is its second token")
    if symbol_class("?CreateScreen@UIXNamespace@@YAJXZ") != "":
        bad.append("a free function carries a namespace, not a class")
    if symbol_class("??0CEncoderXMO@@QAE@XZ") != "CEncoderXMO":
        bad.append("a constructor carries its class first")
    if symbol_class("??_GCEncoderXMO@@UAEPAXI@Z") != "CEncoderXMO":
        bad.append("a scalar-deleting destructor carries its class first")
    if symbol_class("??$copy@PAVCScreen@@@std@@YAXXZ") != "":
        bad.append("a template is refused")
    if symbol_class("__purecall") != "":
        bad.append("an undecorated symbol has no class")

    if vote([], 5) != ("none", "", [], []):
        bad.append("no named slot is no vote")
    if vote(["A", "A", "A", "B"], 5)[0] != "named":
        bad.append("a strict majority names")
    if vote(["A", "B"], 5)[0] != "split":
        bad.append("a tie is refused")
    if vote(["A"], 5)[0] != "thin":
        bad.append("a single named slot is thin, not a name")

    rows = []
    for va, role, verdict, cls, votes in (
            ("0x0053f9ec", "member", "named", "CUIPlugin", "CUIPlugin=7"),
            ("0x0053f990", "anonymous", "thin", "CNotificationTask", "CNotificationTask=1"),
            ("0x004c7800", "member", "none", "", ""),
            ("0x004c9348", "class", "none", "", "")):
        rows.append({"table_va": va, "role": role, "owner_label": "CUIPlugin_member_0x8" if cls == "CUIPlugin" else "",
                     "slots": "7", "named_slots": "7" if verdict == "named" else "1",
                     "classed": "7" if verdict == "named" else "1",
                     "class": cls, "votes": votes, "others": "", "verdict": verdict,
                     "evidence": "synthetic; 7 of 7 slot(s) in a library (uix 7)"})
    rows.append({k: "" for k in FIELDS})
    rows[-1].update({"table_va": "census", "role": "census", "verdict": "census", "slots": "28",
                     "named_slots": "15", "class": "1",
                     "evidence": "interface 3 (named 1 thin 1 none 1); class 1 (none 1); 1 named table(s)"})
    out = render(rows)
    for want in ("interface", "class (control)", "CUIPlugin", "Control:"):
        if want not in out:
            bad.append("render lacks %r" % (want,))
    s = summary(rows)
    if len(s["named"]) != 1 or len(s["agree"]) != 1:
        bad.append("the control population is not the named rows with a label")
    if s["class_votes"] != 0:
        bad.append("a class table that votes would violate the control")

    path = os.path.join(tempfile.mkdtemp(), "a.csv")
    write_artifact(rows, path)
    if read_csv(path) != rows:
        bad.append("artifact round trip differs")
    if bad:
        print("FAIL interface-lib-names: " + "; ".join(bad))
        return 1
    print("ok   interface-lib-names: the symbol reader, the four vote verdicts, the control population "
          "and the artifact round trip agree with the synthetic tables")
    return 0


# --------------------------------------------------------------------------- main
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
        rows = read_csv(OUT)
        if not rows:
            print("FAIL interface-lib-names: %s is missing" % OUT)
            return 1
        block = render(rows)
        text = open(args.check_doc, encoding="utf-8").read()
        i, j = text.find(DOC_START), text.find(DOC_END)
        if i < 0 or j < 0:
            print("FAIL interface-lib-names: %s has no generated block" % args.check_doc)
            return 1
        if text[i:j + len(DOC_END)] != DOC_START + "\n" + block + "\n" + DOC_END:
            print("FAIL interface-lib-names: %s's block does not reproduce from %s"
                  % (args.check_doc, OUT))
            return 1
        print("ok   interface-lib-names: %s's block reproduces from %s" % (args.check_doc, OUT))
        return 0
    if args.report or args.write_doc:
        rows = read_csv(OUT)
        if not rows:
            print("FAIL interface-lib-names: %s is missing" % OUT)
            return 1
        block = render(rows)
        if args.report:
            print(block)
            return 0
        text = splice_doc(args.write_doc, block)
        if text is None:
            print("FAIL interface-lib-names: %s has no generated block" % args.write_doc)
            return 1
        open(args.write_doc, "w", encoding="utf-8").write(text)
        print("wrote %s's block" % args.write_doc)
        return 0
    if not args.xbe:
        ap.error("--xbe is required (or use --report/--check/--write-doc)")
    import xbe as xbemod
    image = xbemod.Xbe(args.xbe)
    rows = harvest(image, library(XDK))
    if args.out:
        write_artifact(rows, os.path.join(args.out, os.path.basename(OUT)))
        print("wrote %s" % os.path.join(args.out, os.path.basename(OUT)))
    else:
        for r in rows:
            print("%s %-9s %-6s %-24s %s" % (r["table_va"], r["role"], r["verdict"], r["class"], r["evidence"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
