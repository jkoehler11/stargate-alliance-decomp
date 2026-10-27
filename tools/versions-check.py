#!/usr/bin/env python3
"""Check `tools/versions.lock` against what is actually installed (PLAN 0.5).

The lock used to be prose: it listed versions and nothing compared them, so it drifted the way any unpinned claim
does. This makes each line a constraint that can be evaluated, and separates the two questions a reproducer has:

    python3 tools/versions-check.py            # table: pinned vs found, per tool
    python3 tools/versions-check.py --strict   # exit 1 if an installed tool violates its constraint
    python3 tools/versions-check.py --check    # structural only: does the lock still say what it means (in `make ci`)

`--check` is what CI runs, because CI has no dump and no Ghidra: it verifies that every line parses, that no tool is
listed twice, and that no entry is silently unpinned -- `UNPINNED` and `none` are allowed but must carry a note saying
why. `--strict` is for a machine that has the toolchain: it fails on a version that is present and wrong, not on a tool
that is absent, because absence is a stage you have not run, not a broken pin.
"""
import argparse
import os
import re
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LOCK = os.path.join("tools", "versions.lock")
META = {"target.archive", "target.sha256"}
ABSENT = ("UNPINNED", "none")


def parse(path):
    """[(tool, constraint, source, note)] -- comments and blank lines dropped, continuation lines folded."""
    rows = []
    for raw in open(path, encoding="utf-8"):
        line = raw.rstrip("\n")
        if not line.strip() or line.lstrip().startswith("#"):
            continue
        if line.startswith((" ", "\t")) and rows:          # a continuation of the previous note
            tool, constraint, source, note = rows[-1]
            rows[-1] = (tool, constraint, source, (note + " " + line.strip()).strip())
            continue
        parts = line.split(None, 3)
        if len(parts) == 2 and parts[0].startswith("target."):
            rows.append((parts[0], parts[1], "--", ""))
            continue
        if len(parts) == 3 and parts[1] in ABSENT:
            rows.append((parts[0], parts[1], parts[2], ""))   # structural check reports the missing note
            continue
        if len(parts) < 4:
            raise ValueError("cannot parse: %r" % line)
        rows.append((parts[0], parts[1], parts[2], parts[3].strip()))
    return rows


def numeric(text):
    return tuple(int(x) for x in re.findall(r"\d+", text)[:3])


def satisfies(found, constraint):
    """Apply >=, <=, ==, ~= or a bare version to a found version string."""
    if not found:
        return None
    op = "=="
    for candidate in (">=", "<=", "~=", "==", ">"):
        if constraint.startswith(candidate):
            op, constraint = candidate, constraint[len(candidate):].strip()
            break
    want, have = numeric(constraint), numeric(found)
    if not want or not have:
        return None
    n = min(len(want), len(have))
    want, have = want[:n], have[:n]
    if op == ">=":
        return have >= want
    if op == "<=":
        return have <= want
    if op == ">":
        return have > want
    if op == "~=":
        return have[:1] == want[:1] and have >= want
    if "x" in constraint.lower():                       # 10.0.x -> major.minor match
        return have[:2] == want[:2]
    return have == want


def probe(tool):
    """(version string, where it came from) for the tools that can be asked; ('', '') when absent."""
    python = sys.executable

    def run(cmd):
        try:
            out = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        except (OSError, subprocess.SubprocessError):
            return ""
        return (out.stdout or out.stderr).strip()

    if tool == "python":
        return ".".join(str(x) for x in sys.version_info[:3]), python
    if tool == "as":
        m = re.search(r"(\d+\.\d+(?:\.\d+)?)", run(["as", "--version"]))
        return (m.group(1), shutil.which("as") or "") if m else ("", "")
    if tool == "dotnet-sdk":
        return run(["dotnet", "--version"]), shutil.which("dotnet") or ""
    if tool == "jdk":
        text = run(["java", "-version"])
        m = re.search(r'version "?(\d+)', text)
        return (m.group(1), shutil.which("java") or "") if m else ("", "")
    if tool == "ghidra":
        home = os.environ.get("GHIDRA_HOME", "")
        m = re.search(r"ghidra_(\d+\.\d+)", home)
        return (m.group(1), home) if m else ("", "")
    if tool in ("py7zr", "capstone"):
        try:
            module = __import__(tool)
        except ImportError:
            return "", ""
        return getattr(module, "__version__", ""), module.__file__ or ""
    if tool in ("uelib", "uelib-decompiler", "xbox-sigs", "umodel", "xemu", "target.archive", "target.sha256"):
        return "", ""                                     # not versioned by any command on this machine
    return "", ""


def table(rows, strict):
    failures = 0
    print("%-18s %-12s %-10s %-11s %s" % ("tool", "constraint", "found", "status", "source"))
    for tool, constraint, source, _note in rows:
        if tool in META:
            continue
        found, where = probe(tool)
        if constraint in ABSENT:
            status = "n/a (%s)" % constraint
        elif not found:
            status = "absent"
        else:
            okk = satisfies(found, constraint)
            status = "match" if okk else ("different" if okk is False else "unparsed")
            if okk is False:
                failures += 1
        print("%-18s %-12s %-10s %-11s %s" % (tool, constraint, found or "-", status, where or source))
    if failures:
        print("\n%d installed tool(s) violate their constraint -- that is a reproduction finding: either update "
              "tools/versions.lock in its own commit, or expect different artifact hashes" % failures)
    return 1 if (strict and failures) else 0


def structural(path):
    try:
        rows = parse(path)
    except (OSError, ValueError) as exc:
        print("FAIL versions-lock: %s" % exc)
        return 1
    problems = []
    seen = {}
    for tool, constraint, _source, note in rows:
        seen[tool] = seen.get(tool, 0) + 1
        if tool in META:                     # the dump's identity is not a version to compare arithmetically
            continue
        if constraint in ABSENT and not note:
            problems.append("%s: %r without a note saying why" % (tool, constraint))
        if constraint not in ABSENT and not re.match(r"^(>=|<=|==|~=|>)?[\d.]", constraint):
            problems.append("%s: constraint %r is not a version" % (tool, constraint))
    for tool, count in seen.items():
        if count > 1:
            problems.append("%s listed %d times" % (tool, count))
    if "target.sha256" not in seen:
        problems.append("the target dump's sha256 is not pinned")
    if problems:
        print("FAIL versions-lock: " + "; ".join(problems[:6]))
        return 1
    print("ok   versions-lock: %d entries, %d pinned, %d deliberately not"
          % (len(rows), sum(1 for r in rows if r[1] not in ABSENT), sum(1 for r in rows if r[1] in ABSENT)))
    return 0


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--lock", default=os.path.join(ROOT, LOCK))
    ap.add_argument("--strict", action="store_true", help="fail when an installed tool violates its constraint")
    ap.add_argument("--check", action="store_true", help="structural check only (no probe)")
    args = ap.parse_args()
    if args.check:
        return structural(args.lock)
    return table(parse(args.lock), args.strict)


if __name__ == "__main__":
    sys.exit(main())
