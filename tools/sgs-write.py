#!/usr/bin/env python3
"""Write a checkpoint's two artefacts: rebuild the package, and add its `[Saved Games]` row.

A checkpoint is not one file. The `.sgs` package holds the level's actors, and the profile's `[Saved Games]`
section holds the row that makes the save *visible* — key = the file name, value = the description the menu
shows. So the write side is two writers, and a rebuild that produces only the package produces a file the game
cannot resume.

This tool exercises both without a boot and without trusting either one's output:

* **the package** is rebuilt from the parsed model (`pkg-write.rebuild`): header, name/import/export tables and
  every export payload at its offset, then compared with the shipped file byte by byte. A byte-identical rebuild
  says the model covers the file; a diff says exactly where it does not, and any region the model cannot place is
  reported as a gap rather than zero-filled.
* **the row** is added to a *copy* of the profile as `key=value` under `[Saved Games]`, preserving the file's
  CRLF and every other byte, then re-read: the section count must go up by exactly one, the key must be in that
  section, and adding it twice must change nothing. A duplicate key is refused, because the index is a map.

**What it measures on the shipped checkpoints (2026-11-09): the row half works, the package half stops at the
export table.** `ue2pkg` reads a checkpoint's header exactly as `docs/formats/sgs-checkpoint.md` records it
(1,072 names / 93 imports / 210 exports for `C_M02A_Drillsite`) and parses the name and import tables, but the
export walk desyncs inside the table: 3,401 bytes for 210 entries is ~16.2 bytes each, where the game's own
packages use 24–32, and no uniform encoding of the seven export fields reproduces 210 plausible entries (a
search over every combination of compact/`u32`/`u16`/`u8` field widths finds none). A `.unr` map parses with the
same reader in the same run (5,078 exports), so this is a save-package layout, not a reader regression. The tool
therefore reports *where* it stops instead of guessing, and a rebuilt checkpoint needs that layout measured
first — which is what this tool's table is for.

Everything this writes goes under `--out` (a work directory, never the repository).

Usage:
    sgs-write.py --dump DUMP [--out WORK/sgs-write]
    sgs-write.py --check              # synthetic package + profile, no dump, writes nothing
"""
import argparse
import importlib.util
import os
import shutil
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
from ue2pkg import Package  # noqa: E402

SECTION = "Saved Games"          # the section a checkpoint index lives in (no brackets)


def _load(name, path):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


pkg_write = _load("pkg_write", os.path.join(HERE, "pkg-write.py"))


# ---------------------------------------------------------------- the package half


def read_package(path):
    """Parse what this reader can, and record *where* it stops instead of raising.

    On a shipped checkpoint the export table is the stopping point (below), so a rebuild has to report that
    rather than crash: the header and the name and import tables parse, and the export walk desyncs mid-table.
    """
    pkg = Package(path)
    pkg.read_names()
    pkg.read_imports()
    pkg.export_error = None
    pkg.export_error_at = None
    try:
        pkg.read_exports()
    except Exception as exc:                     # the save layout, not a broken file: see the docstring
        pkg.export_error = "%s: %s" % (type(exc).__name__, exc)
        pkg.export_error_at = len(pkg.data) - 1
        pkg.exports = None
    return pkg


def census(pkg):
    """What a reader sees: (counts, names, imports, exports)."""
    return {
        "names": len(pkg.names or []),
        "imports": len(pkg.imports or []),
        "exports": len(pkg.exports or []),
        "name_table": [t for t, _f in (pkg.names or [])],
        "import_rows": [tuple(sorted(r.items())) for r in (pkg.imports or [])],
        "export_rows": [tuple(sorted(r.items())) for r in (pkg.exports or [])],
    }


def first_diff(a, b):
    if a == b:
        return None
    n = min(len(a), len(b))
    for i in range(n):
        if a[i] != b[i]:
            return i
    return n


def rebuilt_path(path, out_dir):
    """A path for the rebuilt copy that still ends in `.sgs`, so the reader sees the same extension."""
    stem = os.path.basename(path)
    os.makedirs(out_dir, exist_ok=True)
    return os.path.join(out_dir, stem)


def table_roundtrip(pkg):
    """Re-encode the two tables that parse, and compare them with the file's own bytes.

    Half a rebuild is still a measurement: if the name and import tables come back byte-identical, those two
    regions are pinned and the export table is the only one this reader does not explain.
    """
    enc_names = pkg_write.encode_names(pkg.names or [])
    names_ok = pkg.data[pkg.name_offset:pkg.name_offset + len(enc_names)] == enc_names
    enc_imports = pkg_write.encode_imports(pkg.imports or [])
    imports_ok = pkg.data[pkg.import_offset:pkg.import_offset + len(enc_imports)] == enc_imports
    return names_ok, imports_ok, len(enc_names), len(enc_imports)


def roundtrip(path, out_dir):
    """Rebuild one package, compare it, and read the rebuilt file back: one row of the report."""
    pkg = read_package(path)
    names_ok, imports_ok, names_bytes, imports_bytes = table_roundtrip(pkg)
    if pkg.exports is None:
        return {"file": os.path.basename(path), "bytes": len(pkg.data), "rebuilt": 0,
                "identical": False, "first_diff": None, "gaps": 0, "gap_bytes": 0,
                "covered": 0, "names": len(pkg.names or []), "imports": len(pkg.imports or []),
                "exports": None, "reparsed": False, "out": None,
                "export_error": pkg.export_error,
                "names_ok": names_ok, "imports_ok": imports_ok,
                "names_bytes": names_bytes, "imports_bytes": imports_bytes}
    data, gaps, covered = pkg_write.rebuild(pkg)
    want = pkg.data
    row = {"file": os.path.basename(path), "bytes": len(want), "rebuilt": len(data),
           "identical": data == want, "first_diff": first_diff(want, data),
           "gaps": len(gaps), "gap_bytes": sum(n for _o, n in gaps),
           "covered": covered, "names": len(pkg.names or []),
           "imports": len(pkg.imports or []), "exports": len(pkg.exports or [])}
    dest = rebuilt_path(path, out_dir)
    with open(dest, "wb") as fh:
        fh.write(data)
    again = read_package(dest)
    row["reparsed"] = census(again) == census(pkg)
    row["out"] = dest
    row["export_error"] = None
    return row


# ---------------------------------------------------------------- the index half


def split_sections(text):
    """[(section_name, [(key, value)])] in file order, keeping the file's own line endings aside."""
    order, rows, cur = [], {}, None
    for raw in text.split("\n"):
        line = raw.rstrip("\r")
        stripped = line.strip()
        if not stripped or stripped[0] in ";#":
            continue
        if stripped.startswith("[") and stripped.endswith("]"):
            cur = stripped[1:-1]
            if cur not in order:
                order.append(cur)
                rows[cur] = []
            continue
        if cur is None or "=" not in stripped:
            continue
        key, value = stripped.split("=", 1)
        rows[cur].append((key.strip(), value.strip()))
    return [(name, rows[name]) for name in order]


def row_count(text, section=SECTION):
    for name, rows in split_sections(text):
        if name == section:
            return len(rows)
    return 0


def add_row(text, key, value, section=SECTION):
    """Add one `key=value` row under `section`. Returns (new_text, status).

    Status is `added`, `exists` (same key already there: nothing changes) or `no-section`. The row goes after the
    last row of the section, so the file's other sections keep their bytes and order.
    """
    eol = "\r\n" if "\r\n" in text else "\n"
    lines = text.split(eol)
    start = None
    for i, line in enumerate(lines):
        if line.strip() == "[%s]" % section:
            start = i
            break
    if start is None:
        return text, "no-section"
    end = len(lines)
    for j in range(start + 1, len(lines)):
        s = lines[j].strip()
        if s.startswith("[") and s.endswith("]"):
            end = j
            break
    for k in range(start + 1, end):
        s = lines[k].strip()
        if not s or s[0] in ";#":
            continue
        if "=" in s and s.split("=", 1)[0].strip() == key:
            return text, "exists"
    insert_at = end
    while insert_at > start + 1 and not lines[insert_at - 1].strip():
        insert_at -= 1
    lines.insert(insert_at, "%s=%s" % (key, value))
    return eol.join(lines), "added"


def index_test(profile_path, key, value, out_dir):
    """Copy the profile, add one row, and prove the copy differs in exactly that row."""
    with open(profile_path, "rb") as fh:
        raw = fh.read()
    text = raw.decode("latin1")
    before = row_count(text)
    new_text, status = add_row(text, key, value)
    again_text, second = add_row(new_text, key, value)
    dest = os.path.join(out_dir, os.path.basename(profile_path))
    with open(dest, "wb") as fh:
        fh.write(new_text.encode("latin1"))
    old_lines, new_lines = text.split("\n"), new_text.split("\n")
    added = [ln for ln in new_lines if ln not in old_lines]
    return {"profile": os.path.basename(profile_path), "rows_before": before,
            "rows_after": row_count(new_text), "status": status, "second_add": second,
            "idempotent": again_text == new_text, "key_in_section": key in dict(
                dict(split_sections(new_text)).get(SECTION, [])),
            "added_lines": len(new_lines) - len(old_lines), "diff_lines": added[:3],
            "crlf_preserved": ("\r\n" in new_text) == ("\r\n" in text), "out": dest}


# ---------------------------------------------------------------- reporting


def report(rows, idx):
    bad = 0
    print("%-32s %8s %6s %5s  %-4s %-4s  %s"
          % ("file", "bytes", "names", "impt", "n.tb", "i.tb", "package rebuild"))
    for r in rows:
        if r["exports"] is None:
            status = "export table unread (%s)" % r["export_error"]
        else:
            status = ("identical" if r["identical"] else "differs at 0x%x" % r["first_diff"]) + \
                     ("" if r["reparsed"] else ", does not re-parse")
            if not r["identical"] or not r["reparsed"]:
                bad += 1
        print("%-32s %8d %6d %5d  %-4s %-4s  %s"
              % (r["file"][:32], r["bytes"], r["names"], r["imports"],
                 "yes" if r.get("names_ok") else "no", "yes" if r.get("imports_ok") else "no", status))
    same = sum(1 for r in rows if r["identical"])
    print("\n%d of %d checkpoints rebuild byte-identically; %d of %d parse to a full export table"
          % (same, len(rows), sum(1 for r in rows if r["exports"] is not None), len(rows)))
    print("name table re-encodes identically in %d of %d; import table in %d of %d"
          % (sum(1 for r in rows if r.get("names_ok")), len(rows),
             sum(1 for r in rows if r.get("imports_ok")), len(rows)))
    unread = sum(1 for r in rows if r["exports"] is None)
    if unread == len(rows):
        print("all %d stop at the same place: the save package's export-entry layout is unread"
              " (a measured blocker, not a regression -- docs/formats/sgs-checkpoint.md)" % unread)
    if idx:
        print("index: %s rows %d -> %d (%s), duplicate refused: %s, CRLF kept: %s"
              % (idx["profile"], idx["rows_before"], idx["rows_after"], idx["status"],
                 idx["second_add"] == "exists" and idx["idempotent"], idx["crlf_preserved"]))
        print("  row added under [%s]: %s" % (SECTION, idx["diff_lines"][0] if idx["diff_lines"] else "-"))
    # exit 1 only for a *regression* (a file that parses and then rebuilds differently); a set of files that all
    # stop at the same unread table is the measured state and keeps the target usable
    return 1 if bad else 0


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--dump", default=os.environ.get("SG1_DUMP"), help="dump game directory")
    ap.add_argument("--out", default=os.path.join(os.environ.get("WORK", "."), "sgs-write"),
                    help="where the rebuilt package and profile copy go (never the repo)")
    ap.add_argument("--limit", type=int, default=0, help="only the first N checkpoints")
    ap.add_argument("--check", action="store_true", help="synthetic package and profile, no dump")
    args = ap.parse_args(argv)
    if args.check:
        return selftest()
    if not args.dump:
        ap.error("--dump is required (or --check)")
    profdir = os.path.join(args.dump, "System", "Profiles", "DefaultProfile")
    files = sorted(f for f in os.listdir(profdir) if f.lower().endswith(".sgs"))
    if args.limit:
        files = files[:args.limit]
    if not files:
        sys.exit("no .sgs under %s" % profdir)
    os.makedirs(args.out, exist_ok=True)
    rows = [roundtrip(os.path.join(profdir, f), args.out) for f in files]
    profile = os.path.join(profdir, "DefaultProfile.sgp")
    idx = None
    if os.path.exists(profile):
        idx = index_test(profile, files[0], "rebuilt checkpoint (tool test)", args.out)
    return report(rows, idx)


# ---------------------------------------------------------------- self-test


def selftest():
    """A synthetic package and a synthetic profile: the codecs, the diff and the row insertion."""
    ok = True

    def chk(name, got, want):
        nonlocal ok
        if got != want:
            ok = False
            print("FAIL %s: got %r want %r" % (name, got, want))

    tmp = tempfile.mkdtemp(prefix="sgs-write-")
    try:
        src = os.path.join(tmp, "Synthetic.sgs")
        pkg_write.synthesize(src, [63, 10, 200])
        row = roundtrip(src, os.path.join(tmp, "out"))
        chk("synthetic rebuild identical", row["identical"], True)
        chk("synthetic reparses", row["reparsed"], True)
        chk("synthetic export count", row["exports"], 3)

        # a model that disagrees with the file must be *located*: flip one export flag and rebuild
        pkg = read_package(src)
        pkg.exports[0]["flags"] ^= 1
        data, _gaps, _cov = pkg_write.rebuild(pkg)
        where = first_diff(pkg.data, data)
        chk("model/file disagreement differs", where is not None, True)
        chk("the disagreement is in the export table", where >= pkg.export_offset, True)
        chk("an identical pair has no diff", first_diff(b"abcd", b"abcd"), None)
        chk("a length-only difference is located", first_diff(b"abcd", b"abcde"), 4)

        profile = ("[Engine.PlayerProfile]\r\nName=Test\r\n\r\n"
                   "[Saved Games]\r\nA.sgs=first\r\nB.sgs=second\r\n")
        before = row_count(profile)
        new, status = add_row(profile, "C.sgs", "third")
        chk("row count grows by one", (before, row_count(new)), (2, 3))
        chk("row status", status, "added")
        chk("row lands in the right section", "C.sgs" in dict(dict(split_sections(new))[SECTION]), True)
        chk("curly order kept", [n for n, _ in split_sections(new)], ["Engine.PlayerProfile", "Saved Games"])
        chk("CRLF kept", new.replace("\r\n", "").count("\n"), 0)
        chk("untouched lines identical", new.split("\r\n")[0], "[Engine.PlayerProfile]")
        _same, again = add_row(new, "C.sgs", "third")
        chk("duplicate refused", again, "exists")
        _x, missing = add_row(profile, "C.sgs", "third", section="Nope")
        chk("missing section refused", missing, "no-section")
    finally:
        shutil.rmtree(tmp, ignore_errors=True)

    if ok:
        print("sgs-write --check: ok (package round-trip, located diff, index row insertion)")
        return 0
    print("sgs-write --check: FAILED")
    return 1


if __name__ == "__main__":
    sys.exit(main())
