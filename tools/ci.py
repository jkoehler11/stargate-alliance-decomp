#!/usr/bin/env python3
"""The checks CI can run without the game.

This project's value is that its claims are checkable, and most of the checks need the dump -- which
may never be committed or downloaded by CI (`AGENTS.md`). What is left is still worth a job per commit,
because it is what goes stale silently:

  1. every committed CSV parses, and every row has as many fields as its header;
  2. the function map is sorted, has no duplicate address, and every size is positive;
  3. the published symbol map agrees with the function map and the class tables it was built from;
  4. regenerating the symbol map reproduces the committed one byte for byte;
  5. regenerating `docs/NATIVE.md` and `inventory/xbe_functions_by_module.csv` reproduces them;
  5b. the opcode table is the table `tools/opcode-table.py` defines, and the disassembler splits the
     streams `docs/BYTECODE.md` documents;
  6. when a dump is present (`SG1_DUMP`), every symbol address is inside one of the image's sections.

Checks 1-5 are pure stdlib and run anywhere. Check 6 skips with a printed reason, because a CI runner
has no dump by design.

    python3 tools/ci.py            # or: make ci
"""
import csv
import io
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
FAILURES = []
SKIPS = []


def fail(check, detail):
    FAILURES.append(f"{check}: {detail}")
    print(f"FAIL {check}: {detail}")


def skip(check, reason):
    SKIPS.append(f"{check}: {reason}")
    print(f"SKIP {check}: {reason}")


def ok(check, detail=""):
    print(f"ok   {check}{': ' + detail if detail else ''}")


def repo(*parts):
    return os.path.join(ROOT, *parts)


def tracked_csvs():
    out = subprocess.run(["git", "-C", ROOT, "ls-files", "*.csv"],
                         capture_output=True, text=True, check=True).stdout.split()
    return out


def check_csvs():
    """Every tracked CSV parses and is rectangular."""
    name = "csv-shape"
    bad = 0
    files = tracked_csvs()
    for rel in files:
        path = repo(rel)
        try:
            with open(path, newline="", encoding="utf-8") as fh:
                rows = list(csv.reader(fh))
        except Exception as exc:                        # noqa: BLE001 - report, never raise
            fail(name, f"{rel}: {exc}")
            bad += 1
            continue
        if not rows:
            fail(name, f"{rel}: empty file")
            bad += 1
            continue
        width = len(rows[0])
        if width == 0:
            fail(name, f"{rel}: empty header")
            bad += 1
            continue
        if len(set(rows[0])) != width:
            fail(name, f"{rel}: duplicate column name in header")
            bad += 1
            continue
        for n, row in enumerate(rows[1:], start=2):
            # A trailing CRLF-only line is not a row; csv yields [] for it.
            if row and len(row) != width:
                fail(name, f"{rel}: line {n} has {len(row)} fields, header has {width}")
                bad += 1
                break
    if not bad:
        ok(name, f"{len(files)} files")


def read_functions():
    rows = []
    with open(repo("symbols", "xbox_functions.csv"), newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            rows.append((int(row["address"], 16), row["name"], int(row["size"])))
    return rows


def read_class_tables():
    out = []
    with open(repo("inventory", "xbe_class_table_names.csv"), newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            out.append((int(row["table_va"], 16), row["class"]))
    return out


def check_function_map():
    """Sorted, unique, positive sizes."""
    name = "function-map"
    rows = read_functions()
    prev = -1
    for addr, fname, size in rows:
        if addr <= prev:
            fail(name, f"addresses not strictly increasing at {addr:#010x}")
            return
        prev = addr
        if size <= 0:
            fail(name, f"{fname} ({addr:#010x}) has size {size}")
            return
        if not fname:
            fail(name, f"{addr:#010x} has no name")
            return
    ok(name, f"{len(rows)} functions")


def read_symbol_map():
    rows = []
    with open(repo("symbols", "xbox.map"), encoding="utf-8") as fh:
        for line in fh:
            if line.startswith("#") or not line.strip():
                continue
            parts = line.split()
            rows.append((int(parts[0], 16), parts[1], int(parts[2]), "class table" in line))
    return rows


def check_symbol_map():
    """The published map agrees with the maps it is built from."""
    name = "symbol-map"
    functions = {a: (n, s) for a, n, s in read_functions()}
    tables = dict(read_class_tables())
    rows = read_symbol_map()
    prev = -1
    seen = set()
    for addr, sname, size, is_table in rows:
        if addr <= prev:
            fail(name, f"not sorted at {addr:#010x}")
            return
        prev = addr
        if addr in seen:
            fail(name, f"duplicate address {addr:#010x}")
            return
        seen.add(addr)
        if is_table:
            if tables.get(addr) is None:
                fail(name, f"{addr:#010x} is marked a class table but no table has that address")
                return
            continue
        if addr not in functions:
            fail(name, f"{addr:#010x} {sname} is not in symbols/xbox_functions.csv")
            return
        if functions[addr] != (sname, size):
            fail(name, f"{addr:#010x}: map says {sname}/{size}, csv says {functions[addr][0]}/{functions[addr][1]}")
            return
    missing = set(functions) - seen
    if missing:
        fail(name, f"{len(missing)} functions missing from the map, first {min(missing):#010x}")
        return
    ok(name, f"{len(rows)} symbols ({len(functions)} functions + {len(rows) - len(functions)} class tables)")


def run_generator(name, argv, cwd):
    proc = subprocess.run(argv, cwd=cwd, capture_output=True, text=True)
    if proc.returncode != 0:
        fail(name, f"generator failed: {proc.stderr.strip().splitlines()[-1:] or proc.returncode}")
        return False
    return True


def diff_against_committed(check, generated, committed):
    with open(generated, "rb") as fh:
        new = fh.read()
    with open(repo(*committed.split("/")), "rb") as fh:
        old = fh.read()
    if new != old:
        # First differing line, so the message is actionable.
        for n, (a, b) in enumerate(zip(new.splitlines(), old.splitlines()), start=1):
            if a != b:
                fail(check, f"{committed} drifted at line {n}: {a!r} != {b!r}")
                return
        fail(check, f"{committed} drifted (length {len(new)} vs {len(old)})")
        return
    ok(check, committed)


def check_campaign_matrix():
    """The mission matrix is dump-free, so CI can insist it reproduces from the committed CSVs."""
    name = "campaign-matrix"
    with tempfile.TemporaryDirectory() as tmp:
        if not run_generator(name, [sys.executable, repo("tools", "campaign-matrix.py"), "-o", tmp], ROOT):
            return
        diff_against_committed(name, os.path.join(tmp, "campaign_matrix.csv"),
                               "inventory/campaign_matrix.csv")


def check_map_regenerates():
    """make symbols must reproduce the committed map byte for byte."""
    name = "symbol-map-regenerates"
    with tempfile.TemporaryDirectory() as tmp:
        env = dict(os.environ)
        if not run_generator(name, [sys.executable, repo("tools", "make-symbols.py"),
                                    "--symbols", repo("symbols", "xbox_functions.csv"),
                                    "--tables", repo("inventory", "xbe_class_table_names.csv"),
                                    "-o", tmp], ROOT):
            return
        diff_against_committed(name, os.path.join(tmp, "xbox.map"), "symbols/xbox.map")


def check_docs_regenerate():
    """The generated docs and per-module table must match a fresh run."""
    name = "docs-counts"
    with tempfile.TemporaryDirectory() as tmp:
        os.makedirs(os.path.join(tmp, "docs"))
        os.makedirs(os.path.join(tmp, "symbols"))
        os.makedirs(os.path.join(tmp, "inventory"))
        for f in ("xbox_functions.csv", "xbe_overrides.csv"):
            shutil.copy(repo("symbols", f), os.path.join(tmp, "symbols", f))
        for f in ("xbe_class_table_names.csv",):
            shutil.copy(repo("inventory", f), os.path.join(tmp, "inventory", f))
        if not run_generator(name, [sys.executable, repo("tools", "native-summary.py"),
                                    "--symbols", os.path.join(tmp, "symbols", "xbox_functions.csv"),
                                    "--overrides", os.path.join(tmp, "symbols", "xbe_overrides.csv"),
                                    "-o", os.path.join(tmp, "inventory")], tmp):
            return
        diff_against_committed(name, os.path.join(tmp, "docs", "NATIVE.md"), "docs/NATIVE.md")
        diff_against_committed(name, os.path.join(tmp, "inventory", "xbe_functions_by_module.csv"),
                               "inventory/xbe_functions_by_module.csv")


def check_opcode_table():
    """The opcode table must be internally consistent and the disassembler must split the known streams."""
    name = "opcode-table"
    for argv in ([sys.executable, repo("tools", "opcode-table.py"), "--check"],
                 [sys.executable, repo("tools", "uscript-dis.py"), "--selftest"]):
        r = subprocess.run(argv, capture_output=True, text=True, cwd=ROOT)
        line = (r.stdout or r.stderr).strip().splitlines()
        if r.returncode != 0:
            fail(name, line[-1] if line else "exit %d" % r.returncode)
            return
    ok(name, "256 opcodes, 31 groups, 11 streams split, 4 refusals honoured")


def check_lipsinc_runtime():
    """Every committed runtime row must still carry evidence for its name."""
    name = "lipsinc-runtime"
    r = subprocess.run([sys.executable, repo("tools", "lipsinc-runtime.py"), "--check"],
                       capture_output=True, text=True, cwd=ROOT)
    line = (r.stdout or r.stderr).strip().splitlines()
    if r.returncode != 0:
        fail(name, line[-1] if line else "exit %d" % r.returncode)
    else:
        ok(name, line[-1].replace("ok   lipsinc-runtime: ", "") if line else "")


def check_lipsinc_index():
    """The committed LIPSinc index must be consistent with itself (target indices, record size, trailer names)."""
    name = "lipsinc-index"
    r = subprocess.run([sys.executable, repo("tools", "lipsinc-index.py"), "--check"],
                       capture_output=True, text=True, cwd=ROOT)
    line = (r.stdout or r.stderr).strip().splitlines()
    if r.returncode != 0:
        fail(name, line[-1] if line else "exit %d" % r.returncode)
    else:
        ok(name, line[-1].replace("ok   lipsinc-index: ", "") if line else "")


def check_function_flags():
    """Every committed flags row must decode to its own name list, and a Net tail must carry the Net bit."""
    name = "function-flags"
    r = subprocess.run([sys.executable, repo("tools", "function-flags.py"), "--check"],
                       capture_output=True, text=True, cwd=ROOT)
    line = (r.stdout or r.stderr).strip().splitlines()
    if r.returncode != 0:
        fail(name, line[-1] if line else "exit %d" % r.returncode)
    else:
        ok(name, line[-1].replace("ok   function-flags: ", "") if line else "")


def check_versions_lock():
    """tools/versions.lock must still parse, pin what it claims, and explain what it does not pin."""
    name = "versions-lock"
    r = subprocess.run([sys.executable, repo("tools", "versions-check.py"), "--check"],
                       capture_output=True, text=True, cwd=ROOT)
    out = (r.stdout or r.stderr).strip().splitlines()
    if r.returncode != 0:
        fail(name, out[-1] if out else "exit %d" % r.returncode)
    else:
        ok(name, out[-1].replace("ok   versions-lock: ", "") if out else "")


def check_artifact_hashes():
    """Every committed artifact must match the expected hash recorded beside it."""
    name = "artifact-hashes"
    r = subprocess.run([sys.executable, repo("tools", "artifact-hashes.py"), "--check"],
                       capture_output=True, text=True, cwd=ROOT)
    out = (r.stdout or r.stderr).strip().splitlines()
    if r.returncode != 0:
        fail(name, out[-1] if out else "exit %d" % r.returncode)
    else:
        ok(name, out[-1].replace("ok   artifact-hashes: ", "") if out else "")


def check_addresses_in_image():
    """Every symbol inside a section of the image, when there is an image."""
    name = "addresses-in-image"
    dump = os.environ.get("SG1_DUMP", os.path.join(os.path.expanduser("~"), "sg1", "dump"))
    xbe = os.path.join(dump, "Stargate_SG-1_-_The_Alliance-2005-12-15", "default.xbe")
    if not os.path.isfile(xbe):
        skip(name, f"no dump at {xbe} (CI never has one: AGENTS.md)")
        return
    sys.path.insert(0, repo("tools"))
    from xbe import Xbe                                        # noqa: PLC0415 - only when a dump exists
    image = Xbe(xbe)
    outside = [a for a, _n, _s, _t in read_symbol_map()
               if image.section_for(a) is None]
    if outside:
        fail(name, f"{len(outside)} symbols outside every section, first {outside[0]:#010x}")
        return
    ok(name, f"{len(read_symbol_map())} symbols inside the image's sections")


def main():
    print(f"ci: {ROOT}")
    check_csvs()
    check_function_map()
    check_symbol_map()
    check_map_regenerates()
    check_docs_regenerate()
    check_campaign_matrix()
    check_artifact_hashes()
    check_versions_lock()
    check_opcode_table()
    check_function_flags()
    check_lipsinc_index()
    check_lipsinc_runtime()
    check_addresses_in_image()
    print()
    if SKIPS:
        print(f"skipped {len(SKIPS)}: " + "; ".join(s.split(':')[0] for s in SKIPS))
    if FAILURES:
        print(f"FAILED {len(FAILURES)} check(s)")
        return 1
    print("all checks passed")
    return 0


if __name__ == "__main__":
    sys.exit(main())
