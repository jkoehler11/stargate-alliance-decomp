#!/usr/bin/env python3
"""One read per interface family: what the per-body read owes, and where the repeats actually sit.

`inventory/class_interface_shapes.csv` joins the 232 interface tables into families and leaves the
naming question open with a bound: *100 of the 165 anonymous tables sit in a family with another table,
so one representative per family is what a per-body read would owe*. This tool turns that bound into a
census -- one row per family with the representative the read would start from, the channels that already
name it (a slot's masked symbol, a member field, an installer's allocation, a caller) and whether the
family needs that read at all.

The other question the shape census raised is what an **exact repeat** is. It counted 27 tables sharing
another's vector and read the repeat as a per-translation-unit copy. Grouped by shape with each member's
role and owner, the repeats answer differently: a group's members are almost all *different* classes --
sibling classes that each embed the same one- or two-slot interface at one fixed member offset -- and
they sit next to each other in `.rdata`, which is one translation unit emitting the interface once per
class rather than one vtable emitted twice.

    tools/class-interface-families.py -o inventory
    tools/class-interface-families.py --report
    tools/class-interface-families.py --write-doc docs/systems/CLASS_REGISTRATION.md
    tools/class-interface-families.py --check-doc docs/systems/CLASS_REGISTRATION.md
    tools/class-interface-families.py --check        # synthetic rows, no dump, writes nothing

Committed CSVs in, two CSVs and two page blocks out: no dump, no image read.
"""
import argparse
import collections
import csv
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

SHAPES = os.path.join(ROOT, "inventory", "class_interface_shapes.csv")
CALLERS = os.path.join(ROOT, "inventory", "class_installer_callers.csv")
EVID = os.path.join(ROOT, "inventory", "class_ctor_evidence.csv")
FAM_OUT = os.path.join(ROOT, "inventory", "class_interface_families.csv")
TWIN_OUT = os.path.join(ROOT, "inventory", "class_interface_twins.csv")
FAM_START = "<!-- generated:class-interface-families -->"
FAM_END = "<!-- /generated:class-interface-families -->"
TWIN_START = "<!-- generated:class-interface-twins -->"
TWIN_END = "<!-- /generated:class-interface-twins -->"
FAM_FIELDS = ["family", "tables", "slots", "named", "member", "anonymous", "rep_va", "rep_role",
              "rep_name", "rep_slots", "rep_size", "rep_installers", "rep_callers", "rep_literals",
              "read_owed", "verdict", "evidence"]
TWIN_FIELDS = ["shape", "tables", "slots", "roles", "owners", "delta", "placement", "members", "verdict",
               "evidence"]
NEAR = 0x1000          # one 4 KiB page: the same .rdata neighbourhood


def read(path):
    if not os.path.isfile(path):
        return []
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def by_table(path, *columns):
    out = {}
    for row in read(path):
        out[row.get("table_va", "")] = {c: row.get(c, "") for c in columns}
    return out


def families(rows, callers, evidence):
    groups = collections.defaultdict(list)
    for row in rows:
        groups[row["family"]].append(row)
    out = []
    for fam, members in sorted(groups.items()):
        roles = collections.Counter(m["role"] for m in members)
        named = [m for m in members if m["role"] == "named"]
        member = [m for m in members if m["role"] == "member"]
        anon = [m for m in members if m["role"] == "anonymous"]
        if named:
            rep = sorted(named, key=lambda m: int(m["table_va"], 16))[0]
            verdict = "named"
        elif member:
            rep = sorted(member, key=lambda m: int(m["table_va"], 16))[0]
            verdict = "member-held"
        else:
            rep = sorted(anon, key=lambda m: (-int(m["slots"]), int(m["table_va"], 16)))[0]
            verdict = "anonymous"
        va = rep["table_va"]
        call = callers.get(va, {})
        ev = evidence.get(va, {})
        row = {
            "family": fam, "tables": len(members), "slots": rep["slots"],
            "named": roles["named"], "member": roles["member"], "anonymous": roles["anonymous"],
            "rep_va": va, "rep_role": rep["role"], "rep_name": rep.get("name", ""),
            "rep_slots": rep["slots"], "rep_size": rep.get("object_size", ""),
            "rep_installers": call.get("installers", ""),
            "rep_callers": (call.get("caller_classes") or call.get("callers") or "")[:120],
            "rep_literals": (ev.get("literals", "") or ev.get("named_calls", ""))[:120],
            "read_owed": "no" if verdict in ("named", "member-held") else "yes",
            "verdict": verdict,
            "evidence": "%d table(s) of %s slot(s); rep %s %s%s"
                        % (len(members), rep["slots"], va, verdict,
                           "" if verdict != "named" else " (%s)" % rep.get("name", "")),
        }
        out.append(row)
    return out


def render_families(fams):
    owed = [f for f in fams if f["read_owed"] == "yes"]
    roles = collections.Counter(f["verdict"] for f in fams)
    covered = sum(int(f["tables"]) for f in owed)
    anonymous = sum(int(f["anonymous"]) for f in fams)
    owed_anon = sum(int(f["anonymous"]) for f in owed)
    lines = ["| representative role | families | tables held | per-body read owed |", "|---|---|---|---|"]
    for role in ("named", "member-held", "anonymous"):
        group = [f for f in fams if f["verdict"] == role]
        if not group:
            continue
        lines.append("| %s | %d | %d | %s |"
                     % (role, len(group), sum(int(f["tables"]) for f in group),
                        "yes" if role == "anonymous" else "no"))
    lines.append("")
    lines.append("**%d families, and %d of them have no named and no member-held table: the per-body read "
                 "is %d representative reads over %d tables, not one read per anonymous table.** The "
                 "other %d families already carry a name -- %d from a slot's masked class scope, %d from "
                 "the member field that holds them -- and owe no read at all."
                 % (len(fams), len(owed), len(owed), covered, len(fams) - len(owed), roles["named"],
                    roles["member-held"]))
    lines.append("")
    lines.append("**%d of the %d anonymous tables sit in a family the census would read once**, and the "
                 "remaining %d are members of the %d anonymous-only families: the family is a unit of "
                 "work, not a name, and one representative per unpaid family is the whole residue."
                 % (anonymous - owed_anon, anonymous, owed_anon, len(owed)))
    return lines


def labels(member):
    """The classes a table belongs to, as the artifacts name them: a slot symbol, or a member field."""
    if member.get("role") == "named" and member.get("name"):
        return [member["name"]]
    return [x for x in (member.get("owners") or "").split(";") if x]


def twins(rows):
    groups = collections.defaultdict(list)
    for row in rows:
        groups[row["shape"]].append(row)
    out = []
    for shape, members in groups.items():
        if len(members) < 2:
            continue
        members.sort(key=lambda m: int(m["table_va"], 16))
        addrs = [int(m["table_va"], 16) for m in members]
        delta = max(addrs) - min(addrs)
        owned = [labels(m) for m in members]
        distinct = sorted({label for group in owned for label in group})
        roles = collections.Counter(m["role"] for m in members)
        if len(distinct) >= 2:
            verdict = "per-class"
        elif distinct:
            verdict = "one-owner"
        else:
            verdict = "unowned"
        placement = "same-page" if delta <= NEAR else "apart"
        out.append({
            "shape": shape, "tables": len(members), "slots": members[0]["slots"],
            "roles": " ".join("%s %d" % (k, v) for k, v in sorted(roles.items())),
            "owners": ",".join(distinct)[:120], "delta": "0x%x" % delta, "placement": placement,
            "members": " ".join(m["table_va"] for m in members),
            "verdict": verdict,
            "evidence": "%d table(s) of %s slot(s), 0x%x apart, %s; %d of %d owned by %s"
                        % (len(members), members[0]["slots"], delta, placement,
                           sum(1 for group in owned if group), len(members),
                           ",".join(distinct) or "no class"),
        })
    out.sort(key=lambda r: (-int(r["tables"]), int(r["members"].split()[0], 16)))
    return out


def render_twins(rows):
    per = [r for r in rows if r["verdict"] == "per-class"]
    one = [r for r in rows if r["verdict"] == "one-owner"]
    none = [r for r in rows if r["verdict"] == "unowned"]
    near = [r for r in rows if r["placement"] == "same-page"]
    tables = sum(int(r["tables"]) for r in rows)
    widest = max(rows, key=lambda r: int(r["delta"], 16)) if rows else None
    lines = ["| shape | tables | slots | members | span | roles | classes |", "|---|---|---|---|---|---|---|"]
    for r in rows:
        lines.append("| `%s` | %d | %s | %s | %s | %s | %s |"
                     % (r["shape"], int(r["tables"]), r["slots"], r["members"], r["delta"],
                        r["roles"], r["owners"] or "-"))
    lines.append("")
    lines.append("**%d shapes repeat over %d tables, and the repeat is not a copy of one class's table: "
                 "%d groups are **per-class** (%d tables, %d distinct owner classes), %d hold one class's "
                 "table twice (%d tables), and %d groups (%d tables) carry no class at all.**"
                 % (len(rows), tables, len(per), sum(int(r["tables"]) for r in per),
                    len({c for r in per for c in r["owners"].split(",") if c}),
                    len(one), sum(int(r["tables"]) for r in one),
                    len(none), sum(int(r["tables"]) for r in none)))
    lines.append("")
    lines.append("**%d of the %d groups sit inside one 4 KiB page and the widest spans `%s`**, so a repeat is "
                 "emitted next to the classes that hold it rather than collected in one place: the two "
                 "4-member groups are one interface at `+0x2c` and one at `+0x28` held by "
                 "`PackageMapLevel`, `TcpipConnection`, `TcpipConnectionXbox` and `DemoRecConnection`, one "
                 "table per class, and the pair is repeated in each."
                 % (len(near), len(rows), widest["delta"] if widest else "0x0"))
    return lines


def write_csv(path, rows, fields):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


def self_check():
    fails = []

    def want(label, cond):
        if not cond:
            fails.append(label)

    def shape(va, role, fam, slots="2", owners="", name="", shape_id=None):
        return {"table_va": va, "role": role, "family": fam, "slots": slots, "owners": owners,
                "name": name, "object_size": "", "shape": shape_id or ("s-" + va)}

    rows = [shape("0x1000", "named", "0x1000", owners="CFeature", name="CFeature"),
            shape("0x2000", "anonymous", "0x1000"),
            shape("0x3000", "member", "0x3000", owners="Level"),
            shape("0x4000", "anonymous", "0x4000"),
            shape("0x4100", "anonymous", "0x4000")]
    fams = families(rows, {}, {})
    want("a named family owes no read",
         [f for f in fams if f["family"] == "0x1000"][0]["read_owed"] == "no")
    want("a member-held family owes no read",
         [f for f in fams if f["family"] == "0x3000"][0]["verdict"] == "member-held")
    anon = [f for f in fams if f["family"] == "0x4000"][0]
    want("an anonymous family owes one read", anon["read_owed"] == "yes" and anon["tables"] == 2)
    block = "\n".join(render_families(fams))
    want("the block counts the owed reads once, not per table",
         "1 representative reads over 2 tables" in block)
    twins_rows = twins([shape("0x5000", "member", "0x5000", owners="Level", shape_id="tiny"),
                        shape("0x50b8", "member", "0x5000", owners="NetPendingLevel", shape_id="tiny"),
                        shape("0x9000", "anonymous", "0x9000", shape_id="other")])
    want("one group for a repeated shape, none for a singleton", len(twins_rows) == 1)
    want("a two-owner group is per-class", twins_rows[0]["verdict"] == "per-class")
    want("its placement is the page test", twins_rows[0]["placement"] == "same-page")
    want("its delta is the address span", twins_rows[0]["delta"] == "0xb8")
    if fails:
        print("FAIL class-interface-families: %s" % "; ".join(fails))
        return 1
    print("ok   class-interface-families: 8 assertions")
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("-o", "--out", help="artifact directory")
    ap.add_argument("--report", action="store_true")
    ap.add_argument("--write-doc", metavar="PAGE")
    ap.add_argument("--check-doc", metavar="PAGE")
    ap.add_argument("--check", action="store_true", help="synthetic rows, no write")
    args = ap.parse_args()
    if args.check:
        return self_check()
    if args.check_doc or args.write_doc:
        fams, twin_rows = read(FAM_OUT), read(TWIN_OUT)
        if not fams or not twin_rows:
            raise SystemExit("missing artifact -- regenerate with -o inventory")
        page = args.check_doc or args.write_doc
        text = open(page, encoding="utf-8").read()
        new = text
        for start, end, lines in ((FAM_START, FAM_END, render_families(fams)),
                                  (TWIN_START, TWIN_END, render_twins(twin_rows))):
            i, j = new.find(start), new.find(end)
            if i < 0 or j < 0:
                raise SystemExit("%s: no %s block" % (page, start))
            new = new[:i + len(start)] + "\n" + "\n".join(lines) + "\n" + new[j:]
        if args.check_doc:
            if new != text:
                raise SystemExit("%s: an interface block is stale" % page)
            print("ok   %s: interface families and twins blocks reproduce" % page)
            return 0
        with open(page, "w", encoding="utf-8", newline="") as fh:
            fh.write(new)
        print("wrote %s" % page)
        return 0
    fams = families(read(SHAPES), by_table(CALLERS, "installers", "callers", "caller_classes"),
                    by_table(EVID, "literals", "named_calls"))
    twin_rows = twins(read(SHAPES))
    print("families %d, owed %d; twin groups %d over %d tables"
          % (len(fams), sum(1 for f in fams if f["read_owed"] == "yes"), len(twin_rows),
             sum(int(r["tables"]) for r in twin_rows)))
    if args.out:
        write_csv(os.path.join(args.out, "class_interface_families.csv"), fams, FAM_FIELDS)
        write_csv(os.path.join(args.out, "class_interface_twins.csv"), twin_rows, TWIN_FIELDS)
        print("wrote %s and %s" % (FAM_OUT, TWIN_OUT))
    if args.report or not args.out:
        print("\n".join(render_families(fams)))
        print()
        print("\n".join(render_twins(twin_rows)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
