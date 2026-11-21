#!/usr/bin/env python3
"""Who a caller names: the class it descends from, one step at a time.

`inventory/class_table_super_names.csv` names a table when every class that calls its installer
agrees on its `Super`. Nine tables are *split* -- their callers disagree -- and the page left them
with the sentence "a table those classes share higher up". This tool reads that sentence as a
measurement, in two channels that do not share an input:

  * **the nearest common ancestor** of the callers, over the hierarchy the image writes itself
    (`inventory/class_super_edges.csv`, 492 classes), the parent the installer's base call gives
    (`inventory/class_table_parent_edges.csv`) and the script tree (`inventory/classes.csv`);
  * **the parent edge**, read from the children: a class whose own `parent_va` is this table calls
    its parent's installer, so the table is the class those children agree their `Super` is.

The first channel is a bound, and the control says so exactly: of the 120 callers-constrained
tables the class map already names, the nearest common ancestor *is* that class in 77, is one of
its descendants in 43 (all of them tables with a single named caller, where the ancestor of one
class is its parent), and is an unrelated class in **0**. A table's class is the NCA or an
ancestor of it, never below it -- so the NCA names a table only when the callers span enough
branches, and the two unnamed tables here are the case where they do (one) or do not (the other).

The second channel has no free parameter and agrees with every name already in the map where it
applies. It reads a parent edge as a base call only because the two disagreeing callers of
`0x004fc4f0` are `PendingLevel`'s subclasses, which cannot descend from `LevelBase`: their stores
are a *member* construction, not a vtable install. That is the note the page carried as
`parent-unnamed` on `0x004fc588`.

    tools/class-table-lca.py --report
    tools/class-table-lca.py -o inventory
    tools/class-table-lca.py --write-doc docs/systems/CLASS_REGISTRATION.md
    tools/class-table-lca.py --check-doc docs/systems/CLASS_REGISTRATION.md
    tools/class-table-lca.py --check          # synthetic tables, no dump, writes nothing

The inputs and the artifact are committed CSVs: no dump and no image read. Class names,
addresses and counts only.
"""
import argparse
import collections
import csv
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

OUT = os.path.join("inventory", "class_table_lca.csv")
SUPER_NAMES = os.path.join(ROOT, "inventory", "class_table_super_names.csv")
PARENT_EDGES = os.path.join(ROOT, "inventory", "class_table_parent_edges.csv")
IMAGE_EDGES = os.path.join(ROOT, "inventory", "class_super_edges.csv")
CLASSES = os.path.join(ROOT, "inventory", "classes.csv")
TABLE_NAMES = os.path.join(ROOT, "inventory", "xbe_class_table_names.csv")
TABLE_SLOTS = os.path.join(ROOT, "inventory", "xbe_class_tables.csv")
PAGE = os.path.join("docs", "systems", "CLASS_REGISTRATION.md")
DOC_START = "<!-- generated:class-table-lca -->"
DOC_END = "<!-- /generated:class-table-lca -->"
FIELDS = ("table_va", "slots", "callers", "distinct_supers", "nearest_common_ancestor", "relation",
          "committed", "name_rule", "parent_edge_children", "parent_edge_class", "callers_not_below",
          "proposed_class", "verdict", "evidence")
ROOT_CLASS = "Object"


def read_csv(path):
    if not os.path.isfile(path):
        return []
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


# --------------------------------------------------------------------------- hierarchy
def hierarchy(image_edges, parent_edges, classes):
    """{class: super} with the image's own edge first: it is the only source that covers a class
    no package declares (`NetConnection`, `LevelBase`), and the script tree is the only one that
    covers a class the image registers with the shared default thunk."""
    sup = {}
    for r in image_edges:
        if r.get("verdict") == "named" and r.get("target_class") and r.get("parent_class"):
            sup.setdefault(r["target_class"], r["parent_class"])
    for r in parent_edges:
        cls, par = (r.get("class") or "").strip(), (r.get("parent_class") or "").strip()
        if cls and par:
            sup.setdefault(cls, par)
    for r in classes:
        cls, par = (r.get("class") or "").strip(), (r.get("super") or "").strip()
        if cls and par:
            sup.setdefault(cls, par)
    return sup


def chain(sup, cls):
    """The ancestors of `cls`, nearest first. A cycle stops the walk rather than looping."""
    out, seen, cur = [], set(), cls
    while cur and cur not in seen:
        seen.add(cur)
        par = sup.get(cur, "")
        if not par:
            break
        out.append(par)
        cur = par
    return out


def nearest_common_ancestor(sup, names):
    """The deepest class every name descends from, or '' when they share no ancestor.

    A single caller answers itself: its own parent is not a lower bound this measurement can
    establish, and returning the parent would silently move every one-caller row one level up.
    """
    names = [n for n in names if n]
    if not names:
        return ""
    if len(names) == 1:
        return names[0]
    chains = [chain(sup, n) for n in names]
    common = set(chains[0])
    for ch in chains[1:]:
        common &= set(ch)
    for anc in chains[0]:
        if anc in common:
            return anc
    return ""


def relation(nca, committed, sup):
    """How the NCA stands to the name the map already holds."""
    if not nca:
        return "no-nca"
    if not committed:
        return "unnamed"
    if nca == committed:
        return "equal"
    if committed in chain(sup, nca):
        return "descendant"
    return "unrelated"


# --------------------------------------------------------------------------- rows
def harvest(sup, super_names, parent_edges, names, slots):
    """One row per table a caller edge constrains, with both channels and their relation."""
    by_parent = collections.defaultdict(list)
    for r in parent_edges:
        pv = r.get("parent_va") or ""
        if pv.startswith("0x") and r.get("class"):
            by_parent[int(pv, 16)].append(r["class"])
    rows = []
    for tva in sorted(set(super_names) | set(by_parent)):
        sn = super_names.get(tva, {})
        callers = [c for c in (sn.get("children") or "").split("|") if c]
        supers = sorted({s for s in (sn.get("child_supers") or "").split("|") if s})
        nca = nearest_common_ancestor(sup, callers)
        committed, rule = names.get(tva, ("", ""))
        rel = relation(nca, committed, sup)
        edge_children = sorted(set(by_parent.get(tva, [])))
        edge_supers = sorted({sup.get(c, "") for c in edge_children} - {""})
        edge_class = edge_supers[0] if len(edge_supers) == 1 else ""
        if edge_children and not edge_class:
            edge_class = ""
        if committed:
            proposed = committed
            verdict = rel
        elif edge_class:
            proposed = edge_class
            verdict = "named-by-parent-edge"
        elif nca and nca != ROOT_CLASS:
            proposed = nca
            verdict = "named-by-nca"
        else:
            proposed = ""
            verdict = "nca-at-root"
        if committed and edge_class and edge_class != committed:
            verdict = "parent-edge-disagrees"
        owner = committed or edge_class
        not_below = [c for c in callers if owner and c != owner and owner not in chain(sup, c)]
        evidence = []
        if callers:
            evidence.append("%d caller(s), %d distinct Super" % (len(callers), len(supers)))
        if nca:
            evidence.append("nca %s (%d ancestor(s))" % (nca, len(chain(sup, nca))))
        if edge_children:
            evidence.append("parent edge from %s" % ", ".join(edge_children))
        if not_below:
            evidence.append("%d caller(s) not below %s: %s" % (len(not_below), owner, ", ".join(not_below)))
        rows.append({
            "table_va": "%#010x" % tva,
            "slots": str(slots.get(tva, "")),
            "callers": "|".join(callers),
            "distinct_supers": str(len(supers)),
            "nearest_common_ancestor": nca,
            "relation": rel,
            "committed": committed,
            "name_rule": rule,
            "parent_edge_children": "|".join(edge_children),
            "parent_edge_class": edge_class,
            "callers_not_below": "|".join(not_below),
            "proposed_class": proposed,
            "verdict": verdict,
            "evidence": "; ".join(evidence),
        })
    return rows


# --------------------------------------------------------------------------- rendering
def render(rows):
    named = [r for r in rows if r["committed"]]
    unnamed = [r for r in rows if not r["committed"]]
    by_rel = collections.Counter(r["relation"] for r in named)
    split = [r for r in rows if int(r["distinct_supers"] or 0) > 1]
    split_named = [r for r in split if r["committed"] and r["relation"] == "equal"]
    split_unnamed = [r for r in split if not r["committed"]]
    edge = [r for r in rows if r["parent_edge_children"]]
    edge_named = [r for r in edge if r["committed"]]
    edge_bad = [r for r in edge if r["parent_edge_class"] and r["committed"]
                and r["parent_edge_class"] != r["committed"]]
    lines = []
    desc = [r for r in named if r["relation"] == "descendant"]
    desc_single = [r for r in desc if len([c for c in r["callers"].split("|") if c]) == 1]
    lines.append("**%d tables carry a caller edge, and the class map already names %d of them. The callers' "
                 "nearest common ancestor IS that class in %d, is one of its descendants in %d, and is an "
                 "unrelated class in %d.** %d of the %d descendant rows are a table with a single named "
                 "caller, where the ancestor of one class is its parent -- so the reading is a *lower bound*: "
                 "a table's class is its callers' nearest common ancestor or an ancestor of it, never below "
                 "it. That is why the rule can name a table only where the callers span branches, and why "
                 "the unnamed tables below split into one it names twice and one it cannot."
                 % (len(rows), len(named), by_rel["equal"], by_rel["descendant"], by_rel["unrelated"],
                    len(desc_single), len(desc)))
    lines.append("")
    lines.append("| table | slots | callers | distinct `Super` | nearest common ancestor | committed | "
                 "parent edge names | verdict |")
    lines.append("|---|---:|---:|---:|---|---|---|---|")
    for r in sorted(rows, key=lambda x: (x["committed"] != "", x["table_va"])):
        if int(r["distinct_supers"] or 0) < 2 and r["committed"]:
            continue
        lines.append("| `%s` | %s | %d | %d | %s | %s | %s | %s |" % (
            r["table_va"], r["slots"] or "-", len([c for c in r["callers"].split("|") if c]),
            int(r["distinct_supers"] or 0), r["nearest_common_ancestor"] or "--",
            ("`%s`" % r["committed"]) if r["committed"] else "--",
            ("`%s`" % r["parent_edge_class"]) if r["parent_edge_class"] else "--",
            r["verdict"]))
    lines.append("")
    lines.append("**%d table(s) split** (their callers disagree on `Super`), and the two channels settle "
                 "them: **%d of the %d named ones have the committed name as their nearest common "
                 "ancestor**%s. %s"
                 % (len(split), len(split_named), len(split) - len(split_unnamed),
                    "".join("; `%s` %s" % (r["table_va"], r["relation"])
                            for r in split if r["committed"] and r["relation"] != "equal"),
                    ("The %d unnamed one%s: %s." % (
                        len(split_unnamed), "" if len(split_unnamed) == 1 else "s",
                        "; ".join("`%s` has %d caller%s, a nearest common ancestor of `%s` and a parent edge that names `%s`"
                                  % (r["table_va"], len([c for c in r["callers"].split("|") if c]),
                                     "" if len([c for c in r["callers"].split("|") if c]) == 1 else "s",
                                     r["nearest_common_ancestor"] or "--",
                                     r["parent_edge_class"])
                                  if r["nearest_common_ancestor"] not in ("", ROOT_CLASS) else
                                  "`%s` has a root nearest common ancestor while the parent edge names `%s` from its child `%s`"
                                  % (r["table_va"], r["parent_edge_class"],
                                     ", ".join(r["parent_edge_children"].split("|")))
                                  for r in split_unnamed))
                     if split_unnamed else "")))
    lines.append("")
    strays = [r for r in rows if r["callers_not_below"]]
    lines.append("**The parent-edge join covers %d tables and agrees with %d of %d names, 0 disagreeing.** "
                 "It reads a class's own installer call as its parent, so a row whose `parent_va` is a table "
                 "is that table's child; where every child agrees on its `Super`, the table is that class. "
                 "Its one free reading is that the call is a *base* call, and %d row(s) separate it from the "
                 "NCA: %s. A caller there is not a descendant of the class its table names, so its store "
                 "installs the table in a *member* rather than in the object's own slot -- which is what "
                 "the `parent-unnamed` row this page carried on `0x004fc588` reads as: `0x004fc4f0` is "
                 "`LevelBase`, `PendingLevel`'s installer constructs a member of that class, and "
                 "`PendingLevel`'s `Super` stays `Object` as its class object says."
                 % (len(edge), len(edge_named) - len(edge_bad), len(edge_named), len(strays),
                    "; ".join("`%s` names `%s` and %d of its %d caller(s) are not below it (%s)"
                              % (r["table_va"], r["parent_edge_class"] or r["committed"],
                                 len([c for c in r["callers_not_below"].split("|") if c]),
                                 len([c for c in r["callers"].split("|") if c]),
                                 ", ".join(r["callers_not_below"].split("|")))
                              for r in strays)))
    lines.append("")
    if unnamed:
        lines.append("**What the two unnamed tables are:** %s. Both are named by a row this tool does not "
                     "write: adding them to `inventory/xbe_class_table_names.csv` is the class-map cascade "
                     "the declarer walk then follows, and this bundle publishes the measurement, not the "
                     "rename."
                     % ", ".join("`%s` = %s (`%s`)" % (r["table_va"], r["proposed_class"], r["verdict"])
                                 for r in unnamed))
    return "\n".join(lines)


def write_artifact(rows, path):
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
    sup = {"Child": "Parent", "Parent": "Object", "Sibling": "Parent", "Grand": "Child",
           "Other": "Object"}
    if chain(sup, "Grand") != ["Child", "Parent", "Object"]:
        bad.append("the ancestor chain is nearest first: %r" % (chain(sup, "Grand"),))
    if nearest_common_ancestor(sup, ["Child", "Sibling"]) != "Parent":
        bad.append("two branches meet at their parent")
    if nearest_common_ancestor(sup, ["Grand", "Sibling"]) != "Parent":
        bad.append("a grandchild and a sibling meet at the parent")
    if nearest_common_ancestor(sup, ["Grand"]) != "Grand":
        bad.append("a single caller answers itself, not its parent")
    if nearest_common_ancestor(sup, ["Child", "Other"]) != "Object":
        bad.append("the root is a common ancestor")
    if nearest_common_ancestor(sup, ["Child", "Nowhere"]) != "":
        bad.append("a class outside the hierarchy leaves no ancestor")
    if relation("Child", "Parent", sup) != "descendant":
        bad.append("a descendant NCA is reported as descendant")
    if relation("Parent", "Parent", sup) != "equal" or relation("Other", "Parent", sup) != "unrelated":
        bad.append("equal and unrelated are told apart")

    super_names = {0x1000: {"children": "Child|Sibling", "child_supers": "Parent|Parent"},
                   0x2000: {"children": "Grand", "child_supers": "Child"},
                   0x4000: {"children": "Child|Other", "child_supers": "Parent|Object"}}
    parent_edges = [{"parent_va": "0x1000", "class": "Child"},
                    {"parent_va": "0x1000", "class": "Sibling"},
                    {"parent_va": "0x4000", "class": "Child"}]
    names = {0x1000: ("Parent", "child-super"), 0x2000: ("Child", "child-super")}
    slots = {0x1000: 7, 0x2000: 4, 0x4000: 9}
    rows = harvest(sup, super_names, parent_edges, names, slots)
    by = {int(r["table_va"], 16): r for r in rows}
    if by[0x1000]["relation"] != "equal" or by[0x1000]["verdict"] != "equal":
        bad.append("the two-branch table agrees with the map: %r" % (by[0x1000],))
    if by[0x2000]["relation"] != "descendant":
        bad.append("the one-caller table is a descendant bound: %r" % (by[0x2000],))
    if by[0x4000]["verdict"] != "named-by-parent-edge" or by[0x4000]["proposed_class"] != "Parent":
        bad.append("the parent edge names the unnamed table: %r" % (by[0x4000],))
    if by[0x1000]["parent_edge_class"] != "Parent":
        bad.append("the parent-edge join reads the children")
    out = render(rows)
    for want in ("nearest common ancestor", "split", "parent-edge"):
        if want not in out:
            bad.append("render lacks %r" % (want,))

    path = os.path.join(tempfile.mkdtemp(), "a.csv")
    write_artifact(rows, path)
    if read_csv(path) != rows:
        bad.append("artifact round trip differs")
    if bad:
        print("FAIL class-table-lca: " + "; ".join(bad))
        return 1
    print("ok   class-table-lca: the chain, the nearest common ancestor, the relation and both naming "
          "channels agree with the synthetic tables")
    return 0


# --------------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("-o", "--out", help="write the artifact under this directory")
    ap.add_argument("--report", action="store_true", help="render the page block to stdout")
    ap.add_argument("--write-doc")
    ap.add_argument("--check-doc")
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    if args.check:
        return self_check()

    if args.check_doc:
        rows = read_csv(OUT)
        if not rows:
            print("FAIL class-table-lca: %s is missing" % OUT)
            return 1
        text = open(args.check_doc, encoding="utf-8").read()
        i, j = text.find(DOC_START), text.find(DOC_END)
        if i < 0 or j < 0:
            print("FAIL class-table-lca: %s has no generated block" % args.check_doc)
            return 1
        if text[i + len(DOC_START):j].strip() != render(rows).strip():
            print("FAIL class-table-lca: %s is stale; re-run --write-doc" % args.check_doc)
            return 1
        print("ok   class-table-lca: the page block matches the committed artifact")
        return 0

    if args.report and not args.out and not args.write_doc:
        print(render(read_csv(OUT)))
        return 0

    sup = hierarchy(read_csv(IMAGE_EDGES), read_csv(PARENT_EDGES), read_csv(CLASSES))
    super_names = {int(r["table_va"], 16): r for r in read_csv(SUPER_NAMES) if r.get("table_va")}
    names = {int(r["table_va"], 16): ((r.get("class") or "").strip(), (r.get("name_rule") or "").strip())
             for r in read_csv(TABLE_NAMES) if r.get("table_va")}
    slots = {int(r["table_va"], 16): r.get("slots", "") for r in read_csv(TABLE_SLOTS) if r.get("table_va")}
    rows = harvest(sup, super_names, read_csv(PARENT_EDGES), names, slots)
    if args.out:
        write_artifact(rows, os.path.join(args.out, os.path.basename(OUT)))
    if args.write_doc:
        spliced = splice_doc(args.write_doc, render(rows))
        if spliced is None:
            print("FAIL class-table-lca: %s has no generated block" % args.write_doc)
            return 1
        open(args.write_doc, "w", encoding="utf-8").write(spliced)
        print("ok   class-table-lca: wrote %s" % args.write_doc)
    if args.report or not (args.out or args.write_doc):
        print(render(rows))
    counts = collections.Counter(r["verdict"] for r in rows)
    print("tables=%d %s" % (len(rows), " ".join("%s=%d" % (k, counts[k]) for k in sorted(counts))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
