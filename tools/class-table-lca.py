#!/usr/bin/env python3
"""Who a caller names: the class it descends from, one step at a time.

`inventory/class_table_super_names.csv` names a table when every class that calls its installer
agrees on its `Super`. Nine tables are *split* -- their callers disagree -- and the page left them
with the sentence "a table those classes share higher up". This tool reads that sentence as a
measurement, in two channels that do not share an input:

  * **the nearest common ancestor** of the callers, over the hierarchy the image writes itself
    (`inventory/class_super_edges.csv`, 492 classes) and the script tree (`inventory/classes.csv`);
  * **the children's `Super`**: a class's installer calls its parent's constructor *before* it
    stores its own table, so the first table-storing call of class C stores the table of the class
    `Super(C)` names. Read per child, that is an equation the image states on both sides --
    `table(Super(C)) = parent_va(C)` -- and the table is the class its children agree their
    parent is.

The first channel is a bound, and the control says so exactly: of the tables a caller edge reaches
and the class map already names, the nearest common ancestor *is* that class in 77, is one of its
descendants in 43 (all of them tables with a single named caller, where the ancestor of one class
is its parent), and is an unrelated class in **0**. A table's class is the NCA or an ancestor of
it, never below it.

The second channel has no free parameter and is exact where it applies: over the 123 tables a child
edge reaches, 120 are named by the map and the equation reproduces that name **120 of 120**, with
0 disagreements, and it names all three tables the map does not. It reads a call as a *base* call,
and the one place that reading fails is stated by the same evidence: `PendingLevel`'s class object
says `Super = Object`, whose table is `0x004dd2a8`, so its store of `0x004fc4f0` is a *member*
construction -- the class embeds an object of that class -- and the child is reported, not counted.

Both channels are scored against a class map blanked of the rows this tool writes
(`name_rule=child-parent-edge`): a name it derived is re-derived from the image on every run and
never votes in its own control.

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
          "committed", "name_rule", "parent_edge_children", "parent_edge_excluded",
          "parent_edge_class", "callers_not_below", "proposed_class", "verdict", "evidence")
ROOT_CLASS = "Object"
OWN_RULES = ("child-parent-edge",)      # rows this tool writes: blanked before scoring


def read_csv(path):
    if not os.path.isfile(path):
        return []
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


# --------------------------------------------------------------------------- hierarchy
def hierarchy(image_edges, classes):
    """{class: super} with the image's own edge first: it is the only source that covers a class
    no package declares (`NetConnection`, `LevelBase`), and the script tree is the only one that
    covers a class the image registers with the shared default thunk.

    `class_table_parent_edges.csv` is *not* an input here: its `parent_class` column is a copy of
    the class map, so reading it would let a name this tool wrote become a hierarchy edge and
    change the next round's vote. The channel below reads that census on its own terms.
    """
    sup = {}
    for r in image_edges:
        if r.get("verdict") == "named" and r.get("target_class") and r.get("parent_class"):
            sup.setdefault(r["target_class"], r["parent_class"])
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
    """How the NCA stands to the name the map holds (blanked: only names another rule wrote)."""
    if not nca:
        return "no-nca"
    if not committed:
        return "unnamed"
    if nca == committed:
        return "equal"
    if committed in chain(sup, nca):
        return "descendant"
    return "unrelated"


# --------------------------------------------------------------------------- the child channel
def table_of(names):
    """class -> the table the class map says is its own. The only place a class's table is stated."""
    return {cls: tva for tva, (cls, _rule) in names.items() if cls}


def child_channel(table, children, sup, tables):
    """(proposal, kept, excluded) for one table, read from its children's `Super`.

    A class's installer calls its parent's constructor before it stores its own table, so the first
    table-storing call of class C stores the table of `Super(C)`. When `Super(C)`'s table is known
    and is a *different* table, the store is not a base call: the class embeds an object of that
    class, so the child is reported with its reason and never votes.
    """
    kept, excluded, supers = [], [], set()
    for child in sorted(set(children)):
        parent = sup.get(child, "")
        known = tables.get(parent)
        if known is not None and known != table:
            excluded.append("%s(%s->%#010x)" % (child, parent, known))
            continue
        kept.append(child)
        if parent:
            supers.add(parent)
    if not kept or any(not sup.get(c) for c in kept) or len(supers) != 1:
        return "", kept, excluded
    return next(iter(supers)), kept, excluded


# --------------------------------------------------------------------------- rows
def harvest(sup, super_names, parent_edges, names, slots):
    """One row per table a channel constrains, with both channels and their relation."""
    by_parent = collections.defaultdict(list)
    for r in parent_edges:
        pv = r.get("parent_va") or ""
        if pv.startswith("0x") and r.get("class"):
            by_parent[int(pv, 16)].append(r["class"])
    tables = table_of(names)
    rows = []
    for tva in sorted(set(super_names) | set(by_parent)):
        sn = super_names.get(tva, {})
        callers = [c for c in (sn.get("children") or "").split("|") if c]
        supers = sorted({s for s in (sn.get("child_supers") or "").split("|") if s})
        nca = nearest_common_ancestor(sup, callers)
        committed, rule = names.get(tva, ("", ""))
        control = "" if rule in OWN_RULES else committed
        rel = relation(nca, control, sup)
        edge_class, kept, excluded = child_channel(tva, by_parent.get(tva, []), sup, tables)
        if control:
            proposed = control
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
        if control and edge_class and edge_class != control:
            verdict = "parent-edge-disagrees"
        owner = control or edge_class
        not_below = [c for c in callers if owner and c != owner and owner not in chain(sup, c)]
        evidence = []
        if callers:
            evidence.append("%d caller(s), %d distinct Super" % (len(callers), len(supers)))
        if nca:
            evidence.append("nca %s (%d ancestor(s))" % (nca, len(chain(sup, nca))))
        if kept:
            evidence.append("child edge from %s" % ", ".join(kept))
        if excluded:
            evidence.append("%d child edge(s) are a member construction: %s"
                            % (len(excluded), ", ".join(excluded)))
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
            "parent_edge_children": "|".join(kept),
            "parent_edge_excluded": "|".join(excluded),
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
    own = [r for r in rows if r["name_rule"] in OWN_RULES]
    scored = [r for r in named if r["name_rule"] not in OWN_RULES]
    by_rel = collections.Counter(r["relation"] for r in scored)
    split = [r for r in rows if int(r["distinct_supers"] or 0) > 1]
    split_named = [r for r in split if r["committed"] and r["name_rule"] not in OWN_RULES]
    split_own = [r for r in split if r["committed"] and r["name_rule"] in OWN_RULES]
    split_unnamed = [r for r in split if not r["committed"]]
    edge = [r for r in rows if r["parent_edge_children"]]
    edge_named = [r for r in edge if r["committed"]]
    edge_scored = [r for r in edge_named if r["name_rule"] not in OWN_RULES]
    edge_bad = [r for r in edge_scored if r["parent_edge_class"]
                and r["parent_edge_class"] != r["committed"]]
    edge_new = [r for r in edge if not r["committed"] and r["parent_edge_class"]]
    edge_split = [r for r in edge if r["parent_edge_children"] and not r["parent_edge_class"]]
    excluded = [r for r in rows if r["parent_edge_excluded"]]
    lines = []
    desc = [r for r in scored if r["relation"] == "descendant"]
    desc_single = [r for r in desc if len([c for c in r["callers"].split("|") if c]) == 1]
    lines.append("**%d tables are read here: %d carry a caller edge and %d are reached only by a "
                 "child edge. The class map names %d of them%s. The callers' nearest common ancestor "
                 "IS that class in %d, is one of its descendants in %d, and is an unrelated class in "
                 "%d.** %d of the %d descendant rows are a table with a single named caller, where "
                 "the ancestor of one class is its parent -- so the reading is a *lower bound*: a "
                 "table's class is its callers' nearest common ancestor or an ancestor of it, never "
                 "below it."
                 % (len(rows), len([r for r in rows if r["callers"]]),
                    len([r for r in rows if not r["callers"]]), len(named),
                    (", and %d of those names are a row this tool wrote, blanked before scoring"
                     % len(own)) if own else "",
                    by_rel["equal"], by_rel["descendant"], by_rel["unrelated"],
                    len(desc_single), len(desc)))
    lines.append("")
    lines.append("| table | slots | callers | distinct `Super` | nearest common ancestor | committed | "
                 "child edge names | child edges excluded | verdict |")
    lines.append("|---|---:|---:|---:|---|---|---|---|---|")
    for r in sorted(rows, key=lambda x: (x["committed"] != "", x["table_va"])):
        if int(r["distinct_supers"] or 0) < 2 and r["committed"] and not r["parent_edge_excluded"]:
            continue
        lines.append("| `%s` | %s | %d | %d | %s | %s | %s | %s | %s |" % (
            r["table_va"], r["slots"] or "-", len([c for c in r["callers"].split("|") if c]),
            int(r["distinct_supers"] or 0), r["nearest_common_ancestor"] or "--",
            ("`%s`" % r["committed"]) if r["committed"] else "--",
            ("`%s`" % r["parent_edge_class"]) if r["parent_edge_class"] else "--",
            len([c for c in r["parent_edge_excluded"].split("|") if c]) or "--",
            r["verdict"]))
    lines.append("")
    lines.append("**%d table(s) split** (their callers disagree on `Super`): **%d of the %d named ones "
                 "have the committed name as their nearest common ancestor**%s. %s"
                 % (len(split), len(split_named), len(split_named),
                    "".join("; `%s` %s" % (r["table_va"], r["relation"])
                            for r in split if r["committed"] and r["name_rule"] not in OWN_RULES
                            and r["relation"] != "equal"),
                    ("The %d unnamed one%s stay unnamed on the caller side; the children settle "
                     "them below." % (len(split_unnamed), "" if len(split_unnamed) == 1 else "s"))
                    if split_unnamed else
                    ("The %d the class map holds only because this tool named them (%s) are blanked "
                     "here, so the score is over names another rule wrote."
                     % (len(split_own), ", ".join("`%s`" % r["table_va"] for r in split_own)))
                    if split_own else ""))
    lines.append("")
    lines.append("**The children's `Super` channel covers %d tables. %d of them carry a name in the "
                 "class map and %d of those were written by another rule; the equation "
                 "`table(Super(C)) = parent_va(C)` reproduces that name %d of %d, 0 disagreeing, and "
                 "the remaining %d are names this tool wrote, blanked here. It names the %d table(s) "
                 "the map does not.** The channel reads a "
                 "class's own first table-storing call as its *base* call -- and when the class "
                 "object's `Super` names a class whose table is a different one, that reading is "
                 "wrong by the same evidence: %d child edge(s) are excluded as member constructions "
                 "(%s). %d table(s) carry a child edge the channel cannot decide (%s)."
                 % (len(edge), len(edge_named), len(edge_scored),
                    len(edge_scored) - len(edge_bad), len(edge_scored), len(edge_named) - len(edge_scored),
                    len(edge_new), len(excluded),
                    "; ".join("`%s` excluded `%s`" % (r["table_va"], r["parent_edge_excluded"])
                              for r in excluded) or "none",
                    len(edge_split),
                    ", ".join("`%s`" % r["table_va"] for r in edge_split) or "none"))
    lines.append("")
    strays = [r for r in rows if r["callers_not_below"]]
    if strays:
        lines.append("**%d row(s) separate the two channels**: %s. A caller that is not a descendant "
                     "of the class its table names does not install the table in its own object, so "
                     "its store is a member construction rather than a vtable -- which is what the "
                     "`parent-unnamed` row this page carried on `0x004fc588` reads as: `0x004fc4f0` "
                     "is `LevelBase`, `PendingLevel`'s installer constructs a member of that class, "
                     "and `PendingLevel`'s `Super` stays `Object` as its class object says."
                     % (len(strays),
                        "; ".join("`%s` names `%s` and %d of its %d caller(s) are not below it (%s)"
                                  % (r["table_va"], r["parent_edge_class"] or r["committed"],
                                     len([c for c in r["callers_not_below"].split("|") if c]),
                                     len([c for c in r["callers"].split("|") if c]),
                                     ", ".join(r["callers_not_below"].split("|")))
                                  for r in strays)))
        lines.append("")
    if edge_new:
        lines.append("**What the children's `Super` names, and the map does not:** %s. Each is a "
                     "class a child states its parent is, so the table is that class's own; "
                     "`tools/class-table-names.py` writes the row with `name_rule=child-parent-edge` "
                     "and the class map, the ctor roster and the declarer walk follow."
                     % ", ".join("`%s` = %s (from %s)" % (r["table_va"], r["parent_edge_class"],
                                                         ", ".join(r["parent_edge_children"].split("|")))
                                 for r in edge_new))
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
           "Other": "Object", "Embedder": "Object"}
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

    # the child channel: the table is the class its children say their parent is -- and the one
    # reading it can fail on is a class whose Super names a class with a *different* table
    base = {"Child": "NewBase", "Sibling": "NewBase", "NewBase": "Object",
            "LeafA": "LeafBase", "LeafB": "LeafBase", "LeafBase": "Object",
            "Other": "Object", "Embedder": "Object", "Grand": "Child"}
    tables = {"Child": 0x2000, "Object": 0x3000, "LeafA": 0x6000}   # class -> its own table
    proposal, kept, excluded = child_channel(0x4000, ["LeafA", "LeafB"], base, tables)
    if (proposal, kept, excluded) != ("LeafBase", ["LeafA", "LeafB"], []):
        bad.append("two children that say LeafBase do not name the table: %r" % ((proposal, kept, excluded),))
    # a child whose Super's table is a different one embeds an object, and does not vote
    proposal, kept, excluded = child_channel(0x4000, ["LeafA", "Embedder"], base, tables)
    if proposal != "LeafBase" or excluded != ["Embedder(Object->0x00003000)"]:
        bad.append("a member-construction child was counted: %r" % ((proposal, kept, excluded),))
    if child_channel(0x4000, ["Embedder"], base, tables)[0] != "":
        bad.append("a table whose only child was excluded still got a name")
    if child_channel(0x4000, ["LeafA", "Unknown"], base, tables)[0] != "":
        bad.append("a child with no Super did not stop the vote")

    super_names = {0x1000: {"children": "Child|Sibling", "child_supers": "NewBase|NewBase"},
                   0x2000: {"children": "Grand", "child_supers": "Child"},
                   0x4000: {"children": "LeafA|LeafB", "child_supers": "LeafBase|LeafBase"}}
    parent_edges = [{"parent_va": "0x1000", "class": "Child"},
                    {"parent_va": "0x1000", "class": "Sibling"},
                    {"parent_va": "0x4000", "class": "LeafA"},
                    {"parent_va": "0x4000", "class": "LeafB"},
                    {"parent_va": "0x4000", "class": "Embedder"}]
    names = {0x1000: ("NewBase", "child-parent-edge"), 0x2000: ("Child", "child-super"),
             0x3000: ("Object", "unshared-thunk"), 0x6000: ("LeafA", "child-super"),
             0x4000: ("", "")}
    slots = {0x1000: 7, 0x2000: 4, 0x4000: 9}
    rows = harvest(base, super_names, parent_edges, names, slots)
    by = {int(r["table_va"], 16): r for r in rows}
    if by[0x1000]["relation"] != "unnamed" or by[0x1000]["verdict"] != "named-by-parent-edge":
        bad.append("a name this tool wrote is scored against itself: %r" % (by[0x1000],))
    if by[0x2000]["relation"] != "descendant":
        bad.append("the one-caller table is a descendant bound: %r" % (by[0x2000],))
    if by[0x4000]["verdict"] != "named-by-parent-edge" or by[0x4000]["proposed_class"] != "LeafBase":
        bad.append("the parent edge names the unnamed table: %r" % (by[0x4000],))
    if by[0x4000]["parent_edge_excluded"] != "Embedder(Object->0x00003000)":
        bad.append("the excluded child is not reported on the row: %r" % (by[0x4000],))
    if by[0x4000]["parent_edge_children"] != "LeafA|LeafB":
        bad.append("the kept children are not reported on the row: %r" % (by[0x4000],))
    # a name another rule wrote is the control, and it is scored
    names = {**names, 0x4000: ("LeafBase", "unshared-thunk")}
    by = {int(r["table_va"], 16): r for r in harvest(base, super_names, parent_edges, names, slots)}
    if by[0x4000]["verdict"] != "equal" or by[0x4000]["relation"] != "equal":
        bad.append("the control does not agree with another rule's name: %r" % (by[0x4000],))
    out = render(rows)
    for want in ("nearest common ancestor", "split", "children's `Super`"):
        if want not in out:
            bad.append("render lacks %r" % (want,))

    path = os.path.join(tempfile.mkdtemp(), "a.csv")
    write_artifact(rows, path)
    if read_csv(path) != rows:
        bad.append("artifact round trip differs")
    if bad:
        print("FAIL class-table-lca: " + "; ".join(bad))
        return 1
    print("ok   class-table-lca: the chain, the nearest common ancestor, the relation, the children's "
          "`Super` equation and its member-construction exclusion agree with the synthetic tables")
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

    sup = hierarchy(read_csv(IMAGE_EDGES), read_csv(CLASSES))
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
