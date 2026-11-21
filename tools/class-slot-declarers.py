#!/usr/bin/env python3
"""Who declares each gap body that sits in a table slot, and what name it therefore earns.

U40's slot channel is "a dword outside `.text` names this head", and the census records the **first**
table a reference lands in (`inventory/gap_heads.csv`'s `slot_table`/`slot_class`/`slot_index`). A
body in a class table is not held by one table: every descendant class that does not override the slot
points at the same dword, so the same body appears at the same index in a whole subtree of tables, and
"which table did the scan see first" is not a declaration. This tool reads **all 724 tables** the
project censuses -- 492 class tables (`inventory/xbe_class_tables.csv`) and 232 interface tables
(`inventory/native_vtable_shared.csv`) -- indexes every slot body, and for each gap head that sits in a
slot decides:

  * **declared** -- some class declares the body: walking up its own table's inheritance chain, the
    declarer is the highest class in the chain whose slot at that index holds the body. That class (and
    only it) can name the body `<Class>_vt<slot>`.
  * **conflict** -- two classes that are not one chain's ancestor and descendant both declare it, or it
    is declared at two different indices; the artifact carries both pairs and earns no name.
  * **member** -- no class declares it and a table a constructor stores into a member field holds it
    (`inventory/class_member_tables.csv`): the body is a method of that member, and a `<Class>_vt<slot>`
    name would claim a class slot the image does not have.
  * **unnamed-table** -- it sits only in one of the class tables the class map leaves unnamed; the class
    is unknown, so the slot is a real declaration with no name to give.
  * **interface** -- only interface tables hold it; the slot is an interface method and the name has to
    come from the interface, which is the residue U40 already measured.

The control is the map's own `<Class>_vt<slot>` rows: 538 names were built from a table slot, and the
same declaration walk must say the same class. A name whose declarer is an ancestor would prove the map
names a holder rather than a declarer; an unresolved one must name the unnamed parent table that stopped
the walk, so the residue is visible.

    SG1_DUMP=$HOME/nfs/sg1-dump python3 tools/class-slot-declarers.py \\
        --xbe "$SG1_DUMP/Stargate_SG-1_-_The_Alliance-2005-12-15/default.xbe" -o inventory
    python3 tools/class-slot-declarers.py --report
    python3 tools/class-slot-declarers.py --write-doc docs/systems/CLASS_REGISTRATION.md
    python3 tools/class-slot-declarers.py --check-doc docs/systems/CLASS_REGISTRATION.md
    python3 tools/class-slot-declarers.py --check        # artifact + synthetic walks, no dump, no write
    python3 tools/class-slot-declarers.py --xbe ... --emit-adds   # the map rows these names earn
    python3 tools/class-slot-declarers.py --xbe ... --apply-adds  # write them, then `make symbols`

Addresses, slot indices, class names and counts only; no package or image content is written.
"""
import argparse
import collections
import csv
import os
import re
import struct
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))

OUT = os.path.join("inventory", "class_slot_declarers.csv")
GAPS = os.path.join(ROOT, "inventory", "gap_heads.csv")
CLASS_TABLES = os.path.join(ROOT, "inventory", "xbe_class_tables.csv")
TABLE_NAMES = os.path.join(ROOT, "inventory", "xbe_class_table_names.csv")
INTERFACE_TABLES = os.path.join(ROOT, "inventory", "native_vtable_shared.csv")
MEMBER_TABLES = os.path.join(ROOT, "inventory", "class_member_tables.csv")
CLASSES = os.path.join(ROOT, "inventory", "classes.csv")
EDGES = os.path.join(ROOT, "inventory", "class_table_parent_edges.csv")
SYMBOLS = os.path.join(ROOT, "symbols", "xbox_functions.csv")
BOUNDARIES = os.path.join(ROOT, "symbols", "xbe_boundaries.csv")
OVERRIDES = os.path.join(ROOT, "symbols", "xbe_overrides.csv")
# a body may be added only where a linear decode closes on a flow terminator: an `add` row whose size
# is a guess puts every later row's address at risk, and the map is what the disassembly is.
CLOSES = ("ret", "ret-imm", "jmp", "jmp-ind")
DOC_START = "<!-- generated:class-slot-declarers -->"
DOC_END = "<!-- /generated:class-slot-declarers -->"
FIELDS = ("row", "head_va", "size", "verdict", "declarer", "slot", "name_earned", "current_name",
          "current_agrees", "sit_ins", "tables", "indices", "member", "member_tables", "evidence",
          "census")
CLASS_SLOT_NAME = re.compile(r"^(?P<cls>.+)_vt(?P<slot>\d+)$")


# --------------------------------------------------------------------------- reading
def read_csv(path):
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def load_inventory():
    """Every input the census needs, as plain dicts. Nothing here touches the image."""
    tables = {int(r["table_va"], 16): int(r["slots"]) for r in read_csv(CLASS_TABLES)}
    shared = {int(r["table_va"], 16): int(r["table_slots"]) for r in read_csv(INTERFACE_TABLES)}
    class_of = {}
    for r in read_csv(TABLE_NAMES):
        if r.get("class"):
            class_of[int(r["table_va"], 16)] = r["class"]
    member = {int(r["table_va"], 16): r for r in read_csv(MEMBER_TABLES)}
    # the parent table of every edge the rung census measured, and the script super an edge names for
    # a parent table no registration named -- the walk then needs no class name to reach a parent
    parent, parent_name = {}, {}
    for r in read_csv(EDGES):
        if not r.get("table_va") or not r.get("parent_va"):
            continue
        t, pv = int(r["table_va"], 16), int(r["parent_va"], 16)
        parent.setdefault(t, pv)
        if r.get("script_super"):
            parent_name.setdefault(pv, r["script_super"])
    supers = {}
    for r in read_csv(CLASSES):
        supers.setdefault(r["class"], r.get("super") or "")
    heads = [r for r in read_csv(GAPS) if r.get("row") == "head"]
    gaps = {h["head_va"]: h for h in heads}
    return dict(tables=tables, shared=shared, class_of=class_of, member=member, supers=supers,
                heads=heads, gaps=gaps, parent=parent, parent_name=parent_name)


# --------------------------------------------------------------------------- the declaration walk
def declarer(slot_of, tables, class_of, table_of, supers, table, index, body, parent=None,
             parent_name=None):
    """The table that declares `body` at `index`, walked up the measured parent edges.

    The parent link comes from `inventory/class_table_parent_edges.csv` (the rung census's 493 measured
    edges), not from the class names: a parent table no registration named still has an edge and an extent,
    so a slot beyond that extent is the child's own and the walk does not have to stop there. The class-name
    route is the fallback for a table the edge census does not cover.

    Returns (class, note). A declaring table with no class name returns ("", "unnamed-declarer:0x…"), and
    the note names the script super the edges recorded for it when there is one.
    """
    parent = parent or {}
    parent_name = parent_name or {}
    cur, who, seen = table, None, set()
    while cur is not None and cur not in seen:
        seen.add(cur)
        n = tables.get(cur)
        if n is None:
            return (who or "", "no-extent:0x%08x" % cur) if who is None else (
                class_of.get(who, ""), "" if class_of.get(who) else
                "unnamed-declarer:0x%08x" % who)
        slots = slot_of(cur, n)
        if index >= len(slots) or slots[index] != body:
            break
        who = cur
        nxt = parent.get(cur)
        if nxt is None:
            cls = class_of.get(cur)
            if cls:
                return cls, ""
            script = parent_name.get(cur, "")
            return "", "unnamed-declarer:0x%08x%s" % (cur, (":" + script) if script else "")
        cur = nxt
    if who is None:
        return "", "no-holder"
    cls = class_of.get(who)
    if cls:
        return cls, ""
    script = parent_name.get(who, "")
    return "", "unnamed-declarer:0x%08x%s" % (who, (":" + script) if script else "")


def index_bodies(slot_of, tables, shared):
    """body_va -> [(table, index, kind)] over every table the project censuses."""
    holds = collections.defaultdict(list)
    for t in sorted(tables):
        for i, v in enumerate(slot_of(t, tables[t])):
            holds[v].append((t, i, "class"))
    for t in sorted(shared):
        if t in tables:
            continue
        try:
            slots = slot_of(t, shared[t])
        except Exception:  # noqa: BLE001 -- an extent that runs off a section is not a body
            continue
        for i, v in enumerate(slots):
            holds[v].append((t, i, "interface"))
    return holds


def census_heads(inv, slot_of):
    tables, shared = inv["tables"], inv["shared"]
    class_of, member, supers = inv["class_of"], inv["member"], inv["supers"]
    parent, parent_name = inv["parent"], inv["parent_name"]
    table_of = {}
    for t in sorted(class_of):
        table_of.setdefault(class_of[t], t)
    holds = index_bodies(slot_of, tables, shared)
    rows = []
    sit_class, sit_iface = 0, 0
    for h in inv["heads"]:
        va = int(h["head_va"], 16)
        sits = holds.get(va, [])
        if not sits:
            continue
        exact = collections.OrderedDict()      # (class, slot) -> holders, the declaring table is named
        unnamed = collections.OrderedDict()    # (table, slot, script) -> holders, the declarer has no class
        mems, ifaces, unresolved = set(), set(), set()
        for t, i, kind in sits:
            if kind != "class":
                sit_iface += 1
                if t in member:
                    mems.add((t, i))
                else:
                    ifaces.add((t, i))
                continue
            sit_class += 1
            if class_of.get(t):
                d, note = declarer(slot_of, tables, class_of, table_of, supers, t, i, va,
                                   parent, parent_name)
                if not note:
                    exact.setdefault((d, i), []).append(t)
                elif note.startswith("unnamed-declarer:"):
                    bits = note.split(":")[1:]
                    unnamed.setdefault((bits[0], i, bits[1] if len(bits) > 1 else ""), []).append(t)
                else:
                    unresolved.add((t, i))
            else:
                unresolved.add((t, i))
        if len(exact) == 1:
            (who, slot), holders = list(exact.items())[0]
            verdict, name = "declared", "%s_vt%d" % (who, slot)
            evidence = ("the %s subtree holds it at slot %d in %d table(s) and the parent edges reach a "
                        "named declarer: %s declares it" % (who, slot, len(holders), who))
        elif len(exact) > 1:
            verdict, name = "conflict", ""
            evidence = "two independent declarations: " + "; ".join(
                "%s_vt%d (held by %d)" % (d, i, len(h)) for (d, i), h in exact.items())
        elif unnamed:
            slots = {(t, i) for t, i, _ in unnamed}
            if len(slots) == 1:
                (tbl, slot, script) = list(unnamed)[0]
                verdict = "declared-unnamed"
                name = "%s_vt%d" % (script, slot) if script else ""
                evidence = ("declared by the unnamed table %s slot %d%s -- no registration names the "
                            "table, so the name comes from the script super an edge records for it"
                            % (tbl, slot, (" (`%s`)" % script) if script else ", and no edge names its class"))
            else:
                verdict, name = "conflict", ""
                evidence = "two declarations on unnamed tables: " + "; ".join(
                    "%s_vt%d" % (t, i) for t, i, _ in unnamed)
        elif mems:
            verdict = "member"
            t, i = min(mems)
            label = member[t].get("member_name") or ""
            name = "%s_vt%d" % (label, i) if label else ""
            evidence = ("member table 0x%08x slot %d (%s), field %s -- not a class slot"
                        % (t, i, label or "no label", member[t].get("fields") or "?"))
        elif unresolved:
            verdict = "unnamed-table"
            t, i = min(unresolved)
            name = ""
            evidence = ("class table 0x%08x slot %d, and neither a class name nor a parent edge reaches "
                        "it" % (t, i))
        else:
            verdict = "interface"
            name = ""
            evidence = "%d interface table(s) hold it; no class table does" % len(ifaces)
        if verdict in ("declared", "declared-unnamed") and mems:
            evidence += "; also a member table's slot"
        cur = ("%s_vt%s" % (h["slot_class"], h["slot_index"])) if h.get("slot_class") else ""
        if cur and name:
            agrees = "yes" if cur == name else "no"
        elif cur and not name:
            agrees = "-"
        else:
            agrees = ""
        if len(exact) == 1:
            dec, slot = list(exact)[0]
        elif unnamed:
            dec, slot = list(unnamed)[0][0], str(list(unnamed)[0][1])
        elif mems:
            dec, slot = "", str(min(mems)[1])
        else:
            dec, slot = "", ""
        labels = sorted({member[t].get("member_name") or "" for t, _ in mems
                         if member[t].get("member_name")})
        mtables = sorted({t for t, _ in mems})
        if verdict == "member":
            # the label and the table the name was built from come first, so a reader never has to
            # guess which member a row is named after
            first = member[min(mems)[0]].get("member_name") or ""
            if first in labels:
                labels.remove(first)
                labels.insert(0, first)
            if min(mems)[0] in mtables:
                mtables.remove(min(mems)[0])
                mtables.insert(0, min(mems)[0])
        rows.append({"row": "head", "head_va": h["head_va"], "size": h.get("size", ""),
                     "verdict": verdict, "declarer": dec, "slot": str(slot),
                     "name_earned": name, "current_name": cur, "current_agrees": agrees,
                     "sit_ins": str(len(sits)), "tables": str(len({t for t, _, _ in sits})),
                     "indices": " ".join(str(i) for i in sorted({i for _, i, _ in sits})),
                     "member": " ".join(labels),
                     "member_tables": " ".join("0x%08x" % t for t in mtables),
                     "evidence": evidence})
    return rows, holds, (sit_class, sit_iface)


def census_control(inv, slot_of, holds):
    """The map's own `<Class>_vt<slot>` rows, read by the same walk.

    The artifact's own `add` rows are excluded: they carry this walk's conclusion, so reading them back
    as an independent control would count the answer as its own evidence.
    """
    tables, class_of, supers = inv["tables"], inv["class_of"], inv["supers"]
    table_of = {}
    for t in sorted(class_of):
        table_of.setdefault(class_of[t], t)
    mine = {int(r["head_va"], 16) for r in read_csv(os.path.join(ROOT, OUT)) if r.get("row") == "add"}
    rows = []
    for r in read_csv(SYMBOLS):
        m = CLASS_SLOT_NAME.match(r.get("name") or "")
        if not m:
            continue
        va, cls, slot = int(r["address"], 16), m.group("cls"), int(m.group("slot"))
        if va in mine:
            continue
        t = table_of.get(cls)
        rec = {"row": "control", "head_va": r["address"], "size": r.get("size", ""),
               "verdict": "unresolved", "declarer": "", "slot": str(slot), "name_earned": r["name"],
               "current_name": r["name"], "current_agrees": "-", "sit_ins": "", "tables": "",
               "indices": "", "member": "", "member_tables": "", "evidence": ""}
        if t is None:
            rec["evidence"] = "%s has no table in the class map" % cls
            rows.append(rec)
            continue
        slots = slot_of(t, tables[t])
        if slot >= len(slots) or slots[slot] != va:
            rec["evidence"] = "the row is not at slot %d of %s's own table" % (slot, cls)
            rows.append(rec)
            continue
        d, note = declarer(slot_of, tables, class_of, table_of, supers, t, slot, va,
                           inv["parent"], inv["parent_name"])
        rec["declarer"] = d
        if d == cls:
            rec["verdict"] = "agrees"
        else:
            rec["verdict"] = "derived"
            rec["evidence"] = ("the name's class %s does not declare it; %s does%s"
                               % (cls, d or "an unnamed table", (" (" + note + ")") if note else ""))
        if note and rec["verdict"] != "derived":
            rec["evidence"] = note
        rows.append(rec)
    return rows


# --------------------------------------------------------------------------- the rows the walk creates
def add_candidates(head_rows, gaps):
    """[(head row, name, gap head)] -- the bodies a settled declaration turns into a map row.

    Two verdicts carry a name the walk proved: a class declares the slot, or a member table the class
    constructor stores into a field holds it (the body is that member's method). A body is added only
    where the gap census decoded it to a flow terminator -- a name is not a size.
    """
    out, taken = [], set()
    for r in head_rows:
        if r["verdict"] not in ("declared", "member") or not r["name_earned"]:
            continue
        h = gaps.get(r["head_va"]) or {}
        if not h.get("size") or h.get("close") not in CLOSES:
            continue
        name = r["name_earned"]
        if name in taken:
            name += "_b"
        taken.add(name)
        out.append((r, name, h))
    return out


def add_evidence(r, h, name):
    """The one sentence an `add` row carries: who declares it, and why the map has no row."""
    pad = (("%d bytes of pad" % int(h["boundary"].split(":")[1])) if h["boundary"].startswith("pad")
           else {"after-ret": "a split boundary"}.get(h["boundary"], "the gap's start"))
    calls, jumps = int(h["calls"] or 0), int(h["jumps"] or 0)
    if calls or jumps:
        reach = ("the image's only references to it are %d call site(s) and %d jump(s), and the map "
                 "turned neither into a row" % (calls, jumps))
    else:
        reach = ("no `E8` and no pointer outside a table names it, so the call graph that builds this "
                 "map can never make it a function")
    if r["verdict"] == "member":
        who = ("the member table %s slot %s, which a constructor stores into the field `%s` -- the body "
               "is that member's method, not a class slot, so the name is the member's and not a "
               "`<Class>_vt<slot>` claim" % (r["member_tables"].split()[0] if r["member_tables"] else "?",
                                             r["slot"], r["member"]))
    else:
        who = ("slot %s of %d table(s) in %s's subtree hold it and the measured parent edges reach %s as "
               "the declarer, so the name is the declarer's and not the first table a reference landed "
               "in" % (r["slot"], int(r["tables"] or 0), r["declarer"], r["declarer"]))
    return ("the body in the gap %s..%s: %s; %s in front, an MSVC %s prologue and a linear decode "
            "closing on `%s` at 0x%08x; %s"
            % (h["gap_lo"], h["gap_hi"], who, pad, h["prologue"], h["close"],
               int(r["head_va"], 16) + int(r["size"]), reach))


def carried_adds():
    """The `add` rows the artifact already records, re-read against the map.

    An add row is the artifact's own receipt: after the census is regenerated the body is no longer a
    gap head, so the only record that it was added -- and the name it was given -- is this row. A row
    whose map name no longer matches keeps `current_agrees = no` and `--check` fails on it.
    """
    have = {}
    for row in read_csv(SYMBOLS):
        have[int(row["address"], 16)] = row.get("name") or ""
    out = []
    for r in read_csv(os.path.join(ROOT, OUT)):
        if r.get("row") != "add":
            continue
        got = have.get(int(r["head_va"], 16))
        rec = dict(r)
        rec["current_name"] = got or ""
        rec["current_agrees"] = "yes" if got == r["name_earned"] else ("no" if got else "")
        out.append(rec)
    return out


def append_adds(cand):
    """Write the population's `add` rows and the creation row each needs, refusing a duplicate.

    An `add` with no `xbe_overrides.csv` row is invisible to a fresh import (`XbePost.java` builds the
    function from that file), so the two rows are written together or not at all.
    """
    b_rows = read_csv(BOUNDARIES)
    o_rows = read_csv(OVERRIDES)
    have = {int(r["address"], 16) for r in b_rows} | {int(r["address"], 16) for r in o_rows}
    dup = [r["head_va"] for r, _n, _h in cand if int(r["head_va"], 16) in have]
    if dup:
        sys.exit("class-slot-declarers: --apply-adds: %d of the population is already a row (%s); "
                 "regenerate the census instead of re-adding it" % (len(dup), ", ".join(dup[:4])))
    names = {r.get("name") for r in read_csv(SYMBOLS)}
    clash = [n for _r, n, _h in cand if n in names]
    if clash:
        sys.exit("class-slot-declarers: --apply-adds: %d name(s) the map already uses (%s)"
                 % (len(clash), ", ".join(clash[:4])))
    art = []
    for r, name, h in cand:
        ev = add_evidence(r, h, name)
        b_rows.append({"address": r["head_va"], "action": "add", "target": name, "size": r["size"],
                       "evidence": ev})
        o_rows.append({"address": r["head_va"], "name": name,
                       "evidence": "added to the map by symbols/xbe_boundaries.csv: " + ev,
                       "class": "", "outer": "", "object_size": "", "assert_line": "", "module": "",
                       "source_file": "", "function_size": r["size"]})
        art.append({"row": "add", "head_va": r["head_va"], "size": r["size"], "verdict": "added",
                    "declarer": r["declarer"], "slot": r["slot"], "name_earned": name,
                    "current_name": "", "current_agrees": "", "sit_ins": r["sit_ins"],
                    "tables": r["tables"], "indices": r["indices"], "member": r["member"],
                    "member_tables": r["member_tables"], "evidence": ev, "census": ""})
    with open(BOUNDARIES, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["address", "action", "target", "size", "evidence"],
                           lineterminator="\n")
        w.writeheader()
        w.writerows(b_rows)
    with open(OVERRIDES, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["address", "name", "evidence", "class", "outer",
                                           "object_size", "assert_line", "module", "source_file",
                                           "function_size"], lineterminator="\r\n")
        w.writeheader()
        w.writerows(o_rows)
    with open(os.path.join(ROOT, OUT), "a", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, FIELDS, lineterminator="\r\n")
        for r in art:
            w.writerow(r)
    print("class-slot-declarers: %d add row(s) and %d creation row(s) written; re-run `make symbols`"
          % (len(cand), len(cand)))
    return art


def census_meta(head_rows, ctl_rows, inv, holds, sits=(0, 0), adds=None):
    v = collections.Counter(r["verdict"] for r in head_rows)
    m = {"heads": len(head_rows), "declared": v["declared"],
         "declared_unnamed": v["declared-unnamed"],
         "conflict": v["conflict"],
         "member": v["member"], "interface": v["interface"], "unnamed_table": v["unnamed-table"],
         "sit_ins": sum(int(r["sit_ins"]) for r in head_rows),
         "cross_index": sum(1 for r in head_rows if len(r["indices"].split()) > 1),
         "tables_read": len(inv["tables"]) + len([t for t in inv["shared"] if t not in inv["tables"]]),
         "class_tables": len(inv["tables"]), "named_class_tables": len(inv["class_of"]),
         "interface_tables": len(inv["shared"]), "member_tables": len(inv["member"]),
         "current_named": sum(1 for r in head_rows if r["current_name"]),
         "current_agree": sum(1 for r in head_rows if r["current_agrees"] == "yes"),
         "current_disagree": sum(1 for r in head_rows if r["current_agrees"] == "no"),
         "declarers": len({r["declarer"] for r in head_rows if r["declarer"]}),
         "declared_unnamed_tables": len({r["declarer"] for r in head_rows
                                         if r["verdict"] == "declared-unnamed"}),
         "declared_unnamed_named": sum(1 for r in head_rows
                                       if r["verdict"] == "declared-unnamed" and r["name_earned"]),
         "fanout_max": max([int(r["sit_ins"]) for r in head_rows] or [0]),
         "control_rows": len(ctl_rows),
         "control_agree": sum(1 for r in ctl_rows if r["verdict"] == "agrees"),
         "control_derived": sum(1 for r in ctl_rows if r["verdict"] == "derived"),
         "control_unresolved": sum(1 for r in ctl_rows if r["verdict"] == "unresolved")}
    m["class_sit_ins"], m["interface_sit_ins"] = sits
    m["declared_bytes"] = sum(int(r["size"]) for r in head_rows
                              if r["verdict"] in ("declared", "declared-unnamed") and r["size"])
    m["declared_tables"] = sum(int(r["tables"]) for r in head_rows if r["verdict"] == "declared")
    m["member_sit_tables"] = len({t for r in head_rows if r["verdict"] == "member"
                                 for t in r["member_tables"].split()})
    adds = adds or []
    gaps = inv.get("gaps", {})
    m["add_rows"] = len(adds)
    m["add_bytes"] = sum(int(r["size"]) for r in adds if r["size"])
    m["add_member"] = sum(1 for r in adds if not r["declarer"])
    m["add_live"] = sum(1 for r in adds if r["current_agrees"] == "yes")
    # the bodies this walk names that no `add` can carry: a name is not a size, and an undecoded body
    # would put every later row's address at risk
    m["named_unplaced"] = sum(
        1 for r in head_rows if r["name_earned"]
        and not (gaps.get(r["head_va"], {}).get("size")
                 and gaps.get(r["head_va"], {}).get("close") in CLOSES))
    return m


def meta_of(rows):
    """The census row as ints (the CSV round-trip turns every value into a string)."""
    out = {}
    for r in rows:
        if r.get("row") != "census":
            continue
        for kv in r["census"].split(";"):
            if "=" in kv:
                k, v = kv.split("=", 1)
                out[k] = int(v) if v.lstrip("-").isdigit() else v
    return out


# --------------------------------------------------------------------------- doc block
def render(rows, ctl, meta):
    if not rows:
        return "| no slot body was read | |\n"
    n = float(meta["heads"])
    lines = ["| what holds the body | bodies | share | name it earns |", "|---|---|---|---|"]
    lines.append("| a class table, and the walk reaches the top of its chain | %d | %.0f%% | "
                 "`<Class>_vt<slot>` |" % (meta["declared"], 100.0 * meta["declared"] / n))
    lines.append("| a class table no registration names, which the walk reaches by its parent edge | %d "
                 "| %.0f%% | `<script>_vt<slot>` when an edge records the script super (%d), none "
                 "otherwise |" % (meta["declared_unnamed"], 100.0 * meta["declared_unnamed"] / n,
                                  meta["declared_unnamed_named"]))
    lines.append("| two independent declarations of one body | %d | %.0f%% | none (a conflict) |"
                 % (meta["conflict"], 100.0 * meta["conflict"] / n))
    lines.append("| a member table (a constructor stores it into a field) | %d | %.0f%% | "
                 "`<Class>_member_<field>_vt<slot>` |"
                 % (meta["member"], 100.0 * meta["member"] / n))
    lines.append("| a class table the class map leaves unnamed | %d | %.0f%% | none (no class) |"
                 % (meta["unnamed_table"], 100.0 * meta["unnamed_table"] / n))
    lines.append("| interface tables only | %d | %.0f%% | none (an interface name) |"
                 % (meta["interface"], 100.0 * meta["interface"] / n))
    lines.append("")
    lines.append("**The %d bodies are %d slot sit-ins over %d tables, and a declared body sits in every "
                 "descendant table that does not override the slot.** The census reads the %d tables the "
                 "project knows -- %d class tables (%d with a class) and %d interface tables -- and indexes "
                 "every slot, because `inventory/gap_heads.csv`'s `slot_table` is the *first* table a "
                 "reference happened to land in, not a declaration. %d sit-ins are class-table slots and %d "
                 "are interface slots; the widest fan-out is **%d tables for one body**, so \"the first table "
                 "that holds it\" is a choice among a subtree. The declarer is the highest class in the "
                 "`extends` chain whose slot at that index still holds the body."
                 % (meta["heads"], meta["sit_ins"], meta["tables_read"], meta["tables_read"],
                    meta["class_tables"],
                    meta["named_class_tables"], meta["interface_tables"], meta["class_sit_ins"],
                    meta["sit_ins"] - meta["class_sit_ins"], meta["fanout_max"]))
    lines.append("")
    lines.append("**The control is the map's own names: %d of %d agree, %d name a class that inherits the "
                 "body, %d could not be resolved.** Every `<Class>_vt<slot>` row in "
                 "`symbols/xbox_functions.csv` is run through the same walk against its own class's table. "
                 "%s" % (meta["control_agree"], meta["control_rows"], meta["control_derived"],
                         meta["control_unresolved"],
                         ("The %d derived rows are %s: their class's table holds the body at that slot, and so "
                          "does an ancestor's, so the name is a holder's and not the declarer's."
                          % (meta["control_derived"],
                             ", ".join("`%s`" % r["current_name"] for r in ctl
                                       if r["verdict"] == "derived")))
                         if meta["control_derived"] else "No row names a derived holder."))
    lines.append("")
    named = [r for r in rows if r["current_name"]]
    lines.append("**The gap census's own slot names survive the walk: %d of %d name the declarer, %d name "
                 "someone else, and %d have no declarer to agree with.** The first-hit table agrees with the "
                 "declaration walk where the subtree's root is the table the reference landed in, which is "
                 "the common case; the artifact is what makes the difference checkable rather than assumed."
                 % (meta["current_agree"], len(named), meta["current_disagree"],
                    len(named) - meta["current_agree"] - meta["current_disagree"]))
    lines.append("")
    cross = [r for r in rows if len(r["indices"].split()) > 1]
    if cross:
        lines.append("**%d bodies sit at two or more slot indices**, which no single name can carry: %s. The "
                     "index set is recorded per body; the verdict is `conflict` only when two *classes* both "
                     "declare it -- an interface slot beside a class slot is one method seen through two "
                     "tables, not two declarations."
                     % (len(cross), ", ".join("`%s` (%s)" % (r["head_va"], r["indices"].replace(" ", "/"))
                                              for r in cross[:8])))
        lines.append("")
    conf = [r for r in rows if r["verdict"] == "conflict"]
    if conf:
        lines.append("**The %d conflicts are one body two independent classes both declare** -- the two "
                     "tables point at the same address, and whether the linker folded two identical "
                     "functions or both were built from one stub is not decided here: %s."
                     % (len(conf), "; ".join("`%s` at slot%s %s"
                                             % (r["head_va"], "" if len(r["indices"].split()) == 1 else "s",
                                                r["indices"].replace(" ", "/")) for r in conf[:6])))
        lines.append("")
    lines.append("**What the walk did with its names.** %d of these bodies are map rows now -- %d named "
                 "`<Class>_vt<slot>` from the declaration, %d a member's method -- %d bytes, each through "
                 "`symbols/xbe_boundaries.csv` with the `xbe_overrides.csv` creation row a fresh import "
                 "needs, and each named after its **declarer**, never the first table that held the "
                 "pointer. %d bodies earn a name the decode cannot carry, because a name is not a size. "
                 "What is left is a name, not a read: %d are an interface's method, and %d sit in a class "
                 "table whose class the class map has not named."
                 % (meta["add_rows"], meta["add_rows"] - meta["add_member"], meta["add_member"],
                    meta["add_bytes"], meta["named_unplaced"], meta["interface"],
                    meta["unnamed_table"]))
    lines.append("")
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- checks
def synthetic():
    """The walk on tables whose answers are known, with no image and no artifact."""
    tables = {0x100: 6, 0x200: 8, 0x300: 6, 0x400: 8}
    grid = {0x100: [1, 2, 3, 90, 91, 92],
            0x200: [1, 2, 3, 90, 91, 92, 77, 78],     # inherits 3, adds 6 and 7
            0x300: [1, 2, 3, 77, 91, 92],             # a sibling that declares its own slot 3
            0x400: [1, 2, 3, 90, 91, 92, 0, 0]}       # Kid's child, overrides slot 6 with 0
    class_of = {0x100: "Base", 0x200: "Kid", 0x300: "Other", 0x400: "Lone"}
    parent = {0x200: 0x100, 0x300: 0x100, 0x400: 0x200}
    parent_name = {0x100: "Base"}
    slot_of = lambda t, n: grid[t][:n]  # noqa: E731
    checks = []

    def want(name, got, expect):
        checks.append((name, got, expect))

    def walk(t, i, b, co=None, pa=None, pn=None):
        return declarer(slot_of, tables, co or class_of, {}, {}, t, i, b,
                        parent if pa is None else pa, parent_name if pn is None else pn)

    want("Kid inherits slot 3 from Base", walk(0x200, 3, 90), ("Base", ""))
    want("Kid's added slot 6 is Kid's", walk(0x200, 6, 77), ("Kid", ""))
    want("an override below the declarer is the child's", walk(0x400, 6, 0), ("Lone", ""))
    want("a sibling's slot is not an ancestor's", walk(0x300, 3, 77), ("Other", ""))
    want("Base's own slot is Base's", walk(0x100, 3, 90), ("Base", ""))
    want("a slot past the parent's extent is the child's own", walk(0x200, 7, 78), ("Kid", ""))
    no_name = {0x200: "Kid", 0x300: "Other", 0x400: "Lone"}
    want("an unnamed declarer names the script an edge records",
         walk(0x200, 3, 90, co=no_name), ("", "unnamed-declarer:0x00000100:Base"))
    want("an edge-less table declares nothing",
         walk(0x900, 0, 1), ("", "no-extent:0x00000900"))
    bad = [(n, g, e) for n, g, e in checks if g != e]
    if bad:
        print("FAIL class-slot-declarers: %d synthetic walk(s) wrong: %s" % (len(bad), bad))
        return 1
    print("ok   class-slot-declarers: %d synthetic walks" % len(checks))
    return 0


def self_check():
    rows = read_csv(os.path.join(ROOT, OUT))
    heads = [r for r in rows if r["row"] == "head"]
    ctl = [r for r in rows if r["row"] == "control"]
    if not heads or not ctl:
        print("FAIL class-slot-declarers: %s has no head/control rows" % OUT)
        return 1
    meta = [r for r in rows if r["row"] == "census"]
    if not meta:
        print("FAIL class-slot-declarers: %s has no census row" % OUT)
        return 1
    m = meta_of(rows)
    inv = load_inventory()
    slot_heads = [h for h in inv["heads"] if h.get("slot_table")]
    if len(heads) != len(slot_heads):
        print("FAIL class-slot-declarers: %d head rows against %d slot sit-ins in gap_heads.csv"
              % (len(heads), len(slot_heads)))
        return 1
    by_va = {h["head_va"]: h for h in slot_heads}
    bad = [r["head_va"] for r in heads if r["head_va"] not in by_va]
    if bad:
        print("FAIL class-slot-declarers: %d rows are not gap heads: %s" % (len(bad), bad[:3]))
        return 1
    v = collections.Counter(r["verdict"] for r in heads)
    total = sum(v[k] for k in ("declared", "declared-unnamed", "conflict", "member", "interface",
                               "unnamed-table"))
    if total != int(m["heads"]):
        print("FAIL class-slot-declarers: verdicts sum to %d, census says %s" % (total, m["heads"]))
        return 1
    if v["declared"] != int(m["declared"]) or v["member"] != int(m["member"]) \
            or v["declared-unnamed"] != int(m["declared_unnamed"]) \
            or v["conflict"] != int(m["conflict"]):
        print("FAIL class-slot-declarers: census verdict counts do not match the rows")
        return 1
    for r in heads:
        if r["verdict"] == "declared":
            want = "%s_vt%s" % (r["declarer"], r["slot"])
            if r["name_earned"] != want or not r["declarer"]:
                print("FAIL class-slot-declarers: %s earns %r, not %r"
                      % (r["head_va"], r["name_earned"], want))
                return 1
        if r["verdict"] == "declared-unnamed" and r["name_earned"]:
            if not r["name_earned"].startswith(r["evidence"].split("(`")[-1].split("`")[0]):
                print("FAIL class-slot-declarers: %s names an unnamed table from the wrong script"
                      % r["head_va"])
                return 1
        if r["current_agrees"] == "yes" and r["current_name"] != r["name_earned"]:
            print("FAIL class-slot-declarers: %s claims to agree but does not" % r["head_va"])
            return 1
    if int(m["current_agree"]) + int(m["current_disagree"]) > int(m["current_named"]):
        print("FAIL class-slot-declarers: the name comparison does not add up")
        return 1
    mine = {int(r["head_va"], 16) for r in rows if r["row"] == "add"}
    map_rows = [r for r in read_csv(SYMBOLS)
                if CLASS_SLOT_NAME.match(r.get("name") or "")
                and int(r["address"], 16) not in mine]
    if len(ctl) != len(map_rows) or len(ctl) != int(m["control_rows"]):
        print("FAIL class-slot-declarers: %d control rows against %d map rows"
              % (len(ctl), len(map_rows)))
        return 1
    cv = collections.Counter(r["verdict"] for r in ctl)
    if (cv["agrees"], cv["derived"], cv["unresolved"]) != (
            int(m["control_agree"]), int(m["control_derived"]), int(m["control_unresolved"])):
        print("FAIL class-slot-declarers: control verdicts do not match the census")
        return 1
    # the add rows are receipts: each one must be a map row with the name the walk gave it, must not be
    # a gap head again, and must be named after its declarer (or its member's field), never a holder
    adds = [r for r in rows if r["row"] == "add"]
    if len(adds) != int(m.get("add_rows", -1)):
        print("FAIL class-slot-declarers: %d add rows against a census of %s"
              % (len(adds), m.get("add_rows")))
        return 1
    map_by_va = {int(r["address"], 16): r.get("name") or "" for r in read_csv(SYMBOLS)}
    head_vas = {h["head_va"] for h in slot_heads}
    for r in adds:
        va, name = int(r["head_va"], 16), r["name_earned"]
        if map_by_va.get(va) != name:
            print("FAIL class-slot-declarers: the add at %s is %r in the map, not %r"
                  % (r["head_va"], map_by_va.get(va), name))
            return 1
        if r["head_va"] in head_vas:
            print("FAIL class-slot-declarers: %s is an add row and still a gap head" % r["head_va"])
            return 1
        # a class slot's name is the declarer's and nobody else's; a body no class declares may be a
        # member's method, and then any of the member labels it records may name it
        leaders = [r["declarer"]] if r["declarer"] else r["member"].split()
        if not leaders:
            print("FAIL class-slot-declarers: %s is an add with no declarer" % r["head_va"])
            return 1
        want = "%s_vt%s" % (leaders[0], r["slot"])
        ok = [("%s_vt%s" % (m, r["slot"]), "%s_vt%s_b" % (m, r["slot"])) for m in leaders]
        if r["name_earned"] not in [w for pair in ok for w in pair]:
            print("FAIL class-slot-declarers: the add at %s is named %r, not after its declarer %r"
                  % (r["head_va"], r["name_earned"], want))
            return 1
    names = [r["name_earned"] for r in adds]
    if len(names) != len(set(names)):
        print("FAIL class-slot-declarers: two add rows share a name")
        return 1
    if int(m.get("add_live", 0)) != len(adds):
        print("FAIL class-slot-declarers: %d add rows, %s live in the map"
              % (len(adds), m.get("add_live")))
        return 1
    rc = synthetic()
    if rc:
        return rc
    print("ok   class-slot-declarers: %d head rows, %d control rows, census consistent"
          % (len(heads), len(ctl)))
    return 0


# --------------------------------------------------------------------------- main
def main():
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--xbe", help="<dump>/default.xbe")
    ap.add_argument("-o", "--out", default="inventory")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--write-doc")
    ap.add_argument("--check-doc")
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--emit-adds", action="store_true",
                    help="print the `add` rows (symbols/xbe_boundaries.csv) the walk's names earn")
    ap.add_argument("--apply-adds", action="store_true",
                    help="write those rows, the creation row each needs, and the artifact receipt")
    args = ap.parse_args()
    if args.check:
        return self_check()

    path = os.path.join(ROOT, OUT)
    if args.check_doc:
        if not os.path.exists(path):
            print("FAIL class-slot-declarers: %s is missing" % OUT)
            return 1
        rows = read_csv(path)
        text = open(args.check_doc, encoding="utf-8").read()
        i, j = text.find(DOC_START), text.find(DOC_END)
        if i < 0 or j < 0:
            print("FAIL class-slot-declarers: %s has no generated block" % args.check_doc)
            return 1
        block = render([r for r in rows if r["row"] == "head"],
                       [r for r in rows if r["row"] == "control"], meta_of(rows))
        if text[i + len(DOC_START):j].strip() != block.strip():
            print("FAIL class-slot-declarers: %s is stale; re-run --write-doc" % args.check_doc)
            return 1
        print("ok   class-slot-declarers: the page block matches the committed artifact")
        return 0

    if args.report or args.write_doc:
        if not os.path.exists(path):
            print("FAIL class-slot-declarers: %s is missing" % OUT)
            return 1
        rows = read_csv(path)
        m = meta_of(rows)
        block = render([r for r in rows if r["row"] == "head"],
                       [r for r in rows if r["row"] == "control"], m)
        if args.report and not args.write_doc:
            print(block)
            return 0
        text = open(args.write_doc, encoding="utf-8").read()
        i, j = text.find(DOC_START), text.find(DOC_END)
        if i < 0 or j < 0:
            text = text.rstrip("\n") + "\n\n" + DOC_START + "\n" + block + DOC_END + "\n"
        else:
            text = text[:i] + DOC_START + "\n" + block + text[j:]
        with open(args.write_doc, "w", encoding="utf-8") as f:
            f.write(text)
        print("wrote %s" % args.write_doc)
        return 0

    if not args.xbe:
        ap.error("--xbe is required unless --report/--check/--write-doc/--check-doc is given")
    from xbe import Xbe  # noqa: E402

    xbe = Xbe(args.xbe)
    cache = {}

    def slot_of(table, n):
        if table not in cache:
            cache[table] = list(struct.unpack("<%dI" % n, xbe.read_va(table, 4 * n)))
        return cache[table]

    inv = load_inventory()
    head_rows, holds, sits = census_heads(inv, slot_of)
    if args.emit_adds or args.apply_adds:
        cand = add_candidates(head_rows, inv["gaps"])
        if args.emit_adds:
            w = csv.writer(sys.stdout, lineterminator="\n")
            for r, name, h in cand:
                w.writerow([r["head_va"], "add", name, r["size"], add_evidence(r, h, name)])
            print("class-slot-declarers: %d add(s) for %d named bodies (%d undecodable)"
                  % (len(cand), sum(1 for r in head_rows if r["name_earned"]),
                     sum(1 for r in head_rows if r["name_earned"]) - len(cand)), file=sys.stderr)
            return 0
        append_adds(cand)
        return 0
    ctl_rows = census_control(inv, slot_of, holds)
    adds = carried_adds()
    meta = census_meta(head_rows, ctl_rows, inv, holds, sits, adds)
    os.makedirs(os.path.join(ROOT, args.out), exist_ok=True)
    out = os.path.join(ROOT, args.out, "class_slot_declarers.csv")
    with open(out, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, FIELDS, lineterminator="\r\n")
        w.writeheader()
        for r in head_rows + ctl_rows + adds:
            w.writerow(r)
        w.writerow({"row": "census", "census": ";".join("%s=%s" % kv for kv in meta.items())})
    print("wrote %s (%d head rows, %d control rows, %d add rows)"
          % (out, len(head_rows), len(ctl_rows), len(adds)))
    print("  declared %d, conflict %d, member %d, unnamed-table %d, interface %d in %d sit-ins"
          % (meta["declared"], meta["conflict"], meta["member"], meta["unnamed_table"],
             meta["interface"], meta["sit_ins"]))
    print("  control: %d agree, %d derived, %d unresolved; gap census names: %d agree, %d not"
          % (meta["control_agree"], meta["control_derived"], meta["control_unresolved"],
             meta["current_agree"], meta["current_disagree"]))
    return 0


if __name__ == "__main__":
    sys.exit(main())
