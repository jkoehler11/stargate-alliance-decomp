#!/usr/bin/env python3
"""Which font the console's input line is drawn in, resolved end to end from the packages (PLAN 7.3).

`Engine.HUD.DrawTypingPrompt` sets three drawing inputs before it draws: `C.Font = GetConsoleFont(C)`,
`C.DrawColor = ConsoleColor`, and a position computed from `ConsoleMessagePosX/Y`, `HudCanvasScale`,
`C.SizeX/SizeY` and the text height. The first of those was recorded as native and therefore unreadable
("no dump holds it"); it is *script*, and this tool follows it: which class in the HUD's lineage declares it,
which one wins, what the winning body reads out of its own class defaults, and whether the font it names
exists in the shipped tree.

    python3 tools/hud-console-font.py DUMP                      # 640x480, the Xbox's own output
    python3 tools/hud-console-font.py DUMP --clip-x 800 --size-x 800 --size-y 600
    python3 tools/hud-console-font.py DUMP --text-height 30 -o inventory

The tool reads class defaults through `tools/class-defaults.py` (the same parser `make defaults-*` gate) and
object tables through `tools/ue2pkg.py`. It prints font *names* and numbers only: packages are the input, no
game content goes to stdout, and `-o inventory` writes a small CSV of the chain.

A static answer needs no boot, and this one retires a planned measurement: if every path lands on an object
that exists in the dump, then a missing prompt is not a font question, and a breakpoint on `DrawTypingPrompt`
has nothing to find there.
"""
import argparse
import csv
import importlib.util
import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "tools"))
from ue2pkg import Package  # noqa: E402

HUD_CLASS = "HUD"
PACKAGE_OF = {"HUD": os.path.join("System", "Engine.u"),
              "StargateHUD": os.path.join("System", "StargateGame.u")}
# Directories a `Package.Object` default can name, in the order the engine's own `[Core.System] Paths` lists them.
PATH_DIRS = ["System", "Textures", "Sounds", "Maps", "StaticMeshes", "Animations", "KarmaData", "Web"]
# The thresholds `GetConsoleFont` (HUD) walks on `C.ClipX`; the four values are the body's own literals.
CONSOLE_THRESHOLDS = [800, 1024, 1280, 1600]


def load_module(name, filename):
    spec = importlib.util.spec_from_file_location(name, os.path.join(ROOT, "tools", filename))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def read_pkg(path):
    pkg = Package(path)
    pkg.read_names()
    pkg.read_imports()
    pkg.read_exports()
    return pkg


def class_defaults(cd, stream, path, cls):
    """Every default the class's own block carries, in stream order (array elements included, in order)."""
    pkg = read_pkg(path)
    supers, declared = cd.super_map(), cd.declared_properties()
    exp = None
    for e in pkg.exports:
        if pkg.class_name_of(e) == "Class" and pkg.name(e["name"]) == cls:
            exp = e
            break
    if exp is None:
        return {}, pkg
    known, seen, cur = set(), set(), cls
    while cur and cur not in seen:
        seen.add(cur)
        known |= declared.get(cur, set())
        cur = supers.get(cur)
    cands, data = cd.candidates(pkg, stream, exp, kinds=cd.kinds_for_class(cls))
    if not cands:
        return {}, pkg
    _score, _count, start, _names, _bad = cd.choose(cands, known)
    props, _end, _reason = stream.read_block(data[start:], pkg, True)
    out = {}
    for p in props:
        if p.get("value") is None:
            continue
        out.setdefault(p["name"], []).append((p["type"], p["value"]))
    return out, pkg


def one(defaults, name, default=None):
    vals = defaults.get(name)
    return vals[0][1] if vals else default


def num(defaults, name, default=0):
    v = one(defaults, name)
    try:
        return float(v)
    except (TypeError, ValueError):
        return default


def strings(defaults, name):
    return [str(v) for _t, v in defaults.get(name, [])]


def declared_functions(pkg, cls):
    """The class's own function children -- what it overrides or adds, straight from the export tables."""
    target = None
    for e in pkg.exports:
        if pkg.class_name_of(e) == "Class" and pkg.name(e["name"]) == cls:
            target = e
            break
    if target is None:
        return set()
    ti = target["index"] + 1
    return {pkg.name(e["name"]) for e in pkg.exports
            if e.get("outer") == ti and pkg.class_name_of(e) == "Function"}


def find_object(dump, qualified):
    """`Package.Object` -> (file, exists, size) by opening that package out of the dump's tree."""
    if "." not in qualified:
        return None, False, 0
    pkg_name, obj_name = qualified.split(".", 1)
    for d in PATH_DIRS:
        for ext in (".u", ".utx", ".uax", ".usx", ".ukx", ".unr"):
            path = os.path.join(dump, d, pkg_name + ext)
            if not os.path.isfile(path):
                continue
            pkg = read_pkg(path)
            for e in pkg.exports:
                if pkg.name(e["name"]) == obj_name:
                    return path, True, e["size"]
            return path, False, 0
    return None, False, 0


def hud_path(clip_x, small_widths, array_names):
    """`StargateHUD.GetSmallFontFor`: first threshold that fits, else index 8."""
    for i in range(min(8, len(small_widths))):
        if small_widths[i] <= clip_x:
            return i, array_names[i] if i < len(array_names) else None
    return 8, array_names[8] if len(array_names) > 8 else None


def base_path(clip_x, console_font_size, array_names):
    """`HUD.GetConsoleFont`: ConsoleFontSize plus one per threshold the clip is under, then clamp(8 - n, 0, 8)."""
    size = console_font_size + sum(1 for t in CONSOLE_THRESHOLDS if clip_x < t)
    i = max(0, min(8, 8 - int(size)))
    return i, array_names[i] if i < len(array_names) else None


def selftest():
    """The two index rules, on synthetic tables -- what `make ci` runs (no dump, no game names)."""
    names = ["n%d" % i for i in range(9)]
    widths = [4096, 3200, 2560, 2048, 1600, 1280, 1024, 800, 640]
    cases = [
        ("small font at the widest clip", hud_path(4096, widths, names), (0, "n0")),
        ("small font at 1280", hud_path(1280, widths, names), (5, "n5")),
        ("small font at exactly 800", hud_path(800, widths, names), (7, "n7")),
        ("small font below the table", hud_path(640, widths, names), (8, "n8")),
        ("base font, ConsoleFontSize 0 at 640", base_path(640, 0, names), (4, "n4")),
        ("base font at 1600 or wider", base_path(1600, 0, names), (8, "n8")),
        ("base font clamps at 0", base_path(640, 9, names), (0, "n0")),
    ]
    bad = 0
    for label, got, want in cases:
        if got != want:
            bad += 1
            print("  FAIL %-34s got %r want %r" % (label, got, want))
    print("hud-console-font: %d/%d index rules" % (len(cases) - bad, len(cases)))
    return 1 if bad else 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("dump", nargs="?", help="the extracted dump root (holds System/, Textures/, ...)")
    ap.add_argument("--selftest", action="store_true", help="check the two index rules only; needs no dump")
    ap.add_argument("--clip-x", type=int, default=640, help="the canvas clip width the body tests (Xbox: 640)")
    ap.add_argument("--size-x", type=float, default=0.0, help="Canvas.SizeX (defaults to --clip-x)")
    ap.add_argument("--size-y", type=float, default=480.0, help="Canvas.SizeY")
    ap.add_argument("--text-height", type=float, default=None,
                    help="YL from Canvas.TextSize(\"A\") -- the glyph height the position subtracts")
    ap.add_argument("--override", default=None, help="assume this OverrideConsoleFontName instead of the default")
    ap.add_argument("-o", "--out", help="write a CSV of the resolved chain here")
    args = ap.parse_args()
    if args.selftest:
        return selftest()
    if not args.dump:
        sys.exit("hud-console-font: pass the dump root, or --selftest")

    size_x = args.size_x or float(args.clip_x)
    cd = load_module("class_defaults", "class-defaults.py")
    stream = cd.load_prop_stream()

    hud_defaults, hud_pkg = class_defaults(cd, stream, os.path.join(args.dump, PACKAGE_OF["HUD"]), HUD_CLASS)
    sg_defaults, sg_pkg = class_defaults(cd, stream, os.path.join(args.dump, PACKAGE_OF["StargateHUD"]),
                                         "StargateHUD")
    if not hud_defaults:
        sys.exit("hud-console-font: %s carries no default block" % PACKAGE_OF["HUD"])

    hud_fns = declared_functions(hud_pkg, HUD_CLASS)
    sg_fns = declared_functions(sg_pkg, "StargateHUD")
    rows = [("class", "item", "value")]

    def add(cls, item, value):
        rows.append((cls, item, value))
        print("  %-14s %-26s %s" % (cls, item, value))

    print("GetConsoleFont: declared by %s, %s" % (
        HUD_CLASS, "re-declared by StargateHUD" if "GetConsoleFont" in sg_fns else "not re-declared"))
    add(HUD_CLASS, "declares GetConsoleFont", "yes" if "GetConsoleFont" in hud_fns else "no")
    add("StargateHUD", "declares GetConsoleFont", "yes" if "GetConsoleFont" in sg_fns else "no")

    for cls, d in ((HUD_CLASS, hud_defaults), ("StargateHUD", sg_defaults)):
        for name in ("OverrideConsoleFontName", "ConsoleFontSize", "ConsoleColor", "ConsoleMessagePosX",
                     "ConsoleMessagePosY", "HudCanvasScale"):
            if name in d:
                add(cls, name, one(d, name))
        for name in ("FontArrayNames", "FontScreenWidthSmall"):
            if name in d:
                add(cls, name, "%d entries: %s" % (len(strings(d, name)), ",".join(strings(d, name))))

    override = args.override if args.override is not None else (
        one(sg_defaults, "OverrideConsoleFontName") or one(hud_defaults, "OverrideConsoleFontName") or "")
    override = str(override or "")
    add("resolved", "OverrideConsoleFontName", override if override else "(empty: the array path runs)")

    paths = []
    if override:
        paths.append(("OverrideConsoleFontName", override))
    else:
        idx, name = hud_path(args.clip_x, [int(float(v)) for v in strings(sg_defaults, "FontScreenWidthSmall")],
                             strings(sg_defaults, "FontArrayNames"))
        paths.append(("StargateHUD.GetSmallFontFor(%d) -> LoadFontStatic(%d)" % (args.clip_x, idx), name))
        idx2, name2 = base_path(args.clip_x, num(hud_defaults, "ConsoleFontSize"),
                                strings(hud_defaults, "FontArrayNames"))
        paths.append(("HUD.GetConsoleFont(%d) -> LoadFontStatic(%d)" % (args.clip_x, idx2), name2))

    for label, name in paths:
        path, exists, size = find_object(args.dump, name or "")
        add("resolved", label, "%s -> %s (%s)" % (
            name, "in %s, %d bytes" % (os.path.basename(path), size) if exists
            else "NOT in the dump" + ("" if path else " (package absent)"), "found" if exists else "missing"))

    scale = num(sg_defaults, "HudCanvasScale", num(hud_defaults, "HudCanvasScale", 1.0))
    posx = num(sg_defaults, "ConsoleMessagePosX", num(hud_defaults, "ConsoleMessagePosX", 0.0))
    posy = num(sg_defaults, "ConsoleMessagePosY", num(hud_defaults, "ConsoleMessagePosY", 1.0))
    x = posx * scale * size_x + ((1.0 - scale) * 0.5) * size_x
    y = posy * scale * args.size_y + ((1.0 - scale) * 0.5) * args.size_y
    add("geometry", "prompt X", "%.1f of %.0f" % (x, size_x))
    if args.text_height is None:
        add("geometry", "prompt Y", "%.1f of %.0f, minus the glyph height" % (y, args.size_y))
    else:
        add("geometry", "prompt Y", "%.1f of %.0f (%.1f after -= YL %.1f)" % (y - args.text_height, args.size_y,
                                                                            y - args.text_height, args.text_height))
        add("geometry", "inside the frame", "yes" if 0 <= y - args.text_height <= args.size_y else "NO")
    add("geometry", "colour (B,G,R)", one(hud_defaults, "ConsoleColor"))

    if args.out:
        os.makedirs(args.out, exist_ok=True)
        with open(os.path.join(args.out, "hud_console_font.csv"), "w", newline="") as f:
            csv.writer(f).writerows(rows)
        print("wrote %s" % os.path.join(args.out, "hud_console_font.csv"))
    return 0


if __name__ == "__main__":
    sys.exit(main())
