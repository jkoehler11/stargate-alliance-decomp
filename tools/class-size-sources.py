#!/usr/bin/env python3
"""Where a class's object size is measured, and how many of the committed columns are that one read.

`object_size` appears in four committed artifacts and reads like four measurements: the registration
census (`inventory/class_registrations.csv`), the naming census (`inventory/xbe_class_table_names.csv`),
the constructor census (`inventory/class_constructors.csv`) and the base-call census
(`inventory/class_ctor_bases.csv`). This tool joins them on the table and asks a blunt question -- does
each column carry the value the registration site pushes? -- because the answer decides whether the
proposed three-way size join (`PLAN.md`: "the class's own size is measured independently by
`tools/class-ctors.py` and by the record census") can be made at all.

Measured: it cannot, and the reason is visible in the tools, not in the image.

  * the registration census reads the site's **second-to-last push**, the argument position;
  * the naming census reads its chosen site's **last immediate push** (falling back one when that is a
    literal zero), which is the same value wherever the outer pointer arrives in a register;
  * `tools/class-ctors.py` and `tools/class-ctor-base.py` copy `object_size` off the table row they are
    joining (`trow["object_size"]`), so their columns are the naming census's, not a second reading;
  * `inventory/class_records.csv` carries `record_bytes`, the class *script* record's length, and no
    object size at all -- the two are not comparable (it is smaller than the object size on 266 of the
    333 classes that have both).

So the object size has exactly one source in this tree, and a disagreement between two of the columns can
only be an attribution defect upstream (which is what the 40-vs-56 on the object root was: the first-store
rule attaching `VertexStreamBase`'s site to the root's table). A second, independent measurement would have
to come from outside these artifacts.

    tools/class-size-sources.py -o inventory
    tools/class-size-sources.py --write-doc docs/systems/CLASS_REGISTRATION.md
    tools/class-size-sources.py --check-doc docs/systems/CLASS_REGISTRATION.md   # from the committed CSVs
    tools/class-size-sources.py --check        # synthetic CSVs, no dump, writes nothing

Addresses and counts only; no package or image content is written.
"""
import argparse
import csv
import os
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FIELDS = ["table_va", "class", "site_va", "site_size", "naming_size", "site_agrees_naming",
          "ctor_rows", "ctor_agrees", "base_rows", "base_agrees", "record_bytes", "verdict"]
ARTIFACT = os.path.join(ROOT, "inventory/class_size_sources.csv")
DOC = os.path.join(ROOT, "docs/systems/CLASS_REGISTRATION.md")
DOC_START = "<!-- generated:class-size-sources -->"
DOC_END = "<!-- /generated:class-size-sources -->"


def read_csv(path):
    if not path or not os.path.isfile(path):
        return []
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def harvest(reg, names, ctors, bases, records):
    """One row per class table: the site's push against every column that claims to carry it."""
    site_of = {r["site_va"]: r for r in reg}
    ctor_by_table, base_by_table = {}, {}
    for r in ctors:
        ctor_by_table.setdefault(r["table_va"], []).append(r["object_size"])
    for r in bases:
        base_by_table.setdefault(r["table_va"], []).append(r["object_size"])
    record_of = {}
    for r in records:
        if r.get("record_bytes") and r["class"] not in record_of:
            record_of[r["class"]] = r["record_bytes"]

    rows = []
    for r in names:
        table = r["table_va"]
        site = site_of.get(r.get("registration_va") or "", None)
        size = (site or {}).get("object_size") or ""
        naming = r.get("object_size") or ""
        ct = [s for s in ctor_by_table.get(table, []) if s]
        cb = [s for s in base_by_table.get(table, []) if s]
        ctor_ok = "".join("yes" if s == size else "no" for s in ct) or ""
        base_ok = "".join("yes" if s == size else "no" for s in cb) or ""
        agrees = ("" if not (size and naming) else "yes" if size == naming else "no")
        if not size:
            verdict = "no-registration"
        elif agrees == "yes" and "no" not in ctor_ok and "no" not in base_ok:
            verdict = "one-read"
        else:
            verdict = "disagrees"
        rows.append({
            "table_va": table,
            "class": r.get("class") or "",
            "site_va": r.get("registration_va") or "",
            "site_size": size,
            "naming_size": naming,
            "site_agrees_naming": agrees,
            "ctor_rows": str(len(ct)),
            "ctor_agrees": ctor_ok,
            "base_rows": str(len(cb)),
            "base_agrees": base_ok,
            "record_bytes": record_of.get(r.get("class") or "", ""),
            "verdict": verdict,
        })
    return rows


def render(rows):
    with_site = [r for r in rows if r["site_size"]]
    no_site = [r for r in rows if not r["site_size"]]
    naming_ok = [r for r in with_site if r["site_agrees_naming"] == "yes"]
    naming_bad = [r for r in with_site if r["site_agrees_naming"] == "no"]
    ctor_rows = sum(int(r["ctor_rows"]) for r in with_site)
    ctor_ok = sum(r["ctor_agrees"].count("yes") for r in with_site)
    base_rows = sum(int(r["base_rows"]) for r in with_site)
    base_ok = sum(r["base_agrees"].count("yes") for r in with_site)
    joined = [r for r in with_site if r["record_bytes"]]
    smaller = [r for r in joined if int(r["record_bytes"]) < int(r["site_size"])]
    sizes = sorted(int(r["site_size"]) for r in with_site)

    lines = ["| source | what the value is | rows that carry it | equals the site's push |",
             "|---|---|---|---|"]
    lines.append("| `inventory/class_registrations.csv` | the site's second-to-last push (the argument "
                 "position) | %d sites | -- this is the read |" % len(with_site))
    lines.append("| `inventory/xbe_class_table_names.csv` | its chosen site's last immediate push | %d "
                 "tables | %d of %d%s |"
                 % (len(with_site), len(naming_ok), len(with_site),
                    "" if not naming_bad else " (**%d differ**)" % len(naming_bad)))
    lines.append("| `inventory/class_constructors.csv` | copied from the table row | %d rows | %d of %d |"
                 % (ctor_rows, ctor_ok, ctor_rows))
    lines.append("| `inventory/class_ctor_bases.csv` | copied from the constructor row | %d rows | %d of %d |"
                 % (base_rows, base_ok, base_rows))
    lines.append("| `inventory/class_records.csv` | the class **script** record's byte length | %d of %d "
                 "classes | not an object size (smaller than it on %d of %d) |"
                 % (len(joined), len(with_site), len(smaller), len(joined)))
    lines.append("")
    lines.append("**The object size has one source, and three of those columns are copies of it.** "
                 "%d of the %d tables have a registration site; their sizes are **%d distinct values, %d to "
                 "%d bytes**, and the naming census carries the same value on all of them%s. The 2 "
                 "constructor-side artifacts copy the column rather than measuring it, so a disagreement "
                 "between two of the four columns can only come from the site-to-table attribution "
                 "upstream -- which is what the object root's 40 against the old reader's 56 was."
                 % (len(with_site), len(rows), len(set(sizes)), sizes[0] if sizes else 0,
                    sizes[-1] if sizes else 0,
                    "" if not naming_bad else " apart from %d row(s)" % len(naming_bad)))
    lines.append("")
    lines.append("%d of the %d tables have **no size at all** -- no registration site resolves to them, so "
                 "nothing in this tree measures them." % (len(no_site), len(rows)))
    return "\n".join(lines)


def write_artifact(rows, path):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=FIELDS, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


def read_artifact(path):
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def splice_doc(path, block):
    text = open(path, encoding="utf-8").read()
    i, j = text.find(DOC_START), text.find(DOC_END)
    if i < 0 or j < 0:
        return None
    return text[:i] + DOC_START + "\n" + block + "\n" + text[j:]


# --------------------------------------------------------------------------- self-test

def write_rows(path, fields, rows):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=fields, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


def self_check():
    bad = []
    tmp = tempfile.mkdtemp()
    reg = os.path.join(tmp, "reg.csv")
    names = os.path.join(tmp, "names.csv")
    ctors = os.path.join(tmp, "ctors.csv")
    bases = os.path.join(tmp, "bases.csv")
    records = os.path.join(tmp, "records.csv")
    write_rows(reg, ["site_va", "class_literal", "object_size", "table_va"],
               [{"site_va": "0x00001000", "class_literal": "A", "object_size": "40", "table_va": "0x00009000"},
                {"site_va": "0x00002000", "class_literal": "B", "object_size": "64", "table_va": "0x00009100"}])
    names2 = os.path.join(tmp, "names2.csv")
    write_rows(names2, ["table_va", "class", "object_size", "registration_va"],
               [{"table_va": "0x00009000", "class": "A", "object_size": "40", "registration_va": "0x00001000"},
                {"table_va": "0x00009100", "class": "B", "object_size": "64", "registration_va": "0x00002000"},
                {"table_va": "0x00009200", "class": "C", "object_size": "", "registration_va": ""}])
    names = names2
    write_rows(ctors, ["table_va", "object_size"],
               [{"table_va": "0x00009000", "object_size": "40"},
                {"table_va": "0x00009000", "object_size": "40"}])
    write_rows(bases, ["table_va", "object_size"], [{"table_va": "0x00009100", "object_size": "64"}])
    write_rows(records, ["class", "record_bytes"], [{"class": "A", "record_bytes": "8"}])

    rows = harvest(read_csv(reg), read_csv(names), read_csv(ctors), read_csv(bases), read_csv(records))
    if len(rows) != 3:
        bad.append("a row per table: %r" % (len(rows),))
    by_table = {r["table_va"]: r for r in rows}
    if by_table["0x00009000"]["verdict"] != "one-read":
        bad.append("a copied column that agrees is one read: %r" % (by_table["0x00009000"],))
    if by_table["0x00009200"]["verdict"] != "no-registration":
        bad.append("a table with no site has no size: %r" % (by_table["0x00009200"],))
    if by_table["0x00009000"]["ctor_rows"] != "2" or by_table["0x00009000"]["ctor_agrees"] != "yesyes":
        bad.append("the constructor rows are counted per table: %r" % (by_table["0x00009000"],))
    if by_table["0x00009100"]["base_agrees"] != "yes":
        bad.append("the base-call rows join on the table: %r" % (by_table["0x00009100"],))

    # a naming column that disagrees must be reported, not averaged away
    names3 = os.path.join(tmp, "names3.csv")
    write_rows(names3, ["table_va", "class", "object_size", "registration_va"],
               [{"table_va": "0x00009000", "class": "A", "object_size": "56", "registration_va": "0x00001000"}])
    rows = harvest(read_csv(reg), read_csv(names3), read_csv(ctors), read_csv(bases), read_csv(records))
    if rows[0]["verdict"] != "disagrees":
        bad.append("a disagreeing column is not hidden: %r" % (rows[0],))

    out = render(harvest(read_csv(reg), read_csv(names2), read_csv(ctors), read_csv(bases), read_csv(records)))
    for want in ("| `inventory/class_constructors.csv` | copied from the table row | 2 rows | 2 of 2 |",
                 "1 of the 3 tables have **no size at all**",
                 "| `inventory/class_records.csv` | the class **script** record's byte length | 1 of 2 "
                 "classes | not an object size (smaller than it on 1 of 1) |"):
        if want not in out:
            bad.append("render lacks %r: %r" % (want, out))

    path = os.path.join(tmp, "a.csv")
    write_artifact(rows, path)
    if read_artifact(path) != rows:
        bad.append("artifact round trip differs")
    if bad:
        print("FAIL class-size-sources: " + "; ".join(bad))
        return 1
    print("ok   class-size-sources: the four size columns are joined per table, and a disagreeing one is "
          "reported")
    return 0


def main():
    if "--check" in sys.argv[1:]:
        return self_check()
    ap = argparse.ArgumentParser()
    ap.add_argument("-o", "--out", default=None)
    ap.add_argument("--write-doc", nargs="?", const=DOC, default=None)
    ap.add_argument("--check-doc", nargs="?", const=DOC, default=None)
    args = ap.parse_args()

    inv = os.path.join(ROOT, "inventory")
    if args.write_doc or args.check_doc:
        rows = read_artifact(ARTIFACT)
    else:
        rows = harvest(read_csv(os.path.join(inv, "class_registrations.csv")),
                       read_csv(os.path.join(inv, "xbe_class_table_names.csv")),
                       read_csv(os.path.join(inv, "class_constructors.csv")),
                       read_csv(os.path.join(inv, "class_ctor_bases.csv")),
                       read_csv(os.path.join(inv, "class_records.csv")))
        if args.out:
            path = os.path.join(args.out, "class_size_sources.csv")
            write_artifact(rows, path)
            print("%s: %d rows" % (path, len(rows)))
    print(render(rows))
    if args.write_doc or args.check_doc:
        path = args.write_doc or args.check_doc
        new = splice_doc(path, render(rows))
        if new is None:
            print("FAIL class-size-sources: markers not found in %s" % path)
            return 1
        old = open(path, encoding="utf-8").read()
        if args.check_doc:
            if new != old:
                print("FAIL class-size-sources: %s is stale against the committed artifact" % path)
                return 1
            print("ok   class-size-sources: %s matches the committed artifact" % path)
            return 0
        if new != old:
            with open(path, "w", encoding="utf-8") as fh:
                fh.write(new)
            print("wrote %s" % path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
