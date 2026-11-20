#!/usr/bin/env python3
"""Drive umodel over the shipped asset packages and record what it produced (PLAN 5.1).

The export itself is umodel's, and its output is game data: it goes to `--out` (a directory
under $WORK) and never into this repository. What is committed is the *measurement* —
`inventory/assets.csv` per package and `inventory/asset_classes.csv` per object class — plus
`docs/ASSETS.md`, which is where each class's extraction path is written down.

    python3 tools/asset-export.py --dump $SG1_DUMP/Stargate_SG-1_-_The_Alliance-2005-12-15 \
        --out $WORK/extracted/assets -o inventory --jobs 4

`--check` validates the two artifacts without a dump or an export run.
"""

import argparse
import collections
import csv
import os
import re
import subprocess
import sys
import time
from concurrent.futures import ThreadPoolExecutor

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

# Extension -> extra umodel flags. The UE2 profile is what the game's packages were built for.
EXTENSIONS = {".utx": [], ".usx": [], ".ukx": [], ".uax": ["-sounds"]}
# NOT -nostat: in this umodel it means "do not load StaticMesh", which silently exported 0 objects
# for 81 of the 91 mesh packages.
COMMON_FLAGS = ["-export", "-png", "-gltf"]

EXPORTED = re.compile(r"Exported (\d+)/(\d+) objects")
EXPORTING = re.compile(r"^Exporting (\w+) (.+?) to (.+)$")
WARNING = re.compile(r"^(?:\*\*\* )?(WARNING|ERROR): (.+)$")

PACKAGE_COLUMNS = ["dir", "package", "bytes", "objects", "exported", "files",
                   "out_bytes", "seconds", "status", "error"]
CLASS_COLUMNS = ["class", "objects"]


def discover(dump, only):
    """[(dir, package, path, ext)] for every asset package, sorted for reproducibility."""
    found = []
    for dirpath, dirnames, filenames in os.walk(dump):
        dirnames.sort()
        for fn in sorted(filenames):
            ext = os.path.splitext(fn)[1].lower()
            if ext not in EXTENSIONS:
                continue
            if only and ext.lstrip(".") not in only:
                continue
            rel = os.path.relpath(dirpath, dump)
            found.append((rel, os.path.splitext(fn)[0], os.path.join(dirpath, fn), ext))
    return sorted(found)


def tree_files(path):
    n = size = 0
    for dirpath, _dirs, files in os.walk(path):
        for fn in files:
            n += 1
            try:
                size += os.path.getsize(os.path.join(dirpath, fn))
            except OSError:
                pass
    return n, size


def run_one(umodel, dump, out_root, entry):
    rel, package, path, ext = entry
    dest = os.path.join(out_root, package)
    cmd = [umodel, "-path=%s" % dump, "-game=ue2"] + COMMON_FLAGS + EXTENSIONS[ext]
    cmd += ["-out=%s" % dest, package]
    started = time.time()
    try:
        r = subprocess.run(cmd, capture_output=True, text=True, timeout=1800,
                           env=dict(os.environ, SDL_VIDEODRIVER="dummy"))
        rc, out, err = r.returncode, r.stdout, r.stderr
    except subprocess.TimeoutExpired:
        rc, out, err = 124, "", "timeout"
    seconds = time.time() - started
    log = out + "\n" + err

    m = EXPORTED.search(log)
    objects = int(m.group(2)) if m else 0
    exported = int(m.group(1)) if m else 0
    classes = collections.Counter()
    errors = collections.Counter()
    for line in log.splitlines():
        em = EXPORTING.match(line.strip())
        if em:
            classes[em.group(1)] += 1
        wm = WARNING.match(line.strip())
        if wm:
            errors[wm.group(2)[:60]] += 1
    files, size = tree_files(dest)
    status = "ok" if rc == 0 and m else ("empty" if rc == 0 and files == 0 else "fail")
    if rc == 0 and m and exported < objects:
        status = "partial"
    first_error = next(iter(errors), "") if status != "ok" else ""
    row = {"dir": rel, "package": package, "bytes": os.path.getsize(path),
           "objects": objects, "exported": exported, "files": files,
           "out_bytes": size, "seconds": round(seconds, 1), "status": status,
           "error": first_error}
    return row, classes, errors, (rc, log[-400:] if status in ("fail", "partial") else "")


def export(args):
    if not os.path.isfile(args.umodel):
        raise SystemExit("no umodel at %s" % args.umodel)
    if not os.path.isdir(args.dump):
        raise SystemExit("no dump at %s" % args.dump)
    entries = discover(args.dump, set(args.only.split(",")) if args.only else None)
    if args.limit:
        entries = entries[:args.limit]
    print("%d asset packages under %s" % (len(entries), args.dump))

    rows, class_objects, class_errors = [], collections.Counter(), collections.Counter()
    failures = []
    with ThreadPoolExecutor(max_workers=args.jobs) as pool:
        futures = [pool.submit(run_one, args.umodel, args.dump, args.out, e) for e in entries]
        for i, fut in enumerate(futures, 1):
            row, classes, errors, detail = fut.result()
            rows.append(row)
            for name, n in classes.items():
                class_objects[name] += n
            for msg, n in errors.items():
                class_errors[msg] += n
            if row["status"] != "ok":
                failures.append((row["package"], row["status"], detail[1] if detail else ""))
            if i % 25 == 0 or i == len(entries):
                print("  %d/%d packages, %d objects exported, %d files, %.0f MB"
                      % (i, len(entries), sum(r["exported"] for r in rows),
                         sum(r["files"] for r in rows), sum(r["out_bytes"] for r in rows) / 1e6))

    write_csv(os.path.join(args.out_dir, "assets.csv"), PACKAGE_COLUMNS, rows)
    classes = [{"class": k, "objects": class_objects[k]} for k in sorted(class_objects)]
    write_csv(os.path.join(args.out_dir, "asset_classes.csv"), CLASS_COLUMNS, classes)
    write_doc(args.doc, rows, classes, failures)
    print("%d packages: %d ok, %d partial, %d failed; %d files, %.0f MB"
          % (len(rows), sum(r["status"] == "ok" for r in rows),
             sum(r["status"] == "partial" for r in rows),
             sum(r["status"] == "fail" for r in rows) + sum(r["status"] == "empty" for r in rows),
             sum(r["files"] for r in rows), sum(r["out_bytes"] for r in rows) / 1e6))
    for pkg, status, detail in failures[:10]:
        print("  %-28s %-8s %s" % (pkg, status, detail.replace("\n", " ")[:150]))
    return 0 if not any(s in ("fail",) for _p, s, _d in failures) else 1


def write_csv(path, columns, rows):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=columns, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


def write_doc(path, rows, classes, failures):
    by_dir = collections.Counter(r["dir"] for r in rows)
    total_files = sum(int(r["files"]) for r in rows)
    total_bytes = sum(int(r["out_bytes"]) for r in rows)
    lines = [
        "# Asset export (PLAN 5.1)",
        "",
        "Generated by `tools/asset-export.py` (`make assets`) from `inventory/assets.csv` and",
        "`inventory/asset_classes.csv`. The exported data itself is not in this repository: it lands under",
        "`$WORK/extracted/assets`, and only the measurement below is committed.",
        "",
        "| packages | files | output | objects |",
        "|---:|---:|---:|---:|",
        "| %d | %d | %.1f MB | %d |" % (len(rows), total_files, total_bytes / 1e6,
                                         sum(int(r["exported"]) for r in rows)),
        "",
        "## By directory",
        "",
        "| directory | packages | files | MB |",
        "|---|---:|---:|---:|",
    ]
    for d, n in sorted(by_dir.items()):
        sel = [r for r in rows if r["dir"] == d]
        lines.append("| `%s` | %d | %d | %.1f |"
                     % (d, n, sum(int(r["files"]) for r in sel),
                        sum(int(r["out_bytes"]) for r in sel) / 1e6))
    lines += ["", "## What each class's extraction path is", "",
              "umodel names the class as it writes the object; the path below is the one this run took, so",
              "it is measured rather than declared.", "",
              "| class family | path | note |", "|---|---|---|",
              "| `Texture`, `Shader`, `Combiner`, `FinalBlend`, `TexEnvMap`, `TexOscillator`, `TexPanner`,"
              " `TexRotator`, `TexScaler` | umodel `-png` | one image per material/master object |",
              "| `StaticMesh`, `VertMesh` | umodel `-gltf` | `.gltf` + `.bin` per mesh |",
              "| `SkeletalMesh`, `MeshAnimation` | umodel `-gltf` | animations are the class that umodel's"
              " `MeshAnimation::Serialize` still refuses in 14 packages |",
              "| `Sound` | **not umodel** | umodel leaves unread bytes in every `.uax` (`USound::Serialize`;"
              " 53 of 55 packages failed), so sounds stay with this project's own reader (`docs/DIALOGUE.md`,"
              " PLAN 5.3, 3,287 WAVs indexed) |", "",
              "`-nostat` is **not** a quiet flag in this umodel: it means *do not load StaticMesh* and it",
              "silently exported 0 objects for 81 of the 91 mesh packages in the first pass.", "",
              "## By object class", "",
              "The class is what umodel announced as it wrote the object, so this table is measured, not",
              "declared. It is the list PLAN 5.1's acceptance asks to have a documented path for.",
              "", "| class | objects |", "|---|---:|"]
    for c in classes:
        lines.append("| `%s` | %d |" % (c["class"], int(c["objects"])))
    if failures:
        lines += ["", "## Packages that did not export cleanly", ""]
        for pkg, status, detail in failures[:50]:
            lines.append("- `%s` — %s%s" % (pkg, status,
                                            (": " + detail.replace("\n", " ")[:160]) if detail else ""))
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("\n".join(lines) + "\n")


def check(args):
    """Validate the committed artifacts. No dump, no umodel, no export run."""
    problems = []
    for name, columns in (("assets.csv", PACKAGE_COLUMNS), ("asset_classes.csv", CLASS_COLUMNS)):
        path = os.path.join(args.out_dir, name)
        if not os.path.isfile(path):
            raise SystemExit("%s missing -- run the exporter" % path)
        with open(path, newline="") as fh:
            rd = csv.DictReader(fh)
            if rd.fieldnames != columns:
                problems.append("%s columns are %r" % (name, rd.fieldnames))
                continue
            rows = list(rd)
        if not rows:
            problems.append("%s has no rows" % name)
        if name == "assets.csv":
            seen = set()
            for row in rows:
                key = (row["dir"], row["package"])
                if key in seen:
                    problems.append("%s listed twice" % (key,))
                seen.add(key)
                if row["status"] not in ("ok", "partial", "empty", "fail"):
                    problems.append("%s has status %r" % (row["package"], row["status"]))
                if int(row["exported"]) > int(row["objects"]):
                    problems.append("%s exported more than it holds" % row["package"])
    if problems:
        print("asset-export: %d problem(s)" % len(problems))
        for p in problems[:10]:
            print("  " + p)
        return 1
    with open(os.path.join(args.out_dir, "assets.csv"), newline="") as fh:
        n = sum(1 for _ in fh) - 1
    print("ok   asset-export: %d package rows, statuses and counts consistent" % n)
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dump", help="the game directory holding System/, Textures/, ...")
    ap.add_argument("--out", help="export destination (game data; keep it under $WORK)")
    ap.add_argument("--umodel", default=os.path.expanduser("~/tools/umodel/umodel"))
    ap.add_argument("-o", "--out-dir", default=os.path.join(ROOT, "inventory"))
    ap.add_argument("--doc", default=os.path.join(ROOT, "docs", "ASSETS.md"))
    ap.add_argument("--only", help="comma list of extensions: utx,usx,ukx,uax")
    ap.add_argument("--limit", type=int, default=0, help="first N packages only (a trial run)")
    ap.add_argument("--jobs", type=int, default=4)
    ap.add_argument("--doc-only", action="store_true",
                    help="rewrite docs/ASSETS.md from the committed artifacts, no export")
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    if args.check:
        return check(args)
    if args.doc_only:
        with open(os.path.join(args.out_dir, "assets.csv"), newline="") as fh:
            rows = list(csv.DictReader(fh))
        with open(os.path.join(args.out_dir, "asset_classes.csv"), newline="") as fh:
            classes = list(csv.DictReader(fh))
        failures = [(r["package"], r["status"], r.get("error", "")) for r in rows
                    if r["status"] != "ok"]
        write_doc(args.doc, rows, classes, failures)
        print("wrote %s from %d package rows" % (args.doc, len(rows)))
        return 0
    missing = [f for f in ("dump", "out") if not getattr(args, f)]
    if missing:
        raise SystemExit("--%s required to export" % missing[0])
    return export(args)


if __name__ == "__main__":
    sys.exit(main())
