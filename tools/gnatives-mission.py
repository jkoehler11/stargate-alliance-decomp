#!/usr/bin/env python3
"""Diff the `GNatives` table between two snapshots of the same boot: the main menu and a loaded mission.

`GNatives` is 4,096 pointers in the uninitialised part of `.data` (`0x006313e8`), filled at startup; the first
snapshot in `inventory/gnatives_slots.csv` was taken at the **main menu**, where 356 slots hold a body and the
rest still hold the startup default stub (`0x000404a0`). Natives are registered with the class that declares
them, so the open question (docs/UNKNOWNS.md U7) is whether loading a mission -- which brings in classes the
front end never touches -- registers any slot the menu-time dump does not have.

This tool reads the two 16 KB dumps and answers it: every slot whose value moves between the menus, with the
declarer of its index when a package declares one, and the channel the static instruments say registered it
(`store` = an absolute `mov [slot]` site, `index-stub` = a stub that pushes the slot as a literal, `record` =
a `{name, body}` record in `.data`; docs/NATIVE_VM.md).

    # the two dumps stay under $WORK: they are guest memory, and only the derived map is committed
    python3 tools/xemu/mem.py --port 12345 --dump 0x6313e8:0x6353e8 -o $WORK/gnatives/runtime.bin
    tools/xemu/gnatives-mission.sh index-10          # boots M00, holds the guest, writes its own dump
    python3 tools/gnatives-mission.py "$SG1_DUMP/default.xbe" \\
        --menu $WORK/gnatives/runtime.bin --mission $WORK/mission-gnatives/index-10/gnatives.bin -o inventory

    python3 tools/gnatives-mission.py --check        # synthetic snapshots only; no dump, writes nothing

Both dumps are validated the way `tools/gnatives.py` validates the first one: 16,384 bytes, every non-zero
entry inside `.text`, and not one value repeated 4,096 times (a dump taken before anything registered).
"""
import argparse
import csv
import os
import struct
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
from gnatives import DEFAULT_STUB, ENTRIES, TABLE_BYTES, load_runtime  # noqa: E402
from xbe import Xbe  # noqa: E402

NAMES = os.path.join(ROOT, "inventory", "native_names.csv")
WRITE_SITES = os.path.join(ROOT, "inventory", "gnatives_write_sites.csv")
INDEX_STUBS = os.path.join(ROOT, "inventory", "gnatives_stubs.csv")
RECORDS = os.path.join(ROOT, "inventory", "gnatives_records.csv")
OUT = os.path.join(ROOT, "inventory", "gnatives_mission.csv")
COLUMNS = ["index", "menu_address", "mission_address", "change", "name", "evidence", "declarers", "channel"]


def read_csv(path):
    if not os.path.exists(path):
        return []
    with open(path, newline="", encoding="utf-8") as fh:
        return list(csv.DictReader(fh))


def names_by_index(path=NAMES):
    out = {}
    for row in read_csv(path):
        out[int(row["index"])] = row
    return out


def channels(paths=None):
    """Which static instrument claims each slot: two of the three name the slot by index."""
    paths = paths or (WRITE_SITES, INDEX_STUBS, RECORDS)
    out = {}
    for path, column, label in ((paths[0], "slot", "store"),
                                (paths[1], "index", "index-stub"),
                                (paths[2], "slot", "record")):
        for row in read_csv(path):
            try:
                index = int(row[column], 0)
            except (KeyError, ValueError):
                continue
            out.setdefault(index, label)
    return out


def load_baseline(path, image):
    """The menu-time snapshot: either its 16 KB dump or the committed `index -> address` map derived from it.

    The dump itself lives under `$WORK` and gets pruned with the rest of a run's scratch; the map is the
    artifact that survives (`inventory/gnatives_slots.csv`), and it carries the same information -- a slot the
    map does not name was holding the startup default stub when the dump was taken.
    """
    if path.endswith(".csv"):
        vals = [DEFAULT_STUB] * ENTRIES
        for row in read_csv(path):
            vals[int(row["index"])] = int(row["address"], 16)
        return vals
    return load_runtime(path, 0, image)


def live(vals):
    return [i for i, v in enumerate(vals) if v != DEFAULT_STUB]


def compare(menu, mission):
    """One row per slot that holds a body in either snapshot; `change` says whether it moved.

    A row set, not a diff list: an empty diff and a diff nobody wrote down look the same in a log, and the whole
    question this answers is whether the second snapshot is the first one. So every live slot is recorded with
    `same` or with the one word that says how it moved (`gained`, `lost`, `replaced`).
    """
    rows = []
    for index in range(len(menu)):
        before, after = menu[index], mission[index]
        if before == DEFAULT_STUB and after == DEFAULT_STUB:
            continue
        if before == after:
            change = "same"
        elif before == DEFAULT_STUB:
            change = "gained"
        elif after == DEFAULT_STUB:
            change = "lost"
        else:
            change = "replaced"
        rows.append({"index": index, "menu": before, "mission": after, "change": change})
    return rows


def summary(menu, mission, rows):
    kinds = {k: sum(1 for r in rows if r["change"] == k)
             for k in ("same", "gained", "lost", "replaced")}
    return {
        "menu_live": len(live(menu)),
        "mission_live": len(live(mission)),
        "gained": kinds["gained"],
        "lost": kinds["lost"],
        "replaced": kinds["replaced"],
        "same": kinds["same"],
        "shared": len(set(live(menu)) & set(live(mission))),
        "bodies_added": len({mission[r["index"]] for r in rows if r["change"] == "gained"}),
    }


def write_rows(path, rows, names, channel):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=COLUMNS, lineterminator="\n")
        w.writeheader()
        for r in rows:
            entry = names.get(r["index"], {})
            w.writerow({
                "index": r["index"],
                "menu_address": "0x%08x" % r["menu"],
                "mission_address": "0x%08x" % r["mission"],
                "change": r["change"],
                "name": entry.get("name", ""),
                "evidence": entry.get("evidence", ""),
                "declarers": entry.get("declarers", ""),
                "channel": channel.get(r["index"], ""),
            })


def check():
    """A self-test on two synthetic snapshots: the four cases, the ordering, and both dump refusals."""
    menu = [DEFAULT_STUB] * ENTRIES
    for index in range(10, 30):
        menu[index] = 0x400000 + index * 0x10
    mission = list(menu)
    mission[40] = 0x401000                       # gained
    mission[12] = 0x402000                       # replaced
    mission[15] = DEFAULT_STUB                   # lost: 12 replaced, 40 gained, 15 lost
    rows = compare(menu, mission)
    got = [(r["index"], r["change"]) for r in rows if r["change"] != "same"]
    want = [(12, "replaced"), (15, "lost"), (40, "gained")]
    assert got == want, "diff is %r, expected %r" % (got, want)
    s = summary(menu, mission, rows)
    assert (s["menu_live"], s["mission_live"], s["gained"], s["lost"], s["replaced"]) == (20, 20, 1, 1, 1), s
    assert len(rows) == 21, len(rows)   # 20 menu slots + slot 40, which only the mission snapshot has
    assert s["bodies_added"] == 1 and s["shared"] == 19, s
    # an identical second dump still has one row per live slot, every one of them `same`
    same = compare(menu, list(menu))
    assert len(same) == 20 and {r["change"] for r in same} == {"same"}, same
    # the two refusals the first dump's reader makes must still bite: a truncated buffer and a stub-only dump
    tmpdir = tempfile.mkdtemp(prefix="gnatives-mission-check-")
    for name, payload in (("short.bin", b"\0" * 64),
                          ("stub.bin", struct.pack("<%dI" % ENTRIES, *([DEFAULT_STUB] * ENTRIES)))):
        tmp = os.path.join(tmpdir, name)
        with open(tmp, "wb") as fh:
            fh.write(payload)
        try:
            class Fake:                                  # load_runtime needs a section list, not the dump
                sections = [type("S", (), {"name": ".text", "va": 0x100000, "vsize": 0x400000})()]
            load_runtime(tmp, 0, Fake)
        except SystemExit:
            pass
        else:
            raise AssertionError("%s was accepted" % name)
        finally:
            os.unlink(tmp)
    os.rmdir(tmpdir)
    print("ok: gained/replaced/lost classified, ordering index-ascending, truncated and stub-only dumps refused")
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("xbe", nargs="?", help="DUMP/default.xbe -- validates both dumps against .text")
    ap.add_argument("--menu", help="16 KB GNatives dump taken at the main menu, or inventory/gnatives_slots.csv")
    ap.add_argument("--mission", help="16 KB GNatives dump taken after a mission load")
    ap.add_argument("-o", "--out", default="inventory")
    ap.add_argument("--check", action="store_true", help="synthetic self-test; no dump, writes nothing")
    args = ap.parse_args()
    if args.check:
        return check()
    if not (args.xbe and args.menu and args.mission):
        ap.error("--check, or an xbe with --menu and --mission")

    image = Xbe(args.xbe)
    menu = load_baseline(args.menu, image)
    mission = load_runtime(args.mission, 0, image)
    rows = compare(menu, mission)
    s = summary(menu, mission, rows)
    path = os.path.join(args.out, "gnatives_mission.csv")
    write_rows(path, rows, names_by_index(), channels())
    print("menu %d live slots, mission %d live slots, %d bodies in the same slot in both"
          % (s["menu_live"], s["mission_live"], s["shared"]))
    print("slots unchanged: %d; changed: %d gained, %d lost, %d replaced (%d distinct bodies gained)"
          % (s["same"], s["gained"], s["lost"], s["replaced"], s["bodies_added"]))
    for r in [r for r in rows if r["change"] != "same"][:12]:
        print("  %3d  %#010x -> %#010x  %s" % (r["index"], r["menu"], r["mission"], r["change"]))
    print("wrote %s (%d rows)" % (path, len(rows)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
