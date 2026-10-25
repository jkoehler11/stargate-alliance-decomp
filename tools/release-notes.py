#!/usr/bin/env python3
"""Assemble release notes for a PLAN 8.4 milestone out of what the repository actually contains.

PLAN 8.4 defines six milestones. Their notes should not be written by hand: the numbers are in
`docs/STATUS.md`'s sources and the artifacts are committed, so the notes are generated and can be diffed
between releases. This prints the body for `gh release create --notes-file`, with the milestone's exit
criteria, the measured state, and the checks a reader can run.

    python3 tools/release-notes.py --milestone a            # to stdout
    python3 tools/release-notes.py --milestone a -o /tmp/notes.md

Milestones (PLAN 8.4): a inventory+tooling, b scripts decompiled, c scripts compile, d bytecode match,
e playable rebuild, f asset-complete rebuild.
"""
import argparse
import csv
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

MILESTONES = {
    "a": ("Inventory and tooling", "every artifact in the repository can be regenerated from the dump"),
    "b": ("Scripts decompiled", "all classes decompile to readable source and the script tree is complete"),
    "c": ("Scripts compile", "the decompiled tree compiles with a UE2 build of the same generation"),
    "d": ("Bytecode match", "recompiled package bytecode matches the original function for function"),
    "e": ("Playable rebuild", "a rebuilt package set boots and plays a mission in the emulator"),
    "f": ("Asset-complete rebuild", "rebuilt packages plus the original assets reproduce the build"),
}


def rows(path):
    full = os.path.join(ROOT, path)
    if not os.path.isfile(full):
        return []
    with open(full, newline="") as f:
        return list(csv.DictReader(f))


def counts():
    classes = rows("inventory/classes.csv")
    funcs = rows("symbols/xbox_functions.csv")
    cov = rows("inventory/decompile_coverage.csv")
    tables = rows("inventory/xbe_class_table_names.csv")
    maps = rows("inventory/maps.csv")
    music = rows("inventory/music_index.csv")
    speech = rows("inventory/speech_index.csv")
    sgs = rows("inventory/sgs_schema.csv")
    named = [f for f in funcs if not f["name"].startswith("FUN_")]
    return {
        "classes": len(classes),
        "classes_reconstructed": sum(1 for r in cov if r["status"] == "ok"),
        "functions": len(funcs),
        "functions_named": len(named),
        "class_tables_named": sum(1 for r in tables if r.get("class")),
        "class_tables": len(tables),
        "maps": len(maps),
        "speech_wavs": len(speech),
        "music_wavs": sum(int(r["files"]) for r in music),
        "checkpoint_rows": len(sgs),
    }


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--milestone", required=True, choices=sorted(MILESTONES))
    ap.add_argument("--version", help="default: v0.%s.0" % "MILESTONE")
    ap.add_argument("-o", "--out")
    args = ap.parse_args()

    title, criteria = MILESTONES[args.milestone]
    c = counts()
    version = args.version or ("v0.%d.0" % (ord(args.milestone) - ord("a") + 1))
    lines = [
        "# %s -- milestone %s: %s" % (version, args.milestone.upper(), title),
        "",
        "Exit criteria: %s." % criteria,
        "",
        "## Measured state at this tag",
        "",
        "| what | value | source |",
        "|---|---:|---|",
        "| classes in the script tree | %d | `inventory/classes.csv` |" % c["classes"],
        "| classes reconstructed (L0) | %d | `inventory/decompile_coverage.csv` |" % c["classes_reconstructed"],
        "| native functions | %d | `symbols/xbox_functions.csv` |" % c["functions"],
        "| native functions named | %d (%.2f%%) | same |" % (c["functions_named"], 100.0 * c["functions_named"] / max(1, c["functions"])),
        "| class tables named | %d of %d | `inventory/xbe_class_table_names.csv` |" % (c["class_tables_named"], c["class_tables"]),
        "| maps indexed | %d | `inventory/maps.csv` |" % c["maps"],
        "| speech WAVs indexed | %d | `inventory/speech_index.csv` |" % c["speech_wavs"],
        "| music WAVs indexed | %d | `inventory/music_index.csv` |" % c["music_wavs"],
        "| checkpoint property rows | %d | `inventory/sgs_schema.csv` |" % c["checkpoint_rows"],
        "",
        "## What this release is not",
        "",
        "No game data is included, and no rebuilt package exists yet: the compile loop (PLAN 3.6), the asset",
        "export (5.1) and the rebuild (6.1-6.4) have not started, so every percentage above covers inventory,",
        "decompilation and symbolisation only.",
        "",
        "## Checking it yourself",
        "",
        "```sh",
        "make ci                                   # dump-free gate over the committed artifacts",
        "make verify-dump SG1_DUMP=...             # manifest check, 4,006 files",
        "make inventory SG1_DUMP=...               # regenerates inventory/*.csv",
        "```",
        "",
        "Documents to read first: `docs/ARCHITECTURE.md`, `docs/STATUS.md`, `docs/BUILDING.md`.",
    ]
    body = "\n".join(lines) + "\n"
    if args.out:
        with open(args.out, "w") as f:
            f.write(body)
        print("wrote %s (%d bytes)" % (args.out, len(body)))
    else:
        sys.stdout.write(body)
    return 0


if __name__ == "__main__":
    sys.exit(main())
