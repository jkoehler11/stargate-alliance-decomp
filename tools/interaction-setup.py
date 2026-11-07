#!/usr/bin/env python3
"""Every `Interaction` the game installs on the player, and the routine that installs it (U15/7.3).

`docs/UNKNOWNS.md` U15 left one question after the field walk: the player's `GameHudPtr` and
`NotificationManager` are `none`, so the two message channels had no sink -- but *where a `GameHud` is supposed to
come from* was unanswered, and the plan's next measurement (load a checkpoint, walk the player again) assumed the
game's profile gates it.

It does not. The packages carry their own `ScriptText`, so the installation sites are readable today without a
boot:

    python3 tools/pkg-script-text.py --all --write $WORK/UnrealScript   # extract first
    python3 tools/interaction-setup.py --src $WORK/UnrealScript -o inventory

The artifact is `inventory/interaction_setup.csv`: one row per `AddInteraction` call site, with the field it lands
in, the interaction class it names, and the routine(s) that call the routine containing it. `callers` names a call
site as `Package/Class.Routine`; `-` means no call site in the corpus (an engine callback, a `SetTimer`-armed
`Timer`, or a `static` reached through a class reference). It answers "what installs what, from where" for the
script side and nothing else: the native `UInteractionMaster::AddInteraction` is a separate question.
"""
import argparse
import csv
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
COLUMNS = ["package", "class", "routine", "line", "field", "interaction", "callers"]

CLASS_RE = re.compile(r"^\s*class\s+(\w+)", re.I)
ROUTINE_RE = re.compile(r"^\s*(?:simulated\s+|final\s+|static\s+|native\s+|exec\s+|latent\s+)*"
                        r"(?:function|event|state)\s+(\w+)", re.I)
DECLARE_RE = re.compile(r"\bnative\b.*\b(?:function|event)\b", re.I)
CALL_RE = re.compile(r"AddInteraction\s*\(\s*\"([^\"]+)\"")
PLAIN_RE = re.compile(r"AddInteraction\s*\(")
DECL_ADD_RE = re.compile(r"^\s*(?:(?:simulated|final|static|native|exec|latent)\s+)*"
                         r"(?:function|event)\b.*\bAddInteraction\s*\(", re.I)
NAME_RE = re.compile(r"\b(\w+)\s*\(")
ASSIGN_RE = re.compile(r"([\w\.\[\]]+)\s*=\s*[^=]")


def walk(src):
    """(package, class, routine, line, code) for every line of the corpus."""
    for dirpath, _dirs, names in os.walk(src):
        for name in sorted(names):
            if not name.endswith(".uc"):
                continue
            path = os.path.join(dirpath, name)
            package = os.path.relpath(dirpath, src).replace(os.sep, "/") or "."
            cls, routine = "", ""
            with open(path, encoding="utf-8", errors="replace") as fh:
                for number, raw in enumerate(fh, 1):
                    code = raw.split("//")[0]
                    m = CLASS_RE.match(code)
                    if m:
                        cls = m.group(1)
                    m = ROUTINE_RE.match(code)
                    if m and not DECLARE_RE.match(code):
                        routine = m.group(1)      # a declaration names a function, it is not inside one
                    yield package, cls, routine, number, code


def field_of(code):
    """The variable an AddInteraction result is assigned to, or `-`."""
    head = code.split("AddInteraction")[0]
    m = ASSIGN_RE.search(head)
    if not m:
        return "-"
    name = m.group(1).rstrip(".[]")
    return name.split(".")[-1] or "-"


def build(src):
    lines = list(walk(src))
    sites = [row for row in lines if PLAIN_RE.search(row[4]) and not DECL_ADD_RE.match(row[4])]
    callers = {}
    for package, cls, routine, _number, code in lines:
        m = ROUTINE_RE.match(code)
        declared = m.group(1) if m else ""     # a declaration names a function, it does not call one
        for name in set(NAME_RE.findall(code)):
            if name == routine or name == declared:
                continue
            callers.setdefault(name, []).append((package, cls, routine))

    rows = []
    for package, cls, routine, number, code in sites:
        m = CALL_RE.search(code)
        found = []
        seen = set()
        frontier = [routine]
        for _depth in range(2):
            nxt, labels = [], []
            for wanted in frontier:
                for package2, cls2, routine2 in callers.get(wanted, []):
                    label = "%s/%s.%s" % (package2, cls2, routine2)
                    if label in seen or routine2 == routine:
                        continue
                    seen.add(label)
                    labels.append(label)
                    nxt.append(routine2)
            if not labels:
                break
            found.append(",".join(labels[:3]))
            frontier = nxt[:3]
        rows.append({"package": package, "class": cls, "routine": routine, "line": number,
                     "field": field_of(code), "interaction": m.group(1) if m else "-",
                     "callers": " <- ".join(found) if found else "-"})
    return sorted(rows, key=lambda r: (r["interaction"], r["package"], r["class"], int(r["line"])))


def check(path):
    rows = list(csv.DictReader(open(path, encoding="utf-8")))
    problems = []
    if not rows:
        problems.append("no rows")
    if rows and sorted(rows[0]) != sorted(COLUMNS):
        problems.append("columns")
    for r in rows:
        if r["interaction"] == "-":
            problems.append("%s.%s: no interaction class" % (r["class"], r["routine"]))
        if not r["field"]:
            problems.append("%s.%s: no field" % (r["class"], r["routine"]))
    if not any("GameHud" in r["interaction"] for r in rows):
        problems.append("nothing installs GameHud -- the scan is not seeing the surface")
    if not any(r["callers"] != "-" for r in rows):
        problems.append("no call site resolved a caller -- the chain is not being walked")
    if problems:
        print("FAIL interaction-setup: " + "; ".join(problems[:5]))
        return 1
    hud = sorted({r["field"] for r in rows if "GameHud" in r["interaction"]})
    print("ok   interaction-setup: %d call sites, %d interaction classes, GameHud lands in %s"
          % (len(rows), len({r["interaction"] for r in rows}), ", ".join(hud)))
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--src", help="a tree of extracted .uc files (from tools/pkg-script-text.py)")
    ap.add_argument("-o", "--out", default=os.path.join(ROOT, "inventory"))
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    path = os.path.join(args.out, "interaction_setup.csv")
    if args.check:
        return check(path)
    if not args.src:
        ap.error("--src is required unless --check")
    rows = build(args.src)
    os.makedirs(args.out, exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    for r in rows:
        print("%-22s %-24s %-26s %-30s %s"
              % (r["interaction"], "%s.%s" % (r["class"], r["routine"]), r["field"], r["callers"], r["package"]))
    print("%d rows -> %s" % (len(rows), path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
