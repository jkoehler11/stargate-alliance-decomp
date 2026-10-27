#!/usr/bin/env python3
"""The expected hashes of every committed artifact (PLAN 8.1).

`make ci` proves that the artifacts it can regenerate come out byte for byte. It cannot prove the rest, and it cannot
notice an artifact edited by hand instead of regenerated. This is the manifest that closes both: every file under
`inventory/` and `symbols/` is generated, so its SHA-256 is a claim that a reproducer in another checkout must match.

    python3 tools/artifact-hashes.py            # rewrite inventory/artifact_hashes.csv
    python3 tools/artifact-hashes.py --check     # verify the committed manifest against the files (in `make ci`)

`--check` fails on three things, and says which: a file whose hash changed (regenerated, but the manifest was not
rewritten in the same commit), a file in the manifest that is gone, and an artifact that is not in the manifest at
all — an unrecorded artifact is the same defect as an unregenerable one.

Hand-written documents are deliberately out of scope: `docs/BUILDING.md`, `PLAN.md` and the rest are prose. The
generated documents that carry counts are checked by regenerating them (`tools/ci.py` `check_docs_regenerate`).
"""
import argparse
import csv
import hashlib
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIRS = ("inventory", "symbols")
MANIFEST = os.path.join("inventory", "artifact_hashes.csv")
COLUMNS = ["path", "bytes", "sha256"]


def digest(path):
    h = hashlib.sha256()
    size = 0
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
            size += len(block)
    return size, h.hexdigest()


def artifacts():
    """Every committed file under the artifact directories, except the manifest itself."""
    out = []
    for d in DIRS:
        full = os.path.join(ROOT, d)
        if not os.path.isdir(full):
            continue
        for f in sorted(os.listdir(full)):
            path = os.path.join(full, f)
            if not os.path.isfile(path):
                continue
            rel = os.path.join(d, f)
            if rel == MANIFEST:
                continue
            out.append(rel)
    return sorted(out)


def build():
    rows = []
    for rel in artifacts():
        size, sha = digest(os.path.join(ROOT, rel))
        rows.append({"path": rel, "bytes": size, "sha256": sha})
    return rows


def read_manifest(path):
    with open(path, newline="", encoding="utf-8") as fh:
        return {r["path"]: r for r in csv.DictReader(fh)}


def check(manifest_path):
    if not os.path.isfile(manifest_path):
        print("FAIL artifact-hashes: %s is missing; run `make artifact-hashes`" % MANIFEST)
        return 1
    committed = read_manifest(manifest_path)
    changed, missing, unrecorded = [], [], []
    for rel in artifacts():
        size, sha = digest(os.path.join(ROOT, rel))
        row = committed.get(rel)
        if row is None:
            unrecorded.append(rel)
        elif row["sha256"] != sha:
            changed.append("%s (%s -> %s bytes)" % (rel, row["bytes"], size))
    for rel in committed:
        if not os.path.isfile(os.path.join(ROOT, rel)):
            missing.append(rel)
    if changed or missing or unrecorded:
        for label, items in (("changed", changed), ("gone", missing), ("unrecorded", unrecorded)):
            for item in items[:6]:
                print("FAIL artifact-hashes: %s: %s" % (label, item))
        print("FAIL artifact-hashes: %d changed, %d gone, %d unrecorded -- regenerate the artifacts, then run "
              "`make artifact-hashes` and commit the manifest with them" % (len(changed), len(missing), len(unrecorded)))
        return 1
    total = sum(int(r["bytes"]) for r in committed.values())
    print("ok   artifact-hashes: %d artifacts, %.1f MiB, manifest matches" % (len(committed), total / 2**20))
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("-o", "--out", default=os.path.join(ROOT, MANIFEST))
    ap.add_argument("--check", action="store_true")
    args = ap.parse_args()
    if args.check:
        return check(args.out)
    rows = build()
    os.makedirs(os.path.dirname(args.out), exist_ok=True)
    with open(args.out, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)
    print("%d artifacts, %.1f MiB -> %s" % (len(rows), sum(r["bytes"] for r in rows) / 2**20,
                                            os.path.relpath(args.out, ROOT)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
