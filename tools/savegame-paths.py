#!/usr/bin/env python3
"""The save/load surface, read out of the scripts the packages ship (U5).

`docs/UNKNOWNS.md` U5 says the checkpoint container is settled but asked which script path calls `SavePackage`, in what
order, and what makes an actor eligible — and it expected PLAN 3.10's reconstruction to answer that. It does not need
reconstruction: the packages carry their own `ScriptText`, so the call sites are readable today.

    python3 tools/pkg-script-text.py StargateGame.u --all --write $WORK/UnrealScript   # extract first
    python3 tools/savegame-paths.py --src $WORK/UnrealScript -o inventory             # then measure

The artifact is `inventory/savegame_paths.csv`: one row per declaration, call and console command touching the save
surface, with the class and routine that contains it. It answers "which" and "in what order" for the script side and
nothing else — the native bodies behind `SavePackage`/`LoadSaveGame` are a separate question.
"""
import argparse
import csv
import os
import re
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SYMBOLS = ("SavePackage", "LoadSaveGame", "LoadSaveGameInfo", "MarkForSaveGame")
COLUMNS = ["package", "class", "routine", "line", "role", "symbol", "text"]

CLASS_RE = re.compile(r"^\s*class\s+(\w+)", re.I)
ROUTINE_RE = re.compile(r"^\s*(?:simulated\s+|final\s+|static\s+|native\s+|exec\s+|latent\s+)*"
                        r"(?:function|event|state)\s+(\w+)", re.I)
DECLARE_RE = re.compile(r"\bnative\b.*\b(?:function|event)\b", re.I)


def rows_for(path, package):
    out = []
    cls, routine = "", ""
    with open(path, encoding="utf-8", errors="replace") as fh:
        for number, raw in enumerate(fh, 1):
            line = raw.rstrip("\n")
            m = CLASS_RE.match(line)
            if m:
                cls = m.group(1)
            m = ROUTINE_RE.match(line)
            if m and not DECLARE_RE.match(line):
                routine = m.group(1)          # a declaration names a function, it is not inside one
            code = line.split("//")[0]
            for symbol in SYMBOLS:
                if symbol not in code:
                    continue
                if symbol == "LoadSaveGame" and "LoadSaveGameInfo" in code:
                    continue
                if DECLARE_RE.search(code) or re.match(r"^\s*(?:event|function)\s+" + symbol + r"\b", code):
                    role = "declare"
                elif "ConsoleCommand" in code:
                    role = "console"
                else:
                    role = "call"
                out.append({"package": package, "class": cls, "routine": "" if role == "declare" else routine,
                            "line": number,
                            "role": role, "symbol": symbol, "text": code.strip()[:160]})
                break
    return out


def build(src):
    rows = []
    for dirpath, _dirs, names in os.walk(src):
        for name in sorted(names):
            if not name.endswith(".uc"):
                continue
            rows.extend(rows_for(os.path.join(dirpath, name), os.path.splitext(name)[0]))
    return sorted(rows, key=lambda r: (r["role"], r["symbol"], r["class"], int(r["line"])))


def check(path):
    rows = list(csv.DictReader(open(path, encoding="utf-8")))
    problems = []
    if not rows:
        problems.append("no rows")
    for r in rows:
        if sorted(r) != sorted(COLUMNS):
            problems.append("columns")
            break
        if r["role"] not in ("declare", "call", "console"):
            problems.append("%s:%s: role %r" % (r["class"], r["line"], r["role"]))
        if not r["text"]:
            problems.append("%s:%s: no text" % (r["class"], r["line"]))
    roles = {r["role"] for r in rows}
    for needed in ("declare", "call"):
        if needed not in roles:
            problems.append("nothing with role %s -- the scan is not seeing the surface" % needed)
    if problems:
        print("FAIL savegame-paths: " + "; ".join(problems[:5]))
        return 1
    counts = {role: sum(1 for r in rows if r["role"] == role) for role in ("declare", "call", "console")}
    print("ok   savegame-paths: %d rows, %d declare, %d call, %d console"
          % (len(rows), counts["declare"], counts["call"], counts["console"]))
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--src", help="a tree of extracted .uc files (from tools/pkg-script-text.py)")
    ap.add_argument("-o", "--out", default=os.path.join(ROOT, "inventory"))
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    path = os.path.join(args.out, "savegame_paths.csv")
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
        print("%-9s %-26s %-28s %-22s %s" % (r["role"], r["class"], r["routine"], r["symbol"], r["text"][:60]))
    print("%d rows -> %s" % (len(rows), path))
    return 0


if __name__ == "__main__":
    sys.exit(main())
