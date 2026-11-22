#!/usr/bin/env python3
"""The two parents of a class: the `Super` its class object stores and the base its installer calls.

Two censuses already read a class's parent, from two different places, and `docs/systems/CLASS_REGISTRATION.md`
called the one class they disagree on a question rather than an answer. The page's *The parent, from the image*
section reads the `+0x28` store every class's setup body makes on its class object
(`inventory/class_super_edges.csv`, 493 classes, keyed to the class literals two `.data` cells carry).
`tools/class-table-parent-edge.py` reads the same relation from the object side: a table's installer calls its
base's installer before it stores its own table at `[this]` (`inventory/class_table_parent_edges.csv`, 492
tables). The page's *PendingLevel* paragraph said "either `PendingLevel` embeds a `LevelBase`-like object
there, or one of the two reads is wrong".

Neither is wrong, and this join is the census of that: the class object's `+0x28` is the **script** parent,
and the installer's call is the **native** base, and they differ for exactly one of the image's 492 class
tables. A join is the whole measurement -- no dump, no disassembly -- because both reads are committed
artifacts:

    python3 tools/class-parent-relations.py -o inventory
    python3 tools/class-parent-relations.py --report
    python3 tools/class-parent-relations.py --write-doc docs/systems/CLASS_REGISTRATION.md
    python3 tools/class-parent-relations.py --check-doc docs/systems/CLASS_REGISTRATION.md
    python3 tools/class-parent-relations.py --check        # synthetic, no dump, writes nothing

Addresses, class names and counts only; no image or package content is written.
"""
import argparse
import collections
import csv
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join("inventory", "class_parent_relations.csv")
EDGES = os.path.join(ROOT, "inventory", "class_table_parent_edges.csv")
SUPERS = os.path.join(ROOT, "inventory", "class_super_edges.csv")
DOC_A = ("<!-- generated:class-parent-relations -->", "<!-- /generated:class-parent-relations -->")
FIELDS = ("table_va", "class", "script_super", "native_base", "installer", "call_site", "super_body",
          "store_site", "verdict", "evidence")
CENSUS = ("tables", "named", "joined", "agree", "differ", "no_native_edge", "no_super_store",
          "unnamed_table")
VERDICTS = ("agree", "differ", "no-native-edge", "no-super-store")


def read_csv(path):
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def load(root):
    """The two committed reads: the native base per table, and the script parent per class."""
    native = {}
    for r in read_csv(os.path.join(root, "inventory", "class_table_parent_edges.csv")):
        if (r.get("table_va") or "") == "census" or not r.get("table_va"):
            continue
        try:
            native[int(r["table_va"], 16)] = r
        except ValueError:
            continue
    supers = {}
    for r in read_csv(os.path.join(root, "inventory", "class_super_edges.csv")):
        # the same class appears once per store site (the store and the null fallback); the parent is one
        supers.setdefault((r.get("target_class") or "").strip(),
                          ((r.get("parent_class") or "").strip(), r.get("body_va") or "",
                           r.get("store_va") or ""))
    return native, supers


def verdict_for(cls, parent, super_row):
    """One of four: the two reads agree, differ, or only one of them exists."""
    if not cls:
        return "unnamed-table"
    if not parent:
        return "no-native-edge"
    if super_row is None:
        return "no-super-store"
    return "agree" if parent == super_row[0] else "differ"


def join(native, supers):
    rows, counts = [], collections.Counter()
    for table in sorted(native):
        r = native[table]
        cls = (r.get("class") or "").strip()
        parent = (r.get("parent_class") or "").strip()
        sup = supers.get(cls) if cls else None
        verdict = verdict_for(cls, parent, sup)
        counts["tables"] += 1
        counts[verdict.replace("-", "_")] += 1
        if cls and parent and sup is not None:
            counts["joined"] += 1
        if cls:
            counts["named"] += 1
        if verdict == "differ":
            evidence = ("the class object stores `%s` as Super (`%s`, store `%s`), and `%s`'s installer "
                        "`%s` calls `%s` before its own store" % (
                            sup[0], sup[1], sup[2], cls, r.get("installer_va") or "-",
                            r.get("thunk_va") or "-"))
        elif verdict == "agree":
            evidence = ("the class object stores `%s` as Super and the installer `%s` calls the body that "
                        "installs its table" % (sup[0], r.get("installer_va") or "-"))
        elif verdict == "no-native-edge":
            evidence = ("the class object stores `%s` as Super; its installer makes no call that stores a "
                        "table" % (sup[0] if sup else "-"))
        elif verdict == "no-super-store":
            evidence = ("the installer `%s` calls the body that installs `%s`; no `+0x28` store names this "
                        "class" % (r.get("installer_va") or "-", parent))
        else:
            evidence = "the table has no class name, so there is no Super to join"
        rows.append({
            "table_va": r["table_va"], "class": cls,
            "script_super": sup[0] if sup else "", "native_base": parent,
            "installer": r.get("installer_va") or "", "call_site": r.get("call_site") or "",
            "super_body": sup[1] if sup else "", "store_site": sup[2] if sup else "",
            "verdict": verdict, "evidence": evidence,
        })
    census = dict((k, counts.get(k, 0)) for k in CENSUS)
    return rows, census


def render(rows, census):
    def cell(v):
        return "-" if not v else str(v)

    lines = []
    lines.append("**%d class tables, %d joins: %d agree, %d differ, %d with no native edge and %d with no "
                 "`Super` store; %d table(s) have no class name.**" % (
                     census["tables"], census["joined"], census["agree"], census["differ"],
                     census["no_native_edge"], census["no_super_store"], census["unnamed_table"]))
    lines.append("")
    lines.append("The join is `inventory/class_super_edges.csv` (the `+0x28` store on the class object) against "
                 "`inventory/class_table_parent_edges.csv` (the installer's call to its base), by class name.")
    lines.append("")
    lines.append("| what the two reads say | classes | |")
    lines.append("|---|---:|---|")
    lines.append("| the same parent | %d | the script parent is the native base |" % census["agree"])
    lines.append("| a different parent | %d | the script parent is not the native base |" % census["differ"])
    lines.append("| no native edge | %d | the installer calls nothing that stores a table |"
                 % census["no_native_edge"])
    lines.append("| no `Super` store | %d | no class object is written a parent |" % census["no_super_store"])
    lines.append("| no class name | %d | an unnamed table, so nothing to join |" % census["unnamed_table"])
    diff = [r for r in rows if r["verdict"] == "differ"]
    if diff:
        lines.append("")
        lines.append("| the class that differs | script `Super` | native base | its `Super` store | its installer |")
        lines.append("|---|---|---|---|---|")
        for r in diff:
            lines.append("| `%s` (`%s`) | `%s` | `%s` | `%s` | `%s` |" % (
                cell(r["class"]), r["table_va"], cell(r["script_super"]), cell(r["native_base"]),
                "%s at %s" % (cell(r["super_body"]), cell(r["store_site"])),
                "%s at %s" % (cell(r["installer"]), cell(r["call_site"]))))
    return "\n".join(lines)


def splice_doc(path, block, marks):
    text = open(path, encoding="utf-8").read()
    i, j = text.find(marks[0]), text.find(marks[1])
    if i < 0 or j < 0:
        return None
    return text[:i] + marks[0] + "\n" + block + "\n" + text[j:]


def read_artifact(path):
    rows, census = [], None
    for r in read_csv(path):
        if (r.get("table_va") or "") == "census":
            census = {}
            for k in CENSUS:
                try:
                    census[k] = int(r.get(k) or 0)
                except ValueError:
                    census[k] = 0
        else:
            rows.append(r)
    return rows, census


def write_artifact(rows, census, path):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=list(FIELDS) + list(CENSUS), lineterminator="\n")
        w.writeheader()
        for r in rows:
            w.writerow(dict((k, r.get(k, "")) for k in FIELDS))
        w.writerow(dict([("table_va", "census")] + [(k, census.get(k, 0)) for k in CENSUS]))


def self_check():
    """Synthetic joins only: the four verdicts, the agreement rule, the render and both round trips."""
    bad = []
    native = {
        0x1000: {"table_va": "0x00001000", "class": "A", "parent_class": "Object", "installer_va": "0x100",
                 "call_site": "0x180", "thunk_va": "0x200"},
        0x2000: {"table_va": "0x00002000", "class": "B", "parent_class": "A", "installer_va": "0x300",
                 "call_site": "0x380", "thunk_va": "0x400"},
        0x3000: {"table_va": "0x00003000", "class": "C", "parent_class": "", "installer_va": "0x500",
                 "call_site": "0x580", "thunk_va": ""},
        0x4000: {"table_va": "0x00004000", "class": "", "parent_class": "A", "installer_va": "0x600",
                 "call_site": "0x680", "thunk_va": "0x700"},
    }
    supers = {"A": ("Object", "0x900", "0x950"), "B": ("Object", "0xa00", "0xa50"),
              "C": ("Object", "0xb00", "0xb50")}
    rows, census = join(native, supers)
    got = dict((r["class"], r["verdict"]) for r in rows if r["class"])
    if got.get("A") != "agree":
        bad.append("A should agree, got %r" % (got.get("A"),))
    if got.get("B") != "differ":
        bad.append("B should differ, got %r" % (got.get("B"),))
    if got.get("C") != "no-native-edge":
        bad.append("C should have no native edge, got %r" % (got.get("C"),))
    if census["unnamed_table"] != 1 or census["agree"] != 1 or census["differ"] != 1:
        bad.append("the split should be 1 agree / 1 differ / 1 unnamed, got %r" % (census,))
    if verdict_for("D", "A", None) != "no-super-store":
        bad.append("a class with no Super store should be no-super-store")
    block = render(rows, census)
    if not block.startswith("**4 class tables") or block.endswith("\n"):
        bad.append("the render should open with the table count and end without a newline")
    import tempfile
    tmp = tempfile.mkdtemp()
    path = os.path.join(tmp, "rel.csv")
    write_artifact(rows, census, path)
    rows2, census2 = read_artifact(path)
    if len(rows2) != len(rows) or census2 != census:
        bad.append("the artifact round trip lost rows or the census")
    doc = os.path.join(tmp, "doc.md")
    open(doc, "w", encoding="utf-8").write("# x\n\n%s\n%s\n\ntail\n" % DOC_A)
    new = splice_doc(doc, block, DOC_A)
    if new is None or DOC_A[0] + "\n" + block + "\n" + DOC_A[1] not in new:
        bad.append("the doc splice did not place the block between its markers")
    for b in bad:
        print("FAIL class-parent-relations: %s" % b)
    print("%s class-parent-relations: synthetic joins, the four verdicts, the render, both round trips"
          % ("FAIL" if bad else "ok  "))
    return 1 if bad else 0


def check_artifact():
    """Recompute the join from the two committed reads and compare it with the committed artifact."""
    rows, census = read_artifact(OUT)
    if not rows or not census:
        print("FAIL class-parent-relations: %s is missing or has no census row" % OUT)
        return 1
    fresh, fresh_census = join(*load(ROOT))
    if fresh_census != census or len(fresh) != len(rows):
        print("FAIL class-parent-relations: the committed artifact is stale (%d rows / %r against %d / %r)"
              % (len(rows), census, len(fresh), fresh_census))
        return 1
    stale = [f["table_va"] for f, c in zip(fresh, rows)
             if f["verdict"] != c["verdict"] or f["script_super"] != c["script_super"]
             or f["native_base"] != c["native_base"]]
    if stale:
        print("FAIL class-parent-relations: %d row(s) disagree with the reads: %s"
              % (len(stale), ", ".join(stale[:5])))
        return 1
    print("ok   class-parent-relations: %d rows reproduce from the two committed reads" % len(rows))
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("-o", "--out")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--write-doc")
    ap.add_argument("--check-doc")
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    if args.check:
        rc = self_check()
        return rc or check_artifact()
    if args.check_doc:
        rows, census = read_artifact(OUT)
        if not rows or not census:
            print("FAIL class-parent-relations: %s is missing or has no census row" % OUT)
            return 1
        block = render(rows, census)
        text = open(args.check_doc, encoding="utf-8").read()
        i, j = text.find(DOC_A[0]), text.find(DOC_A[1])
        if i < 0 or j < 0:
            print("FAIL class-parent-relations: %s has no %s block" % (args.check_doc, DOC_A[0]))
            return 1
        if text[i:j + len(DOC_A[1])] != DOC_A[0] + "\n" + block + "\n" + DOC_A[1]:
            print("FAIL class-parent-relations: %s's block does not reproduce from the committed CSV"
                  % args.check_doc)
            return 1
        print("ok   class-parent-relations: %s reproduces from the committed CSV" % args.check_doc)
        return 0
    if args.report or args.write_doc:
        rows, census = read_artifact(OUT)
        if not rows or not census:
            print("FAIL class-parent-relations: %s is missing or has no census row" % OUT)
            return 1
        block = render(rows, census)
        if args.report:
            print(block)
            return 0
        new = splice_doc(args.write_doc, block, DOC_A)
        if new is None:
            print("FAIL class-parent-relations: %s has no %s markers" % (args.write_doc, DOC_A[0]))
            return 1
        if new != open(args.write_doc, encoding="utf-8").read():
            open(args.write_doc, "w", encoding="utf-8").write(new)
            print("wrote %s (%s)" % (args.write_doc, DOC_A[0]))
        return 0
    rows, census = join(*load(ROOT))
    out = os.path.join(args.out, os.path.basename(OUT)) if args.out else OUT
    os.makedirs(os.path.dirname(out) or ".", exist_ok=True)
    write_artifact(rows, census, out)
    print("wrote %s (%d rows: %d agree, %d differ, %d no-native-edge, %d no-super-store)"
          % (out, len(rows), census["agree"], census["differ"], census["no_native_edge"],
             census["no_super_store"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
