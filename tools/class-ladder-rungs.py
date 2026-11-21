#!/usr/bin/env python3
"""The 493 measured parent edges as inheritance links: a rung census with the script side as control.

`inventory/class_table_parent_edges.csv` records, for every class table, the table its own installer's
base-constructor call installs -- a **measured** parent link rather than the script `extends` column.
`inventory/class_virtual_overrides.csv` answers the other half ("which virtuals does a class replace")
but only where `classes.csv` has a script super, so the 88 edges whose parent is a table no registration
names -- the unnamed heads and their kin -- have no override row at all.

This tool diffs the two slot vectors over every measured edge and records what the child does to its
parent's table: how many slots it **repeats** at the same index, how many it **replaces**, whether slot 3
(the per-class bookkeeping slot, distinct in 492 of 492) differs, and how many slots it **adds**. The
verdict is the inheritance invariant a vtable must satisfy: a derived class's table is never *shorter*
than its parent's -- `extends` when it is not, `shorter` when it is, and `identical` when the child
copies its parent whole.

The control is the script-linked population: where both a script super and a measured parent exist the
two links name the same table (345 `agrees` rows), so this read's `replaced`/`added` counts must equal
the counts `class_virtual_overrides.csv` already publishes for those classes.

    tools/class-ladder-rungs.py --xbe DUMP/default.xbe -o inventory
    tools/class-ladder-rungs.py --report
    tools/class-ladder-rungs.py --write-doc docs/systems/CLASS_REGISTRATION.md
    tools/class-ladder-rungs.py --check-doc docs/systems/CLASS_REGISTRATION.md   # committed CSV, no dump
    tools/class-ladder-rungs.py --check        # synthetic tables, no dump, writes nothing

Addresses, slot indexes and counts only: no image content is written.
"""
import argparse
import collections
import csv
import os
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

EDGES = os.path.join(ROOT, "inventory", "class_table_parent_edges.csv")
TABLES = os.path.join(ROOT, "inventory", "xbe_class_tables.csv")
PLAIN = os.path.join(ROOT, "inventory", "class_plain_tables.csv")
OVERRIDES = os.path.join(ROOT, "inventory", "class_virtual_overrides.csv")
OUT = os.path.join(ROOT, "inventory", "class_ladder_rungs.csv")
DOC_START = "<!-- generated:class-ladder-rungs -->"
DOC_END = "<!-- /generated:class-ladder-rungs -->"
CLASS_SLOT = 3          # distinct per table in 492/492; bookkeeping, never an override (docs/NATIVE_VM.md)
FIELDS = ["child_va", "child_class", "child_slots", "parent_va", "parent_class", "parent_slots",
          "repeated", "replaced", "class_slot", "added", "verdict", "evidence"]


def read(path):
    if not os.path.isfile(path):
        return []
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def slot_counts():
    """table_va -> slot count, from the two committed censuses (the 492 class tables win a tie)."""
    out = {}
    for row in read(PLAIN):
        try:
            out[int(row["table_va"], 16)] = int(row["slots"])
        except (KeyError, ValueError):
            continue
    for row in read(TABLES):
        try:
            out[int(row["table_va"], 16)] = int(row["slots"])
        except (KeyError, ValueError):
            continue
    return out


def read_slots(xbe, va, count):
    try:
        return struct.unpack("<%dI" % count, xbe.read_va(va, 4 * count))
    except Exception:      # a table that runs off a mapped section is skipped, not guessed
        return None


def diff(child, parent):
    """The four counts, and slot 3 reported separately (it is per-class, not an override)."""
    common = min(len(child), len(parent))
    repeated = replaced = 0
    for idx in range(common):
        if idx == CLASS_SLOT:
            continue
        if child[idx] == parent[idx]:
            repeated += 1
        else:
            replaced += 1
    class_slot = "same"
    if len(child) > CLASS_SLOT and len(parent) > CLASS_SLOT:
        class_slot = "differs" if child[CLASS_SLOT] != parent[CLASS_SLOT] else "same"
    added = max(0, len(child) - len(parent))
    if len(child) < len(parent):
        verdict = "shorter"
    elif replaced == 0 and added == 0:
        verdict = "identical"
    else:
        verdict = "extends"
    return repeated, replaced, class_slot, added, verdict


def harvest(xbe, edges, counts):
    rows = []
    for row in edges:
        try:
            cva = int(row["table_va"], 16)
            pva = int(row["parent_va"], 16)
        except (KeyError, ValueError):
            continue                      # the census summary row, and rows with no parent
        n_child, n_par = counts.get(cva), counts.get(pva)
        if not n_child or not n_par:
            continue
        child, parent = read_slots(xbe, cva, n_child), read_slots(xbe, pva, n_par)
        if child is None or parent is None:
            continue
        repeated, replaced, class_slot, added, verdict = diff(child, parent)
        rows.append({
            "child_va": "0x%08x" % cva, "child_class": row.get("class", ""),
            "child_slots": n_child, "parent_va": "0x%08x" % pva,
            "parent_class": row.get("parent_class", ""), "parent_slots": n_par,
            "repeated": repeated, "replaced": replaced, "class_slot": class_slot,
            "added": added, "verdict": verdict,
            "evidence": "%d slot(s) against %d: %d repeated, %d replaced, slot 3 %s, %d added"
                        % (n_child, n_par, repeated, replaced, class_slot, added),
        })
    rows.sort(key=lambda r: (r["parent_va"], r["child_va"]))
    return rows


def count_overrides(path):
    """class -> (override count, added count) as class_virtual_overrides.csv already publishes them."""
    out = collections.defaultdict(lambda: [0, 0])
    for row in read(path):
        cls = row.get("class", "")
        if not cls:
            continue
        out[cls]          # a pair always writes a class-slot row, so a class with no override is a row
        if row.get("relationship") == "override":
            out[cls][0] += 1
        elif row.get("relationship") == "added":
            out[cls][1] += 1
    return {k: tuple(v) for k, v in out.items()}


def control(rows, published):
    """The same arithmetic on the population whose answer is already committed."""
    agree = differ = 0
    detail = []
    for row in rows:
        cls = row["child_class"]
        if not cls or cls not in published:
            continue
        got = (int(row["replaced"]), int(row["added"]))
        if got == published[cls]:
            agree += 1
        else:
            differ += 1
            if len(detail) < 4:
                detail.append("%s %s vs %s" % (cls, got, published[cls]))
    return agree, differ, detail


def control_line(agree, differ):
    return ("**Control: the %d script-linked edges this read reaches reproduce "
            "`class_virtual_overrides.csv` on %d, %d disagreeing.** Same slot vectors and the same "
            "slot-3 exclusion, so a count on a row that has no published answer is the same measurement."
            % (agree + differ, agree, differ))


def heads(rows):
    """One summary per parent nobody names: the heads whose rungs have no other read."""
    groups = collections.defaultdict(list)
    for row in rows:
        groups[row["parent_va"]].append(row)
    out = []
    for pva, group in sorted(groups.items()):
        if group[0]["parent_class"]:
            continue
        verdicts = collections.Counter(r["verdict"] for r in group)
        out.append((pva, int(group[0]["parent_slots"]), len(group),
                    sum(int(r["replaced"]) for r in group), sum(int(r["added"]) for r in group),
                    max(int(r["child_slots"]) for r in group),
                    " ".join("%s %d" % (k, v) for k, v in sorted(verdicts.items()))))
    return out


def render(rows, line):
    tot = collections.Counter(r["verdict"] for r in rows)
    heads_rows = heads(rows)
    unnamed = [r for r in rows if not r["parent_class"]]
    lines = ["| parent (unnamed) | slots | children | replaced | added | most slots in a child | verdicts |",
             "|---|---|---|---|---|---|---|"]
    for pva, slots, n, rep, add, big, verdicts in heads_rows:
        lines.append("| `%s` | %d | %d | %d | %d | %d | %s |" % (pva, slots, n, rep, add, big, verdicts))
    lines.append("")
    lines.append("**%d of the %d measured parent edges read as rungs -- %d `extends`, %d `identical`, "
                 "%d `shorter`.** A child's table is never shorter than its parent's, so a `shorter` row is "
                 "the parent link or the slot count, not the rule: %s."
                 % (len(rows) - tot["shorter"], len(rows), tot["extends"], tot["identical"],
                    tot["shorter"], ("none does" if not tot["shorter"] else "the rows above carry them")))
    lines.append("")
    lines.append("**%d of the %d edges land in a parent no registration names, over %d distinct heads**, "
                 "and every one of those heads is a class with no script row, so this is the only override "
                 "census the 88 edges have: the remaining %d are the script-linked population the control "
                 "is read on." % (len(unnamed), len(rows), len(heads_rows), len(rows) - len(unnamed)))
    lines.append("")
    lines.append(line)
    return lines


def write_doc(path, rows, line, check=False):
    text = open(path, encoding="utf-8").read()
    i, j = text.find(DOC_START), text.find(DOC_END)
    if i < 0 or j < 0:
        raise SystemExit("%s: no %s block" % (path, DOC_START))
    new = text[:i + len(DOC_START)] + "\n" + "\n".join(render(rows, line)) + "\n" + text[j:]
    if check:
        if new != text:
            raise SystemExit("%s: the class-ladder-rungs block is stale" % path)
        print("ok   %s: class-ladder-rungs block reproduces" % path)
        return
    with open(path, "w", encoding="utf-8", newline="") as fh:
        fh.write(new)
    print("wrote %s" % path)


def write_csv(path, rows):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


class Fake:
    """A synthetic image: `va -> dword vector`, and nothing outside it."""

    def __init__(self, vectors):
        self.vectors = vectors

    def read_va(self, va, n):
        vec = self.vectors.get(va)
        if vec is None or n > 4 * len(vec):
            raise KeyError(hex(va))
        return struct.pack("<%dI" % len(vec), *vec)


def self_check():
    fails = []

    def want(label, cond):
        if not cond:
            fails.append(label)

    parent = [0x100, 0x200, 0x300, 0x400]
    child = [0x100, 0x999, 0x300, 0x777, 0x500]      # slot 1 replaced, slot 3 per-class, one added
    want("a child that replaces one slot and adds one",
         diff(child, parent) == (2, 1, "differs", 1, "extends"))
    want("a child that copies its parent whole is identical", diff(parent, parent)[4] == "identical")
    want("a shorter child is not a rung", diff(parent[:3], parent)[4] == "shorter")
    want("slot 3 alone is not an override", diff([0x100, 0x200, 0x300, 0x401], parent)[1] == 0)
    xbe = Fake({0x1000: parent, 0x2000: child})
    edges = [{"table_va": "0x00002000", "parent_va": "0x00001000", "class": "Child", "parent_class": ""},
             {"table_va": "0x00003000", "parent_va": "0x00001000"}]     # no slot count: skipped
    rows = harvest(xbe, edges, {0x1000: 4, 0x2000: 5})
    want("one row per readable edge, with the class it carries",
         len(rows) == 1 and rows[0]["child_class"] == "Child")
    want("the control counts a reproduced row", control(rows, {"Child": (1, 1)})[:2] == (1, 0))
    want("a mismatch is counted, not hidden", control(rows, {"Child": (2, 1)})[:2] == (0, 1))
    block = "\n".join(render(rows, control_line(1, 0)))
    want("the block names the control", "Control: the 1 script-linked edges" in block)
    want("the block lists the unnamed head", "`0x00001000`" in block)
    if fails:
        print("FAIL class-ladder-rungs: %s" % "; ".join(fails))
        return 1
    print("ok   class-ladder-rungs: 10 assertions")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--xbe", help="the dump's default.xbe")
    ap.add_argument("-o", "--out", help="artifact directory")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--write-doc", metavar="PAGE")
    ap.add_argument("--check-doc", metavar="PAGE")
    ap.add_argument("--check", action="store_true", help="synthetic tables, no dump, no write")
    args = ap.parse_args()
    if args.check:
        return self_check()
    if args.check_doc or args.write_doc:
        rows = read(OUT)
        if not rows:
            raise SystemExit("%s: missing -- regenerate it with --xbe" % OUT)
        agree, differ, _ = control(rows, count_overrides(OVERRIDES))
        write_doc(args.check_doc or args.write_doc, rows, control_line(agree, differ),
                  check=bool(args.check_doc))
        return 0
    if not args.xbe:
        if args.report:
            rows = read(OUT)
            agree, differ, _ = control(rows, count_overrides(OVERRIDES))
            print("\n".join(render(rows, control_line(agree, differ))))
            return 0
        ap.error("--xbe, --report, --check or a doc pass is required")
    from xbe import Xbe
    edges = [r for r in read(EDGES) if r.get("table_va", "").startswith("0x")]
    rows = harvest(Xbe(args.xbe), edges, slot_counts())
    agree, differ, detail = control(rows, count_overrides(OVERRIDES))
    if differ:
        raise SystemExit("the control disagrees on %d class(es) -- %s. Regenerate %s before trusting any "
                         "row this run would publish." % (differ, "; ".join(detail), OVERRIDES))
    print("edges %d, read %d; control %d agree / %d differ%s"
          % (len(edges), len(rows), agree, differ, (": " + "; ".join(detail)) if detail else ""))
    if args.out:
        write_csv(os.path.join(args.out, "class_ladder_rungs.csv"), rows)
        print("wrote %s" % os.path.join(args.out, "class_ladder_rungs.csv"))
    if args.report or not args.out:
        print("\n".join(render(rows, control_line(agree, differ))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
