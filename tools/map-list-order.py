#!/usr/bin/env python3
"""The `QUICK SELECT MAP` list is the disc's 73 `.unr` files in sorted name order (PLAN 7.2 / U16).

`UGUIController::execGetMapList` (`0x002fe5f0`) collects every `*.unr` the file layer hands back and sorts
them before appending -- measured, no cap: at the call's return (`0x2fe809`) the generic file manager's
`FindFirstFile` loop hands over **73** names and the controller's array reads `count=73 max=76` at
`0x2fe8e0` (`docs/UNKNOWNS.md` U16). The pad positions measured elsewhere are the check on the *order*:
ten `pad=down` presses highlight `M00`, forty highlight `M15B` (`docs/XEMU.md`, `tools/xemu/missions.sh`).

So this tool is a prediction plus two anchors, not a census from the guest:

* sorted 0-based index 10 is `M00`, and index 40 is `M15B` in the shipped `Maps/` directory;
* the list has 73 entries, of which 61 are campaign `M*`, 7 deathmatch, 3 CTF and 2 shell maps
  (`Entry`, `MainMenu`).

The consequence is what matters: nothing truncates at `M15B`. The 30 campaign maps after it sit at indices
41-70, which repeated presses reach (untested -- no run has gone past 40) and which `QUICK SELECT MAP`'s
"first page and a bit" reading hid behind a scrolled page table.

    python3 tools/map-list-order.py --maps "$SG1_DUMP/Maps"           # the table
    python3 tools/map-list-order.py --maps "$SG1_DUMP/Maps" --check   # anchors, exit 1 on a mismatch
    python3 tools/map-list-order.py --selftest                        # no dump needed

Names only: nothing here reads a package, and no game data leaves the dump.
"""
import argparse
import contextlib
import io
import os
import re
import sys
import tempfile

MEASURED = {10: "M00", 40: "M15B"}   # a `pad=down` count that a frame in docs/XEMU.md shows highlighted
TOTAL = 73                           # names the file layer returned at 0x2fe809


def kind(name):
    if re.match(r"^M[0-9]", name):
        return "campaign"
    if name.startswith("DM-"):
        return "deathmatch"
    if name.startswith("CTF-") or name.startswith("SGCTF-"):
        return "ctf"
    return "shell"


def listing(maps):
    """Every `.unr` in the directory, sorted the way the guest's list is (plain byte order of the name)."""
    names = [n for n in os.listdir(maps) if n.lower().endswith(".unr")]
    names.sort()
    return [(i, re.sub(r"\.unr$", "", n, flags=re.I), kind(re.sub(r"\.unr$", "", n, flags=re.I)))
            for i, n in enumerate(names)]


def table(rows):
    for index, name, what in rows:
        print("%3d  %-24s %s" % (index, name, what))


def check(rows):
    bad = []
    got = {index: name for index, name, _ in rows}
    for index, want in MEASURED.items():
        if got.get(index) != want:
            bad.append("index %d reads %r, the measured highlight is %r" % (index, got.get(index), want))
    if len(rows) != TOTAL:
        bad.append("%d .unr files, the file layer returned %d" % (len(rows), TOTAL))
    counts = {}
    for _, _, what in rows:
        counts[what] = counts.get(what, 0) + 1
    print("%d entries: %s" % (len(rows), ", ".join("%s %d" % (k, counts[k]) for k in sorted(counts))))
    for line in bad:
        print("FAIL %s" % line)
    if bad:
        return 1
    print("ok   %s" % ", ".join("index %d is %s" % (i, n) for i, n in sorted(MEASURED.items())))
    return 0


def selftest():
    """The order, the anchors and the kinds, on a fabricated directory -- no dump, no boot."""
    good, bad = 0, []

    def case(what, ok, detail=""):
        nonlocal good
        if ok:
            good += 1
        else:
            bad.append("%s%s" % (what, ": " + str(detail) if detail else ""))

    with tempfile.TemporaryDirectory() as tmp:
        names = ["M00.unr", "M15B.unr", "Entry.unr", "MainMenu.unr"]
        names += ["M%02d.unr" % i for i in range(1, 9)]
        names += ["DM-Ha'tak.unr", "CTF-Abydos.unr", "SGCTF-AbydosVsSGC.unr"]
        for n in names:
            open(os.path.join(tmp, n), "w").close()
        open(os.path.join(tmp, "readme.txt"), "w").close()
        rows = listing(tmp)
        case("listing counts only .unr", len(rows) == len(names), len(rows))
        case("listing sorts by name", [r[1] for r in rows] == sorted(r[1] for r in rows))
        case("listing is 0-based", rows[0][0] == 0 and rows[-1][0] == len(names) - 1)
        case("kind campaign", kind("M02A") == "campaign")
        case("kind deathmatch", kind("DM-Ha'tak") == "deathmatch")
        case("kind ctf", kind("SGCTF-AbydosVsSGC") == "ctf" and kind("CTF-Abydos") == "ctf")
        case("kind shell", kind("Entry") == "shell" and kind("MainMenu") == "shell")
        # The fabricated set is 15 names, so both anchors and the 73 total must fail -- and say which. Its own
        # FAIL lines are the point of the case, not a CI failure, so they are captured rather than printed.
        got = {r[0]: r[1] for r in rows}
        case("anchor 10 is not M00 on a fabricated set", got.get(10) != "M00", got.get(10))
        said = io.StringIO()
        with contextlib.redirect_stdout(said):
            rc = check(rows)
        case("check names the count it wanted", rc == 1)
        case("check names the count", "15 .unr files" in said.getvalue(), said.getvalue()[:80])
    print("%s: %d case(s), %d failed" % ("ok  " if not bad else "FAIL", good + len(bad), len(bad)))
    for line in bad:
        print("  " + line)
    return 0 if not bad else 1


def main(argv=None):
    ap = argparse.ArgumentParser(description="the map list's order, from the shipped Maps directory")
    ap.add_argument("--maps", help="the dump's Maps directory (default $SG1_DUMP/Maps)")
    ap.add_argument("--check", action="store_true", help="assert the measured anchors and the 73-name count")
    ap.add_argument("--selftest", action="store_true", help="check the order and the kinds, no dump needed")
    args = ap.parse_args(argv)
    if args.selftest:
        return selftest()
    maps = args.maps or os.path.join(os.environ.get("SG1_DUMP", ""), "Maps")
    if not os.path.isdir(maps):
        # `tools/ci.py`'s check contract: exit 2 and say "required" when a dump-side argument is missing, so a
        # run without the dump counts the tool as dump-dependent rather than broken.
        print("no Maps directory at %s: --maps or SG1_DUMP required" % (maps or "<unset>"), file=sys.stderr)
        return 2
    rows = listing(maps)
    if args.check:
        return check(rows)
    table(rows)
    return 0


if __name__ == "__main__":
    sys.exit(main())
