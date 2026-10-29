#!/usr/bin/env python3
"""The expected hashes of every committed artifact (PLAN 8.1).

`make ci` proves that the artifacts it can regenerate come out byte for byte. It cannot prove the rest, and it cannot
notice an artifact edited by hand instead of regenerated. This is the manifest that closes both: every file under
`inventory/` and `symbols/` is generated, so its SHA-256 is a claim that a reproducer in another checkout must match.

    python3 tools/artifact-hashes.py            # rewrite inventory/artifact_hashes.csv
    python3 tools/artifact-hashes.py --check     # verify the committed manifest against the files (in `make ci`)
    python3 tools/artifact-hashes.py --selftest  # the tracked-only rule, in a throwaway repository (runs in CI)

`--check` fails on three things, and says which: a file whose hash changed (regenerated, but the manifest was not
rewritten in the same commit), a file in the manifest that is gone, and an artifact that is not in the manifest at
all — an unrecorded artifact is the same defect as an unregenerable one.

"Committed" is the git index, not the directory listing. `symbols/*.sym.s` and `symbols/*.sym.o` are the assembler
output of a build and are in `.gitignore`; while they were counted, any tree that had been built in failed the check
with `unrecorded:` and a regenerated manifest recorded build products that a clean checkout then reported `gone:`.

Hand-written documents are deliberately out of scope: `docs/BUILDING.md`, `PLAN.md` and the rest are prose. The
generated documents that carry counts are checked by regenerating them (`tools/ci.py` `check_docs_regenerate`).
"""
import argparse
import contextlib
import csv
import hashlib
import io
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DIRS = ("inventory", "symbols")
MANIFEST = os.path.join("inventory", "artifact_hashes.csv")
COLUMNS = ["path", "bytes", "sha256"]
SOURCE = "index"  # what artifacts() enumerated last: "index", or "directory" with no git to ask


def digest(path):
    h = hashlib.sha256()
    size = 0
    with open(path, "rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
            size += len(block)
    return size, h.hexdigest()


def index_files():
    """The paths git has in the index under the artifact directories, or None when git cannot answer."""
    try:
        r = subprocess.run(["git", "-C", ROOT, "ls-files", "-z", "--"] + list(DIRS),
                           stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, check=False)
    except OSError:
        return None
    if r.returncode != 0:
        return None
    return [p for p in r.stdout.decode("utf-8", "surrogateescape").split("\0") if p]


def directory_files():
    """Fallback for a tree without a git index (an export): every file, ignored build products included."""
    out = []
    for d in DIRS:
        full = os.path.join(ROOT, d)
        if not os.path.isdir(full):
            continue
        for f in sorted(os.listdir(full)):
            path = os.path.join(full, f)
            if os.path.isfile(path):
                out.append(os.path.join(d, f))
    return out


def artifacts():
    """Every committed file under the artifact directories, except the manifest itself."""
    global SOURCE
    tracked = index_files()
    SOURCE = "index" if tracked is not None else "directory"
    files = tracked if tracked is not None else directory_files()
    return sorted(p for p in files if p != MANIFEST)


def source_note():
    """What enumerated the artifacts. Only the fallback can see files git was told to ignore."""
    if SOURCE == "index":
        return ""
    return (" (directory scan: no git index, so ignored build products cannot be excluded -- an `unrecorded` "
            "line here is the tree, not the manifest)")


def build():
    """(rows, absent): the manifest rows, and the committed artifacts that are not in the worktree."""
    rows, absent = [], []
    for rel in artifacts():
        path = os.path.join(ROOT, rel)
        if not os.path.isfile(path):
            absent.append(rel)  # in the index, not on disk: `--check` reports it as gone
            continue
        size, sha = digest(path)
        rows.append({"path": rel, "bytes": size, "sha256": sha})
    return rows, absent


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
        if not os.path.isfile(os.path.join(ROOT, rel)):
            continue  # a committed artifact deleted from the worktree: reported as gone below
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
    print("ok   artifact-hashes: %d artifacts, %.1f MiB, manifest matches%s"
          % (len(committed), total / 2**20, source_note()))
    return 0


def write_manifest(path, rows):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS, lineterminator="\n")
        w.writeheader()
        w.writerows(rows)


def _write(path, text):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write(text)


def _captured(fn, *args):
    """Run a checker and return (its exit status, its printed lines)."""
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        rc = fn(*args)
    return rc, buf.getvalue()


def selftest():
    """The tracked-only rule, in a throwaway repository: an ignored build product is not an artifact."""
    global ROOT
    tmp = tempfile.mkdtemp(prefix="artifact-hashes-")
    saved = ROOT
    git = ["git", "-C", tmp]
    try:
        os.makedirs(os.path.join(tmp, "inventory"))
        os.makedirs(os.path.join(tmp, "symbols"))
        _write(os.path.join(tmp, ".gitignore"), "symbols/*.sym.s\nsymbols/*.sym.o\n")
        _write(os.path.join(tmp, "symbols", "xbox.map"), "map\n")
        _write(os.path.join(tmp, "inventory", "rows.csv"), "path\nrows\n")
        try:
            subprocess.run(git + ["init", "-q", "."], check=True, stdout=subprocess.DEVNULL)
            subprocess.run(git + ["add", "-A"], check=True)
            subprocess.run(git + ["-c", "user.email=selftest@example.invalid", "-c", "user.name=selftest",
                                  "commit", "-qm", "base"], check=True, stdout=subprocess.DEVNULL)
        except (OSError, subprocess.CalledProcessError) as exc:
            print("FAIL selftest: needs a working git to build the throwaway repository (%s)" % exc)
            return 1
        ROOT = tmp

        # 1. A build product sitting in the tree is not an artifact, however many of them there are.
        _write(os.path.join(tmp, "symbols", "xbox.sym.o"), "leftover\n")
        _write(os.path.join(tmp, "symbols", "xbox.sym.s"), "leftover\n")
        found = artifacts()
        wanted = ["inventory/rows.csv", "symbols/xbox.map"]
        if found != wanted:
            print("FAIL selftest: artifacts() saw %s, wanted %s -- an ignored build product was counted"
                  % (found, wanted))
            return 1
        print("ok   selftest: two ignored build products on disk are not artifacts (%d artifacts)"
              % len(found))

        # 2. With a manifest that covers the tracked files, the check passes in that same tree.
        manifest = os.path.join(tmp, MANIFEST)
        rows, absent = build()
        if absent:
            print("FAIL selftest: build() reported %s gone in a tree where every artifact is present" % absent)
            return 1
        write_manifest(manifest, rows)
        rc, out = _captured(check, manifest)
        if rc != 0:
            print("FAIL selftest: the check failed on a tree whose only extra files are ignored:\n%s"
                  % out.strip())
            return 1
        print("ok   selftest: the check passes over that tree and the manifest holds %d rows"
              % len(read_manifest(manifest)))

        # 3. Committed is the index: an untracked file is not an artifact, tracked-but-absent is `gone`.
        _write(os.path.join(tmp, "inventory", "untracked.csv"), "path\nuntracked\n")
        if "inventory/untracked.csv" in artifacts():
            print("FAIL selftest: an untracked file was counted as an artifact")
            return 1
        os.remove(os.path.join(tmp, "symbols", "xbox.map"))
        rc, out = _captured(check, manifest)
        if rc != 1 or "gone: symbols/xbox.map" not in out:
            print("FAIL selftest: a tracked artifact deleted from the worktree was not reported gone:\n%s"
                  % out.strip())
            return 1
        print("ok   selftest: an untracked file is not an artifact and a deleted tracked artifact is `gone`")

        # 4. A newly added artifact that the manifest does not mention is still `unrecorded`.
        subprocess.run(git + ["-c", "user.email=selftest@example.invalid", "-c", "user.name=selftest",
                              "checkout", "--", "symbols/xbox.map"], check=True)
        subprocess.run(git + ["add", "inventory/untracked.csv"], check=True)
        rc, out = _captured(check, manifest)
        if rc != 1 or "unrecorded: inventory/untracked.csv" not in out:
            print("FAIL selftest: a committed artifact missing from the manifest was not reported:\n%s"
                  % out.strip())
            return 1
        print("ok   selftest: a committed artifact absent from the manifest is still `unrecorded`")
    finally:
        ROOT = saved
        shutil.rmtree(tmp, ignore_errors=True)
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("-o", "--out", default=os.path.join(ROOT, MANIFEST))
    ap.add_argument("--check", action="store_true")
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        return selftest()
    if args.check:
        return check(args.out)
    rows, absent = build()
    if absent:
        print("FAIL artifact-hashes: %d committed artifact(s) are gone from the worktree, so the manifest would "
              "stop describing them: %s" % (len(absent), ", ".join(absent[:6])))
        return 1
    write_manifest(args.out, rows)
    print("%d artifacts, %.1f MiB -> %s%s" % (len(rows), sum(r["bytes"] for r in rows) / 2**20,
                                              os.path.relpath(args.out, ROOT), source_note()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
