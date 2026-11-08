#!/usr/bin/env python3
"""Which console the live HUD holds, read out of a whole-RAM dump (PLAN 7.3, U15).

`Engine/HUD.uc:424` draws the console's prompt only when `(PlayerConsole != None) && PlayerConsole.bTyping`, and
`PlayerConsole` is the HUD's own `var() Console` linked from `PlayerOwner.Player.Console` (`LinkActors`, 683). So
"did the HUD's side of that link break" is two reads out of one dump: the HUD instance that exists there, and the
`Console`-class object its field points at -- not the console the keys were typed into, which
`console-state.py` already finds.

    python3 tools/xemu/hud-link.py dump.bin --cr3 0xf000
    python3 tools/xemu/hud-link.py dump.bin --cr3 0xf000 --csv inventory/hud_console_link.csv

Two measured details make this possible without the guest or a property chain:

* **A pointer can be screened by class without knowing its field.** In this build an object's first dword is its
  class-table pointer (measured: the typed-into `Console` opens `00 50 cb 80 ...`, and `symbols/` maps that table
  to `Console`), so every dword of the HUD can be tried as a pointer and kept only when the object it names has a
  `Console` class table. `Engine/HUD.uc:27` declares exactly one `Console`-typed field (`PlayerConsole`), so a
  Console-typed pointer inside the HUD is that field and the offset it sits at is the field's own offset.
* **Physical and linear are different spaces.** `pmemsave` writes physical memory, so an object's base is a file
  offset, while every pointer stored in the dump is linear. The match is made through `vamap.physical_for` and
  both addresses are printed -- comparing the two spaces directly finds nothing (`refs to 0x2661b00: 0`) and
  reads as "nothing links the console".

The HUD's state fields are *not* resolved here: `ConsoleColor`, `ConsoleMessagePosX/Y` and `HudCanvasScale` are
class defaults, and `tools/class-defaults.py --class HUD` reads them off the disc with no boot.

`--selftest` proves the match and the negative on a synthetic image, because CI has no dump.
"""
import argparse
import csv
import os
import struct
import sys

TOOLS = os.path.dirname(os.path.abspath(__file__))
REPO = os.path.dirname(os.path.dirname(TOOLS))
CLASS_OFF = 0x00            # the class-table word is the object's first dword in this build (see above)
STATE_FIELDS = (("typed_str_pos", 0x110), ("b_typing", 0x114))


def load(name):
    import importlib.util
    spec = importlib.util.spec_from_file_location(name, os.path.join(TOOLS, name + ".py"))
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def pointers_to_class(blob, base, depth, translate, tables, wanted):
    """[(offset, linear, class_name)] for every dword of the object at `base` that names an object of `wanted`.

    `wanted` is a set of class-table addresses. A dword is only accepted when the address it names translates
    and the object there opens with a wanted table, so a small integer, a float or a stale pointer is skipped
    rather than reported as a link.
    """
    out = []
    for offset in range(0, min(depth, 0x4000), 4):
        value = struct.unpack_from("<I", blob, base + offset)[0]
        if not value:
            continue
        physical = translate(value)
        if physical is None or physical + 4 > len(blob):
            continue
        table = struct.unpack_from("<I", blob, physical + CLASS_OFF)[0]
        if table in wanted:
            out.append((offset, value, tables.get(table, "?"), physical))
    return out


def occurrences(blob, value):
    """How many dwords in the image hold this linear address -- the image's own opinion of who links it."""
    pattern = struct.pack("<I", value)
    count = 0
    at = blob.find(pattern)
    while at >= 0:
        if at % 4 == 0:
            count += 1
        at = blob.find(pattern, at + 1)
    return count


def build(blob, translate, tables, hud_match, console_match, depth, limit):
    console_state = load("console-state")
    ramscan = load("ramscan")
    console_tables = {t for t, name in tables.items() if console_match.lower() in name.lower()}
    consoles = console_state.objects(blob, tables, console_match, limit)
    huds = console_state.objects(blob, tables, hud_match, limit)
    rows = []
    for hud_base, hud_name in huds:
        for offset, target, target_class, physical in pointers_to_class(
                blob, hud_base, depth, translate, tables, console_tables):
            match = [name for base, name in consoles if base == physical]
            counters = {"typed_str_pos": "", "b_typing": ""}
            if match:
                for key, where in STATE_FIELDS:
                    counters[key] = struct.unpack_from("<i", blob, physical + where)[0]
            rows.append({
                "dump": "",                      # filled by the caller, so the CSV is readable on its own
                "hud": "0x%08x" % hud_base,
                "hud_class": hud_name,
                "field_offset": "0x%03x" % offset,
                "target": "0x%08x" % target,
                "target_class": target_class,
                "same_object_as_typed_console": "yes" if match else "no",
                "typed_str_pos": counters["typed_str_pos"],
                "b_typing": counters["b_typing"],
                "refs_in_image": occurrences(blob, target),
            })
    return rows, huds, consoles


def selftest():
    """A synthetic image: one HUD pointing at a Console, and one HUD that points at nothing."""
    console_table, hud_table, other_table = 0x50CB80, 0x4CD738, 0x221100
    console, hud, lonely, tangle = 0x2000, 0x3000, 0x4000, 0x5000
    blob = bytearray(0x8000)
    for base, table in ((console, console_table), (hud, hud_table), (lonely, hud_table), (tangle, other_table)):
        # The header `ramscan.object_shape` screens on, as the Console default measured it: table, a serial
        # number, then the `0, 0xffffffff` pair at +0x10.
        struct.pack_into("<IIIIII", blob, base, table, 0x8D34, 0, 0, 0, 0xFFFFFFFF)
    struct.pack_into("<I", blob, hud + 0x398, console)          # the HUD's Console-typed field
    struct.pack_into("<i", blob, console + 0x110, 5)            # TypedStrPos
    struct.pack_into("<i", blob, console + 0x114, 1)            # bTyping
    struct.pack_into("<I", blob, tangle + 0x10, console)        # a pointer on an object that is not a HUD
    blob = bytes(blob)

    tables = {console_table: "Console", hud_table: "StargateHUD", other_table: "SomethingElse"}
    flat = lambda va: va if 0 <= va < len(blob) else None      # noqa: E731 - the synthetic image is identity-mapped
    rows, huds, consoles = build(blob, flat, tables, "Hud", "Console", 0x800, 8)
    assert sorted(base for base, _name in huds) == [hud, lonely], huds
    assert len(consoles) == 1 and consoles[0][0] == console, consoles
    assert len(rows) == 1, rows
    row = rows[0]
    assert (row["hud"], row["field_offset"], row["target"]) == ("0x00003000", "0x398", "0x00002000"), row
    assert row["same_object_as_typed_console"] == "yes" and row["typed_str_pos"] == 5, row
    assert row["b_typing"] == 1 and row["refs_in_image"] == 2, row
    # The lonely HUD shares a class table and contributes no row: "holds no console" is an absence, not an error.
    assert not [r for r in rows if r["target"] == "0x00000000"], rows
    print("ok   hud-link: the HUD's Console-typed field is 0x398 and the object it names carries bTyping; "
          "a HUD with no such pointer contributes no row")
    return 0


def main():
    if "--selftest" in sys.argv[1:]:
        return selftest()
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0],
                                     formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("dump", help="RAM image written by `monitor=pmemsave` (address == file offset)")
    parser.add_argument("--cr3", type=lambda v: int(v, 0), default=None, help="CR3 from `info registers` (0xf000)")
    parser.add_argument("--flat", action="store_true", help="no translation: pointers are physical (a synthetic image)")
    parser.add_argument("--hud-class", default="Hud", metavar="SUBSTR", help="class-name substring (default: Hud)")
    parser.add_argument("--console-class", default="Console", metavar="SUBSTR",
                        help="the field's class-name substring (default: Console)")
    parser.add_argument("--depth", type=lambda v: int(v, 0), default=0x1000,
                        help="bytes of each HUD to try as pointers (default: 0x1000)")
    parser.add_argument("--limit", type=int, default=8, help="stop after N objects per class (default: 8)")
    parser.add_argument("--csv", metavar="PATH", default=None, help="write the rows as a CSV (a committed artifact)")
    parser.add_argument("-o", "--out", default=os.path.join(REPO, "inventory"),
                        help="directory for --csv's default name (default: inventory)")
    args = parser.parse_args()

    with open(args.dump, "rb") as handle:
        blob = handle.read()
    if args.flat:
        translate = lambda va: va if 0 <= va < len(blob) else None       # noqa: E731
    else:
        if not args.cr3:
            parser.error("--cr3 is required to translate a linear pointer (or --flat for a synthetic image)")
        vamap = load("vamap")

        def translate(va):
            mapped = vamap.physical_for(blob, args.cr3, va)
            return mapped[0][0] if mapped else None

    tables = load("ramscan").class_names()
    rows, huds, consoles = build(blob, translate, tables, args.hud_class, args.console_class,
                                 args.depth, args.limit)
    name = os.path.join(*os.path.abspath(args.dump).split(os.sep)[-2:])   # `ram.bin` alone is not an identity
    print("%s: %d byte(s), %d HUD object(s), %d %s object(s)" % (name, len(blob), len(huds), len(consoles),
                                                                 args.console_class))
    if not huds:
        print("  no HUD object whose class table is in the census: nothing to read")
        return 1
    for base, hud_name in huds:
        print("  %s at linear %#x" % (hud_name, base))
        told = False
        for row in rows:
            if row["hud"] != "0x%08x" % base:
                continue
            told = True
            print("    +%s -> %s (%s)  same object as the typed console: %s  bTyping %s  TypedStrPos %s"
                  % (row["field_offset"], row["target"], row["target_class"],
                     row["same_object_as_typed_console"],
                     "?" if row["b_typing"] == "" else row["b_typing"],
                     "?" if row["typed_str_pos"] == "" else row["typed_str_pos"]))
        if not told:
            print("    no %s-typed pointer in the first %#x bytes" % (args.console_class, args.depth))
    same = [r for r in rows if r["same_object_as_typed_console"] == "yes"]
    if same and same[0]["b_typing"] == 1:
        print("%s: the HUD holds the typed console and it is open (bTyping 1) -- `PostRender`'s own condition is "
              "satisfied, so the prompt's silence is downstream of the link" % name)
    elif same:
        print("%s: the HUD holds the typed console, but it is closed (bTyping %s) -- the prompt should not be drawn"
              % (name, same[0]["b_typing"]))
    elif rows:
        print("%s: the HUD holds a DIFFERENT %s object -- the prompt is drawn against the other one"
              % (name, args.console_class))
    if args.csv:
        for row in rows:
            row["dump"] = name
        path = args.csv if args.csv != "-" else os.path.join(args.out, "hud_console_link.csv")
        columns = ["dump", "hud", "hud_class", "field_offset", "target", "target_class",
                   "same_object_as_typed_console", "typed_str_pos", "b_typing", "refs_in_image"]
        with open(path, "w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=columns, lineterminator="\n")
            writer.writeheader()
            writer.writerows(rows)
        print("wrote %s (%d row(s))" % (path, len(rows)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
